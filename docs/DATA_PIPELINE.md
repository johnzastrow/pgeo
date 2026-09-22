# Data Pipeline Runbook: Extraction, Preparation, and Loading

How to reproduce the Maine Pelias build by hand, from empty directories to a running,
tested geocoder and a deployable Elasticsearch snapshot. Every step has the scripted
command and the equivalent manual commands, plus checks with the values observed on the
reference build (2026-09-18).

- Scripts referenced here live in `scripts/` and `prep/`. They are thin wrappers around the
  manual commands shown, so you can run either.
- Paths are relative to the repo root unless stated otherwise.
- The whole pipeline runs on the workstation. Deploy hosts (Proxmox VM, VPS) only receive
  the finished snapshot and a few service data directories (see step 9).

---

## Contents

0. [Requirements](#0-requirements)
1. [Directory layout](#1-directory-layout)
2. [Pinned versions](#2-pinned-versions)
3. [Extract raw inputs (custom sources + basemap)](#3-extract-raw-inputs)
4. [Prepare custom sources as Pelias CSV](#4-prepare-custom-sources-as-pelias-csv)
5. [Configure the Pelias project](#5-configure-the-pelias-project)
6. [Build Pelias: download, prepare, import](#6-build-pelias)
7. [Start and verify](#7-start-and-verify)
8. [Snapshot the index](#8-snapshot-the-index)
9. [What a deploy host needs](#9-what-a-deploy-host-needs)
10. [Overture Maps: all themes](#10-overture-maps-all-themes)
11. [Refreshing data](#11-refreshing-data)
12. [Troubleshooting and known issues](#12-troubleshooting-and-known-issues)
13. [Sources and licenses](#13-sources-and-licenses)

---

## 0. Requirements

### Hardware (workstation doing the build)

| Resource | Minimum | Reference build | Notes |
|----------|---------|-----------------|-------|
| RAM | 16 GB | 31 GB | ES heap 4 GB during build, plus libpostal (~2-3 GB), importers, Valhalla |
| CPU | 4 cores | 12 threads | Imports and interpolation parallelize |
| Free disk | 20 GB | 375 GB free | Build uses ~7 GB (`data/pelias` 6.4 GB, of which WOF is 5.2 GB) |
| Network | Broadband | -- | ~6.5 GB downloaded in total, mostly WOF |

### Software

| Tool | Version used | Purpose | Install hint (Debian/Ubuntu) |
|------|--------------|---------|-------------------------------|
| Linux | Ubuntu 26.04.1 LTS | Host OS | Any modern Linux with Docker |
| Docker Engine | 29.1.3 | Runs all Pelias services and importers | `apt install docker.io` or docker.com repo |
| Docker Compose plugin | v5.1.4 | Used by the pelias CLI | `apt install docker-compose-plugin` |
| git | 2.53.0 | Vendor the pelias CLI | `apt install git` |
| curl | 8.18.0 | Downloads | `apt install curl` |
| jq | 1.8.1 | JSON handling in scripts | `apt install jq` |
| DuckDB CLI | 1.5.5 | Overture extraction, ad-hoc checks | https://duckdb.org/docs/installation |
| uv | 0.9.17 | Python env for `prep/` (pulls Python >= 3.12 and duckdb 1.5.5) | https://docs.astral.sh/uv/ |
| pmtiles CLI | 1.30.1 | Basemap extract | https://github.com/protomaps/go-pmtiles/releases |
| shellcheck | 0.11.0 | Optional, lints scripts | `apt install shellcheck` |

### Host settings

```bash
# Elasticsearch needs a high mmap count (reference host already had 1048576)
sysctl vm.max_map_count                      # must be >= 262144
sudo sysctl -w vm.max_map_count=262144       # if lower; persist in /etc/sysctl.d/

# Your user must be able to run docker without sudo
docker run --rm hello-world

# These local ports must be free (all services bind to 127.0.0.1 only)
ss -ltn | grep -E ':(4000|4100|4200|4300|4400|9200|9300)\b' || echo "ports free"
```

### Accounts and secrets

| Item | Required? | Where it goes |
|------|-----------|---------------|
| OpenAddresses token (batch.openaddresses.io) | No. Without one, the importer uses a shared default token, which can be throttled. | `projects/pelias_maine/secrets.env` as `OA_TOKEN=...` (mode 600, gitignored) |
| AWS / Azure credentials for Overture | No, anonymous access | -- |

---

## 1. Directory layout

```
data/                              (gitignored; keep it: it is the source archive)
  raw/                             step 3: untouched downloads
    osm/          maine-latest.osm.pbf (+ .md5)
    gnis/         DomesticNames_ME_Text.zip
    zcta/         2025_Gaz_zcta_national.zip
    boundary/     cb_2024_us_state_500k.zip
    overture/     places_<R>_me_bbox.parquet and other theme extracts (section 10)
    basemap/      maine.pmtiles
  work/                            step 4: files unzipped from raw/
  processed/csv/                   step 4: gnis.csv, zcta.csv, overture.csv, manifest.json
  pelias/                          Pelias DATA_DIR (steps 6-8)
    openstreetmap/ openaddresses/ whosonfirst/ tiger/   (pelias download)
    polylines/ placeholder/ interpolation/              (pelias prepare)
    interpolation_oa/                                   (pelias-prep oa-interp)
    csv/                                                (copied from processed/csv)
    elasticsearch/ es_snapshots/                        (index and snapshots)
  logs/                            build logs
projects/pelias_maine/             compose file, pelias.template.json, synonyms, tests
vendor/pelias-docker/              pelias CLI at a pinned commit (gitignored)
```

---

## 2. Pinned versions

Changing a pin changes the output. Bump pins deliberately, rebuild, and re-run the tests.

| What | Pin | Where it is set |
|------|-----|-----------------|
| pelias/docker CLI | commit `3dfa07d` (2026-03-25) | `scripts/bootstrap.sh` (`PELIAS_DOCKER_REF`) |
| Pelias images | dated `master-YYYY-MM-DD-<sha>` tags; ES `7.17.27-2025-01-22-...` | `projects/pelias_maine/docker-compose.yml` |
| Overture release | `2026-08-19.0` | `scripts/fetch_data.sh` (`OVERTURE_RELEASE`) |
| Protomaps build | `20260918` | `scripts/fetch_data.sh` (`PROTOMAPS_BUILD`) |
| Census ZCTA Gazetteer | 2025 | `scripts/fetch_data.sh` (`ZCTA_YEAR`) |
| Census state boundaries | `cb_2024_us_state_500k` | `scripts/fetch_data.sh`, `prep/src/pelias_prep/cli.py` |
| Python deps | duckdb 1.5.5, pytest 9.1.1, ruff 0.16.8 | `prep/pyproject.toml`, `prep/uv.lock` |
| Who's On First place | Maine region `85688769` | `regions/regions.json`; `projects/pelias_maine/pelias.template.json` for the Pelias stack |
| TIGER state | FIPS `23` (Maine) | `projects/pelias_maine/pelias.template.json` |
| OpenAddresses sources | `us/me/statewide`, `us/me/city_of_biddeford` | `regions/regions.json` (pinned there as built); the Pelias template for the Pelias stack |
| Census ZCTA-to-county | `tab20_zcta520_county20_natl` (2020) | `scripts/fetch_data.sh` (`zcta` target) |

"Latest" inputs that are **not** pinned, because upstream keeps no dated URL: the Geofabrik
Maine PBF, GNIS Domestic Names, OpenAddresses, WOF, TIGER. Their exact bytes are archived in
`data/` and recorded by hash in `data/processed/<build>/csv/manifest.json` (custom sources).

Find the current Overture release and Protomaps build before bumping pins:

```bash
curl -s https://stac.overturemaps.org/catalog.json | jq -r .latest          # e.g. 2026-08-19.0
curl -s https://build-metadata.protomaps.dev/builds.json | jq -r '.[-1].key' # e.g. 20260918.pmtiles
```

---

## 3. Extract raw inputs

> **Builds and paths (2026-09-22).** The pipeline is parameterised by *build* - one or more US
> states named in `regions/regions.json`. Per-build inputs live in `data/raw/<build>/` and
> outputs in `data/processed/<build>/csv/`; national files shared by every build are in
> `data/raw/shared/`. Every command below takes `--build NAME` and defaults to `me`, so the
> Maine examples are still literally correct. `GETTING_STARTED.md` walks the same ground for a
> new region, and section 3 of `TODO.md` records what the change involved.
>
> A pgeo build no longer needs the Pelias CLI at all: OpenAddresses comes from
> `results.openaddresses.io` and Who's on First from `data.geocode.earth`, both as targets of
> `scripts/fetch_data.sh`. Steps 6-8 below remain the route for the *Pelias* stack, which is
> still built the Pelias way so the comparison in the report stays like-for-like.

Everything in this step lands in `data/raw/<build>/` and `data/raw/shared/`.

Scripted, all at once (about 5 minutes on broadband for one state):

```bash
scripts/fetch_data.sh --build me all
# or one target: osm | gnis | oa | wof | zcta | boundary | overture | overture_themes | basemap
```

Maine bbox used everywhere: `-71.2,42.9,-66.8,47.5` (lon_min, lat_min, lon_max, lat_max),
generous on purpose. Exact clipping to the state polygon happens in step 4.

### 3.1 OpenStreetMap (Geofabrik)

```bash
mkdir -p data/raw/osm && cd data/raw/osm
curl -fSL -O https://download.geofabrik.de/north-america/us/maine-latest.osm.pbf
curl -fSL -O https://download.geofabrik.de/north-america/us/maine-latest.osm.pbf.md5
md5sum -c maine-latest.osm.pbf.md5          # expect: maine-latest.osm.pbf: OK
cd -
```

Reference: 90,862,431 bytes, Last-Modified 2026-09-18. Geofabrik updates daily.

### 3.2 USGS GNIS Domestic Names (Maine)

```bash
mkdir -p data/raw/gnis
curl -fSL -o data/raw/gnis/DomesticNames_ME_Text.zip \
  https://prd-tnm.s3.amazonaws.com/StagedProducts/GeographicNames/DomesticNames/DomesticNames_ME_Text.zip
unzip -l data/raw/gnis/DomesticNames_ME_Text.zip    # contains Text/DomesticNames_ME.txt
```

Pipe-delimited, 21 columns, UTF-8 with a BOM. Reference: 761,968 bytes (2026-08-28), 20,188
rows across 36 feature classes (Stream 4,113; Lake 2,809; Summit 2,447; Populated Place
2,140; Island 1,902; Civil 982; ...). "Civil" includes the unorganized townships and
plantations (e.g. "T4 R9 WELS", "Township of Parmachenee").

### 3.3 Census ZCTA Gazetteer (national)

```bash
mkdir -p data/raw/zcta
curl -fSL -o data/raw/zcta/2025_Gaz_zcta_national.zip \
  https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/2025_Gaz_zcta_national.zip
```

Note: from 2025 this file is **pipe-delimited** (it was tab-delimited through 2024). Header:
`GEOID|GEOIDFQ|ALAND|AWATER|ALAND_SQMI|AWATER_SQMI|INTPTLAT|INTPTLONG`.

### 3.4 Census state boundaries (for clipping Overture)

```bash
mkdir -p data/raw/boundary
curl -fSL -o data/raw/boundary/cb_2024_us_state_500k.zip \
  https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_state_500k.zip
```

### 3.5 Overture Places (GeoParquet, anonymous S3)

Only the bbox filter is applied here, so the raw extract stays reusable.

```bash
mkdir -p data/raw/overture
R=2026-08-19.0
duckdb -c "
INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';
COPY (
  SELECT * FROM read_parquet('s3://overturemaps-us-west-2/release/${R}/theme=places/type=place/*',
                             hive_partitioning=1)
  WHERE bbox.xmin BETWEEN -71.2 AND -66.8 AND bbox.ymin BETWEEN 42.9 AND 47.5
) TO 'data/raw/overture/places_${R}_me_bbox.parquet' (FORMAT parquet, COMPRESSION zstd);
SELECT count(*) FROM 'data/raw/overture/places_${R}_me_bbox.parquet';"
```

Reference: 127,185 rows in the bbox (includes parts of NH, QC, NB), 16 MB, about 1 minute.
DuckDB reads only the row groups whose bbox statistics overlap, so it does not download the
whole global dataset. Other Overture themes: section 10.

### 3.6 Protomaps basemap (for the demo page)

```bash
mkdir -p data/raw/basemap
pmtiles extract https://build.protomaps.com/20260918.pmtiles data/raw/basemap/maine.pmtiles \
  --bbox=-71.2,42.9,-66.8,47.5
pmtiles show data/raw/basemap/maine.pmtiles      # sanity check: bounds, zooms, layers
```

Reference: 292,581,559 bytes (280 MB), max zoom 15. HTTP range requests pull only the
needed tiles from the ~138 GB planet build.

---

## 4. Prepare custom sources as Pelias CSV

The `prep/` package converts GNIS, ZCTA, and Overture Places into files for the Pelias CSV
importer. Every output is validated before it is kept: required fields present, every
row inside the Maine bbox, unique ids, at least one row. Any failure stops the run
(fail closed).

```bash
cd prep
uv sync                      # creates .venv with Python >= 3.12 and duckdb 1.5.5 from uv.lock
uv run python -m pytest -q   # tests on synthetic inputs; expect "10 passed"
uv run pelias-prep all       # or: gnis | zcta | overture
cd ..
```

Expected output (reference build, about 13 s):

```
gnis        20,104 rows -> data/processed/csv/gnis.csv
zcta           426 rows -> data/processed/csv/zcta.csv
overture    76,582 rows -> data/processed/csv/overture.csv
```

`data/processed/csv/manifest.json` records, for each output, the row count, the input file,
and SHA-256 hashes of both input and output. Runs are deterministic: the same inputs give
byte-identical CSVs.

Common CSV columns (Pelias CSV importer schema): `id, source, layer, name, lat, lon`, plus
optional `housenumber, street, postcode, category` and a namespaced
`addendum_json_<source>` column (returned by the API under `addendum.<source>`).

### 4.1 GNIS -> `gnis.csv`

Rules (`prep/src/pelias_prep/gnis.py`):

| Rule | Reason |
|------|--------|
| `source=gnis`, `layer=venue`, `category` = feature class in lower snake case (`lake`, `summit`, `civil`, `populated_place`, ...) | Stay out of the admin layers that Who's On First owns |
| Drop rows whose primary point is outside the Maine bbox | GNIS lists e.g. the Saint John River (mouth in New Brunswick) and the "Atlantic Ocean" point off North Carolina under Maine |
| Drop names containing `(historical)` unless `--include-historical` | Historical features confuse geocoding |
| `addendum_json_gnis` = feature_id, class, county, map name, date edited | Keeps provenance |

Manual equivalent (DuckDB CLI):

```bash
mkdir -p data/work/gnis
unzip -o -j data/raw/gnis/DomesticNames_ME_Text.zip 'Text/DomesticNames_ME.txt' -d data/work/gnis
duckdb -c "
COPY (
  SELECT feature_id AS id, 'gnis' AS source, 'venue' AS layer, trim(feature_name) AS name,
         CAST(prim_lat_dec AS DOUBLE) AS lat, CAST(prim_long_dec AS DOUBLE) AS lon,
         lower(replace(feature_class,' ','_')) AS category,
         to_json({feature_id: feature_id, feature_class: feature_class, county: county_name,
                  map_name: map_name, date_edited: nullif(date_edited,'')}) AS addendum_json_gnis
  FROM read_csv('data/work/gnis/DomesticNames_ME.txt', delim='|', header=true, all_varchar=true, quote='')
  WHERE CAST(prim_lat_dec AS DOUBLE) BETWEEN 42.9 AND 47.5
    AND CAST(prim_long_dec AS DOUBLE) BETWEEN -71.2 AND -66.8
    AND feature_name NOT ILIKE '%(historical)%'
  ORDER BY CAST(feature_id AS BIGINT)
) TO 'data/processed/csv/gnis.csv' (FORMAT csv, HEADER true);"
```

### 4.2 ZCTA -> `zcta.csv`

Keeps Maine ZIP prefixes `039`-`049`. Each ZCTA becomes a `postalcode` record at the Census
internal point (`source=zcta`). Polygons are not used, because Pelias reverse
point-in-polygon only uses Who's On First.

```bash
mkdir -p data/work/zcta
unzip -o -j data/raw/zcta/2025_Gaz_zcta_national.zip -d data/work/zcta
duckdb -c "
COPY (
  SELECT trim(geoid) AS id, 'zcta' AS source, 'postalcode' AS layer, trim(geoid) AS name,
         CAST(trim(intptlat) AS DOUBLE) AS lat, CAST(trim(intptlong) AS DOUBLE) AS lon,
         trim(geoid) AS postcode,
         to_json({zcta5: trim(geoid), aland_sqmi: CAST(trim(aland_sqmi) AS DOUBLE),
                  awater_sqmi: CAST(trim(awater_sqmi) AS DOUBLE)}) AS addendum_json_zcta
  FROM read_csv('data/work/zcta/2025_Gaz_zcta_national.txt', delim='|', header=true,
                all_varchar=true, normalize_names=true)
  WHERE substr(trim(geoid),1,3) BETWEEN '039' AND '049'
  ORDER BY id
) TO 'data/processed/csv/zcta.csv' (FORMAT csv, HEADER true);"
```

### 4.3 Overture Places -> `overture.csv`

Rules (`prep/src/pelias_prep/overture.py`):

| Rule | Value / reason |
|------|----------------|
| Confidence | `confidence >= 0.5` (`--min-confidence` to change) |
| Status | `operating_status` is `open` or null |
| Name | `names.primary` not empty |
| Clip | Inside the Census Maine polygon: keep. Inside only a 0.003 degree (about 300 m) buffer: keep only if `addresses[1].region` is ME/Maine or the ZIP starts 039-049. This keeps shoreline and pier POIs (e.g. Badgers Island, Kittery) and rejects Portsmouth, NH across the Piscataqua. |
| Address | From `addresses[1].freeform`: a leading house number (`123`, `12A`, `10-12`) becomes `housenumber` and the rest becomes `street`. Freeform without a leading number (PO boxes etc.) gives no address fields. |
| Postcode | First 5 digits, only if a Maine ZIP. Non-Maine ZIPs on Maine points are dropped as source errors, not guessed. |
| Category | `basic_category` |
| Addendum | GERS id, confidence, basic_category, legacy category, taxonomy hierarchy, brand, websites, phones, source datasets |

Manual equivalent:

```bash
duckdb -c "
INSTALL spatial; LOAD spatial;
CREATE TABLE maine AS
  SELECT geom FROM ST_Read('/vsizip/data/raw/boundary/cb_2024_us_state_500k.zip/cb_2024_us_state_500k.shp')
  WHERE STUSPS = 'ME';
COPY (
  WITH region AS (SELECT ST_Buffer(geom, 0.003) AS g FROM maine),
  p AS (
    SELECT *, ST_X(geometry::GEOMETRY) AS lon, ST_Y(geometry::GEOMETRY) AS lat, addresses[1] AS addr
    FROM 'data/raw/overture/places_2026-08-19.0_me_bbox.parquet'
    WHERE confidence >= 0.5 AND coalesce(operating_status,'open') = 'open'
      AND names.\"primary\" IS NOT NULL AND trim(names.\"primary\") <> ''
  ),
  clipped AS (
    SELECT p.* FROM p, maine, region
    WHERE ST_Intersects(ST_Point(p.lon,p.lat), maine.geom)
       OR (ST_Intersects(ST_Point(p.lon,p.lat), region.g)
           AND (upper(p.addr.region) IN ('ME','MAINE') OR substr(p.addr.postcode,1,3) BETWEEN '039' AND '049'))
  )
  SELECT id, 'overture' AS source, 'venue' AS layer, trim(names.\"primary\") AS name, lat, lon,
    nullif(regexp_extract(addr.freeform, '^\s*(\d+[A-Za-z]?(?:-\d+[A-Za-z]?)?)\s+\S', 1), '') AS housenumber,
    CASE WHEN regexp_matches(addr.freeform, '^\s*\d+[A-Za-z]?(?:-\d+[A-Za-z]?)?\s+\S')
         THEN trim(regexp_replace(addr.freeform, '^\s*\d+[A-Za-z]?(?:-\d+[A-Za-z]?)?\s+', '')) END AS street,
    CASE WHEN substr(addr.postcode,1,3) BETWEEN '039' AND '049'
         THEN nullif(regexp_extract(addr.postcode, '^(\d{5})', 1), '') END AS postcode,
    basic_category AS category,
    to_json({id: id, confidence: round(confidence,3), basic_category: basic_category,
             category: categories.\"primary\", taxonomy: taxonomy.hierarchy,
             brand: brand.names.\"primary\", websites: websites, phones: phones,
             datasets: list_distinct([s.dataset FOR s IN sources])}) AS addendum_json_overture
  FROM clipped ORDER BY id
) TO 'data/processed/csv/overture.csv' (FORMAT csv, HEADER true);"
```

Reference profile of `overture.csv`: 76,582 rows; 70,543 with house number and street;
73,686 with a Maine ZIP; 75,509 with a category (top: restaurant 3,657, home_service 3,543,
automotive_service 2,482).

### 4.4 Checks on the prepared CSVs

```bash
duckdb -c "
SELECT 'gnis' f, count(*) n, count(DISTINCT id) ids, min(lat), max(lat), min(lon), max(lon) FROM 'data/processed/csv/gnis.csv'
UNION ALL SELECT 'zcta', count(*), count(DISTINCT id), min(lat), max(lat), min(lon), max(lon) FROM 'data/processed/csv/zcta.csv'
UNION ALL SELECT 'overture', count(*), count(DISTINCT id), min(lat), max(lat), min(lon), max(lon) FROM 'data/processed/csv/overture.csv';"
# n must equal ids; lat within 42.9..47.5; lon within -71.2..-66.8
```

---

## 5. Configure the Pelias project

```bash
scripts/bootstrap.sh
# equivalent:
#   git clone https://github.com/pelias/docker.git vendor/pelias-docker
#   git -C vendor/pelias-docker checkout 3dfa07d

cd projects/pelias_maine
cp .env.example .env && chmod 600 .env
#   DATA_DIR=<absolute path to repo>/data/pelias   (must be absolute)
#   ES_HEAP=4g                                     (2g on the 10 GB VM)
cp secrets.env.example secrets.env && chmod 600 secrets.env   # optional: OA_TOKEN=...
cd ../..

scripts/render_config.sh      # writes projects/pelias_maine/pelias.json (mode 600, gitignored)
```

Why `secrets.env` is separate: the pelias CLI and docker compose read `.env` and reject
empty variables, and they should never see the token. `render_config.sh` reads only
`OA_TOKEN` from `secrets.env` (it never sources the file) and injects it with `jq` into
`imports.openaddresses.token`.

What `pelias.template.json` configures (the diff from upstream `projects/texas`):

| Section | Setting |
|---------|---------|
| `imports.openstreetmap` | Geofabrik `maine-latest.osm.pbf` |
| `imports.openaddresses.files` | `us/me/statewide`, `us/me/city_of_biddeford` |
| `imports.whosonfirst` | `countryCode: US`, `importPlace: ["85688769"]` (Maine), `importPostalcodes: true` |
| `imports.interpolation.download.tiger` | `state_code: 23` |
| `imports.csv` | `gnis.csv`, `zcta.csv`, `overture.csv` from `/data/csv` |
| `imports.transit` | no feeds yet |
| `api.targets.auto_discover` | `true`, so the custom sources `gnis`, `zcta`, `overture` work with `sources=` filters |
| GeoNames | not configured (would import all of the US; redundant) |

---

## 6. Build Pelias

Scripted (about 15 minutes end to end on the reference workstation):

```bash
mkdir -p data/logs
scripts/build_local.sh 2>&1 | tee data/logs/build_$(date +%Y%m%d_%H%M).log
# or selected steps, in order: setup download prepare import up test
```

Do not edit `scripts/build_local.sh` while it is running: bash reads scripts incrementally,
and an edit mid-run breaks the remaining steps.

Manual equivalent. The pelias CLI must run from the project directory (it reads `.env` and
`docker-compose.yml` from the current directory):

```bash
cd projects/pelias_maine
PELIAS=../../vendor/pelias-docker/pelias

# 6.1 setup (about 1 minute plus image pulls)
mkdir -p ../../data/pelias/{csv,elasticsearch,es_snapshots}
install -m 0644 ../../data/processed/csv/{gnis,zcta,overture}.csv ../../data/pelias/csv/
$PELIAS compose pull
$PELIAS elastic start
$PELIAS elastic wait
$PELIAS elastic create          # fails if index 'pelias' exists; `$PELIAS elastic drop` to rebuild

# 6.2 download (under 1 minute on broadband, except the WOF US database)
$PELIAS download wof            # US Who's On First SQLite, ~5.2 GB even with importPlace
$PELIAS download osm            # maine-latest.osm.pbf
$PELIAS download oa             # us/me/*.geojson (shared default token unless OA_TOKEN set)
$PELIAS download tiger          # TIGER edges for FIPS 23

# 6.2b convert OA GeoJSON to the CSV layout the interpolation builder needs (see section 12)
(cd ../../prep && uv run pelias-prep oa-interp)
#   oa-interp    7,640 rows -> data/pelias/interpolation_oa/us/me/city_of_biddeford.csv
#   oa-interp  774,216 rows -> data/pelias/interpolation_oa/us/me/statewide.csv

# 6.3 prepare (about 3-4 minutes)
$PELIAS prepare all             # polylines (Valhalla), placeholder, then interpolation

# 6.4 import (about 5 minutes)
$PELIAS import all              # wof, oa, osm, polylines, transit, csv

# 6.5 start the query services
$PELIAS compose up
cd ../..
```

Manual equivalent of 6.2b, if you do not want to use the Python package:

```bash
for f in data/pelias/openaddresses/us/me/*.geojson; do
  out="data/pelias/interpolation_oa/us/me/$(basename "${f%.geojson}").csv"
  mkdir -p "$(dirname "$out")"
  duckdb -c "
  COPY (
    SELECT geometry.coordinates[1] AS LON, geometry.coordinates[2] AS LAT,
           properties.number AS NUMBER, properties.street AS STREET, properties.unit AS UNIT,
           properties.city AS CITY, properties.district AS DISTRICT, properties.region AS REGION,
           properties.postcode AS POSTCODE, properties.id AS ID, properties.hash AS HASH
    FROM read_json('$f', format='newline_delimited', columns={
      geometry: 'STRUCT(type VARCHAR, coordinates DOUBLE[])',
      properties: 'STRUCT(\"number\" VARCHAR, street VARCHAR, unit VARCHAR, city VARCHAR,
                          district VARCHAR, region VARCHAR, postcode VARCHAR, id VARCHAR, hash VARCHAR)'})
    WHERE geometry.coordinates[1] IS NOT NULL
      AND nullif(trim(properties.number),'') IS NOT NULL
      AND nullif(trim(properties.street),'') IS NOT NULL
  ) TO '$out' (FORMAT csv, HEADER true);"
done
```

The compose file sets `OAPATH=/data/interpolation_oa` on the interpolation service so the
builder reads these CSVs.

### Checks after the build

```bash
# Interpolation actually conflated OA (last line = rows processed; must not be 0)
tail -n 1 data/pelias/interpolation/conflate_oa.out      # reference: 721600

# Import log: CSV importer must report badRecordCount=0 for every file
grep -E 'Finished import|badRecordCount' data/logs/build_*.log | tail -3

# Document counts per source and layer
curl -s -XPOST '127.0.0.1:9200/pelias/_search?size=0' -H 'Content-Type: application/json' \
  -d '{"aggs":{"s":{"terms":{"field":"source","size":20},"aggs":{"l":{"terms":{"field":"layer","size":20}}}}}}' \
  | jq -c '.aggregations.s.buckets[] | {source: .key, n: .doc_count, layers: ([.l.buckets[] | {(.key): .doc_count}] | add)}'
```

Reference counts (2026-09-18), 1,649,644 documents:

| source | documents | layers |
|--------|-----------|--------|
| openaddresses | 780,260 | address |
| openstreetmap | 768,992 | address 641,485; street 65,323 (polylines); venue 62,184 |
| overture | 76,582 | venue |
| gnis | 20,104 | venue |
| whosonfirst | 3,280 | locality 1,591; neighbourhood 706; localadmin 532; postalcode 433; county 16; region 1; country 1 |
| zcta | 426 | postalcode |

A rebuild from newer inputs will differ somewhat; differences of more than a few percent
per source deserve a look.

---

## 7. Start and verify

```bash
docker ps --filter name=pelias_maine --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'
# api, libpostal, placeholder, pip, interpolation, elasticsearch: Up; every port 127.0.0.1:...

until curl -sf '127.0.0.1:4000/v1/search?text=portland' >/dev/null; do sleep 2; done

q(){ curl -s "127.0.0.1:4000/v1/$1" | jq -r '.features[:3][] |
  "\(.properties.label) | \(.properties.layer)/\(.properties.source) conf=\(.properties.confidence // "-")"'; }
q 'search?text=389%20Congress%20St,%20Portland,%20ME'     # 389 Congress Street, Portland | address/openaddresses conf=1
q 'search/structured?address=73%20Harlow%20St&locality=Bangor&region=ME'
q 'autocomplete?text=moosehe'
q 'reverse?point.lat=43.6591&point.lon=-70.2568&size=3'
q 'search?text=04101'                                      # 04101, Portland | postalcode/zcta
q 'search?text=T4%20R9%20WELS'                             # T4 R9 WELS | venue/gnis

# Test suite (projects/pelias_maine/test_cases)
(cd projects/pelias_maine && ../../vendor/pelias-docker/pelias test run)
# Reference: Pass 15, Expected Failures 4, Regressions 0 (known ranking/confidence issues)
```

---

## 8. Snapshot the index

```bash
scripts/snapshot_local.sh                 # name defaults to pelias-YYYYMMDD-HHMM
# -> snapshot pelias-20260918-1715: SUCCESS; deploy with -e pelias_snapshot_name=pelias-20260918-1715
```

Manual equivalent (the snapshot repository path is set by `path.repo` in the compose file):

```bash
ES=http://127.0.0.1:9200; NAME=pelias-$(date +%Y%m%d-%H%M)
curl -fsS -X PUT "$ES/_snapshot/pelias_repo" -H 'Content-Type: application/json' \
  -d '{"type":"fs","settings":{"location":"/usr/share/elasticsearch/snapshots","compress":true}}'
curl -fsS -X POST "$ES/pelias/_refresh"
curl -fsS -X POST "$ES/pelias/_forcemerge?max_num_segments=1"
curl -fsS -X PUT "$ES/_snapshot/pelias_repo/$NAME?wait_for_completion=true" \
  -H 'Content-Type: application/json' -d '{"indices":"pelias","include_global_state":false}' | jq .snapshot.state
curl -s "$ES/_snapshot/pelias_repo/_all" | jq -r '.snapshots[] | "\(.snapshot) \(.state)"'
```

Reference size: 435 MB in `data/pelias/es_snapshots/`.

---

## 9. What a deploy host needs

A query-only host (Proxmox VM or VPS) runs `elasticsearch, libpostal, placeholder, pip,
interpolation, api` and needs only:

| From `data/pelias/` | Why |
|---------------------|-----|
| `es_snapshots/` | Restored into the empty ES index |
| `whosonfirst/` | pip service (reverse admin lookup) reads the WOF SQLite |
| `placeholder/` | Admin-name search database |
| `interpolation/` | `street.db` + `address.db` |

Ansible (`infra/ansible`, role `pelias_runtime`) ships exactly these with rsync and restores
the snapshot: `ansible-playbook site.yml -e pelias_snapshot_name=<name>`. Nothing else is
pruned; the workstation keeps the full `data/` tree as the archive.

---

## 10. Overture Maps: all themes

### Access

| Item | Value |
|------|-------|
| AWS S3 (anonymous) | `s3://overturemaps-us-west-2/release/<RELEASE>/theme=<theme>/type=<type>/*` |
| Azure Blob (anonymous) | `https://overturemapswestus2.blob.core.windows.net/release/<RELEASE>/...` |
| Release list / latest | STAC: `https://stac.overturemaps.org/catalog.json` (`.latest`) |
| Cadence | Monthly, `YYYY-MM-DD.N`; GERS ids are stable across releases |
| Format | GeoParquet; filter on the `bbox` struct column so DuckDB can skip row groups |
| Esri ArcGIS Online | Same data exposed as Parquet feature layers (beta, June 2026, early-access Overture layers). Requires an ArcGIS org account; useful for viewing and QA in ArcGIS, not needed for this pipeline. |

Themes and types: addresses/address; base/{bathymetry, infrastructure, land, land_cover,
land_use, water}; buildings/{building, building_part}; divisions/{division, division_area,
division_boundary}; places/place; transportation/{segment, connector}.

### Extract the evaluated themes

```bash
scripts/fetch_data.sh overture_themes        # about 5-10 minutes
```

Manual equivalent (writes six files into `data/raw/overture/`):

```bash
R=2026-08-19.0; B=s3://overturemaps-us-west-2/release/$R; D=data/raw/overture
duckdb -c "
INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';
CREATE MACRO inbox(b) AS b.xmin BETWEEN -71.2 AND -66.8 AND b.ymin BETWEEN 42.9 AND 47.5;
CREATE MACRO overlaps(b) AS b.xmax >= -71.2 AND b.xmin <= -66.8 AND b.ymax >= 42.9 AND b.ymin <= 47.5;
COPY (SELECT * FROM read_parquet('$B/theme=addresses/type=address/*', hive_partitioning=1)
      WHERE inbox(bbox) AND country='US') TO '$D/addresses_${R}_me_bbox.parquet' (FORMAT parquet, COMPRESSION zstd);
COPY (SELECT * FROM read_parquet('$B/theme=divisions/type=division/*', hive_partitioning=1)
      WHERE inbox(bbox)) TO '$D/division_${R}_me_bbox.parquet' (FORMAT parquet, COMPRESSION zstd);
COPY (SELECT * FROM read_parquet('$B/theme=divisions/type=division_area/*', hive_partitioning=1)
      WHERE overlaps(bbox) AND country='US') TO '$D/division_area_${R}_me_bbox.parquet' (FORMAT parquet, COMPRESSION zstd);
COPY (SELECT * FROM read_parquet('$B/theme=base/type=water/*', hive_partitioning=1)
      WHERE inbox(bbox) AND names.primary IS NOT NULL) TO '$D/water_named_${R}_me_bbox.parquet' (FORMAT parquet, COMPRESSION zstd);
COPY (SELECT * FROM read_parquet('$B/theme=base/type=land/*', hive_partitioning=1)
      WHERE inbox(bbox) AND names.primary IS NOT NULL) TO '$D/land_named_${R}_me_bbox.parquet' (FORMAT parquet, COMPRESSION zstd);
COPY (SELECT * FROM read_parquet('$B/theme=transportation/type=segment/*', hive_partitioning=1)
      WHERE inbox(bbox) AND subtype='road') TO '$D/segment_road_${R}_me_bbox.parquet' (FORMAT parquet, COMPRESSION zstd);"
```

`division_area` uses an overlap test instead of `inbox` because large polygons (the state,
counties) start outside the bbox.

Reference sizes: addresses 32 MB, division 0.5 MB, division_area 6 MB, water_named 31 MB,
land_named 4 MB, segment_road 113 MB, places 16 MB.

### Evaluation queries and results

```bash
# Addresses: provenance and overlap with OpenAddresses (number + first street word, ~100 m)
duckdb -c "
LOAD spatial;
SELECT sources[1].dataset AS dataset, count(*) FROM 'data/raw/overture/addresses_2026-08-19.0_me_bbox.parquet' GROUP BY 1;
CREATE TABLE ov AS SELECT id, lower(trim(number)) n, split_part(lower(trim(street)),' ',1) s1,
  ST_X(geometry::GEOMETRY) lon, ST_Y(geometry::GEOMETRY) lat FROM 'data/raw/overture/addresses_2026-08-19.0_me_bbox.parquet';
CREATE TABLE oa AS SELECT lower(trim(NUMBER)) n, split_part(lower(trim(STREET)),' ',1) s1, LON lon, LAT lat
  FROM read_csv('data/pelias/interpolation_oa/us/me/statewide.csv');
SELECT count(DISTINCT ov.id) AS overture_matched_in_oa FROM ov JOIN oa
  ON ov.n=oa.n AND ov.s1=oa.s1 AND abs(ov.lat-oa.lat)<0.001 AND abs(ov.lon-oa.lon)<0.0013;
-- The unmatched remainder: does each have an OA point within ~5 m? (reference: all 24,915 do)
CREATE TABLE ov_only AS SELECT * FROM ov WHERE id NOT IN (SELECT ov.id FROM ov JOIN oa
  ON ov.n=oa.n AND ov.s1=oa.s1 AND abs(ov.lat-oa.lat)<0.001 AND abs(ov.lon-oa.lon)<0.0013);
SELECT count(DISTINCT o.id) AS ov_only_with_oa_within_5m FROM ov_only o JOIN oa a
  ON abs(o.lat-a.lat)<0.00005 AND abs(o.lon-a.lon)<0.00007;"

# Divisions, water, land, roads: volumes by subtype/class
duckdb -c "
SELECT subtype, count(*) FILTER (WHERE region='US-ME') FROM 'data/raw/overture/division_2026-08-19.0_me_bbox.parquet' GROUP BY 1;
SELECT subtype, class, count(*) FROM 'data/raw/overture/water_named_2026-08-19.0_me_bbox.parquet' GROUP BY 1,2 ORDER BY 3 DESC LIMIT 8;
SELECT subtype, class, count(*) FROM 'data/raw/overture/land_named_2026-08-19.0_me_bbox.parquet' GROUP BY 1,2 ORDER BY 3 DESC LIMIT 8;
SELECT count(*), count(names.primary), count(DISTINCT names.primary) FROM 'data/raw/overture/segment_road_2026-08-19.0_me_bbox.parquet';"
```

| Theme | Maine result (2026-08-19.0) | Decision |
|-------|-----------------------------|----------|
| places | 127,185 in bbox -> 76,582 after rules | **Loaded** as source `overture` |
| addresses | 772,684, all from USDOT NAD. Initial match (number + first street word, ~100 m): 747,769. The remaining 24,915 all have an unmatched OA record within 5 m that differs only in directional/suffix spelling ("78 East Grand Avenue" vs "78 E Grand Ave"). 673,765 matched pairs have identical coordinates (1e-6 deg); p95 offset 1 cm. Same upstream data (Maine E911 address points) packaged twice. | **Not loaded**: zero unique addresses for Maine. Keep as a token-free fallback for OA and re-evaluate per state (NH). NAD spells street names out, which could serve as aliases in Phase 10. |
| divisions | 1,145 localities, 1,437 neighborhoods, 16 counties; 916 locality polygons | Not loadable into Pelias PIP (WOF only). Phase 10 admin-hierarchy candidate. |
| base/water (named) | 33k features, 9.6k distinct names (streams 19k, rivers 5.5k, lakes 1.1k, ponds 1k) | OSM-derived, and GNIS covers the names. Phase 10 polygon extents. |
| base/land (named) | 3.4k peaks, 2.3k islands/islets, 335 beaches | Same as water |
| transportation/segment (road) | 500k segments, 218k named, 55k distinct names | Redundant with the OSM polylines already in Pelias. Phase 10 street geometry. |

### Loading another Overture theme into Pelias later

1. Add a converter in `prep/src/pelias_prep/` following `overture.py` (output the CSV
   columns in section 4, own `source` name, validation via `export_csv`).
2. Add the file to `imports.csv.files` in `pelias.template.json`.
3. `uv run pelias-prep <target>`, copy the CSV to `data/pelias/csv/`, then
   `pelias import csv` (after `pelias elastic drop && pelias elastic create` for a clean
   index, or accept that re-importing replaces documents with the same ids).
4. Re-run the tests and compare counts.

---

## 11. Refreshing data

Monthly is a sensible cadence (Overture releases monthly; OSM and OA change continuously).

```bash
# 1. New raw inputs (bump OVERTURE_RELEASE / PROTOMAPS_BUILD pins first if desired)
scripts/fetch_data.sh all
# 2. Re-prepare custom sources
(cd prep && uv run python -m pytest -q && uv run pelias-prep all)
# 3. Clean rebuild of the index
(cd projects/pelias_maine && ../../vendor/pelias-docker/pelias elastic drop)
scripts/build_local.sh
# 4. Verify (section 6 checks + section 7 tests), then snapshot and deploy
scripts/snapshot_local.sh
```

Before overwriting, copy the previous `data/raw/` if you want to keep that release. Upstream
"latest" URLs do not keep old versions. Recommended: archive `data/raw/` and the latest
snapshot to `bigblock` (PBS) or `ObeliskNFS`.

---

## 12. Troubleshooting and known issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Invalid environment var "OA_TOKEN="` from the pelias CLI | The pelias CLI rejects empty values in `.env` | Keep `OA_TOKEN` only in `secrets.env` (already the layout here) |
| `conflate_oa.out` shows 0 rows; interpolation ignores OA | OpenAddresses now ships GeoJSON; the interpolation builder only globs `*.csv` with the legacy header | Run `pelias-prep oa-interp` before `pelias prepare interpolation`; compose sets `OAPATH=/data/interpolation_oa` |
| `conflate_oa.skip` exists | Not an error: it lists OA rows that could not be matched to a street | Check size and spot-check if it grows sharply |
| ZCTA prep error: `Referenced column "geoid" not found` | 2025 Gazetteer switched from tab to pipe delimiter | Reader uses `delim='|'`; for older years use `delim='\t'` |
| GNIS prep fails `rows outside the Maine bbox` | GNIS lists some features whose primary point is out of state | Converter filters them; validation is a guard |
| Overture places across the border (Portsmouth, NH) | Buffer crossed the Piscataqua | Buffer-zone rows need Maine region/ZIP evidence |
| `build_local.sh: syntax error near unexpected token` mid-run | Script edited while running | Re-run the remaining steps, e.g. `scripts/build_local.sh up test` |
| `pelias elastic create` fails | Index already exists | `pelias elastic drop` first (destroys the index) |
| WOF download is 5+ GB | The importer downloads the US-wide SQLite even with `importPlace` | Expected |
| Ranking: "Moosehead Lake" returns neighbourhood "Moosehead"; "Mount Desert Island" returns the town; jetport at rank 5 | Admin records outrank venues with similar names | Tracked as expected failures in the tests; tuning targets |

---

## 13. Sources and licenses

Check each license before redistributing data or derived tiles. The API output carries
per-record `source` so attribution can be shown.

| Source | Publisher | License |
|--------|-----------|---------|
| OpenStreetMap (Geofabrik extract) | OSM contributors | ODbL 1.0 |
| OpenAddresses `us/me/*` | Maine E911 / municipal sources via OpenAddresses | Per source; see the source JSON in openaddresses/openaddresses (`sources/us/me/`) |
| Who's On First | whosonfirst.org / Geocode Earth distribution | CC-BY 4.0, with per-record source licenses |
| TIGER/Line | US Census Bureau | Public domain |
| GNIS Domestic Names | USGS | Public domain |
| ZCTA Gazetteer, cartographic boundaries | US Census Bureau | Public domain |
| Overture places | Overture Maps Foundation | CDLA-Permissive-2.0 |
| Overture base, transportation, divisions | Overture Maps Foundation (OSM-derived) | ODbL 1.0 |
| Overture addresses (Maine = USDOT NAD) | USDOT via Overture | Per record (`sources[].license`) |
| Protomaps basemap | Protomaps (OSM-derived) | ODbL 1.0 (data); attribution required |

References: [pelias/docker](https://github.com/pelias/docker),
[pelias/csv-importer](https://github.com/pelias/csv-importer),
[pelias/openaddresses](https://github.com/pelias/openaddresses),
[pelias/whosonfirst](https://github.com/pelias/whosonfirst),
[Overture cloud sources](https://docs.overturemaps.org/getting-data/cloud-sources/),
[Esri: Overture data as Parquet feature layers](https://www.esri.com/arcgis-blog/products/arcgis-online/announcements/overture-maps-data-as-parquet-feature-layers-early-access),
[Geofabrik Maine](https://download.geofabrik.de/north-america/us/maine.html),
[USGS GNIS downloads](https://www.usgs.gov/us-board-on-geographic-names/download-gnis-data),
[Census Gazetteer files](https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html),
[Protomaps builds](https://maps.protomaps.com/builds/).
