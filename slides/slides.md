---
theme: default
title: pgeo - a geocoder inside PostgreSQL
info: |
  pgeo: architecture, requirements, features, use and limits.
  Measured against Pelias on the same open data for the State of Maine.
class: text-center
highlighter: shiki
transition: slide-left
mdc: true
---

# pgeo

A geocoder that lives **inside PostgreSQL**

<div class="opacity-70 mt-4">
Search, autocomplete and reverse geocoding for the State of Maine<br>
PostgreSQL 18 · PostGIS · core and contrib extensions only
</div>

<div class="opacity-50 mt-6 text-sm">
<code>pg</code> + <em>geo</em> — said "pee-geo"
</div>

<div class="abs-bl m-6 text-sm opacity-60">
Measured against Pelias on the same data · full study in <code>docs/REPORT.md</code>
</div>

---

# Why build it

A self-hosted geocoder was needed for one state, a handful of users, and a dispatch
application whose client addresses are sensitive.

<v-clicks>

- **Commercial APIs** charge per request and see every address you look up
- **Pelias** is the established open-source answer: accurate, fast, and **six services plus Elasticsearch**
- The guiding principle was **G6: the fewest components, data staying in PostgreSQL** — unless that produces an inferior product

</v-clicks>

<v-click>

<div class="mt-8 p-4 border-l-4 border-teal-500 bg-teal-50 dark:bg-teal-900/20">

So the question was not "can it be done in SQL?" but **"is the result worse?"**
pgeo was built to answer that honestly, with Pelias as the reference and the test oracle.

</div>

</v-click>

---
layout: two-cols
---

# Architecture

Everything that ranks, filters and formats is **SQL inside one database**.

- **Data**: one `feature` table, 906,101 rows, 654 MB with indexes
- **Query pipeline**: PL/pgSQL — normalize, parse, candidates, filter, score, dedupe, JSON
- **HTTP**: a stateless gateway, no application code

Two front ends, same SQL:

| | |
|---|---|
| **PostgREST** | "pure SQL" — maps `/v1/*` to functions |
| **FastAPI** | thin Python service, same functions |

::right::

<img src="/fig_architecture.png" class="rounded shadow mt-12" />

<div class="text-xs opacity-60 mt-2">
Pelias (left) coordinates six processes; pgeo (right) is two containers.
</div>

---

# The query pipeline

<img src="/fig_query_pipeline.png" class="rounded shadow" />

<v-click>

Each step is plain SQL in a file under version control, so a ranking change is a pull request
and the accuracy gate measures it. Tuning lives in steps **1, 3, 4, 5 and 6**.

</v-click>

---

# What it needs

<div grid="~ cols-2 gap-8">

<div>

## To run

| | |
|---|---|
| Containers | **2** (PostgreSQL, PostgREST) |
| Floor, 3 users | **0.25 vCPU, 1.40 GB** |
| Confirmed on a real VM | **1 vCPU / 1 GB**, 32 users |
| Disk | 654 MB of tables; size for 3-4× |
| Extensions | `postgis`, `pg_trgm`, `unaccent`, `fuzzystrmatch` — all core or contrib |

</div>

<div>

## To build

| | |
|---|---|
| Wall time | 13 min |
| Peak memory | **5.5 GB** |
| CPU | 1.2 cores average |
| Result to ship | a 117 MB dump |

<div class="mt-4 p-3 border-l-4 border-amber-500 bg-amber-50 dark:bg-amber-900/20 text-sm">

**The build needs four times the runtime floor.** The cheap VM that runs pgeo cannot build it —
build on a workstation, ship the dump.

</div>

</div>

</div>

---

# Where it flexes

<v-clicks>

- **Two HTTP front ends** over identical SQL: PostgREST for no application code, FastAPI when you want Python in the path
- **Five tuning profiles** (`tiny` → `workstation`), applied and verified against the running server; `pgeo-tune auto --cpus N --mem-gb M` sizes anything between
- **Three parsing modes**: a rule parser in SQL (most accurate here), libpostal as a service, or as an extension
- **Subset builds** — load only the sources you want
- **It is just a database**: replicas, `pg_dump`, PITR, your existing backup and monitoring all apply
- **Deployable from a 1 GB VPS to a multi-core VM** without changing anything but the profile
- **Deployable without Pelias at all**: `pelias_enabled=false` and pgeo answers on the canonical
  `/v1/*` paths, so an existing Pelias client needs no change and the demo page drops its engine
  switch and Compare tab by itself

</v-clicks>

<v-click>

<div class="mt-6 text-sm opacity-70">
The flexibility that matters most: no new operational vocabulary. If you run PostgreSQL, you already run this.
</div>

</v-click>

---

# The alternatives inside PostgreSQL

pgeo is not the first geocoder in a database. Surveyed September 2026:

| Project | Logic | Data | Interface |
|---|---|---|---|
| **PostGIS TIGER Geocoder** | all PL/pgSQL | US Census TIGER only | SQL only, no HTTP |
| **osmgeocoder** | mostly SQL, Python orchestration | OSM, optionally OpenAddresses | Python library, optional Flask |
| **Nominatim** | indexing in PL/pgSQL, **search in Python** | OSM | HTTP |
| **pgeo** | all SQL | OA, OSM, WOF, GNIS, ZCTA, Overture | HTTP, **no application code** |

<v-click>

<div class="mt-4 p-3 border-l-4 border-teal-500 bg-teal-50 dark:bg-teal-900/20 text-sm">

TIGER is exact-match and Census-only. osmgeocoder needs libpostal and a Python service to do more
than street names. Nominatim computes addresses in the database but searches from an application.

What this survey did not find: a geocoder whose **whole query path is SQL**, served over HTTP with
**no application code**, speaking **an existing geocoder's API**. Uncommon rather than novel — the
pieces are all well known.

</div>

</v-click>

---

# Features

<div grid="~ cols-2 gap-6" class="text-sm">

<div>

**Pelias-compatible API** — 28 of 28 contract cases

- `/v1/search`, `/v1/search/structured`
- `/v1/autocomplete`, `/v1/reverse`
- `/v1/place`, `/v1/attribution`

**Filters**: layers, sources, focus point, rectangle, circle, country, `boundary.gid`, categories

**Beyond Pelias**

- `/v1/address` — USPS Publication 28 structured addresses
- nearest street address for a venue that has none

</div>

<div>

**Built for messy input**

- trigram similarity on whole names, not tokens
- abbreviations expanded before comparing
- the queried town treated as a *location*, not just a name
- confidence lowered for ties between distinct places

**Operations**

- accuracy gate on every build
- tuning profiles verified against the server
- one-command rebuild

</div>

</div>

---

# How accurate

<img src="/fig_accuracy_category.png" class="rounded shadow h-80 mx-auto" />

<div grid="~ cols-4 gap-4" class="mt-4 text-center">
<div><div class="text-3xl font-bold text-teal-600">95.8%</div><div class="text-xs opacity-70">overall (Pelias 75.5%)</div></div>
<div><div class="text-3xl font-bold text-teal-600">87%</div><div class="text-xs opacity-70">typos (Pelias 30%)</div></div>
<div><div class="text-3xl font-bold text-teal-600">95%</div><div class="text-xs opacity-70">misses handled (41%)</div></div>
<div><div class="text-3xl font-bold text-amber-600">3-4×</div><div class="text-xs opacity-70">Pelias throughput per CPU</div></div>
</div>

<div class="text-xs opacity-60 mt-4">
1,560 engine-independent cases whose answers come from the source data.
</div>

---

# The trade, in one screenshot

<div grid="~ cols-2 gap-4">

<div>

Searching the typo **"Portlnd, ME"** on both engines, side by side in the demo app:

| | Results | Time |
|---|---|---|
| **Pelias** | 0 | 17 ms |
| **pgeo** | 5, Portland at rank 1 | 45 ms |

<div class="mt-6 p-3 border-l-4 border-teal-500 bg-teal-50 dark:bg-teal-900/20 text-sm">

pgeo answers what people actually type. Pelias is faster at answering nothing.

</div>

<div class="mt-4 text-xs opacity-70">
That is the whole study: pgeo trades throughput per CPU for accuracy on real input, and for a
quarter of the memory.
</div>

</div>

<img src="/desktop-light-08-compare.png" class="rounded shadow" />

</div>

---

# How small a server

<img src="/fig_frontier.png" class="rounded shadow h-72 mx-auto" />

<div grid="~ cols-2 gap-6" class="mt-4 text-sm">

<div>

Both engines serve three concurrent users on **a quarter of a vCPU**. Memory decides:

| | Floor | Plan |
|---|---|---|
| **pgeo** | 1.40 GB | 1 GB VPS, ~$5/mo |
| **Pelias** | 7.17 GB | 8 GB, ~$48/mo |

</div>

<div>

Confirmed on a real 1 vCPU / 1 GB VM: the three-user load ran at **21% of its latency budget**,
using 307 MB, and held **32 concurrent users**.

<div class="text-xs opacity-60 mt-2">
Pelias's floor is set by services that will not start below a threshold — libpostal needs 2 GB
before it serves anything.
</div>

</div>

</div>

---

# Using it

```bash
# Search
curl 'http://host/v1/search?text=389+Congress+St,+Portland,+ME&size=5'

# Autocomplete, biased to the map centre
curl 'http://host/v1/autocomplete?text=389+congres&focus.point.lat=43.65&focus.point.lon=-70.26'

# Reverse
curl 'http://host/v1/reverse?point.lat=43.6568&point.lon=-70.2626'

# A structured USPS address for whatever the user picked  (pgeo only)
curl 'http://host/v1/address?ids=openstreetmap:venue:node/123456'
```

<v-click>

Responses are GeoJSON with the Pelias `geocoding` block, so **an existing Pelias client needs no
changes**. Point it at the new base URL.

</v-click>

<v-click>

```bash
scripts/pgeo_setup.sh --profile workstation   # secrets, images, containers, edge
scripts/pgeo_rebuild.sh --profile workstation # load, tune, verify, accuracy gate
```

</v-click>

---
layout: two-cols
---

# The demo app

One page, both engines, switchable at the top.

- **Search** with map-centre bias and filters
- **Address** — type-ahead, then a formatted USPS address
- **Structured**, **Reverse**, **Batch** (CSV)
- **Compare** — the same query on both engines

Served same-origin with a strict CSP: no CDN, no remote script, every value written with
`textContent`.

::right::

<img src="/desktop-light-06-engine.png" class="rounded shadow" />

<img src="/desktop-light-07-address.png" class="rounded shadow mt-3" />

---

# Limitations

<v-clicks>

- **Throughput**: 3-4× fewer concurrent users per CPU than Pelias. Plan ~24 users per vCPU, then halve it for headroom
- **One state, tested**. Maine only -- and measured: across Maine's own data range pgeo loses four times its capacity where Pelias loses two, so coverage growth costs pgeo roughly twice as much. Multi-state is still untested
- **English only** — `lang` is accepted and ignored
- **US addresses only** for `/v1/address` (USPS Publication 28)
- **Our own code**: the ranking SQL has one project's eyes on it, where Pelias has many
- **Tuned against the set that scores it** — treat the 20-point accuracy gap as an upper bound until both engines meet queries neither has seen
- **No authorization yet**: anything that reaches the edge can query

</v-clicks>

<v-click>

<div class="mt-4 p-3 border-l-4 border-amber-500 bg-amber-50 dark:bg-amber-900/20 text-sm">

**Autocomplete is the bottleneck**, not reverse geocoding — it has the tightest target (250 ms)
and fires several times per typed word. That is where the next factor of two lives.

</div>

</v-click>

---

# Security, briefly

<div grid="~ cols-2 gap-6" class="text-sm">

<div>

**In pgeo's favour**

- the data store authenticates; the API role holds `SELECT` only
- two components to patch, not six
- no query parameters in the PostgreSQL log

**Against**

- the ranking SQL and edge rules are this project's own code

</div>

<div>

**Shared posture**

- every engine port on loopback; only the edge listens
- `GET` only, per-client rate limits, strict CSP
- 30 posture tests run on every build and deploy

<div class="mt-3 p-3 border-l-4 border-red-500 bg-red-50 dark:bg-red-900/20 text-xs">

**Neither engine authorizes callers yet.** On a LAN that is a decision; on the internet it would
be a defect.

</div>

</div>

</div>

<div class="mt-4 text-xs opacity-70">
A finding worth repeating: PostgreSQL was configured not to log query parameters, but the nginx
access log in front of it recorded the full query string — every address searched, in plaintext.
A control at one layer, undone by a default at the next. Fixed, and now tested.
</div>

---

# When to use which

| Scenario | Use | Why |
|---|---|---|
| A few users, small VM or VPS | **pgeo** | meets every target from 1.4 GB and a quarter core |
| Messy input: typos, venues, half-remembered names | **pgeo** | 87% vs 30% on typos |
| Structured US addresses for another system | **pgeo** | `/v1/address` |
| Hundreds of concurrent users per vCPU | **Pelias** | 3-4× the throughput |
| Multi-state, national, international | **Pelias** | built for planet scale |
| Fewest components, data in PostgreSQL | **pgeo** | a database and a stateless gateway |
| Existing Pelias clients | **either** | pgeo passes the compatibility contract |

---
class: text-center
---

# Read the study

The full comparison — methods, 20 figures, 37 tables, and every number computed from saved
results — is in the repository.

<div class="mt-8 text-left max-w-2xl mx-auto text-sm">

| | |
|---|---|
| `docs/REPORT.md` / `.pdf` | the study |
| `docs/REBUILD.md` | rebuild everything, one command per stage |
| `docs/SECURITY_DEPLOYMENT.md` | deployment tiers and their trade-offs |
| `docs/PELIAS_COMPATIBILITY.md` | what matches Pelias, and what does not |
| `docs/ADDRESS_API.md` | the USPS address endpoint |

</div>

<div class="mt-10 opacity-60 text-sm">
Built on open data: OpenStreetMap (ODbL) · OpenAddresses · Who's On First · USGS GNIS · US Census · Overture Maps
</div>
