# pgeo Tuning Log

Every tuning change to the PostgreSQL/PostGIS geocoder: what changed, why, and the measured
effect on accuracy (tests/accuracy) and speed (tests/load). A change is kept only if it
does not reduce accuracy; speed gains that cost accuracy are rejected. Design:
[PGEO_DESIGN.md](PGEO_DESIGN.md).

## Planned experiments

| # | Area | Experiment | Measured by |
|---|------|------------|-------------|
| T1 | Parallelism, between queries | API workers and connection pool size vs cores (1, 2, 4 cores): each backend is its own process, so concurrent users spread across cores | Load ramp limit, p95 per endpoint |
| T2 | Parallelism, within a query | `max_parallel_workers_per_gather` 0 / 2 / 4, with lower `parallel_setup_cost` / `parallel_tuple_cost` and `min_parallel_index_scan_size`; heavier queries (fuzzy names, misses) are the candidates | p95 by query type at 3 users and under load |
| T3 | Memory | `shared_buffers`, `effective_cache_size`, `work_mem` sized per VM budget | p95 and memory peak per budget |
| T4 | JIT | `jit` on vs off (off by default for short queries) | p95 |
| T5 | Text indexes | GIN vs GiST trigram (`gist_trgm_ops` supports `<->` KNN ordering), partial indexes per layer | EXPLAIN (ANALYZE, BUFFERS) on slowest cases, then load |
| T6 | Autocomplete | prefix table depth, tsquery candidate limit, typo fallback threshold | autocomplete accuracy + p95 |
| T7 | Parsing | libpostal as service vs extension (shared_preload_libraries vs per-backend load) vs rule parser | accuracy, memory, p95 |
| T8 | Text search engine | core pg_trgm/FTS vs ParadeDB pg_search (BM25, AGPL-3.0) | accuracy + p95 |
| T9 | Admin hierarchy | WOF vs Overture divisions | reverse and town accuracy |
| T10 | Connections | prepared statements, statement cache, PgBouncer transaction pooling | p95 at high concurrency |

Open tuning items noted during testing:

- Focus-point weight too weak: "main st" near Bangor ranks Ellsworth and Belfast first.
- "Portlnd, ME" ranks Overture venues literally named "Portland, ME" above the town.
- Autocomplete repeats Portland as locality and localadmin in the typo fallback branch.

## Log

| Date | Change | Why | Accuracy before -> after | Speed effect |
|------|--------|-----|--------------------------|--------------|
| 2026-09-18 | Name targets: full text searched as a name unless an address was parsed; parser name part and bare town also searched | Town outranked exact addresses; libpostal misreads odd input ("T4 R9 WELS") | smoke tests: exact addresses now rank 1 | none measurable |
| 2026-09-18 | Dedupe results by label and layer group; lighter importance weight | Duplicate towns/ZIPs; importance let towns outrank addresses | smoke tests | none |
| 2026-09-18 | Town agreement ignores parser "towns" contained in the matched name; interpolation and street fallback need street similarity >= 0.6 | libpostal reads "Moosehead Lake" as a city; interpolation used Pine St for Park St | smoke tests | none |
| 2026-09-18 | First accuracy baseline (parse modes) | | service 80.3%, extension 80.3%, rule parser 78.1% correct | p50 ~50-80 ms per query (single core, unloaded) |
| 2026-09-18 | `norm()` expands abbreviations instead of shortening ("rd" -> "road", "mt" -> "mount", "st" -> "street"/"saint") | Typos in spelled-out words failed: sim("mtn rd", "moountain rd") = 0.25 vs sim("mountain road", "moountain road") = 0.81 | combined with the next row | |
| 2026-09-18 | Towns matched against WOF town and postal city; ZIP is only a fallback when an address is present; continuous town-agreement penalty; addresses deduped on number + street + town preferring OpenAddresses; rule-parser street fallback when libpostal finds a number but no street | Failure analysis of the baseline: structured search 0% at rank 1 (ZIP outranked the address), duplicate OA/OSM addresses, libpostal misreading misspelled streets as venue names | overall 80.3% -> **90.4%** (service), rule parser 78.1% -> **89.0%**; addresses 68% -> 98%; structured 0% -> 100%; typos 69% -> 73% | p50 unchanged (~50-80 ms single core) |
| 2026-09-18 | Fuzz rounds baseline (service / rule parser) | New test | F0 91/91, F1 75/74, F2 68/67, F3 47/39, F4 35/38, F5 41/45 (% correct) | |
| 2026-09-18 | word_similarity direction flipped to (query, name); accuracy set qualifies non-unique lake/summit/venue names with the nearest town (applies to both engines) | Generic one-word names ("Mountain", "Hill") matched any query; bare "Mud Pond" has no single right answer | service 92.4%, rule parser **92.7%**, pure SQL via PostgREST **92.7%** (identical per category to FastAPI + rule parser); lakes/summits 59% -> 77% | |
| 2026-09-18 | Comparison with Pelias on the same sets | | Pelias 75.5% overall (typos 30%, misses 41%, lakes 37%); fuzz F1 34% / F2 10% vs pgeo 84% / 73% | Latency not yet comparable (see load tests) |

Open items from this round:

- **Confidence calibration**: pgeo's mean confidence is 0.94 when right vs 0.90 when wrong
  (Pelias 0.98 vs 0.87); confidence should separate right from wrong much better.
- Venues 80% and lakes/summits 77%: next failure analysis.
