# Accuracy Results

Test set: `tests/accuracy/cases.json` (seeded; ground truth from the source data, independent of both engines). "Correct" = right place at rank 1 (autocomplete: in the top 5); misses are correct when nothing, or nothing with confidence >= 0.8, is returned.

## Overall

| Engine / config | Correct | hit@1 | hit@5 | Median error (m) | No result | Conf. when right | Conf. when wrong | p50 ms |
|---|---|---|---|---|---|---|---|---|
| pelias/default | 76% | 77% | 84% | 0.0 | 5% | 0.983 | 0.87 | 13.3 |
| pgeo/api-rule-rank4 | 96% | 94% | 98% | 0.0 | 4% | 0.917 | 0.725 | 45.5 |
| pgeo/api-service-rank4 | 94% | 92% | 97% | 0.0 | 4% | 0.915 | 0.609 | 44.4 |
| pgeo/sql-rank4 | 96% | 94% | 98% | 0.0 | 4% | 0.917 | 0.725 | 39.6 |

## By query quality

| Engine / config | exact | typo | variant | miss |
|---|---|---|---|---|
| pelias/default | 88% | 30% | 85% | 41% |
| pgeo/api-rule-rank4 | 98% | 87% | 95% | 95% |
| pgeo/api-service-rank4 | 95% | 87% | 93% | 94% |
| pgeo/sql-rank4 | 98% | 87% | 95% | 95% |

## By endpoint

| Engine / config | search | structured | autocomplete | reverse |
|---|---|---|---|---|
| pelias/default | 66% | 97% | 87% | 100% |
| pgeo/api-rule-rank4 | 94% | 100% | 95% | 100% |
| pgeo/api-service-rank4 | 92% | 100% | 95% | 100% |
| pgeo/sql-rank4 | 94% | 100% | 95% | 100% |

## By kind of place

| Engine / config | address | town | lake_summit | venue | zip | reverse_address | miss |
|---|---|---|---|---|---|---|---|
| pelias/default | 88% | 81% | 37% | 60% | 100% | 100% | 41% |
| pgeo/api-rule-rank4 | 98% | 96% | 87% | 94% | 98% | 100% | 95% |
| pgeo/api-service-rank4 | 98% | 96% | 76% | 89% | 98% | 100% | 94% |
| pgeo/sql-rank4 | 98% | 96% | 87% | 94% | 98% | 100% | 95% |

## Search detail (endpoint / query type)

| Engine / config | group | n | correct | hit@1 | hit@5 | median error (m) | no result |
|---|---|---|---|---|---|---|---|
| pelias/default | autocomplete/exact | 150 | 87% | 67% | 87% | 0.1 | 0% |
| pelias/default | reverse/exact | 200 | 100% | 100% | 100% | 0.0 | 0% |
| pelias/default | search/exact | 563 | 81% | 81% | 88% | 0.0 | 0% |
| pelias/default | search/miss | 150 | 41% | - | - | None | 39% |
| pelias/default | search/typo | 198 | 30% | 30% | 40% | 5249.5 | 13% |
| pelias/default | search/variant | 149 | 85% | 85% | 87% | 0.0 | 0% |
| pelias/default | structured/exact | 150 | 97% | 97% | 97% | 0.0 | 0% |
| pgeo/api-rule-rank4 | autocomplete/exact | 150 | 95% | 81% | 95% | 0.0 | 0% |
| pgeo/api-rule-rank4 | reverse/exact | 200 | 100% | 100% | 100% | 0.0 | 0% |
| pgeo/api-rule-rank4 | search/exact | 563 | 97% | 97% | 99% | 0.0 | 0% |
| pgeo/api-rule-rank4 | search/miss | 150 | 95% | - | - | None | 39% |
| pgeo/api-rule-rank4 | search/typo | 198 | 87% | 87% | 95% | 0.0 | 2% |
| pgeo/api-rule-rank4 | search/variant | 149 | 95% | 95% | 97% | 0.0 | 0% |
| pgeo/api-rule-rank4 | structured/exact | 150 | 100% | 100% | 100% | 0.0 | 0% |
| pgeo/api-service-rank4 | autocomplete/exact | 150 | 95% | 81% | 95% | 0.0 | 0% |
| pgeo/api-service-rank4 | reverse/exact | 200 | 100% | 100% | 100% | 0.0 | 0% |
| pgeo/api-service-rank4 | search/exact | 563 | 93% | 93% | 97% | 0.0 | 0% |
| pgeo/api-service-rank4 | search/miss | 150 | 94% | - | - | None | 38% |
| pgeo/api-service-rank4 | search/typo | 198 | 87% | 87% | 94% | 0.0 | 2% |
| pgeo/api-service-rank4 | search/variant | 149 | 93% | 93% | 94% | 0.0 | 3% |
| pgeo/api-service-rank4 | structured/exact | 150 | 100% | 100% | 100% | 0.0 | 0% |
| pgeo/sql-rank4 | autocomplete/exact | 150 | 95% | 81% | 95% | 0.0 | 0% |
| pgeo/sql-rank4 | reverse/exact | 200 | 100% | 100% | 100% | 0.0 | 0% |
| pgeo/sql-rank4 | search/exact | 563 | 97% | 97% | 99% | 0.0 | 0% |
| pgeo/sql-rank4 | search/miss | 150 | 95% | - | - | None | 39% |
| pgeo/sql-rank4 | search/typo | 198 | 87% | 87% | 95% | 0.0 | 2% |
| pgeo/sql-rank4 | search/variant | 149 | 95% | 95% | 97% | 0.0 | 0% |
| pgeo/sql-rank4 | structured/exact | 150 | 100% | 100% | 100% | 0.0 | 0% |

```mermaid
xychart-beta
    title "Correct % on exact queries"
    x-axis ["pelias/default", "pgeo/api-rule-rank4", "pgeo/api-service-rank4", "pgeo/sql-rank4"]
    y-axis "%" 0 --> 100
    bar [88, 98, 95, 98]
```

```mermaid
xychart-beta
    title "Correct % on typo queries"
    x-axis ["pelias/default", "pgeo/api-rule-rank4", "pgeo/api-service-rank4", "pgeo/sql-rank4"]
    y-axis "%" 0 --> 100
    bar [30, 87, 87, 87]
```

```mermaid
xychart-beta
    title "Correct % on variant queries"
    x-axis ["pelias/default", "pgeo/api-rule-rank4", "pgeo/api-service-rank4", "pgeo/sql-rank4"]
    y-axis "%" 0 --> 100
    bar [85, 95, 93, 95]
```

```mermaid
xychart-beta
    title "Correct % on miss queries"
    x-axis ["pelias/default", "pgeo/api-rule-rank4", "pgeo/api-service-rank4", "pgeo/sql-rank4"]
    y-axis "%" 0 --> 100
    bar [41, 95, 94, 95]
```

## Accuracy vs fuzziness (rounds F0-F5)

Same 300 base queries at every level: F0 exact, F1 one typo, F2 two, F3 three + abbreviation flips + no commas, F4 F3 + a dropped component / word order, F5 heavy phonetic corruption (tests/accuracy/build_fuzz_rounds.py).

| Engine / config | F0 | F1 | F2 | F3 | F4 | F5 |
|---|---|---|---|---|---|---|
| pelias/fuzz | 82% | 34% | 10% | 4% | 3% | 10% |
| pgeo/fuzz-sql-rank4 | 98% | 87% | 74% | 46% | 45% | 49% |

**address**

| Engine / config | F0 | F1 | F2 | F3 | F4 | F5 |
|---|---|---|---|---|---|---|
| pelias/fuzz | 97% | 45% | 15% | 7% | 4% | 13% |
| pgeo/fuzz-sql-rank4 | 99% | 95% | 88% | 47% | 42% | 59% |

**town**

| Engine / config | F0 | F1 | F2 | F3 | F4 | F5 |
|---|---|---|---|---|---|---|
| pelias/fuzz | 92% | 26% | 6% | 0% | 4% | 12% |
| pgeo/fuzz-sql-rank4 | 98% | 78% | 48% | 30% | 42% | 34% |

**lake_summit**

| Engine / config | F0 | F1 | F2 | F3 | F4 | F5 |
|---|---|---|---|---|---|---|
| pelias/fuzz | 38% | 18% | 4% | 0% | 2% | 2% |
| pgeo/fuzz-sql-rank4 | 98% | 84% | 72% | 56% | 54% | 38% |

**venue**

| Engine / config | F0 | F1 | F2 | F3 | F4 | F5 |
|---|---|---|---|---|---|---|
| pelias/fuzz | 72% | 22% | 6% | 2% | 0% | 4% |
| pgeo/fuzz-sql-rank4 | 96% | 76% | 62% | 50% | 48% | 44% |

```mermaid
xychart-beta
    title "pelias/fuzz: % correct by fuzz level"
    x-axis [F0, F1, F2, F3, F4, F5]
    y-axis "%" 0 --> 100
    line [82, 34, 10, 4, 3, 10]
```

```mermaid
xychart-beta
    title "pgeo/fuzz-sql-rank4: % correct by fuzz level"
    x-axis [F0, F1, F2, F3, F4, F5]
    y-axis "%" 0 --> 100
    line [98, 87, 74, 46, 45, 49]
```

![accuracy vs fuzz](accuracy/accuracy-vs-fuzz.png)

![accuracy by query type](accuracy/accuracy-by-qtype.png)

