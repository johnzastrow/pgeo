<img src="docs/branding/pgeo-clean.svg" alt="pgeo" width="260">

# pgeo

PGEO is a feature-rich geocoder that can live entirely inside PostgreSQL. It's a personal, petite PostgreSQL Geocoder (hence the short name PGEO) for when you need just enough geocoding and a simple deployment. It is fast, resource-efficient, easy to deploy, and feature-rich. With 1 CPU and 2 GB RAM, PGEO can easily serve 10-15 concurrent users with answers in 100 ms (most return sub-25 ms), even for complex queries using rich data for the full State of Maine. However, it does not scale horizontally as well as other options like Pelias. We measured it against Pelias and other popular geocoders to assess accuracy, speed, and scalability. Read the full [docs/REPORT.pdf](report).

This project is unrelated to pgeocode, a Python postal-code library.

PGEO also includes a collection of useful utilities alongside the core geocoding/reverse geocoding service.

* Docker images for the server and pre-processing data (pre-processing needs more memory and compute than serving the service)
* An HTML page that demonstrates connecting to PGEO and optionally Pelias to show off features
* A rich test suite to prove performance and accuracy
* Scripts and documentation to get it running

Links to other useful documents here are the following:

* Quick Start deployment guide
* 


## Licence and data

The code is [Apache-2.0](LICENSE). The data it loads is not: each source carries its own licence
and a deployment that redistributes data or derived tiles must comply with them - see [NOTICE](NOTICE)
and `docs/DATA_PIPELINE.md` section 13. A running service reports the same list at `/v1/attribution`.

## Layout

| Path | Purpose |
|------|---------|
| `projects/pelias_maine/` | Pelias project: compose file (pinned images), `pelias.template.json`, synonyms, test cases |
| `prep/` | Python (uv) package `pelias-prep`: GNIS, Census ZCTA, Overture Places -> Pelias CSV |
| `pgeo/` | PostgreSQL 18/PostGIS geocoder with a Pelias-compatible API (FastAPI, or pure SQL via PostgREST); `pgeo/tuning/profiles/` holds the measured tuning profiles |
| `scripts/` | Fetch raw data, vendor the pelias CLI, render config, build, snapshot |
| `infra/proxmox/` | Debian 13 cloud-init template + VM creation (run on the Proxmox host) |
| `infra/wharf/` | Reference copy of the wharf Caddy site block |
| `infra/ansible/` | Host config for any Debian host (Proxmox VM or VPS): base hardening, Docker, query stack, nginx edge |
| `web/` | Demo page (MapLibre + self-hosted Protomaps basemap), reusable `pelias-client.js` and `<pelias-search>` element; `web/vendor/` holds pinned third-party assets |
| `tests/web/` | Browser smoke test (Playwright) for the demo page |
| `tests/accuracy/`, `tests/load/` | Accuracy set (1,560 cases), fuzz rounds, regression gate; capacity tests (k6) for both engines |
| `docs/` | `DATA_PIPELINE.md` (runbook), `PROJECT_LOG.md` (goals, questions, decisions, findings), `TESTING_GUIDE.md` (how the tests work, in plain language), `LOAD_TEST_PLAN.md`, `PGEO_DESIGN.md`, `REBUILD.md` (rebuild everything), `REPORT.md`/`REPORT.pdf` (the study), `TUNING_REPORT.md` (all tuning and how to re-apply it), `DEPLOY_PGEO.md`, `PELIAS_COMPATIBILITY.md`, `ADDRESS_API.md`, `ENGINE_COMPARISON.md`, `PGEO_TUNING.md` (change log), `ACCURACY_RESULTS.md`, `HTTP_OPTIONS.md` |
| `data/` | Raw downloads, processed CSVs, Pelias data dir (gitignored) |

## Build on the workstation

```bash
scripts/bootstrap.sh                          # vendor pelias/docker CLI at a pinned commit
scripts/fetch_data.sh all                     # GNIS, ZCTA, boundary, Overture, basemap, OSM
(cd prep && uv sync && uv run pelias-prep all && uv run pytest -q)
cp projects/pelias_maine/.env.example projects/pelias_maine/.env        # set DATA_DIR, ES_HEAP
cp projects/pelias_maine/secrets.env.example projects/pelias_maine/secrets.env  # optional OA token
chmod 600 projects/pelias_maine/.env projects/pelias_maine/secrets.env
scripts/build_local.sh                        # setup download prepare import up test
curl 'http://127.0.0.1:4000/v1/search?text=portland,%20maine'
scripts/snapshot_local.sh                     # ES snapshot for shipping to the VM
```

## Deploy (query-only host)

```bash
ssh root@192.0.2.10 'bash -s' < infra/proxmox/create_template.sh
ssh root@192.0.2.10 "SSH_PUBKEY='$(cat ~/.ssh/id_ed25519.pub)' bash -s" < infra/proxmox/create_vm.sh
# put the reported IP in infra/ansible/inventory/hosts.yml, then:
cd infra/ansible && ansible-playbook site.yml -e pelias_snapshot_name=<snapshot>
```

Then add the `geocoder.example.org` site on the wharf Caddy, reverse-proxying to
`<vm-ip>:8080`.

## pgeo: set up, then rebuild with all tuning applied

```bash
scripts/pgeo_setup.sh                      # once: secrets, images, containers, local edge
scripts/pgeo_rebuild.sh --profile medium   # build, tuning profile, known-answer checks, accuracy gate
uv run --project pgeo pgeo-tune list       # profiles (tiny, small, medium, large, workstation)
```

Details: [docs/TUNING_REPORT.md](docs/TUNING_REPORT.md), section 7.

## Demo page

Live at https://geocoder.example.org/ : autocomplete with map-center bias and
viewport restriction, layer/source filters, structured search, click (or right-click) to
reverse geocode, and a small CSV batch tool (forward or reverse, 250 rows, 4 req/s).

```bash
scripts/vendor_web.sh                 # re-vendor pinned libs/fonts/glyphs/sprites (integrity-checked)
scripts/dev_web.sh                    # local preview at http://127.0.0.1:8088 (same CSP as prod)
python3 tests/web/smoke_demo.py       # browser test (or pass https://geocoder.example.org)
# The local preview adds an engine switch (Pelias / pgeo SQL / pgeo FastAPI); production has none.
cd infra/ansible && ansible-playbook site.yml -e pelias_snapshot_name=<snapshot>   # deploy
```

Reusing the search box elsewhere:

```html
<script type="module">
  import { PeliasClient } from './js/pelias-client.js';
  import './js/pelias-search.js';
  const el = document.querySelector('pelias-search');
  el.client = new PeliasClient({ baseUrl: 'https://geocoder.example.org' });
  el.addEventListener('pelias-select', (e) => console.log(e.detail.feature));
</script>
<pelias-search placeholder="Search Maine"></pelias-search>
```

A different origin needs a CORS allowlist entry on the edge first (none today).

## Secrets

- `projects/pelias_maine/secrets.env` holds `OA_TOKEN` (optional). It is read only by
  `scripts/render_config.sh`, which writes the gitignored, mode-600 `pelias.json`.
- `.env` holds non-secret compose settings. Neither file is committed.
