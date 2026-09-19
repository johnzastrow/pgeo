# Project Log: Goals, Questions, Decisions, and Findings

A running record of how this project got to where it is: what we set out to do, the
questions asked along the way, what was decided and why, and what we learned. Newest
entries are appended to each section; dates are absolute. For the plan itself see
[PLAN.md](../PLAN.md); for reproducible commands see [DATA_PIPELINE.md](DATA_PIPELINE.md).

---

## 1. Goals

| # | Goal | Status (2026-09-18) |
|---|------|---------------------|
| G1 | Host our own feature-rich forward and reverse geocoder for Maine (New Hampshire later) | Live at https://geocoder.example.org (VM 120, LAN + tailnet) |
| G2 | Load every applicable Pelias data source for Maine | Done: OSM, OpenAddresses, Who's On First, polylines, TIGER interpolation, GNIS, Census ZCTA, Overture Places. GeoNames and transit deliberately skipped (D6) |
| G3 | Demo web page with a MapLibre map showing the capabilities | Live at https://geocoder.example.org/ |
| G4 | Longer term: reproduce the Pelias capabilities and API entirely in PostgreSQL/PostGIS + extensions, with Pelias as the reference and test oracle | Planned (Phase 10) |
| G6 | Guiding principle (2026-09-18): arrive at the stack with the **fewest additional components and the least data moved out of PostgreSQL**, but not at the cost of an abysmal, inferior or overly complex product | Applied as a scorecard in the final comparison (PLAN.md section 14) |
| G5 | Be able to move the service to our remote VPS | Designed in (build once, ship the snapshot; inventory-driven exposure) |

Requirements carried over from the background notes (`Geocoding Server.md`): single-field
autocomplete linked to a map; batch geocode and reverse geocode; many sources (OSM, OA, WOF,
GeoNames, polylines, CSV, GNIS, ZCTA, Overture); structured and unstructured search;
reusable JS element; viewport and region biasing; source/layer/category filtering;
confidence scores; easy loading and updates.

---

## 2. Timeline

### 2026-09-18

1. **Kickoff.** Asked for a plan to install Pelias on a Proxmox VM with all data sources
   limited to Maine, adapting the upstream `projects/texas` example. Read the background
   notes and the Texas project (pelias/docker commit `3dfa07d`). Drafted `PLAN.md`.
2. **Decisions round 1** (see D1-D5, D7): production, Maine first, LAN only, Protomaps,
   cloud-init + Ansible, primary VLAN, best SSD, `.env` for secrets, download and
   pre-process on the workstation.
3. **First downloads**: Geofabrik Maine PBF (MD5 verified), GNIS Maine, Census ZCTA 2025,
   Protomaps Maine extract (280 MB).
4. **Question: can PostGIS reproduce Pelias?** Answered with a component-by-component map
   (pg_trgm/FTS/pg_search for Elasticsearch, pgsql-postal for libpostal, PostGIS for PIP and
   interpolation, PostgREST/pg_featureserv for the API). Decided to keep building Pelias
   and use it as the reference for a PostGIS rebuild (Phase 10). Target PostgreSQL 18.
5. **VPS mentioned.** Added portability design: split Proxmox provisioning from
   host configuration; build once, ship the index; `exposure: lan|public` switch.
6. **Repo**: initial commit; pushed to Forgejo as a private repo, then renamed everything
   from `pelia_maine` to `pelias_maine` (local folder rename pending, see open items).
7. **Proxmox probed read-only**: node `prox82`, ~46 GB RAM with ~9 GB free (later ~14-17 GB
   after VMs were shut down), `nvme2tb` NVMe chosen, `vmbr0`, wharf VM on the same host.
8. **Decisions round 2** (D8-D11): 10 GB VM, Debian 13, `nvme2tb`,
   `geocoder.example.org` via wharf's Caddy.
9. **Step 1 (local build)**: prep package, pinned Pelias project, full build (~15 min),
   tests, snapshot. Several upstream issues found and fixed (section 5).
10. **Overture evaluated** across all themes, prompted by the Esri Parquet feature layer
    announcement and the Overture cloud-sources docs (section 5, F6-F7).
11. **Runbook written** (`docs/DATA_PIPELINE.md`), with every manual command verified to
    reproduce the scripted outputs byte for byte.
12. **Step 2 (deploy)**: template 9013 (Debian 13, checksum-verified) and VM 120
    `pelias-maine` created on `nvme2tb`; DHCP gave 192.0.2.20. Ansible hardened the host,
    installed Docker, shipped the data, restored snapshot `pelias-20260918-1715`
    (1,649,644 documents) and configured nginx. Verified: only 8080 (wharf only) and 22
    (LAN + tailnet) exposed; allowlisted paths work from wharf; blocked from elsewhere.
    Fixed a permissions flip-flop so a re-run reports zero changes.
13. **HTTPS live**: site block added to wharf's Caddyfile (user reloaded Caddy; sudo needs a
    password). Let's Encrypt DNS-01 certificate issued in ~2 minutes. Fixed an nginx bug
    that returned 403 to every real client (F15). Verified end to end: peer check, real
    client IP logging, spoofed `X-Forwarded-For` ignored, rate limit returns 429.
14. **Demo page (Phase 7)**: "Chart Room" design (nautical-chart styling). Reusable
    `PeliasClient` and `<pelias-search>` element; structured, reverse and batch tabs.
    All third-party assets vendored with pinned integrity (strict same-origin CSP). Local
    preview server with the production CSP; Playwright browser test (light/dark, desktop/
    mobile) passes locally and against the live site. The batch tool surfaced F16 and F17.
15. **Auth roadmap and capacity testing**: API authorization added to the roadmap (PLAN.md
    Phase 11: SSO for people + hashed API keys for machines, recommended). Load-test plan
    written (docs/LOAD_TEST_PLAN.md); k6 harness with typo/variant/miss query types and a
    cache-control section; accuracy suite extended with typos and off names (F18-F20); trial
    run found the memory floor (F21); full matrix run started.

---

## 3. Questions asked and answers

| Date | Question | Answer / outcome |
|------|----------|------------------|
| 2026-09-18 | Production or throwaway? | Production |
| 2026-09-18 | Maine only or Maine + NH? | Maine first; NH later as a config toggle |
| 2026-09-18 | Exposure? | LAN only for now (LAN + tailnet via wharf); VPS possible later |
| 2026-09-18 | Basemap? | Self-hosted Protomaps PMTiles |
| 2026-09-18 | Provisioning method? | cloud-init template + Ansible |
| 2026-09-18 | Network / storage? | Primary VLAN (`vmbr0`), best SSD (`nvme2tb`) |
| 2026-09-18 | Secrets? | `.env` files (later split: `secrets.env` for the OA token) |
| 2026-09-18 | Can Pelias be reproduced in Postgres/PostGIS? | Yes, feasible; relevance tuning is the hard part. Use Pelias as the oracle (Phase 10) |
| 2026-09-18 | Postgres version for Phase 10? | PostgreSQL 18 |
| 2026-09-18 | TLS? | Existing Caddy on wharf, Caddyfile on the VM, public wildcard DNS for ACME certs |
| 2026-09-18 | Proxmox host? | 192.0.2.10 |
| 2026-09-18 | VM IP? | DHCP, reserve on the router later |
| 2026-09-18 | OpenAddresses token? | None yet; the shared default token worked |
| 2026-09-18 | VM RAM given ~9 GB free? | 10 GB, no other changes (headroom later rose when VMs were shut down) |
| 2026-09-18 | Guest OS? | Debian 13 (image already on the host) |
| 2026-09-18 | Did we deploy to Proxmox or locally? | Step 1 was entirely local; Proxmox was only read until step 2 |
| 2026-09-18 | Do you still need an API key? | No: OA worked with the shared token; Proxmox reachable over SSH |
| 2026-09-18 | Keep raw data? | Yes, everything in `data/` is kept as the archive |
| 2026-09-18 | Is Overture address data worth using? | Not for Maine: it is the same records as OA (F7) |
| 2026-09-18 | Proceed with wharf Caddy for `geocoder.example.org`? | Yes; user reserves 192.0.2.20 |
| 2026-09-18 | Which name do clients use? | `geocoder.example.org` for everything |
| 2026-09-18 | VM LAN name? | 192.0.2.20 = `geocoder.lan.example` (resolves from workstation and wharf) |
| 2026-09-18 | Add authorization to the roadmap? | Yes: PLAN.md Phase 11 (options A-D, recommend SSO + API keys) |
| 2026-09-18 | Minimum resources for 3 concurrent users, and limits? | Measured by load tests (docs/LOAD_TEST_PLAN.md, LOAD_TEST_RESULTS.md) |
| 2026-09-18 | Include fuzzy, off names and complete misses? | Yes, in both the load corpus and the accuracy suite |
| 2026-09-18 | Is there caching that affects results? | Yes (ES query cache, OS page cache, JIT); controlled and documented; results are warm steady state |
| 2026-09-18 | Results format? | Tables + Mermaid charts in Markdown, plus matplotlib PNGs |
| 2026-09-18 | Is the load testing only Pelias? | Yes for now; the harness is engine-agnostic and reruns unchanged on Phase 10 |
| 2026-09-18 | When does Phase 10 start? | After the Pelias results are written up |
| 2026-09-18 | Phase 10 host / search engine? | Workstation first; core Postgres text search first, pg_search as A/B |
| 2026-09-18 | Tune Postgres/PostGIS? | Yes, first-class: per-budget settings, indexes, query design, pools, tuning log |
| 2026-09-18 | libpostal for Phase 10? | Try both: service and PG18 extension (plus a no-libpostal arm) |
| 2026-09-18 | Ask all remaining questions now so work can finish unattended | Answered; recorded as D25 |
| 2026-09-18 | Add progressively fuzzier accuracy rounds? | Yes: F0-F5 rounds on the same 300 base queries (tests/accuracy/build_fuzz_rounds.py) |
| 2026-09-18 | Add a phase where Postgres itself is the API endpoint? | Yes: Phase 13 (Omnigres omni_httpd in-database HTTP vs PostgREST gateway) |
| 2026-09-18 | Is Postgres parallelism tuned? | Between-query parallelism (processes per connection, pool/API workers vs cores) is the main lever; within-query parallel workers off by default for short queries, to be measured (tuning T1/T2) |
| 2026-09-18 | Stay within Postgres core + contrib? | For the HTTP part only (clarified): PostgREST instead of Omnigres (D28); keep everything already built |
| 2026-09-18 | What is the overall goal for the final stack? | Fewest extra components, least data outside Postgres, unless that makes the product clearly worse or overly complex (G6) |
| 2026-09-18 | SSH key for the VM? | `~/.ssh/id_ed25519.pub` (pasted, since `~/.ssh` reads are blocked by permission rules) |

---

## 4. Decisions

| ID | Decision | Why | Alternatives considered |
|----|----------|-----|-------------------------|
| D1 | Production profile | It will be exposed and maintained | Scratch/learning |
| D2 | Maine only first; NH as a toggle | Smaller, faster iteration; NH adds little complexity later | Both from the start |
| D3 | LAN only (via wharf); VPS later | Lower risk now; design keeps the move cheap | Public now; VPN-only |
| D4 | Self-hosted Protomaps extract | Fits the self-hosted goal; 280 MB | OpenFreeMap; keyed services |
| D5 | cloud-init template + Ansible | Right size for one or two hosts | Manual + bash; Terraform + Ansible |
| D6 | Skip GeoNames; no transit feeds yet | GeoNames imports all of the US and is redundant with WOF/OSM | Include both |
| D7 | Secrets in gitignored, mode-600 files | User choice; kept out of git and out of compose's view | Secret manager |
| D8 | VM: Debian 13, 4 vCPU, 10 GB RAM, 120 GB on `nvme2tb`, DHCP | Host RAM is the constraint; NVMe is the fastest pool | 12-16 GB RAM; Ubuntu 24.04; `ssd4tb` |
| D9 | Build on the workstation, ship an ES snapshot | 10 GB VM is tight for imports; same path serves the VPS | Build on the VM |
| D10 | TLS at wharf's Caddy; nginx on the VM | nginx has rate limiting, real-IP and method limits built in; stock Caddy needs a plugin | Caddy with a custom rate-limit build |
| D11 | Hostname `geocoder.example.org` | Matches the `git.wharf...` pattern | `geocode.wharf...` |
| D12 | Pin Pelias images to dated `master-YYYY-MM-DD-<sha>` tags | Reproducible, reviewable updates (upstream uses `:master`) | Floating `:master` |
| D13 | All container ports bound to 127.0.0.1 | Docker-published ports bypass UFW | Upstream `0.0.0.0` for the API |
| D14 | `secrets.env` separate from `.env` | pelias CLI rejects empty vars and should never see the token | Token in `.env` |
| D15 | GNIS loaded as `venue` with category = feature class | Avoid competing with WOF admin layers; revisit with the harness | GNIS populated places as `locality` |
| D16 | Overture Places: confidence >= 0.5, open/unknown only, polygon clip with evidence-gated 300 m buffer | Removes low-quality and cross-border records while keeping shoreline POIs | Plain bbox; no buffer |
| D17 | Keep all raw and intermediate data on the workstation | Archive for rebuilds and Phase 10; upstream "latest" URLs do not keep history | Prune after import |
| D18 | Do not load Overture addresses for Maine | Identical to OA (F7) | Load all; load NAD-only rows |
| D20 | Single canonical endpoint `https://geocoder.example.org` for all clients (API, demo page, batch jobs, harness) | One name, one TLS terminator, one rate-limit policy; `geocoder.lan.example:8080` stays wharf-only | Direct LAN HTTP access for trusted hosts |
| D21 | Demo design "Chart Room": chart-paper palette, magenta aids, graduated neatline, DMS readout; Fraunces + IBM Plex | Distinctive and on-theme for a Maine coast geocoder | Generic dashboard styling |
| D22 | Vendor all web assets (npm tarballs with pinned sha512, basemap assets at a pinned commit) and serve same-origin | Strict CSP, no third-party requests from clients, reproducible | CDN script tags with SRI |
| D23 | Demo batch runs client-side: 250 rows, 4 req/s, backoff on 429, CSV export with formula-injection guard | Pelias has no batch endpoint; stays under the edge rate limit | Server-side batch service (Phase 10) |
| D24 | Phase 10: workstation first; core text search + pg_search A/B; libpostal as service and as PG18 extension; FastAPI API layer; tuning log | See PLAN.md section 10 | Proxmox VM first; PostgREST |
| D25 | Unattended-session grants (2026-09-18): FastAPI API layer; load-test VM 120 including ramp to crash; commit + push at verified milestones; Phase 10 "finished" = parity (search, autocomplete, reverse, structured, place) + accuracy harness + tuning log + load comparison report; WOF admin with Overture divisions as A/B; demo engine switch (local only); resize VM 120 if clearly needed; official postgis PG18 image by digest + libpostal/pgsql-postal built from pinned commits + pg_search release for A/B | User answered all open questions up front so work can finish without them | Ask at each step |
| D26 | Phase 13: Postgres as the API endpoint, two arms (Omnigres in-database HTTP; PostgREST gateway) | User request: the service purely in Postgres | Keep FastAPI only |
| D27 | norm() expands abbreviations to full words | Typo matching: trigram overlap is much higher on full words (0.81 vs 0.25) | Abbreviate (first version) |
| D28 | Phase 13 HTTP layer uses no third-party Postgres extension: PostgREST gateway (core SQL behind it), not Omnigres omni_httpd. Scope clarified by the user: the constraint is for the HTTP part only; everything already built stays | User constraint 2026-09-18 (clarified) | Omnigres in-database HTTP |
| D19 | Phase 10 targets PostgreSQL 18 + PostGIS 3.6 | User choice | PostgreSQL 17 |

---

## 5. Findings and surprises

| ID | Finding | Impact / action |
|----|---------|-----------------|
| F1 | OpenAddresses now ships newline-delimited GeoJSON, but the Pelias interpolation builder only reads legacy CSV. It skipped all Maine OA addresses silently. | Added `pelias-prep oa-interp` + `OAPATH`; interpolation now conflates 721,600 OA addresses (was 0) |
| F2 | The WOF importer downloads the US-wide SQLite (~5.2 GB) even with `importPlace` | Disk sizing; expected |
| F3 | GNIS lists some Maine features with primary points out of state (Saint John River mouth in NB; "Atlantic Ocean" off NC) | Filtered; validation guards against recurrence |
| F4 | Census 2025 ZCTA Gazetteer switched from tab to pipe delimiter | Reader updated; runbook notes it |
| F5 | Overture Places buffer crossed the Piscataqua into Portsmouth, NH (540 places) | Buffer rows now need Maine region/ZIP evidence |
| F6 | Overture themes for Maine: divisions parallel WOF (1,145 localities, 916 polygons); base water/land and road segments are OSM-derived; places add value | Places loaded; divisions are a Phase 10 admin candidate |
| F7 | Overture addresses for Maine are USDOT NAD, and NAD and OA are the same Maine E911 points: all 24,915 apparent NAD-only rows have an OA twin within 5 m that differs only in spelling ("East Grand Avenue" vs "E Grand Ave"); 90% of pairs have identical coordinates, p95 offset 1 cm | Not loaded (D18); fallback source and Phase 10 alias material; re-check for NH |
| F8 | Ranking baseline: "Moosehead Lake" returns neighbourhood "Moosehead"; "Mount Desert Island" returns the town; the jetport is at rank 5 | Tracked as expected failures; first tuning targets |
| F9 | The pelias CLI rejects empty variables in `.env` | Led to D14 |
| F10 | Editing `build_local.sh` while it ran broke the remaining steps (bash reads scripts incrementally) | Noted in the runbook |
| F12 | On the 10 GB VM the query stack uses ~8.1 GB (ES 2.5 GB, interpolation 1.9 GB, libpostal 1.9 GB, pip 0.7 GB); ~1.8 GB available | Works, but little headroom; 12 GB would be comfortable if host RAM allows |
| F13 | My first document-count total (1.73 M) was an arithmetic slip; the real total is 1,649,644, confirmed on both hosts | Docs corrected |
| F14 | wharf: user `jcz` can edit `/etc/caddy/Caddyfile` (group-writable) but `sudo` needs a password. My append ran even though the `sudo cp` backup failed (a failing command inside an `&&` list does not trip `set -e`). Caddy was not reloaded, so nothing went live. Backup reconstructed and verified byte-exact (1,828 bytes) at `~/Caddyfile.pre-pelias-orig` on wharf | Lesson: in remote scripts, make the backup a hard gate (`cp ... || exit 1`) before any edit. The user reloads Caddy (password) |
| F15 | nginx `allow`/`deny` is evaluated after the realip module rewrites `$remote_addr` to the end client, so "allow wharf only" denied every real user (403 through Caddy) | Peer check moved to a `geo` on `$realip_remote_addr`; rate limits still key on the real client IP |
| F16 | Wrong-town exact match: "12 Park St, Bar Harbor, ME" returns 12 Park Street, **Fairfield** with confidence 1.0 (parse is correct: city = bar harbor). Bar Harbor has no #12, so Pelias drops the town (a boost, not a filter) and its confidence score does not penalize the mismatch. Found by the demo's batch tool | Added as an expected-failure test; a key Phase 10 target (locality as a hard constraint when present, confidence that accounts for admin mismatch) |
| F17 | Demo batch bug: unquoted addresses in a one-column CSV were split at commas, sending only "210 State St" (-> Bangor) | Fixed: a lone address column rejoins the whole line; browser test asserts the right towns |
| F18 | Pelias has **no typo tolerance** in `/v1/search` or `/v1/autocomplete`: "Portlnd, ME", "Moosehed Lake", "Katahdn" return nothing; "389 Congres St" falls back to the town | 6 expected-failure tests; strongest Phase 10 opportunity (pg_trgm similarity) |
| F19 | Off names: suffix spelled out, units, lowercase, "Mt" work; word order ("Lake Moosehead" -> Lake Arrowhead), partial venue names and "ZIP + town" fail | Expected-failure tests |
| F20 | Complete misses mostly behave (gibberish, out-of-state, offshore reverse -> no result; impossible house numbers -> the street at 0.8), but invented names sharing a common word get confident false positives ("Trumyux Brewing Co" -> Belleflower Brewing, confidence 1.0) | Confidence is not a reliable "did it really match" signal; Phase 10 target |
| F21 | Memory floor is set by fixed-size services: pip OOM-killed at 0.4 GB under reverse load, interpolation crash-looped at 1.9 GB; ES heap is the only big adjustable | Profiles raised; see LOAD_TEST_PLAN.md |
| F22 | Pelias API runs one Node.js worker by default (`CPUS` env enables more), so extra vCPUs do not help the API itself unless `CPUS` is set | Tested as C4 vs C4a |
| F23 | PostgREST keeps only the last segment of dotted query keys; naming the SQL API arguments after those segments makes Pelias URLs work through PostgREST with only a path rewrite | Phase 13 edge = path mapping only |
| F24 | Stricter address dedupe (number + street + town, preferring OA) reduced pgeo addresses from 754,490 to 702,701 | Removes OSM copies of OA points that sat >100 m away |
| F11 | The whole Maine build is small: ~15 min, 1,649,644 documents, 435 MB snapshot | A VPS deploy is cheap to ship |

---

## 6. Open items

| Item | Owner | Notes |
|------|-------|-------|
| Reserve 192.0.2.20 (MAC BC:24:11:00:00:00) for VM 120 on the router | User | LAN name `geocoder.lan.example` in place |
| Rename local folder `~/Forge/pelia_maine` -> `~/Forge/pelias_maine` | User, after the session | Then update `DATA_DIR` in `projects/pelias_maine/.env` |
| Get an OpenAddresses account and token | User | Avoids throttling on rebuilds |
| Back up `data/raw` + latest snapshot to `bigblock` or `ObeliskNFS` | Not started | |
| Phase 8 ops, Phase 9 NH, Phase 10 PostGIS | Planned | See PLAN.md |
