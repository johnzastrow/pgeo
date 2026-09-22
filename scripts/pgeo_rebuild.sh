#!/usr/bin/env bash
# Rebuild pgeo from the downloaded data and re-apply all tuning, then prove it held.
#
#   scripts/pgeo_rebuild.sh [--build NAME] [--profile NAME] [--sources LIST] [--skip-build]
#                           [--skip-gate] [-f EXTRA_COMPOSE_FILE]...
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
# --build selects which stack to rebuild (scripts/pgeo_setup.sh --build NAME set it up). The
# default build "me" behaves exactly as before. Another build talks to its own database and
# ports, keeps the machine-wide tuning profile as it is (every stack shares those files), and
# needs --skip-gate until it has an accuracy set of its own - see GETTING_STARTED.md section 11.
#
# Example (development workstation, pgeo pinned to its own cores):
#   scripts/pgeo_rebuild.sh -f pgeo/compose.pinned.yml
# Details: docs/TUNING_REPORT.md, "Applying the tuning to later builds".
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

profile=""
sources=""
build=me
skip_build=0
skip_gate=0
compose_args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --build) build="${2:?--build needs a name}"; shift 2 ;;
    --profile) profile="${2:?--profile needs a name}"; shift 2 ;;
    --sources) sources="${2:?--sources needs a list}"; shift 2 ;;
    --skip-build) skip_build=1; shift ;;
    --skip-gate) skip_gate=1; shift ;;
    -f) compose_args+=(-f "${2:?-f needs a file}"); shift 2 ;;
    -h|--help) sed -n '2,21p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1 (see --help)" >&2; exit 2 ;;
  esac
done

# A build given as a state list ("me,nh,vt") names a directory, a database and containers, so
# it takes the same dashed form the rest of the pipeline uses.
build="$(tr ',' '-' <<<"${build,,}")"
# Ports and database for this build. Offset 0 is the original stack; any other build has an env
# file written by scripts/pgeo_setup.sh.
api_port=4500; sql_port=4700
if [[ "$build" != "me" ]]; then
  env_file="pgeo/builds/${build}.env"
  [[ -f "$env_file" ]] || { echo "no $env_file; run scripts/pgeo_setup.sh --build $build" >&2; exit 1; }
  get() { sed -n "s/^$1=//p" "$env_file"; }
  db_port="$(get PGEO_DB_PORT)"; api_port="$(get PGEO_API_PORT)"
  sql_port=$((4700 + api_port - 4500))
  pw="$(sed -n 's/^PGEO_DB_PASSWORD=//p' pgeo/pgeo.secrets)"
  export PGEO_BUILD="$build"
  export PGEO_DSN="postgresql://pgeo:${pw}@127.0.0.1:${db_port}/$(get PGEO_DB_NAME)"
  export PGEO_LIBPOSTAL_URL="http://127.0.0.1:$(get PGEO_LIBPOSTAL_PORT)"
fi

if [[ -z "$profile" ]]; then
  profile="$(sed -n 's/^PGEO_TUNING_PROFILE=\([a-z0-9-]*\)$/\1/p' pgeo/tuning/active.vars 2>/dev/null || true)"
  profile="${profile:-workstation}"
fi

pgeo() { uv run --project pgeo "$@"; }
step() { printf '\n== %s\n' "$*"; }

if [[ $skip_build -eq 0 ]]; then
  step "1/4 build (ranking tuning from pgeo/sql is applied here)"
  args=(--build "$build")
  [[ -n "$sources" ]] && args+=(--sources "$sources")
  pgeo pgeo-load build "${args[@]}"
fi

if [[ "$build" == "me" ]]; then
  step "2/4 tuning profile: $profile"
  pgeo pgeo-tune apply "$profile" "${compose_args[@]}"
else
  # One set of server tuning files per machine, mounted by every stack: applying a profile here
  # would move the other builds too. On a host that serves one build this does not arise.
  step "2/4 tuning profile: keeping $profile (shared with the other stacks on this machine)"
  docker restart "pgeo-${build}_api" "pgeo-${build}_rest" >/dev/null
  # Wait for them, or the known answers that follow test a front end that is still starting and
  # report every case as a read error. pgeo-tune does this for the default build; here it is ours.
  for url in "http://127.0.0.1:${api_port}/v1/search?text=a&size=1" \
             "http://127.0.0.1:${sql_port}/v1/search?text=a&size=1"; do
    for _ in $(seq 60); do
      curl -fsS -o /dev/null --max-time 3 "$url" && break
      sleep 2
    done
  done
fi

step "3/4 known-answer checks"
pgeo pgeo-tune verify --build "$build" \
  --url "http://127.0.0.1:${api_port}" --url "http://127.0.0.1:${sql_port}"

if [[ $skip_gate -eq 1 ]]; then
  echo "accuracy gate skipped (--skip-gate)"
  exit 0
fi
if [[ "$build" != "me" ]]; then
  echo "the accuracy gate is Maine's case set; pass --skip-gate for build $build" >&2
  exit 1
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
