#!/usr/bin/env bash
# Rebuild pgeo from the downloaded data and re-apply all tuning, then prove it held.
#
#   scripts/pgeo_rebuild.sh [--profile NAME] [--sources LIST] [--skip-build] [--skip-gate]
#                           [-f EXTRA_COMPOSE_FILE]...
#
# Steps (each stops the script on failure):
#   1. pgeo-load build     loads the data, applies the ranking/search tuning in pgeo/sql
#                          (040_functions.sql, 050_api.sql), VACUUM ANALYZE, atomic swap
#   2. pgeo-tune apply     server + front-end profile (pgeo/tuning/profiles/NAME.toml);
#                          default: the active profile, else "workstation"
#   3. pgeo-tune verify    known-answer queries through FastAPI and the pure-SQL edge
#   4. accuracy + gate     1,560 cases and 1,800 fuzz cases against the pure-SQL API,
#                          compared with tests/accuracy/baseline.json
#
# Example (development workstation, pgeo pinned to its own cores):
#   scripts/pgeo_rebuild.sh -f pgeo/compose.pinned.yml
# Details: docs/TUNING_REPORT.md, "Applying the tuning to later builds".
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

profile=""
sources=""
skip_build=0
skip_gate=0
compose_args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) profile="${2:?--profile needs a name}"; shift 2 ;;
    --sources) sources="${2:?--sources needs a list}"; shift 2 ;;
    --skip-build) skip_build=1; shift ;;
    --skip-gate) skip_gate=1; shift ;;
    -f) compose_args+=(-f "${2:?-f needs a file}"); shift 2 ;;
    -h|--help) sed -n '2,21p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1 (see --help)" >&2; exit 2 ;;
  esac
done

if [[ -z "$profile" ]]; then
  profile="$(sed -n 's/^PGEO_TUNING_PROFILE=\([a-z0-9-]*\)$/\1/p' pgeo/tuning/active.vars 2>/dev/null || true)"
  profile="${profile:-workstation}"
fi

pgeo() { uv run --project pgeo "$@"; }
step() { printf '\n== %s\n' "$*"; }

if [[ $skip_build -eq 0 ]]; then
  step "1/4 build (ranking tuning from pgeo/sql is applied here)"
  if [[ -n "$sources" ]]; then pgeo pgeo-load build --sources "$sources"; else pgeo pgeo-load build; fi
fi

step "2/4 tuning profile: $profile"
pgeo pgeo-tune apply "$profile" "${compose_args[@]}"

step "3/4 known-answer checks"
pgeo pgeo-tune verify

if [[ $skip_gate -eq 1 ]]; then
  echo "accuracy gate skipped (--skip-gate)"
  exit 0
fi
step "4/4 accuracy gate"
label="rebuild-$(date +%Y%m%d-%H%M)"
pgeo python tests/accuracy/run_accuracy.py --engine pgeo --base http://127.0.0.1:4700 \
  --label "$label" --concurrency 2
pgeo python tests/accuracy/run_accuracy.py --engine pgeo --base http://127.0.0.1:4700 \
  --label "fuzz-$label" --cases tests/accuracy/fuzz_cases.json --concurrency 2
# keep rebuild runs out of the published report's inputs (report.py reads data/accuracy/*.json)
mkdir -p data/accuracy/rebuilds
mv "data/accuracy/pgeo-$label.json" "data/accuracy/pgeo-fuzz-$label.json" data/accuracy/rebuilds/
pgeo python tests/accuracy/gate.py "data/accuracy/rebuilds/pgeo-$label.json" \
  --fuzz "data/accuracy/rebuilds/pgeo-fuzz-$label.json"
echo "rebuild complete: profile $profile, gate passed"
