# API keys

Every request to `/v1/*` needs a key in the `X-API-Key` header. The edge (nginx) hashes it and
looks the hash up in its list of known clients; no match, no API. Static files, tiles and the
demo page shell need no key.

```bash
curl -H 'X-API-Key: pgeo_...' 'https://geocoder.example.org/v1/search?text=389+congress+st+portland'
```

A key in the URL (`?api_key=`) is **refused**, valid or not: URLs land in browser history,
referers and proxy logs.

## Issue a key

```bash
scripts/edge_apikey.sh new dispatch
```

prints the key **once** and the inventory entry for it. Give the key to the client; add the
entry to `infra/ansible/group_vars/pelias/zz-local.yml` (gitignored):

```yaml
edge_api_keys:
  - name: dispatch
    sha256: 4f2a...
```

then deploy the edge - nginx reloads, nothing goes down:

```bash
cd infra/ansible && ansible-playbook site.yml --tags edge
```

One entry per client (the dispatch application, the demo page's users, a batch job). A client
name is what the access log records, so choose names you would want to read in one.

## Revoke or rotate

Delete the entry (or add a new one beside it, hand out the new key, delete the old one later),
deploy the edge. Takes effect on reload.

## What the server keeps

The SHA-256 of each key and its name. Not the key. A key is 32 bytes from the operating system's
random source (`pgeo_` + 43 base64url characters); with 256 bits of entropy there is nothing for
a fast hash to expose, which is why it is not stored with a slow password hash - those exist for
secrets people choose.

## What the log shows

```
192.0.2.10 - dispatch [22/Sep/2026:14:05:01 +0000] "GET /v1/search" 200 8829 "-" "..."
192.0.2.77 -  [22/Sep/2026:14:05:03 +0000] "GET /v1/search" 401 141 "-" "..."
```

The client's name on a success, nothing on a refusal, never the key, never the query string.

## The demo page

Asks for a key when it has none, and again if the one it has stops being accepted. The key is
kept for that browser tab only - `sessionStorage`, not `localStorage`, not a cookie - and sent in
the header like any other client. Users of the page get their own client entry (`demo`, say), so
their use is distinguishable from the applications' in the log and revocable on its own.

## Clients

- **LANCER**: `backend/app/geocode.py` builds its request with an `httpx.AsyncClient`; add
  `"X-API-Key": settings.geocoder_api_key` to its headers and the setting beside `geocoder_url`.
- **The harnesses in this repository** read `PGEO_API_KEY` from the environment (or
  `data/dev_api_key`, written by `scripts/dev_web.sh`): the accuracy runner, the compatibility
  contract, the browser smoke test, and k6 (`-e API_KEY=`).
- **Anything else**: one header. There is no handshake and no token to refresh.

## Turning it off

`edge_require_api_key: false` in the inventory makes every caller `anonymous` and refuses no one.
It exists for a LAN demo with nothing sensitive behind it; the security tests fail against a host
configured this way, on purpose.

## Development

`scripts/dev_web.sh` enforces keys exactly as production does. It uses `PGEO_API_KEY` if set,
otherwise makes a key, prints it, and writes it to `data/dev_api_key` for the tests to find. The
tests: `tests/security/test_api_keys.py` (37 checks against a live edge and the configuration).
