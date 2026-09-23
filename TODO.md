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

## 3. ~~Multi-region builds~~ Done in the pipeline 2026-09-22; still to do in the images

The build pipeline is now parameterised by *build*: one or more US states, named in
`regions/regions.json`, which `scripts/gen_regions.py` fills from the Census boundary file, the
Who's on First distribution and the OpenAddresses source listing. Every step takes `--build`
(`ny`, or a bare list such as `me,nh,vt`), and nothing in `scripts/`, `prep/` or `pgeo/` names a
state any more. Proven by building New York - about ten times Maine - beside the Maine stack on
one workstation; `GETTING_STARTED.md` is the procedure, written against that run.

What the parameterisation changed, beyond paths:

- OpenAddresses and Who's on First are now fetched straight from their publishers, so a pgeo
  build no longer needs the Pelias CLI for anything.
- ZCTAs are assigned to states by Census land area rather than by ZIP prefix. The prefix range
  was a Maine fact; the land-area rule is the same question asked of the data, and it also fixes
  Maine, which gains 03579 (846 km2 in Oxford County against 611 in Coos County, NH).
- Postcodes in source data are validated against the prefixes a build has to itself, so a
  neighbour's ZIPs cannot leak in - New York holds 06390 on Fishers Island, and a plain prefix
  test there would have admitted every Connecticut 063xx.

A multi-state build was then run for real, through the one command the guide gives
(`scripts/build_region.sh --build vt,nh`): 1,074,744 raw features from two OSM extracts appended
into one schema, 285,846 of them on the Vermont side and 121,554 on the New Hampshire side, and
all sixteen known answers passing on both front ends - including "Burlington, Vermont" ->
`Burlington, VT, USA` and "Concord, New Hampshire" -> `Concord, NH, USA`, which is the thing a
multi-state build has to get right and a bare name check would not notice.

Still to do: the container images take the same parameter (`docs/DOCKER_IMAGES.md`, "Region
parameter"). Per-machine server tuning is shared by every stack on a workstation, which does not
arise on a host that serves one build.

## 4. ~~The point-in-polygon step is the whole cost of a bigger region~~ Done 2026-09-22

It was: four probes per raw feature against whole admin polygons, on one core. A county outline
is tens of thousands of vertices and every candidate cost a full ST_Intersects.

ST_Subdivide cuts the polygons into pieces of at most 128 vertices, and one probe replaces the
four. Measured on New York, 200,372 points against 8,619 polygons:

| Form | Time | Answers |
|---|---|---|
| Four probes against whole polygons (before) | 62.6 s | - |
| One probe against whole polygons | 39.9 s | identical |
| One probe against subdivided pieces | **7.8 s** | identical |

Maine's whole build went from 1,152 s to 309 s, and the step itself from about 18 minutes to
4. Every hierarchy value and every label is identical row for row; the accuracy set moved by
two cases, one each way, both from the duplicate-address tie-break below rather than from this.

The same trick would help reverse geocoding, which does its own point-in-polygon against
`admin` at query time. Not done: it needs the subdivided table kept rather than dropped, and
its own measurement.

## 5. Alaska crosses the antimeridian

Its box runs -179.3 to 179.95, so as min/max longitude it spans the planet: the Overture
prefilter would pull the world, the basemap extract would be the world, and the coordinate check
in prep would accept anything. Both region resolvers refuse an Alaska build with that reason.

Handling it means carrying two boxes through every bbox step - the Overture S3 filter, the
pmtiles extract, prep's validation - or working in a projected space. Nobody has asked for
Alaska; the refusal is honest until they do. Hawaii is fine: islands, but one box.

## 6. ~~An explicit tie-break rule at build time~~ Done 2026-09-22; query time still open

**Build time, done.** The address dedupe kept one row per house number, street and town and
ordered only by source, so the winner was whichever the table happened to hold first. Two builds
of the same data disagreed on 10,769 rows, and a reverse-geocoding case that had passed for
months began to fail because 101 Hancock St, Rumford landed 25 m from its seven agreeing
siblings.

Chasing that found the larger bug: normalisation makes "18 N St" and "18 North St" one key, and
Bangor has both, 5 km apart, so the dedupe was merging two real addresses and discarding one.
Rows sharing a key are now clustered at about 200 m first, and each cluster keeps one row -
Maine gains 4,088 addresses. Within a cluster the majority point wins, and the source id settles
the rest.

Two consecutive builds now produce 908,347 rows with identical gids, against 10,769 differing
before.

**Query time, still open.** Where candidates tie on score the winner is still decided by physical
row order (report Section 3.16.3), which is about one case of noise in the accuracy figure
("Stevenns Corner"). Any rule - source priority, then id - changes some current outputs, so it
needs its own accuracy run.

## 7. ~~Rewrite history before publishing to GitHub~~ Done 2026-09-22

The local inventory file had been tracked for a day, and the sanitisation of 2026-09-21 had left
every earlier commit's copies of the private details in place (some twenty files: the plan, the
project log, the Proxmox scripts, the Caddy config, the inventory). The whole history was
rewritten with `git filter-repo --replace-text`, mapping each real value to the placeholder HEAD
already used - LAN addresses to 192.0.2.x, hostnames to *.example.org - and force-pushed. A fresh
clone of the remote holds 149 commits and 38 tags with zero blobs containing any private token.
Commit hashes before this date changed; a backup bundle of the old history is kept outside the
repository. Three tokens the original sanitisation had missed at HEAD (a tailnet address, the
VM's MAC, the internal DNS name) went in the same pass.

## 8. Authorization: single sign-on

API keys at the edge are done (0.19.0, report Section 3.13.2). A key names a client - the
dispatch application, the demo - not a person. Single sign-on for the people behind the clients
is the remaining half; it costs a component and is not needed for three users in one department.
