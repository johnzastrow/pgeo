# Photon comparison

Photon is the type-ahead specialist, and autocomplete is the endpoint that limits pgeo on every
configuration, so this is the comparison that presses hardest on pgeo's weakest axis. Report
Section 3.14 has the findings; this page is how to reproduce them.

Photon is not part of the deployed stack. It is built and run locally for measurement only.

## What was measured

| | Pelias C2 | Photon (2 cores) | pgeo rest-P2 |
|---|---|---|---|
| Users within targets | 192 | 128 | 48 |
| Broke at | 384 | 256 | 96 |
| Throughput at the limit | 209 req/s | 127 req/s | 53 req/s |
| Memory under load | 8.4 GB budget | 1.84 GB resident | 2.5 GB budget |
| Autocomplete p95 at 48 users | 21 ms | 53 ms | 103-189 ms |
| Accuracy, all 1,560 cases | 75.5% | 73.7% | 95.8% |

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
