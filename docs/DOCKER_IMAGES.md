# Published container images - design

Status: **designed, not built** (2026-09-22; multi-region and the data-image option added the same day). Operator's page: `DOCKER-DEPLOY.md`. Goal: pgeo deployable with `docker pull` rather than
"clone the repository and run four scripts". Decisions below were taken in conversation on
2026-09-22; the remaining open points are listed at the end.

## What exists today

- The build (`pgeo-load build`) runs on a workstation: DuckDB and GDAL stage the raw sources,
  PostgreSQL does the enrichment, indexes and an atomic schema swap. 13 minutes, 5.5 GB peak.
- The result ships as a `pg_dump` of the `pgeo` schema (`scripts/pgeo_dump.sh`, 117 MB), which
  the Ansible `pgeo_runtime` role restores on the VM. The VM never builds from raw data.
- Serving is two pinned images, `postgis/postgis:18-3.6` and `postgrest/postgrest:v16.3`, with
  nginx at the edge. Runtime floor 0.25 vCPU / 1.4 GB (report Section 3.12).

Everything below is packaging and a transport around those pieces; no new engine code.

## Decisions

| Question | Decision | Why |
|---|---|---|
| Transport from workstation to VPS | **Push over SSH/rsync**, from the build image | The operator already has SSH to their VPS; no registry account or bucket to operate for data. The registry "data image" is kept as an option - `TODO.md` item 2 describes it - and adds beside this without changing the build or the swap. |
| The all-in-one | **A compose bundle**, not a single container | Keeps the measured two-container design; each image stays stock and upgrades on its own. |
| The pre-processor's database | **Self-contained** - the image carries its own PostgreSQL | A workstation needs only Docker. |
| Architectures | **amd64 only** to start | Ship what is measured; arm64 when someone needs it. |
| The push command | **One command in the build image** | `pgeo-build push` dumps, rsyncs, and triggers the swap. Ansible keeps working for this project's own VM 120 but is not required of adopters. |
| Applying a new dump on the VPS | **Atomic online swap** | Restore into a build schema, rename, drop the old - the loader's own trick. A refresh is a non-event. This delivers the "atomic online restore" future-work item. |
| Raw sources | **The image fetches them**, into a cache volume | One flow for a first-timer; the volume makes the second build cheap. |
| Regions | **Parameterised** (`PGEO_BUILD`, as the scripts already are), **one or several** | A New Hampshire build becomes a flag, not a fork; a three-state build is a list. Ranking stays tuned on Maine, as the report says. |

## The three artifacts

### 1. `pgeo-build` - the pre-processor (workstation)

Contents: PostgreSQL 18 + PostGIS 3.6 (the same `postgis/postgis:18-3.6` base), the `pgeo`
Python package with DuckDB and GDAL, `pgeo/sql/*`, and the fetch, dump and push scripts.
About 1.2 GB. Needs Docker and about 6 GB of memory while building.

```bash
# first time: fetch (cached in ./data), build, dump - one command
docker run --rm -v ./data:/data -e PGEO_BUILD=me \
  git.wharf.example/jcz/pgeo-build:0.10 build
#   -> /data/dumps/pgeo-us-maine-20260922.dump   (117 MB)

# ship it to a VPS running the serving bundle (atomic swap on arrival, no downtime)
docker run --rm -v ./data:/data -v "$SSH_AUTH_SOCK:/ssh-agent" -e SSH_AUTH_SOCK=/ssh-agent \
  git.wharf.example/jcz/pgeo-build:0.10 push deploy@vps.example.org
```

Subcommands: `fetch` (sources missing from the cache), `build` (fetch + load + dump),
`dump` (from the image's own database), `push <host>` (rsync the newest dump, then run the swap
on the far side), `accuracy` (the 1,560-case gate against the built database, so a region build
can be checked before it ships). The internal PostgreSQL is started for the duration of the
command and lives in `/data/pg`, so a rebuild reuses nothing and a re-dump costs nothing.

### 2. `pgeo` - the serving bundle (VPS, and the "all-in-one")

A published `compose.yml` that pins three services:

| Service | Image | Role |
|---|---|---|
| `db` | `postgis/postgis:18-3.6@sha256:...` | the database; `/srv/pgeo/pg` volume |
| `api` | `postgrest/postgrest:v16.3@sha256:...` | the pure-SQL front end, bound to loopback |
| `edge` | `nginx` with the project's config | path allowlist, rate limits, security headers, the demo page, `/v1/*` -> `/rpc/v1_*` |

plus, **optionally**, `build` = the `pgeo-build` image with a profile (`--profile build`), so a
centralised host can refresh its own data in place: `docker compose run build build && docker
compose run build swap`. That is the "all-in-one": the same bundle, with the pre-processor
switched on, for the slower-but-central deployment.

The bundle starts empty and serves a clear 503 with a one-line hint until a dump has arrived.
The swap is a script in the `db` image's `/docker-entrypoint-initdb.d`-adjacent tooling:

```
pgeo-swap <dump>:  pg_restore into schema pgeo_incoming
                   -> run pgeo/sql 040-060 (functions are versioned with the dump)
                   -> BEGIN; ALTER SCHEMA pgeo RENAME TO pgeo_old; ALTER SCHEMA pgeo_incoming
                      RENAME TO pgeo; COMMIT; DROP SCHEMA pgeo_old CASCADE
                   -> NOTIFY pgrst, 'reload schema'
```

The rename is one transaction; PostgREST's connections see the old schema until it commits and
the new one after. This is what the loader does locally today.

### 3. The pipeline between them

```
workstation                              VPS
-----------                              ---
pgeo-build build                         (bundle running, serving the previous data)
  fetch -> stage -> load -> swap -> dump
pgeo-build push deploy@vps  ---rsync--->  /srv/pgeo/incoming/<dump>
                            ---ssh----->  docker compose exec db pgeo-swap <dump>
                                          (service up throughout; old schema dropped after)
```

The push is idempotent and resumable (rsync), verifies the dump's checksum on the far side
before swapping, and refuses to swap a dump built by a newer `pgeo` schema version than the
bundle's functions expect (the version is stamped in the dump's `build_info`).

## Region parameter, including several at once

`PGEO_BUILD` takes one state code or a comma-separated list: `me`, or `me,nh,vt`. It is the same
name `scripts/build_region.sh --build` takes, and the facts come from the same place - a row per
state in `regions/regions.json`, which `scripts/gen_regions.py` fills from the Census boundary
file, the Who's on First distribution and the OpenAddresses source listing:

| Field | Maine's value | Used by |
|---|---|---|
| Geofabrik extract | `maine` (under `north-america/us`) | OSM fetch |
| OpenAddresses collection | `us/me` | OA fetch |
| Who's On First filter | region id 85688769 | WOF bounding |
| Overture bounding box | Maine's | Overture fetch (DuckDB reads Parquet by bbox) |
| State code, FIPS | ME, 23 | `region_a`, county FIPS, `/v1/address` |
| Accuracy set | `tests/accuracy/cases.json` | `pgeo-build accuracy` |

Adding a state is adding a row. Nothing in the query path knows the region.

**A multi-region build** is the union of its rows, and four things have to be done as a merge
rather than a repeat:

1. **Sources are fetched per region and staged into one set of tables.** Geofabrik extracts are
   loaded one after another into the same staging tables (no `osmium merge` needed - overlap at
   state borders is a handful of ways, and the enrich step's dedupe already handles a feature that
   appears twice). OA collections and Overture bboxes likewise. Who's On First is one download
   filtered by the union of region ids.
2. **Admin lookup, labels and the town table span all regions**, so "Portland" resolves to the
   right one by state, and a label says which. `region_a` is per feature, not per build.
3. **The `ac_prefix` top-25 and the street-name table are built over the whole set**, once, at
   the end - they are global by construction, so a merge is free.
4. **The accuracy check is per region.** `pgeo-build accuracy` runs each region's set that
   exists and reports them separately; Maine's 95.8% says nothing about New Hampshire until New
   Hampshire has cases of its own (report Section 5: "one state, tested").

What a larger build costs is measured, not guessed: report Section 3.6.1 found pgeo loses about
four times its capacity across Maine's own data range where Pelias loses two, and Section 3.16
then raised every pgeo figure by a third. A three-state build should expect roughly that curve,
and the build machine's 5.5 GB peak grows with the largest single source, not the sum. The
serving side's memory does not: Section 3.4.4 found the hot working set far smaller than the
tables. Both numbers should be re-measured on the first multi-state build and written into the
report as the New Hampshire future-work item.

## Registries and tags

- Forgejo container registry at the project's Forgejo (canonical), mirrored to GHCR when the
  repository is published on GitHub. Same tags on both.
- `pgeo-build:<pgeo semver>` and `pgeo-build:latest`; the compose bundle pins the serving images
  by digest, and the bundle file itself is released with the repository tag.
- Built by CI on a tag (Forgejo Actions; GitHub Actions mirroring), amd64. Images pinned by
  digest in the published compose file, as the Ansible role already does.

## What this delivers against the report

- "Fewest components" (G6) becomes literal for an adopter: Docker, one command, one push.
- The runtime floor is unchanged (the bundle *is* the measured configuration).
- The "atomic online restore" future-work item is delivered as `pgeo-swap`.
- The build-machine requirement (5.5 GB) stays on the workstation side, where the report put it.

## Estimate

3-5 days: the images and compose file (1), `pgeo-swap` and the push with its checks (1), the
region table and fetch-with-cache (1), CI and registry publishing (0.5-1), documentation and a
clean-VPS rehearsal (0.5-1).

## Still open

- **SSH credentials for the push.** Mounting the agent socket (as above) is the least-privilege
  option; a mounted key file is the fallback for CI. Confirm which to document as primary.
- **The edge in the bundle.** Included above because the report's security posture depends on
  it (rate limits, allowlist, headers). An adopter with their own reverse proxy would disable
  the service; the compose file should make that a one-line change.
- **Authorization** is still Phase 11. The bundle ships with the edge's LAN allowlist and no API
  keys, exactly as VM 120 runs today; the report already says this is the one open security item.
- **Dump compatibility across PostgreSQL majors.** `pg_restore` accepts a dump from the same or
  an older major, so a bundle on PG 18 accepts a workstation dump from PG 18 - both images share
  the base, so this is enforced by construction. Worth a check in `pgeo-swap` all the same.
