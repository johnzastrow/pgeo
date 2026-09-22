# Deploying pgeo with Docker

> **Status: the images described here are designed, not yet published.** This page is the
> operator's interface they are being built to; commands will work as written once the images
> ship (tracked in `docs/DOCKER_IMAGES.md` and `TODO.md`). Until then, deploy from the repository
> as `docs/DEPLOY_PGEO.md` describes.

pgeo is a geocoder that runs inside PostgreSQL. Two images cover everything:

| Image | Runs on | What it is |
|---|---|---|
| `pgeo-build` | a workstation (or the server itself) | the **pre-processor**: fetches the open data, builds the database, dumps it, and pushes it to a server |
| `pgeo` compose bundle | the server (a small VPS is enough) | the **serving side**: PostgreSQL + PostGIS, PostgREST, nginx. Serves the Pelias-compatible API and the demo page |

The serving side never builds from raw data. Building needs about 6 GB of memory and 15
minutes; serving needs a quarter of a core and 1.4 GB.

## 1. Build the database (workstation)

```bash
mkdir pgeo && cd pgeo
docker run --rm -v ./data:/data -e PGEO_REGION=us/maine \
  git.wharf.example/jcz/pgeo-build build
```

That fetches the sources for the region into `./data` (kept, so the next build is quick), builds
the database, and writes `./data/dumps/pgeo-us-maine-<date>.dump` - about 120 MB.

Several regions in one build: `-e PGEO_REGION=us/maine,us/new-hampshire`.

Check it before shipping (the 1,560-case Maine accuracy set; other regions need their own):

```bash
docker run --rm -v ./data:/data git.wharf.example/jcz/pgeo-build accuracy
```

## 2. Start the serving side (server)

```bash
mkdir -p /srv/pgeo && cd /srv/pgeo
curl -O https://git.wharf.example/jcz/pgeo/releases/latest/compose.yml
docker compose up -d
```

It comes up empty and answers `503` with a one-line hint until a database arrives. The API is
on port 8080 behind nginx, with the rate limits, security headers and path allowlist the study
deployed. Put your TLS terminator in front of it; the bundle does not do TLS.

## 3. Push the database (workstation to server)

```bash
docker run --rm -v ./data:/data \
  -v "$SSH_AUTH_SOCK:/ssh-agent" -e SSH_AUTH_SOCK=/ssh-agent \
  git.wharf.example/jcz/pgeo-build push deploy@your-server
```

Over SSH: `rsync` the newest dump (resumable), verify its checksum on the server, then swap it
in **without downtime** - the restore lands in a second schema and is renamed into place in one
transaction. The previous data is dropped only after the new one is serving. Run the same
command again whenever you rebuild; nothing else changes.

The server user needs SSH access and membership of the `docker` group. Nothing else is
installed on the server.

## 4. Check it

```bash
curl 'http://your-server:8080/v1/search?text=389+congress+st+portland'
curl 'http://your-server:8080/v1/autocomplete?text=portl'
```

The demo page is at `http://your-server:8080/`.

## All-in-one: build on the server itself

For a central host that should refresh its own data, the same bundle with the build profile:

```bash
docker compose --profile build run --rm build build     # fetch + build + dump, on the server
docker compose --profile build run --rm build swap      # atomic swap into service
```

Slower (the build wants 6 GB), but one machine and no push step.

## Updating

- **Data**: rebuild and push. Weekly is plenty for these sources.
- **pgeo itself**: `docker compose pull && docker compose up -d`. The functions that make up
  the engine live in the database, and a dump carries the version it was built with; `push`
  refuses to swap a dump built by a newer pgeo than the bundle expects, and says what to update.

## What is where

| Path | Contents |
|---|---|
| workstation `./data/raw` | downloaded sources (cache) |
| workstation `./data/pg` | the build's own PostgreSQL |
| workstation `./data/dumps` | built databases, one file each |
| server `/srv/pgeo/pg` | the serving database |
| server `/srv/pgeo/incoming` | dumps as they arrive |

## Security notes

The bundle ships as the study measured it (report Section 3.13): every engine port on loopback,
GET only, per-client rate limits, a strict content security policy, no query text in the access
log. It does **not** ship API keys or user authentication - any client that can reach port 8080
can geocode. Keep it on a LAN or behind your own authenticating proxy until that lands.
