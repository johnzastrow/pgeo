# Which geocoder the demo page talks to

The demo page (`web/`) is a client for a Pelias-compatible API. It does not care which
implementation answers, so one page serves three arrangements: **pgeo alone**, **Pelias alone**,
or **both side by side** with a switch. This page is how you choose.

## How the page decides

Two things, in this order:

1. **The meta tag.** The server injects `<meta name="demo-engines" content="/engines.json">` into
   `index.html`. The tag is not in the file: it is added by the edge, so the same `web/` directory
   can be deployed in any of the three arrangements without being edited.
2. **`/engines.json`.** If the tag is present, the page fetches this and uses what it finds.

**With no tag the page does not ask**, and falls back to a single built-in engine:

```json
{"label": "Pelias", "base": "", "kind": "pelias"}
```

That fallback is the trap. On a pgeo-only deployment the page still works — `base: ""` is this
origin, which *is* pgeo — but it calls pgeo's answers "Pelias / Elasticsearch", and because no
engine is marked `kind: "pgeo"` it hides the tabs that only pgeo can serve. If your deployment
shows the wrong engine name, this is why: the tag or the file is missing.

## The shape of `engines.json`

```json
{"engines": [
  {"label": "pgeo", "base": "", "kind": "pgeo"}
]}
```

| Field | Meaning |
|---|---|
| `label` | What the user sees. Max 40 characters. |
| `base` | A **same-origin path prefix**: `""` for this origin's root, or `/name`. The page requests `<base>/v1/search` and so on. |
| `kind` | `pgeo` or `pelias`. Decides the note beside the name ("PostgreSQL / PostGIS" or "Elasticsearch") and which tabs appear. |

The page validates what it reads: `base` must match `^(/[a-z0-9-]{1,32})?$`, so it can never be
pointed at another origin, and at most 8 engines are accepted. A malformed file is ignored and
the built-in fallback applies — which, per above, is Pelias-only.

`kind` may be omitted, in which case any non-empty `base` is assumed to be pgeo. Be explicit;
it is the field that decides whether the pgeo-only tabs appear.

## What each arrangement gives you

| Tab | Needs |
|---|---|
| Search, Structured, Reverse, Batch, Confidence, Area, Nearby | any engine |
| **Address** | at least one `kind: "pgeo"` engine (`/v1/address` exists only in pgeo) |
| **Compare** | one of each kind, so there is something to compare |

Tabs whose requirement is unmet are hidden, not broken.

---

## 1. pgeo only

The arrangement for a pgeo deployment. **This is what `deploy/` ships.**

`web/engines.json`:

```json
{"engines": [{"label": "pgeo", "base": "", "kind": "pgeo"}]}
```

The edge already injects the meta tag (see the `location /` block in `deploy/edge/pgeo.conf`).
You get every tab except Compare, the engine is named correctly, and nothing tries to reach a
Pelias that is not there.

There is no switch to show with one engine, so the page prints the name instead of a dropdown.

## 2. Pelias only

Serve the page with **no** `/engines.json` and **no** meta tag, and let the built-in fallback
apply. Nothing to configure — this is what a plain Pelias deployment does, and why the fallback
is what it is.

If you would rather be explicit (recommended, so the next person is not guessing):

```json
{"engines": [{"label": "Pelias", "base": "", "kind": "pelias"}]}
```

Address and Compare stay hidden, because neither can be served.

## 3. Both, side by side

Each engine needs its own path prefix on **one origin** — the page will not cross origins, and a
strict CSP would stop it anyway. Put Pelias at the root and pgeo under a prefix, then proxy that
prefix to pgeo:

```json
{"engines": [
  {"label": "Pelias",           "base": "",          "kind": "pelias"},
  {"label": "pgeo (pure SQL)",  "base": "/pgeo-sql", "kind": "pgeo"},
  {"label": "pgeo (FastAPI)",   "base": "/pgeo-api", "kind": "pgeo"}
]}
```

and in the edge, one location per prefix that strips it before proxying:

```nginx
location ~ ^/pgeo-sql(/v1/(autocomplete|search|search/structured|reverse|place|address|attribution))$ {
    limit_except GET { deny all; }
    proxy_pass http://127.0.0.1:4700$1$is_args$args;
}
```

The capture group is doing the work: `$1` is the path without the prefix, so pgeo sees the plain
Pelias path it expects. Listing the endpoints explicitly rather than matching `/v1/.*` keeps the
prefix from becoming an open proxy into anything else the upstream serves.

`scripts/dev/nginx.dev.conf` is a complete working example of this three-engine arrangement, and
`scripts/dev_web.sh` runs it. Note what that script does for a non-default build: it strips the
Pelias entry, because only the default build has a Pelias index behind it and announcing one
would answer this region's queries from another region's data.

The page remembers the chosen engine in `localStorage` under `pelias-demo-engine`, so a reload
keeps your selection. Switching engines clears the current result rather than leaving one
engine's answer captioned with another's name.

---

## Changing it on a running deployment

`engines.json` is a static file in the web root, so it takes effect on the next page load:

```sh
# on the server
printf '{"engines":[{"label":"pgeo","base":"","kind":"pgeo"}]}\n' > ~/pgeo/web/engines.json
```

No restart — nginx serves it from disk. If the page still shows the old engine, it is the browser
cache; a hard reload settles it.

## Checking it

```sh
curl -s https://<host>/engines.json
curl -s https://<host>/ | grep -o 'demo-engines[^>]*'
```

The first must be valid JSON in the shape above; the second must find the meta tag. If the tag is
missing the file is never read, whatever it contains.
