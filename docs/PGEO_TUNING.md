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
| T7 | Parsing | rule parser (primary, core-only) vs libpostal service / extension (comparison only) | accuracy, memory, p95 |
| T8 | Text search engine | core pg_trgm/FTS only (constraint D28); pg_search dropped | n/a |
| T9 | Admin hierarchy | WOF vs Overture divisions | reverse and town accuracy |
| T10 | Connections | prepared statements, statement cache, PgBouncer transaction pooling | p95 at high concurrency |

## Log

| Date | Change | Why | Accuracy before -> after | Speed effect |
|------|--------|-----|--------------------------|--------------|
| 2026-09-18 | Name targets: full text searched as a name unless an address was parsed; parser name part and bare town also searched | Town outranked exact addresses; libpostal misreads odd input ("T4 R9 WELS") | smoke tests: exact addresses now rank 1 | none measurable |
| 2026-09-18 | Dedupe results by label and layer group; lighter importance weight | Duplicate towns/ZIPs; importance let towns outrank addresses | smoke tests | none |
| 2026-09-18 | Town agreement ignores parser "towns" contained in the matched name; interpolation and street fallback need street similarity >= 0.6 | libpostal reads "Moosehead Lake" as a city; interpolation used Pine St for Park St | smoke tests | none |
| 2026-09-18 | First accuracy baseline (parse modes) | | service 80.3%, extension 80.3%, rule parser 78.1% correct | p50 ~50-80 ms per query (single core, unloaded) |
| 2026-09-18 | `norm()` expands abbreviations instead of shortening ("rd" -> "road", "mt" -> "mount", "st" -> "street"/"saint") | Typos in spelled-out words failed: sim("mtn rd", "moountain rd") = 0.25 vs sim("mountain road", "moountain road") = 0.81 | measured after rebuild (below) | |
| 2026-09-18 | Towns matched against WOF town and postal city; ZIP is only a fallback when an address is present; continuous town-agreement penalty; addresses deduped on number + street + town preferring OpenAddresses; rule-parser street fallback when libpostal finds a number but no street | Failure analysis of the baseline: structured search 0% at rank 1 (ZIP outranked the address), duplicate OA/OSM addresses, libpostal misreading misspelled streets as venue names | measured after rebuild (below) | |
