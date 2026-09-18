#!/usr/bin/env bash
# Serve web/ locally at http://127.0.0.1:8088 with production-equivalent headers and CSP,
# proxying /v1/ to the local Pelias API (scripts/build_local.sh up). Ctrl-C to stop.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TILES="${ROOT}/data/raw/basemap/maine.pmtiles"
[[ -f "$TILES" ]] || { echo "missing ${TILES} (scripts/fetch_data.sh basemap)" >&2; exit 1; }
exec docker run --rm --name pelias_maine_web_dev --network host \
  -v "${ROOT}/web:/srv/pelias-demo:ro" \
  -v "${TILES}:/srv/tiles/maine.pmtiles:ro" \
  -v "${ROOT}/scripts/dev/nginx.dev.conf:/etc/nginx/conf.d/default.conf:ro" \
  nginx:1.29-alpine
