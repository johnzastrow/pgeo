# Getting started

From nothing to your own geocoder answering on a public URL. Two machines are involved: a
**workstation** that turns open data into a PostgreSQL database, and a **VPS** that serves it.
Building is the heavy part - it wants cores, memory and disk for an hour or two. Serving is not:
one core and a gigabyte of RAM handled 32 concurrent users in our tests.

The examples build New York. Substitute any state code, or several
(`--build me,nh,vt`), wherever you see `ny`.

---

## 0. What you need

**Workstation** - Linux or macOS, 8 GB RAM, and free disk of roughly ten times the OSM extract:
about 15 GB for Maine, 45 GB for New York, and note that Who's on First adds a one-off 5 GB shared
by every build.

```bash
# Debian/Ubuntu. On macOS: brew install jq gdal duckdb go-pmtiles
sudo apt-get install -y git curl jq unzip bzip2 gdal-bin
curl -LsSf https://astral.sh/uv/install.sh | sh                      # uv (Python)
curl -LsSf https://install.duckdb.org | sh                           # duckdb CLI
# Docker Engine with the compose plugin: https://docs.docker.com/engine/install/
# pmtiles CLI, only for the demo map's basemap:
#   https://github.com/protomaps/go-pmtiles/releases
```

**VPS** - 1 vCPU, 1 GB RAM and 10 GB disk is enough for one state; Debian 13, reachable by SSH with
a key. A DNS name pointing at it if you want HTTPS.

Check the tools are all there before starting a long download:

```bash
for t in git curl jq unzip duckdb ogr2ogr docker uv; do
  command -v "$t" >/dev/null || echo "missing: $t"
done
docker compose version >/dev/null || echo "missing: docker compose plugin"
```

---

## 1. Get the code

```bash
git clone <repository-url> pgeo && cd pgeo
```

---

## 2. Pick the region

A *build* is one or more states. `ny` works straight away because `NY` is a state code;
`regions/regions.json` carries the per-state facts (bounding box, Geofabrik extract, Who's on
First id, OpenAddresses sources) for all 50 states plus DC.

```bash
jq -r '.states | keys | join(" ")' regions/regions.json     # what you can build
jq '.states.NY' regions/regions.json                        # what it knows about one
```

To give a multi-state build a stable name, add it to `builds` (optional - `--build me,nh,vt`
also works and is named `me-nh-vt`):

```bash
jq '.builds += {"northeast": {"name": "Northern New England", "states": ["ME","NH","VT"]}}' \
  regions/regions.json > /tmp/r.json && mv /tmp/r.json regions/regions.json
```

---

## 3. Download the data

```bash
scripts/fetch_data.sh --build ny all
```

Six sources, straight from their publishers - nothing here needs the Pelias toolchain:

| Source | What it gives | New York |
|---|---|---|
| Who's on First | admin hierarchy, postcodes | 5.2 GB, national, downloaded once |
| OpenStreetMap (Geofabrik) | streets, venues | 474 MB |
| OpenAddresses | address points | 718 MB, 8.1 M rows |
| USGS GNIS | named natural and civil features | 1 MB |
| Census Gazetteer + ZCTA relationship | postcodes and their states | 7 MB, national |
| Overture Places | venues with categories | 260 MB |
| Protomaps | basemap for the demo page | 1.5 GB (skip with individual targets) |

Individual targets re-run a single source: `scripts/fetch_data.sh --build ny oa`.

An OpenAddresses source that has no current run is reported and skipped - six of New York's
nineteen were absent the day this was written, which is normal.

---

## 4. Turn the downloads into CSV

```bash
cd prep && uv sync && uv run python -m pytest -q          # ~10 tests, seconds
uv run pelias-prep all --build ny --data-dir ../data
cd ..
```

Writes `data/processed/ny/csv/{gnis,zcta,overture}.csv`. It clips everything to the states you
named, so a venue on the wrong side of a border does not get in.

---

## 5. Build the database

```bash
scripts/pgeo_setup.sh --build ny       # containers, passwords, ports; first run compiles libpostal
scripts/pgeo_rebuild.sh --build ny --profile medium --skip-gate
```

`pgeo_setup.sh` puts a build that is not `me` on its own ports so several can run side by side:

| | default (`me`) | `--build ny` |
|---|---|---|
| PostgreSQL | 5433 | 5434 |
| FastAPI | 4500 | 4501 |
| PostgREST | 4600 | 4601 |
| Pelias-compatible SQL API | 4700 | 4701 |

Profiles are in `pgeo/tuning/profiles/`: `tiny`, `small`, `medium`, `large`, `workstation`. Pick
by the server's memory, not by the region's size (`uv run --project pgeo pgeo-tune list`).

`--skip-gate` is needed for any region except Maine: the accuracy gate compares against a
1,560-case Maine set. Section 9 covers building a case set for a new region.

---

## 6. Try it locally

```bash
curl -s 'http://127.0.0.1:4701/v1/search?text=350+5th+Ave,+New+York&size=1' | jq '.features[0].properties.label'
curl -s 'http://127.0.0.1:4701/v1/autocomplete?text=albany' | jq '.features[0].properties.label'
curl -s 'http://127.0.0.1:4701/v1/reverse?point.lat=40.7484&point.lon=-73.9857&size=1' | jq '.features[0].properties.label'
uv run --project pgeo pgeo-load info | jq '.features_by_layer'
```

The demo page, with the map and the four demonstration tabs:

```bash
scripts/vendor_web.sh                  # once: pinned libraries, fonts, glyphs
scripts/dev_web.sh --build ny          # http://127.0.0.1:8088
```

---

## 7. Snapshot it for the server

```bash
scripts/pgeo_dump.sh --build ny        # -> data/pgeo-ny/dumps/pgeo-ny-<date>.dump
```

The server restores this rather than rebuilding, which is why the VPS can be small.

---

## 8. Prepare the VPS

Debian 13, a user with sudo, your SSH key installed. Then, on the workstation:

```bash
cd infra/ansible
cp inventory/hosts.yml.example inventory/hosts.yml
cp group_vars/pelias/zz-local.yml.example group_vars/pelias/zz-local.yml
```

Put the server's address in `inventory/hosts.yml`, then edit
`group_vars/pelias/zz-local.yml` - it is gitignored, and holds everything specific to your
network:

```yaml
pelias_enabled: false            # pgeo only; it then serves the canonical /v1/* paths
pgeo_build: ny                   # names the basemap and the page's own title
ssh_allowed_sources: ["203.0.113.0/24"]     # where you administer from
edge_allowed_sources: ["203.0.113.7"]       # the TLS terminator, and only it
edge_trusted_proxies: ["203.0.113.7"]
edge_server_name: geocoder.your-domain.example
edge_api_keys: []                # filled in below
```

Issue a key for each client. The key is printed once; keep it in a password manager.

```bash
cd ../.. && scripts/edge_apikey.sh new demo
# prints the key, and the `- name:/sha256:` entry to paste under edge_api_keys
```

The edge refuses `/v1/*` without a known key and only the hash is ever stored
(`docs/API_KEYS.md`).

---

## 9. Deploy

```bash
cd infra/ansible
ansible-playbook site.yml -e pgeo_dump_name=pgeo-ny-<date>
```

This hardens the host, installs Docker, restores the dump, starts PostgreSQL, PostgREST and the
FastAPI service, and puts nginx in front on port 8080 with the demo page, the basemap and rate
limits.

Then terminate TLS in front of it. There is no TLS role - use whatever you already run. With
Caddy on another host:

```caddyfile
geocoder.your-domain.example {
	encode zstd gzip
	reverse_proxy <vps-ip>:8080
}
```

To let the public demo page work without visitors typing a key, have the proxy present one
(`infra/wharf/pelias.caddy` is a working example). That makes the key labelling and rate limiting
at that hostname rather than access control - which is the point of a public demo, but be
deliberate about it.

---

## 10. Verify

```bash
KEY=pgeo_...    # the key from step 8
curl -s -H "X-API-Key: $KEY" 'https://geocoder.your-domain.example/v1/search?text=albany&size=1' \
  | jq '.features[0].properties.label'
curl -s -o /dev/null -w '%{http_code}\n' 'https://geocoder.your-domain.example/v1/search?text=albany'   # 401
python3 tests/web/smoke_demo.py https://geocoder.your-domain.example    # browser test
```

---

## 11. Accuracy for a new region

Maine ships a 1,560-case set and a regression gate. A new region starts with neither, so
`--skip-gate` is not optional - it is an honest statement that nothing has been checked yet.
To build a set of your own:

```bash
uv run --project prep python tests/accuracy/build_cases.py --build ny --n 200
uv run --project pgeo python tests/accuracy/run_accuracy.py --engine pgeo \
  --base http://127.0.0.1:4701 --cases tests/accuracy/cases_ny.json --label ny
```

The cases come from sources held out of the build, so the geocoder is not marked against the data
it was built from. `docs/TESTING_GUIDE.md` explains what the numbers mean.

---

## Keeping it up to date

Sources change. To refresh, repeat steps 3 to 7 and deploy the new dump; the swap on the server is
atomic, so queries keep being answered from the old data until the new build is complete.

```bash
scripts/fetch_data.sh --build ny all
(cd prep && uv run pelias-prep all --build ny --data-dir ../data)
scripts/pgeo_rebuild.sh --build ny --profile medium --skip-gate
scripts/pgeo_dump.sh --build ny
(cd infra/ansible && ansible-playbook site.yml --tags pgeo -e pgeo_dump_name=<name>)
```

## Where to look when something breaks

| Symptom | Look at |
|---|---|
| A download 404s | `regions/regions.json` for that state; `scripts/gen_regions.py --states XX` refills it |
| `prep` rejects its own output | it validates before writing; the message names the check |
| The build runs out of memory | a smaller tuning profile, or more swap during the build only |
| `/v1/*` answers 401 | the key, or `edge_require_api_key` - `docs/API_KEYS.md` |
| The map is blank | the basemap for this build is missing: `scripts/fetch_data.sh --build ny basemap` |
| Something else | `docs/REBUILD.md` rebuilds everything from scratch, and says what each step proves |
