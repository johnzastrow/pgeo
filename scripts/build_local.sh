#!/usr/bin/env bash
# Full Pelias build for the Maine project on this machine.
#
#   scripts/build_local.sh            # everything, from an empty index
#   scripts/build_local.sh <step>...  # run selected steps, in order
#
# Steps: setup download prepare import up test
# Prerequisites: scripts/bootstrap.sh, projects/pelias_maine/.env (+ optional secrets.env), and
# `uv run pelias-prep all` (in prep/) so data/processed/csv/*.csv exist.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_DIR="${ROOT}/projects/pelias_maine"
PELIAS="${ROOT}/vendor/pelias-docker/pelias"

[[ -x "$PELIAS" ]] || { echo "run scripts/bootstrap.sh first" >&2; exit 1; }
[[ -f "${PROJECT_DIR}/.env" ]] || { echo "create ${PROJECT_DIR}/.env from .env.example" >&2; exit 1; }

DATA_DIR="$(grep -E '^DATA_DIR=' "${PROJECT_DIR}/.env" | tail -1 | cut -d= -f2-)"
[[ "$DATA_DIR" == /* ]] || { echo "DATA_DIR in .env must be an absolute path" >&2; exit 1; }

# The pelias CLI reads .env and docker-compose.yml from the current directory.
cd "$PROJECT_DIR"
p() { echo "+ pelias $*"; "$PELIAS" "$@"; }

step_setup() {
  "${ROOT}/scripts/render_config.sh" "$PROJECT_DIR"
  mkdir -p "${DATA_DIR}"/{csv,elasticsearch,es_snapshots}
  # Custom sources produced by prep/ (GNIS, ZCTA, Overture)
  for f in gnis zcta overture; do
    install -m 0644 "${ROOT}/data/processed/csv/${f}.csv" "${DATA_DIR}/csv/${f}.csv"
  done
  p compose pull
  p elastic start
  p elastic wait
  # Fresh index. `elastic create` fails if it exists; drop explicitly to rebuild.
  if [[ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:9200/pelias)" == "200" ]]; then
    echo "index 'pelias' exists; drop it with: (cd ${PROJECT_DIR} && ${PELIAS} elastic drop)" >&2
    exit 1
  fi
  p elastic create
}

step_download() {
  # Not `download all`: CSV inputs are local files, and transit has no feeds yet.
  p download wof
  p download osm
  p download oa
  p download tiger
  # Interpolation only reads legacy OA CSV; convert the GeoJSON that OA now ships.
  (cd "${ROOT}/prep" && uv run pelias-prep oa-interp)
}

step_prepare() { p prepare all; }
step_import()  { p import all; }
step_up()      { p compose up; }
step_test()    { p test run; }

steps=("$@")
[[ ${#steps[@]} -gt 0 ]] || steps=(setup download prepare import up test)
for s in "${steps[@]}"; do
  case "$s" in
    setup|download|prepare|import|up|test)
      echo "=== ${s} ($(date -Is))"
      "step_${s}"
      ;;
    *) echo "unknown step: ${s}" >&2; exit 2 ;;
  esac
done
echo "=== done ($(date -Is))"
