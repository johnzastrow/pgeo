# pelias_maine

Self-hosted Pelias geocoder for Maine, built from every applicable open data source, plus
(Phase 10) a PostGIS-native geocoder tuned against it. See [PLAN.md](PLAN.md) for the
full plan, decisions, and security baseline, and
[docs/DATA_PIPELINE.md](docs/DATA_PIPELINE.md) for the step-by-step data extraction and
loading runbook (every step with manual commands, checks, and reference counts).

## Endpoint

All clients use **https://geocoder.example.org** (LAN and tailnet only; TLS at the
wharf Caddy). Exposed paths: `/v1/search`, `/v1/search/structured`, `/v1/autocomplete`,
`/v1/reverse`, `/v1/place` (GET only), plus the demo page at `/`.

```bash
curl 'https://geocoder.example.org/v1/search?text=389%20Congress%20St,%20Portland,%20ME'
```

## Layout

| Path | Purpose |
|------|---------|
| `projects/pelias_maine/` | Pelias project: compose file (pinned images), `pelias.template.json`, synonyms, test cases |
| `prep/` | Python (uv) package `pelias-prep`: GNIS, Census ZCTA, Overture Places -> Pelias CSV |
| `scripts/` | Fetch raw data, vendor the pelias CLI, render config, build, snapshot |
| `infra/proxmox/` | Debian 13 cloud-init template + VM creation (run on the Proxmox host) |
| `infra/wharf/` | Reference copy of the wharf Caddy site block |
| `infra/ansible/` | Host config for any Debian host (Proxmox VM or VPS): base hardening, Docker, query stack, nginx edge |
| `web/` | Demo page (MapLibre + self-hosted Protomaps basemap), reusable `pelias-client.js` and `<pelias-search>` element; `web/vendor/` holds pinned third-party assets |
| `tests/web/` | Browser smoke test (Playwright) for the demo page |
| `docs/` | `DATA_PIPELINE.md` (runbook), `PROJECT_LOG.md` (goals, questions, decisions, findings) |
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

## Demo page

Live at https://geocoder.example.org/ : autocomplete with map-center bias and
viewport restriction, layer/source filters, structured search, click (or right-click) to
reverse geocode, and a small CSV batch tool (forward or reverse, 250 rows, 4 req/s).

```bash
scripts/vendor_web.sh                 # re-vendor pinned libs/fonts/glyphs/sprites (integrity-checked)
scripts/dev_web.sh                    # local preview at http://127.0.0.1:8088 (same CSP as prod)
python3 tests/web/smoke_demo.py       # browser test (or pass https://geocoder.example.org)
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
