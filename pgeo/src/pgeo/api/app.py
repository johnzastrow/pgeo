"""pgeo API: Pelias-compatible /v1 endpoints over the SQL functions in schema geocode.

Run:  uvicorn pgeo.api.app:app --host 127.0.0.1 --port 4500 --workers 2
The API connects as the read-only role pgeo_api. All SQL is parameterized.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from typing import Annotated

import asyncpg
import httpx
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from pgeo.api.parse import Parsed, RuleParser, from_libpostal, merge_rule_fallback
from pgeo.settings import Settings

log = logging.getLogger("pgeo.api")

LAYERS = {
    "address", "venue", "street", "neighbourhood", "locality", "localadmin", "county", "region", "country",
    "postalcode",
}
LAYER_ALIASES = {"coarse": ["neighbourhood", "locality", "localadmin", "county", "region", "country", "postalcode"]}
SOURCES = {"openaddresses", "openstreetmap", "whosonfirst", "gnis", "zcta", "overture", "interpolation"}
SOURCE_ALIASES = {"oa": "openaddresses", "osm": "openstreetmap", "wof": "whosonfirst"}
# Request limits. These are the defaults; at startup they are replaced by what the database
# reports for this role (geocode.api_limits(), which reads the pgeo.* settings), so FastAPI and
# the SQL front end always enforce the same values. See pgeo/sql/010_base.sql.
LIMITS: dict[str, float] = {
    "max_text": 200,
    "max_size": 40,
    "max_circle_km": 100,
    "max_reverse_km": 50,
    "max_address_km": 5,
}
# How long a request waits for a pooled connection before it is turned away with 503. Without a
# limit, a few slow queries queue every other request behind them indefinitely.
ACQUIRE_TIMEOUT = float(os.environ.get("PGEO_ACQUIRE_TIMEOUT", "3"))
try:
    ENGINE_VERSION = version("pgeo")  # single source: pgeo/pyproject.toml
except PackageNotFoundError:
    ENGINE_VERSION = "0+unknown"
ATTRIBUTION = os.environ.get(
    "PGEO_ATTRIBUTION",
    "pgeo: OpenStreetMap, OpenAddresses, Who's On First, USGS GNIS, US Census, Overture Maps",
)


class BadRequest(Exception):
    pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = Settings.load()
    app.state.settings = s
    app.state.pool = await asyncpg.create_pool(
        s.dsn,
        min_size=1,
        max_size=s.pool_max,
        command_timeout=15,
        server_settings={"application_name": "pgeo-api"},
    )
    app.state.http = httpx.AsyncClient(base_url=s.libpostal_url, timeout=5.0)
    async with app.state.pool.acquire() as con:
        # The state each town is in, so the typo fallback can prefer one in the state the query
        # named: "Hosuton, TX" is one edit from Hosston, Louisiana and two from Houston, Texas.
        names = await con.fetch(
            "SELECT DISTINCT lower(name) AS n, region_a, coarse FROM pgeo.feature "
            "WHERE layer IN ('locality', 'localadmin')"
        )
        # Every US state and territory, not only the build's own: a query naming California is
        # answered from the worldwide tier, and the parser must know "CA" is a state.
        regions = await con.fetch("SELECT lower(name) AS name, lower(abbr) AS abbr, abbr AS code "
                                  "FROM geocode.state_ref")
        home = {r["abbr"] for r in await con.fetch("SELECT abbr FROM geocode.region_ref")}
        # ISO alpha-2 -> alpha-3, so country_a reads as Pelias gives it (the data carries alpha-2)
        ALPHA3.clear()
        ALPHA3.update({r["alpha2"]: r["alpha3"] for r in await con.fetch(
            "SELECT alpha2, alpha3 FROM geocode.country_ref")})
    states = {}
    for r in regions:
        states[r["abbr"]] = r["code"]
        states[r["name"]] = r["code"]
    town_states: dict[str, set[str]] = {}
    for r in names:
        if r["region_a"]:
            town_states.setdefault(r["n"], set()).add(r["region_a"])
    # every town, and separately the build's own (the worldwide tier is flagged `coarse`)
    app.state.rules = RuleParser({r["n"] for r in names}, states, town_states,
                                 home_localities={r["n"] for r in names if not r["coarse"]},
                                 home_states=home)
    await load_limits(app.state.pool)
    yield
    await app.state.http.aclose()
    await app.state.pool.close()


app = FastAPI(
    title="pgeo", version=ENGINE_VERSION, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
)


async def load_limits(pool: asyncpg.Pool) -> None:
    """Take the request limits from the database, so both front ends enforce the same ones."""
    try:
        async with pool.acquire() as con:
            doc = await con.fetchval("SELECT geocode.api_limits()")
    except asyncpg.exceptions.UndefinedFunctionError:
        # A database loaded before the limits were configurable enforces exactly the defaults.
        log.warning("geocode.api_limits() not found; using the default limits")
        return
    found = json.loads(doc) if isinstance(doc, str) else doc
    for key in LIMITS:
        if key in found:
            LIMITS[key] = float(found[key])
    log.info("request limits: %s", LIMITS)


def acquire(request: Request):
    """A pooled connection, or TimeoutError (answered as 503) if none frees up in time."""
    return request.app.state.pool.acquire(timeout=ACQUIRE_TIMEOUT)


@app.exception_handler(BadRequest)
async def bad_request(_: Request, exc: BadRequest) -> JSONResponse:
    return JSONResponse(status_code=400, content=envelope({}, [], errors=[str(exc)]))


@app.exception_handler(TimeoutError)
async def busy(_: Request, exc: TimeoutError) -> JSONResponse:
    # Pool exhausted, or a query hit command_timeout: the service is overloaded, not broken.
    log.warning("timed out waiting for the database")
    return JSONResponse(status_code=503, content=envelope({}, [], errors=["service busy, try again"]))


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error")  # details in logs only; generic message to clients
    return JSONResponse(status_code=500, content=envelope({}, [], errors=["internal error"]))


# ---- parameter helpers ---------------------------------------------------------------------


def clean_text(v: str | None, name: str = "text", required: bool = True) -> str | None:
    v = (v or "").strip()
    if not v:
        if required:
            raise BadRequest(f"missing param '{name}'")
        return None
    if len(v) > LIMITS["max_text"]:
        raise BadRequest(f"'{name}' is longer than {LIMITS['max_text']:g} characters")
    return v


def finite(v: str | None, name: str, lo: float, hi: float, unit: str = "") -> float | None:
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except ValueError as e:
        raise BadRequest(f"'{name}' must be a number") from e
    if not math.isfinite(f) or not lo <= f <= hi:
        raise BadRequest(f"'{name}' must be between {lo:g} and {hi:g}{unit}")
    return f


def bool_param(v: str | None, name: str, default: bool = True) -> bool:
    """A true/false request parameter; absent means `default`. Mirrors geocode.check_bool."""
    if v is None or not v.strip():
        return default
    t = v.strip().lower()
    if t in ("true", "t", "1", "yes", "y", "on"):
        return True
    if t in ("false", "f", "0", "no", "n", "off"):
        return False
    raise BadRequest(f"{name} must be true or false")


def layers_param(v: str | None) -> list[str] | None:
    if not v:
        return None
    out: list[str] = []
    for x in v.split(","):
        x = x.strip()
        out += LAYER_ALIASES.get(x, [x])
    bad = [x for x in out if x not in LAYERS]
    if bad:
        raise BadRequest(f"invalid layers: {', '.join(bad)}")
    return out


def sources_param(v: str | None) -> list[str] | None:
    if not v:
        return None
    out = [SOURCE_ALIASES.get(x.strip(), x.strip()) for x in v.split(",")]
    bad = [x for x in out if x not in SOURCES]
    if bad:
        raise BadRequest(f"invalid sources: {', '.join(bad)}")
    return out


def size_param(v: str | None) -> int:
    if v is None:
        return 10
    try:
        n = int(v)
    except ValueError as e:
        raise BadRequest("'size' must be an integer") from e
    return max(1, min(n, int(LIMITS["max_size"])))


COUNTRY_RE = re.compile(r"^[A-Za-z]{2,3}$")
GID_RE = re.compile(r"^[a-z_]+:[a-z]+:[A-Za-z0-9_/.:-]+$")
LANG_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
CATEGORY_RE = re.compile(r"^[a-z0-9_=:.-]{1,60}$")


def extras(q: dict) -> dict:
    """boundary.country, boundary.gid, categories; lang and api_key are validated and ignored
    (same rules as geocode.check_extras in pgeo/sql/050_api.sql)."""
    country = (q.get("boundary.country") or "").strip() or None
    if country and not COUNTRY_RE.match(country):
        raise BadRequest("boundary.country must be an ISO 3166 alpha-2 or alpha-3 code")
    gid = (q.get("boundary.gid") or "").strip() or None
    if gid and (len(gid) > 200 or not GID_RE.match(gid)):
        raise BadRequest("boundary.gid must look like whosonfirst:locality:85948877")
    lang = q.get("lang")
    if lang and not LANG_RE.match(lang):
        raise BadRequest("invalid lang")
    if len(q.get("api_key") or "") > 200:
        raise BadRequest("invalid api_key")
    cats = [c.strip().lower() for c in (q.get("categories") or "").split(",") if c.strip()] or None
    if cats and (len(cats) > 20 or not all(CATEGORY_RE.match(c) for c in cats)):
        raise BadRequest("invalid categories")
    return {"country": country, "gid": gid, "categories": cats}


def circle(q: dict) -> list[float] | None:
    """boundary.circle.lat/lon/radius (km, default 50) as [lon, lat, radius], or None."""
    lat = finite(q.get("boundary.circle.lat"), "boundary.circle.lat", -90, 90)
    lon = finite(q.get("boundary.circle.lon"), "boundary.circle.lon", -180, 180)
    radius = finite(q.get("boundary.circle.radius"), "boundary.circle.radius", 0.001, LIMITS["max_circle_km"], " km")
    if lat is None and lon is None:
        if radius is not None:
            raise BadRequest("boundary.circle.radius needs boundary.circle.lat and boundary.circle.lon")
        return None
    if lat is None or lon is None:
        raise BadRequest("boundary.circle needs both lat and lon")
    return [lon, lat, 50.0 if radius is None else radius]


def common(q: dict) -> dict:
    """focus.point, boundary.rect, boundary.circle, boundary.country, boundary.gid, categories,
    layers, sources, size (Pelias parameter names)."""
    flat = finite(q.get("focus.point.lat"), "focus.point.lat", -90, 90)
    flon = finite(q.get("focus.point.lon"), "focus.point.lon", -180, 180)
    if (flat is None) != (flon is None):
        raise BadRequest("focus.point needs both lat and lon")
    rect = [
        finite(q.get(f"boundary.rect.{k}"), f"boundary.rect.{k}", -lim, lim)
        for k, lim in (("min_lon", 180), ("min_lat", 90), ("max_lon", 180), ("max_lat", 90))
    ]
    if any(r is not None for r in rect) and any(r is None for r in rect):
        raise BadRequest("boundary.rect needs min_lon, min_lat, max_lon, max_lat")
    if rect[0] is not None:
        for lo, hi, axis in ((rect[0], rect[2], "lon"), (rect[1], rect[3], "lat")):
            if lo > hi:
                raise BadRequest(f"boundary.rect.min_{axis} ({lo:g}) is greater than boundary.rect.max_{axis} ({hi:g})")
    return {
        "focus_lon": flon,
        "focus_lat": flat,
        "rect": rect if rect[0] is not None else None,
        "layers": layers_param(q.get("layers")),
        "sources": sources_param(q.get("sources")),
        "size": size_param(q.get("size")),
        "circle": circle(q),
        # the worldwide tier answers unless the caller turns it off (coarse=false)
        "coarse": bool_param(q.get("coarse"), "coarse"),
    } | extras(q)


# ---- parsing -------------------------------------------------------------------------------


async def parse(request: Request, text: str) -> Parsed:
    mode = request.app.state.settings.parse_mode
    # Per-request override, for A/B experiments only. Off unless PGEO_ALLOW_PARSE_OVERRIDE is set,
    # since it would let any client force the libpostal or in-database parse on every request.
    override = request.query_params.get("pgeo.parse")
    if request.app.state.settings.allow_parse_override and override in ("service", "extension", "none"):
        mode = override
    if mode == "service":
        try:
            r = await request.app.state.http.get("/parse", params={"address": text})
            r.raise_for_status()
            return merge_rule_fallback(from_libpostal(text, r.json()), request.app.state.rules.parse(text))
        except (httpx.HTTPError, ValueError):
            log.warning("libpostal service unavailable; falling back to rule parser")
    elif mode == "extension":
        async with acquire(request) as con:
            comp = await con.fetchval("SELECT postal_parse($1)", text)
        lp = from_libpostal(text, json.loads(comp) if isinstance(comp, str) else comp)
        return merge_rule_fallback(lp, request.app.state.rules.parse(text))
    return request.app.state.rules.parse(text)


# ---- output --------------------------------------------------------------------------------


# ISO 3166-1 alpha-2 -> alpha-3, filled from geocode.country_ref at startup
ALPHA3: dict[str, str] = {}


def feature_json(r: asyncpg.Record) -> dict:
    props = {
        "id": r["source_id"],
        "gid": r["gid"],
        "layer": r["layer"],
        "source": r["source"],
        "source_id": r["source_id"],
        "name": r["name"],
        "housenumber": r["housenumber"],
        "street": r["street"],
        "postalcode": r["postcode"],
        "confidence": None if r["confidence"] is None else round(float(r["confidence"]), 3),
        "match_type": r["match_type"],
        "accuracy": r["accuracy"],
        "distance": None if r["distance_km"] is None else round(float(r["distance_km"]), 3),
        # The country is the feature's own: country_a is its ISO alpha-3 code (as Pelias returns
        # it; none for a territory outside ISO 3166-1), country_code the alpha-2 the data carries.
        # coarse marks the worldwide tier.
        "country": r["country"],
        "country_gid": "whosonfirst:country:85633793" if r["country_a"] == "US" else None,
        "country_a": ALPHA3.get(r["country_a"]),
        "country_code": r["country_a"],
        "coarse": True if r["coarse"] else None,
        "region": r["region"],
        "region_a": r["region_a"],
        # Already carries its own suffix from Who's on First. This used to append " County"
        # to every county name, which made Louisiana's parishes "Acadia County".
        "county": r["county"],
        "localadmin": r["localadmin"],
        "locality": r["locality"],
        "neighbourhood": r["neighbourhood"],
        "label": r["label"],
    }
    if r["category"]:
        props["category"] = list(r["category"])
    if r["addendum"]:
        props["addendum"] = json.loads(r["addendum"]) if isinstance(r["addendum"], str) else r["addendum"]
    if r["hier"]:  # locality_gid, county_gid, county_a, ... (set at build time)
        props |= json.loads(r["hier"]) if isinstance(r["hier"], str) else dict(r["hier"])
    props = {k: v for k, v in props.items() if v is not None}
    f = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]}, "properties": props}
    if r["bbox"]:
        f["bbox"] = list(r["bbox"])
    return f


def envelope(query: dict, rows: list, errors: list[str] | None = None, parsed: Parsed | None = None) -> dict:
    feats = [feature_json(r) for r in rows]
    geo: dict = {
        "version": "0.2",
        "attribution": ATTRIBUTION,
        "query": query,
        "engine": {"name": "pgeo", "author": "pelias_maine", "version": ENGINE_VERSION},
        "timestamp": int(time.time() * 1000),
    }
    if parsed is not None:
        geo["query"]["parsed_text"] = {
            k: v
            for k, v in {
                "housenumber": parsed.housenumber,
                "street": parsed.street,
                "city": parsed.locality,
                "postalcode": parsed.postcode,
                "state": parsed.state,
                "name": parsed.name,
            }.items()
            if v
        }
    if errors:
        geo["errors"] = errors
    out = {"geocoding": geo, "type": "FeatureCollection", "features": feats}
    if feats:
        xs = [f["geometry"]["coordinates"][0] for f in feats]
        ys = [f["geometry"]["coordinates"][1] for f in feats]
        out["bbox"] = [min(xs), min(ys), max(xs), max(ys)]
    return out


# ---- endpoints -----------------------------------------------------------------------------

SEARCH_SQL = (
    "SELECT * FROM geocode.search($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)"
)


async def run_search(request: Request, text: str | None, p: Parsed, c: dict) -> list:
    async with acquire(request) as con:
        return await con.fetch(
            SEARCH_SQL,
            text,
            p.name,  # the SQL also searches the full text unless an address was parsed
            p.housenumber,
            p.street,
            p.locality,
            p.postcode,
            c["focus_lon"],
            c["focus_lat"],
            c["layers"],
            c["sources"],
            c["rect"],
            c["size"],
            c["circle"],
            c["gid"],
            c["categories"],
            c["country"],
            # the state the parser found, so "Texarkana, AR" is not answered with Texarkana, TX
            p.state,
            c["coarse"],
        )


@app.get("/v1/search")
async def search(request: Request, text: Annotated[str | None, Query()] = None):
    q = dict(request.query_params)
    text = clean_text(text)
    c = common(q)
    # A country named at the end ("Oslo, Norway") limits the search to it and is not searched for
    # as a place; the same split the SQL edge makes (geocode.split_country).
    searched = text
    if c["country"] is None:
        async with acquire(request) as con:
            sc = await con.fetchrow("SELECT rest, cc FROM geocode.split_country($1)", text)
        if sc and sc["cc"]:
            searched, c["country"] = sc["rest"], sc["cc"]
    p = await parse(request, searched)
    rows = await run_search(request, searched, p, c)
    return envelope({"text": text, "size": c["size"]}, rows, parsed=p)


@app.get("/v1/search/structured")
async def structured(request: Request):
    q = dict(request.query_params)
    fields = {
        k: clean_text(q.get(k), k, required=False)
        for k in ("address", "neighbourhood", "locality", "county", "region", "postalcode", "country")
    }
    if not any(fields.values()):
        raise BadRequest("at least one of address, locality, postalcode, county, region is required")
    c = common(q)
    p = Parsed(text=" ".join(v for v in fields.values() if v))
    if fields["address"]:
        ap = request.app.state.rules.parse(fields["address"])
        p.housenumber, p.street = ap.housenumber, ap.street
        if not ap.housenumber:
            p.name = fields["address"]
    p.locality = fields["locality"] or fields["neighbourhood"]
    p.postcode = fields["postalcode"]
    if not fields["address"]:
        p.name = p.locality or fields["county"] or p.postcode
    async with acquire(request) as con:
        # Nothing but a region: that asks for the region itself, and the two front ends used to
        # answer it differently. Everywhere else region is a filter, not text, but p.text above
        # joins every field - so "region=ME" alone searched the free text for "ME" and came back
        # with venues called "Mrs & Me", while the SQL edge, which leaves region out of its text
        # entirely, had no name to search for and returned nothing. Resolve it to the region's
        # own name through the same helper the SQL edge calls, and pin the answer to the region
        # layer unless the caller asked for particular layers.
        if fields["region"] and not any(
            v for k, v in fields.items() if k not in ("region", "country")
        ):
            name = await con.fetchval("SELECT geocode.region_name($1)", fields["region"])
            # A region this build does not cover resolves to nothing, and the query then has no
            # subject at all - which is an empty answer, not a free-text search for the word.
            # "region=Vermont" against a Maine build otherwise found Vermont Avenue in Bangor.
            p.name = name
            p.text = name or ""
            if name and c["layers"] is None:
                c["layers"] = ["region"]
        rows = await con.fetch(
            SEARCH_SQL,
            p.text,
            p.name,
            p.housenumber,
            p.street,
            p.locality,
            p.postcode,
            c["focus_lon"],
            c["focus_lat"],
            c["layers"],
            c["sources"],
            c["rect"],
            c["size"],
            c["circle"],
            c["gid"],
            c["categories"],
            # a country given by name or code; the same helper as the SQL edge
            (await con.fetchval("SELECT geocode.country_code_of($1)", fields["country"]))
            if fields["country"] and c["country"] is None else c["country"],
            fields["region"],
            c["coarse"],
        )
    return envelope({k: v for k, v in fields.items() if v} | {"size": c["size"]}, rows, parsed=p)


@app.get("/v1/autocomplete")
async def autocomplete(request: Request, text: Annotated[str | None, Query()] = None):
    q = dict(request.query_params)
    text = clean_text(text)
    c = common(q)
    async with acquire(request) as con:
        rows = await con.fetch(
            "SELECT * FROM geocode.autocomplete($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)",
            text,
            c["focus_lon"],
            c["focus_lat"],
            c["layers"],
            c["sources"],
            c["rect"],
            c["size"],
            c["circle"],
            c["gid"],
            c["categories"],
            c["country"],
            c["coarse"],
        )
    return envelope({"text": text, "size": c["size"]}, rows)


@app.get("/v1/reverse")
async def reverse(request: Request):
    q = dict(request.query_params)
    lat = finite(q.get("point.lat"), "point.lat", -90, 90)
    lon = finite(q.get("point.lon"), "point.lon", -180, 180)
    if lat is None or lon is None:
        raise BadRequest("point.lat and point.lon are required")
    radius = finite(q.get("boundary.circle.radius"), "boundary.circle.radius", 0.001, LIMITS["max_reverse_km"], " km")
    c = common({k: v for k, v in q.items() if not k.startswith(("focus.", "boundary.rect", "boundary.circle"))})
    async with acquire(request) as con:
        rows = await con.fetch(
            "SELECT * FROM geocode.reverse($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
            lon,
            lat,
            c["layers"],
            c["sources"],
            radius,
            c["size"],
            c["gid"],
            c["categories"],
            c["country"],
            c["coarse"],
        )
    return envelope({"point.lat": lat, "point.lon": lon, "size": c["size"]}, rows)


@app.get("/v1/place")
async def place(request: Request, ids: Annotated[str | None, Query()] = None):
    ids = clean_text(ids, "ids")
    gids = [g.strip() for g in ids.split(",") if g.strip()][: int(LIMITS["max_size"])]
    async with acquire(request) as con:
        rows = await con.fetch("SELECT * FROM geocode.place($1)", gids)
    return envelope({"ids": gids}, rows)


@app.get("/v1/attribution", response_class=HTMLResponse)
async def attribution(request: Request) -> HTMLResponse:
    """The data licences as an HTML page, as Pelias serves them.

    The page is built in SQL (geocode_api.v1_attribution), so both front ends and the
    pure-SQL path serve exactly the same text."""
    async with acquire(request) as con:
        html = await con.fetchval("SELECT geocode_api.v1_attribution()")
    return HTMLResponse(content=html)


@app.get("/v1/address")
async def address(request: Request):
    """Structured USPS Publication 28 address (pgeo extension; not a Pelias endpoint).

    Exactly one of ids=, text=, or point.lat + point.lon; optional radius (km) and unit.
    Validation and the work are in SQL (geocode_api.v1_address), shared with PostgREST."""
    q = dict(request.query_params)
    lat = finite(q.get("point.lat"), "point.lat", -90, 90)
    lon = finite(q.get("point.lon"), "point.lon", -180, 180)
    radius = finite(q.get("radius"), "radius", 0.001, LIMITS["max_address_km"], " km")
    ids = q.get("ids")
    text = q.get("text")
    unit = q.get("unit")
    for name, v in (("ids", ids), ("text", text), ("unit", unit)):
        if v is not None and len(v) > 2000:
            raise BadRequest(f"{name} is too long")
    async with acquire(request) as con:
        try:
            doc = await con.fetchval(
                "SELECT geocode_api.v1_address($1, $2, $3, $4, $5, $6)", ids, text, lat, lon, radius, unit
            )
        except asyncpg.exceptions.InvalidParameterValueError as e:  # SQLSTATE 22023: bad input
            # Only pgeo's own validation messages (HINT 'pgeo') are written for clients; a 22023
            # from inside PostgreSQL or PostGIS describes internals, so it is not passed on.
            raise BadRequest(e.message if e.hint == "pgeo" else "invalid parameter value") from None
    doc = json.loads(doc) if isinstance(doc, str) else doc
    if doc.get("geocoding", {}).get("errors"):  # the SQL function returns input errors as data
        return JSONResponse(status_code=400, content=doc)
    return doc


@app.get("/health")
async def health(request: Request):
    async with acquire(request) as con:
        await con.fetchval("SELECT 1")
    return {"status": "ok"}
