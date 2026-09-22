# TODO

The report's Section 6 is the full future-work table with effort and payoff. This file holds the
items with enough design behind them to start, and the designs themselves where they are short.

## 1. Published container images

Design: `docs/DOCKER_IMAGES.md`. Operator's page: `DOCKER-DEPLOY.md`. Decided 2026-09-22:
`pgeo-build` (self-contained pre-processor, amd64), a serving compose bundle with an optional
build profile as the all-in-one, SSH/rsync push from workstation to server, atomic online swap on
arrival, sources fetched into a cache volume, region-parameterised including multi-region builds.
3-5 days.

## 2. The data-image variant of the transport (kept as an option)

SSH push was chosen for now. The registry "data image" is the alternative, deferred rather than
rejected; it costs nothing extra to add later because the artifact it ships already exists.

**How it would work.** A built database is a single `pg_dump` file. That file can be published as
a container image with no runtime in it at all:

```dockerfile
FROM scratch
COPY pgeo-us-maine-20260922.dump /pgeo.dump
LABEL org.opencontainers.image.version="us-maine-20260922" \
      dev.pgeo.schema="0.10.0" dev.pgeo.regions="us/maine" dev.pgeo.sha256="..."
```

pushed as `pgeo-data:us-maine-20260922` (and `pgeo-data:us-maine`, the moving tag). About
120 MB, the size of the dump; registries store layers content-addressed, so a rebuild with little
change still costs a full layer, but nothing more.

On the server, the bundle gains a one-shot service:

```yaml
  data:
    image: git.wharf.example/jcz/pgeo-data:us-maine   # or a pinned date tag
    profiles: [refresh]
    volumes: [incoming:/incoming]
    command: ["cp", "/pgeo.dump", "/incoming/"]       # FROM scratch has no shell; use a tiny copier image, or
                                                      # mount the image as a volume (docker >= 25: --mount type=image)
```

and `docker compose --profile refresh run --rm data && docker compose exec db pgeo-swap
/incoming/pgeo.dump` does what `pgeo-build push` does today, from the server's side.

**What it buys.**

- No SSH from the workstation to any server. The workstation needs only push access to the
  registry it already pushes the build image to; a server needs only pull access.
- One server can serve many; many servers can pull one build. Fan-out is a registry feature.
- Versioning and rollback for free: every build is a tag; rolling back is swapping in the
  previous tag. Provenance is the image digest, and the registry keeps the history.
- Works through CI: a scheduled job builds and pushes `pgeo-data`; servers refresh on their own
  schedule. Nothing needs to be running on a workstation.
- Air-gapped or offline servers can be fed with `docker save` / `docker load`.

**What it costs.**

- A registry account for data, not just code, and its storage: 120 MB per build kept.
- The swap must verify the dump's schema version against the bundle's functions, exactly as
  the SSH push does; the label carries it.
- `FROM scratch` images have no shell, so getting the file out needs either a tiny copier stage
  or Docker's image mounts. Both are small.

**When to add it.** As soon as there is a second server, or a server the workstation cannot
reach by SSH, or a wish to schedule builds in CI. The `pgeo-build` image would gain a
`publish` subcommand beside `push`; the bundle would gain the `refresh` profile; nothing about
the build or the swap changes.

## 3. Multi-region builds

Added 2026-09-22 to the images design: `PGEO_REGION` accepts a list. See `docs/DOCKER_IMAGES.md`,
"Region parameter", for what a multi-region build has to merge and what the report says about
capacity as data grows.

## 4. An explicit tie-break rule

Where candidates tie on score, the winner is currently decided by physical row order (report
Section 3.16.3). It is deterministic per build but not chosen, and it puts about one case of
noise into the accuracy figure between builds and hosts ("Stevenns Corner", Section 5.7 of
`docs/PERFORMANCE_OPTIMIZATION.md`). Any rule - source priority, then id - changes some
current outputs, so it is a deliberate change with its own accuracy run.

## 5. Authorization (Phase 11)

The one open security item in the report (Section 3.13): hashed API keys checked at the edge,
issued out of band. Blocking for LANCER's use of the service.
