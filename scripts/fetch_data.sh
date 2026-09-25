#!/usr/bin/env bash
# Download the raw inputs for a pgeo build into data/raw/.
#
# A build covers one or more US states, named in regions/regions.json. Per-state inputs land in
# data/raw/<build>/, national ones (they are the same for every build) in data/raw/shared/.
#
#   scripts/fetch_data.sh --build ny all
#   scripts/fetch_data.sh --build me,nh,vt osm oa
#   scripts/fetch_data.sh --build me all        # the default build is "me"
#
# Targets:
#   per-build  osm gnis oa overture overture_themes basemap
#   national   zcta boundary wof neighbours
#   all        everything except overture_themes (evaluation only, several GB)
#
# Pinned versions live below; bump them deliberately and re-run. Nothing here needs the Pelias
# CLI: OpenAddresses and Who's on First are fetched straight from their publishers, so a pgeo
# build has no Pelias dependency (docs/DATA_PIPELINE.md section 2). DuckDB comes from prep/,
# so there is no separate CLI to install or keep in step.
set -euo pipefail

OVERTURE_RELEASE="${OVERTURE_RELEASE:-2026-08-19.0}"
PROTOMAPS_BUILD="${PROTOMAPS_BUILD:-20260925}"
ZCTA_YEAR="${ZCTA_YEAR:-2025}"
WOF_DIST="${WOF_DIST:-https://data.geocode.earth/wof/dist/sqlite}"
OA_RUNS="${OA_RUNS:-https://results.openaddresses.io/latest/run}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REGISTRY="${ROOT}/regions/regions.json"
SHARED="${ROOT}/data/raw/shared"

build=me
while [[ $# -gt 0 && "$1" == --* ]]; do
  case "$1" in
    --build) build="${2:?--build needs a name or a comma-separated list of state codes}"; shift 2 ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

[[ -f "$REGISTRY" ]] || { echo "missing $REGISTRY (scripts/gen_regions.py)" >&2; exit 1; }

# Resolve the build to its member states: either a named build or a bare list of state codes.
states="$(jq -r --arg b "$build" '.builds[$b].states // empty | join(" ")' "$REGISTRY")"
if [[ -z "$states" ]]; then
  states="$(tr ',-' '  ' <<<"${build^^}")"
  for st in $states; do
    jq -e --arg s "$st" '.states[$s]' "$REGISTRY" >/dev/null \
      || { echo "unknown state or build: $st (see regions/regions.json)" >&2; exit 2; }
  done
  # The build names a directory, so "me,nh,vt" becomes "me-nh-vt" (already dashed is fine).
  build="$(tr ',' '-' <<<"${build,,}")"
fi
RAW="${ROOT}/data/raw/${build}"

field() {  # field STATE KEY
  jq -r --arg s "$1" --arg k "$2" '.states[$s][$k] // empty' "$REGISTRY"
}

bbox() {  # the union of the member states' boxes, as "xmin,ymin,xmax,ymax"
  # `--args` has to follow the input file: everything after it is a positional argument.
  # shellcheck disable=SC2086 - $states is a deliberate word list
  jq -r '
    .states as $s
    | [ $ARGS.positional[] | $s[.].bbox ] as $b
    | [ ([$b[][0]] | min), ([$b[][1]] | min), ([$b[][2]] | max), ([$b[][3]] | max) ]
    | @csv' "$REGISTRY" --args $states
}

duck() {  # run a SQL script on stdin through the DuckDB pinned in prep/
  # Not the duckdb CLI: prep already depends on the library at a pinned version, so using it
  # here means one less thing to install and one less version to keep in step.
  uv run --project "${ROOT}/prep" python -c \
    'import sys, duckdb; duckdb.connect().execute(sys.stdin.read())'
}

fetch() {  # fetch URL DEST: download atomically, fail on HTTP errors
  local url="$1" dest="$2"
  mkdir -p "$(dirname "$dest")"
  curl -sSfL --retry 3 -o "${dest}.part" "$url"
  mv "${dest}.part" "$dest"
  echo "fetched $(basename "$dest") ($(du -h "$dest" | cut -f1))"
}

osm() {
  local base="https://download.geofabrik.de/north-america/us" slug
  for st in $states; do
    slug="$(field "$st" geofabrik)"
    [[ -n "$slug" ]] || { echo "no Geofabrik extract recorded for $st" >&2; exit 1; }
    fetch "${base}/${slug}-latest.osm.pbf" "${RAW}/osm/${slug}-latest.osm.pbf"
    fetch "${base}/${slug}-latest.osm.pbf.md5" "${RAW}/osm/${slug}-latest.osm.pbf.md5"
    (cd "${RAW}/osm" && md5sum -c "${slug}-latest.osm.pbf.md5")
  done
}

gnis() {
  for st in $states; do
    fetch "$(field "$st" gnis)" "${RAW}/gnis/DomesticNames_${st}_Text.zip"
  done
}

OA_HEADER='LON,LAT,NUMBER,STREET,UNIT,CITY,DISTRICT,REGION,POSTCODE,ID,HASH'

oa() {
  # Each OpenAddresses run archive holds <source>.csv in the legacy column layout, plus a README
  # and a .vrt. Sources come and go: one listed in the registry but absent from the latest run is
  # reported and skipped, not fatal. A present-but-wrong archive is fatal.
  local missing=0 src zip csv
  for st in $states; do
    while read -r src; do
      [[ -n "$src" ]] || continue
      zip="${RAW}/oa/${src}.zip"; csv="${RAW}/oa/${src}.csv"
      mkdir -p "$(dirname "$zip")"
      if ! curl -sSfL --retry 2 -o "${zip}.part" "${OA_RUNS}/${src}.zip" 2>/dev/null; then
        rm -f "${zip}.part"; echo "  no current run for ${src}"; missing=$((missing + 1)); continue
      fi
      mv "${zip}.part" "$zip"
      # Extract the member by its exact name into a path of our own; never trust archive paths.
      unzip -p -- "$zip" "${src}.csv" > "${csv}.part"
      # The run archives are CRLF; DuckDB reads that fine, so only the check has to allow for it.
      if [[ ! -s "${csv}.part" || "$(head -1 "${csv}.part" | tr -d '\r')" != "$OA_HEADER" ]]; then
        rm -f "${csv}.part" "$zip"
        echo "${src}: run archive has no ${src}.csv with the expected header" >&2
        exit 1
      fi
      mv "${csv}.part" "$csv"; rm -f "$zip"
      echo "fetched ${src}.csv ($(du -h "$csv" | cut -f1), $(($(wc -l < "$csv") - 1)) rows)"
    done < <(jq -r --arg s "$st" '.states[$s].openaddresses[]? // empty' "$REGISTRY")
  done
  [[ $missing -eq 0 ]] || echo "$missing OpenAddresses source(s) had no current run"
}

wof() {
  # National admin and postcode distributions; the same files serve every build.
  local dir="${ROOT}/data/pelias/whosonfirst/sqlite" f
  for f in whosonfirst-data-admin-us-latest.db whosonfirst-data-postalcode-us-latest.db; do
    if [[ -f "${dir}/${f}" ]]; then echo "have ${f}"; continue; fi
    fetch "${WOF_DIST}/${f}.bz2" "${dir}/${f}.bz2"
    bunzip2 "${dir}/${f}.bz2"
    echo "expanded ${f} ($(du -h "${dir}/${f}" | cut -f1))"
  done
}

neighbours() {
  # Canada and Mexico, as single country polygons, for subtracting from the region clip.
  #
  # The clip buffers the region by ~300 m so an unbuffered boundary does not drop piers and
  # island shoreline. At a coast that is right; at an international land border it admits a strip
  # of the other country, and New York held "Akwesasne Canada Post" 77 m outside the state,
  # labelled ", NY, USA". Subtracting these two leaves the seaward buffer untouched, because
  # there is nothing out there to subtract.
  #
  # One record each rather than the admin distributions, which are 176 MB and 230 MB compressed
  # for two polygons. The ids are Who's on First country records, verified by name and ISO code
  # on fetch - 85633057 looks like Mexico's and is Chile's.
  local dir="${SHARED}/neighbours" id name p
  mkdir -p "$dir"
  for id in 85633041:CA:Canada 85633293:MX:Mexico; do
    name="${id##*:}"; iso="${id#*:}"; iso="${iso%%:*}"; id="${id%%:*}"
    local out="${dir}/${id}.geojson"
    if [[ -f "$out" ]]; then echo "have ${name} (${id})"; continue; fi
    p="$(echo "$id" | sed 's/.\{3\}/&\//g; s|/$||')"
    fetch "https://data.whosonfirst.org/${p}/${id}.geojson" "$out"
    # Refuse a file that is not the country it claims to be: the path is built from an id, and a
    # wrong id returns a perfectly valid polygon for somewhere else.
    if ! grep -q "\"iso:country\":\"${iso}\"" "$out"; then
      echo "  ${out} is not ${name} (${iso}); removing" >&2; rm -f "$out"; exit 1
    fi
    echo "fetched ${name} ($(du -h "$out" | cut -f1))"
  done
}

zcta() {
  fetch "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/${ZCTA_YEAR}_Gazetteer/${ZCTA_YEAR}_Gaz_zcta_national.zip" \
    "${SHARED}/zcta/${ZCTA_YEAR}_Gaz_zcta_national.zip"
  # Which state each ZCTA belongs to. The Gazetteer gives a point and no state, and deciding
  # from geometry gets the coast wrong: against the cartographic state polygon, Maine loses
  # Peaks Island, Cranberry Isles and six other island and shoreline ZCTAs, and gains one in
  # New Hampshire. This relationship file states the answer, by land area, per county.
  fetch "https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/tab20_zcta520_county20_natl.txt" \
    "${SHARED}/zcta/tab20_zcta520_county20_natl.txt"
}

boundary() {
  fetch "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_state_500k.zip" \
    "${SHARED}/boundary/cb_2024_us_state_500k.zip"
}

overture() {
  local out="${RAW}/overture/places_${OVERTURE_RELEASE}_bbox.parquet"
  local xmin ymin xmax ymax
  IFS=, read -r xmin ymin xmax ymax < <(bbox)
  mkdir -p "$(dirname "$out")"
  # Anonymous read of the public Overture bucket; bbox prefilter only, clipping happens in prep.
  duck <<SQL
    INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';
    COPY (
      SELECT * FROM read_parquet('s3://overturemaps-us-west-2/release/${OVERTURE_RELEASE}/theme=places/type=place/*',
                                 hive_partitioning=1)
      WHERE bbox.xmin BETWEEN ${xmin} AND ${xmax} AND bbox.ymin BETWEEN ${ymin} AND ${ymax}
    ) TO '${out}.part' (FORMAT parquet, COMPRESSION zstd);
SQL
  mv "${out}.part" "$out"
  echo "fetched $(basename "$out") ($(du -h "$out" | cut -f1))"
}

overture_themes() {
  # Other Overture themes, kept as raw inputs for evaluation and Phase 10 (PostGIS).
  local r="$OVERTURE_RELEASE" base="s3://overturemaps-us-west-2/release/${OVERTURE_RELEASE}"
  local xmin ymin xmax ymax dir="${RAW}/overture"
  IFS=, read -r xmin ymin xmax ymax < <(bbox)
  mkdir -p "$dir"
  duck <<SQL
    INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';
    CREATE MACRO inbox(b) AS b.xmin BETWEEN ${xmin} AND ${xmax} AND b.ymin BETWEEN ${ymin} AND ${ymax};
    CREATE MACRO overlaps(b) AS b.xmax >= ${xmin} AND b.xmin <= ${xmax} AND b.ymax >= ${ymin} AND b.ymin <= ${ymax};
    COPY (SELECT * FROM read_parquet('${base}/theme=addresses/type=address/*', hive_partitioning=1)
          WHERE inbox(bbox) AND country='US') TO '${dir}/addresses_${r}_bbox.parquet' (FORMAT parquet, COMPRESSION zstd);
    COPY (SELECT * FROM read_parquet('${base}/theme=divisions/type=division/*', hive_partitioning=1)
          WHERE inbox(bbox)) TO '${dir}/division_${r}_bbox.parquet' (FORMAT parquet, COMPRESSION zstd);
    COPY (SELECT * FROM read_parquet('${base}/theme=divisions/type=division_area/*', hive_partitioning=1)
          WHERE overlaps(bbox) AND country='US') TO '${dir}/division_area_${r}_bbox.parquet' (FORMAT parquet, COMPRESSION zstd);
    COPY (SELECT * FROM read_parquet('${base}/theme=base/type=water/*', hive_partitioning=1)
          WHERE inbox(bbox) AND names.primary IS NOT NULL) TO '${dir}/water_named_${r}_bbox.parquet' (FORMAT parquet, COMPRESSION zstd);
    COPY (SELECT * FROM read_parquet('${base}/theme=base/type=land/*', hive_partitioning=1)
          WHERE inbox(bbox) AND names.primary IS NOT NULL) TO '${dir}/land_named_${r}_bbox.parquet' (FORMAT parquet, COMPRESSION zstd);
    COPY (SELECT * FROM read_parquet('${base}/theme=transportation/type=segment/*', hive_partitioning=1)
          WHERE inbox(bbox) AND subtype='road') TO '${dir}/segment_road_${r}_bbox.parquet' (FORMAT parquet, COMPRESSION zstd);
SQL
  ls -la "$dir"
}

# How far past the region the basemap reaches, as a fraction of the region's span. The geocoder's
# own data is NOT widened by this - only the picture is. Without it the extract stopped exactly at
# the state box, which left nothing to pan into: the demo page floats its panel over the left of
# the map, and the part of the state underneath could not be moved out from behind it. It also
# means a coastal or border town is shown in its surroundings rather than against a blank edge.
# web/js/map.js has the same constant and uses it as the pan limit; keep the two in step.
BASEMAP_MARGIN=0.15

basemap() {
  local out="${RAW}/basemap/${build}.pmtiles"
  mkdir -p "$(dirname "$out")"
  local bb
  bb="$(bbox | awk -F, -v m="$BASEMAP_MARGIN" '{
        dx=($3-$1)*m; dy=($4-$2)*m;
        printf "%.4f,%.4f,%.4f,%.4f", $1-dx, $2-dy, $3+dx, $4+dy }')"
  echo "basemap bbox ${bb} (region widened by ${BASEMAP_MARGIN})"
  # build.protomaps.com keeps only about a week of daily builds, so a pin that was fine last month
  # is a 404 today. Say that plainly rather than leaving the operator with a Go stack trace: the
  # fix is always to re-pin PROTOMAPS_BUILD to a build that still exists.
  local src="https://build.protomaps.com/${PROTOMAPS_BUILD}.pmtiles"
  if ! curl -fsS -o /dev/null -r 0-0 "$src"; then
    echo "fetch_data: the pinned Protomaps build ${PROTOMAPS_BUILD} is not available." >&2
    echo "  build.protomaps.com keeps roughly the last week. Re-pin PROTOMAPS_BUILD in this" >&2
    echo "  script (or pass it in the environment) to a recent date, then run this target again." >&2
    return 1
  fi
  pmtiles extract "$src" "${out}.part" --bbox="$bb"
  mv "${out}.part" "$out"
  echo "fetched $(basename "$out") ($(du -h "$out" | cut -f1))"
}

targets=("${@:-all}")
echo "build ${build}: states ${states// /, }"
for t in "${targets[@]}"; do
  case "$t" in
    all) wof; neighbours; boundary; zcta; osm; gnis; oa; overture; basemap ;;
    osm|gnis|oa|wof|neighbours|zcta|boundary|overture|overture_themes|basemap) "$t" ;;
    *) echo "usage: $0 [--build NAME] [all|osm|gnis|oa|wof|neighbours|zcta|boundary|overture|overture_themes|basemap]" >&2
       exit 2 ;;
  esac
done
