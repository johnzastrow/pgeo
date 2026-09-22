"""Overture Maps Places (GeoParquet extract) -> Pelias CSV, clipped to the build's states.

Input is a bbox extract of the Overture `places/place` theme (see scripts/fetch_data.sh).
Rows are kept when they are open (or status unknown), meet a minimum confidence, and fall
inside the region polygon (table `region_poly`, slightly buffered to keep piers and shoreline
POIs). Requires `region_poly`, `zctas` and `zcta_prefixes` on the connection (see common.py).

The ZIP test used to be a literal Maine prefix range ('039'-'049'). It is now `zcta_prefixes`,
the prefixes the Census gives this build and no other state (common.py). That reproduces the Maine
range exactly, keeps PO-box ZIPs that have no ZCTA of their own, and does not leak across a
border - New York holds 06390 on Fishers Island, and a plain prefix test there would have admitted
every Connecticut 063xx ZIP.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from .common import ExportResult, Region, export_csv

# ~0.003 degrees is roughly 250-330 m at the latitudes we cover.
DEFAULT_BUFFER_DEG = 0.003
DEFAULT_MIN_CONFIDENCE = 0.5

SQL = """
WITH poly AS (
    SELECT ST_Union_Agg(geom) AS g FROM region_poly
),
buffered AS (
    SELECT ST_Buffer(g, ?) AS g FROM poly
),
p AS (
    SELECT
        *,
        ST_X(geometry::GEOMETRY) AS lon,
        ST_Y(geometry::GEOMETRY) AS lat,
        addresses[1] AS addr
    FROM read_parquet(?)
    WHERE confidence >= ?
      AND coalesce(operating_status, 'open') = 'open'
      AND names."primary" IS NOT NULL
      AND trim(names."primary") <> ''
      AND bbox.xmin BETWEEN ? AND ? AND bbox.ymin BETWEEN ? AND ?
),
-- Inside the exact polygon: keep. Inside only the buffered edge (piers, shoreline, and for a
-- Maine build the New Hampshire shore of the Piscataqua): keep only with in-region evidence.
clipped AS (
    SELECT p.*
    FROM p, poly, buffered
    WHERE ST_Intersects(ST_Point(p.lon, p.lat), poly.g)
       OR (ST_Intersects(ST_Point(p.lon, p.lat), buffered.g)
           AND (list_contains(?::VARCHAR[], upper(p.addr.region))
                OR substr(p.addr.postcode, 1, 3) IN (SELECT p FROM zcta_prefixes)))
)
SELECT
    id,
    'overture'                                               AS source,
    'venue'                                                  AS layer,
    trim(names."primary")                                    AS name,
    lat,
    lon,
    nullif(regexp_extract(addr.freeform,
        '^\\s*(\\d+[A-Za-z]?(?:-\\d+[A-Za-z]?)?)\\s+\\S', 1), '')  AS housenumber,
    CASE WHEN regexp_matches(addr.freeform, '^\\s*\\d+[A-Za-z]?(?:-\\d+[A-Za-z]?)?\\s+\\S')
         THEN trim(regexp_replace(addr.freeform,
                   '^\\s*\\d+[A-Za-z]?(?:-\\d+[A-Za-z]?)?\\s+', ''))
    END                                                      AS street,
    -- Drop (do not guess) postcodes that are not in-region ZIPs; they are source errors.
    CASE WHEN substr(addr.postcode, 1, 3) IN (SELECT p FROM zcta_prefixes)
         THEN nullif(regexp_extract(addr.postcode, '^(\\d{5})', 1), '')
    END                                                      AS postcode,
    basic_category                                           AS category,
    to_json({
        id: id,
        confidence: round(confidence, 3),
        basic_category: basic_category,
        category: categories."primary",
        taxonomy: taxonomy.hierarchy,
        brand: brand.names."primary",
        websites: websites,
        phones: phones,
        datasets: list_distinct([s.dataset FOR s IN sources])
    })                                                       AS addendum_json_overture
FROM clipped
ORDER BY id
"""


def convert(
    con: duckdb.DuckDBPyConnection,
    src: Path,
    out: Path,
    reg: Region,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    buffer_deg: float = DEFAULT_BUFFER_DEG,
) -> ExportResult:
    if not 0.0 <= min_confidence <= 1.0:
        raise ValueError("min_confidence must be between 0 and 1")
    if not 0.0 <= buffer_deg <= 0.05:
        raise ValueError("buffer_deg must be between 0 and 0.05")
    lon_min, lat_min, lon_max, lat_max = reg.bbox
    params = [buffer_deg, str(src), min_confidence, lon_min, lon_max, lat_min, lat_max,
              list(reg.evidence)]
    return export_csv(con, SQL, params, out, reg)
