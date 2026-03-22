#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$HOME/coding_projects/zefix_analyzer"
BRANCH="main"

cd "$APP_DIR"

echo "==> Updating repo"
git fetch origin
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

export BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export BUILD_GIT_SHA="$(git rev-parse --short=12 HEAD)"

echo "==> Building and restarting containers"
docker compose up -d --build --remove-orphans

echo "==> Current status"
docker compose ps

# ── First-time setup: create the initial admin user ───────────────────────────
# Run this once after the first deployment (or whenever you need to add/reset an admin):
#
#   docker compose --profile create-admin run --rm create-admin \
#     python -m app.create_admin create --username admin --password <password> --email admin@example.com
#
# To list existing users:
#   docker compose --profile create-admin run --rm create-admin \
#     python -m app.create_admin list
#
# To reset a password:
#   docker compose --profile create-admin run --rm create-admin \
#     python -m app.create_admin set-password --username admin --password <newpassword>
# ─────────────────────────────────────────────────────────────────────────────
