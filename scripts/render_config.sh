#!/usr/bin/env bash
# Render projects/<project>/pelias.json from pelias.template.json, injecting OA_TOKEN from the
# project's secrets.env when set. The rendered file is gitignored and mode 600.
# secrets.env is kept separate from .env because the pelias CLI and docker compose read .env.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_DIR="${1:-${ROOT}/projects/pelias_maine}"
SECRETS_FILE="${PROJECT_DIR}/secrets.env"

OA_TOKEN=""
if [[ -f "$SECRETS_FILE" ]]; then
  # Read only OA_TOKEN; never source the file.
  OA_TOKEN="$(grep -E '^OA_TOKEN=' "$SECRETS_FILE" | tail -1 | cut -d= -f2- || true)"
fi

umask 077
if [[ -n "$OA_TOKEN" ]]; then
  jq --arg t "$OA_TOKEN" '.imports.openaddresses.token = $t' \
    "${PROJECT_DIR}/pelias.template.json" > "${PROJECT_DIR}/pelias.json"
  echo "rendered pelias.json (with OA token)"
else
  cp "${PROJECT_DIR}/pelias.template.json" "${PROJECT_DIR}/pelias.json"
  echo "rendered pelias.json (no OA token: importer default token will be used)"
fi
