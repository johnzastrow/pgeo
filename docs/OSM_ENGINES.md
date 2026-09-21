# The OpenStreetMap engines: Photon and Nominatim

Two engines, one setup: a Photon index is exported from a Nominatim database, so building Photon
means building Nominatim, and measuring both costs little more than measuring one.

Photon is the type-ahead specialist, and autocomplete is the endpoint that limits pgeo on every
configuration. Nominatim is the reference OpenStreetMap geocoder and pgeo's closest relative -
both put the work in PostgreSQL. Report Sections 3.14 and 3.15 have the findings; this page is
how to reproduce them.

Photon is not part of the deployed stack. It is built and run locally for measurement only.

## What was measured

| | Pelias C2 | Photon | Nominatim | pgeo rest-P2 |
|---|---|---|---|---|
| Users within targets | 192 | 128 | 64 | 48 |
| Broke at | 384 | 256 | 128 | 96 |
| Throughput at the limit | 209 req/s | 127 req/s | 68 req/s | 53 req/s |
| Memory under load | 8.4 GB budget | 1.84 GB resident | 2.19 GB resident | 2.5 GB budget |
| Autocomplete p95 at 48 users | 21 ms | 53 ms | 85 ms | 103-189 ms |
| Accuracy, all 1,560 cases | 75.5% | 73.7% | 61.7% | 95.8% |
| Autocomplete accuracy | 86.7% | 58.0% | 8.0% | 95.3% |

Unconstrained (all 12 cores): Photon 384 users at 404 req/s, Pelias 192, pgeo 128.

Three conclusions. Photon is quicker per keystroke than pgeo and lighter on memory than either
other engine. It is *not* more CPU-efficient than Pelias, so it does not replace Pelias as the
capacity yardstick. And its Maine index cannot hold the data this project needs, because a Photon
index is exported from Nominatim and Nominatim imports OpenStreetMap only - no OpenAddresses E911
points, no Overture places. Venue accuracy is 19.1% against pgeo's 94.2% for that reason.

Photon also returns no confidence score, so it cannot mark an answer it does not believe. The
accuracy set's deliberate misses score 69.3% against pgeo's 94.7% on that account alone.

## Building the index

Photon has no importer of its own: it reads a Nominatim database.

```bash
# 1. Nominatim, from the same Geofabrik Maine extract the other engines use (about 8 minutes).
docker run -d --name nominatim_maine -p 8081:8080 \
  -v "$PWD/data/raw/osm/maine-latest.osm.pbf:/nominatim/data.osm.pbf:ro" \
  -e PBF_PATH=/nominatim/data.osm.pbf -e IMPORT_STYLE=address \
  mediagis/nominatim:4.5

# 2. Photon's index, exported from it (39 seconds, 791,593 documents, 203 MB).
mkdir -p data/photon-app && cd data/photon-app
curl -LO https://github.com/komoot/photon/releases/download/1.3.0/photon-1.3.0.jar
java -jar photon-1.3.0.jar -nominatim-import \
  -host 127.0.0.1 -port 5432 -database nominatim -user nominatim -password <pw>
```

Photon 1.3.0 bundles OpenSearch, the same class of engine as Pelias's Elasticsearch.

## Running the measurements

```bash
# Serve. No argument uses every core; an argument pins it, to match a Pelias/pgeo configuration.
tests/load/photon_serve.sh        # unconstrained, comparable to M0 and PM
tests/load/photon_serve.sh 0,1    # two cores, comparable to Pelias C2 and pgeo P2

# Capacity: same corpus, mix, think times, ramp and targets as the other engines.
python3 tests/load/run_photon.py --base http://127.0.0.1:2322

# Accuracy: the same 1,560 cases and the same ground truth, scored by run_accuracy.score.
uv run --project pgeo python tests/accuracy/run_accuracy_photon.py --base http://127.0.0.1:2322
```

k6 runs pinned to cores 5 and 11 (`run_matrix.K6_CPUS`), so a pinned Photon must avoid those.

## Where the comparison is not like for like

These are properties of Photon, reported rather than worked around:

- **No structured endpoint.** The structured cases and the structured share of the load session
  are sent as one line, which is what a client would have to do.
- **No confidence score.** A miss counts as correct when an engine returns nothing *or* scores its
  best answer below 0.8. Photon can only ever satisfy the first.
- **Reverse needs a different filter.** The cases ask Pelias for `layers=address`; Photon spells
  that `layer=house`, and the adapter translates it. Without it Photon answers with the nearest
  street and fails a housenumber check it was never asked to satisfy.
- **OpenStreetMap only**, as above - the largest difference of the four, and the one that decides
  the outcome.

Photon was run with default JVM settings while Pelias and pgeo were tuned (report Section 2.7).
That favours Photon rather than handicapping it: the default maximum heap on this 31 GB host is
about 7.75 GB, against the 768 MB Pelias's tuned C2 configuration uses, and the pinned process
correctly reported two CPUs (`Cpus_allowed_list: 0-1`), so its GC and thread pools were sized to
the cores it had.

## Nominatim

Nominatim needs no extra build: the database imported above *is* the engine. The container serves
its API from four Gunicorn workers in front of its own PostgreSQL, so unlike Pelias's
unconstrained configuration its front end is not the bottleneck.

```bash
# Pin it to two cores, to match Pelias C2 and pgeo P2. No restart needed.
docker update --cpuset-cpus 0,1 nominatim_maine

python3 tests/load/run_nominatim.py --base http://127.0.0.1:8081
uv run --project pgeo python tests/accuracy/run_accuracy_nominatim.py --base http://127.0.0.1:8081
```

Result: **64 users within targets, breaking at 128**, and 61.7% accuracy. The number that matters
is 8.0% at autocomplete, with 75.3% of those queries returning nothing: Nominatim has no
autocomplete endpoint, so a keystroke prefix goes to `/search`, and `/search` on two characters
matches nothing. That gap is why Photon exists.

The other number worth carrying away is the median error on addresses: **0 m**. Nominatim is
exact when it answers, and returns nothing for 38.9% of all queries. Its failure mode is recall,
not precision - and its joint-best 94.7% on the deliberate misses is earned by the same silence
rather than by knowing it does not know.

Where the Nominatim comparison is not like for like:

- **No autocomplete endpoint**, as above. The prefixes go to `/search`, which is what a client
  would have to do.
- **No confidence score.** It returns an `importance`, but that ranks results against each other
  rather than expressing belief, so a miss can only be signalled by returning nothing.
- **Reverse returns one result**, so hit@5 equals hit@1 there. `zoom=18` is the house-level
  equivalent of the other engines' `layers=address`.
- **No focus biasing** was sent, where the Photon session sends a focus point. The accuracy cases
  carry no focus point at all, so this affects only the load session, and only slightly: the
  index holds one state.

## Stopping them

Neither is part of the deployed stack. `docker stop nominatim_maine` and `pkill -f photon.jar`
when the measurements are done; the data survives a stop. The Nominatim container is about
3.8 GB on top of a 2.4 GB image, and the Photon index 297 MB under `data/photon-app/`.
