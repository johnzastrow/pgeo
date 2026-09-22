#!/usr/bin/env bash
# Serve web/ locally at http://127.0.0.1:8088 with production-equivalent headers and CSP,
# proxying /v1/ to the local Pelias API (scripts/build_local.sh up). Ctrl-C to stop.
#
#   scripts/dev_web.sh [--build NAME]
#
# A build other than "me" is served from its own stack's ports and its own basemap, and the page
# names it: the region is announced in /region.json, generated here.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

build=me
while [[ $# -gt 0 ]]; do
  case "$1" in
    --build) build="${2:?--build needs a name}"; shift 2 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
build="${build,,}"

TILES="${ROOT}/data/raw/${build}/basemap/${build}.pmtiles"
[[ -f "$TILES" ]] || { echo "missing ${TILES} (scripts/fetch_data.sh --build ${build} basemap)" >&2; exit 1; }

# The page asks the server which region it is serving.
REGION="$(mktemp --suffix=.json)"
python3 "${ROOT}/scripts/gen_region_json.py" --build "$build" > "$REGION"
chmod 0644 "$REGION"

# A build other than the default sits one step up the port range (scripts/pgeo_setup.sh), so the
# dev edge needs its own copy of the config with those ports.
DEVCONF="${ROOT}/scripts/dev/nginx.dev.conf"
port=8088
if [[ "$build" != "me" ]]; then
  api_port="$(sed -n 's/^PGEO_API_PORT=//p' "pgeo/builds/${build}.env" 2>/dev/null || true)"
  [[ -n "$api_port" ]] || { echo "no pgeo/builds/${build}.env (scripts/pgeo_setup.sh --build ${build})" >&2; exit 1; }
  off=$((api_port - 4500))
  port=$((8088 + off))
  DEVCONF="$(mktemp --suffix=.conf)"
  sed -e "s/127\.0\.0\.1:4500/127.0.0.1:$((4500 + off))/g" \
      -e "s/127\.0\.0\.1:4700/127.0.0.1:$((4700 + off))/g" \
      -e "s/127\.0\.0\.1:8088/127.0.0.1:${port}/g" \
      "${ROOT}/scripts/dev/nginx.dev.conf" > "$DEVCONF"
  chmod 0644 "$DEVCONF"
fi
echo "serving build ${build} at http://127.0.0.1:${port}"
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
trap 'rm -f "${MAP}" "${REGION}"' EXIT
name=pelias_maine_web_dev   # the default build keeps its historical name
[[ "$build" == "me" ]] || name="pgeo_web_dev_${build}"
docker run --rm --name "$name" --network host \
  -v "${ROOT}/web:/srv/pelias-demo:ro" \
  -v "${REGION}:/srv/pelias-demo/region.json:ro" \
  -v "${TILES}:/srv/tiles/${build}.pmtiles:ro" \
  -v "${ROOT}/scripts/dev/nginx.dev.main.conf:/etc/nginx/nginx.conf:ro" \
  -v "${DEVCONF}:/etc/nginx/conf.d/default.conf:ro" \
  -v "${MAP}:/etc/nginx/conf.d/apikey.map:ro" \
  -v "${ROOT}/infra/ansible/roles/edge/files/apikey.js:/etc/nginx/njs/apikey.js:ro" \
  nginx:1.29-alpine
