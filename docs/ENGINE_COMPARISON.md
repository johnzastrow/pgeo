# Engine Comparison: Pelias vs pgeo

Answers the two questions set for the capacity tests: **(a) which performs better, and
(b) is pgeo good enough at the same or lower resources?** Same k6 session, SLOs, ramp and
host for both engines. Full results:
[LOAD_TEST_RESULTS.md](LOAD_TEST_RESULTS.md) (Pelias, including data volume) and
[LOAD_TEST_RESULTS_PGEO.md](LOAD_TEST_RESULTS_PGEO.md) (pgeo); accuracy in
[ACCURACY_RESULTS.md](ACCURACY_RESULTS.md). Measured 2026-09-18/19 on the full Maine data.

SLO targets (p95): autocomplete 250 ms, search and structured 750 ms, reverse 400 ms;
errors < 1%. "Limit" = most concurrent users with every endpoint within target.

## Answers

- **(a) Speed and throughput: Pelias is clearly faster.** At the same CPU count it serves
  about four times more users within the targets, and its p95 at 3 users is 20-40 ms on every
  endpoint versus 5-105 ms for pgeo.
- **(b) Good enough at lower resources: yes, for the stated need.** pgeo meets every target
  at 3 users on the smallest configuration tested (1 vCPU, 1.6 GB budget), where Pelias
  needs an ~8.3 GB budget (it was OOM-killed below that). pgeo also answers far more
  queries correctly: 95.8% vs 75.5%.
- **Where the gap comes from**: pgeo runs several candidate queries and a scoring step in
  PL/pgSQL per request, where Pelias does compiled index lookups in Elasticsearch. Since the
  reverse-geocoding index fix (TUNING_REPORT.md, T11), **autocomplete** is the first endpoint
  over target in every pgeo configuration, because it has the tightest target (250 ms) and
  fires several times per typed word. Reverse, previously the bottleneck at 225-390 ms, now
  answers in 5-18 ms.

## Head to head

| CPUs | Pelias config: memory budget | Pelias limit | pgeo config: memory budget | pgeo limit (pure SQL / FastAPI) |
|------|-------------------------------|--------------|----------------------------|---------------------------------|
| 1 | C1: 8.3 GB | 96 | Pmin: 1.6 GB; P1: 2.0 GB | 24 / 24; 24 / 24 |
| 2 | C2: 8.4 GB; C3: 9.5 GB | 192; 192 | P2: 2.5 / 2.7 GB | 48 / 48 |
| 4 | C4: 10.8 GB | 384 | P4: 3.0 / 3.3 GB | 96 / 96 |
| all (12 threads) | M0: unlimited | 192 (1 API worker) | PM: unlimited | 128 / 192 |

| At 3 users | Pelias (C3) | pgeo pure SQL (P2) | pgeo FastAPI (P2) |
|------------|-------------|--------------------|-------------------|
| p95 autocomplete / search / structured / reverse (ms) | 19 / 27 / 20 / 39 | 44 / 97 / 105 / 5 | 54 / 95 / 106 / 14 |
| Memory in use (all containers) | 6.3 GB | 0.55 GB | 0.66 GB |
| Accuracy (1,560 cases) | 75.5% | 95.8% | 95.8% |
| Containers | 6 (Elasticsearch, API, libpostal, placeholder, pip, interpolation) | 3 (PostgreSQL, PostgREST, nginx edge) | 2 (PostgreSQL, FastAPI) |

## Data volume (Pelias, C3: 2 vCPU, 9.5 GB)

| Dataset | Documents | Limit |
|---------|-----------|-------|
| D1 Who's On First | 3,280 | 384 |
| D2 + OpenAddresses | 783,540 | 384 |
| D3 + OpenStreetMap | 1,552,532 | 256 |
| D4 + GNIS, ZCTA | 1,573,062 | 256 |
| D5 + Overture places (full) | 1,649,644 | 192 |

Addresses are nearly free; OpenStreetMap and Overture places (venues and names, which
widen every text match) cost one ramp step each. The pgeo equivalent (subset builds via
`pgeo-load build --sources`) is not yet measured.

## Which to use

| Need | Better choice | Why |
|------|---------------|-----|
| Small VM or VPS, a handful of users (the stated target) | pgeo | 1.6-2.7 GB instead of 8.3-9.5 GB; more accurate |
| Hundreds of concurrent users per vCPU | Pelias (today) | 4x the throughput per CPU |
| Messy input: typos, variants, misses, venues | pgeo | 87% vs 30% on typos; misses handled 95% vs 41% |
| Fewest components, data in PostgreSQL (G6) | pgeo pure SQL | Database + stateless gateway |
| Structured USPS addresses (LANCER) | pgeo | `/v1/address` exists only in pgeo |

Caveats: pgeo's ranking was tuned against the accuracy set that scores it here; Pelias ran its
published configuration. The reverse-geocoding work is done and these numbers are from after it;
the remaining per-request cost in PL/pgSQL (autocomplete, now the binding endpoint) has not been
tuned. Minimum-server results for both engines are in the study report, Section 3.12.
