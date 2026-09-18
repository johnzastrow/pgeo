# Capacity and Load Test Plan

Goal: find the **minimum server resources** that serve **3 concurrent users** of the Maine
geocoder within agreed latency targets, then find each configuration's **limits** by
ramping users from 1 until the service degrades or fails. The same tests are re-run later
against the Phase 10 PostgreSQL/PostGIS service so the two can be compared on
(a) raw performance and (b) "good enough" at equal or lower resources.

Status: plan written 2026-09-18; Pelias runs in progress/recorded in
[LOAD_TEST_RESULTS.md](LOAD_TEST_RESULTS.md).

---

## 1. What a "concurrent user" is

A person using the demo page or an app built on the `<pelias-search>` element. One virtual
user (VU) loops through sessions picked at random with these weights:

| Session | Weight | Requests | Pacing |
|---------|--------|----------|--------|
| Type-ahead search | 60% | One `/v1/autocomplete` per keystroke from 2 characters (the widget's 150 ms debounce is shorter than a ~200 ms keystroke gap, so every keystroke fires), then a final `/v1/search` about half the time | 200 ms per keystroke, then 3-8 s think time |
| Full search | 15% | One `/v1/search` (address, place, venue or ZIP) | 3-8 s think time |
| Structured search | 10% | One `/v1/search/structured` | 3-8 s think time |
| Reverse (map click) | 15% | One `/v1/reverse` | 3-8 s think time |

That averages roughly 1.5-2 requests per second per user, with bursts near 5 req/s while
typing.

**Query quality mix** (text queries): 65% exact, 12% typos (1-2 character errors:
"Mosehead Lake", "Congerss St"), 13% off-name variants (St<->Street, N<->North, missing
town or state, unit numbers, "Mt" for "Mount", word order, partial venue names, lowercase),
10% complete misses (impossible house numbers on real streets, invented names,
out-of-state places, gibberish). Reverse: 10% of clicks land offshore in the Gulf of Maine.
Every request is tagged with its query type so latency is reported per type; misses can be
the most expensive queries because the engine falls back through looser searches.

The query corpus (`tests/load/corpus/maine_corpus.json`, about 11,000 entries) is drawn
with a fixed seed from the loaded data (OpenAddresses
addresses, WOF towns, GNIS lakes and summits, Overture/OSM venues, ZCTA ZIPs, and random
points inside the Maine polygon), so both engines get identical requests.

### Caching and how it is controlled

| Cache | Effect on results | Control |
|-------|-------------------|---------|
| Elasticsearch query/filter cache | Repeated filters (layers, sources, boundaries) get faster | Cleared per configuration (containers are recreated); large corpus limits exact repeats |
| OS page cache (ES index ~0.5 GB, placeholder and interpolation SQLite) | Cold reads hit disk; warm reads hit RAM | Fixed warm-up before measuring; the host page cache survives container recreation (clearing it needs `sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches'`, not done) |
| JVM / Node.js JIT warm-up | First requests slower | Same warm-up; warm-up requests are excluded (`phase` tag) |
| nginx / Caddy | No response caching configured; bypassed in these tests | n/a |
| Pelias API, libpostal, pip | No response caches | n/a |

All numbers are therefore **warm steady state**, which is what a continuously running
server experiences. The PostGIS comparison uses the same rules (Postgres shared buffers and
the OS cache are warmed the same way).

---

## 2. Pass criteria (service level objectives)

A configuration "supports N users" if, over a steady-state window at N users:

| Metric | Target |
|--------|--------|
| `/v1/autocomplete` p95 latency | <= 250 ms |
| `/v1/search` and `/v1/search/structured` p95 | <= 750 ms |
| `/v1/reverse` p95 | <= 400 ms |
| Error rate (non-2xx, timeouts) | < 1% |
| Stability | No container restart or OOM kill |

The **limit** of a configuration is the highest user count that still passes. The
**breaking point** is where errors exceed 5%, p95 exceeds 5x target, or a service dies.

---

## 3. Where and how it runs

Tests run on the workstation against the same Pelias images and data as production, with
the query stack constrained to emulate a VM:

- **CPU**: every query container pinned to the same set of logical CPUs (`cpuset`), so the
  stack shares N CPUs like an N-vCPU VM. The load generator (k6) is pinned to other CPUs so
  it does not steal from the stack. Caveat: workstation logical CPUs are hyperthreads, so
  results are indicative; the chosen minimum is re-validated on a real VM (step 5).
- **Memory**: per-container memory limits summing to the VM budget, Elasticsearch heap set
  to match, swap disabled for the containers (a VM under memory pressure would swap or
  OOM; we want the failure to be visible).
- **API workers**: Pelias API `CPUS` set to the CPU count (one Node.js worker per CPU); the
  default of 1 worker is also tested, since a single Node.js process caps at one core.
- **Direct to the API** (port 4000), bypassing nginx: the edge rate limits (10-25 req/s per
  client IP) would otherwise measure the limiter, not the engine. One edge run checks that
  the limits do not throttle 3 real users.
- **Tooling**: k6 (`grafana/k6:2.2.0` container), per-endpoint tags and thresholds;
  `docker stats` sampled every 2 s for CPU and memory per container; container restart and
  OOM counters checked after every run.

Nothing here touches the production VM or the Proxmox host.

---

## 4. Test matrix

Measured idle footprint (anonymous memory): Elasticsearch 5.0 GB at a 4 GB heap, libpostal
1.83 GB, interpolation 1.83 GB, pip 0.36 GB, placeholder 0.30 GB, API 0.08 GB per worker.
A trial run with tighter limits OOM-killed pip at 0.4 GB (it grows under reverse load) and
crash-looped interpolation at 1.9 GB, which set the profiles below. "VM budget" = container
limits + 0.8 GB for the OS (computed by `run_matrix.budget_gb`).

| Profile | libpostal | interpolation | pip | placeholder | API |
|---------|-----------|---------------|-----|-------------|-----|
| floor | 2.0 GB | 2.1 GB | 0.7 GB | 0.8 GB (was 0.4 GB: OOM-killed under load, see results) | 0.3 GB + 0.12 GB/worker |
| standard | 2.2 GB | 2.3 GB | 0.9 GB | 0.5 GB | same |

| ID | CPUs | Memory profile | ES heap / limit | API workers | VM budget | Purpose |
|----|------|----------------|-----------------|-------------|-----------|---------|
| M0 | unconstrained | unconstrained | 4g / none | 1 | n/a | Baseline and reference ceiling |
| C1 | 1 | floor | 768m / 1.5 GB | 1 | 8.3 GB | Smallest plausible |
| C1s | 1 | standard, placeholder 1.0 GB | 768m / 1.5 GB | 1 | 9.1 GB | Single CPU with ample memory: isolates the CPU-bound limit |
| C2 | 2 | floor | 768m / 1.5 GB | 2 | 8.4 GB | Small |
| C3 | 2 | standard | 1g / 2.0 GB | 2 | 9.2 GB | Small, comfortable memory |
| C4 | 4 | standard | 2g / 3.0 GB | 4 | 10.5 GB | Current VM size, API workers = CPUs |
| C4a | 4 | standard | 2g / 3.0 GB | 1 | 10.1 GB | Current VM as deployed (1 API worker) |

### Runs per configuration

1. **Validation**: 3 users, 45 s warm-up plus 3 minutes steady state. Pass/fail against
   section 2.
2. **Ramp to limit**: start at 1 user; add users in steps (1, 2, 3, 4, 6, 8, 12, 16, 24, 32,
   48, 64, 96, 128, 192, 256, 384, 512), each step 15 s warm-up + 60 s measured, until the
   breaking point or 512 users. Any container restart or OOM kill ends the ramp. Record the
   last passing step (limit) and what failed first (CPU saturation of which service,
   memory, errors).

Minimum resources for 3 users = the smallest configuration whose validation passes, with
headroom judged from its ramp curve.

---

## 5. Validation on the real VM (needs approval)

Re-run the 3-user validation, and optionally a ramp, against the Proxmox VM at the
recommended minimum size. This affects the live service and shares CPU with other VMs on
`prox82`, so it runs only with explicit approval, ideally in a quiet window. A crash test
on the production VM is not planned unless requested.

---

## 6. Re-test on the PostgreSQL-only solution (Phase 10)

The PostGIS service exposes the same `/v1/*` API, so the identical k6 scripts, corpus, SLOs
and matrix apply. Comparison report per configuration:

| Question | Measure |
|----------|---------|
| (a) Which performs better? | p50/p95/p99 per endpoint at equal users and resources; highest passing user count |
| (b) Good enough at same or lower resources? | Smallest configuration passing the 3-user validation; ramp limit per GB and per CPU |
| Quality check | Same accuracy harness (PLAN.md section 10); speed does not count if answers are worse |

PostgreSQL-specific knobs to sweep then: `shared_buffers`, `work_mem`, connection pool size
(PgBouncer), PostgREST worker count, and whether libpostal runs in-database or as a
service (its ~2 GB model is the same cost either way).

---

## 7. Deliverables

- `tests/load/`: corpus builder, k6 scripts, runner, report generator (committed)
- `data/loadtest/<run-id>/`: raw k6 summaries and resource samples (gitignored)
- `docs/LOAD_TEST_RESULTS.md`: results tables, limits, recommendation
