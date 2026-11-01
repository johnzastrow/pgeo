# Requirements and technology stacks: pgeo, Pelias, Photon and Nominatim

What each of the four open-source geocoders needs to build and run: languages, data stores,
services, data sources, hardware and the operational work involved. Use it to decide which engine
fits your infrastructure before you look at which one is most accurate or fastest.

Two kinds of figures appear below, and they are labelled:

- **Published:** what each project documents for itself, usually for a full-planet deployment.
  Links are under [Sources](#sources).
- **Measured:** what this repository measured for all four engines, using the same Maine data on
  the same hardware (Ryzen 5 3600, 31 GB, NVMe). Details are in [REPORT.md](REPORT.md) sections
  3.12, 3.14 and 3.15, [OSM_ENGINES.md](OSM_ENGINES.md) and [SIZING.md](SIZING.md).

The upstream versions are the ones documented on 2026-11-01. The versions measured here are
Pelias images from 2026-08 with Elasticsearch 7.17.27, Photon 1.3.0, Nominatim 4.5 (the
`mediagis/nominatim:4.5` image) and pgeo 0.19.x on PostgreSQL 18.6 with PostGIS 3.6.4.

---

## 1. At a glance

| | **pgeo** | **Pelias** | **Photon** | **Nominatim** |
|---|---|---|---|---|
| What it is | Geocoder that runs inside PostgreSQL, with a Pelias-compatible API | Modular geocoder built from microservices | Search-as-you-type geocoder for OSM | Reference OSM geocoder, behind openstreetmap.org |
| Main language | SQL / PL/pgSQL (Python for loading, plus an optional front end) | Node.js (Go for some tools) | Java | Python, with PL/pgSQL in the database |
| Search store | PostgreSQL + PostGIS | Elasticsearch | OpenSearch (embedded by default) | PostgreSQL + PostGIS |
| Address parser | Rule parser in SQL; libpostal optional | libpostal (statistical, about 2 GB model) | None (full-text match) | Built-in query analysis (ICU tokenizer) |
| Data sources | OSM, OpenAddresses, Who's on First, USGS GNIS, Census ZCTA, Overture Places | OSM, OpenAddresses, Who's on First, Geonames, polylines, custom CSV, TIGER (interpolation) | OSM only, via Nominatim | OSM only (plus optional TIGER, Wikipedia importance and postcodes) |
| Intended scope | One US state or a few states | City to whole planet | Country to whole planet | Country to whole planet |
| Containers when serving | 2-3 | 6 or more | 1 (JVM) | 1-2 (PostgreSQL + Python API) |
| Autocomplete endpoint | Yes | Yes | Yes (its main purpose) | No |
| Structured endpoint | Yes, plus `/v1/address` (USPS Pub. 28) | Yes | Yes (`/structured`) | Yes (`/search` with field parameters) |
| Confidence score | Yes, per result | Yes, per result | No | No (`importance` is a rank, not a confidence) |
| Incremental updates | No: rebuild and swap a dump | No: rebuild and swap an index | Yes, from a Nominatim database | Yes, by OSM replication |
| Scale-out model | Vertical (one database) | Horizontal (Elasticsearch cluster plus stateless API) | Bigger heap, or an external OpenSearch cluster | PostgreSQL scale-up and replicas |
| Code licence | Apache-2.0 | MIT | Apache-2.0 | GPL-2.0 |

---

## 2. Architecture in brief

**pgeo** keeps the data, parsing, candidate generation, ranking and JSON formatting inside
PostgreSQL. The HTTP layer is either PostgREST, a stateless gateway that turns
`/rpc/v1_search` into a function call and contains no application code, or a thin FastAPI
application. nginx maps the Pelias paths (`/v1/search`, and so on) onto those calls and applies
rate limits, API keys and security headers. See [PGEO_DESIGN.md](PGEO_DESIGN.md) and
[HTTP_OPTIONS.md](HTTP_OPTIONS.md).

**Pelias** has three parts: importers, an Elasticsearch index and query services. A Node.js API
coordinates four helpers. *placeholder* holds the admin hierarchy in SQLite. *pip* does
point-in-polygon lookups in memory. *interpolation* estimates house-number positions from
SQLite. *libpostal* parses addresses. Each service can be scaled or replaced on its own.

**Photon** is one Java process with an OpenSearch index. It has no importer of its own: it reads a
Nominatim database, or a prebuilt dump that GraphHopper publishes weekly. It is designed for
type-ahead and typo tolerance on OSM names.

**Nominatim** imports OSM with osm2pgsql into PostgreSQL/PostGIS. PL/pgSQL triggers then compute
each object's address hierarchy and search terms. The search frontend is a Python application
(SQLAlchemy plus falcon or starlette under uvicorn or gunicorn). Version 5 finished the move from
PHP to Python.

pgeo and Nominatim are the closest relatives: both put the indexing work in PostgreSQL. The
difference is that Nominatim runs its search in Python, while pgeo's search runs in SQL.

---

## 3. Software requirements

### 3.1 Run time (what a server answering queries needs)

| Component | **pgeo** | **Pelias** | **Photon** | **Nominatim** |
|---|---|---|---|---|
| Database / index | PostgreSQL 18, PostGIS 3.6 | Elasticsearch 7.5+ (7.x recommended; 8 supported) | OpenSearch, embedded (an external cluster needs OpenSearch 3.x) | PostgreSQL 12+ (13+ recommended), PostGIS 3.0+ (3.2+ recommended) |
| Database extensions | `pg_trgm`, `fuzzystrmatch`, `unaccent` (all ship with PostgreSQL) | - | - | PostGIS, plus Nominatim's own SQL functions |
| Language runtime | None (PostgREST), or Python 3.12+ (FastAPI) | Node.js 22+ | Java 21+ | Python 3.9+ |
| Application libraries | FastAPI path only: FastAPI, Uvicorn, asyncpg, Pydantic | Pelias API and service packages (npm) | Bundled in the JAR | SQLAlchemy 1.4.31+, falcon 3.0+ or starlette, uvicorn, psycopg 3, PyICU, Jinja2, PyYAML |
| Helper services | None | placeholder, pip, interpolation, libpostal | None | None |
| Other storage | - | SQLite 3.11+ (placeholder, interpolation) | - | - |
| Address parser model | None by default; libpostal (about 1.9 GB) is optional | libpostal, about 2 GB of RAM and about 4 GB of disk to download | None | None (ICU tokenizer) |
| HTTP edge | nginx (or any reverse proxy); PostgREST v16.3 | Any reverse proxy | Built-in HTTP server on port 2322 | uvicorn/gunicorn behind a reverse proxy |
| Container images | `postgis/postgis:18-3.6`, `postgrest/postgrest:v16.3`, `nginx` (all stock) | `pelias/*` images plus `pelias/elasticsearch` | Not official; usually run as a JAR | `mediagis/nominatim` (community) |
| Host OS | Any OS with Docker. Deployment roles target Debian 13 | Linux or macOS. "Not well tested on Windows" | Any OS with Java 21 | Linux. The install docs target Ubuntu and Debian |

What pgeo leaves out matters as much as what it includes. It needs no search engine, no JVM, no
Node runtime, no message queue and no separate parsing, point-in-polygon or interpolation
service. Of its seven run-time components, three ship inside PostgreSQL ([REPORT.md](REPORT.md)
Table 7).

### 3.2 Build time (what turns raw data into an index)

| | **pgeo** | **Pelias** | **Photon** | **Nominatim** |
|---|---|---|---|---|
| Orchestration | `scripts/build_region.sh` (one command per state or set of states) | Pelias CLI (`pelias compose`, `pelias import ...`) | `java -jar photon.jar -nominatim-import`, or download a dump | `nominatim import` |
| Data tools | DuckDB 1.5.5, GDAL `ogr2ogr`, Python 3.12+ via `uv`, `jq`, `curl` | Per-source Node.js importers (openstreetmap, openaddresses, whosonfirst, csv, polylines, interpolation) | Needs a running Nominatim database | osm2pgsql 1.8+, PyICU, pyosmium (for updates) |
| Output you ship | PostgreSQL dump (117 MB for Maine) | Elasticsearch snapshot plus helper databases (435 MB snapshot for Maine) | OpenSearch data directory (297 MB for Maine) | Whole PostgreSQL database |
| Build and serve on separate machines | Yes, and recommended | Yes | Yes | Usually built where it is served |

---

## 4. Data sources

| Source | **pgeo** | **Pelias** | **Photon** | **Nominatim** |
|---|---|---|---|---|
| OpenStreetMap | Yes | Yes | Yes (via Nominatim) | Yes |
| OpenAddresses (authoritative address points, e.g. E911) | Yes | Yes | No | No |
| Who's on First (admin hierarchy) | Yes | Yes | No | No (uses OSM boundaries) |
| Overture Places (venues) | Yes | Via the CSV importer | No | No |
| USGS GNIS (named places) | Yes | Via the CSV importer | No | No |
| Census ZCTA (postcodes) | Yes | Via the CSV importer | No | Postcode computation from OSM; optional external postcode files |
| US TIGER | No | Yes (interpolation) | No | Optional (house-number interpolation) |
| Geonames | No | Yes | No | No |
| Custom data | Add a source to the SQL loader | CSV importer | JSON dump import | OSM-format data only |

This is the row that decides most outcomes in the measurements. Photon and Nominatim can index
only OpenStreetMap. In Maine, that leaves out most authoritative address points and most
commercial venues. Venue accuracy was 19.1% for Photon against 94.2% for pgeo, and overall
accuracy was 73.7% for Photon and 61.7% for Nominatim ([OSM_ENGINES.md](OSM_ENGINES.md)).

---

## 5. Hardware requirements

### 5.1 Published by each project

| | **pgeo** | **Pelias** | **Photon** | **Nominatim** |
|---|---|---|---|---|
| Minimum RAM | 1 GB for a Maine-sized state; 4 GB for a New York-sized state | "At least 8GB RAM" even for a small metro project | Not stated for extracts. Planet: 64 GB recommended | 2 GB minimum, "or installation will fail" |
| Planet RAM | Not supported (US states only) | Not stated in the docker README | 64 GB recommended | 128 GB or more strongly recommended; 64 GB the floor for support |
| Planet disk | - | Tens to hundreds of GB of intermediate data (about 176 GB for interpolation alone) | About 95 GB, growing about 10% a year; twice that during an update | About 1 TB, on NVMe |
| Planet build time | - | "Under a day" on about 32 cores, excluding interpolation (6+ days, single-threaded) | Hours to export from Nominatim; or download a prebuilt dump | About 2.5 days on NVMe; 4-5 days on SATA SSD |

### 5.2 Measured here, Maine (906,000 features for pgeo)

| | **pgeo** (pure SQL) | **Pelias** | **Photon** | **Nominatim** |
|---|---|---|---|---|
| Smallest configuration that met every latency target | 1 vCPU, 1 GB (32 users) | 1 vCPU, about 8.3 GB (96 users) | Not tested below 2 cores | Not tested below 2 cores |
| Memory under load, 2 cores | 2.5 GB budget | 8.4 GB budget | 1.84 GB resident | 2.19 GB resident |
| Concurrent users within targets, 2 cores | 48 (64 after the 0.10.0 optimisation) | 192 | 128 | 64 |
| Throughput at the limit, 2 cores | 53 req/s | 209 req/s | 127 req/s | 68 req/s |
| Disk when serving | About 1.0 GB database, about 2.6 GB including WAL | About 1.4 GB Elasticsearch plus 0.9 GB helper databases | 297 MB index | About 3.8 GB container data plus a 2.4 GB image |
| Build time (inputs downloaded) | About 5 minutes | About 9 minutes | 39 seconds, after an 8-minute Nominatim import | About 8 minutes |
| Accuracy, 1,560 ground-truth cases | 95.8% | 75.5% | 73.7% | 61.7% |

Two caveats. Photon ran with its default JVM heap, about 7.75 GB maximum on this host, so its
memory figure is resident set rather than a tuned budget. Nominatim and Photon were also measured
on OSM data only, because that is all they can import, while pgeo and Pelias were measured on
every source.

For a larger region, pgeo's requirements grow with the feature count. New York has eight times
Maine's data and wants 2 vCPU, 4 GB and 20 GB of disk ([SIZING.md](SIZING.md)).

---

## 6. Operational requirements

| Concern | **pgeo** | **Pelias** | **Photon** | **Nominatim** |
|---|---|---|---|---|
| Moving parts to monitor | PostgreSQL, PostgREST, nginx | Elasticsearch plus five Node/Go services | One JVM | PostgreSQL plus the Python frontend |
| Typical failure under memory pressure | Slower queries as the index leaves cache | Services OOM-killed (placeholder, pip, interpolation) | JVM heap or GC pressure | Slow queries and imports |
| Refreshing data | Rebuild on a workstation; `pgeo_swap.sh` restores the new dump with no downtime | Rebuild the index; swap the snapshot | Re-export or re-download; or apply updates from Nominatim | Continuous minutely, hourly or daily OSM replication |
| Scaling up | More cores and RAM; tuning profiles in `pgeo/tuning/` | More API workers and a bigger heap | More heap and cores | More RAM and faster disk |
| Scaling out | PostgreSQL read replicas behind PostgREST (possible, not built or tested here) | Elasticsearch shards and replicas plus stateless API instances | External OpenSearch cluster, or several read-only instances | PostgreSQL replicas plus several frontends |
| Security model in this repo | Read-only database role; PostgREST exposes one schema; nginx API keys and rate limits; strict CSP | Read-only services; Elasticsearch has no credentials and listens on loopback only | Not deployed (measurement only) | Not deployed (measurement only) |
| Provisioning provided here | Ansible roles for a hardened Debian host; Compose bundle in `deploy/` | Compose stack in `projects/pelias_maine/`; Ansible role | Scripts in `tests/load/` | `docker run` line in [OSM_ENGINES.md](OSM_ENGINES.md) |

---

## 7. API compatibility

| | **pgeo** | **Pelias** | **Photon** | **Nominatim** |
|---|---|---|---|---|
| Forward search | `/v1/search` | `/v1/search` | `/api` | `/search` |
| Autocomplete | `/v1/autocomplete` | `/v1/autocomplete` | `/api` (designed for prefixes) | None. Its public usage policy forbids search-as-you-type |
| Structured | `/v1/search/structured`, `/v1/address` | `/v1/search/structured` | `/structured` | `/search?street=...&city=...` |
| Reverse | `/v1/reverse` | `/v1/reverse` | `/reverse` | `/reverse` |
| Lookup by ID | `/v1/place` | `/v1/place` | - | `/lookup`, `/details` |
| Response format | GeoJSON, Pelias-shaped | GeoJSON, Pelias-shaped | GeoJSON (GeocodeJSON properties) | JSON, JSONv2, GeoJSON, GeocodeJSON, XML |

pgeo is a drop-in replacement for a Pelias client. Photon and Nominatim each need their own
client adapter; see `tests/load/k6/session_photon.js` and `session_nominatim.js`.

---

## 8. Choosing among them

| If you need... | Choose |
|---|---|
| One or a few US states on minimal hardware, with authoritative addresses and venues, and you already run PostgreSQL | **pgeo** |
| Many countries or the planet, horizontal scale, many data sources, and a team comfortable running Elasticsearch | **Pelias** |
| Fast, typo-tolerant type-ahead over OSM, as one simple service, with prebuilt planet dumps | **Photon** |
| Exact OSM addressing, detailed reverse geocoding and admin polygons, with continuous OSM updates and no autocomplete | **Nominatim** |

---

## Sources

Upstream documentation, accessed 2026-11-01:

1. Nominatim installation requirements (5.3.3 manual): <https://nominatim.org/release-docs/latest/admin/Installation/>
2. Nominatim API overview: <https://nominatim.org/release-docs/latest/api/Overview/>
3. Nominatim architecture overview: <https://nominatim.org/release-docs/latest/develop/overview/>
4. Nominatim source and licence (GPL-2.0): <https://github.com/osm-search/Nominatim>
5. The road to Nominatim 6 (July 2025): <https://nominatim.org/2025/07/14/roadmap-nominatim6.html>
6. Nominatim usage policy (no autocomplete): <https://operations.osmfoundation.org/policies/nominatim/>
7. Photon README (requirements, hardware, data import, Apache-2.0): <https://github.com/komoot/photon>
8. Photon API documentation: <https://github.com/komoot/photon/blob/master/docs/api-v1.md>
9. Photon releases (1.0.0: OpenSearch backend, Java 21 minimum; latest 1.3.0): <https://github.com/komoot/photon/releases>
10. Pelias software requirements: <https://github.com/pelias/documentation/blob/master/requirements.md>
11. Pelias Docker (hardware, build time, supported OS): <https://github.com/pelias/docker>
12. Pelias architecture and data sources: <https://pelias.io/> and <https://github.com/pelias/pelias>
13. libpostal: <https://github.com/openvenues/libpostal>
14. PostgREST: <https://postgrest.org>
15. PostGIS: <https://postgis.net>

This repository's measurements:

- [REPORT.md](REPORT.md): Tables 2, 7-11 and 32, and sections 3.12, 3.14 and 3.15
- [OSM_ENGINES.md](OSM_ENGINES.md): how Photon and Nominatim were built and measured
- [ENGINE_COMPARISON.md](ENGINE_COMPARISON.md): pgeo against Pelias under load
- [SIZING.md](SIZING.md): pgeo build and serving costs for Maine and New York
