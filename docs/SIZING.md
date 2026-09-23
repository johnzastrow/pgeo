# Sizing a pgeo deployment

Measured, not estimated. Two regions were built and served on the same workstation (Ryzen 5
3600, 31 GB, NVMe), which is also the machine the report's capacity tests used.

## The two ends of the range

|  | Maine | New York |
|---|---|---|
| Population served | 1.4 M | 19.6 M |
| Area | 91,600 km2 | 141,300 km2 |
| **Features in the database** | **906,102** | **7,246,923** |
| addresses | 702,701 | 6,024,618 |
| venues | 132,373 | 992,120 |
| streets | 67,312 | 217,945 |
| admin, postcodes, neighbourhoods | 3,716 | 12,240 |

New York holds eight times Maine's data. Everything below scales with the feature count, not
with the area or the population.

## Building

The workstation that builds needs far more than the server that serves.

|  | Maine | New York |
|---|---|---|
| Downloads (`data/raw/<build>`) | 0.6 GB | 3.0 GB |
| Who's on First (shared by every build, once) | 5.2 GB | 5.2 GB |
| Basemap for the demo page (optional) | 0.3 GB | 1.5 GB |
| Database while building | 2.6 GB | 11 GB |
| **Build time** | **5 min** | **44 min** (measured, 2,643 s) |

Build time is dominated by one step, the admin point-in-polygon pass, which runs on a single
core; the rest is I/O. Before the `ST_Subdivide` change of 2026-09-22 the same builds took
19 minutes and 112 minutes respectively, so a build you time against an older version will look
very different.

Memory during the build is modest - the tuning profile's `maintenance_work_mem` (1 GB on the
workstation profile) plus PostgreSQL's shared buffers. It is disk and one fast core that matter.

## Serving

|  | Maine | New York |
|---|---|---|
| Database | 1.0 GB | 7.0 GB |
| of which the feature table | 804 MB | 6.5 GB |
| of which its indexes | 231 MB | 1.8 GB |
| On disk including WAL | 2.6 GB | 11 GB |
| **Deployable dump** | **117 MB** | **892 MB** |

The server restores the dump rather than building, so it needs the database size plus room for
the restore, not the build's working space. **A New York deployment wants 20 GB of disk**;
Maine fits comfortably in 10.

### Memory and cores

The report measured Maine on four VM shapes; the capacity figures there are for Maine. New York
has not been load-tested, and the honest statement is that the numbers below are Maine's:

| Profile | vCPU | RAM | Concurrent users within target |
|---|---|---|---|
| P1 | 1 | 1 GB | 32 |
| P2 | 2 | 2 GB | 64 |
| P4 | 4 | 4 GB | 128 |
| PM | 4 | 8 GB | 192 |

What changes with a larger region is the working set. PostgreSQL will serve New York from 1 GB
of RAM, but the indexes alone are 1.8 GB, so a 1 GB machine reads from disk on most queries
rather than from cache. Single-query latency measured on the workstation for New York:

| Query | Time |
|---|---|
| `search?text=buffalo` | 69 ms |
| `search?text=albany` | 123 ms |
| `search?text=350 5th ave new york` (full address parse) | 476 ms |

Maine's equivalents are 15-40 ms. The gap is cache behaviour and candidate counts, not a
different amount of work per candidate.

**Recommendation for a state the size of New York**: 2 vCPU and 4 GB to keep the indexes
resident, 20 GB of disk, and the `medium` or `large` tuning profile. For a state the size of
Maine, 1 vCPU and 1 GB is genuinely enough, which is the claim the report tests.

## What drives each number

- **Disk** follows the address count almost exactly: addresses are 83% of New York's features.
- **Build time** follows the feature count and the number of admin polygons, because the
  point-in-polygon pass is per feature.
- **Query latency** follows how much of the index fits in RAM.
- **The dump** compresses to about an eighth of the database.

A build can be limited to a bounding box rather than whole states, which scales all of these
down together; see `TODO.md`.
