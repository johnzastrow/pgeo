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

## [0.21.0] - 2026-09-25

### Fixed
- The map could not be panned far enough to uncover the part of the region hidden behind the
  demo page's own panel. Two causes, both now addressed. `maxBounds` was the region's exact
  bounding box, so the viewport could never move past the state line; it is now the basemap's
  extent, which is wider (see below). And the panel floats over the map, so the usable area is
  not the whole canvas - the map now calls `setPadding()` with the panel's measured inset, which
  makes the opening fit, `flyTo` on a result and the "bias to map centre" reading all use the
  part the user can actually see. Recomputed on resize, and switched to a bottom inset at the
  720px breakpoint where the panel becomes a bottom sheet.
- `scripts/fetch_data.sh basemap` failed with a Go stack trace when `PROTOMAPS_BUILD` pointed at
  a build that no longer exists. build.protomaps.com keeps roughly a week of daily builds, so any
  pin older than that is a 404 - it now checks first and says what to re-pin. The pin is moved to
  20260925.

### Changed
- The basemap extract is taken 15% wider than the region on each side (`BASEMAP_MARGIN`), so
  there is something to pan into and a coastal or border town is shown in its surroundings rather
  than against a blank edge. For Maine that reaches into New Hampshire, Quebec, New Brunswick and
  further out into the Gulf of Maine. Only the picture is widened - the geocoder's own data is
  still clipped to the region, and the same constant is mirrored in `web/js/map.js` as the pan
  limit so the user can never reach untiled area. **Existing deployments need a re-fetched
  basemap**; the file is correspondingly larger.
- The serving bundle declares its engine. The page discovers what it is talking to through
  `<meta name="demo-engines">` and `/engines.json`; the bundle served neither, so the page fell
  back to its built-in default and labelled pgeo's own answers "Pelias / Elasticsearch" while
  hiding the Address tab, which only pgeo can serve. Nothing was ever reaching a Pelias instance -
  the default's `base` is this origin - but the deployment was misdescribed and a feature short.
  The edge now injects the tag and `deploy/web/engines.json` names pgeo. New guide,
  `docs/DEMO_ENGINES.md`, covers pointing the page at pgeo, at Pelias, or at both with a switch.
- The map attribution no longer claims "Geocoding: Pelias / pgeo". It named whichever engines the
  development stack happened to run and was wrong on any deployment serving one of them;
  attribution is owed to the data's publishers, not to the software that queried them, and which
  engine answered is already stated in the panel where it stays correct as the engine changes.
- Result markers are a pin for the answer and a dot for probe points, replacing the rotated
  "light flare" and detached ring left over from the chart-room styling, which read as a smudge
  at a distance and pulsed without saying anything. The pin's tip is the coordinate and it casts
  a shadow so it lifts off a pale ground; the probe dot is centred and hides nothing underneath.

## [0.20.0] - 2026-09-22

### Added
- Builds are parameterised by region. A *build* is one or more US states named in
  `regions/regions.json`, which `scripts/gen_regions.py` fills from the Census boundary file, the
  Who's on First distribution and the OpenAddresses source listing (50 states plus DC). Every
  step takes `--build`: `scripts/fetch_data.sh`, `pelias-prep`, `pgeo-load`, `scripts/pgeo_setup.sh`,
  `scripts/pgeo_rebuild.sh`, `scripts/pgeo_dump.sh`, `scripts/dev_web.sh` and the Ansible
  `pgeo_build` variable. A build other than the default gets its own stack, database and ports,
  so several run side by side on one workstation. Proven by building New York beside Maine.
- `GETTING_STARTED.md`: nothing to a geocoder answering on a public URL, mostly commands,
  written against the New York run.
- OpenAddresses and Who's on First are downloaded straight from their publishers
  (`results.openaddresses.io`, `data.geocode.earth`), so a pgeo build no longer needs the Pelias
  CLI for anything. The Pelias stack is still built the Pelias way.
- The demo page reads `/region.json` for its map extent, basemap, feature count and its own
  title, so one page serves any build. The edge role writes it; `scripts/gen_region_json.py`
  does the same locally.
- `tests/accuracy/build_cases.py --build` builds a case set for any region, with the
  "somewhere else" misses filtered against that region's own states and towns.

### Changed
- Data layout: per-build inputs in `data/raw/<build>/`, national ones in `data/raw/shared/`,
  outputs in `data/processed/<build>/csv/`. Existing Maine files were moved, not rebuilt.
- ZCTAs are assigned to states by Census land area (the ZCTA-to-county relationship file) rather
  than by ZIP prefix. Maine gains 03579, which has 846 km2 of land in Oxford County against 611
  in Coos County, New Hampshire. Deciding by geometry instead was tried and rejected: the
  cartographic state polygon generalises away Peaks Island, Cliff Island, the Cranberry Isles and
  five other coastal ZCTAs. Maine therefore holds 906,102 features where the report's measured
  build held 906,101; the report is left as it stands, because it describes what was measured.
- Postcodes in source data are validated against the ZIP prefixes a build has to itself, which
  reproduces Maine's old 039-049 range exactly and does not leak across a border - New York holds
  06390 on Fishers Island, so a plain prefix test there would have admitted Connecticut's 063xx.
- The demo page no longer asks for an API key on load, only when the edge answers 401: a
  deployment may sit behind a proxy that presents the key itself. `infra/wharf/pelias.caddy`
  shows that arrangement for the public host.
- README rewritten: what pgeo is, why to use it, when to use something else, and an index of the
  documentation. The procedure moved to `GETTING_STARTED.md`.
- The Pelias role in `infra/ansible/site.yml` is now conditional on `pelias_enabled`, so a
  pgeo-only host skips it instead of installing a stack it will not run.

### Fixed
- The Maine Overture and GNIS outputs are reproduced byte for byte by the parameterised
  converters; only the ZCTA set changes, by the one row above.

### Security
- `infra/ansible/group_vars/pelias/zz-local.yml` was tracked by git from 2026-09-21 to
  2026-09-22: the ignore rule named the file's previous name. It holds the TLS terminator's LAN
  address, the tailnet range and the hostname - no credentials. Untracked, the rule corrected, a
  test pins both. The history was then rewritten in full (`git filter-repo --replace-text`): every
  private address and hostname in every commit replaced with the placeholders HEAD already used,
  including the copies the 2026-09-21 sanitisation had left in earlier commits and three tokens it
  had missed at HEAD. Force-pushed; a fresh clone shows 149 commits, 38 tags, no private token.
  Commit hashes before 2026-09-22 changed.

### Fixed
- The pgeo locations answered a refused key with nginx's HTML 401 page and no
  `WWW-Authenticate`: `error_page` does not merge, and a location with its own `error_page 404`
  inherits none from the server. The 401 handler is now declared beside each 404 one, and a
  configuration test checks every guarded location resolves its 401 to the JSON handler. Found on
  VM 120 by the production run of the suite, which is what the suite is for.

### Deployed
- VM 120: the key check is on. Two clients issued, `demo` and `dispatch`; hashes in the
  inventory, keys handed over out of band. 38 checks pass against production; the browser smoke
  test passes through the key form; the access log shows client names and no keys.

## [0.19.0] - 2026-09-22

### Added
- **Phase 11: API keys at the edge.** Every `/v1/*` request needs a key in the `X-API-Key`
  header. nginx hashes it with SHA-256 through the njs module - a Debian package, no new process -
  and maps the hash to a client name from the inventory; no name, no API. Keys are 32 CSPRNG bytes
  made by `scripts/edge_apikey.sh`, shown once, never stored; the server holds hashes only. A key
  in the URL is refused, valid or not. The access log gains the client name and never the key.
  Fails closed: the play refuses to deploy with the check on and no keys. Static files, tiles and
  the page shell need no key. `docs/API_KEYS.md`.
- The demo page asks for a key, keeps it per tab in `sessionStorage`, sends it in the header, and
  asks again if the edge stops accepting it.
- `PGEO_API_KEY` is read by every harness that can reach an edge (accuracy runner, compatibility
  contract, browser smoke test, k6 via `API_KEY`); `scripts/dev_web.sh` enforces keys like
  production and issues a dev key. `tests/apikey.py` is the one place they read it.
- `tests/security/test_api_keys.py`: 37 checks against a live edge (no key, wrong key, malformed
  key, key in the query string, every endpoint with a good key, static assets open, the log) and
  against the configuration (every proxied location guarded before it proxies, hashes only, fails
  closed, the off switch named, the inventory has it on). The smoke test enters a wrong key, then
  the right one, and checks the key is nowhere in the page or `localStorage`.
- Report Section 3.13.2 describes the design; the 3.13 caution that said Phase 11 was not done is
  replaced, and the future-work row is closed except for single sign-on.

### Changed
- The demo page is light only.
- `tests/compat/compat_test.py` no longer reports a pass when Pelias itself failed most of the
  contract: three engines refusing every request identically used to read as "compatible".

### Fixed
- The dev edge now routes `/v1/attribution` for pgeo, as production does.

### Fixed
- `pgeo.feature_ac` no longer travels in the dump; the restore rebuilds it on the target from the
  restored `feature` table in that host's physical order. A dump and restore of two tables does
  not preserve their relative order - on VM 120, 177 rows landed elsewhere and 6 of 276
  keystrokes then answered differently on the fast and slow routes. Now consistent by
  construction on any host.
- `tests/accuracy/run_accuracy.py --rps N` paces requests, so the set can be run through an edge
  with a rate limit; without it VM 120 answered 1,469 cases with HTTP 429.

### Deployed
- VM 120: the four demo tabs (0.18.0) and pgeo 0.10.0. Accuracy over the public endpoint 95.8%,
  gate passed; one case differs from the workstation, a two-way tie at equal confidence that a
  fresh restore orders the other way.

## [0.18.0] - 2026-09-21

### Added
- Four demo tabs (`web/js/demo-tabs.js`), each showing something the study measured:
  - **Form filler.** The map follows the single best type-ahead candidate on every keystroke,
    with a callout that gains a confidence once the query is complete enough for full search to
    score it; Confirm fills a contact form (business, street, city, state, ZIP, lat, lon) from
    pgeo's `/v1/address`, and is disabled while the confidence is below 0.5. The point follows
    the top *autocomplete* result, not a search: full search on a half-typed address parses the
    wrong street and returns nothing.
  - **Confidence.** Every candidate with its score, from whichever engine is selected, and a
    note that says what the engine actually did. "Mud Pond" on pgeo: ten places tie, confidence
    divided to 0.36 each. On Pelias: eight at 1.00, no doubt expressed. The calibration finding
    of Section 3.1.1, live.
  - **Area.** Drag a rectangle or click a circle; results restricted to it, with what the
    statewide answer would have been. This is the `boundary.rect` / `boundary.circle` path, which
    nothing else on the page exercised.
  - **Nearby.** Click the map: the address at that point, then everything within the radius by
    distance, grouped by kind.
- `tests/web/smoke_demo.py` drives all four in light and dark, asserting outcomes (the filled
  form's fields, that an address does not fill the business field, the confidence note, a
  rectangle that restricts, an address "here"), under the production CSP with no console errors.
- Two deck slides with screenshots of the four tabs.

### Changed
- Switching engines now clears the demo tabs' results, as it already did the others'.

## [0.17.0] - 2026-09-21

### Added
- Report Section 3.16, "Optimization: a third more capacity, with no answer changed", reporting
  pgeo 0.10.0: the four query-path findings, how "no answer changed" was established (7,380
  golden queries byte-identical; 6,720 accuracy and fuzz cases unchanged case by case on both
  front ends), the three defects the verification caught that the accuracy percentage would have
  let through, and capacity before and after with a same-day control run.
- A deck slide on the optimization and its proof; the four-engine slide gains a "pgeo,
  optimized" column.
- `docs/PERFORMANCE_OPTIMIZATION.md` completed: verification, results, the rebuild check, and
  what is left.

### Changed
- Capacity: pgeo now holds 32 / 64 / 128 / 192 users on 1 / 2 / 4 / all vCPU (was 24 / 48 / 96 /
  128), on the same memory budgets. The executive summary, Sections 3.15 and 4, and the
  conclusions restate the comparisons: three times Pelias per CPU (was four), two times Photon
  (was 2.7), level with Nominatim (was 1.33). Sections 3.3-3.12 keep the as-first-tuned figures,
  labelled as such, so the tuning history stays readable.
- The Discussion's claim that moving the last search step into SQL "costs a third of the
  capacity" is withdrawn: that third was three ordinary inefficiencies, now removed.

## [0.16.0] - 2026-09-21

### Added
- The report now *concludes* from the Photon and Nominatim measurements rather than only
  reporting them. Sections 3.14 and 3.15 had the results, but the executive summary, discussion
  and conclusions did not mention either engine, so a reader of the summary would not have known
  two more engines were measured at all.
  - Executive summary: a paragraph giving the four-engine capacity ladder (Pelias 192, Photon
    128, Nominatim 64, pgeo 48 on two cores) and saying plainly that the accuracy gap against the
    OpenStreetMap engines is mostly data rather than engine. The opening sentence no longer says
    the study compared two geocoders.
  - Discussion: what the other two engines say about the SQL question - against Nominatim, which
    differs from pgeo only in that its search runs in Python rather than SQL, the cost is a third
    rather than four times, which locates the expense: most of the gap to Pelias is the inverted
    index, not the SQL. Plus why the accuracy column flatters pgeo against OSM-only engines.
  - Conclusions: two rows in the recommendations table (when Photon or Nominatim is the right
    tool), and a sentence putting the four-times figure in proportion.
- A publication version and date on the report's cover page, driven from `VERSION` and the clock
  so it cannot go stale, and passed to both the PDF and the Word output.

## [0.15.0] - 2026-09-21

### Added
- Report Section 3.15, the Nominatim comparison, closing the OpenStreetMap side of the study.
  Nominatim is pgeo's closest relative in the survey - both put the work in PostgreSQL - so it is
  the fairest architectural comparison available.
  - Capacity on two cores: Nominatim 64 users, pgeo 48. **pgeo is within 1.33x of the reference
    OpenStreetMap geocoder**, the narrowest gap in the report, against 4x for Pelias and 2.7x for
    Photon. Throughput saturates near 75 req/s; memory 2.19 GB resident.
  - Accuracy 61.7% against pgeo's 95.8%, and **8.0% at autocomplete with 75.3% of prefixes
    returning nothing** - Nominatim has no autocomplete endpoint, which is the clearest evidence
    in the report for why Photon exists.
  - A failure mode worth naming: Nominatim's median error on addresses is 0 m. It is exact when
    it answers and returns nothing for 38.9% of all queries, so its errors are recall, not
    precision. Its 94.7% on deliberate misses - the joint best - is earned by the same silence.
  - Section 3.15.4 sets all four engines side by side and says plainly that data sources explain
    more of the accuracy column than engine quality does.
- `tests/load/k6/session_nominatim.js`, `tests/load/run_nominatim.py`, and
  `tests/accuracy/run_accuracy_nominatim.py`. Nominatim has a real structured endpoint (unlike
  Photon) so it gets one; its `layers=address` equivalent is `zoom=18`; and its house number
  lives at `properties.address.house_number`, which the adapter lifts to where the scorer reads.
- `tests/load/run_alt.py` holds the ramp shared by the two non-Pelias-API engines, so
  `run_photon.py` and `run_nominatim.py` are thin entry points rather than copies.
- `tests/accuracy/test_adapters.py` (11 tests) pins the translations that silently decide
  results. A bad mapping does not raise - it reads as the engine being inaccurate.
- The wordmark is now on the report's title page, the deck's cover, the demo page header and as
  its favicon, and in the README. `build_report.py` regenerates the title-page PDF from
  `docs/branding/pgeo-clean.svg` whenever the master is newer, so the two cannot drift.
- `docs/branding/pgeo-icon.svg` and `pgeo-icon-g.svg`: square icons derived from the wordmark.

### Changed
- The deck's Photon slide is now a four-engine comparison, with a second slide on why the
  accuracy column is not the whole story.
- Section 1.6 no longer only asserts the architectural difference against Nominatim; it gives the
  measured cost of moving the last of the search into SQL.
- `docs/PHOTON_COMPARISON.md` is now `docs/OSM_ENGINES.md`, covering both engines, since a Photon
  index is built from a Nominatim database and the two share their setup.

### Fixed
- The title-page wordmark landed on the page before the title: both `\maketitle` and
  `\@maketitle` begin with a `\newpage`, so anything prepended to either misses the title block.

## [0.14.0] - 2026-09-21

### Added
- Report Section 3.14, the Photon comparison - the open question Section 1.5.1 flagged as this
  study's biggest exposure, now measured rather than caveated. Same corpus, session mix, ramp,
  targets and accuracy cases as the other two engines.
  - Capacity on two cores: Photon 128 users, against Pelias 192 and pgeo 48. Photon carries 2.7x
    pgeo but is *less* CPU-efficient than Pelias, so "four times fewer users than Pelias" remains
    the binding figure for pgeo.
  - Autocomplete p95 at 48 users: Photon 53 ms, Pelias 21 ms, pgeo 103-189 ms.
  - Memory: 1.84 GB resident under full load, the lightest engine measured.
  - Accuracy: 73.7% overall, and 19.1% on venues against pgeo's 94.2%. A Photon index is exported
    from Nominatim, which imports OpenStreetMap only, so the OpenAddresses E911 points and the
    Overture places both other engines carry cannot be in it. Photon also returns no confidence
    score, so it cannot mark an answer it does not believe.
- `tests/accuracy/run_accuracy_photon.py`: the 1,560-case set against Photon, scored by
  `run_accuracy.score` so the numbers are comparable. Translates the request only; Photon already
  answers in GeoJSON with `properties.housenumber`. Maps `layers=address` to Photon's
  `layer=house`, without which reverse cases fail a housenumber check they were never asked to
  satisfy.
- `docs/OSM_ENGINES.md` (renamed from PHOTON_COMPARISON.md in 0.15.0): how to build the Nominatim database and Photon index, run both
  measurements, and the four ways the comparison is not like for like.

### Changed
- Section 1.5.1 no longer says Photon "was not tested"; its caution callout now states what the
  measurement changed and what it did not. The Section 6 future-work item is marked done.

## [0.13.0] - 2026-09-21

### Added
- Report Section 2.1.1, "What pgeo is made of": an itemised bill of materials separating the
  run-time components (seven, three of which ship inside PostgreSQL) from the build-time tools,
  the optional FastAPI front end, and the demo/test software a running geocoder does not need.
  Each entry carries a version, a licence and a role, and the section states what pgeo does
  *not* need - no search engine, JVM, Node runtime, message queue or cache tier - and which SQL
  file covers the work each absent Pelias service would have done.
- `report/tests/test_bom.py` (24 tests): every line count, pinned dependency version and vendored
  web library version in that section is checked against the repository, so the inventory cannot
  drift. A new SQL file that is not listed fails the suite.

### Fixed
- Bibliography URLs ran up to two inches into the margin: they were plain text, which LaTeX
  cannot break. The renderer now emits them as autolinks and the header loads `xurl`. Overfull
  boxes in the PDF: 13 -> 7.
- The report read "runs on PostgreSQL PostgreSQL 18.6 (Debian 18.6-1.pgdg13+2)" - the value
  carries the full server string. Added `pg_version_short` for prose and table cells that
  already say "PostgreSQL".
- `session_photon.js` declared no `http_req_failed{phase:steady}` or `http_reqs{phase:steady}`
  threshold. k6 only emits a sub-metric some threshold names, so the Photon run reported
  throughput as 0.0 and - more seriously - would have reported a clean error rate however the
  engine behaved. `run_matrix.evaluate()` now records which sub-metrics were absent and refuses
  to score a pass without them.

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
