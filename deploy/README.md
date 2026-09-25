# The serving bundle

Three stock images and a compose file: PostgreSQL/PostGIS, PostgREST, and an nginx edge that
serves the demo page and maps the Pelias paths onto the SQL API. Nothing is built here and
nothing is built on the server - a dump made by `pgeo-build` (or `scripts/pgeo_dump.sh`) is
restored into it.

What is tracked in git is only the configuration: `compose.yml`, `edge/`, `db/init/` and
`.env.example`. The payload - `web/`, `tiles/`, `dumps/`, the swap script and `.env` - is
assembled when the bundle is shipped, and is deliberately ignored.

## Shipping it

From a checkout, with the build already made:

```sh
cd deploy
mkdir -p web tiles dumps
cp -r ../web/. web/                                  # the demo page, already vendored
python3 ../scripts/gen_region_json.py --build me > web/region.json
printf '{"engines":[{"label":"pgeo","base":"","kind":"pgeo"}]}\n' > web/engines.json
cp ../data/raw/me/basemap/me.pmtiles tiles/          # the basemap for this build
cp ../data/pgeo/dumps/pgeo-<version>-<date>.dump dumps/
cp ../scripts/pgeo_swap.sh .                         # one source of truth lives in scripts/
rsync -a --exclude pgdata/ --exclude .env ./ <host>:~/pgeo/
```

Then on the host, once:

```sh
cd ~/pgeo
cp .env.example .env && $EDITOR .env                 # two passwords, the port, the stack name
docker compose up -d
./pgeo_swap.sh --dump dumps/pgeo-<version>-<date>.dump
```

and for every refresh after that, only the last two lines of the first block and the
`pgeo_swap.sh` call. The swap does its work in a staging database while the current one keeps
answering; see the comments at the top of the script.

`region.json` is not optional and is not in `web/`: it is generated per build and tells the page
its title, feature count, map extent and which basemap file to load. Without it the page falls
back to a hard-coded Maine default whose basemap filename does not match what is shipped, and the
map comes up blank with a 404 for a file nobody asked for. The basemap's own name must match what
that file says - `me.pmtiles` for the `me` build.

`engines.json` says which geocoder the page is talking to. Without it the page falls back to a
built-in default that calls this deployment "Pelias / Elasticsearch" and hides the tabs only pgeo
can serve - it still works, it is just labelled wrong and missing features. The edge injects the
meta tag that makes the page read it. To point the page at Pelias instead, or at both with a
switch, see `docs/DEMO_ENGINES.md`.

## Two things worth knowing before the first run

**The stack name.** `PGEO_STACK` prefixes the compose project and all three containers, and
defaults to `pgeo` because that is right on a host dedicated to serving. A machine that also
runs this repository's *development* stack already has containers by those names, and compose
will quietly take them over - set `PGEO_STACK` to something else there.

**No TLS.** The edge publishes on loopback only and holds no certificate. Put a terminator in
front of it; this project's own deployment uses Caddy:

```
pgeo.example.org {
	encode zstd gzip
	reverse_proxy 127.0.0.1:8091
}
```

There is no API authorization yet (TODO section 9 is on hold), so the deployment's only access
control is whatever the terminator and the network provide. The reference deployment is reachable
over a tailnet and not from the public internet, which is what stands in for it meanwhile.
