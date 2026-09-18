# Pelias Maine (+NH) Geocoder -- Project Plan

Status: DRAFT for review (2026-09-18)

## 1. Goal

Self-hosted, feature-rich forward/reverse geocoder for Maine (New Hampshire optional),
running Pelias on a VM on the Proxmox server, loaded with every applicable Pelias data
source, plus a MapLibre demo page that exercises the API's capabilities.

Pattern: adapt `pelias/docker` `projects/texas` (reviewed at pelias/docker commit
`3dfa07d`, 2026-03-25). Texas is a stock "state" project: OSM + OpenAddresses +
Who's On First + GeoNames + polylines + TIGER interpolation, all driven by the
`pelias` CLI and `docker compose`.

## 2. Architecture

```
            Browser (demo page, MapLibre GL JS)
                         |  HTTPS
            +------------v-------------+   Proxmox VM: Ubuntu 24.04 LTS
            | Caddy reverse proxy      |   (TLS, rate limit, CORS allowlist,
            |  /        -> static demo |    serves demo page same-origin)
            |  /v1/*    -> api:4000    |
            +------------+-------------+
                         | 127.0.0.1 only below this line
   +---------+-----------+-----------+------------+-------------+
   | api     | placeholder| pip       | interpolation| libpostal |
   | :4000   | :4100      | :4200     | :4300        | :4400     |
   +----+----+------------+-----------+--------------+-----------+
        |
   Elasticsearch 7.17 (pelias/elasticsearch image), :9200 localhost only
   Importers (run-once containers): openstreetmap, openaddresses, whosonfirst,
   polylines, csv-importer, transit, (geonames)
```

## 3. Data sources

| # | Source | Pelias importer | Maine config | NH config | Notes |
|---|--------|-----------------|--------------|-----------|-------|
| 1 | OpenStreetMap | openstreetmap | Geofabrik `maine-latest.osm.pbf` | `new-hampshire-latest.osm.pbf` | Venues, addresses, streets. If both states, merge with `osmium merge` into one PBF so polylines/valhalla sees one file. |
| 2 | OpenAddresses | openaddresses | `us/me/statewide`, `us/me/city_of_biddeford` | `us/nh/statewide` + 5 town sources | Requires a (free) batch.openaddresses.io token in `imports.openaddresses.token`; the shared Pelias token can be throttled. |
| 3 | Who's On First | whosonfirst | `importPlace: ["85688769"]` (Maine region, verified) | NH region id TBD (lookup in Phase 2) | Admin hierarchy + postal codes (`importPostalcodes: true`). Also feeds `pip` and `placeholder`. |
| 4 | Polylines (streets) | polylines | Generated from the OSM PBF by `pelias prepare polylines` (Valhalla) | same | Street-level results where no address exists. |
| 5 | TIGER interpolation | interpolation | `state_code: 23` | `state_code: 33` | Address-range interpolation between known house numbers. |
| 6 | GeoNames | geonames | `countryCode: US` | same | Imports all of the US; mostly redundant with WOF/OSM. Recommend SKIP, or clip to ME/NH and load via CSV. |
| 7 | Transit (GTFS) | transit | METRO (Portland), South Portland, Amtrak Downeaster, others TBD | Concord/Manchester feeds TBD | Stops/stations as venues. Importer is lightly maintained; optional. |
| 8 | USGS GNIS | csv-importer (custom) | GNIS Domestic Names, ME file -> CSV | NH file | Lakes, mountains, islands, localities; strong for Maine's unorganized territories. Source `gnis`, layer by feature class. |
| 9 | Census ZCTA | csv-importer (custom) | ZCTA centroids filtered to ME -> CSV, layer `postalcode` | NH | Supplements WOF postal codes. Polygons are not used for reverse PIP (Pelias PIP only uses WOF). |
| 10 | Overture Places | csv-importer (custom) | DuckDB query on Overture GeoParquet, bbox + state filter -> CSV | same | Rich POIs with categories in `addendum`. Dedupe vs OSM is imperfect; expect some duplicates. |
| 11 | Your own data | csv-importer | Any CSV (source/lat/lon/name + optional address fields) | -- | Hook for future authoritative/proprietary layers. |

Custom sources (8-11) get their own `source` names (`gnis`, `zcta`, `overture`, ...), so
the API's `sources=` / `layers=` filters work on them.

## 4. VM sizing (Proxmox)

| Resource | Maine only | Maine + NH | Rationale |
|----------|-----------|-----------|-----------|
| vCPU | 4 | 6 | Imports parallelize; steady-state load is light |
| RAM | 16 GB | 16-24 GB | ES heap 4-6 GB, libpostal ~2-3 GB, pip/placeholder, OS. Pelias minimum is 8 GB |
| Disk | 120 GB | 160 GB | Raw downloads (WOF, TIGER, OA, Overture) + ES index; raw data can be pruned after import |
| Type | VM (not LXC) | | Elasticsearch + Docker inside unprivileged LXC needs workarounds (memlock, nesting) |

Disk: SSD/NVMe-backed storage, virtio-scsi, `discard=on`. Set `vm.max_map_count=262144`
for Elasticsearch.

## 5. Phases

| Phase | Work | Output | Verification |
|-------|------|--------|-------------|
| 0 | Decisions (section 7), OA account | Confirmed profile | -- |
| 1 | Provision VM (cloud-init Ubuntu 24.04 template), SSH keys, UFW + Proxmox firewall, Docker Engine + compose plugin, non-root `pelias` user | Reachable hardened VM | `docker run hello-world` as non-root |
| 2 | Project repo: copy Texas files, rewrite `pelias.json`, `.env`, compose (bind all ports to 127.0.0.1, pin image tags instead of `:master`), blacklist, synonyms | `projects/maine/` in this repo | `pelias compose pull`, config lint |
| 3 | Custom-data prep scripts (Python + DuckDB): GNIS, ZCTA, Overture -> Pelias CSV | `scripts/prep_*.py`, reproducible | Row counts, bbox sanity checks |
| 4 | Build: `elastic start/create`, `download all`, `prepare all`, `import all`, CSV imports, `compose up` | Running geocoder | `pelias elastic stats` per source/layer |
| 5 | Tests: fuzzy-tester cases for Maine (addresses, towns, unorganized territories, lakes, venues, reverse) | `test_cases/*.json` | `pelias test run` pass rate |
| 6 | Reverse proxy: Caddy with TLS, rate limit, CORS allowlist, security headers, only `/v1/*` exposed | Public/LAN endpoint | External scan shows only 443 (and 80->443) |
| 7 | Demo page (below) | `web/` static site | Manual + Playwright smoke test |
| 8 | Ops: rebuild/update script (monthly OSM/OA refresh into a new index, alias swap), backups (ES snapshot or rebuild-from-scratch), log rotation, monitoring | `scripts/rebuild.sh`, runbook | Dry-run refresh |
| 9 | (Optional) Add NH | Updated config | Re-run Phase 4-5 |
| 10 | PostGIS-native geocoder, tuned against Pelias as the reference (section 10) | `pgeo/` service with a Pelias-compatible API | Shared test corpus + differential harness score |

Build-time estimate for Maine: roughly 1-3 hours end-to-end, dominated by downloads,
Valhalla polyline prep and OA import.

## 6. Demo page (MapLibre GL JS)

Single static page served by Caddy from the same origin as the API (no CORS needed).

- Autocomplete search box (`/v1/autocomplete`), debounced, with `focus.point` = map center
- Toggles: restrict to viewport (`boundary.rect`), layers (address/venue/street/locality/
  postalcode...), sources (OSM/OA/WOF/GNIS/Overture/custom)
- Structured search form (`/v1/search/structured`: address, locality, postalcode, region)
- Click-on-map reverse geocode (`/v1/reverse`) with layer selection and radius
- Result panel: label, layer, source, confidence, match_type, accuracy
- Small batch demo: paste/upload a CSV, geocode sequentially with client-side throttling
  (Pelias has no native batch endpoint), download results
- Basemap: see decision D4

Packaged as a small reusable JS module (`pelias-search.js`) so it can be dropped into
other pages later (your requirement #5).

## 7. Decisions needed

| ID | Decision | Options | Recommendation |
|----|----------|---------|----------------|
| D1 | Profile | Production vs scratch/learning | Production (it will be exposed and maintained) |
| D2 | Scope | Maine only, or Maine + NH from the start | Build config with NH as a toggle; first build Maine only |
| D3 | Exposure | LAN only, VPN (Tailscale/WireGuard), or public internet with domain + TLS | Decide; drives Phase 6 |
| D4 | Basemap for demo | (a) OpenFreeMap hosted tiles (free, no key); (b) self-hosted Protomaps PMTiles extract of ME/NH (fully self-hosted, ~100-300 MB); (c) MapTiler/Stadia (API key) | (b) matches the self-hosted goal; (a) is fastest |
| D5 | Provisioning method | (a) Manual Proxmox UI + bash scripts; (b) cloud-init template + Ansible; (c) Terraform (bpg/proxmox) + Ansible | (a) or (b) for a single VM; (c) only if you want infra-as-code across more VMs |
| D6 | GeoNames and Transit | Include / skip | Skip GeoNames (redundant); include Transit if you care about bus/rail stops |
| D7 | Secrets | Where the OA token (and any TLS/DNS API token) lives | `.env` with 0600 perms on the VM, sourced from your secret manager |

### Decided (2026-09-18)

- D1 Production. D2 Maine only first (NH later). D3 LAN only for the Proxmox build;
  a later move to the remote VPS (public internet) is likely -- see section 11.
- D4 Self-hosted Protomaps PMTiles extract. D5 cloud-init template + Ansible.
- VM on primary VLAN, disk on the best SSD pool (2 TB or 4 TB SSD).
- D7 Secrets in `.env` (0600, gitignored).
- Data is downloaded and pre-processed on the workstation, then shipped to the VM.

## 8. Security baseline (production profile)

- Only Caddy listens externally; ES, libpostal, pip, placeholder, interpolation, api bound
  to 127.0.0.1 (Texas compose binds api to 0.0.0.0 -- change it)
- Containers run as non-root `DOCKER_USER`; VM has no password SSH, UFW default-deny
- Pin container image tags/digests (Texas uses `:master`) for reproducible, reviewable updates
- Rate limiting and request size limits at the proxy; CORS allowlist, not `*`
- OA token not committed; `.env` gitignored
- Elasticsearch has no auth in this image -- must never be reachable off-host
- Demo page: all API results rendered via `textContent`, never HTML injection; CSP header

## 9. Risks

- OpenAddresses token/throttling -- mitigate with own account
- WOF download size: whosonfirst importer may pull a US-wide SQLite even with `importPlace`
- `:master` images can change under you -- pin versions
- Overture/OSM venue duplicates -- tune or accept; possible dedupe step later
- Pelias polylines prepare with multiple PBFs -- merge first

## 10. Phase 10 -- PostGIS-native geocoder (Pelias as reference)

### Objective

Reproduce the Pelias capabilities and a Pelias-compatible `/v1/*` API entirely in
PostgreSQL/PostGIS plus extensions, with user-facing pieces (widget, loaders, admin) outside
the database. Pelias is the oracle: we build a test corpus, measure both systems against
it, and iterate on the Postgres side until it matches or beats Pelias on the Maine set.

### Principles

- **Same inputs:** both systems load the same prepared data (`data/processed/*`), so
  differences come from the engine, not the data.
- **Same interface:** the Postgres service returns Pelias-shaped GeoJSON (`features[]`,
  `properties.{label,layer,source,confidence,match_type,accuracy,...}`) at the same paths,
  so the demo page, JS widget and fuzzy-tester run unchanged against either backend.
- **Measure, then tune:** every ranking change is judged by the harness score, not by eye.

### Target stack

| Concern | Component | Notes |
|---------|-----------|-------|
| Database | PostgreSQL 18 + PostGIS 3.6 | Same VM, or a second VM for isolation. Verify PG18 builds exist for every extension (pg_search, pgsql-postal, h3 if used) before committing |
| Text search | `pg_trgm`, core FTS (`tsvector`, prefix `:*`), `unaccent`, `fuzzystrmatch` | Baseline, permissive licenses |
| Text search (candidate) | ParadeDB `pg_search` (BM25, Tantivy) | AGPL-3.0; evaluated as an A/B arm, adopted only if the harness says it is worth it |
| Parsing | libpostal: `pgsql-postal` in-DB, or the existing `libpostal-service` container called from the API layer | ~2 GB per loaded backend in-DB; decide by benchmark |
| Admin hierarchy / PIP | WOF (+ Census) polygons, hierarchy precomputed at load | Replaces pip + placeholder |
| Interpolation | TIGER edges/ranges (`postgis_tiger_geocoder` or own `ST_LineInterpolatePoint`) | Replaces interpolation service |
| Loaders | `osm2pgsql` flex (Lua), `ogr2ogr`, DuckDB `postgres` ext, TIGER loader | Scripted, idempotent, load-to-staging then swap |
| API | PostgREST RPC (or pg_featureserv), Caddy rewrites `/v1/*` -> RPC | Thin FastAPI shim only if response shaping can't be done in SQL |
| Frontend | Same demo page + reusable widget, backend switchable (Pelias / PostGIS / side-by-side) | Side-by-side view is a tuning tool |

### Test corpus (built once, used by both systems)

Stored in `tests/corpus/` as versioned JSON/CSV; each case has input, expected result
(coords + tolerance and/or expected id/layer/source/label parts), and tags.

| Category | Source of cases | Approx. count |
|----------|-----------------|---------------|
| Known addresses (exact) | Random sample of OA statewide + E911 points, held out by id | 2,000 |
| Degraded addresses | Same sample with typos, abbreviations (St/Street, Rte/Route), missing town/ZIP, wrong case, unit numbers | 2,000 |
| Towns, villages, unorganized territories, counties | WOF + GNIS populated places | 800 |
| Natural features (lakes, mountains, islands) | GNIS | 500 |
| Venues / POIs | OSM + Overture (well-known: hospitals, schools, airports, parks) | 500 |
| Postal codes | ZCTA | all ME ZIPs |
| Autocomplete prefixes | Prefixes (3, 5, 8 chars, word boundaries) of the above | 2,000 |
| Reverse geocode | Random points + known address points, per layer | 1,000 |
| Structured search | Split components of address sample | 500 |
| Filters / biasing | Same queries with `layers`, `sources`, `boundary.rect`, `focus.point` | 300 |
| Hand-curated Maine quirks | Maine-specific cases (e.g. "Saint" vs "St", plantations, townships like T4 R9 WELS, islands) | 100+ |

Ground truth comes from the source data itself (not from Pelias), so Pelias is also
scored rather than assumed correct. Where no ground truth exists, Pelias output is used
as a provisional expectation and flagged as such.

### Differential test harness (`tests/harness/`, Python + pytest)

- Runs the corpus against one or both backends (configurable base URLs), with concurrency
  limits and caching of Pelias responses to keep runs fast.
- Metrics per category and overall:
  - Top-1 / top-5 hit rate (id match, or distance within tolerance)
  - Distance error (median, p95) for address cases
  - Autocomplete: hit rate at rank k after n keystrokes, mean keystrokes-to-hit
  - Reverse: correct layer + nearest feature rate
  - Confidence calibration: confidence vs actual correctness (reliability curve)
  - Latency p50/p95/p99 per endpoint
- Output: JSON results + HTML/Markdown report with a Pelias-vs-PostGIS diff list
  (cases where one wins), which drives the next tuning iteration.
- Regression gate: a PostGIS change is kept only if the overall score does not drop and no
  category drops beyond a set threshold.

### Iteration order (easiest to hardest)

| Step | Capability | Exit criterion |
|------|-----------|----------------|
| 10.1 | Schema, loaders, admin hierarchy precompute, API skeleton (`/v1/place`) | Row counts match Pelias `elastic stats` per source/layer within tolerance |
| 10.2 | Reverse geocoding (+ layers, radius) | >= Pelias on reverse corpus |
| 10.3 | Filters and biasing (`sources`, `layers`, `boundary.*`, `focus.point`, categories) | Filter tests 100% |
| 10.4 | Structured search | >= Pelias top-1 |
| 10.5 | Batch endpoints (CSV in, CSV/GeoJSON out, set-based SQL) | Throughput benchmark; correctness = single-query results |
| 10.6 | Unstructured forward search (parser choice, synonyms, ranking) | >= Pelias top-1 on exact + degraded addresses |
| 10.7 | Autocomplete (prefix tables, candidate limits, ranking) | >= Pelias keystrokes-to-hit, p95 < 50 ms |
| 10.8 | Confidence scoring | Better calibration than Pelias |
| 10.9 | Loading/update tooling + admin UI (if needed) | One-command refresh with table swap |

### Decisions deferred to Phase 10 start

- Same VM vs separate VM for Postgres
- `pg_search` (AGPL) as an A/B arm: yes/no
- libpostal in-DB vs external service
- PostgREST vs pg_featureserv vs a thin FastAPI layer

## 11. Portability to the remote VPS

The deployment may move from the Proxmox VM (LAN) to our remote VPS (public). Design
for that now so the move is a config change, not a rebuild of the tooling.

### Design rules (apply from Phase 1)

- **Split provisioning from configuration.** Proxmox-specific steps (template, cloud-init,
  VM create) live in `infra/proxmox/`. Everything after "an Ubuntu 24.04 host with SSH"
  is Ansible roles (`infra/ansible/`) that target any host: Proxmox VM or VPS.
- **Inventory-driven exposure.** One variable (`exposure: lan | public`) switches Caddy
  between internal CA TLS on the LAN and Let's Encrypt with a real domain on the VPS,
  and turns on the public-only controls below.
- **Build once, ship the index.** Heavy work (downloads, prep, polylines, imports) runs
  on the workstation or the Proxmox VM. The VPS receives a finished Elasticsearch snapshot
  plus the service data dirs (WOF, placeholder, interpolation) via rsync, and only runs the
  query services. The VPS then needs no build resources and no OA token.
- **Docker vs host firewall.** Docker-published ports bypass UFW. All containers except
  Caddy bind to 127.0.0.1 (already planned); on the VPS this is critical, not optional.

### Extra controls when `exposure: public`

| Control | Implementation |
|---------|----------------|
| TLS | Caddy + Let's Encrypt, HSTS |
| Access | API keys per client checked at Caddy (or a small auth shim), demo page allowed by origin; or keep the API private and expose only via VPN |
| Abuse | Per-IP and per-key rate limits, request size/time limits, `size` param capped |
| CORS | Allowlist of our own origins only |
| Host | SSH key-only on non-default port or behind VPN, fail2ban/CrowdSec, unattended-upgrades |
| Monitoring | Uptime check, disk/heap alerts, log retention policy (query logs can contain addresses -- treat as internal data) |

### VPS sizing (query-only)

Pelias query stack for Maine: ~8 GB RAM minimum, 12-16 GB comfortable (ES heap 2-4 GB,
libpostal ~2-3 GB), 2-4 vCPU, ~40-60 GB SSD. The Phase 10 PostGIS stack should be
noticeably lighter, which is one of its practical payoffs on a VPS.

### Open questions

- VPS provider, current specs (RAM/CPU/disk), and OS
- Domain/subdomain for the public endpoint
- Public API open to anyone, keyed clients only, or VPN-only
