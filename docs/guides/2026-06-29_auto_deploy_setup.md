# Auto-deploy: git push → VPS rebuild

**Date:** 2026-06-29
**Project origin:** dispatch_chicago
**Status:** in production use, 1× successful deploy verified

This guide documents the auto-deploy pipeline that takes a `git push origin main` and turns it into a rebuilt, restarted, health-checked production stack — entirely from your laptop, with no GitHub Actions, no container registry, and no manual steps on the server.

It is written as a **reusable recipe**: section 2 explains how each piece works in the current project; section 3 is a step-by-step setup you can follow verbatim on a new VPS + new project.

---

## 1. What you get

Once set up, the daily workflow is:

```bash
# edit code, test locally
git add -A
git commit -m "..."
git push origin main
make deploy          # or: bash scripts/deploy.sh
```

`make deploy` SSHes into the VPS, fast-forwards the repo, rebuilds Docker images, restarts containers, polls the backend healthcheck, and prints the public URLs on success. ~30s on a no-op, ~2min for a full backend rebuild.

---

## 2. How it works — the four pieces

There are four components, all on your laptop (none on the VPS):

| Piece | Where | What it does |
|---|---|---|
| SSH key pair | `~/.ssh/id_ed25519` (private) + `~/.ssh/id_ed25519.pub` (public) | Authenticates you to the VPS without a password |
| SSH config alias | `~/.ssh/config` (`Host dispatch`) | Maps a short name → IP/port/user/identity, with ControlMaster so all calls reuse one TCP connection |
| Deploy script | `scripts/deploy.sh` | The orchestrator: SSHes in, runs git/docker commands, polls healthcheck |
| Makefile target | `Makefile` (`deploy:`) | Wrapper that calls the script with env-var overrides |

### Why each piece exists — the failures that motivated them

**SSH key, not password.** Bots constantly scan the public internet for SSH on port 22 and try common passwords. With password auth, you will eventually be brute-forced. With key-only auth, the attack has zero chance of success even if the bot finds you.

**SSH config alias (`Host dispatch`).** Hardcoding `ssh root@144.126.138.157 -p 2022 -i ~/.ssh/key` into a script leaks IPs/ports/keys into your repo. The alias resolves all of that out of band. Bonus: when you eventually move VPSes, you change one line in `~/.ssh/config`, not in any script.

**ControlMaster (`ControlMaster auto`, `ControlPath ~/.ssh/cm-%r@%h:%p`, `ControlPersist 10m`).** This was the fix that made the deploy reliable under attack. Default sshd config (`MaxStartups 10:30:100`) throttles unauthenticated connections. A botnet hitting your VPS chews through those slots; your key-auth connections get dropped too. With ControlMaster, the first SSH call opens a master TCP connection; subsequent calls multiplex over it. The deploy script's 6+ SSH calls become 1 TCP connection — throttling becomes irrelevant.

**Per-ssh retry inside `deploy.sh`.** Even with ControlMaster, individual commands can fail (network blips, transient sshd hiccups). `run_ssh` retries up to 4 times on transient errors (`Connection reset`, `Software caused connection abort`, `kex_exchange_identification`) and fails fast on real errors (auth failure, command exit ≠0).

**`make deploy` instead of `bash scripts/deploy.sh`.** Lets you override config from the command line without editing files: `make deploy SSH_ALIAS=prod VPS_REPO_PATH=/srv/app`. Also gets tab-completion in most shells.

---

## 3. Setup from scratch — for a new VPS + new project

Follow these in order. Total time: ~10 minutes for a new VPS, ~5 minutes for a new project on an existing VPS.

### 3.1 On the VPS — one time

```bash
# SSH in with whatever you have (root + password is fine for first contact)
ssh root@<VPS_IP>

# Create the project directory and clone
mkdir -p /opt/<project_name>
cd /opt/<project_name>
git clone <your-repo-url> .
```

For the auto-deploy to work, the repo on the VPS must:
- Be on branch `main` (or whatever you set `BRANCH` to)
- Have `docker-compose.prod.yml` at the root (or wherever your script expects it)
- Have its `.env.prod` in place — the deploy script verifies it but **never creates or overwrites it**

```bash
# Populate .env.prod by hand (NEVER commit this)
cat > /opt/<project_name>/.env.prod <<'EOF'
# ... your real secrets here ...
EOF
chmod 600 /opt/<project_name>/.env.prod
```

The deploy script's existence check is:
```bash
test -f '<VPS_REPO_PATH>/.env.prod'
```

So the file must exist before any deploy runs. Missing it → deploy aborts.

### 3.2 On your laptop — one time

**a) Generate an SSH key (if you don't already have one for this VPS):**

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -C "your-laptop project_name" -N ""
```

`-N ""` creates a key with no passphrase. Less secure (anyone with the file can use it), but means no agent-prompting on every SSH call. If you want a passphrase:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -C "your-laptop project_name"
# Enter passphrase when prompted
ssh-add ~/.ssh/id_ed25519  # once per session, or use the Windows service (see below)
```

**b) Push the public key to the VPS:**

```bash
ssh-copy-id -i ~/.ssh/id_ed25519.pub root@<VPS_IP>
# or, if ssh-copy-id is unavailable:
cat ~/.ssh/id_ed25519.pub | ssh root@<VPS_IP> 'mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys'
```

Verify:
```bash
ssh -o BatchMode=yes -o ConnectTimeout=5 root@<VPS_IP> true && echo OK
```

If `OK`, key auth works.

**c) Add the SSH config alias:**

Edit `~/.ssh/config` (Windows: `C:\Users\<you>\.ssh\config`):

```
Host <alias>                  # e.g. dispatch, prod, blue, etc.
    HostName <VPS_IP_OR_FQDN>
    User <SSH_USER>
    Port <PORT>               # omit this line for default port 22
    IdentityFile ~/.ssh/id_ed25519
    ServerAliveInterval 60
    ServerAliveCountMax 3
    ControlMaster auto
    ControlPath ~/.ssh/cm-%r@%h:%p
    ControlPersist 10m
```

Verify the alias works:
```bash
ssh <alias> true && echo OK
```

**d) (Windows only) Make ssh-agent persistent:**

The `ssh-agent` started by `eval "$(ssh-agent -s)"` dies when you close the terminal. Enable the Windows service so it survives:

```powershell
# Run in elevated PowerShell once
Set-Service ssh-agent -StartupType Automatic
Start-Service ssh-agent
```

Then in any normal terminal:
```bash
ssh-add ~/.ssh/id_ed25519   # loads the key into the persistent agent
ssh-add -l                  # confirm it's loaded
```

Verify after closing all terminals and opening a fresh one:
```bash
ssh <alias> true && echo OK
```

### 3.3 Add the deploy script — for each project

Create `scripts/deploy.sh` at your project root:

```bash
#!/usr/bin/env bash
set -euo pipefail

# === EDIT THESE TWO LINES ===
SSH_ALIAS="${SSH_ALIAS:-<alias>}"
VPS_REPO_PATH="${VPS_REPO_PATH:-/opt/<project_name>}"

# === Internal config ===
COMPOSE_FILE="docker-compose.prod.yml"          # or whatever your prod compose file is named
HEALTHCHECK_URL="http://localhost:8888/api/v1/health"   # must match your backend's health endpoint
HEALTHCHECK_TIMEOUT=90
LOG_TAIL=80
SSH_RETRY_MAX=4
SSH_RETRY_SLEEP=2

# run_ssh <description> <ssh-args...>
# Retries on transient connection errors, fails fast on real errors.
run_ssh() {
    local desc="$1"; shift
    local attempt=1 rc
    while true; do
        if "$@" 2>/tmp/ssh_err.$$; then
            rm -f /tmp/ssh_err.$$
            return 0
        fi
        rc=$?
        if grep -qE "Connection (closed|reset)|Connection reset by|Software caused connection abort|kex_exchange_identification|Connection to .* (aborted|refused)" /tmp/ssh_err.$$ 2>/dev/null; then
            if (( attempt >= SSH_RETRY_MAX )); then
                echo "    [${desc}] ssh failed after ${SSH_RETRY_MAX} attempts:"
                sed 's/^/        /' /tmp/ssh_err.$$
                rm -f /tmp/ssh_err.$$
                return $rc
            fi
            echo "    [${desc}] transient ssh error (attempt ${attempt}/${SSH_RETRY_MAX}), retrying in ${SSH_RETRY_SLEEP}s"
            sleep "$SSH_RETRY_SLEEP"
            attempt=$((attempt + 1))
            continue
        fi
        sed 's/^/        /' /tmp/ssh_err.$$ 2>/dev/null
        rm -f /tmp/ssh_err.$$
        return $rc
    done
}

# ---- Preflight ----
command -v ssh >/dev/null || { echo "ssh not found in PATH"; exit 1; }

echo "==> Checking SSH alias '${SSH_ALIAS}' is reachable"
run_ssh "preflight" ssh -o BatchMode=yes -o ConnectTimeout=5 "${SSH_ALIAS}" true \
    || { echo "    Cannot reach '${SSH_ALIAS}'."; exit 1; }

echo "==> Verifying .env.prod exists on VPS (script will not overwrite it)"
run_ssh "env-check" ssh "${SSH_ALIAS}" "test -f '${VPS_REPO_PATH}/.env.prod'" \
    || { echo "    .env.prod not found at ${VPS_REPO_PATH}/.env.prod. Aborting."; exit 1; }

echo "==> Fetching and fast-forwarding to origin/main"
run_ssh "sync" ssh "${SSH_ALIAS}" "cd '${VPS_REPO_PATH}' && git fetch origin && git merge --ff-only origin/main" \
    || { echo "    git pull failed. Inspect VPS state."; exit 1; }

echo "==> Building images"
run_ssh "build" ssh "${SSH_ALIAS}" "cd '${VPS_REPO_PATH}' && docker compose -f ${COMPOSE_FILE} build"

echo "==> Bringing stack up"
run_ssh "up" ssh "${SSH_ALIAS}" "cd '${VPS_REPO_PATH}' && docker compose -f ${COMPOSE_FILE} up -d"

echo "==> Waiting up to ${HEALTHCHECK_TIMEOUT}s for backend healthcheck"
SECONDS=0
until run_ssh "healthcheck" ssh "${SSH_ALIAS}" "curl -fsS ${HEALTHCHECK_URL}" >/dev/null 2>&1; do
    if (( SECONDS >= HEALTHCHECK_TIMEOUT )); then
        echo "!! Backend did not become healthy within ${HEALTHCHECK_TIMEOUT}s."
        run_ssh "logs" ssh "${SSH_ALIAS}" "cd '${VPS_REPO_PATH}' && docker compose -f ${COMPOSE_FILE} logs --tail=${LOG_TAIL}" || true
        exit 1
    fi
    sleep 3
done
echo "    Healthy after ${SECONDS}s"

echo
echo "==> Deploy complete."
echo "    Tail logs: ssh ${SSH_ALIAS} 'cd ${VPS_REPO_PATH} && docker compose -f ${COMPOSE_FILE} logs -f'"
```

Add a Makefile target at your project root:

```makefile
deploy:
	@SSH_ALIAS="$(SSH_ALIAS)" VPS_REPO_PATH="$(VPS_REPO_PATH)" bash scripts/deploy.sh
```

Then `make deploy` (or `bash scripts/deploy.sh`) just works.

---

## 4. The deploy script, line by line

What each section actually does, so you can debug when it breaks:

**Preflight** — opens a master SSH connection, verifies the alias resolves. If `BatchMode=yes` (no password prompt) and `ConnectTimeout=5` fail, your SSH config is wrong or the VPS is down.

**`.env.prod` check** — refuses to proceed if the secrets file is missing. Silent deployment with missing env vars would start a container with `SECRET_KEY=` empty or `OPENAI_API_KEY=` blank — a serious security incident. This check is the most important safety property of the script.

**`git fetch origin && git merge --ff-only origin/main`** — the fast-forward constraint catches divergence early. If your VPS has unpushed commits or uncommitted edits, `--ff-only` fails instead of silently creating a merge commit. The error message tells you to inspect manually. **Don't remove `--ff-only`** — it's there for a reason.

**`docker compose build`** — rebuilds images from the source on the VPS. No source-code transfer over the network; Docker uses its layer cache so unchanged stages are near-instant.

**`docker compose up -d`** — recreates only the containers whose image hash changed. No full downtime; Traefik (or your reverse proxy) routes around the restart.

**Healthcheck polling** — every 3s, runs `curl http://localhost:8888/api/v1/health` *on the VPS* (note: `localhost`, not your public domain — we're checking from inside the VPS, so TLS and DNS aren't required). If 90s pass without success, dumps the last 80 log lines and exits 1.

The healthcheck URL must exist on your backend. Common patterns: `/health`, `/api/v1/health`, `/_health`. If your backend doesn't expose a health endpoint, add one before relying on this script — without it, a deploy that crashed at startup would be reported as successful.

---

## 5. What breaks and how to fix it

| Symptom | Cause | Fix |
|---|---|---|
| `Host key verification failed` | First connection to this VPS | `ssh-keyscan -H <VPS_IP> >> ~/.ssh/known_hosts` |
| `Permission denied (publickey)` | Public key not in VPS `~/.ssh/authorized_keys`, or wrong perms | Re-run `ssh-copy-id`; verify `chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys` |
| `Connection closed by ... port 22` | sshd `MaxStartups` throttling from bots | ControlMaster (this guide) fixes it. Long-term: install fail2ban on the VPS |
| `git pull failed (likely non-fast-forward)` | VPS has unmerged commits or uncommitted edits | SSH in manually: `git status`, `git log origin/main..HEAD`, decide whether to `git reset --hard origin/main` or commit/push the VPS-side work first |
| `Backend did not become healthy` | Container crashed on startup | Re-run the deploy (no-op rebuild). If still failing: `ssh <alias> 'cd <path> && docker compose -f docker-compose.prod.yml logs --tail=200 app'` |
| `.env.prod not found` | File missing or wrong path | SSH in, create it. Check `VPS_REPO_PATH` in `scripts/deploy.sh` matches reality |

---

## 6. VPS-side hardening (recommended, not blocking)

The setup above works as-is, but a publicly-discoverable VPS on port 22 will keep eating botnet noise. Three follow-ups, in order of impact:

**a) Install fail2ban.** Bans IPs that fail auth N times. Standard Ubuntu/Debian:
```bash
sudo apt install fail2ban
sudo systemctl enable --now fail2ban
# defaults: 5 failures → 10-minute ban
```

**b) Move SSH to a non-standard port.** Bots scanning the entire IPv4 space find port 22 quickly; they don't bother with high ports.
```bash
# /etc/ssh/sshd_config
Port 2222

# then on your laptop, update ~/.ssh/config:
#   Host dispatch
#       Port 2222

sudo systemctl restart sshd
```

**c) Disable password auth entirely** (only safe if every user has key auth):
```bash
# /etc/ssh/sshd_config
PasswordAuthentication no
sudo systemctl restart sshd
```

---

## 7. Files this added to dispatch_chicago

```
dispatch_bot/
├── scripts/
│   └── deploy.sh                    # the deploy orchestrator
└── Makefile                          # `deploy:` target added

# (user-local, not in repo)
~/.ssh/config                         # `Host dispatch` block with ControlMaster
~/.ssh/id_ed25519                     # SSH key for VPS auth
```

These three files together are the entire auto-deploy system.