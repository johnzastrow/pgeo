"""USGS GNIS Domestic Names (pipe-delimited text) -> Pelias CSV.

All features are loaded as layer `venue` with `category` = GNIS feature class
(lake, summit, island, civil, populated_place, ...). Keeping them out of the admin
layers (locality, county) avoids competing with Who's On First, which owns the admin
hierarchy and point-in-polygon lookups. Revisit with the Phase 10 harness.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from .common import MAINE_BBOX, ExportResult, export_csv

SQL = """
WITH src AS (
    SELECT *
    FROM read_csv(?, delim = '|', header = true, all_varchar = true, quote = '')
)
SELECT
    feature_id                                   AS id,
    'gnis'                                       AS source,
    'venue'                                      AS layer,
    trim(feature_name)                           AS name,
    CAST(prim_lat_dec AS DOUBLE)                 AS lat,
    CAST(prim_long_dec AS DOUBLE)                AS lon,
    lower(replace(feature_class, ' ', '_'))      AS category,
    to_json({
        feature_id: feature_id,
        feature_class: feature_class,
        county: county_name,
        map_name: map_name,
        date_edited: nullif(date_edited, '')
    })                                           AS addendum_json_gnis
FROM src
-- Drop features whose primary point lies outside Maine (e.g. a river mouth in
-- New Brunswick, the Atlantic Ocean point off North Carolina).
WHERE CAST(prim_lat_dec AS DOUBLE) BETWEEN ? AND ?
  AND CAST(prim_long_dec AS DOUBLE) BETWEEN ? AND ?
  AND (? OR feature_name NOT ILIKE '%(historical)%')
ORDER BY CAST(feature_id AS BIGINT)
"""


def convert(
    con: duckdb.DuckDBPyConnection, src: Path, out: Path, include_historical: bool = False
) -> ExportResult:
    lon_min, lat_min, lon_max, lat_max = MAINE_BBOX
    params = [str(src), lat_min, lat_max, lon_min, lon_max, include_historical]
    return export_csv(con, SQL, params, out)
