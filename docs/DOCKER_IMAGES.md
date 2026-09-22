# Published container images - design notes

Status: **TODO, design open** (added 2026-09-22). Goal: make pgeo deployable with `docker pull`
rather than "clone the repository and run four scripts".

## What exists today

- The build (`pgeo-load build`) runs on a workstation: DuckDB and GDAL stage the raw sources,
  PostgreSQL does the enrichment, indexes and an atomic schema swap. 13 minutes, 5.5 GB peak.
- The result ships as a `pg_dump` of the `pgeo` schema (`scripts/pgeo_dump.sh`, 117 MB), which
  the Ansible `pgeo_runtime` role restores on the VM. The VM never builds from raw data.
- The serving side is two pinned images: `postgis/postgis:18-3.6` and `postgrest/postgrest:v16.3`,
  plus nginx at the edge. Runtime floor 0.25 vCPU / 1.4 GB (report Section 3.12).

## Proposed shape (to be confirmed - see the open questions)

1. **`pgeo-build`** - the pre-processor. Everything needed to turn raw sources into a built
   database: the Python loader, DuckDB, GDAL, and a PostgreSQL to build into. Meant for a
   workstation; produces an artifact.
2. **`pgeo`** (all-in-one) - PostgreSQL + PostGIS + the pgeo schema functions + PostgREST, with the
   pre-processors included so a single centralised host can also refresh its own data.
3. **A transport** from the build artifact to a VPS running the server side, with an atomic
   online swap on arrival (already a future-work item in its own right).

## Open questions

Recorded here so the answers become the design. See the conversation of 2026-09-22.
