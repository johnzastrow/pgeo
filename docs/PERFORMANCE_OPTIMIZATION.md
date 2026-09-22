# pgeo performance optimization log

A working record of the September 2026 investigation into pgeo's query-path performance: what was
measured, what was found, what was tried, and what each change is worth. Kept in this detail so it
can be written up as a section of the study report. Negative results are recorded too.

**Constraint set by the project:** no loss of accuracy, and the same resources. Every change below
is classified by what it does to the *output*, because "accuracy preserved" can mean two very
different things:

- **Identical output** - the same rows in the same order for every query. No accuracy question
  arises, because nothing a client can observe has changed.
- **Same measured accuracy, different output** - the accuracy set scores the same, but a client
  sees a different list. That is a product decision, not an optimization, and is flagged as one.

Status: **implemented in pgeo 0.10.0.** Findings F1 to F4 are in `pgeo/sql/`; F5's proposed change
was tried and rejected on the evidence (Section 5.3). Section 5 is the verification, Section 6 the
results.

## 1. Where to look, and why

Section 3.4.3 of the report established that autocomplete is the first endpoint over its target on
every pgeo configuration: it has the tightest target (250 ms) and by far the most requests, since
a type-ahead session sends one request per keystroke. pgeo is CPU-bound (Section 3.4.4: memory is
not its limit), so capacity is set by CPU time per autocomplete request. Previous tuning had been
configuration (parallelism, JIT, memory) and one missing index; the SQL of the query path itself
had not been profiled.

`search` was profiled for comparison: 6 to 134 ms per request against a 750 ms target. It has
headroom and is not what limits capacity, so the work below is on `geocode.autocomplete`. `search`
shares three of the patterns found (Section 6).

## 2. Method

- **Workload.** 2,920 keystrokes from 140 simulated type-ahead sessions, drawn from the load
  corpus with the same mix the k6 session uses (65% exact of which half addresses, a quarter
  places, a quarter venues; 12% typos; 13% variants; 10% misses), each with a focus point, every
  prefix from 2 characters to the full text. Seeded, so it is reproducible.
- **Timing.** Each variant run over the whole workload twice in one session; the first pass warms
  plans and cache and is discarded. Single connection, otherwise idle machine. These are CPU
  costs per request, **not capacity figures** - capacity has to be confirmed with the load ramp.
- **Equivalence.** For every keystroke, the ordered list of result ids from the variant is
  compared with the original's. "Identical" below means 2,920 of 2,920 lists equal, in order.
- **Accuracy.** The 150 autocomplete cases of the accuracy set, scored in SQL by the harness's
  rule (hit within the radius in the first five results, house number equal where the truth has
  one). The SQL scorer reproduces the harness's published figure exactly: 143 of 150, 95.3%.

## 3. Findings

### F1. `keep()` cost 63% of the candidate stage while filtering nothing

`geocode.keep()` applies the optional filters (layers, sources, boundary, gid, categories). With
no filter set - the normal case, and every request in the load test - it returns true for every
row. It is declared `LANGUAGE sql`, but it contains an `EXISTS` subquery, so the planner cannot
inline it: it is called once per candidate row, and because its first argument is the whole
`pgeo.feature` row, each call materialises a 780-byte composite including the geometry, the
tsvector and two jsonb columns.

Evidence, prefix `portland:*` (12,559 candidate rows):

| | Execution time |
|---|---|
| Candidate stage as written | 127.4 ms |
| Same, without `keep()` | 47.9 ms |

**Change:** compute one boolean, `nofilter`, when the function starts, and write the predicate as
`(nofilter OR geocode.keep(...))`. Output identical: when no filter is set `keep()` is true by
definition. The function was also applied a second time to the rows that had already passed it;
that call is dropped.

### F2. Full result records were built for every candidate, then all but ten discarded

The ranking stage called `geocode.to_hit()` - which assembles the 27-field result record - on
every deduplicated candidate (376 for `portland`), ordered by the score inside that record, and
kept `size` of them. It also computed `ST_Distance` on the spheroid **twice** per row: once for
the reported distance, and again inside `focus_boost()`.

Evidence: ranking stage 42 ms for 376 rows, of which 366 were discarded.

**Change:** rank on a plain expression, computing the distance once and deriving both the
reported distance and the focus boost from it; `LIMIT`; then join back to the feature table and
call `to_hit()` for the winners only. Output identical: `to_hit()` passes the score through
untouched, so the ordering key is the same number.

### F3. The hot columns sit behind twenty variable-length columns in a 780-byte row

Ranking reads `importance`, `name_norm`, `label`, `geom` and `tokens`. In `pgeo.feature` these
are columns 24, 25, 17, 20 and 30 of 30, behind some twenty `text`/`jsonb` columns, and
PostgreSQL cannot jump to an attribute that follows a variable-length one - it walks them. The
average non-address row is 780 bytes, of which `hier` is 204 and `addendum` 171, neither of which
ranking reads. So the 12,559 candidates for `portland` are spread over 7,809 heap pages.

The codebase already solves this problem for streets, with the narrow `street_name` table that
fuzzy street matching runs against before touching the big one.

**Change:** a side table for autocomplete, non-address rows only, hot columns first:
`(id, importance, geom, layer, label, name_norm, tokens)` with its own GIN indexes on `tokens`
and on `name_norm` (trigram). 203,400 rows, **188 bytes per row against 780; 39 MB plus 16 MB of
indexes; built in 1.2 seconds.** When a request carries filters, which need columns the side
table does not have, the original path is used.

Evidence, prefix `portland:*`: candidate stage 47.9 ms -> **14.2 ms**, heap pages visited 7,809 ->
2,912. Output identical.

Resource note: +55 MB on disk. It is not expected to raise memory use - the point of it is that
the hot working set becomes smaller - but that is to be confirmed under load.

### F4. A one- or two-letter prefix was expanded inside the index scan

The largest single finding. While a word is being typed, its prefix is searched as `s:*`. GIN
expands a prefix term to every token that starts with it, and unions all of their posting lists.
For `s:*` that includes `street`, which is in nearly every address. The selective terms in the
same query (`12 & main`) cannot narrow anything until that union is finished.

Evidence, address mode, `12 main s`:

| | Index scan | Total | Rows |
|---|---|---|---|
| `tokens @@ '12 & main & (south:* \\| s:*)'` | 43.0 ms | 44.8 ms | 54 |
| `tokens @@ '12 & main'`, prefix applied as a filter | 1.0 ms | **2.9 ms** | 54 |

**Change:** when the last token is at most two characters and there is at least one complete
token, the index is asked for the complete tokens only (`tsq_idx`), and the full query (`tsq`) is
applied to the rows that come back. The filter is written in function form,
`ts_match_vq(tokens, tsq)`, precisely so that the planner does *not* recognise it as an index
condition and push it back into the scan. Output identical: `tsq` implies `tsq_idx`, and the
filter runs before the sort and limit.

Thresholds of 1, 2 and 3 characters were measured (33.6, 32.2, 32.1 s over the workload). Two is
used: three gains nothing further.

### F5. The typo fallback is over half of all autocomplete CPU

After the prefix match, if fewer than `size` rows were found and the text is four characters or
longer, a trigram similarity search appends fuzzy matches to fill the list. Because results are
appended and never re-sorted, fallback rows always follow every prefix row.

| | |
|---|---|
| Keystrokes on which it fires | 2,022 of 2,920 (69%) |
| ...with the prefix match having found 1 to 4 rows | 1,431 |
| ...with the prefix match having found nothing | 488 |
| ...with it having found 5 to 9 | 103 |
| Firings that add no row at all | 330 |
| Share of original autocomplete CPU | 52% (49.9 s with it, 23.7 s without) |

Its cost is the recheck. For `main street ban` the trigram index offers 10,221 candidates, 8,634
fail the similarity recheck, 1,543 survive to be ranked, and at most `size - found` are used.
F2 and F3 were applied to it (rank first, narrow table), which helps the ranking but not the
recheck: 52 ms -> 47 ms.

**What the filler is.** For `389 congress st po` the prefix match returns exactly one row - the
correct address - and the fallback appends nine other towns' Congress Streets.

This one cannot be made cheaper with identical output (see N1). What can be done is to fire it
less often, which changes what a client sees. Firing it only when the prefix match found nothing
was approved on condition that no accuracy was lost, implemented, and **rejected**: Section 5.3.

## 4. Negative results

### N1. A trigram-count bound does not prune the fallback's candidates

`similarity = s / (a + b - s) <= min(a, b) / max(a, b)`, so a similarity of at least 0.35 is
impossible unless the two strings' trigram counts satisfy `0.35a <= b <= a / 0.35`. That bound is
exact and could be applied inside the index with a `btree_gin` column. Measured on
`main street ban`: it removes **132 of 10,221 candidates (1.3%)** and wrongly removes none. The
rejected candidates are of similar length; they simply share too few trigrams. Not worth an index.

### N2. Firing the fallback only when fewer than five rows were found

Provably cannot change hit@5, since fallback rows follow prefix rows. But it saves almost nothing:
33.0 s -> 31.1 s, because only 103 of 2,022 firings have five or more prefix rows.

### N3. The narrow table does not suit `search`'s fuzzy-name stage

F3 was applied to the `names` stage of `geocode.search` (16,716 trigram candidates for
`main street`; 86 ms -> 72 ms). Generic names tie in their hundreds at one similarity - for
`Flying Hill` the 60th and 61st candidates both score 0.375 - and which survive the 60-row cut is
decided by the order rows reach the sort. It changed the top result of **16 of 5,002** golden
search queries. Reverted: `search` reads the wide table there, and is byte-identical. A 17% gain
on one stage of an endpoint with fivefold headroom was never worth a changed answer.

### N4. Gating the typo fallback to "the prefix match found nothing"

The largest gain on offer (3.3 times less CPU) and the one recommended at the end of the
investigation. Rejected; see Section 5.3.

### N5. Configuration

Parallel query, JIT and memory settings were covered by earlier tuning (report Section 2.7) and
were not revisited. `max_parallel_workers_per_gather = 0` is deliberate: with many concurrent
users, parallelism between queries is worth more than within one.

## 5. Verification

The bar was set by the project: accuracy is pgeo's selling point, so it may not drop at any point.
"The accuracy percentage did not move" is too weak a test for that - a percentage cannot see one
case breaking while another is fixed, or an answer changing to a different correct one. So the
claim tested was the stronger one: **nothing a client can observe changed.**

### 5.1 The golden set

Before any code was touched, the full `features` JSON returned by the API functions was recorded
for 7,380 queries - every field of every result, not just ids:

| Group | Queries | What it covers |
|---|---|---|
| Accuracy set | 1,560 | all four endpoints |
| Fuzz rounds | 1,800 | progressively corrupted input, F0 to F5 |
| Keystrokes | 2,920 | every prefix of 140 type-ahead sessions, with a focus point |
| Filtered | 880 | layers, sources, rectangle, circle, gid and sizes 1, 5, 40, on prefixes and full text |
| Focused search | 220 | full-text search with a focus point |

The filtered group exists because the optimizations add a fast route for *unfiltered* requests;
the slow route needs its own proof. **The original was first run against itself** - 7,380 of
7,380 identical - so it is deterministic, and any difference afterwards is attributable to the
change and nothing else.

Final result: **7,380 of 7,380 queries byte-identical, 47,962 result rows.**

### 5.2 What the golden set caught that the accuracy score did not

Two defects, both introduced by this work, both invisible in the accuracy percentage.

**The side table was filled in the wrong order.** It was first built with `CREATE TABLE` and
`INSERT ... SELECT ... ORDER BY ctid`. An `INSERT` consults the free-space map and backfills
earlier pages with rows that fit: **660 of 203,399 rows landed out of physical order.** That
matters because the engine breaks ties by arrival order - two sources for one pond with the same
label and importance, two towns with the same score - and arrival order is physical order. It
changed 12 of 4,018 golden autocomplete results, every one a tie falling the other way.
`CREATE TABLE AS ... ORDER BY ctid` bulk-appends and leaves **none** out of order; the 12 went to
zero. `test_feature_ac_sql.py` now guards it.

This exposes something about the design worth knowing: **where candidates tie, the winner is
decided by physical row order.** That was already true before this work. It is deterministic on a
given build, which is why the accuracy numbers are stable, but it is not a rule anyone chose.

**The narrow table changed answers in `search`** (N3): 16 of 5,002. Reverted.

### 5.3 The recommendation that was wrong

The investigation ended by recommending that the typo fallback fire only when the prefix match
found nothing: 3.3 times less CPU, and the 150 autocomplete accuracy cases did not move (143 of
150 either way). It was approved on condition that no accuracy was lost, and implemented.

The fuzz set found the hole. **`Walker Ci`** is a one-character corruption of "Walker Corner". It
still prefix-matches one row - *Walker Heights Circle*, in Kennebunk, 166 km away - so under the
gate the fallback did not run. Before, it had:

```
 1. [prefix]   Walker Heights Circle, Kennebunk      166.3 km from the truth
 2. [fallback] Walker
 3. [fallback] Walker, Central Aroostook
 4. [fallback] Walker Corner                           0.0 km  <- the right answer
```

The premise was that a typo makes the prefix match find nothing. **Sometimes a typo makes it find
the wrong thing instead**, and then the "filler" is the rescue. One case of 3,360, hidden inside a
fuzz percentage that did not change at one decimal place (66.7% both times). Found by diffing
every case individually rather than reading the headline. The gate was reverted, the fallback
fires exactly as before, and `test_a_typo_that_still_prefix_matches_something_is_rescued` pins
that query so the idea is not re-tried blind.

A provably safe variant exists - fire only when fewer than five rows were found, which cannot
alter the first five results - but it saves 4% (N2) and was not worth a visible change.

### 5.4 Through the front ends

The golden set calls the SQL functions directly. The accuracy harness goes over HTTP, so it was
run before and after on both front ends - PostgREST and FastAPI - for the accuracy set and the
fuzz rounds, and compared **case by case** on verdict, hit@1, hit@5, distance to the truth,
confidence, error and result count:

| | Cases | Changed |
|---|---|---|
| Pure SQL, accuracy + fuzz | 3,360 | 0 |
| FastAPI, accuracy + fuzz | 3,360 | 0 |

Accuracy 95.8% and fuzz 66.7%, before and after, on both. The accuracy gate passes against the
committed baseline; the Pelias compatibility contract passes; the security posture, unit and SQL
suites pass (212 pgeo tests).

### 5.5 Tests that stay

The golden snapshot is evidence about this change on this data build; it would go stale as a
fixture. What was added to the suite are properties that hold on any build (89 tests):

- **Two routes, one answer.** A `layers` filter naming every layer excludes nothing but is still a
  filter, so it forces the slow route - the wide table, `keep()` on every row. Its result must
  equal the unfiltered one, on every keystroke of 18 texts, for autocomplete and search.
- **An independent oracle.** Address mode has a total order (score, then id), so its expected
  rows are computed by a plain query with no optimization in it and compared exactly. This is the
  direct check on the short-prefix split (F4).
- **The side table** is a complete, faithful copy, in the same physical order, a third the width.
- **Behaviour.** Typos are rescued, including `Walker Ci`; the reported distance is the real
  spheroidal distance; sizes are honoured on both routes; a real filter still filters.

## 6. Results

### 6.1 CPU per request

Whole workload, 2,920 keystrokes, second pass, single connection:

| Variant | Total | Mean | p50 | p95 | Output |
|---|---|---|---|---|---|
| Original | 50.3 s | 17.2 ms | 13.5 ms | 45.7 ms | - |
| F1 + F2 | 45.7 s | 15.7 ms | 12.5 ms | 43.3 ms | identical |
| + F3 narrow table | 38.0 s | 13.0 ms | 9.3 ms | 37.9 ms | identical |
| **+ F4 short prefix (shipped)** | **32.2 s** | **11.0 ms** | **7.8 ms** | **35.3 ms** | **identical** |
| + fallback only if found < 5 | 31.1 s | 10.7 ms | 7.1 ms | 36.2 ms | 88 lists shorter - not shipped |
| + fallback only if found = 0 | 15.4 s | 5.3 ms | 2.0 ms | 20.4 ms | loses a case - rejected |

**A 36% cut in autocomplete CPU, with output byte-identical.** What remains is two-thirds typo
fallback, which Section 5.3 shows is doing real work.

### 6.2 Capacity

The claim that matters. Same harness, corpus, session mix, ramp and latency targets as every
other capacity figure in the report (`tests/load/run_matrix_pgeo.py`), pure-SQL front end, run
`20260921-1642-pgeo`. "Before" is the published figure (Section 3.4 of the report, stable across
two independent runs on 2026-09-19).

| Configuration | Before | After | Change | Worst endpoint at the new limit | Throughput |
|---|---|---|---|---|---|
| P1 - 1 vCPU, 1.0 GB | 24 users | **32** | +33% | 54% of its target | 37 req/s |
| P2 - 2 vCPU, 1.5 GB | 48 users | **64** | +33% | 39% | 74 req/s |
| P4 - 4 vCPU, 2.0 GB | 96 users | **128** | +33% | 45% | 147 req/s |
| PM - unconstrained | 128 users | **192** | +50% | 21% | 222 req/s |

Every configuration moved up exactly one step of the ramp (... 24, 32, 48, 64, 96, 128, 192 ...),
and the unconstrained one moved two. The ramp has no steps in between, so each figure is a floor:
at every new limit the worst endpoint is at half its target or less, where before the optimization
the limits sat closer to theirs.

Memory budgets are unchanged - the same P1 to P4 profiles - so this is the same resources, as the
project required. The side table adds 60 MB on disk.

What it does to the comparisons in Sections 3.14 and 3.15 of the report, on two cores:

| | Pelias | Photon | Nominatim | pgeo before | pgeo after |
|---|---|---|---|---|---|
| Users within targets | 192 | 128 | 64 | 48 | **64** |
| pgeo's cost against it | | | | 4x / 2.7x / 1.33x | **3x / 2x / parity** |

pgeo now holds as many users per core as Nominatim, the engine nearest its own architecture, and
unconstrained it matches Pelias's unconstrained figure (192) - though that comparison flatters
pgeo, since Pelias M0 was held to one API worker.

**Same-day control.** The "before" figures above are two days old, so the old functions were
reinstalled and P2 was run again on the same day, machine and harness (run `20260921-1826-pgeo`):

| Users, P2 | Old engine, worst endpoint | New engine, worst endpoint | Old CPU p95 | New CPU p95 |
|---|---|---|---|---|
| 32 | 0.29x target | 0.21x | 131% | 107% |
| 48 | 0.48x | 0.35x | 193% | 160% |
| 64 | **1.44x - fails** | **0.39x - passes** | 202% | 186% |
| 96 | 5.67x | 2.08x | 204% | 202% |

The old engine holds 48 users and fails at 64, exactly the published figure, so the machine was
not simply faster on the day: the gain is the code. At 64 users the worst endpoint went from 1.44
times its target to 0.39. The new engine was then reinstalled and the golden set re-run against
it: 7,380 of 7,380 byte-identical.

### 5.6 The build itself

The golden set and the tests above ran against a side table created by hand. The committed build
(`scripts/pgeo_rebuild.sh`: load from raw data, atomic swap, known-answer checks, accuracy gate)
was then run end to end. Gate passed, 95.8% and 66.7%; on the rebuilt database the verdict is
unchanged on all 3,360 cases and the 89 new tests pass, including the physical-order guard on
the side table. Thirty-one cases report a different distance to the truth, all still correct: a
rebuild lays rows out in a new physical order, and ties fall with it (Section 5.2). That is the
one thing the byte-for-byte comparison cannot carry across a rebuild, which is why the checks
that survive it are properties, not snapshots.

## 7. What is left

- The typo fallback is now the dominant cost and cannot be gated. Making the trigram recheck
  itself cheaper is the open problem: 10,221 index candidates for 1,543 survivors. A
  word-level rather than whole-string similarity index is the direction worth trying, and would
  need the same golden-set treatment.
- Tie-breaking by physical row order (Section 5.2) deserves an explicit rule. Any rule changes
  some current outputs, so it is a deliberate decision with its own accuracy run, not a cleanup.
- `search`'s fuzzy-name stage (86 ms for `main street`) is recheck-bound in the same way.
