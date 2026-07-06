#!/usr/bin/env bash
# scripts/deploy.sh — deploy dispatch_bot to the VPS over SSH
#
# Usage:  ./scripts/deploy.sh
#         make deploy
#
# Prerequisites:
#   1. SSH alias `dispatch` in ~/.ssh/config (see CLAUDE.md / setup notes)
#   2. Repo cloned at VPS_REPO_PATH on the VPS, on branch `main`
#   3. .env.prod populated at VPS_REPO_PATH/.env.prod  (this script NEVER
#      touches it — it only verifies it exists)
#   4. docker compose plugin + the `traefik-public` external network
#      already in place on the VPS
#
# Notes on SSH reliability:
#   - The `dispatch` SSH alias uses ControlMaster so all ssh calls in this
#     script multiplex over ONE persistent TCP connection. The first call
#     opens the master; subsequent calls reuse it. This avoids the sshd
#     MaxStartups throttling that happens when brute-force bots flood the
#     auth slots with new connections.
#   - `run_ssh` wraps each command with up to 4 retries on transient
#     connection errors (Connection reset / Software caused abort / etc.),
#     which can still happen even with ControlMaster during the master's
#     initial handshake.

set -euo pipefail

# === EDIT THESE TWO LINES ===
SSH_ALIAS="${SSH_ALIAS:-dispatch}"
VPS_REPO_PATH="${VPS_REPO_PATH:-/opt/santiagoproperties/dispatch_system}"

# === Internal config — usually no need to change ===
COMPOSE_FILE="docker-compose.prod.yml"
HEALTHCHECK_URL="http://localhost:8888/api/v1/health"
HEALTHCHECK_TIMEOUT=90   # seconds to wait for the new backend to come up
LOG_TAIL=80              # lines to dump if the healthcheck fails
SSH_RETRY_MAX=4          # attempts per ssh call (1 initial + 3 retries)
SSH_RETRY_SLEEP=2        # seconds between retries

# run_ssh <description> <ssh-args...> — runs ssh, retries on transient errors.
# The first arg is a human label for logs. The remaining args are passed
# verbatim to ssh (typically "${SSH_ALIAS}" "<remote command>").
run_ssh() {
    local desc="$1"; shift
    local attempt=1 rc
    while true; do
        if "$@" 2>/tmp/ssh_err.$$; then
            rm -f /tmp/ssh_err.$$
            return 0
        fi
        rc=$?
        # Classify: connection-level errors are transient (retry).
        # Anything else (auth failure, command exit !=0) bubbles up.
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
        # Non-transient error: surface stderr and bail.
        sed 's/^/        /' /tmp/ssh_err.$$ 2>/dev/null
        rm -f /tmp/ssh_err.$$
        return $rc
    done
}

# ---- Preflight ----
command -v ssh >/dev/null || { echo "ssh not found in PATH"; exit 1; }

echo "==> Checking SSH alias '${SSH_ALIAS}' is reachable"
run_ssh "preflight" ssh -o BatchMode=yes -o ConnectTimeout=5 "${SSH_ALIAS}" true \
    || { echo "    Cannot reach '${SSH_ALIAS}'. Check ~/.ssh/config and that the VPS is up."; exit 1; }

echo "==> Verifying .env.prod exists on VPS (script will not overwrite it)"
run_ssh "env-check" ssh "${SSH_ALIAS}" "test -f '${VPS_REPO_PATH}/.env.prod'" \
    || { echo "    .env.prod not found at ${VPS_REPO_PATH}/.env.prod. Aborting."; exit 1; }

# ---- Sync ----
echo "==> Fetching and fast-forwarding to origin/main"
run_ssh "sync" ssh "${SSH_ALIAS}" "cd '${VPS_REPO_PATH}' && git fetch origin && git merge --ff-only origin/main" \
    || { echo "    git pull failed (likely non-fast-forward). Inspect VPS state manually."; exit 1; }

# ---- Build ----
echo "==> Building images (backend + frontend)"
run_ssh "build" ssh "${SSH_ALIAS}" "cd '${VPS_REPO_PATH}' && docker compose -f ${COMPOSE_FILE} build"

# ---- Recreate containers ----
echo "==> Bringing stack up"
run_ssh "up" ssh "${SSH_ALIAS}" "cd '${VPS_REPO_PATH}' && docker compose -f ${COMPOSE_FILE} up -d"

# ---- Migrate ----
# Run Alembic migrations with a one-off container from the freshly built
# image (--no-deps: DB/deps are already running from `up -d`; --rm: clean up
# after). Overrides the service command with `alembic upgrade head`.
echo "==> Running database migrations (alembic upgrade head)"
run_ssh "migrate" ssh "${SSH_ALIAS}" "cd '${VPS_REPO_PATH}' && docker compose -f ${COMPOSE_FILE} run --rm --no-deps app alembic upgrade head" \
    || { echo "    Alembic migration failed. Stack is up but the schema may be out of date. Inspect the VPS before serving traffic."; exit 1; }

# ---- Healthcheck ----
echo "==> Waiting up to ${HEALTHCHECK_TIMEOUT}s for backend healthcheck"
SECONDS=0
until run_ssh "healthcheck" ssh "${SSH_ALIAS}" "curl -fsS ${HEALTHCHECK_URL}" >/dev/null 2>&1; do
    if (( SECONDS >= HEALTHCHECK_TIMEOUT )); then
        echo "!! Backend did not become healthy within ${HEALTHCHECK_TIMEOUT}s."
        echo "   Last ${LOG_TAIL} log lines:"
        run_ssh "logs" ssh "${SSH_ALIAS}" "cd '${VPS_REPO_PATH}' && docker compose -f ${COMPOSE_FILE} logs --tail=${LOG_TAIL}" || true
        exit 1
    fi
    sleep 3
done
echo "    Healthy after ${SECONDS}s"

echo
echo "==> Deploy complete."
echo "    Frontend: https://santiagoproperties.uk"
echo "    API:      https://api.santiagoproperties.uk"
echo "    Tail logs: ssh ${SSH_ALIAS} 'cd ${VPS_REPO_PATH} && docker compose -f ${COMPOSE_FILE} logs -f'"