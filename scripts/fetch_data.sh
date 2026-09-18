#!/usr/bin/env bash
# Download raw inputs for the custom (CSV) sources and the demo basemap into data/raw/.
# Pelias-native sources (OSM, OA, WOF, TIGER) are downloaded by `pelias download` instead.
#
# Usage: scripts/fetch_data.sh [all|osm|gnis|zcta|boundary|overture|overture_themes|basemap]
# Pinned versions live below; bump them deliberately and re-run.
set -euo pipefail

OVERTURE_RELEASE="${OVERTURE_RELEASE:-2026-08-19.0}"
PROTOMAPS_BUILD="${PROTOMAPS_BUILD:-20260918}"
ZCTA_YEAR="${ZCTA_YEAR:-2025}"
# Maine plus a small margin (lon_min,lat_min,lon_max,lat_max)
BBOX="-71.2,42.9,-66.8,47.5"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RAW="${ROOT}/data/raw"

fetch() {  # fetch URL DEST: download atomically, fail on HTTP errors
  local url="$1" dest="$2"
  mkdir -p "$(dirname "$dest")"
  curl -sSfL --retry 3 -o "${dest}.part" "$url"
  mv "${dest}.part" "$dest"
  echo "fetched $(basename "$dest") ($(du -h "$dest" | cut -f1))"
}

osm() {
  local base="https://download.geofabrik.de/north-america/us"
  fetch "${base}/maine-latest.osm.pbf" "${RAW}/osm/maine-latest.osm.pbf"
  fetch "${base}/maine-latest.osm.pbf.md5" "${RAW}/osm/maine-latest.osm.pbf.md5"
  (cd "${RAW}/osm" && md5sum -c maine-latest.osm.pbf.md5)
}

gnis() {
  fetch "https://prd-tnm.s3.amazonaws.com/StagedProducts/GeographicNames/DomesticNames/DomesticNames_ME_Text.zip" \
    "${RAW}/gnis/DomesticNames_ME_Text.zip"
}

zcta() {
  fetch "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/${ZCTA_YEAR}_Gazetteer/${ZCTA_YEAR}_Gaz_zcta_national.zip" \
    "${RAW}/zcta/${ZCTA_YEAR}_Gaz_zcta_national.zip"
}

boundary() {
  fetch "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_state_500k.zip" \
    "${RAW}/boundary/cb_2024_us_state_500k.zip"
}

overture() {
  local out="${RAW}/overture/places_${OVERTURE_RELEASE}_me_bbox.parquet"
  local xmin ymin xmax ymax
  IFS=, read -r xmin ymin xmax ymax <<<"$BBOX"
  mkdir -p "$(dirname "$out")"
  # Anonymous read of the public Overture bucket; bbox prefilter only, clipping happens in prep.
  duckdb -c "
    INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';
    COPY (
      SELECT * FROM read_parquet('s3://overturemaps-us-west-2/release/${OVERTURE_RELEASE}/theme=places/type=place/*',
                                 hive_partitioning=1)
      WHERE bbox.xmin BETWEEN ${xmin} AND ${xmax} AND bbox.ymin BETWEEN ${ymin} AND ${ymax}
    ) TO '${out}.part' (FORMAT parquet, COMPRESSION zstd);"
  mv "${out}.part" "$out"
  echo "fetched $(basename "$out") ($(du -h "$out" | cut -f1))"
}

overture_themes() {
  # Other Overture themes, kept as raw inputs for evaluation and Phase 10 (PostGIS).
  local r="$OVERTURE_RELEASE" base="s3://overturemaps-us-west-2/release/${OVERTURE_RELEASE}"
  local xmin ymin xmax ymax dir="${RAW}/overture"
  IFS=, read -r xmin ymin xmax ymax <<<"$BBOX"
  mkdir -p "$dir"
  duckdb -c "
    INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';
    CREATE MACRO inbox(b) AS b.xmin BETWEEN ${xmin} AND ${xmax} AND b.ymin BETWEEN ${ymin} AND ${ymax};
    CREATE MACRO overlaps(b) AS b.xmax >= ${xmin} AND b.xmin <= ${xmax} AND b.ymax >= ${ymin} AND b.ymin <= ${ymax};
    COPY (SELECT * FROM read_parquet('${base}/theme=addresses/type=address/*', hive_partitioning=1)
          WHERE inbox(bbox) AND country='US') TO '${dir}/addresses_${r}_me_bbox.parquet' (FORMAT parquet, COMPRESSION zstd);
    COPY (SELECT * FROM read_parquet('${base}/theme=divisions/type=division/*', hive_partitioning=1)
          WHERE inbox(bbox)) TO '${dir}/division_${r}_me_bbox.parquet' (FORMAT parquet, COMPRESSION zstd);
    COPY (SELECT * FROM read_parquet('${base}/theme=divisions/type=division_area/*', hive_partitioning=1)
          WHERE overlaps(bbox) AND country='US') TO '${dir}/division_area_${r}_me_bbox.parquet' (FORMAT parquet, COMPRESSION zstd);
    COPY (SELECT * FROM read_parquet('${base}/theme=base/type=water/*', hive_partitioning=1)
          WHERE inbox(bbox) AND names.primary IS NOT NULL) TO '${dir}/water_named_${r}_me_bbox.parquet' (FORMAT parquet, COMPRESSION zstd);
    COPY (SELECT * FROM read_parquet('${base}/theme=base/type=land/*', hive_partitioning=1)
          WHERE inbox(bbox) AND names.primary IS NOT NULL) TO '${dir}/land_named_${r}_me_bbox.parquet' (FORMAT parquet, COMPRESSION zstd);
    COPY (SELECT * FROM read_parquet('${base}/theme=transportation/type=segment/*', hive_partitioning=1)
          WHERE inbox(bbox) AND subtype='road') TO '${dir}/segment_road_${r}_me_bbox.parquet' (FORMAT parquet, COMPRESSION zstd);"
  ls -la "$dir"
}

basemap() {
  local out="${RAW}/basemap/maine.pmtiles"
  mkdir -p "$(dirname "$out")"
  pmtiles extract "https://build.protomaps.com/${PROTOMAPS_BUILD}.pmtiles" "${out}.part" --bbox="$BBOX"
  mv "${out}.part" "$out"
}

case "${1:-all}" in
  all) osm; gnis; zcta; boundary; overture; overture_themes; basemap ;;
  osm|gnis|zcta|boundary|overture|overture_themes|basemap) "$1" ;;
  *) echo "usage: $0 [all|osm|gnis|zcta|boundary|overture|overture_themes|basemap]" >&2; exit 2 ;;
esac
