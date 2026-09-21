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

Status: **investigation and prototype complete; nothing has been changed in `pgeo/sql/` yet.** The
prototype lives in a scratch schema, `perf_lab`, in the local bench database.

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
less often, which changes what a client sees. That is a product decision; Section 5.

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

### N3. Configuration

Parallel query, JIT and memory settings were covered by earlier tuning (report Section 2.7) and
were not revisited. `max_parallel_workers_per_gather = 0` is deliberate: with many concurrent
users, parallelism between queries is worth more than within one.

## 5. Results

Whole workload, 2,920 keystrokes, second pass:

| Variant | Total | Mean | p50 | p95 | Output |
|---|---|---|---|---|---|
| Original | 50.3 s | 17.2 ms | 13.5 ms | 45.7 ms | - |
| A: F1 + F2 | 45.7 s | 15.7 ms | 12.5 ms | 43.3 ms | identical |
| A + B: + F3 narrow table | 38.0 s | 13.0 ms | 9.3 ms | 37.9 ms | identical |
| **A + B + C: + F4 short prefix** | **32.2 s** | **11.0 ms** | **7.8 ms** | **35.3 ms** | **identical** |
| ...and fallback only if found < 5 | 31.1 s | 10.7 ms | 7.1 ms | 36.2 ms | 88 lists shorter |
| ...and fallback only if found = 0 | 15.4 s | 5.3 ms | 2.0 ms | 20.4 ms | 1,365 lists shorter |

Accuracy, 150 autocomplete cases: **143 of 150 (95.3%) for every row of that table**, the
original included.

Reading it:

- **A + B + C is a 36% cut in autocomplete CPU with output identical on 2,920 of 2,920
  keystrokes.** There is no accuracy question to ask of it.
- The remaining cost is two-thirds typo fallback, and address-mode typing is 83% of what is left,
  because addresses are long and so generate most keystrokes.
- Gating the fallback to "the prefix match found nothing" takes the total to **15.4 s - 3.3 times
  less CPU than the original** - with the same measured accuracy. It would shorten the suggestion
  list on 47% of keystrokes, removing fuzzy filler that follows at least one exact prefix match.

## 6. Not yet done

- **Nothing above is in `pgeo/sql/`.** It is a prototype in `perf_lab`.
- **Capacity is unmeasured.** These are per-request CPU costs on one connection. The claim that
  matters - concurrent users within targets - needs the load ramp (P1, P2, P4, PM) before and
  after.
- **`search` shares F1, F2 and the doubled distance** (`keep()` is called in nine places,
  `to_hit()` in nine, `focus_boost()` in seven). It is not the binding endpoint, so it was left,
  but `main st` costs 134 ms there and the same fixes should apply.
- The full accuracy gate (1,560 cases, all endpoints) and the compatibility contract must pass
  on the implemented version, not only the 150 autocomplete cases on the prototype.
- F3 needs a build step in `030_enrich_index.sql`; being in the `pgeo` schema, the side table
  would be swapped atomically with everything else.
