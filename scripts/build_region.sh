#!/usr/bin/env bash
# Build a geocoder for a region, from nothing to a dump ready to deploy.
#
#   scripts/build_region.sh --build ny
#   scripts/build_region.sh --build me,nh,vt --profile medium
#   scripts/build_region.sh --build ny --from build       # resume after a failure
#
# Five stages, in order. Each is a script you can also run on its own, and this only puts them
# in the right order with the right arguments:
#
#   fetch    scripts/fetch_data.sh      downloads (once per region; Who's on First is shared)
#   prep     pelias-prep                GNIS, ZCTA and Overture into CSV, clipped to the states
#   setup    scripts/pgeo_setup.sh      containers, passwords, ports for this build
#   build    scripts/pgeo_rebuild.sh    load, index, swap, and check the known answers
#   dump     scripts/pgeo_dump.sh       the file the server restores
#
# Everything it needs is checked before the first download, because the alternative is finding
# out two hours in. Options:
#
#   --build NAME     a build in regions/regions.json, or a list of state codes  (required)
#   --profile NAME   tuning profile for the database         (default: workstation)
#   --from STAGE     start at this stage, skipping earlier ones
#   --no-basemap     skip the demo map's tiles (1.5 GB for a large state)
#   --yes            do not stop for confirmation
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

build=""; profile=workstation; from=fetch; basemap=1; assume_yes=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --build) build="${2:?--build needs a name or a comma-separated list of state codes}"; shift 2 ;;
    --profile) profile="${2:?--profile needs a name}"; shift 2 ;;
    --from) from="${2:?--from needs a stage}"; shift 2 ;;
    --no-basemap) basemap=0; shift ;;
    --yes|-y) assume_yes=1; shift ;;
    -h|--help) sed -n '2,29p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1 (see --help)" >&2; exit 2 ;;
  esac
done
[[ -n "$build" ]] || { echo "--build is required (see --help)" >&2; exit 2; }
build="$(tr ',' '-' <<<"${build,,}")"
case "$from" in fetch|prep|setup|build|dump) ;; *)
  echo "--from must be one of: fetch prep setup build dump" >&2; exit 2 ;; esac

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die() { echo "$*" >&2; exit 1; }

# ---- preflight: everything that can be known before the first byte is downloaded ------------

problems=()
for t in git curl jq unzip bunzip2 ogr2ogr docker uv python3; do
  command -v "$t" >/dev/null || problems+=("missing tool: $t")
done
docker compose version >/dev/null 2>&1 || problems+=("missing: the docker compose plugin")
[[ $basemap -eq 0 ]] || command -v pmtiles >/dev/null \
  || problems+=("missing tool: pmtiles, for the demo basemap (or pass --no-basemap)")

[[ -f regions/regions.json ]] || problems+=("missing regions/regions.json (scripts/gen_regions.py)")
if [[ -f regions/regions.json ]]; then
  states="$(jq -r --arg b "$build" '.builds[$b].states // empty | join(" ")' regions/regions.json)"
  if [[ -z "$states" ]]; then
    states="$(tr '-' ' ' <<<"${build^^}")"
    for st in $states; do
      jq -e --arg s "$st" '.states[$s]' regions/regions.json >/dev/null \
        || problems+=("unknown state or build: $st")
    done
  fi
fi

# The known answers are facts about the region, so they can be written before anything is built -
# and asking for them now rather than at the end is the whole point of checking early.
verify="pgeo/tuning/verify/${build}.json"
if [[ ! -f "$verify" ]]; then
  problems+=("no known answers for this region: $verify
      Copy one and edit it for your region - two well-known places, a street address you can
      check on a map, a coordinate you recognise:
          cp pgeo/tuning/verify/me.json $verify
      They are what tells you the build is sound rather than merely finished.")
fi

# Disk. A state needs roughly ten times its OSM extract, and Who's on First is a shared 5 GB.
free_gb=$(df -BG --output=avail . | tail -1 | tr -dc '0-9')
need_gb=20
[[ -d data/pelias/whosonfirst/sqlite ]] || need_gb=$((need_gb + 6))
(( free_gb >= need_gb )) || problems+=("only ${free_gb} GB free; a build wants about ${need_gb} GB")

if (( ${#problems[@]} )); then
  echo "Cannot start:" >&2
  printf '  - %s\n' "${problems[@]}" >&2
  exit 1
fi

say "build ${build}: ${states// /, }"
echo "   profile ${profile}, stages from '${from}', $( ((basemap)) && echo 'with' || echo 'without') the demo basemap"
echo "   a single small state takes about half an hour; a large one, a few hours, nearly all of"
echo "   it the indexing step"
if [[ $assume_yes -eq 0 ]]; then
  # No terminal to ask: say so rather than hanging on a prompt nobody can see.
  [[ -t 0 ]] || { echo "   not a terminal; pass --yes to run unattended" >&2; exit 1; }
  read -r -p "   continue? [y/N] " reply
  [[ "$reply" =~ ^[Yy] ]] || exit 1
fi

started=$(date +%s)
STAGES=(fetch prep setup build dump)
index_of() { local i; for i in "${!STAGES[@]}"; do [[ "${STAGES[i]}" == "$1" ]] && { echo "$i"; return; }; done; }
first=$(index_of "$from")
stage_at() { (( $(index_of "$1") >= first )); }
stage_name=""

mins_since() { echo $(( ($(date +%s) - $1) / 60 )); }

# Most people run this by hand and watch it. Each stage says what it is about to do, and the long
# ones say they are still going, because the indexing step is quiet for minutes at a time and a
# quiet terminal looks like a hung one.
run_stage() {
  local label="$1" note="$2"; shift 2
  stage_name="$label"
  printf '\n\033[1m== %s\033[0m\n' "$label"
  [[ -z "$note" ]] || echo "   $note"
  local t0 pid hb rc
  t0=$(date +%s)
  "$@" &
  pid=$!
  ( while kill -0 "$pid" 2>/dev/null; do
      sleep 60
      kill -0 "$pid" 2>/dev/null && printf '   ... still going, %s min so far\n' "$(mins_since "$t0")"
    done ) &
  hb=$!
  wait "$pid"; rc=$?
  kill "$hb" 2>/dev/null || true
  wait "$hb" 2>/dev/null || true
  if (( rc != 0 )); then
    printf '\n\033[1m!! %s failed after %s min\033[0m\n' "$label" "$(mins_since "$t0")" >&2
    echo "   Fix it, then pick up where it stopped:" >&2
    echo "     scripts/build_region.sh --build ${build} --from ${current_stage}" >&2
    exit $rc
  fi
  printf '   %s min\n' "$(mins_since "$t0")"
}

if stage_at fetch; then
  current_stage=fetch
  targets=(wof boundary zcta osm gnis oa overture)
  ((basemap)) && targets+=(basemap)
  run_stage "1/5 download" \
    "six sources from their publishers; Who's on First is 5 GB and only downloads once" \
    scripts/fetch_data.sh --build "$build" "${targets[@]}"
fi

if stage_at prep; then
  current_stage=prep
  run_stage "2/5 prepare" \
    "GNIS, Census ZCTAs and Overture into CSV, clipped to the states you named" \
    bash -c 'cd prep && uv run pelias-prep all --build "$1"' _ "$build"
fi

if stage_at setup; then
  current_stage=setup
  run_stage "3/5 containers" \
    "PostgreSQL, PostgREST and the API for this build; the first run compiles libpostal" \
    scripts/pgeo_setup.sh --build "$build" --profile "$profile"
fi

if stage_at build; then
  current_stage=build
  # The accuracy gate compares against Maine's case set, so it only applies to Maine.
  gate=(--skip-gate); [[ "$build" == "me" ]] && gate=()
  run_stage "4/5 load and index" \
    "the long one: minutes for a small state, half an hour for a large one, and quiet throughout" \
    scripts/pgeo_rebuild.sh --build "$build" --profile "$profile" "${gate[@]}"
fi

if stage_at dump; then
  current_stage=dump
  run_stage "5/5 snapshot" "the file a server restores" scripts/pgeo_dump.sh --build "$build"
fi

# What was actually built, so the last thing on screen is the answer rather than a log line.
db_container=pgeo_db; db_name=pgeo
if [[ "$build" != "me" ]]; then
  db_container="pgeo-${build}_db"
  db_name="$(sed -n 's/^PGEO_DB_NAME=//p' "pgeo/builds/${build}.env" 2>/dev/null)"
fi
printf '\n\033[1m== built %s in %s min\033[0m\n' "$build" "$(mins_since "$started")"
docker exec "$db_container" psql -U pgeo -d "$db_name" -Atc "
  SELECT '   ' || rpad(layer, 16) || to_char(count(*), 'FM999,999,999')
  FROM pgeo.feature GROUP BY layer ORDER BY count(*) DESC" 2>/dev/null || true
docker exec "$db_container" psql -U pgeo -d "$db_name" -Atc "
  SELECT '   ' || rpad('database', 16) || pg_size_pretty(pg_database_size('$db_name'))" 2>/dev/null || true
dump_dir="data/pgeo$([[ "$build" == me ]] || echo "-${build}")/dumps"
latest="$(ls -t "${dump_dir}"/*.dump 2>/dev/null | head -1 || true)"
[[ -z "$latest" ]] || printf '   %s%s\n' "$(printf '%-16s' dump)" "$latest"

sql_port=$((4700 + offset))
cat <<EOF

Next, in the order most people want them:

  See it        scripts/dev_web.sh --build ${build}
  Ask it        curl -s 'http://127.0.0.1:${sql_port}/v1/search?text=<a+place+you+know>&size=1' | jq -r '.features[0].properties.label'
  Measure it    uv run --project prep python tests/accuracy/build_cases.py --build ${build} --n 200
                uv run --project pgeo python tests/accuracy/run_accuracy.py --engine pgeo \\
                  --base http://127.0.0.1:${sql_port} --cases tests/accuracy/cases_${build}.json --label ${build}
  Serve it      scripts/new_host.sh --build ${build}     then GETTING_STARTED.md section 5
EOF
