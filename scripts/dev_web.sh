#!/usr/bin/env bash
# Serve web/ locally at http://127.0.0.1:8088 with production-equivalent headers and CSP,
# proxying /v1/ to the local Pelias API (scripts/build_local.sh up). Ctrl-C to stop.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TILES="${ROOT}/data/raw/basemap/maine.pmtiles"
[[ -f "$TILES" ]] || { echo "missing ${TILES} (scripts/fetch_data.sh basemap)" >&2; exit 1; }
# API keys, as in production. PGEO_API_KEY names the key the dev edge accepts; without one a key
# is made and printed. The tests and the page read the same variable. Only the hash reaches nginx.
if [[ -z "${PGEO_API_KEY:-}" ]]; then
  PGEO_API_KEY="$("${ROOT}/scripts/edge_apikey.sh" new dev | sed -n '3p' | tr -d ' ')"
  echo "dev API key (also in data/dev_api_key):  ${PGEO_API_KEY}"
  echo "  export PGEO_API_KEY=${PGEO_API_KEY}"
fi
mkdir -p "${ROOT}/data"
( umask 077; printf '%s\n' "${PGEO_API_KEY}" > "${ROOT}/data/dev_api_key" )
MAP="$(mktemp --suffix=.map)"
printf '"%s" "dev";\n' "$(printf '%s' "${PGEO_API_KEY}" | "${ROOT}/scripts/edge_apikey.sh" hash)" > "${MAP}"
trap 'rm -f "${MAP}"' EXIT
docker run --rm --name pelias_maine_web_dev --network host \
  -v "${ROOT}/web:/srv/pelias-demo:ro" \
  -v "${TILES}:/srv/tiles/maine.pmtiles:ro" \
  -v "${ROOT}/scripts/dev/nginx.dev.main.conf:/etc/nginx/nginx.conf:ro" \
  -v "${ROOT}/scripts/dev/nginx.dev.conf:/etc/nginx/conf.d/default.conf:ro" \
  -v "${MAP}:/etc/nginx/conf.d/apikey.map:ro" \
  -v "${ROOT}/infra/ansible/roles/edge/files/apikey.js:/etc/nginx/njs/apikey.js:ro" \
  nginx:1.29-alpine
