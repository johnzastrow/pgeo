# How the Testing Works (plain-language guide)

A short explanation of what the performance and accuracy tests measure, how to read the
results, and why they are set up the way they are. The detailed plan is
[LOAD_TEST_PLAN.md](LOAD_TEST_PLAN.md); results are in
[LOAD_TEST_RESULTS.md](LOAD_TEST_RESULTS.md) and [ACCURACY_RESULTS.md](ACCURACY_RESULTS.md).

Two engines are tested with identical inputs, rules and resource limits: **Pelias** (the
reference) and **pgeo** (the PostgreSQL/PostGIS geocoder built in Phase 10).

---

## 1. What a "user" is

One simulated user behaves like a person using the demo page:

- **60%** of the time they type into the search box. Every keystroke from the second
  character sends an autocomplete request (about every 200 ms), and half the time they
  finish with a full search.
- **15%** a full search, **10%** a structured search (street / town / ZIP fields),
  **15%** a click on the map (reverse geocode).
- After each action they pause 3-8 seconds, like a real person reading the result.

That works out to about **1.5-2 requests per second per user**, with bursts near 5 per
second while typing.

The queries come from a fixed, seeded corpus of about 11,000 entries drawn from the real
data: 65% exact names and addresses, 12% typos ("Moosehed Lake"), 13% "off" names
("Lake Moosehead", "St" vs "Street", missing town, apartment numbers), and 10% complete
misses (invented names, out-of-state places, gibberish). 10% of map clicks land offshore.
Both engines get exactly the same queries.

---

## 2. Latency targets

| Endpoint | p95 target | Why |
|----------|-----------|-----|
| `/v1/autocomplete` | **250 ms** | Fires on every keystroke; slower than this and suggestions lag behind typing |
| `/v1/search` | **750 ms** | One-off search after Enter; under a second still feels immediate |
| `/v1/search/structured` | **750 ms** | Same as search |
| `/v1/reverse` | **400 ms** | Map click; the answer should appear about as the marker lands |

**"p95"** means 95% of requests at that load finish within the target. Warm-up requests
are not counted.

A configuration **passes** a load step only if, at the same time:

- every endpoint meets its p95 target,
- errors (non-200 responses, timeouts) stay **below 1%**, and
- **no service restarts or is killed for running out of memory** during the step.

It is **broken** (the test stops going higher) when errors exceed 5%, any p95 reaches
5x its target, or a service dies.

The targets are generous for Maine-sized data: at 3 users every Pelias configuration used
only 8-13% of its slowest target (autocomplete ran around 20-30 ms). Because every run
records the full latency distribution, results can be re-scored against stricter targets
without re-running anything.

---

## 3. How the load ramps up

Each configuration runs:

1. **A 3-user validation**: 45 s warm-up, then 3 minutes measured. This answers "does this
   size of server handle 3 concurrent users?"
2. **A ramp to the breaking point**: 1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 128,
   192, 256, 384, **512** users, each step 15 s warm-up + 60 s measured. The ramp stops at
   the breaking point. The **limit** is the highest step that still passed.

512 users is roughly 900 requests per second, well past anything this service needs; it
exists to find where each configuration actually gives out.

---

## 4. Simulating smaller servers on one machine

The tests run on the workstation, with the engine's containers restricted to act like a
smaller VM:

- **CPU**: the containers share a fixed set of physical cores (1, 2 or 4), like a VM with
  that many vCPUs. The load generator (k6) runs on a separate core so it never competes.
- **Memory**: every service gets a hard memory limit with no swap, so running out of memory
  shows up as a visible failure instead of silent slowdown.
- **Directly to the API**: the tests bypass the production nginx, whose per-client rate
  limits would otherwise be what gets measured.

Configurations tested: C1 (1 CPU, tightest memory), C1s (1 CPU, ample memory), C2 and C3
(2 CPUs), C4 (4 CPUs, the current VM size) and C4a (4 CPUs with Pelias' default single
API worker, as deployed), plus M0 (no limits, the ceiling). The recommended size is then
checked on the real Proxmox VM, including a ramp until it breaks.

---

## 5. Caching, and why the numbers are "warm"

Several layers cache, so the first queries after a start are slower than later ones:

- **Search engine caches** (Elasticsearch filter cache; Postgres shared buffers): cleared
  whenever a configuration's containers are recreated.
- **The operating system's file cache** (index files, SQLite databases): survives container
  restarts; clearing it needs root (`echo 3 > /proc/sys/vm/drop_caches`), which the tests
  do not do.
- **JIT warm-up** (Java, Node.js): the first requests are slow.
- The web proxies do not cache responses.

Every run therefore warms up first and measures **steady state**, which is what a server
that runs all the time experiences. The corpus is large so that exact repeated queries are
rare. Both engines follow the same rules.

---

## 6. Data volume

A second dimension keeps resources fixed (2 CPUs) and changes how much data is loaded:
admin areas only, then adding OpenAddresses, OpenStreetMap, GNIS + ZCTA, and finally
Overture (everything). This shows how much each data layer costs in latency, memory and
capacity. Growth beyond Maine will be measured with real New Hampshire data rather than
synthetic copies, which would distort ranking.

---

## 7. Accuracy (being fast is not enough)

A separate set of 1,560 seeded questions checks whether answers are *right*, using ground
truth taken from the source data (not from either engine):

- **addresses** (OpenAddresses points), **towns** (Who's On First), **lakes and summits**
  (GNIS), **venues** (Overture), **ZIP codes** (Census), **structured** addresses,
  **autocomplete** prefixes, and **reverse** lookups at known address points;
- each in exact, typo and "off name" forms, plus **complete misses** that should return
  nothing (or nothing confident).

A result counts as correct when it lands within a set distance of the true location (for
example 150 m for an address, with the right house number; 8 km for a town) at rank 1
(autocomplete: in the top 5). A miss is handled correctly when there is no result, or the
top result's confidence is below 0.8. The report also checks whether confidence scores
mean anything: correct answers should carry higher confidence than wrong ones.

---

## 8. Findings so far that shaped the tests

- Pelias' memory floor is set by services that cannot shrink (libpostal ~1.8 GB,
  interpolation ~1.8 GB). Tight limits made them crash rather than slow down, and the
  placeholder service's memory grows with the number of concurrent users.
- Pelias has no typo tolerance in search or autocomplete, and can return a confident answer
  in the wrong town (12 Park St, Bar Harbor -> Fairfield at confidence 1.0).
- pgeo, with trigram matching in Postgres, handles typos and those ranking cases; its
  libpostal-free mode is only about 2 points less accurate than with libpostal, which
  matters because libpostal costs about 2 GB of RAM.
