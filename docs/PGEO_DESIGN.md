# pgeo: PostgreSQL/PostGIS Geocoder (Phase 10) -- Design

A Pelias-compatible geocoder whose runtime is entirely PostgreSQL 18 + PostGIS 3.6 +
extensions, with a thin FastAPI layer that speaks the Pelias `/v1` API. Pelias is the
reference: same raw inputs, same accuracy harness, same load harness.

Decisions: PLAN.md section 10 and docs/PROJECT_LOG.md (D24, D25).

---

## 1. Components

```
k6 / demo page / harness
        |  /v1/search, /v1/autocomplete, /v1/reverse, /v1/search/structured, /v1/place
+-------v--------+        +---------------------------+
| pgeo-api       |------->| pgeo-libpostal (service   |  parse mode "service"
| FastAPI +      |        |  arm; same image as Pelias)|
| asyncpg pool   |        +---------------------------+
+-------+--------+
        | SQL functions (pgeo.search, pgeo.autocomplete, pgeo.reverse, ...)
+-------v-----------------------------------------------+
| pgeo-db: PostgreSQL 18 + PostGIS 3.6                  |
|  extensions: postgis, pg_trgm, unaccent, fuzzystrmatch |
|  optional: postal (pgsql-postal, parse mode "extension")|
|  optional: pg_search (ParadeDB BM25, A/B arm)          |
+-------------------------------------------------------+
```

All ports bind to 127.0.0.1. The API is stateless; connection pool per worker.

Parse modes (per request via a query flag for experiments, default set by config):
`service` (libpostal HTTP), `extension` (`postal_parse()` in SQL), `none` (rule-based
parser in Python). The engine never trusts parser output: it only shapes candidate
retrieval and scoring.

---

## 2. Data model

### `pgeo.admin` (Who's On First; Overture divisions as A/B)

| Column | Notes |
|--------|-------|
| id, source, source_id | `whosonfirst:<id>` or `overture:<gers>` |
| placetype | country, region, county, localadmin, locality, neighbourhood, postalcode |
| name, abbr, name_norm | `name_norm` = lower(unaccent(name)) |
| parent_* ids and names | precomputed hierarchy |
| geom | MultiPolygon 4326, GiST |
| centroid, bbox | display |
| population / importance | ranking of places |

### `pgeo.feature` (everything searchable)

| Column | Notes |
|--------|-------|
| gid | `source:layer:id`, unique (Pelias format) |
| source, layer, source_id | layers: address, street, venue, locality, localadmin, neighbourhood, county, region, postalcode |
| name, housenumber, street, unit, postcode | as loaded |
| neighbourhood, locality, localadmin, county, region, region_a | from point-in-polygon against `pgeo.admin` at load time (the Pelias `pip` service equivalent) |
| label | Pelias-style label |
| name_norm, street_norm, label_norm | normalized (lowercase, unaccent, suffix/directional abbreviations canonicalized) |
| category text[], addendum jsonb | as in Pelias |
| geom | Point 4326 (streets: line in `geom_line`, point = midpoint) |
| importance real | layer prior + popularity signals |

Indexes (tuned later): GIN `gin_trgm_ops` on `name_norm` and `label_norm`;
btree `text_pattern_ops` on `label_norm` and `name_norm` (prefix autocomplete); GiST on
`geom`; btree on `(street_norm, housenumber)`, `(layer)`, `(postcode)`; tsvector GIN.

### `pgeo.street_number` (interpolation)

Per street segment and town: known house numbers with positions, so a missing number is
interpolated linearly between its nearest known neighbours on the same street (replaces the
Pelias interpolation service without TIGER as a hard dependency; TIGER ranges can be added).

---

## 3. Loaders (same raw inputs as Pelias)

| Source | Input | Method |
|--------|-------|--------|
| WOF | `data/pelias/whosonfirst/sqlite/*.db` | DuckDB sqlite scan, Maine descendants, polygons -> `pgeo.admin`; localities/postal codes also -> `pgeo.feature` |
| OpenAddresses | `data/pelias/interpolation_oa/us/me/*.csv` | COPY -> address features |
| OSM | `data/pelias/openstreetmap/maine-latest.osm.pbf` | `ogr2ogr` (GDAL OSM driver): named POIs -> venues, `addr:*` -> addresses, named highways -> streets (lines) |
| GNIS, ZCTA, Overture | `data/processed/<build>/csv/*.csv` | COPY -> venue / postalcode features |

Load is into staging tables, then PIP enrichment, normalization, dedupe, index build, and an
atomic schema swap (`pgeo_next` -> `pgeo`). Loaders are Python (uv project `pgeo/`), using
DuckDB for file wrangling and `COPY` into Postgres.

---

## 4. Query strategies

- **search** (unstructured): parse -> candidate sets in parallel CTEs:
  address (housenumber + street trigram, town/ZIP as boost), name (trigram + FTS on
  `name_norm`), admin (localities/counties). Score = weighted text similarity + component
  agreement (town, ZIP, state) + layer prior + importance + focus distance decay.
  Fallbacks: exact address -> interpolated address -> street -> town, each with lower
  confidence (Pelias `match_type` exact/interpolated/fallback).
- **autocomplete**: prefix match on `label_norm`/`name_norm` (btree pattern ops), then
  trigram fuzzy fill if too few; ranked by importance and focus distance. Typo tolerance is a
  goal Pelias lacks (F18).
- **reverse**: KNN (`ORDER BY geom <-> point`) with `ST_DWithin` radius and layer filters;
  admin layers answered by `ST_Contains` on `pgeo.admin`.
- **structured**: fields map directly to components; same scoring.
- **place**: by gid.
- **confidence**: 0..1 from component agreement (housenumber exact, street similarity, town
  and ZIP agreement, distance), explicitly penalizing a town mismatch (F16) and low name
  similarity (F20).

---

## 5. Tuning and evaluation

- Accuracy harness (`tests/accuracy/`): the load corpus's exact queries have ground truth
  (source records); typos/variants inherit the truth of their source query; misses must
  return nothing or low confidence. Metrics: top-1 / top-5 hit, distance error, confidence
  calibration, per query type. Run against both engines.
- Load harness: `tests/load/run_matrix.py` with a pgeo target (same corpus, SLOs, matrix).
- Tuning log: `docs/PGEO_TUNING.md` (setting, rationale, before/after per endpoint and type).

---

## 6. Security

Same baseline as the Pelias stack: 127.0.0.1-only ports, non-root containers where the
image allows, parameterized SQL only (asyncpg bind parameters; SQL functions take typed
arguments), input length limits, no dynamic SQL built from user input, generic error
messages, secrets (DB password) in a gitignored mode-600 env file.
