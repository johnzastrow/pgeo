# Changelog: pgeo

All notable changes to this project. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Versioning

The repository holds three independently versioned components:

| Component | Version source | Changelog | Git tag |
|-----------|----------------|-----------|---------|
| Project: Pelias deployment, infrastructure, demo page, test harnesses, docs | `VERSION` | this file | `vX.Y.Z` |
| pgeo: PostgreSQL/PostGIS geocoder (Python package + SQL) | `pgeo/pyproject.toml` | [pgeo/CHANGELOG.md](pgeo/CHANGELOG.md) | `pgeo-vX.Y.Z` |
| pelias-prep: data preparation tools | `prep/pyproject.toml` | [prep/CHANGELOG.md](prep/CHANGELOG.md) | `prep-vX.Y.Z` |

Rules: PATCH for fixes, dependency bumps and documentation; MINOR for new features and
backward-compatible changes; MAJOR for breaking changes (API behaviour, configuration or
data layout that existing deployments must change for). All components are pre-1.0: the
service is not yet in production use (API authorization, Phase 11, is still open). Every
functional change bumps the affected component and adds a dated entry here or in the
component changelog. Versions before 0.9.0 were assigned retroactively on 2026-09-18 to the
commits where each milestone was complete.

## [Unreleased]

## [0.12.2] - 2026-09-20

### Added
- pgeo serves `/v1/attribution` (pgeo 0.9.0), so the only Pelias endpoint it lacked is now the
  `/v1/` API description page. The compatibility contract grew a case for it - 28 of 28 pass on
  both front ends - and the contract runner can now check non-JSON responses.

## [0.12.1] - 2026-09-20

### Fixed
- The report's feature and parity tables missed two Pelias HTTP surfaces that pgeo does not
  implement: `/v1/attribution` (the data-licence page) and the `/v1/` API description page.
  Both are now listed, and Section 3.8 says plainly that this is the one place where "an
  existing Pelias client cannot tell the difference" does not hold. Implementing
  `/v1/attribution` is added to the next steps.
- Demo page attribution named only Pelias as the geocoder and omitted OpenStreetMap from the
  geocoding sources; it now names both engines and all sources.

## [0.12.0] - 2026-09-19

### Added
- **The study report** (`docs/REPORT.md`, `docs/REPORT.pdf`): the full comparison of the two
  engines as a paper - abstract, methods, results with 20 figures and 37 numbered tables,
  discussion, conclusions and next steps. Built from saved results by
  `scripts/build_report.sh`; every number comes from `report/inputs.toml`, so a rebuild cannot
  leave a stale figure in the prose.
- **Minimum server for 3 users** (`tests/load/find_floor.py`): shrinks one resource at a time
  until the three-user load misses a target. pgeo floors at 0.25 vCPU / 1.40 GB, Pelias at
  0.25 vCPU / 7.17 GB. `--resume` continues an earlier search after the step list grows.
- **Confirmation on real machines** (`tests/load/floor_vm.sh`): creates a temporary Proxmox VM
  of the measured size, deploys one engine, runs the validation and ramp, and destroys it.
  pgeo runs on 1 vCPU / 1 GB - the cheapest plan providers sell - holding 32 concurrent users.
- **Production validation** (`tests/load/run_vm.py`): both engines on VM 120 through the real
  HTTPS path. Pelias 384 concurrent users, pgeo 128; both meet the three-user target with the
  worst endpoint under a seventh of its budget.
- **Build resources** (`tests/build/measure_build.py`): Pelias 9 min / 11.5 GB peak / 2.9 cores;
  pgeo 13 min / 5.5 GB / 1.2 cores. pgeo is built in four times its own runtime floor, so the
  VPS that can run it cannot build it.
- `docs/REBUILD.md`: authoritative runbook to rebuild both platforms, the demo, verification,
  deployment and the report; `scripts/rebuild_all.sh` runs it in stages; `scripts/pgeo_setup.sh`
  scripts pgeo's first-time setup (secrets, images, containers, edge), previously manual.
  Referenced from the README and the report (Appendix A).
- Demo page: engine switch announced by the server (Pelias / pgeo), Address tab (type-ahead
  with a best-match suggestion, USPS block with copy, nearest-address link on the map, map
  click), Compare tab (the same search on both engines, timings, agreement), Search tab
  filters (circle around the map center, town or county via boundary.gid, categories).
  Browser tests pending.
- pgeo on the query host: Ansible role `pgeo_runtime` (official PostGIS + PostgREST, dump
  restore, secrets generated on the VM), edge routes `/pgeo/v1/*`, `scripts/pgeo_dump.sh`,
  runbook `docs/DEPLOY_PGEO.md`. Not yet deployed.

### Fixed
- pgeo on the query host would not start: `postgresql.conf` was written 0640 owned by the host
  account, and PostgreSQL in the container runs as its own uid, so it could not read the file.
  It is 0644 now (tuning settings only; credentials stay in `pgeo.secrets`, 0600). The failed
  first start left a half-initialized cluster - no database, and a `pg_hba.conf` without the
  entry the image appends on a completed init - which the role now detects and explains
  instead of failing later with "database does not exist".
- pgeo load runner mounts a per-run tuning directory (mounting a file inside the read-only
  base directory failed when the file did not exist).
- Report PDF readability: request strings in the compatibility tables now wrap instead of
  running into the margin (`report/pdf/breakcode.lua`), table cells hyphenate their first
  word, and column padding is narrower - 51 overfull boxes down to 4 below 3 pt.
- Report callouts are escaped for LaTeX: a percent sign silently truncated the first Key
  result box at "95.8", and dollar amounts were typeset as mathematics.
- Figure 20 (compatibility contract) draws separated cells; 27 x 3 passes previously rendered
  as one solid green block.
- Table captions stay with their tables in the PDF (Table 9's caption had been left at the foot
  of the previous page).
- Three faults in the temporary-VM tooling, each found by running it end to end for the first
  time: `create_vm.sh` extracted the guest IP with `grep -oE '[0-9.]+$'` on a line ending in a
  quote, so it never matched and every VM was torn down as "no IP reported"; `floor_vm.sh` wrote
  its inventory to a file with no `.yml` suffix, which Ansible would not parse, so the play
  matched no hosts, exited 0 and the script load-tested an empty machine; and it passed the edge
  allow-list as `key=value`, which Ansible reads as a string, so the template looped over its
  characters and wrote `allow [;` into the nginx config. The script now verifies that the host is
  in the inventory and that `pgeo_db` is running before generating any load.
- `find_floor.py` logs a trial whose stack never started; such a trial returned before the print
  and left a gap in the log (libpostal at 1.8 GB).

### Changed
- Documentation carries the post-index-fix measurements: `docs/TUNING_REPORT.md` section 5,
  `docs/ENGINE_COMPARISON.md`, and `docs/LOAD_TEST_RESULTS_PGEO.md` regenerated from the
  post-fix runs. The throughput ratio is four times per CPU under controlled conditions and
  three on VM 120's newer cores, not the pre-fix "4 to 24".

## [0.11.0] - 2026-09-19

### Added
- Compatibility contract test (`tests/compat/compat_test.py`): 27 documented Pelias requests
  against Pelias and both pgeo engines; 28 failures -> 0 with pgeo 0.7.0.
- pgeo edge: `boundary.circle.*` renamed for PostgREST; unsupported parameters return a
  Pelias-shaped 400.
- Report pipeline groundwork (`report/`): figures, diagrams (PNG + editable SVG), data snapshot.

### Changed
- ZIP+4 declined: the address API stays on open data (docs/ADDRESS_API.md).

## [0.10.1] - 2026-09-19

### Added
- Load-test results for both engines (`docs/LOAD_TEST_RESULTS.md`,
  `docs/LOAD_TEST_RESULTS_PGEO.md`, plots in `docs/loadtest*/`) and the head-to-head
  answer to the capacity questions (`docs/ENGINE_COMPARISON.md`): Pelias serves 4-24x more
  users per CPU; pgeo meets the 3-user target in 1.6 GB and is more accurate.
- Pelias data-volume results (C3 x D1-D5): 384 -> 192 users as OSM and Overture are added.

### Fixed
- `tests/load/report.py`: skips non-result JSON in run folders and labels pgeo configs.

## [0.10.0] - 2026-09-19

### Added
- Structured address API for LANCER (`docs/ADDRESS_API.md`, pgeo 0.6.0): USPS
  Publication 28 addresses from a selected feature, free text or a coordinate; non-address
  results resolve to the nearest street address. The `/v1/address` path is mapped in the
  pgeo edge and the dev server's engine prefixes.
- USPS ZIP+4 research (licence, price, what it would add) in `docs/ADDRESS_API.md`.

## [0.9.0] - 2026-09-18

### Added
- Tuning report (`docs/TUNING_REPORT.md`): every ranking, build and server tuning change,
  its measured effect, and how to carry it into later builds.
- `scripts/pgeo_rebuild.sh`: rebuild pgeo, apply a tuning profile, run known-answer checks
  and the accuracy gate in one command.
- Accuracy regression gate (`tests/accuracy/gate.py`) with a committed baseline
  (`tests/accuracy/baseline.json`, pgeo pure SQL at 95.8%).
- This changelog, `VERSION` and per-component changelogs; retroactive tags.

### Changed
- Requires pgeo 0.5.0 (tuning profiles, `pgeo-tune`).

### Fixed
- Pelias data-volume runs: the runner waits for Elasticsearch before building subset
  indexes (the 2026-09-18 run failed at start).

## [0.8.0] - 2026-09-18

### Added
- Demo page engine switch for side-by-side comparison (Pelias, pgeo pure SQL, pgeo
  FastAPI). Development server only: it injects a `demo-engines` meta tag; production
  serves the page unmodified and makes no extra request.
- Browser smoke test exercises every engine when the switch is present.

## [0.7.0] - 2026-09-18

### Added
- pgeo load-test runner (`tests/load/run_matrix_pgeo.py`): same k6 session, SLOs and ramp as
  the Pelias runner; engines `rest`, `api`, `api-svc`; configurations Pmin, P1, P2, P4, PM.

### Changed
- Pelias standard memory profile: placeholder 0.5 GB -> 0.8 GB after OOM kills at 128 users
  (configurations C3, C4, C4a).

## [0.6.1] - 2026-09-18

### Fixed
- Accuracy test set: non-unique lake, summit and venue names are qualified with the nearest
  town (a bare "Mud Pond" has no single right answer). Applies to both engines.

## [0.6.0] - 2026-09-18

### Added
- Phase 13: pure-SQL API served through PostgREST with unchanged Pelias URLs; local edge
  `scripts/dev/nginx.pgeo-rest.conf` maps `/v1/*` to `/rpc/v1_*` (GET only).
- Research: HTTP options for "Postgres as the endpoint" (`docs/HTTP_OPTIONS.md`); guiding
  principle G6 and the final comparison scorecard.

## [0.5.0] - 2026-09-18

### Added
- Fuzz accuracy rounds: 1,800 cases in six levels of increasing corruption (F0-F5).
- Phase 13 plan; pgeo tuning log (`docs/PGEO_TUNING.md`).

## [0.4.1] - 2026-09-18

### Added
- Plain-language testing guide (`docs/TESTING_GUIDE.md`).

## [0.4.0] - 2026-09-18

### Added
- Engine-independent accuracy harness: 1,560 cases with ground truth from the source data
  (addresses, towns, lakes and summits, venues, ZIPs, reverse, misses; exact, typo and
  variant query quality).

## [0.3.0] - 2026-09-18

### Added
- Capacity test harness: k6 session model, configuration matrix with CPU pinning and memory
  limits, ramp to the breaking point, Markdown/Mermaid/matplotlib report.
- Data-volume dimension (subset indexes D1-D5).
- Phase 11 plan: API authorization options.
- pgeo design document (Phase 10).

## [0.2.0] - 2026-09-18

### Added
- "Chart Room" demo page: MapLibre GL with a self-hosted Protomaps basemap, autocomplete
  with focus biasing, structured search, reverse geocoding by map click, CSV batch
  geocoder. Vendored libraries with sha512 pins; strict Content-Security-Policy.

## [0.1.0] - 2026-09-18

### Added
- Pelias for Maine built and deployed on Proxmox VM 120 (`geocoder.example.org`,
  LAN only, HTTPS via the wharf Caddy): Debian 13 template, Ansible roles (base, docker,
  pelias runtime, nginx edge), pinned docker-compose project.
- Data: OpenStreetMap, OpenAddresses, Who's On First, TIGER interpolation, USGS GNIS,
  Census ZCTA and Overture places, with a documented manual pipeline
  (`docs/DATA_PIPELINE.md`).
- Project plan, environment facts and decision log.

[Unreleased]: https://git.example.org/jcz/pelias_maine/compare/v0.11.0...HEAD
[0.11.0]: https://git.example.org/jcz/pelias_maine/compare/v0.10.1...v0.11.0
[0.10.1]: https://git.example.org/jcz/pelias_maine/compare/v0.10.0...v0.10.1
[0.10.0]: https://git.example.org/jcz/pelias_maine/compare/v0.9.0...v0.10.0
[0.9.0]: https://git.example.org/jcz/pelias_maine/compare/v0.8.0...v0.9.0
[0.8.0]: https://git.example.org/jcz/pelias_maine/compare/v0.7.0...v0.8.0
[0.7.0]: https://git.example.org/jcz/pelias_maine/compare/v0.6.1...v0.7.0
[0.6.1]: https://git.example.org/jcz/pelias_maine/compare/v0.6.0...v0.6.1
[0.6.0]: https://git.example.org/jcz/pelias_maine/compare/v0.5.0...v0.6.0
[0.5.0]: https://git.example.org/jcz/pelias_maine/compare/v0.4.1...v0.5.0
[0.4.1]: https://git.example.org/jcz/pelias_maine/compare/v0.4.0...v0.4.1
[0.4.0]: https://git.example.org/jcz/pelias_maine/compare/v0.3.0...v0.4.0
[0.3.0]: https://git.example.org/jcz/pelias_maine/compare/v0.2.0...v0.3.0
[0.2.0]: https://git.example.org/jcz/pelias_maine/compare/v0.1.0...v0.2.0
[0.1.0]: https://git.example.org/jcz/pelias_maine/releases/tag/v0.1.0
