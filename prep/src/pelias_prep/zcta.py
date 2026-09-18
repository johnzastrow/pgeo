"""Census Gazetteer ZCTA file (national, pipe-delimited since 2025) -> Pelias CSV, Maine only.

Maine ZIP codes use the 039-049 prefixes. Each ZCTA becomes a `postalcode` layer
record at its internal point. The polygons are not used: Pelias reverse
point-in-polygon only uses Who's On First.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from .common import ExportResult, export_csv

SQL = """
WITH src AS (
    SELECT *
    FROM read_csv(?, delim = '|', header = true, all_varchar = true, normalize_names = true)
)
SELECT
    trim(geoid)                                  AS id,
    'zcta'                                       AS source,
    'postalcode'                                 AS layer,
    trim(geoid)                                  AS name,
    CAST(trim(intptlat) AS DOUBLE)               AS lat,
    CAST(trim(intptlong) AS DOUBLE)              AS lon,
    trim(geoid)                                  AS postcode,
    to_json({
        zcta5: trim(geoid),
        aland_sqmi: CAST(trim(aland_sqmi) AS DOUBLE),
        awater_sqmi: CAST(trim(awater_sqmi) AS DOUBLE)
    })                                           AS addendum_json_zcta
FROM src
WHERE substr(trim(geoid), 1, 3) BETWEEN '039' AND '049'
ORDER BY id
"""


def convert(con: duckdb.DuckDBPyConnection, src: Path, out: Path) -> ExportResult:
    # `src` is the extracted .txt; the CLI unpacks it from the Census zip.
    return export_csv(con, SQL, [str(src)], out)
