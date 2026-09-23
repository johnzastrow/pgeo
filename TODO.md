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

## 5. Who's on First loses places that have no region ancestor

Admin places are selected as descendants of the build's region id, which is authoritative when
the ancestry is complete. It is not always complete: Jarbidge, Nevada is in Who's on First as a
current locality at 41.87, -115.43, and its only ancestors are the United States and itself. No
region, so the build never sees it.

Places inside a build's box with no region ancestor at all:

| Build | Missed |
|---|---|
| Maine | 1 |
| New York | 5 |
| Nevada | 62 |
| Arizona | 72 |

Small, but not evenly spread, and not evenly important. The Arizona list is Allenville, Aripine,
Artesa, Chutum Vaya, Cibecue Creek, Bradshaw City and their like - unincorporated settlements,
historic camps and tribal communities, which is the category a rural geocoder is most asked
about and the category with the most Indigenous names.

**The fix** is a point-in-polygon fallback: a current US locality or localadmin whose point falls
inside the build's region polygon, and which the ancestry did not already bring in, is part of the
build. That also excludes the ones a bounding box would wrongly add - about half the Nevada list
is actually in California, because the box overlaps it. It needs its own accuracy run, because
adding places changes what a search can return.

## 6. Alaska crosses the antimeridian

Its box runs -179.3 to 179.95, so as min/max longitude it spans the planet: the Overture
prefilter would pull the world, the basemap extract would be the world, and the coordinate check
in prep would accept anything. Both region resolvers refuse an Alaska build with that reason.

Handling it means carrying two boxes through every bbox step - the Overture S3 filter, the
pmtiles extract, prep's validation - or working in a projected space. Nobody has asked for
Alaska; the refusal is honest until they do. Hawaii is fine: islands, but one box.

## 7. ~~An explicit tie-break rule at build time~~ Done 2026-09-22; query time still open

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

## 8. ~~Rewrite history before publishing to GitHub~~ Done 2026-09-22

The local inventory file had been tracked for a day, and the sanitisation of 2026-09-21 had left
every earlier commit's copies of the private details in place (some twenty files: the plan, the
project log, the Proxmox scripts, the Caddy config, the inventory). The whole history was
rewritten with `git filter-repo --replace-text`, mapping each real value to the placeholder HEAD
already used - LAN addresses to 192.0.2.x, hostnames to *.example.org - and force-pushed. A fresh
clone of the remote holds 149 commits and 38 tags with zero blobs containing any private token.
Commit hashes before this date changed; a backup bundle of the old history is kept outside the
repository. Three tokens the original sanitisation had missed at HEAD (a tailnet address, the
VM's MAC, the internal DNS name) went in the same pass.

## 9. Authorization: single sign-on

API keys at the edge are done (0.19.0, report Section 3.13.2). A key names a client - the
dispatch application, the demo - not a person. Single sign-on for the people behind the clients
is the remaining half; it costs a component and is not needed for three users in one department.

## 10. Only OpenStreetMap is clipped to the region polygon

The Maine build held 4,270 OpenStreetMap features outside Maine, labelled `, ME, USA`, and the
fix of 2026-09-22 clips OpenStreetMap to the region polygon. The other sources are still filtered
by the build's bounding box alone, so a margin survives. Measured on Arizona plus Nevada:

| Source | Outside the region | Beyond 1 km | Furthest |
|---|---|---|---|
| GNIS | 297 | 227 | 66.5 km |
| OpenAddresses | 240 | 139 | 9.8 km |
| Overture | 115 | 0 | 0.3 km |
| OpenStreetMap (streets) | 1,037 | 148 | 17.2 km |

The GNIS and OpenAddresses rows are a genuine leak: features in Utah, New Mexico and Sonora that
the box admitted and nothing removed.

The OpenStreetMap rows are a different thing and probably fine: they are all streets, and a street
that crosses the border is kept whole while its representative point is the centroid of the merged
geometry, which can land outside. Removing those would remove real, wanted streets.

### A single-state build relabels the leak as its own

Arizona plus Nevada leaves those features with no state, which is honest. A single-state build
does not, and this is the part that matters. The 0.12.0 rule - a feature with no county falls back
to the build's own state when the build has exactly one - turns every leaked feature into a claim:

| Build | Outside the region | Claiming the build's state | Furthest |
|---|---|---|---|
| Maine | 2,516 | **2,516** | 15.3 km |
| New York | 3,322 | **3,322** | 67.6 km |
| Vermont + New Hampshire | 2,861 | 0 | - |
| Arizona + Nevada | 1,689 | 0 | 66.5 km |

So New York answers `Ringwood River, NY, USA` for a river in New Jersey, and holds Canadian
features from the Akwesasne reserve - `Akwesasne Canada Post`, `Rue Akwesasne` - on the Quebec and
Ontario sides of a territory the border runs through. Maine's 2,516 are gentler: all GNIS, all
rivers and streams that genuinely are Maine's and whose GNIS point sits just over the line
(Meduxnekeag River, Prestile Stream, Aroostook River, all flowing into New Brunswick).

This is the Maine defect of section 4.1 in the report, reduced but not gone: clipping
OpenStreetMap removed the bulk of it, and the single-state fallback added in the same release
quietly re-creates it for whatever the other sources still admit. The fallback is right for
features genuinely inside the region whose county lookup failed, and wrong for everything
outside it - so it should be conditioned on the feature being inside the region polygon, which
is the same test the clip already performs.

**The fix** is to apply the clip already written in `sql/025_osm.sql` to the GNIS, OpenAddresses
and Overture staging paths, on the point rather than the line, leaving the street centroid case
alone. It needs its own accuracy run: the border margin includes places a query near the state
line might legitimately want.

## 11. Two front ends, two copies of the parser, and no check that they agree

The FastAPI front end parses in Python (`pgeo/api/parse.py`, baked into the container image);
the pure-SQL front end parses in PL/pgSQL (`geocode.parse_rule`, stored in the database). The
five-digit house-number fix of 2026-09-23 had to be written into both, and `pgeo-load functions`
updates only the second. A container left on an older image therefore serves the old parser while
the database serves the new one, and nothing reports the disagreement.

This happened during that fix and was caught by accident: the Vermont-plus-New Hampshire API was
still on a 2026-09-22 image, and answered "Burlington, Vermont" with a venue named
`Burlington Vermont` while the SQL edge answered `Burlington, VT, USA`. Two further traps sat
behind it:

- **The known-answer check is a substring test** (`want in label`), so the wrong answer passed:
  "Burlington" is present in `Burlington Vermont, South Burlington, VT, USA`. A build can report
  16 of 16 while three of its town queries return venues. The check should compare the layer as
  well as the label.
- **The accuracy runner did not record which endpoint it measured**, so a score could not be
  traced to a front end. Fixed 2026-09-23: the result file now carries `base`.

**The fix** is a contract test that puts the same queries through both front ends and fails on
any disagreement, run as part of the build gate. It would have caught all of this at the point
the image went stale rather than three regions later. The deeper fix - one parser, called from
both - is a larger change worth costing separately.

## 12. A misspelled town name answers with a venue

Pinning the expected layer in the known answers (2026-09-23) turned four passes into failures.
They are two distinct defects, and neither was visible while the check compared labels as
substrings.

**A misspelling loses the locality entirely.** The right place is not merely outranked; it is
absent from the top results:

| Query | Build | Answer | Wanted |
|---|---|---|---|
| `Albny, NY` | New York | `Albany, NY - Albany.com`, Colonie (venue, 0.53) | Albany the city |
| `Manchestr, NH` | VT + NH | `Manchester, NH`, Manchester (venue, 0.445) | Manchester the city |
| `Tuscon, AZ` | AZ + NV | `One Hope - Tuscon`, Valencia West (venue, 0.494) | Tucson the city |

The cause is that venue names routinely embed their own city and state - businesses are listed as
"Albany, NY - Albany.com", "Manchester, NH", "Tuson Az" - so a fuzzy match on "City, ST" scores
those venues above the bare locality name, which contains only "Albany". The exact-match spelling
is what normally saves the locality, and a misspelling removes it. Maine passes `Portlnd, ME`
only because it has fewer such venues.

**A county outranks the city of the same name.** `Albany, New York` returns Albany County, not the
city of Albany, both at confidence 1.0 - a tie broken by physical row order (section 7). The same
shows as `Cattaraugus, NY` returning the county rather than the village. A user typing a bare
"Name, State" means the populated place far more often than the county.

**The fix** for the second is a layer priority in the tie-break: locality before county before
venue at equal confidence. The first is harder and needs its own accuracy run, since boosting
localities on fuzzy matches will change what many queries return.
