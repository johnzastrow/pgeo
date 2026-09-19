# Pelias API Compatibility (pgeo 0.7.0)

pgeo aims to be a drop-in replacement for the Pelias HTTP API, with extensions. This note
records what is compatible, how it is proven, and the differences that remain.

## How it is proven

`tests/compat/compat_test.py` sends the same 27 documented Pelias requests to Pelias
(`:4000`), pgeo FastAPI (`:4500`) and pgeo pure SQL through PostgREST (`:4700`), and checks
each response against a contract: status code, envelope shape, property names, filter
behaviour (every result inside the circle, rectangle or `boundary.gid` area; no results for
another country) and Pelias-shaped errors. Pelias is the reference: a check that Pelias
itself fails is reported, not counted.

| Date | pgeo FastAPI | pgeo pure SQL |
|------|--------------|---------------|
| 2026-09-19, before (pgeo 0.6.0) | 11 failures | 17 failures (28 in total) |
| 2026-09-19, after (pgeo 0.7.0) | 0 | 0 |

The accuracy gate passes unchanged (95.8%).

## Supported

| Area | Supported |
|------|-----------|
| Endpoints | `/v1/search`, `/v1/search/structured`, `/v1/autocomplete`, `/v1/reverse`, `/v1/place` |
| Parameters | `text`, `size`, `layers` (incl. `coarse`), `sources` (incl. `oa`, `osm`, `wof`), `focus.point.lat/lon`, `boundary.rect.*`, `boundary.circle.lat/lon/radius`, `boundary.country`, `boundary.gid`, `categories`, structured fields (`address`, `neighbourhood`, `locality`, `county`, `region`, `postalcode`, `country`), `point.lat/lon`, `ids`; `lang`, `api_key` and `debug` are accepted and ignored |
| Response | GeoJSON FeatureCollection with the Pelias `geocoding` block (`version`, `query`, `engine`, `timestamp`, `errors`), `bbox`, and per feature `gid`, `layer`, `source`, `source_id`, `name`, address parts, `confidence`, `match_type`, `accuracy`, `distance`, `label`, `country`/`country_a`/`country_code`/`country_gid`, `region`/`region_a`/`region_gid`, `county`/`county_a`/`county_gid`, `localadmin`/`localadmin_gid`, `locality`/`locality_gid`, `neighbourhood`/`neighbourhood_gid`, `category`, `addendum` |
| Hierarchy ids | Same Who's On First ids as Pelias (for example `whosonfirst:locality:85948877` for Portland), recorded at build time by point-in-polygon |
| Errors | HTTP 400 with `geocoding.errors`, from both front ends; an unsupported parameter is also a Pelias-shaped 400 |
| Extension | `/v1/address` (USPS Publication 28 addresses; docs/ADDRESS_API.md) |

## How the pure-SQL path does it

- PostgREST keeps only the last segment of a dotted query key, so the SQL functions name their
  arguments after those segments (`focus.point.lat` -> `lat`, `boundary.gid` -> `gid`).
- `boundary.circle.lat/lon` would collide with `focus.point.lat/lon`, so the edge renames them to
  `circle_lat/circle_lon/circle_radius` on search, structured and autocomplete (three chained
  nginx `map`s; `scripts/dev/nginx.pgeo-rest.conf`). Reverse keeps `boundary.circle.radius`,
  which arrives as `radius`, as in Pelias.
- Input errors are caught in each SQL endpoint and returned as the Pelias envelope with HTTP 400
  (PostgREST's `response.status` setting). A parameter the SQL API does not declare makes
  PostgREST answer 404 ("function not found"); the edge turns that into a Pelias-shaped 400.

## Remaining differences

| Difference | Effect | Why / plan |
|------------|--------|------------|
| gids of OpenAddresses records differ (`openaddresses:address:us/me/statewide:<hash>` in Pelias, `openaddresses:address:<hash>` in pgeo) | A gid saved from Pelias cannot be passed to pgeo's `/v1/place` | Each engine derives its own record id; a mapping table is possible if clients store gids |
| `categories` values are the sources' own (Overture `restaurant`, OSM `natural=water`), not the Pelias taxonomy (`food`, `health`) | A Pelias-taxonomy filter matches nothing | A taxonomy mapping is future work |
| Unknown parameters: Pelias ignores them, pgeo pure SQL rejects them (400) | Clients sending undocumented parameters must drop them on the pure-SQL path (FastAPI ignores them) | PostgREST matches functions by argument names |
| `lang` is ignored | English names only | The Maine data is English |
| Ranking differs | Different, usually more accurate, first results (95.8% vs 75.5%) | By design; tests/accuracy |
| Pelias accepts `focus.point.lat=999`; pgeo returns 400 | Stricter validation | Kept |

## Found along the way

The `boundary.rect` filter returned nothing on pgeo (the rectangle was applied after the
candidate cut; its centre now anchors the tie-break), and reverse geocoding joined admin
polygons to features without an index on `feature.admin_id`, so every reverse request scanned
all 906,101 features. With the index, a full reverse lookup takes 4-30 ms instead of
hundreds; the load tests are being re-run to measure the effect.
