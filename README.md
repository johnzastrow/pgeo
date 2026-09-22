<img src="docs/branding/pgeo-clean.svg" alt="pgeo" width="260">

# pgeo

PGEO is a feature-rich geocoder that can live entirely inside PostgreSQL. It's a personal, petite
PostgreSQL Geocoder (hence the short name PGEO) for when you need just enough geocoding and a
simple deployment. It is fast, resource-efficient, easy to deploy, and feature-rich. With 1 CPU
and 1 GB of RAM it served 32 concurrent users with a median answer in 40 ms and most autocomplete
keystrokes under 35 ms, using rich data for the whole State of Maine. It does not scale
horizontally the way Pelias does. We measured it against Pelias, Photon and Nominatim for
accuracy, speed and scalability: [docs/REPORT.pdf](docs/REPORT.pdf).

This project is unrelated to pgeocode, a Python postal-code library.

**[Getting started](GETTING_STARTED.md)** goes from nothing to a geocoder answering on a public
URL, for any US state. The workstation half is one command:

```bash
scripts/build_region.sh --build ny     # or me,nh,vt, or any state code
```

## Why pgeo

- **It is more accurate on the same data.** On 1,560 ground-truth cases for Maine, pgeo answered
  95.8% correctly against Pelias's 75.5%, with the identical inputs. Photon managed 73.7% and
  Nominatim 61.7%, both on OpenStreetMap alone.
- **It fits on a small server.** One core, one gigabyte, one Postgres. No Elasticsearch, no JVM,
  no six-container stack to keep alive.
- **It is Pelias-compatible.** The same `/v1/search`, `/v1/autocomplete`, `/v1/reverse` and
  `/v1/place` with the same query parameters and the same GeoJSON, so existing Pelias clients
  work unchanged.
- **The data is richer than OSM alone.** OpenAddresses, OpenStreetMap, Who's on First, USGS GNIS,
  Census ZCTAs and Overture Places, conflated into one table.
- **It can be pure SQL.** Serve it with PostgREST and there is no application code in the request
  path at all - or use the FastAPI front end if you want libpostal parsing.
- **You can host it yourself, entirely.** Every source is downloaded straight from its publisher;
  nothing phones home, and no query leaves your network.

## When to use which

| Use | Why |
|---|---|
| **pgeo** | One state or a handful; modest hardware; you already run PostgreSQL; you need address accuracy and autocomplete more than global coverage |
| **[Pelias](https://pelias.io)** | Many countries or the planet; you want to scale out across machines; you have the operational appetite for Elasticsearch |
| **[Photon](https://photon.komoot.io)** | OSM is enough; you want good typo tolerance and a simple single service; venues and addresses are not the priority |
| **[Nominatim](https://nominatim.org)** | OSM is enough; you need detailed reverse geocoding and administrative polygons; you do not need autocomplete |

pgeo's limits are real and measured: it is a single database, so capacity grows by making the
machine bigger rather than by adding machines, and the report's section 4 says where it stops.

## What is in the box

* A PostgreSQL 18 / PostGIS geocoder with a Pelias-compatible HTTP API, served by PostgREST
  (no application code) or FastAPI
* A build pipeline that turns six open datasets into one table, for any US state or combination
  of states
* A demo web page - map, autocomplete, structured search, reverse geocoding, a batch CSV tool,
  and four tabs that show off confidence, boundary search, nearby search and form filling
* Ansible roles that take a bare Debian host to a hardened, rate-limited, API-key-protected
  service
* A test suite that measures accuracy against independent ground truth, not against itself
* Docker images for the pre-processor and the server are designed but not yet published
  ([DOCKER-DEPLOY.md](DOCKER-DEPLOY.md), [docs/DOCKER_IMAGES.md](docs/DOCKER_IMAGES.md))

## Documentation

**Start here**

| Document | What it covers |
|---|---|
| [GETTING_STARTED.md](GETTING_STARTED.md) | Zero to a hosted geocoder, mostly commands |
| [docs/REPORT.md](docs/REPORT.md) / [REPORT.pdf](docs/REPORT.pdf) | The study: four engines, accuracy, capacity, cost |
| [DOCKER-DEPLOY.md](DOCKER-DEPLOY.md) | The container route (designed; images not yet published) |
| [TODO.md](TODO.md) | What is not done, and what was decided about it |

**Running it**

| Document | What it covers |
|---|---|
| [docs/DATA_PIPELINE.md](docs/DATA_PIPELINE.md) | Every source, where it comes from, how it is converted |
| [docs/REBUILD.md](docs/REBUILD.md) | Rebuild everything from scratch, and what each step proves |
| [docs/DEPLOY_PGEO.md](docs/DEPLOY_PGEO.md) | The query host in detail |
| [docs/SECURITY_DEPLOYMENT.md](docs/SECURITY_DEPLOYMENT.md) | The hardening the Ansible roles apply |
| [docs/API_KEYS.md](docs/API_KEYS.md) | Issuing, using and revoking keys at the edge |
| [docs/PGEO_TUNING.md](docs/PGEO_TUNING.md) / [TUNING_REPORT.md](docs/TUNING_REPORT.md) | The tuning profiles and how they were measured |

**Using the API**

| Document | What it covers |
|---|---|
| [docs/PELIAS_COMPATIBILITY.md](docs/PELIAS_COMPATIBILITY.md) | What matches Pelias, and where it deliberately does not |
| [docs/ADDRESS_API.md](docs/ADDRESS_API.md) | `/v1/address`, the structured extension |
| [docs/HTTP_OPTIONS.md](docs/HTTP_OPTIONS.md) | PostgREST or FastAPI, and why you might pick each |

**How it works, and how it was checked**

| Document | What it covers |
|---|---|
| [docs/PGEO_DESIGN.md](docs/PGEO_DESIGN.md) | The schema, the query pipeline, the ranking |
| [docs/PERFORMANCE_OPTIMIZATION.md](docs/PERFORMANCE_OPTIMIZATION.md) | The 0.10.0 optimisation work, including what did not help |
| [docs/TESTING_GUIDE.md](docs/TESTING_GUIDE.md) | What the tests do, in plain language |
| [docs/ACCURACY_RESULTS.md](docs/ACCURACY_RESULTS.md) / [ENGINE_COMPARISON.md](docs/ENGINE_COMPARISON.md) | The measurements |
| [docs/PROJECT_LOG.md](docs/PROJECT_LOG.md) | Goals, questions, decisions, findings, in order |

## Layout

| Path | Purpose |
|------|---------|
| `pgeo/` | The geocoder: SQL, loader, FastAPI app, tuning profiles |
| `regions/` | `regions.json`: the per-state facts every build step reads |
| `prep/` | Python (uv) package `pelias-prep`: GNIS, Census ZCTA, Overture Places -> CSV |
| `scripts/` | Fetch data, build, snapshot, issue API keys, serve the page locally |
| `web/` | Demo page (MapLibre + self-hosted Protomaps basemap) and a reusable search element |
| `infra/ansible/` | Host config for any Debian host: hardening, Docker, query stack, nginx edge |
| `infra/proxmox/`, `infra/wharf/` | VM creation, and a reference TLS front end |
| `tests/` | Accuracy, load, compatibility, security and browser tests |
| `projects/pelias_maine/` | The Pelias stack pgeo was measured against |
| `report/`, `slides/`, `docs/` | The study, the deck, and the documentation above |
| `data/` | Downloads and build artefacts (gitignored, except the measurements) |

## Licence and data

The code is [Apache-2.0](LICENSE). The data it loads is not: each source carries its own licence
and a deployment that redistributes data or derived tiles must comply with them - see [NOTICE](NOTICE)
and `docs/DATA_PIPELINE.md` section 13. A running service reports the same list at `/v1/attribution`.
