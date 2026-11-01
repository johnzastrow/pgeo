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
Table 7). The deployed stack has three containers, but it can go down to PostgreSQL alone.
[Section 9](#9-how-simple-pgeos-stack-can-be) explains each step down and what it costs.

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
client adapter; see `tests/load/k6/session_photon.js` and `session_nominatim.js`. Those adapters
do not yet use Photon's `/structured` endpoint, so this repository's Photon "structured" figures
measure one-line search ([OSM_ENGINES.md](OSM_ENGINES.md)).

---

## 8. Choosing among them

| If you need... | Choose |
|---|---|
| One or a few US states on minimal hardware, with authoritative addresses and venues, and you already run PostgreSQL | **pgeo** |
| Many countries or the planet, horizontal scale, many data sources, and a team comfortable running Elasticsearch | **Pelias** |
| Fast, typo-tolerant type-ahead over OSM, as one simple service, with prebuilt planet dumps | **Photon** |
| Exact OSM addressing, detailed reverse geocoding and admin polygons, with continuous OSM updates and no autocomplete | **Nominatim** |

---

## 9. How simple pgeo's stack can be

The geocoder is the database. Every endpoint is a SQL function in the `geocode_api` schema that
returns a ready-made GeoJSON `FeatureCollection` as `jsonb`: `v1_search`,
`v1_search_structured`, `v1_autocomplete`, `v1_reverse`, `v1_place`, `v1_address` and
`v1_attribution` (`pgeo/sql/050_api.sql`, `060_address.sql`). Everything outside PostgreSQL only
carries requests to those functions or protects them. That is why the stack can shrink so far,
and why each layer you remove costs features at the edge, not geocoding.

### 9.1 The four shapes

| Shape | Processes | URL shape | Pelias-compatible | Suits |
|---|---|---|---|---|
| **A. Full edge** (the default) | PostgreSQL, PostgREST, nginx, plus a TLS terminator | `/v1/search?...` | Yes | Public internet; existing Pelias clients; the demo page |
| **B. FastAPI direct** | PostgreSQL, FastAPI (plus libpostal if wanted) | `/v1/search?...` | Yes | Internal networks; behind a gateway you already run; libpostal parsing |
| **C. PostgREST direct** | PostgreSQL, PostgREST | `/rpc/v1_search?...` | No (flat parameter names) | Greenfield HTTP clients |
| **D. SQL only** | PostgreSQL (or a database on a server you already run) | `SELECT geocode_api.v1_search(...)` | No | Greenfield apps that already connect to PostgreSQL |

In shape A, Caddy can replace nginx and also terminate TLS; section 9.4 shows how.

PostgreSQL 18, PostGIS 3.6 and the `pg_trgm`, `fuzzystrmatch` and `unaccent` extensions are the
floor that no shape removes. libpostal is never required: the SQL rule parser is the default, and
it measured better than libpostal ([REPORT.md](REPORT.md) section 3.1).

### 9.2 Greenfield: the smallest stack that keeps every function

If you are writing a new client and do not need Pelias compatibility, **shape C or D keeps every
geocoding function**. That means forward search, structured search, autocomplete, reverse,
lookup by ID, the USPS-style `/v1/address`, all the filters and the confidence score. What
changes is only the address and the parameter names.

**Shape C: two containers.** Run the `db` and `rest` services from `deploy/compose.yml` without
`edge`, and publish PostgREST's port. Call the functions as RPCs, using the SQL argument names:

```bash
curl 'http://geo.internal:3000/rpc/v1_search?text=12%20main%20st%20portland&size=5'
curl 'http://geo.internal:3000/rpc/v1_autocomplete?text=congress%20s&lat=43.66&lon=-70.26'
curl 'http://geo.internal:3000/rpc/v1_search_structured?address=12%20main%20st&locality=portland&region=me'
curl 'http://geo.internal:3000/rpc/v1_reverse?lat=43.6591&lon=-70.2568&layers=address&radius=0.2'
curl 'http://geo.internal:3000/rpc/v1_address?text=12%20main%20st%20portland%20me'
curl -H 'Accept: text/html' 'http://geo.internal:3000/rpc/v1_attribution'
```

**Shape D: one container, or none.** An application that already holds a PostgreSQL connection
calls the functions as ordinary queries, with no HTTP layer at all:

```sql
SELECT geocode_api.v1_search(text => '12 main st portland', size => 5);
SELECT geocode_api.v1_reverse(lat => 43.6591, lon => -70.2568, layers => 'address');
```

You can restore the dump into a database server you already run, so pgeo adds no process at
all. That server needs PostgreSQL 18 or newer (`pg_restore` does not go to an older major
version), PostGIS 3.6 and the three contrib extensions. Restore into a dedicated database, because
the dump brings the schemas `geocode`, `geocode_api` and the build schemas. Create the `pgeo_api`
login role yourself, as `deploy/db/init/10-api-role.sh` does, because a dump does not carry role
passwords, and set its statement timeout (see 9.3).

**Parameter names** are the last part of each Pelias parameter, which is also how PostgREST reads
a dotted key:

| Pelias parameter | Function argument |
|---|---|
| `text`, `size`, `layers`, `sources`, `categories` | the same |
| `focus.point.lat` / `.lon` (search, autocomplete) | `lat` / `lon` |
| `point.lat` / `.lon` (reverse) | `lat` / `lon` |
| `boundary.rect.min_lat` ... `max_lon` | `min_lat` ... `max_lon` |
| `boundary.circle.lat` / `.lon` / `.radius` (search, autocomplete) | `circle_lat` / `circle_lon` / `circle_radius` |
| `boundary.circle.radius` (reverse) | `radius` |
| `boundary.country`, `boundary.gid` | `country`, `gid` |
| structured: `address`, `locality`, `region`, `postalcode`, ... | the same |
| `ids` (place) | `ids` |

The circle row is the one rename that matters. Search and autocomplete accept both a focus
point and a circle. Both would end in `.lat` and `.lon`, so the functions name the circle
`circle_*`, and the nginx edge renames it for Pelias clients. A greenfield client sends
`circle_*` directly.

### 9.3 What each step down gives up

| Lost when you remove... | Effect | Replacement, if you need it |
|---|---|---|
| **nginx: Pelias paths** (C, D) | Pelias clients do not work unchanged | Use the RPC paths, or choose shape B |
| **nginx: circle renaming** (C) | `boundary.circle.*` collides with `focus.point.*` | Send `circle_lat`, `circle_lon`, `circle_radius` |
| **nginx: API keys** (B, C, D) | No authentication. FastAPI checks only that `api_key` is short, then ignores it | A gateway that checks keys (Caddy, Traefik, a cloud load balancer or API gateway), or a private network |
| **nginx: rate limiting** (B, C, D) | Neither front end limits request rates. One client can saturate a 1-core server | Gateway rate limiting. Caddy needs a plugin for this |
| **nginx: security headers, GET-only, 1 MB body cap** (B, C, D) | Missing on responses | Gateway configuration; not needed for server-to-server calls |
| **nginx: demo page and basemap** (B, C, D) | Not served. FastAPI serves no static files, and the page expects the `/v1/` paths | Keep the edge if you want the page |
| **nginx: Pelias-shaped 404s** (C) | Unknown paths get PostgREST's own error | Clients handle PostgREST errors |
| **PostgREST and its HTTP status codes** (D) | Bad input returns the JSON envelope with `geocoding.errors` filled in, not an HTTP 400. The function sets `response.status`, but only PostgREST reads it | Check `geocoding.errors` in the result |
| **A separate network hop** (D) | The application holds database credentials and pools its own connections | Connect as the read-only `pgeo_api` role only. Never use the owner role |

These protections survive every shape, because they live in the database or in PostgREST
configuration, not in nginx:

- The `pgeo_api` role is read-only. It owns nothing and can only `SELECT` and execute the API
  functions.
- PostgREST exposes only the `geocode_api` schema, returns at most 40 rows and has the OpenAPI
  description disabled.
- Every function validates and bounds its input in SQL: text up to 200 characters, `size` from 1
  to 40, and at most 40 `ids`.
- The `pgeo_api` role has `statement_timeout = '5s'`, so no single query can hold a core longer
  than that. The slowest measured query is about 0.5 s (a full New York address parse), so the
  cap only stops runaways. With no edge to absorb a burst, this is what bounds the cost of each
  request.

**The rule.** Shapes B, C and D are fine on a private network, between services or behind a gateway
you already operate. Do not expose PostgREST, FastAPI or PostgreSQL directly to the internet.
Whatever faces the internet needs TLS, authentication and rate limits, and in shape A that
something is the nginx edge plus a TLS terminator. If you restore the dump into your own
database server (shape D), set the statement timeout yourself, because a dump does not carry
role settings: `ALTER ROLE pgeo_api SET statement_timeout = '5s'`. Every route in this
repository already sets it: `deploy/db/init/10-api-role.sh`, `scripts/pgeo_swap.sh`, the
Ansible `pgeo_runtime` role and the loader. A query that hits the cap is cancelled and returns
an error: HTTP 500 from PostgREST or FastAPI, or SQLSTATE `57014` in shape D.

### 9.4 Pelias compatibility with Caddy instead of nginx

Shape A does not need nginx specifically. Caddy can take the edge's place and terminate TLS as
well, so the stack is still three processes (PostgreSQL, PostgREST, Caddy), but with automatic
HTTPS and no separate TLS terminator. Everything nginx does for compatibility is a rewrite of the
request, and Caddy has a directive for each:

| nginx edge does | Caddy equivalent | Built in? |
|---|---|---|
| Maps `/v1/<endpoint>` to `/rpc/v1_<endpoint>` | `rewrite` (the query string is kept) | Yes |
| Renames `boundary.circle.*` to `circle_*` on search, structured and autocomplete | `uri query { old>new }` | Yes |
| Asks PostgREST for HTML on `/v1/attribution` | `request_header Accept text/html` | Yes |
| Returns a Pelias-shaped 400 for unknown `/v1/*` paths | `respond` with a JSON body | Yes |
| Accepts GET only; caps the body at 1 MB | `not method GET` matcher; `request_body { max_size }` | Yes |
| Security headers (CSP and others) | `header` | Yes |
| Serves the demo page and basemap, with range requests | `file_server` | Yes |
| Checks API keys | `map` on the `X-API-Key` header plus a `vars` matcher | Yes, but the keys are kept in plain text (see below) |
| Rate-limits each client IP | `rate_limit` | No. Needs the [caddy-ratelimit](https://github.com/mholt/caddy-ratelimit) plugin in a custom build (`xcaddy build --with github.com/mholt/caddy-ratelimit`) |
| Injects `<meta name="demo-engines">` into the demo page | None (Caddy has no built-in response rewriting) | No. Add the tag to the served copy of `web/index.html` instead |

A complete site block, equivalent to `deploy/edge/pgeo.conf`. It assumes PostgREST is reachable
as `rest:3000` (for example on the same Compose network) and the demo page is at `/srv/web`.

```caddyfile
geocoder.example.org {
	encode zstd gzip
	request_body {
		max_size 1MB
	}
	header {
		Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; worker-src 'self' blob:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
		X-Content-Type-Options nosniff
		Referrer-Policy strict-origin-when-cross-origin
		X-Frame-Options DENY
	}

	# API keys: header value -> client name. Keys come from the environment, never this file.
	# Delete this block and the @nokey lines below to run without keys.
	map {header.X-API-Key} {api_client} {
		{$PGEO_KEY_WEBAPP}  webapp
		{$PGEO_KEY_BATCH}   batch
		default             none
	}

	# Everything under /v1 runs in the order written (route), and nothing else reaches PostgREST:
	# a client that asks for /rpc/... directly falls through to the file server and gets a 404.
	route /v1/* {
		@notget not method GET
		respond @notget 405

		@nokey vars {api_client} none
		respond @nokey `\{"geocoding":\{"errors":["missing or unknown API key"]},"type":"FeatureCollection","features":[]}` 401

		# Needs the caddy-ratelimit plugin; delete this block on a stock Caddy.
		rate_limit {
			zone pgeo_api {
				key {remote_host}
				events 100
				window 10s
			}
		}

		# boundary.circle.* would collide with focus.point.* once PostgREST keeps only the last
		# segment of a dotted name. Reverse is left alone: its radius is already the last segment.
		@circle path /v1/search /v1/search/structured /v1/autocomplete
		uri @circle query {
			boundary.circle.lat>circle_lat
			boundary.circle.lon>circle_lon
			boundary.circle.radius>circle_radius
		}

		@attribution path /v1/attribution
		request_header @attribution Accept text/html

		# Paths match exactly, so /v1/search does not catch /v1/search/structured.
		rewrite /v1/search            /rpc/v1_search
		rewrite /v1/search/structured /rpc/v1_search_structured
		rewrite /v1/autocomplete      /rpc/v1_autocomplete
		rewrite /v1/reverse           /rpc/v1_reverse
		rewrite /v1/place             /rpc/v1_place
		rewrite /v1/address           /rpc/v1_address
		rewrite /v1/attribution       /rpc/v1_attribution

		@unknown not path /rpc/*
		respond @unknown `\{"geocoding":\{"version":"0.2","errors":["unsupported parameter or endpoint: pgeo accepts the documented Pelias parameters"]},"type":"FeatureCollection","features":[]}` 400

		reverse_proxy rest:3000
	}

	# The demo page and the basemap (file_server handles range requests for PMTiles).
	handle {
		root * /srv/web
		file_server
	}
}
```

Where it differs from the nginx edge:

- **API keys are compared in plain text.** The nginx edge stores only SHA-256 hashes
  (`docs/API_KEYS.md`). Caddy's `map` compares the header against the key itself, so the real keys
  must sit in Caddy's environment (`{$PGEO_KEY_...}` is read at startup). Keep that environment
  file at mode 600, and never write keys into the Caddyfile. If you would rather store hashes,
  use `forward_auth` to a small checker, at the cost of one more process.
- **Rate limiting needs a custom build**, as the table says. On a stock Caddy, delete the
  `rate_limit` block, and the edge then gives no protection against a burst. The 5 s statement
  timeout on `pgeo_api` still bounds what each query costs.
- **The circle rename runs before the path rewrite**, because the `@circle` matcher tests the
  original `/v1/...` path. Inside `route` the directives run in the order written, which is why
  this block uses `route` rather than Caddy's default directive order.
- **Braces in the JSON error bodies are escaped** (`\{`) so that Caddy does not read them as
  placeholders.

This site block has not been run in this repository; the reference front end is
`infra/wharf/pelias.caddy`, which proxies to the nginx edge rather than replacing it. Check the
block with `caddy validate --config Caddyfile` before you deploy it, and run the compatibility
test against the result:
`uv run --project pgeo python tests/compat/compat_test.py --sql https://geocoder.example.org`.

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
