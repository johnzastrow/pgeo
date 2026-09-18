#!/usr/bin/env bash
# Fetch the pelias/docker CLI at a pinned commit into vendor/pelias-docker.
set -euo pipefail
PELIAS_DOCKER_REF="${PELIAS_DOCKER_REF:-3dfa07d}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${ROOT}/vendor/pelias-docker"

if [[ ! -d "${DEST}/.git" ]]; then
  git clone --quiet https://github.com/pelias/docker.git "$DEST"
fi
git -C "$DEST" fetch --quiet origin
git -C "$DEST" checkout --quiet "$PELIAS_DOCKER_REF"
echo "pelias CLI at $(git -C "$DEST" rev-parse --short HEAD): ${DEST}/pelias"
