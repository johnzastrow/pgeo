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
  read -r -p "   continue? [y/N] " reply
  [[ "$reply" =~ ^[Yy] ]] || exit 1
fi

started=$(date +%s)
STAGES=(fetch prep setup build dump)
index_of() { local i; for i in "${!STAGES[@]}"; do [[ "${STAGES[i]}" == "$1" ]] && { echo "$i"; return; }; done; }
first=$(index_of "$from")
stage_at() { (( $(index_of "$1") >= first )); }

if stage_at fetch; then
  say "1/5 download"
  targets=(wof boundary zcta osm gnis oa overture)
  ((basemap)) && targets+=(basemap)
  scripts/fetch_data.sh --build "$build" "${targets[@]}"
fi

if stage_at prep; then
  say "2/5 prepare (GNIS, ZCTA, Overture -> CSV)"
  (cd prep && uv run pelias-prep all --build "$build")
fi

if stage_at setup; then
  say "3/5 containers"
  scripts/pgeo_setup.sh --build "$build" --profile "$profile"
fi

if stage_at build; then
  say "4/5 load and index"
  # The accuracy gate compares against Maine's case set, so it only applies to Maine.
  gate=(--skip-gate); [[ "$build" == "me" ]] && gate=()
  scripts/pgeo_rebuild.sh --build "$build" --profile "$profile" "${gate[@]}"
fi

if stage_at dump; then
  say "5/5 snapshot"
  scripts/pgeo_dump.sh --build "$build"
fi

mins=$(( ($(date +%s) - started) / 60 ))
say "done in ${mins} min"
cat <<EOF
   Try it:     scripts/dev_web.sh --build ${build}
   Deploy it:  GETTING_STARTED.md, "Prepare the VPS"
   Measure it: uv run --project prep python tests/accuracy/build_cases.py --build ${build} --n 200
EOF
