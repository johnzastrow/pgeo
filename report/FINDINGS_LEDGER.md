# Findings Ledger for the Report

Every detail below must appear in the report (docs/REPORT.md / REPORT.pdf). The report
build does not read this file; it is the editorial checklist, reviewed against the finished
report before each release. Sources: docs/PROJECT_LOG.md (F1-F30, D1-D28), docs/PGEO_TUNING.md,
docs/TUNING_REPORT.md, docs/ENGINE_COMPARISON.md, docs/ADDRESS_API.md, docs/HTTP_OPTIONS.md,
and observations made during the work (marked "session").

## Data

- [ ] Pelias holds 1,421,745 address documents vs pgeo's 702,701: OpenAddresses and OSM carry
      most Maine addresses twice; Pelias indexes both and dedupes at query time, pgeo dedupes
      once at build time (house number + street + town, preferring OpenAddresses). (session)
- [ ] Pelias index: 1,649,644 documents, 435 MB; sources OA 780,260, OSM 768,992, Overture
      76,582, GNIS 20,104, WOF 3,280, ZCTA 426. (session)
- [ ] pgeo: 906,101 features; database 674 MB after dropping build-only tables (971 MB before). (session)
- [ ] Raw inputs: OSM PBF 87 MB, OA 223 MB, Overture places 203 MB, GNIS 3.6 MB, ZCTA 0.9 MB,
      WOF SQLite (US, 5.2 GB on disk, Maine subset used), interpolation DB 333 MB. (session)
- [ ] Data problems fixed in prep: GNIS out-of-state points; ZCTA pipe delimiter; Overture
      Portsmouth (NH) leak; interpolation needed OA as legacy CSV. (summary)
- [ ] Test-set errors: 8 of 150 "misses" exist in Maine (Death Valley in Minot x7, "The Eiffel
      Tower of Paris, Maine"). (session)
- [ ] Pelias and pgeo assign different gids to the same OpenAddresses record
      (us/me/statewide:<hash> vs <hash>): saved Pelias gids do not resolve in pgeo. (session)
- [ ] Pelias names counties "Cumberland County"; pgeo stored "Cumberland" (now output with " County"). (session)
- [ ] Category values are the sources' own (Overture "restaurant", OSM "natural=water"), not
      the Pelias taxonomy ("food"). (session)

## Accuracy and tuning

- [ ] pgeo 95.8% vs Pelias 75.5% (1,560 cases); typos 87% vs 30%; misses 95% vs 41%; venues
      exact 94% vs 68%; lakes/summits exact 95% vs 41%.
- [ ] Tuning steps and measured effect (report/data/tuning_steps.json), incl. "word_sim step
      also corrected the test set".
- [ ] Root causes: abbreviation shortening hurt typo matching (0.25 vs 0.81); ZIP outranked
      address (structured 0% -> 100%); word_similarity direction; town as a location
      ("Calvary Bible Church, Stratton" is in Eustis); candidate limits cut before focus
      ("main st" near Bangor); 25-of-150 Church Streets made an ambiguous address look unique;
      "Portlnd, ME" matched venues named "Portland, ME"; autocomplete "st" read as "saint".
- [ ] Calibration: right/wrong 0.94/0.90 -> 0.92/0.69 (Pelias 0.98/0.87); 70 of 85 wrong exact
      matches were ties.
- [ ] libpostal buys nothing measurable: rule parser 95.8% vs libpostal service 94.0% (F26).
- [ ] Pure SQL and FastAPI identical accuracy (F27).
- [ ] Fuzz: pgeo F0-F5 98/87/75/46/45/49 vs Pelias 82/34/10/4/3/10.
- [ ] Remaining failures (66): categories and reasons (TUNING_REPORT section 8).
- [ ] Gate reproduces lost tuning: five regressions against pre-town-aware results.

## Load and capacity

- [ ] Pelias limits: 96 (1 vCPU), 192 (2), 384 (4 vCPU, 4 workers), 192 (4 vCPU, 1 worker),
      M0 192; memory floor ~8.3 GB; placeholder OOM at 0.4-0.5 GB (C1@16, C2@12, C3/C4/C4a@128),
      pip OOM at 0.4 GB, interpolation crash-loop at 1.9 GB.
- [ ] pgeo limits: pure SQL 4/4/24/64/128, FastAPI 8/6/32/96/192 (Pmin/P1/P2/P4/PM); budgets
      1.6-3.3 GB; reverse is first over target everywhere; FastAPI beats PostgREST at 2-4 vCPU.
- [ ] Pelias 4-24x more users per CPU; pgeo meets 3 users in 1.6 GB (Pelias needs ~8.3 GB).
- [ ] Data volume (Pelias C3): D1 384, D2 384, D3 256, D4 256, D5 192; addresses nearly free,
      OSM and Overture cost a step each.
- [ ] Workers matter: C4a (1 API worker on 4 vCPU) 192 vs C4 384.
- [ ] Docker daemon stopped at 04:32 on 2026-09-19 (host event): api-svc-P4 cut at 32 users (passing).
- [ ] Dataset run failed first time: Elasticsearch not ready after the previous run's restore; fixed with a readiness wait.
- [ ] Caching: warm-up excluded; caches warm by design (LOAD_TEST_PLAN caching section).

## Architecture and design decisions

- [ ] G6: fewest components / data stays in Postgres unless the product suffers.
- [ ] HTTP options research: PostgREST chosen; Omnigres rejected (third-party code in the
      database); OpenResty as fallback; FastAPI kept as baseline.
- [ ] PostgREST keeps only the last segment of dotted params -> SQL arg names; boundary.circle
      collides with focus.point -> edge renames; unknown params -> 404 -> edge returns Pelias 400.
- [ ] Pelias-shaped errors from SQL via PostgREST response.status.
- [ ] Compatibility contract: 28 failures before, results after (compat.json).
- [ ] Address API: USPS Pub 28, 97.1% of 41,868 street names get a suffix; nearest address for
      venues; units only from the caller; ZIP+4 declined (licence terms and price researched).
- [ ] Privacy: bind parameters never logged (LANCER addresses are sensitive).
- [ ] Security posture: read-only API role, allowlisted tuning profiles, pinned images, CSP.

## Operations

- [ ] pgeo data directory 2.6 GB on disk vs 674 MB database: write-ahead log (max_wal_size 4GB)
      and space from replaced tables; size VM disk for ~3-4x the database. (session)
- [x] (report 3.10) Parity answer (user question 2026-09-19): feature parity for the documented API (27/27);
      pgeo adds /v1/address and better calibration; Pelias keeps /v1/nearby, multilingual names,
      international parsing, its category taxonomy, planet scale; gid and unknown-parameter
      differences. Accuracy: pgeo ahead everywhere except ZIP codes (100% vs 98%, one case).
      Speed after the reverse fix: Pelias ~4x users per CPU at every size. (session)

- [ ] Proxmox host prox82: AMD Ryzen 5 7600X (Zen 4, 6 cores / 12 threads, boost 5.4 GHz), 46 GB RAM;
      VM 120 uses cpu=host, no ballooning. Workstation (all load tests): Ryzen 5 3600 (Zen 2, 4.2 GHz).
      Expect the VM to outperform the workstation per vCPU; VM validation run gives the factor. (session)
- [ ] Reverse index fix effect: rest-Pmin 4 -> 24+ users (post-fix run in progress at time of note). (session)

- [ ] pgeo-tune profiles, accuracy gate, pgeo_rebuild.sh (verified end to end: 95.7%, gate passed).
- [ ] Semver per component; retroactive tags.
- [ ] Build times: Pelias ~15 min, pgeo ~13 min (single core).

## Required report sections (user requests, 2026-09-19)

- [ ] Scientific structure: abstract / executive summary; introduction (motivation, starting
      conditions); methods (what was built, architecture diagrams, why, data with sizes and
      sources, testing methods, final tuning procedures); results (figures and tables with
      interpretation tied to architecture and resources; the requests used for testing);
      conclusions (which tool for which scenario); next steps.
- [ ] Every figure and table numbered, with a caption; matplotlib, publication quality; sans-serif font.
- [~] (report 3.5, build numbers pending) Resource requirements: build (CPU, peak memory, disk, time) and operations (vCPU, memory,
      disk per load level) for each platform; which loads and which data volumes each supports.
- [ ] Features of each platform: endpoints, parameters, parsing, data layers, interpolation,
      autocomplete, reverse, confidence, extensions (/v1/address), compatibility, security,
      operations and tuning tooling.
- [ ] Web UI (demo page) features, including the engine switch and the Address tab.
- [ ] Report build pipeline: re-runnable (scripts/build_report.sh), Markdown and PDF.

## Headline conclusion requested (user, 2026-09-19)

- [ ] The smallest server that supports 3 concurrent users, per platform: found by the floor
      search (tests/load/find_floor.py: shrink CPU quota and each service's memory at 3 users)
      and confirmed on a temporary Proxmox VM of that size.
- [ ] On that minimum VM, how many users each platform scales to (full ramp on the VM).

- [x] (report Appendix A, Section 2.3) Rebuild runbook docs/REBUILD.md and scripts/rebuild_all.sh referenced (user request 2026-09-19).
