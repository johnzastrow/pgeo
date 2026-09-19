# Pelias Maine (+NH) Geocoder -- Project Plan

Status: Active -- Phases 1-7 done (2026-09-18); see docs/PROJECT_LOG.md

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

### Environment facts (probed 2026-09-18, read-only)

| Item | Value |
|------|-------|
| Proxmox | node `prox82`, 192.0.2.10, PVE 9.2.11, 12 threads, ~46 GB RAM |
| Host RAM headroom | ~9 GB available with current VMs running -- the binding constraint |
| Storage choice | `nvme2tb` (Samsung 980 PRO NVMe, dir storage, ~1.0 TB free) -- preferred over `ssd4tb` (SATA 870 EVO, 72% used, ~0.9 TB free) |
| Network | `vmbr0` (not VLAN-aware) = primary LAN 192.0.2.0/24; VM 120 on DHCP, reserved as 192.0.2.20 = `geocoder.lan.example` (MAC BC:24:11:00:00:00) |
| TLS / ingress | Existing Caddy on `wharf` (VM 102; LAN 192.0.2.254, tailnet 100.64.0.2). `*.example.org` resolves to the tailnet IP, so "internal" = LAN + tailnet |
| Cloud images on host | `debian-13-genericcloud-amd64.qcow2` already present (no Ubuntu image yet) |

Ingress path: client -> HTTPS -> wharf Caddy (TLS) -> HTTP over LAN -> pelias VM
Caddy (path allowlist, rate limit, demo static files) -> api:4000 on 127.0.0.1. The VM's
Caddy runs on the host network (not a Docker-published port) so UFW can restrict its
port to wharf (192.0.2.254) only. On the VPS the same VM-side Caddy terminates TLS itself.

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
| 11 | API authorization: only authorized users/clients may call the API (section 12) | Auth at the edge for browsers and machine clients | Unauthorized requests rejected; keys revocable; audit log |
| 13 | Postgres as the API endpoint: the whole service inside PostgreSQL, no application server (section 13) | `pgeo/sql/050_api.sql` + in-database HTTP (Omnigres) and/or PostgREST gateway | Same accuracy and load harness vs Pelias and pgeo+FastAPI |
| 12 | Capacity testing: minimum resources for 3 concurrent users, ramp to limits, repeat for Phase 10 (docs/LOAD_TEST_PLAN.md) | `tests/load/`, `docs/LOAD_TEST_RESULTS.md` | Pelias vs PostGIS comparison at equal resources |

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

**Built 2026-09-18** ("Chart Room" design): see `web/` and README. Implementation notes:
MapLibre GL 6.10 (ES modules, same-origin module worker, so no CSP exception beyond
`worker-src 'self' blob:`), PMTiles 4.5, @protomaps/basemaps 5.7 with a chart-paper flavor,
self-hosted Fraunces / IBM Plex fonts and Noto glyph ranges; all vendored with pinned
integrity by `scripts/vendor_web.sh`. Batch is client-side (Pelias has no batch endpoint).

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
- VM: Debian 13 (existing genericcloud image on the host), 4 vCPU, **10 GB RAM**, 120 GB
  disk on `nvme2tb`, `vmbr0` DHCP (reserve on router later).
- Ingress: HTTPS at `geocoder.example.org` via the existing wharf Caddy (LAN/tailnet only).
- No OpenAddresses token yet: first build uses the importer's shared default token; swap
  in our own token via `.env` when available.
- Because 10 GB is tight for imports, the full Pelias build (download, prepare, import)
  runs on the workstation (31 GB RAM); the VM runs query services only and receives an
  Elasticsearch snapshot + service data dirs. This is the same path the VPS will use.

### Implementation notes (Step 1, 2026-09-18)

- **Edge on the VM is nginx, not Caddy.** Stock nginx has request rate limiting
  (`limit_req`), real-IP restore and method limits built in; stock Caddy needs a third-party
  rate-limit plugin (custom build). TLS stays on the existing wharf Caddy. For the VPS,
  add TLS to the same nginx (certbot) or front it with Caddy.
- **Secrets split:** `projects/pelias_maine/secrets.env` (OA token only, read by
  `scripts/render_config.sh`) is separate from `.env` (compose settings), because the pelias
  CLI rejects empty variables and should never see secrets.
- **Images pinned** to dated `master-YYYY-MM-DD-<sha>` tags (non-`-classic` variants, which
  match upstream `:master`).
- **GNIS** loaded entirely as layer `venue` with `category` = feature class (incl.
  `civil` townships/plantations), to avoid competing with WOF admin records; out-of-state
  primary points dropped. **Overture**: confidence >= 0.5, open/unknown status, clipped to the
  Maine polygon with a 300 m buffer that requires Maine evidence (region or 039-049 ZIP);
  non-Maine ZIPs on Maine points are dropped, not guessed.
- **WOF** downloads the full US SQLite (~5.2 GB) even with `importPlace`.

### Overture Maps evaluation (release 2026-08-19.0, Maine)

Access: anonymous GeoParquet on S3 (`s3://overturemaps-us-west-2/release/<R>/theme=...`) or
Azure, monthly releases, STAC at stac.overturemaps.org. Esri's ArcGIS Online "Parquet feature
layers" (beta June 2026, Overture early-access layers) serve the same data for viewing and
client-side query inside ArcGIS; they need an ArcGIS org account and are not a better
pipeline source than the GeoParquet itself, but are handy for visual QA if you work in ArcGIS.

| Theme / type | Maine volume | Overlap with what is loaded | Pelias use | Phase 10 (PostGIS) use |
|--------------|-------------|------------------------------|------------|------------------------|
| places/place | 76,582 kept (conf >= 0.5) | Complements OSM venues | **Loaded** (`overture` source) | Same |
| addresses/address | 772,684 (all USDOT NAD) | Same records as OA: every NAD-only row has an OA twin within 5 m differing only in spelling ("East Grand Avenue" vs "E Grand Ave"); 90% of pairs have identical coordinates, p95 offset 1 cm | **Do not load** (pure duplicates). Keep as a token-free fallback if OA downloads fail; re-evaluate per state for NH | Useful for GERS ids and spelled-out street names as aliases |
| divisions/division + division_area | 1,145 localities, 1,437 neighborhoods, 16 counties; 916 locality polygons | Parallel to WOF (1,591 localities + 532 localadmin) | Not usable for Pelias PIP (WOF only); adding as records would duplicate WOF | **Strong candidate** for the admin hierarchy (clean polygons, stable GERS ids) vs WOF |
| base/water (named) | 33k features, 9.6k distinct names | Mostly OSM-derived; GNIS covers lakes/streams by name | Low incremental value | Polygon extents for fit-to-bounds results |
| base/land (named) | 3.4k peaks, 2.3k islands/islets, beaches | OSM-derived; GNIS covers summits/islands | Low | Island/peak polygons |
| transportation/segment (road) | 500k segments, 218k named, 55k distinct names | OSM-derived; Pelias already has 65k OSM street docs | Redundant | Street geometry for reverse + own interpolation |
| buildings | not extracted | -- | No | Possible address-to-building snapping later |

### Data retention

All raw and intermediate data is kept on the workstation for later use (Phase 10 loads the
same inputs into PostGIS). Nothing is pruned there; only query-host deploys ship a subset.

| Location | Contents | Size (2026-09-18) |
|----------|----------|-------------------|
| `data/raw/` | OSM PBF, GNIS, ZCTA, state boundaries, Overture extracts, Protomaps basemap | ~0.5 GB (+ Overture themes) |
| `data/pelias/` | Pelias DATA_DIR: OA GeoJSON, WOF US SQLite (5.2 GB), TIGER, polylines, placeholder, interpolation DBs, ES data | ~6.4 GB |
| `data/pelias/es_snapshots/` | ES snapshots for deploys | ~0.4 GB each |
| `data/processed/` | Pelias CSVs + manifest (source hashes) | ~0.1 GB |

Recommended (not yet done): a periodic copy of `data/raw/` and the latest snapshot to the
`bigblock` PBS or `ObeliskNFS` storage, since upstream sources change monthly and old
releases are not always re-downloadable.

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

### Phase 10 decisions (2026-09-18)

- **Host:** develop and tune on the workstation in Docker (same data and load harness as
  Pelias, cgroup-constrained per config); deploy to a VM only once competitive.
- **Text search:** core and contrib only (`pg_trgm`, FTS, `unaccent`, `fuzzystrmatch`), per
  the user's constraint of 2026-09-18. ParadeDB `pg_search` (third-party, AGPL-3.0) is no
  longer an arm; at most an optional footnote comparison.
- **libpostal:** measured as a service and as the in-database `pgsql-postal` extension for
  comparison, but the **primary parse mode is the rule parser** (core-only constraint;
  measured only ~2 points behind libpostal while saving ~2 GB RAM).
  Early experiment: whether the ~2 GB model loads per backend or once via
  `shared_preload_libraries` (shared copy-on-write across backends); patch if needed.
- **API layer:** thin FastAPI service (asyncpg + PgBouncer) returning Pelias-shaped GeoJSON
  (proposed; user did not object), so parsing mode and query strategy are switchable per
  request for tuning.
- **Tuning is first-class:** Postgres settings are tuned per resource budget (as Pelias got
  a per-config heap); index, query-design, data-layout and pool experiments are recorded in
  a tuning log with before/after per endpoint and query type. Speed gains that reduce
  accuracy are rejected.
- **Start:** after the Pelias load-test results are written up and committed.

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

## 12. Phase 11 -- API authorization (roadmap)

Goal: only authorized users and clients can call `/v1/*`; the demo page keeps working for
signed-in users. Authorization is enforced at the edge (wharf Caddy and/or the VM's
nginx), so it applies equally to Pelias now and the PostGIS service later.

| Option | How | Fits | Trade-offs |
|--------|-----|------|-----------|
| A. API keys for machine clients | Per-client random keys (CSPRNG, 256-bit) sent as `Authorization: Bearer` or `X-API-Key`; edge compares against a list of SHA-256 hashes; per-key rate limits and logging | Scripts, batch jobs, other apps | Key distribution and rotation; keys must never ship in public web pages |
| B. SSO for browser users | Caddy `forward_auth` (or oauth2-proxy) against the existing identity service at `auth.example.org`; session cookie (Secure, HttpOnly, SameSite=Lax) | Demo page and human users | Depends on the IdP's availability; CSRF is moot for GET-only APIs but cookies still need the flags |
| C. Network-level only | Tailscale ACLs / LAN allowlist (today's state) | Small trusted group | Authenticates devices, not users; no per-user audit |
| D. mTLS client certificates | Caddy `client_auth` with an internal CA | Server-to-server | Certificate lifecycle overhead |

Recommended target: **B + A** (SSO for people, hashed API keys for machines), keeping C as
an outer layer while the service is LAN/tailnet-only, and required before any VPS
exposure. Requirements to settle when this phase starts: which IdP `auth.wharf` runs, user
and group model (who may use it), key issuance and revocation workflow, per-key quotas,
audit log retention (queries contain addresses: treat logs as internal data).

## 13. Phase 13 -- PostgreSQL as the API endpoint (roadmap, requested 2026-09-18)

**Constraint (2026-09-18): stay within PostgreSQL core + contrib extensions (plus PostGIS).**
Core Postgres has no HTTP server, so the primary design is a logic-free PostgREST gateway in
front of core SQL; the Omnigres arm below is optional and not pursued unless the core-only
design falls short. Parsing in this phase is the PL/pgSQL rule parser (libpostal is
third-party and stays an optional comparison).

Goal: the geocoder runs *entirely* in PostgreSQL 18. Parsing, candidate search, ranking,
confidence and the Pelias-shaped JSON response are all SQL; the HTTP layer either lives
inside Postgres or is a logic-free gateway. Compared against Pelias and against pgeo with
its FastAPI layer (Phase 10) on the same accuracy and load tests.

| Piece | Phase 10 (pgeo + FastAPI) | Phase 13 (pure Postgres) |
|-------|---------------------------|--------------------------|
| Parsing | libpostal service / extension / Python rule parser | `postal_parse()` in SQL (pgsql-postal, `shared_preload_libraries`) plus a PL/pgSQL port of the rule parser as fallback |
| Search, ranking, confidence | SQL functions (`geocode.*`) | same functions |
| Response | Python formats Pelias GeoJSON | `jsonb_build_object` in SQL (`geocode_api.v1_search(...) RETURNS jsonb`, etc.) |
| Parameter validation | Python | SQL (typed parameters, range checks, allowlists; errors as Pelias-style JSON) |
| HTTP | uvicorn/FastAPI | **Arm A:** Omnigres `omni_httpd` + `omni_web`, an HTTP server running inside Postgres (PG18 via `postgresql-18-omnigres`; images published for PG17). **Arm B:** PostgREST v16 as a stateless gateway exposing only the `geocode_api` functions; the edge rewrites `/v1/search` to `/rpc/v1_search` |

Steps:

1. `pgeo/sql/050_api.sql`: `geocode_api` schema with `v1_search`, `v1_autocomplete`,
   `v1_reverse`, `v1_search_structured`, `v1_place` returning complete Pelias JSON; SQL
   parser fallback; input validation. Unit tests in SQL (pgTAP or plain assertions) and a
   parity test against pgeo+FastAPI (identical results expected for the same parse mode).
2. Arm B (PostgREST): compose service, read-only role with EXECUTE on `geocode_api` only,
   edge rewrite rules; run accuracy + load.
3. Arm A (Omnigres): PG18 image with omni_httpd (Debian package), routing table for `/v1/*`,
   same role model; run accuracy + load. If PG18 packaging blocks, run on the PG17 image
   and record the version difference.
4. Compare three ways (Pelias, pgeo+FastAPI, pure Postgres A/B): accuracy, p95, ramp limit,
   memory, moving parts. Record in docs/PGEO_TUNING.md and the results reports.

Security notes: only `geocode_api` functions are reachable over HTTP; the HTTP role has no
table privileges beyond what those SECURITY INVOKER functions read via the read-only role;
request size and statement timeouts enforced in Postgres; rate limiting stays at the edge.
