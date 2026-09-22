"""USGS GNIS Domestic Names (pipe-delimited text) -> Pelias CSV.

All features are loaded as layer `venue` with `category` = GNIS feature class
(lake, summit, island, civil, populated_place, ...). Keeping them out of the admin
layers (locality, county) avoids competing with Who's On First, which owns the admin
hierarchy and point-in-polygon lookups. Revisit with the Phase 10 harness.

GNIS publishes one file per state and a build may cover several, so each file is filtered
against its own state's box and the results are unioned. Filtering against the union box
instead would let a New Hampshire feature through on a Maine-plus-New-York build, and
filtering against the region polygon would drop the offshore ledges and buoys that the
state's own file legitimately carries.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from .common import ExportResult, Region, export_csv

ROW = """
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
FROM read_csv(?, delim = '|', header = true, all_varchar = true, quote = '')
-- Drop features whose primary point lies outside the state (e.g. a river mouth in
-- New Brunswick, the Atlantic Ocean point off North Carolina).
WHERE CAST(prim_lat_dec AS DOUBLE) BETWEEN ? AND ?
  AND CAST(prim_long_dec AS DOUBLE) BETWEEN ? AND ?
  AND (? OR feature_name NOT ILIKE '%(historical)%')
"""


def convert(
    con: duckdb.DuckDBPyConnection,
    sources: list[tuple[str, Path]],
    out: Path,
    reg: Region,
    include_historical: bool = False,
) -> ExportResult:
    """Convert one `(state, file)` per member state into a single CSV."""
    boxes = reg.state_boxes
    con.execute("DROP TABLE IF EXISTS _gnis")
    for i, (state, src) in enumerate(sources):
        lon_min, lat_min, lon_max, lat_max = boxes[state]
        params = [str(src), lat_min, lat_max, lon_min, lon_max, include_historical]
        verb = "CREATE TEMP TABLE _gnis AS" if i == 0 else "INSERT INTO _gnis"
        con.execute(f"{verb} {ROW}", params)  # noqa: S608 - fixed SQL, bound parameters
    # A feature on a state line is published in both states' files, with the same id, name and
    # point: Vermont and New Hampshire share 36 of them, all brooks along the Connecticut River.
    # Keeping both would put duplicate ids in the export, which validate_export refuses.
    return export_csv(
        con, "SELECT DISTINCT ON (id) * FROM _gnis ORDER BY CAST(id AS BIGINT)", [], out, reg
    )
