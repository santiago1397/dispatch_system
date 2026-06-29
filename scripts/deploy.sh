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

set -euo pipefail

# === EDIT THESE TWO LINES ===
SSH_ALIAS="${SSH_ALIAS:-dispatch}"
VPS_REPO_PATH="${VPS_REPO_PATH:-/opt/santiagoproperties/dispatch_system}"

# === Internal config — usually no need to change ===
COMPOSE_FILE="docker-compose.prod.yml"
HEALTHCHECK_URL="http://localhost:8888/api/v1/health"
HEALTHCHECK_TIMEOUT=90   # seconds to wait for the new backend to come up
LOG_TAIL=80              # lines to dump if the healthcheck fails

# ---- Preflight ----
command -v ssh >/dev/null || { echo "ssh not found in PATH"; exit 1; }

echo "==> Checking SSH alias '${SSH_ALIAS}' is reachable"
ssh -o BatchMode=yes -o ConnectTimeout=5 "${SSH_ALIAS}" true \
    || { echo "    Cannot reach '${SSH_ALIAS}'. Check ~/.ssh/config and that the VPS is up."; exit 1; }

echo "==> Verifying .env.prod exists on VPS (script will not overwrite it)"
ssh "${SSH_ALIAS}" "test -f '${VPS_REPO_PATH}/.env.prod'" \
    || { echo "    .env.prod not found at ${VPS_REPO_PATH}/.env.prod. Aborting."; exit 1; }

# ---- Sync ----
echo "==> Fetching and fast-forwarding to origin/main"
ssh "${SSH_ALIAS}" "cd '${VPS_REPO_PATH}' && git fetch origin && git merge --ff-only origin/main" \
    || { echo "    git pull failed (likely non-fast-forward). Inspect VPS state manually."; exit 1; }

# ---- Build ----
echo "==> Building images (backend + frontend)"
ssh "${SSH_ALIAS}" "cd '${VPS_REPO_PATH}' && docker compose -f ${COMPOSE_FILE} build"

# ---- Recreate containers ----
echo "==> Bringing stack up"
ssh "${SSH_ALIAS}" "cd '${VPS_REPO_PATH}' && docker compose -f ${COMPOSE_FILE} up -d"

# ---- Healthcheck ----
echo "==> Waiting up to ${HEALTHCHECK_TIMEOUT}s for backend healthcheck"
SECONDS=0
until ssh "${SSH_ALIAS}" "curl -fsS ${HEALTHCHECK_URL}" >/dev/null 2>&1; do
    if (( SECONDS >= HEALTHCHECK_TIMEOUT )); then
        echo "!! Backend did not become healthy within ${HEALTHCHECK_TIMEOUT}s."
        echo "   Last ${LOG_TAIL} log lines:"
        ssh "${SSH_ALIAS}" "cd '${VPS_REPO_PATH}' && docker compose -f ${COMPOSE_FILE} logs --tail=${LOG_TAIL}" || true
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