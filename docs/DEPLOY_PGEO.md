# Deploying pgeo on VM 120 (next to Pelias)

Decision 2026-09-19: pgeo runs on the same query host as Pelias so the demo page can switch
engines live. The VM is resized for it. pgeo on the VM is the pure-SQL stack only
(PostgreSQL/PostGIS + PostgREST behind the existing nginx): no FastAPI, no libpostal.

## What gets deployed

| Piece | Where | Notes |
|-------|-------|-------|
| `pgeo_db` | `postgis/postgis:18-3.6` (pinned digest), no published port | profile "large": shared_buffers 512MB, effective_cache_size 1536MB, work_mem 16MB; memory limit 2.5 GB; bind parameters never logged |
| `pgeo_rest` | `postgrest/postgrest:v16.3` (pinned digest), `127.0.0.1:4600` | schema `geocode_api` only, read-only role `pgeo_api`, pool 10, memory limit 256 MB |
| nginx edge | existing `pelias.conf` | `/pgeo/v1/*` -> PostgREST (circle renaming, Pelias-shaped 400s, Pelias rate limits); `/engines.json`; meta tag that turns on the engine switch |
| Data | `/srv/pgeo` | restored from a `pg_dump` made on the workstation; the VM never builds from raw data |
| Secrets | `/opt/pgeo/pgeo.secrets` (mode 600) | generated on the VM on first run; never in the repository or the Ansible logs |

Memory plan for VM 120: Pelias ~8.5 GB (standard profile), pgeo ~2.8 GB, OS ~1 GB, so the VM
goes from 10 GB to 14 GB.

## Steps

```bash
# 1. Resize VM 120 (Proxmox host prox82). The new memory applies after a restart;
#    the Pelias stack comes back on its own (systemd unit, restart policies).
ssh root@192.0.2.10 'qm set 120 --memory 14336 && qm reboot 120'

# 2. Dump the workstation database (after a build, when no load test is running)
scripts/pgeo_dump.sh          # prints the dump name

# 3. Deploy (restores the dump on first run; later runs only update configuration)
cd infra/ansible
ansible-playbook site.yml -e pgeo_dump_name=<name>

# 4. Verify through the real HTTPS path
curl -s 'https://geocoder.example.org/pgeo/v1/search?text=Portland,+Maine&size=1'
curl -s 'https://geocoder.example.org/engines.json'
python3 tests/web/smoke_demo.py https://geocoder.example.org
uv run --project pgeo python tests/compat/compat_test.py \
    --pelias https://geocoder.example.org --api https://geocoder.example.org/pgeo \
    --sql https://geocoder.example.org/pgeo
```

Updating the data later: build, `scripts/pgeo_dump.sh`, then
`ansible-playbook site.yml -e pgeo_dump_name=<new> -e pgeo_force_restore=true`.

## Rollback

`ansible-playbook site.yml -e pgeo_enabled=false` removes the pgeo routes and the engine
switch from the edge (the page falls back to Pelias only); then
`ssh <vm> sudo systemctl disable --now pgeo`.
