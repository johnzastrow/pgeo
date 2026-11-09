# Configuration

Every limit and tuning knob a deployment can change, where to set it, and its default. Anything
not listed here is deliberately fixed: header stripping, the CSP, container privileges, error
sanitising, coordinate and NaN checks. Those are security floors rather than tuning, and a switch
would only make it possible to turn them off.

## Request limits

These are enforced in SQL, so they hold whichever front end is in use, and with none at all. They
are custom PostgreSQL settings on the API role, read on each request. FastAPI reads the same
values from the database at startup, so the two front ends always agree.

| Setting | Default | Allowed | What it limits |
|---|---|---|---|
| `pgeo.max_text` | 200 | 20 - 1000 | Characters in `text`, structured fields and `/v1/address` text |
| `pgeo.max_size` | 40 | 1 - 100 | Results per request (`size`), and ids per `/v1/place` |
| `pgeo.max_circle_km` | 100 | 1 - 1000 | `boundary.circle.radius` on search, structured and autocomplete |
| `pgeo.max_reverse_km` | 50 | 0.1 - 200 | `boundary.circle.radius` on reverse |
| `pgeo.max_address_km` | 5 | 0.1 - 50 | `radius` on `/v1/address` |

A value outside its range is a configuration error. `pgeo_swap.sh` and the Ansible role check
the values and stop before anything is switched; at request time an out-of-range value fails the
request with a message naming the setting.

**Where to set them**

| Deployment | How |
|---|---|
| Serving bundle (`deploy/`) | `PGEO_MAX_TEXT`, `PGEO_MAX_SIZE`, `PGEO_MAX_CIRCLE_KM`, `PGEO_MAX_REVERSE_KM`, `PGEO_MAX_ADDRESS_KM` in `.env`. Applied on first boot and again by every `pgeo_swap.sh`; blank or absent means the default. |
| Ansible (`infra/ansible`) | `pgeo_api_limits: { max_circle_km: 150, max_size: 20 }` in `zz-local.yml`. Applied on every play. |
| Local stack (`pgeo/compose.yml`) | The same `PGEO_MAX_*` names in `pgeo/pgeo.secrets` or the environment, applied by every build (`pgeo-load build`) and by `pgeo_swap.sh`. |
| Your own PostgreSQL (shape D in STACK_COMPARISON.md) | `ALTER ROLE pgeo_api SET pgeo.max_circle_km = '150';` |

The settings apply to connections opened after the change. PostgREST and FastAPI keep pooled
connections, so restart them to apply a change at once. To see the values in force:

```sql
SELECT geocode.api_limits();           -- as the API role sees them
SELECT geocode.check_api_role_limits(); -- as the owner: checks what is set on pgeo_api
```

## Statement timeout

The longest any one API query may run before PostgreSQL cancels it, so that no request can hold a
core for long. Default `5s`. The slowest measured query is about 0.5 s, so the default only stops
runaways.

| Deployment | How |
|---|---|
| Serving bundle | `PGEO_API_STATEMENT_TIMEOUT=5s` in `.env` |
| Ansible | `pgeo_api_statement_timeout: "5s"` |
| Local stack | `PGEO_API_STATEMENT_TIMEOUT` in `pgeo/pgeo.secrets` or the environment |

## Edge rate limits

Per client address, after the real address is recovered from the TLS terminator's
`X-Forwarded-For` (bundle: `deploy/edge/real-ip.conf`).

| Deployment | Setting | Default |
|---|---|---|
| Serving bundle | `PGEO_EDGE_RATE` (requests/s), `PGEO_EDGE_BURST`, `PGEO_EDGE_BURST_AUTOCOMPLETE` in `.env` | 10, 20, 40 |
| Ansible | `edge_rate_search`, `edge_rate_autocomplete`, `edge_burst_search`, `edge_burst_autocomplete` | 10, 25, 20, 50 |

**Batch tool pace.** The demo page's batch tool starts requests at the rate in `region.json`
(`"batch_rate"`, requests per second, 1 to 50) and keeps up to 8 in flight; without the field it
uses 8, which fits the default edge limit. If you raise `PGEO_EDGE_RATE`, add a `batch_rate` a
little below it (for example 40 against an edge limit of 50), or the page will not use the headroom.
`scripts/gen_region_json.py` does not write the field, so add it after generating the file.
Raise both only where the clients are trusted, such as a private LAN: the limit is what stops one
client from using the whole database.

## Front ends and pools

| Setting | Where | Default | Notes |
|---|---|---|---|
| `PGEO_REST_POOL` / `pgeo_rest_pool` | bundle `.env` / Ansible | 10 | PostgREST's database connections |
| `PGEO_POOL_MAX` | FastAPI | 8 | FastAPI's database connections per worker |
| `PGEO_WORKERS` | FastAPI | 2 | Uvicorn workers |
| `PGEO_ACQUIRE_TIMEOUT` | FastAPI | 3 (seconds) | Wait for a pooled connection before answering 503 |
| `PGEO_PARSE_MODE` | FastAPI | `service` | `service`, `extension` or `none` |
| `PGEO_ALLOW_PARSE_OVERRIDE` | FastAPI | off | Lets `?pgeo.parse=` choose the parser per request. For local A/B runs only |
| Tuning profile | `pgeo-tune apply <profile>` / `pgeo_profile_name` | per host | PostgreSQL memory and parallelism; `docs/PGEO_TUNING.md` |
