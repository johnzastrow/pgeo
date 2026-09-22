"""Census Gazetteer ZCTAs -> Pelias CSV, limited to the build's states.

Each ZCTA becomes a `postalcode` record at its internal point. The polygons are not used:
Pelias reverse point-in-polygon only uses Who's On First.

Which ZCTAs belong is decided in common.load_region_zctas, by asking whether the Census
internal point falls inside the region polygon. The earlier Maine-only version matched the
ZIP prefixes 039-049 instead, which is a fact about one state; the point test is the same
question asked directly and needs nothing looked up per state.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from .common import ExportResult, Region, export_csv

SQL = """
SELECT
    geoid                                        AS id,
    'zcta'                                       AS source,
    'postalcode'                                 AS layer,
    geoid                                        AS name,
    lat,
    lon,
    geoid                                        AS postcode,
    to_json({
        zcta5: geoid,
        aland_sqmi: aland_sqmi,
        awater_sqmi: awater_sqmi
    })                                           AS addendum_json_zcta
FROM zctas
ORDER BY id
"""


def convert(con: duckdb.DuckDBPyConnection, out: Path, reg: Region) -> ExportResult:
    """Export the `zctas` table (built by common.load_region_zctas) as a Pelias CSV."""
    return export_csv(con, SQL, [], out, reg)
