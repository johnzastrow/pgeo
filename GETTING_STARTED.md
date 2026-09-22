# Getting started

From nothing to your own geocoder answering on a public URL.

Two machines are involved: a **workstation** that turns open data into a PostgreSQL database, and
a **VPS** that serves it. Building is the heavy part - cores, memory and an hour or two. Serving
is not: one core and a gigabyte of RAM handled 32 concurrent users in our tests, which is why the
server restores a file rather than building anything.

The examples build New York. Substitute any state code, or several (`--build me,nh,vt`).

---

## 1. Install what it needs

```bash
# Debian/Ubuntu
sudo apt-get install -y git curl jq unzip bzip2 gdal-bin
curl -LsSf https://astral.sh/uv/install.sh | sh     # uv, for the Python tools
# Docker Engine with the compose plugin: https://docs.docker.com/engine/install/
# Optional, only for the demo map's basemap:
#   pmtiles, from https://github.com/protomaps/go-pmtiles/releases
```

On macOS: `brew install git jq gdal go-pmtiles`, plus uv and Docker Desktop.

Disk, measured, for one state:

| | Maine | New York |
|---|---|---|
| Downloads | 0.6 GB | 3.0 GB |
| Database | 2.6 GB | 8 GB |
| Basemap (optional) | 0.3 GB | 1.5 GB |

Who's on First adds a one-off 5.2 GB that every build shares. Budget 20 GB for a small state and
40 GB for a large one. You do not have to check any of this by hand: the build refuses to start
if something is missing, and says everything that is wrong at once.

**VPS**: 1 vCPU, 1 GB RAM, 10 GB disk, Debian 13, reachable by SSH with a key. A DNS name
pointing at it if you want HTTPS.

---

## 2. Get the code and pick a region

```bash
git clone <repository-url> pgeo && cd pgeo
jq -r '.states | keys | join(" ")' regions/regions.json     # every state, plus DC
```

A *build* is one or more states. `ny` works straight away because `NY` is a state code;
`regions/regions.json` already holds the per-state facts - bounding box, Geofabrik extract, Who's
on First id, OpenAddresses sources.

Now write down a handful of answers you already know. They are facts about the region, so you can
write them before the geocoder exists, and they are what tells you afterwards that the build is
sound rather than merely finished:

```bash
cp pgeo/tuning/verify/me.json pgeo/tuning/verify/ny.json    # then edit it
```

Two well-known places, a street address you can check on a map, a coordinate you recognise, and a
misspelling. Five minutes now; the build will not start without them.

---

## 3. Build it

```bash
scripts/build_region.sh --build ny
```

That is the whole workstation half. It checks everything first - tools, the region, your known
answers, free disk - and then runs five stages, reporting each:

| Stage | What it does | New York |
|---|---|---|
| download | six sources, straight from their publishers | ~20 min |
| prepare | GNIS, ZCTA and Overture into CSV, clipped to the states | ~2 min |
| containers | PostgreSQL, PostgREST and the API, on this build's own ports | ~1 min |
| load and index | into the database, then the known-answer checks | 1-2 hours |
| snapshot | the file the server restores | ~2 min |

Nothing here needs the Pelias toolchain. If a stage fails, fix it and resume with
`--from build`. Add `--no-basemap` to skip the demo map's tiles, or `--profile medium` to pick a
different tuning profile (`uv run --project pgeo pgeo-tune list`).

A build other than the default gets its own stack, so several run side by side:

| | default (`me`) | `--build ny` |
|---|---|---|
| PostgreSQL | 5433 | 5434 |
| FastAPI | 4500 | 4501 |
| PostgREST | 4600 | 4601 |
| Pelias-compatible SQL API | 4700 | 4701 |

---

## 4. Try it

```bash
curl -s 'http://127.0.0.1:4701/v1/search?text=350+5th+Ave,+New+York&size=1' | jq -r '.features[0].properties.label'
curl -s 'http://127.0.0.1:4701/v1/autocomplete?text=albany' | jq -r '.features[0].properties.label'
curl -s 'http://127.0.0.1:4701/v1/reverse?point.lat=40.7484&point.lon=-73.9857&size=1' | jq -r '.features[0].properties.label'
uv run --project pgeo pgeo-load info --build ny | jq '.features_by_layer'
```

The demo page - map, autocomplete, structured search, reverse geocoding, a batch tool, and four
tabs showing confidence, area search, nearby search and form filling:

```bash
scripts/vendor_web.sh                  # once: pinned libraries, fonts, glyphs
scripts/dev_web.sh --build ny          # it prints the URL
```

---

## 5. Put it on a server

Debian 13, a user with sudo, your SSH key installed. Then on the workstation:

```bash
scripts/new_host.sh --build ny
```

It asks for the five things that are specific to your network - the server's address, the user
to log in as, the network you administer from, whatever terminates TLS, and the hostname people
will use - issues the first API key, and writes the two gitignored inventory files. It never
overwrites one without asking. Then:

```bash
cd infra/ansible && ansible-playbook site.yml -e pgeo_dump_name=<the name step 3 printed>
```

That hardens the host, installs Docker, restores the dump, starts the database and both front
ends, and puts nginx in front on port 8080 with the demo page, the basemap, rate limits and the
API-key check (`docs/API_KEYS.md`).

Add a key for each later client with `scripts/edge_apikey.sh new <name>`, paste the entry it
prints under `edge_api_keys`, and re-run with `--tags edge` (nginx reloads; no downtime).

Then terminate TLS in front of it. There is no TLS role - use whatever you already run:

```caddyfile
geocoder.your-domain.example {
	encode zstd gzip
	reverse_proxy <vps-ip>:8080
}
```

To let a public demo page work without visitors typing a key, have the proxy present one
(`infra/wharf/pelias.caddy` is a working example). That makes the key labelling and rate limiting
at that hostname rather than access control - which is the point of a public demo, but be
deliberate about it.

---

## 6. Check it from outside

```bash
KEY=pgeo_...    # the key from step 5
curl -s -H "X-API-Key: $KEY" 'https://geocoder.your-domain.example/v1/search?text=albany&size=1' \
  | jq -r '.features[0].properties.label'
curl -s -o /dev/null -w '%{http_code}\n' 'https://geocoder.your-domain.example/v1/search?text=albany'   # 401
python3 tests/web/smoke_demo.py https://geocoder.your-domain.example                                    # browser test
```

---

## Keeping it up to date

Sources change. Re-run the build and deploy the new dump; the swap on the server is atomic, so
queries keep being answered from the old data until the new build is complete.

```bash
scripts/build_region.sh --build ny --yes
(cd infra/ansible && ansible-playbook site.yml --tags pgeo -e pgeo_dump_name=<name>)
```

---

## How accurate is it?

Maine ships 1,560 ground-truth cases and a regression gate. A new region starts with neither, so
the build skips the gate for it - an honest statement that nothing has been compared yet, not a
shortcut. To measure your own region:

```bash
uv run --project prep python tests/accuracy/build_cases.py --build ny --n 200
uv run --project pgeo python tests/accuracy/run_accuracy.py --engine pgeo \
  --base http://127.0.0.1:4701 --cases tests/accuracy/cases_ny.json --label ny
```

The cases come from the source data - OpenAddresses points, Who's on First town labels, GNIS
features, Overture places, Census ZCTAs - so the geocoder is marked against the data rather than
against itself. `docs/TESTING_GUIDE.md` explains what the numbers mean.

---

## Running the stages yourself

`build_region.sh` is only an ordering of five scripts, each usable alone:

```bash
scripts/fetch_data.sh --build ny all      # or one target: osm gnis oa wof zcta boundary overture basemap
(cd prep && uv run pelias-prep all --build ny)
scripts/pgeo_setup.sh   --build ny
scripts/pgeo_rebuild.sh --build ny --skip-gate
scripts/pgeo_dump.sh    --build ny
```

---

## When something breaks

| Symptom | Look at |
|---|---|
| The build refuses to start | It lists every reason at once; fix them and re-run |
| A download 404s | `regions/regions.json` for that state; `gen_regions.py --states XX` refills it |
| `prep` rejects its own output | It validates before writing; the message names the check |
| The build runs out of memory | A smaller tuning profile, or more swap during the build only |
| `/v1/*` answers 401 | The key, or `edge_require_api_key` - `docs/API_KEYS.md` |
| The map is blank | `scripts/fetch_data.sh --build ny basemap` |
| Anything else | `docs/REBUILD.md` rebuilds everything and says what each step proves |
