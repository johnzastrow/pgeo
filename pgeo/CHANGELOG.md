# Changelog: pgeo

PostgreSQL 18 / PostGIS geocoder with a Pelias-compatible API. Format:
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Version source:
`pgeo/pyproject.toml` (reported by both APIs as `geocoding.engine.version`). Tags:
`pgeo-vX.Y.Z`. Versions before 0.5.0 were assigned retroactively on 2026-09-18. Accuracy
figures are "correct" on the 1,560-case set (tests/accuracy); details in
docs/PGEO_TUNING.md.

## [Unreleased]

## [0.15.0] - 2026-09-23

**Requires a rebuild**, and `scripts/fetch_data.sh neighbours` before it (5.6 MB).

### Fixed
- The region clip no longer reaches across an international land border. The clip buffers the
  region by about 300 m, because an unbuffered boundary generalises the coast and drops piers,
  wharves and island shoreline. Seaward that is right. Across a land border it took in a strip of
  the other country, and a single-state build then labelled what it found with its own state:
  New York held `Akwesasne Canada Post` 77 m outside the state as `, NY, USA`, on a Mohawk
  territory the border runs through, and Maine held 2,255 features in Canada.

  Canada and Mexico are now subtracted from the buffer, which leaves the seaward part untouched
  because there is nothing out there to subtract. Measured on Maine: features outside the state
  fell from 2,492 to 235, all of them within 3.4 km and almost all OpenStreetMap streets, whose
  representative point is the centroid of a way that crosses the line.

  Nothing inside the state can be lost this way, and that was checked rather than assumed: the
  Who's on First polygons for Maine and Canada meet at the border with **zero** overlapping area.
  Maine's accuracy is unchanged at 96.0% with no case altered in either direction, and the
  islands the buffer exists for - Peaks, Cliff, the Cranberry Isles, Isle au Haut, Monhegan - all
  survive.

### Added
- `scripts/fetch_data.sh neighbours` fetches the two country polygons as single Who's on First
  records, 5.6 MB, rather than the `admin-ca` and `admin-mx` distributions, which are 176 MB and
  230 MB compressed for two polygons. It verifies each file's `iso:country` after downloading,
  because the path is built from an id and a wrong id returns a perfectly good polygon for
  somewhere else - 85633057 looks like Mexico's id and is Chile's.

  The step is optional. With no files on disk the clip keeps its full buffer and the build says
  so, so an existing checkout still builds without fetching anything new.

## [0.14.0] - 2026-09-23

**Requires a rebuild.** `admin` gains a column, so an existing database keeps the old behaviour
until `pgeo-load build` runs again.

### Fixed
- A county carries its own name. Both front ends appended the word " County" to every county
  name, on the strength of a comment reading "Pelias (WOF) names counties 'Cumberland County'" -
  true of Maine, and of most states. Louisiana's county equivalents are parishes, Alaska's are
  boroughs, municipalities and census areas, and the District of Columbia's is itself; Who's on
  First files all of them under placetype `county` with the bare name, so Louisiana would have
  answered `Acadia County`. The suffixed form lives in the `label:eng_x_preferred_longname`
  property, which is now carried through `admin.longname` into the feature, and neither front end
  appends anything. Found by writing up the next four regions rather than by building one.

- Every point source is clipped to the region, not only OpenStreetMap. The clip of 2026-09-22
  assumed the others could not stray - "GNIS from the state's own file, Overture clipped in prep"
  - and measurement said otherwise: a box around Arizona and Nevada admitted 297 GNIS features up
  to 66 km outside them, in Utah, New Mexico and Sonora, plus 240 OpenAddresses rows. In a
  single-state build the missing-county fallback then labelled every one with the build's own
  state, so New York answered `Ringwood River, NY, USA` for a river in New Jersey and held the
  Canadian side of the Akwesasne reserve. `region_clip` moved to `022_stage.sql`, where it is
  built whether or not OpenStreetMap is loaded, and `025_osm.sql` now reuses it.

  ZCTAs are deliberately exempt: a ZCTA belongs to the state holding most of its land area, not
  the state holding its internal point - Maine owns 03579 on 846 km2 against New Hampshire's 611 -
  so clipping them by point would undo the rule that assigns them.

### Added
- A build takes a session-level advisory lock and refuses to start when one is already running
  against the same database, naming the backend that holds it. Two builds at once drop and
  recreate each other's staging schema: one started twice on 2026-09-23 finished with 193 Vermont
  streets against New Hampshire's 40,300, no Main Street in Burlington, and still printed "build
  complete", because one process died with a traceback while the other carried on. The lock is
  released by PostgreSQL when the connection goes, however the process ends.

- `ogr2ogr` errors fail the build. It can abandon a layer and still exit zero - "Terminating
  translation prematurely after failed translation of layer lines" was the line that preceded
  Vermont losing its streets - so its own error output is now treated as failure. A partly loaded
  extract is worse than none, because it looks like a build.

## [0.13.0] - 2026-09-23

### Changed
- A misspelled town now reaches the town it means. `Albny, NY` parsed to nothing but the state,
  so the whole raw string went to name matching - where venues that carry their own town in their
  name (`Albany, NY - Albany.com`) beat the town itself. The parser now falls back to a typo
  search over the build's town list when no town is spelled the way the query spells it, and
  passes on the corrected spelling, because the search matches towns by trigram and a
  transposition shares almost no trigrams with its own word.

  The rule is one edit, or two when the two strings have the same letters - that second case
  being a transposition, `Tuscon` for `Tucson`, the commonest typo of all. It applies only to the
  whole remaining text, only at five characters or more, and only after an exact match has
  failed. Those limits are what stop it inventing towns: `walmart` is two edits from Balmat, New
  York, and `central park` ends in a word one edit from Parks. Written twice, in
  `geocode.town_fuzzy` and `pgeo.api.parse`, with a test that pins the two to the same answers.

- A county no longer outranks a city of the same name. `Albany, New York` returned Albany County
  and `york` returned York County, on nothing but importance - 1.000 against the city's 0.942,
  worth 0.003 of score. County and locality now share a deduplication bucket, so the two rows
  reading `Albany, NY, USA` collapse to one, and the county loses. Ranking between localities and
  localadmins is untouched.

### Measured

| | Before | After | Cases changed |
|---|---|---|---|
| Maine | 95.9% | **96.0%** | 2 improved, 0 regressed |
| New York | 94.9% | **95.5%** | 1 improved, 0 regressed |
| Arizona + Nevada | 92.4% | 91.9% | 0 improved, 1 regressed |
| Known-answer failures (all builds) | 4 | **0** | - |

The single Arizona regression is `Mesaa, Arizona`, a generated case whose ground truth is a venue
named "Mesa" inside the city of Mesa, scored against a 300 m radius. The query now answers with
the city, which is what someone typing it means; the case counts it wrong because the city centre
is further than 300 m from that venue. Recorded rather than argued away - the case set is the
measure, and one case it gets wrong does not entitle the code to ignore it.

## [0.12.1] - 2026-09-23

### Fixed
- A five-digit house number is no longer read as a postcode. `13023 E LIMA ST, PRESCOTT VALLEY`
  parsed as postcode `13023` with no house number, so the address lookup found nothing and the
  search fell back to the street - returning `East Lima Street` at confidence 1.0 while
  `13023 E LIMA ST` sat in the build. Both parsers now look for a postcode only past a leading
  house-number token. Western house numbers reach five digits far more often than New England's:
  on the Arizona-plus-Nevada accuracy set all sixteen cases beginning with a five-digit house
  number failed, and no case without one did. Fixed in `geocode.parse_rule` and
  `pgeo.api.parse`, which held the same assumption written twice.

  Arizona + Nevada: correct 84.8% -> 92.4%, addresses 77.1% -> 98.6%, with every other category
  unchanged case for case. Maine re-scored 95.9% on its 1,560 cases with zero changed outcomes
  in either direction, which is what a fix this narrow should look like.

## [0.12.0] - 2026-09-22

### Fixed
- The region a feature is in is now the feature's own, not a constant. `'Maine'`, `'ME'`,
  `', ME, USA'` in every label, and the Maine region gid were written into every row by the
  build; a New York build would have labelled Albany "Albany, ME, USA". The state comes from
  the feature's county, which is how Who's on First records it, so no extra point-in-polygon
  pass is needed; a feature with no county falls back to the build's own state when the build
  has one, and is left unset rather than guessed when it has several.
- County abbreviations and FIPS codes no longer cross state lines. `geocode.county_ref` holds
  Maine's counties, and county names repeat: New York has a Franklin and a Washington, which
  would have been given Maine's abbreviations and Maine's FIPS codes. The state must now match.
  For states other than Maine `county_a` and `county_fips` are null, which is true rather than
  wrong.
- `/v1/address` took `state_fips` from the literal `'23'` and defaulted `state` to `'ME'`.
  Both now come from the feature.
- The query parser stripped a trailing `me` or `maine` from the text. It now strips any state
  the build covers, written as the postal code or the full name - and does not strip a name
  that is also a town here, so "350 5th Ave, New York" keeps its locality while
  "Portland, Maine" still loses its state.
- `boundary.gid` treated the Maine region gid as "everywhere"; it now accepts any region the
  build covers.
- `/v1/attribution` said "serving the State of Maine" whatever the build held.

### Added
- `geocode.region_ref`: the states a build covers, filled by the loader from
  regions/regions.json, and read by the query functions. `pgeo-load functions` takes `--build`
  because it refills it.

All 1,560 Maine accuracy cases are unchanged, case by case, by the query-side half of this;
the build-side half is checked by rebuilding Maine and running the gate.

## [0.11.0] - 2026-09-22

### Added
- `pgeo.regions`: the states a build covers, read from `regions/regions.json`. `pgeo-load build
  --build ny` (or `--build me,nh,vt`, or `$PGEO_BUILD`) selects the Who's on First region ids,
  the OpenAddresses files, the processed CSVs and the OSM extracts. The build metadata records
  the build and its states.
- Several OSM extracts per build: the first creates the tables, the rest append.

### Changed
- OpenAddresses is read from `data/raw/<build>/oa/**/*.csv` - the archives OpenAddresses
  publishes - instead of the tree the Pelias interpolation step produced.
- `compose.yml` takes the stack name, database name and ports from the environment. The defaults
  are the original values, so the existing stack is unchanged.

### Fixed
- The OpenAddresses CSV dialect is stated rather than sniffed. DuckDB sampled 20,480 rows of New
  York's statewide file, saw no quote character, chose `quote=''`, and then failed on line 90,718
  at a unit field reading `"BLDG 16, Boys Girls Club Room"`.

## [0.10.0] - 2026-09-21

Performance, with output unchanged: **7,380 golden queries (47,962 result rows) return
byte-identical JSON before and after**, across all four endpoints, with and without filters and
focus points; 6,720 accuracy and fuzz cases show no change in verdict, distance, confidence or
result count on either front end. Accuracy stays 95.8%. Detail: docs/PERFORMANCE_OPTIMIZATION.md.

### Changed
- `geocode.autocomplete` costs 36% less CPU per request (50.3 s -> 32.2 s over a 2,920-keystroke
  workload), from four changes:
  - `keep()` is skipped when a request carries no filter. It holds an `EXISTS`, so it cannot be
    inlined, and takes the whole 780-byte row: it was 63% of the candidate stage while filtering
    nothing.
  - Candidates are ranked on a plain expression and hits are built only for the rows that survive
    the `LIMIT`. A 27-field record used to be built for every candidate, with the spheroidal
    distance computed twice, and all but `size` discarded.
  - Unfiltered requests read a new narrow table, `pgeo.feature_ac` (188 bytes a row against 780,
    hot columns first): 7,809 heap pages become 2,912 for a common prefix.
  - A one- or two-letter trailing prefix is kept out of the GIN scan and tested on the rows that
    come back. `s:*` used to be expanded to every token starting with "s" - including "street" -
    before the selective words could narrow anything: 44.8 ms -> 2.9 ms for the same 54 rows.
- `geocode.search` skips `keep()` when there is no filter and computes the distance to the focus
  once rather than twice. Its fuzzy-name stage deliberately still reads `pgeo.feature` (below).

### Added
- `pgeo.feature_ac`, built by `030_enrich_index.sql`: +60 MB on disk, 1.2 s of build time.
- `pgeo/tests/test_query_paths_sql.py` (82 tests) and `test_feature_ac_sql.py` (9): the fast and
  slow routes must agree on every keystroke; address mode is checked against a plain query with no
  optimization in it; the side table must be a faithful copy in the same physical order.

### Tried and rejected, because accuracy is the point
- **Firing the typo fallback only when the prefix match found nothing.** It would have made
  autocomplete 3.3x cheaper, and the 150 autocomplete accuracy cases did not move. The fuzz set
  found the hole: "Walker Ci", one character off "Walker Corner", still prefix-matches one wrong
  row, so the fallback was suppressed and the right town - fourth, among the "filler" - was lost.
  One case in 3,360. Reverted; a test now pins that query.
- **The narrow table in `search`'s fuzzy-name stage.** Generic names tie in their hundreds at one
  similarity, and which survive the 60-row cut is decided by physical row order. It changed the
  top result of 16 of 5,002 golden queries for a 17% gain on that stage. Reverted.

### Fixed
- `feature_ac` is built with `CREATE TABLE AS ... ORDER BY ctid`, not `CREATE TABLE` + `INSERT`.
  An `INSERT` consults the free-space map and backfills earlier pages: it left 660 of 203,399
  rows out of physical order, and because ties between equal candidates are broken by arrival
  order, that alone changed 12 of 4,018 golden autocomplete results. Caught by the golden diff
  before release; the accuracy percentage had not moved.

### Fixed
- The database password is percent-encoded into the DSN. A password containing `/` or `#`
  ended the URL authority, so the connection named a different host - silently, and only for
  a hand-set password, since generated ones are alphanumeric. Found by a new test.

### Added
- Tests for `settings.py` (23): secrets-file parsing, environment precedence, DSN building,
  URL-reserved characters in the password, parse-mode validation and the frozen dataclass.
- Tests for the ranking behaviours (28, `tests/test_ranking_sql.py`): one per tuning step, so
  a regression names the behaviour rather than only moving the accuracy score.

## [0.9.0] - 2026-09-20

### Added
- `/v1/attribution`: the data-licence page Pelias also serves, built in SQL
  (`geocode_api.v1_attribution`) and returned as HTML by both front ends. PostgREST serves it
  through a media-type domain (`geocode_api."text/html"`), and the edge sets `Accept: text/html`
  so every client gets the page as it does from Pelias. It names each source the build loads
  and its licence. The compatibility contract covers it (case 28 of 28); 17 unit tests cover
  the page itself.

## [0.8.0] - 2026-09-19

Note on provenance: the databases measured for the study report were built while this package
still read 0.7.0, so they stamp `geocode.engine_version() = 0.7.0` and the report records that
as the measured engine. A database reports 0.8.0 only after the next `pgeo-load build`; the
version is stamped by the build on purpose, and is not edited in place.

### Added
- `pgeo-tune verify` fails when the running database reports engine version `0+unknown`: the
  build stamps `geocode.engine_version()`, and an unstamped database puts that placeholder in
  every API response (it reached the study report's title block).
- `/v1/address`: the address point's `lat`/`lon` in the `usps` block (differs from the place for `nearest` matches).

## [0.7.0] - 2026-09-19

### Added
- Pelias API compatibility (docs/PELIAS_COMPATIBILITY.md): `boundary.circle.*`,
  `boundary.gid`, `boundary.country` and `categories` on search, structured, autocomplete
  (and gid/country/categories on reverse); `lang`, `api_key`, `debug` accepted; Pelias
  hierarchy properties (`*_gid`, `county_a`, `country_code`, `country_gid`) from WOF ids
  recorded at build time (`feature.hier`); counties named "X County" as in Pelias.
- Pelias-shaped HTTP 400 errors from the pure-SQL API (PostgREST `response.status`).

### Fixed
- `boundary.rect` returned no results (filter applied after the candidate cut).
- Reverse geocoding scanned all features per request: new index `feature_admin_idx`.

### Changed
- `/v1/address` returns input errors as a Pelias-style envelope (HTTP 400) instead of raising.

## [0.6.0] - 2026-09-19

### Added
- `GET /v1/address` (pgeo extension, both front ends): USPS Publication 28 components,
  delivery and last lines, municipality, county and FIPS codes, for a selected gid, a
  free-text address or a coordinate. Venues and streets get the nearest address point
  (`match: nearest`, `distance_m`); admin areas get place context only. SQL in
  `pgeo/sql/060_address.sql` with the C1, C2 and directional tables. Docs:
  `docs/ADDRESS_API.md`.
- The loader tells PostgREST to reload its schema cache after applying functions.

### Security
- PostgreSQL never logs bind parameters (`log_parameter_max_length = 0`, also on error):
  queries carry addresses.

## [0.5.0] - 2026-09-18

### Added
- `pgeo-tune`: measured tuning profiles (`pgeo/tuning/profiles/`: tiny, small, medium,
  large, workstation), `apply` (renders the PostgreSQL and front-end settings, restarts and
  checks they took effect), `auto` (extrapolate for another machine size), `show`, `verify`
  (known-answer queries). Profile settings are allowlisted and value-checked.
- The build runs `VACUUM (ANALYZE)` on the new tables after the swap, so index-only scans
  work from the first query.
- Engine version has a single source (`pyproject.toml`): FastAPI reads the package metadata;
  the loader stamps it into `geocode.engine_version()` for the SQL API.

### Changed
- `pgeo/docker/db/tuning/active.conf` is generated by `pgeo-tune` and no longer tracked.

## [0.4.2] - 2026-09-18

### Fixed
- Autocomplete: "st" before another word ("389 congress st portland") matched only "saint";
  it now matches "street" or "saint", and the last token also matches its raw form.

## [0.4.1] - 2026-09-18

### Fixed
- Ambiguous addresses ("21 Church St, Maine") looked unique: the house number is now looked
  up on the best street names in every town. Confidence when wrong 0.73 -> 0.69.

## [0.4.0] - 2026-09-18

### Changed
- Town-aware name ranking: the queried town is resolved to a location that anchors
  tie-breaks and counts as agreement by distance; the bare town is a name target only when
  no name was parsed; admin results auto-agree only for bare-town queries; same-named
  venues no longer make a town query ambiguous. Accuracy 93.1% -> 95.8% (venues 81% ->
  94%).

## [0.3.2] - 2026-09-18

### Fixed
- "Portlnd, ME" returned venues named "Portland, ME" instead of the town (trailing state
  words are dropped from name targets).
- "main st" with a focus point ignored the focus (candidates were cut before the focus
  applied).
- Confidence could rise down the result list; autocomplete typo fallback repeated towns.

## [0.3.1] - 2026-09-18

### Changed
- The build drops `feature_raw` and staging tables: database 971 MB -> 674 MB.

## [0.3.0] - 2026-09-18

### Added
- Ambiguity-aware confidence: ties between distinct places lower confidence (right/wrong
  0.94/0.90 -> 0.91/0.70).

## [0.2.1] - 2026-09-18

### Fixed
- `word_similarity` direction: one-word generic names ("Mountain") no longer match any
  query that contains the word.

## [0.2.0] - 2026-09-18

### Added
- Pure-SQL API (schema `geocode_api`: `v1_search`, `v1_search_structured`,
  `v1_autocomplete`, `v1_reverse`, `v1_place`) returning Pelias GeoJSON, with a PL/pgSQL
  port of the rule parser; PostgREST v16.3 gateway service in `compose.yml`.

## [0.1.0] - 2026-09-18

### Added
- First full build and working Pelias-compatible API (FastAPI + asyncpg): loaders for
  OSM, OpenAddresses, WOF, GNIS, ZCTA and Overture (DuckDB, ogr2ogr); atomic schema swap;
  read-only API role; search, structured search, autocomplete, reverse and place in SQL;
  libpostal as a service or extension, or a rule parser.
