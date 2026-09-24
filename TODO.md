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

## 10. ~~Only OpenStreetMap is clipped to the region polygon~~ Done 2026-09-23 (the border strip it left is section 15)

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

## 13. ~~Two builds against one database destroy each other silently~~ Done 2026-09-23

Running `scripts/pgeo_rebuild.sh --build vt-nh` twice at once (my own mistake on 2026-09-23, a
backgrounded job I believed had failed) produced a build that reported success and was missing
40% of its streets: Vermont came out with 193 against New Hampshire's 40,300, and there was no
Main Street in Burlington.

The mechanism is that each build does `DROP SCHEMA IF EXISTS pgeo_build CASCADE; CREATE SCHEMA
pgeo_build`. The second build dropped the first one's schema mid-load, so the first extract's
`ogr2ogr` lost its table underneath it:

```
ERROR 1: CREATE TABLE "pgeo_build"."osm_lines" (...)
ERROR:  duplicate key value violates unique constraint "pg_type_typname_nsp_index"
ERROR 1: Terminating translation prematurely after failed translation of layer lines
```

One process raised and died; the other carried on and printed `build complete in 206s`. A clean
single rebuild of the same region gives 67,901 streets, so nothing is wrong with the data or the
loader - only with what happens when two of them meet.

**Done** in 0.14.0: a session-level advisory lock on the database, taken for the length of a
build, and `ogr2ogr` errors now fail the build instead of exiting zero over a half-loaded
extract. The original plan follows.

**The fix** is a session-level advisory lock on the database, taken for the length of a build:
`SELECT pg_try_advisory_lock(...)` at the start, refuse with "a build is already running against
this database" when it is not granted. Cheap, and it releases itself if the process dies.

Worth doing at the same time: the build should fail when `ogr2ogr` writes `ERROR 1` even where
the exit status is zero, and the per-source counts at the end should be compared against the
staged counts, so an extract that silently contributed nothing is caught by the build rather than
by someone noticing a missing street months later.

## 14. The next four regions, in order

Every region built so far has found at least one general defect the previous ones could not, and
the rate is falling but not zero: Maine defined the rules, New York found eleven, Vermont plus
New Hampshire found one, Arizona plus Nevada found one, and sharpening the known answers for the
fourth found two more. These four batches are chosen to keep that going - each one probes
something the first four could not.

All nine states are already in `regions/regions.json` with a bounding box, FIPS code, Geofabrik
slug, Who's on First region id and OpenAddresses sources, so each is one `scripts/build_region.sh
--build <states>` away. Each needs known answers written first (`pgeo/tuning/verify/<build>.json`,
with layers pinned per section 11) and an accuracy set generated after.

| Batch | OpenAddresses sources | Against what is built |
|---|---|---|
| Texas + Louisiana + Arkansas | 107 | Arizona + Nevada was 36 |
| Michigan + Wisconsin + Minnesota | 186 | the largest by source count |
| Washington + Idaho | 77 | |
| District of Columbia | 1 | Maine was 2 |

### 14.1 Texas + Louisiana + Arkansas

**Louisiana has parishes, not counties**, and this is the one defect already identifiable without
building anything. Who's on First records all 64 of them under placetype `county` with bare names
("Acadia", not "Acadia Parish"), and `pgeo/src/pgeo/api/app.py:262` appends the word:

```python
"county": r["county"] if not r["county"] or r["county"].endswith(" County") else f"{r['county']} County",
```

with the comment "Pelias (WOF) names counties 'Cumberland County'" - a Maine fact. Louisiana will
answer `Acadia County`, which is not a thing. Alaska would answer `Anchorage County` for a
borough, and the District of Columbia `District of Columbia County`. The fix is to carry the
county-equivalent's own suffix from Who's on First rather than to assume one.

Also here: Texas is 254 counties, twice any state built so far, and the point-in-polygon pass
scales with the number of admin polygons rather than the area. Spanish names throughout Texas and
French and Cajun ones through Louisiana - Natchitoches, Atchafalaya, Thibodaux, Plaquemines -
test the same ground as Maine's French and Wabanaki names, in a different orthography. Louisiana
and Texas both border Mexico or the Gulf, so the border-margin question of section 10 applies.

### 14.2 Michigan + Wisconsin + Minnesota

The largest batch by source count, and the one that tests geometry rather than names.

**Michigan is two peninsulas** separated by water, and **Minnesota's Northwest Angle** is an
exclave north of the 49th parallel reachable by land only through Manitoba - Minnesota's box
reaches 49.55°N for that reason. Both are questions for the region clip of section 10 and the
point-in-polygon pass: a region whose polygon is in disconnected pieces, with a water gap that a
buffer could bridge. Note that Who's on First stores these as a single `Polygon` rather than a
`MultiPolygon`, which is worth confirming before relying on either reading.

All three have long water boundaries with Ontario through the Great Lakes, so Canadian features
sit close to the line without any land border - and Ojibwe, Menominee, Ho-Chunk and Dakota names
throughout: Chequamegon, Escanaba, Sault Ste. Marie, Mille Lacs, Winnebago, Oconomowoc.

### 14.3 Washington + Idaho

A parser batch. **"Washington" is a state, a city (the District of Columbia's), a county in more
than thirty states, and a common street name**; the ambiguity rules of section 7's corner-case
survey - what precedes a trailing state name decides how to read it - get their hardest test
here, and the county-versus-locality fix of 0.13.0 with it. **"ID" is an ordinary word** and
already on the list of abbreviations that are, alongside LA in the first batch: a build covering
both Louisiana and Idaho would have two of them.

Idaho's panhandle also makes the state long and narrow across two time zones, which the bounding
box handles poorly - a box around Idaho contains large parts of Washington, Oregon, Montana,
Wyoming, Utah and Nevada, so it is the strongest test yet of whether the sources are being
filtered by polygon or by box (section 10).

### 14.4 District of Columbia

Last because it is the strangest and the smallest: **not a state at all**. It is a federal
district that is simultaneously its own region, its own county-equivalent and its own city, with
one OpenAddresses source. Who's on First gives it exactly one county, named "District of
Columbia", so region, county and locality all carry the same name - which is precisely the
collision the county-versus-locality dedup of 0.13.0 was written for, in its purest form.

Its name is three words, longer than any state name the parser has had to strip, and its
abbreviation "DC" is also how people write the city. `region_ref` holds a `fips` of 11 for it.
Whether "Washington, DC" and "Washington, District of Columbia" both parse is the question, and
the answer should be compared against what the Washington-state build does with "Washington".

## 15. ~~The clip's buffer admits a strip of the neighbouring country~~ Done 2026-09-23

Section 10 is done: every point source is now clipped to the region, and across the four builds
nothing but OpenStreetMap streets survives beyond the buffer - 270 in New York, 276 in Arizona
plus Nevada, all of them streets, which is the intended case (a street crossing the line is kept
whole and its representative point is the centroid of the whole thing). GNIS strays went from
66.5 km to 0.3 km, and OpenAddresses from 9.8 km to 0.3 km.

What remains is the buffer itself. The clip is the region polygon buffered by about 300 m, which
exists because the boundary generalises the coast and an unbuffered clip drops piers, wharves and
island shoreline. At a coast that is right. At an international land border it admits a strip of
the other country, and in a single-state build the missing-county fallback then labels it:

| Feature | Outside the polygon | Labelled |
|---|---|---|
| `Akwesasne Canada Post` | 77 m | `Akwesasne Canada Post, NY, USA` |
| `Akwesasne Mohawk Police Service` | 102 m | `..., NY, USA` |
| `Rue Akwesasne` | 195 m | `Rue Akwesasne, NY, USA` |

These are on the Quebec and Ontario sides of a Mohawk territory the border runs through, so they
are exactly the places a geocoder for northern New York is most likely to be asked about and
least entitled to claim. It is the Maine defect again, reduced from 118 km to 300 m rather than
removed.

**Done** in 0.15.0. Canada and Mexico are subtracted from the buffered clip, which leaves the
seaward buffer untouched because there is nothing out there to subtract. The data obstacle turned
out to be smaller than it looked: the two country polygons are available as single Who's on First
records totalling 5.6 MB, so `scripts/fetch_data.sh neighbours` does not need the `admin-ca` and
`admin-mx` distributions at 176 MB and 230 MB compressed.

Measured on Maine, features outside the state fell from 2,492 to 235 - all within 3.4 km, almost
all OpenStreetMap streets whose representative point is the centroid of a way that crosses the
line, which is the case worth keeping. Accuracy unchanged, no case altered either way, and the
islands the buffer exists for all survive. Nothing inside the state can be removed this way: the
Who's on First polygons for Maine and Canada meet at the border with zero overlapping area, which
was measured rather than assumed.

One thing to know for later: the fetch verifies each file's `iso:country` after downloading,
because the URL is built from an id and a wrong id returns a perfectly valid polygon for
somewhere else. 85633057 looks like Mexico's id and is Chile's; the right one is 85633293.

## 16. ~~Who's on First's Canada polygon has a hole over Akwesasne~~ Done 2026-09-23

Section 15 subtracts Canada and Mexico from the clip's buffer, and it works: Maine lost 2,255
Canadian features, Vermont and New Hampshire lost the Quebec islands, Arizona and Nevada lost
every Sonoran stray. New York is the exception. It still holds

```
Akwesasne Canada Post              -> "Akwesasne Canada Post, NY, USA"
Akwesasne Mohawk Police Service    -> "..., NY, USA"
Rue Akwesasne                      -> "Rue Akwesasne, NY, USA"
```

and 58 features in total north of the 45th parallel, 15 of them streets whose centroid crosses
the line and 43 of them not.

The reason is not imprecision. Who's on First's Canada polygon was checked against four places
and is exact at all of them - Cornwall, Ontario ten kilometres away, Montreal, Niagara Falls, and
open farmland immediately north of the 45th parallel are all inside it, at zero distance. It has
a **hole** over Akwesasne, and these points sit about a kilometre inside that hole, in a strip
that Who's on First assigns to neither New York nor Canada.

That is likely deliberate rather than broken. Akwesasne is a Mohawk territory straddling Ontario,
Quebec and New York whose jurisdiction is genuinely contested, and a gazetteer declining to
assign it to a country is making a defensible choice. The geocoder is not: it labels the strip
`, NY, USA` because the single-state fallback fills in a missing county with the build's own
state.

**Done** in 0.16.0, by the second of the three options considered - and it turned out to cost one
join rather than the redesign it looked like.

The question was whether the strip is an enclave inside New York or simply beyond it. Measured:
the points are outside New York's **outer ring**, not in a hole, and across all four builds the
region polygons have zero interior rings and hold zero features in holes, so the enclave case
does not arise at all. A concave hull of New York does contain them, and that is the reason not
to use one: it would claim Cornwall's side of the river as readily as the state's own shoreline.

So the rule is the plain one. Land outside the official boundary is not in the state. The state
comes from the county's parent as before, and failing that from whichever region polygon contains
the point - the same probe answers it, so there is no extra pass, only one more placetype in
`admin_parts`. A feature inside no region carries no state, because the build holds states and
not countries, and there is nothing larger loaded to fall back to.

`Akwesasne Canada Post` now answers with no state rather than `, NY, USA`. On Maine 235 features
lose theirs, every one on the New Hampshire line - Salmon Falls River, Hiltons Lane, Upton Road.

Islands keep their state, which was the thing to verify rather than assume, a state's bounds
including them and the coastline being exactly what the clip's buffer exists to survive: Peaks,
Chebeague, Cranberry Isles, Islesboro, Vinalhaven, North Haven, Monhegan and Isle au Haut are
100% Maine across 7,145 features. The Who's on First region polygon includes them, which is why
it was chosen over the Census cartographic one to begin with.
