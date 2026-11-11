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
#   national   zcta boundary wof neighbours coarse
#   shared     basemap_world (low-zoom world tiles for the demo map; not part of "all")
#   all        everything except overture_themes (evaluation only, several GB)
#
# coarse is the worldwide backbone (countries, regions, counties, localadmins and the larger
# localities from Overture divisions), shared by every build like the national targets.
#
# Pinned versions live below; bump them deliberately and re-run. Nothing here needs the Pelias
# CLI: OpenAddresses and Who's on First are fetched straight from their publishers, so a pgeo
# build has no Pelias dependency (docs/DATA_PIPELINE.md section 2). DuckDB comes from prep/,
# so there is no separate CLI to install or keep in step.
set -euo pipefail

# Overture and Protomaps both delete old releases (Overture keeps about two months, Protomaps about
# a week), so a fixed default stops working on its own. They default to "latest", resolved from
# each publisher's own index when a target needs it. Pass a value to pin a build exactly.
OVERTURE_RELEASE="${OVERTURE_RELEASE:-latest}"
PROTOMAPS_BUILD="${PROTOMAPS_BUILD:-latest}"
ZCTA_YEAR="${ZCTA_YEAR:-2025}"
WOF_DIST="${WOF_DIST:-https://data.geocode.earth/wof/dist/sqlite}"
# OpenAddresses publishes each source's latest successful run through its batch service. The older
# results.openaddresses.io/latest/run archives are not kept current: for New Hampshire's statewide
# source they hold 610,582 points with no street numbers or names, where the current run holds
# 495,886 valid addresses, and Maine's statewide file there was missing 63,000 rows.
OA_API="${OA_API:-https://batch.openaddresses.io/api}"
OA_OUTPUT="${OA_OUTPUT:-https://v2.openaddresses.io/batch-prod/job}"

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
  # For each registered source: ask the batch service for its latest address layer, take that
  # layer's job if it succeeded, and download the job's GeoJSON output. That is converted to the
  # legacy column layout (OA_HEADER) the loader reads, so nothing downstream changes. A source
  # listed in the registry but with no successful run is reported and skipped, not fatal; a
  # download that is not the expected shape is fatal.
  local missing=0 src csv id job status updated rows
  for st in $states; do
    while read -r src; do
      [[ -n "$src" ]] || continue
      # The name goes into a URL and a path. The registry is ours, but check it anyway.
      [[ "$src" =~ ^[a-z]{2}(/[a-z0-9_-]+)+$ ]] || { echo "refusing odd OpenAddresses source name: ${src}" >&2; exit 1; }
      csv="${RAW}/oa/${src}.csv"
      mkdir -p "$(dirname "$csv")"
      # Newest address layer for the source; a source can list more than one.
      read -r id updated < <(curl -sSfL --retry 2 "${OA_API}/data?source=${src}&layer=addresses" 2>/dev/null \
        | jq -r 'map(select(.layer == "addresses")) | sort_by(.updated) | last | "\(.id // "") \(.updated // "")"' || true)
      if [[ ! "${id:-}" =~ ^[0-9]+$ ]]; then
        echo "  no current run for ${src}"; missing=$((missing + 1)); continue
      fi
      job="$(curl -sSfL --retry 2 "${OA_API}/data/${id}" 2>/dev/null | jq -r '.job // empty' || true)"
      status="$(curl -sSfL --retry 2 "${OA_API}/job/${job}" 2>/dev/null | jq -r '.status // empty' || true)"
      if [[ ! "$job" =~ ^[0-9]+$ || "$status" != "Success" ]]; then
        echo "  no successful run for ${src} (job '${job}', status '${status}')"; missing=$((missing + 1)); continue
      fi
      curl -sSfL --retry 2 -o "${csv}.gz.part" "${OA_OUTPUT}/${job}/source.geojson.gz"
      gzip -t "${csv}.gz.part" || { rm -f "${csv}.gz.part"; echo "${src}: job ${job} output is not a gzip file" >&2; exit 1; }
      # One GeoJSON feature per line. Fields are written unquoted unless they need it, as the legacy
      # files were, so an empty field stays an empty field. Features without a point are dropped.
      { echo "$OA_HEADER"
        gzip -dc "${csv}.gz.part" | jq -r '
          def q: tostring | if test("[\",\r\n]") then "\"" + gsub("\""; "\"\"") + "\"" else . end;
          select(.geometry.type == "Point") | .properties as $p
          | [.geometry.coordinates[0], .geometry.coordinates[1], $p.number, $p.street, $p.unit,
             $p.city, $p.district, $p.region, $p.postcode, $p.id, $p.hash]
          | map(if . == null then "" else q end) | join(",")'
      } > "${csv}.part"
      rm -f "${csv}.gz.part"
      rows=$(($(wc -l < "${csv}.part") - 1))
      if [[ $rows -le 0 ]]; then
        rm -f "${csv}.part"; echo "${src}: job ${job} produced no rows" >&2; exit 1
      fi
      mv "${csv}.part" "$csv"
      echo "fetched ${src}.csv (${rows} rows, job ${job}, run of ${updated%%T*})"
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

# "latest" -> the newest Overture release, from the STAC catalogue Overture publishes.
resolve_overture() {
  [[ "$OVERTURE_RELEASE" == latest ]] || return 0
  OVERTURE_RELEASE="$(curl -fsS https://stac.overturemaps.org/catalog.json | jq -r .latest)"
  [[ "$OVERTURE_RELEASE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}\.[0-9]+$ ]] || { echo "fetch_data: could not resolve the latest Overture release" >&2; return 1; }
  echo "Overture release: ${OVERTURE_RELEASE} (latest)"
}

coarse_areas() {
  # The boundaries of the coarse tier's regions and countries (land polygon of each), simplified
  # to about 1 km, for reverse geocoding outside a build's own region: which state and country
  # contain a point. About 29 MB of WKB for 4,200 shapes. Overture's own geometry, not Natural Earth.
  local areas="$1" base="$2"
  if [[ -f "$areas" ]]; then echo "have $(basename "$areas")"; return 0; fi
  duck <<SQL
    INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';
    COPY (
      SELECT division_id, subtype, country, region,
             ST_AsWKB(ST_SimplifyPreserveTopology(geometry, 0.01)) AS wkb
      FROM (SELECT *, row_number() OVER (PARTITION BY division_id
                                         ORDER BY coalesce(is_land, false) DESC,
                                                  coalesce(is_territorial, false), id) AS rn
            FROM read_parquet('${base}/type=division_area/*', hive_partitioning=1)
            WHERE subtype IN ('country', 'dependency', 'region')) WHERE rn = 1
      ORDER BY division_id
    ) TO '${areas}.part' (FORMAT parquet, COMPRESSION zstd);
SQL
  mv "${areas}.part" "$areas"
  find "$(dirname "$areas")" -maxdepth 1 -name 'areas_*.parquet' ! -name "$(basename "$areas")" -delete
  echo "fetched $(basename "$areas") ($(du -h "$areas" | cut -f1))"
}

coarse() {
  # The coarse tier: a downsampled worldwide set of Overture divisions, so a build holding a few
  # states can still place "Oslo, Norway" or "Sacramento, CA" at low precision. Kept: every
  # country, dependency, region, county and localadmin; localities in the US, plus elsewhere the
  # cities and towns and anything with a population of 1,000 or more. Bounding boxes come from
  # the division_area polygons (the land area when a division has several). A US state's point is
  # replaced by a point on its polygon where it falls outside it. Shared by every build.
  resolve_overture
  local out="${SHARED}/coarse/divisions_${OVERTURE_RELEASE}.parquet"
  local areas="${SHARED}/coarse/areas_${OVERTURE_RELEASE}.parquet"
  mkdir -p "$(dirname "$out")"
  local base="s3://overturemaps-us-west-2/release/${OVERTURE_RELEASE}/theme=divisions"
  coarse_areas "$areas" "$base"
  if [[ -f "$out" ]]; then echo "have $(basename "$out")"; return 0; fi
  duck <<SQL
    INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';
    CREATE TEMP TABLE area AS
      SELECT division_id, bbox FROM (
        SELECT division_id, bbox,
               row_number() OVER (PARTITION BY division_id
                                  ORDER BY coalesce(is_land, false) DESC,
                                           coalesce(is_territorial, false), id) AS rn
        FROM read_parquet('${base}/type=division_area/*', hive_partitioning=1)
        WHERE subtype IN ('country', 'dependency', 'region', 'county', 'localadmin')) WHERE rn = 1;
    -- the land polygon of each US state, to check the state's point against
    CREATE TEMP TABLE us_region_poly AS
      SELECT division_id, geometry FROM (
        SELECT division_id, geometry,
               row_number() OVER (PARTITION BY division_id
                                  ORDER BY coalesce(is_land, false) DESC,
                                           coalesce(is_territorial, false), id) AS rn
        FROM read_parquet('${base}/type=division_area/*', hive_partitioning=1)
        WHERE subtype = 'region' AND country = 'US') WHERE rn = 1;
    COPY (
      SELECT d.id, d.subtype, d.class, d.country, d.region,
             d.names.primary AS name_primary, d.names.common['en'] AS name_en,
             d.population,
             CASE WHEN p.geometry IS NOT NULL AND NOT ST_Within(d.geometry, p.geometry)
                  THEN ST_X(ST_PointOnSurface(p.geometry)) ELSE ST_X(d.geometry) END AS lon,
             CASE WHEN p.geometry IS NOT NULL AND NOT ST_Within(d.geometry, p.geometry)
                  THEN ST_Y(ST_PointOnSurface(p.geometry)) ELSE ST_Y(d.geometry) END AS lat,
             list_filter(d.hierarchies[1], x -> x.subtype = 'county')[1].division_id AS county_id,
             a.bbox.xmin AS xmin, a.bbox.ymin AS ymin, a.bbox.xmax AS xmax, a.bbox.ymax AS ymax
      FROM read_parquet('${base}/type=division/*', hive_partitioning=1) d
      LEFT JOIN area a ON a.division_id = d.id
      LEFT JOIN us_region_poly p ON p.division_id = d.id AND d.subtype = 'region'
      WHERE d.names.primary IS NOT NULL
        AND (d.subtype IN ('country', 'dependency', 'region', 'county', 'localadmin')
             OR (d.subtype = 'locality'
                 AND (d.country = 'US' OR d.class IN ('city', 'town') OR d.population >= 1000)))
      ORDER BY d.id
    ) TO '${out}.part' (FORMAT parquet, COMPRESSION zstd);
SQL
  mv "${out}.part" "$out"
  # the loader reads exactly one release; drop the ones from older releases
  find "$(dirname "$out")" -maxdepth 1 -name 'divisions_*.parquet' ! -name "$(basename "$out")" -delete
  echo "fetched $(basename "$out") ($(du -h "$out" | cut -f1))"
}

# "latest" -> the newest daily Protomaps build, from build-metadata.protomaps.dev.
resolve_protomaps() {
  [[ "$PROTOMAPS_BUILD" == latest ]] || return 0
  PROTOMAPS_BUILD="$(curl -fsS https://build-metadata.protomaps.dev/builds.json | jq -r '[.[].key | select(test("^[0-9]{8}\\.pmtiles$"))] | sort | last | rtrimstr(".pmtiles")')"
  [[ "$PROTOMAPS_BUILD" =~ ^[0-9]{8}$ ]] || { echo "fetch_data: could not resolve the latest Protomaps build" >&2; return 1; }
  echo "Protomaps build: ${PROTOMAPS_BUILD} (latest)"
}

overture() {
  resolve_overture
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
  # prep reads exactly one places extract; drop the ones from older releases.
  find "$(dirname "$out")" -maxdepth 1 -name 'places_*_bbox.parquet' ! -name "$(basename "$out")" -delete
  echo "fetched $(basename "$out") ($(du -h "$out" | cut -f1))"
}

overture_themes() {
  # Other Overture themes, kept as raw inputs for evaluation and Phase 10 (PostGIS).
  resolve_overture
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
  # The basemap is for the demo page only; the geocoder does not need it. Without the pmtiles
  # tool (the build image does not carry it) skip it rather than fail the whole fetch.
  if ! command -v pmtiles >/dev/null 2>&1; then
    echo "basemap: skipped, pmtiles is not installed (only the demo page's map needs it)"
    return 0
  fi
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
  resolve_protomaps
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

# The low-zoom world basemap, so the demo map shows the whole globe when zoomed out instead of
# only the build's region. One file serves every build, so it lives in shared/ and is not part of
# "all". Zoom 6 is about 45 MB (zoom 5 is 15 MB, zoom 7 is 190 MB); web/js/map.js draws it below
# zoom 7 and the regional file above. Serve it as /tiles/world.pmtiles and add
# "world_tiles": "/tiles/world.pmtiles" to region.json (docs/CONFIGURATION.md).
WORLD_MAXZOOM=6

basemap_world() {
  if ! command -v pmtiles >/dev/null 2>&1; then
    echo "basemap_world: skipped, pmtiles is not installed (only the demo page's map needs it)"
    return 0
  fi
  local out="${SHARED}/basemap/world-z${WORLD_MAXZOOM}.pmtiles"
  if [[ -s "$out" ]]; then
    echo "basemap_world: $(basename "$out") already present, skipping"
    return 0
  fi
  mkdir -p "$(dirname "$out")"
  resolve_protomaps
  local src="https://build.protomaps.com/${PROTOMAPS_BUILD}.pmtiles"
  if ! curl -fsS -o /dev/null -r 0-0 "$src"; then
    echo "fetch_data: the pinned Protomaps build ${PROTOMAPS_BUILD} is not available." >&2
    echo "  Re-pin PROTOMAPS_BUILD to a recent date, then run this target again." >&2
    return 1
  fi
  # No --bbox: the whole world, down to WORLD_MAXZOOM only.
  pmtiles extract "$src" "${out}.part" --maxzoom="$WORLD_MAXZOOM"
  mv "${out}.part" "$out"
  echo "fetched $(basename "$out") ($(du -h "$out" | cut -f1))"
}

targets=("${@:-all}")
echo "build ${build}: states ${states// /, }"
for t in "${targets[@]}"; do
  case "$t" in
    all) wof; neighbours; boundary; zcta; coarse; osm; gnis; oa; overture; basemap ;;
    osm|gnis|oa|wof|neighbours|zcta|coarse|boundary|overture|overture_themes|basemap|basemap_world) "$t" ;;
    *) echo "usage: $0 [--build NAME] [all|osm|gnis|oa|wof|neighbours|zcta|coarse|boundary|overture|overture_themes|basemap|basemap_world]" >&2
       exit 2 ;;
  esac
done
