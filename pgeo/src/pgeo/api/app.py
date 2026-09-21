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

LAYERS = {"address", "venue", "street", "neighbourhood", "locality", "localadmin", "county", "region", "postalcode"}
LAYER_ALIASES = {"coarse": ["neighbourhood", "locality", "localadmin", "county", "region", "postalcode"]}
SOURCES = {"openaddresses", "openstreetmap", "whosonfirst", "gnis", "zcta", "overture", "interpolation"}
SOURCE_ALIASES = {"oa": "openaddresses", "osm": "openstreetmap", "wof": "whosonfirst"}
MAX_TEXT = 200
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
        names = await con.fetch(
            "SELECT DISTINCT lower(name) AS n FROM pgeo.feature WHERE layer IN ('locality', 'localadmin')"
        )
    app.state.rules = RuleParser({r["n"] for r in names})
    yield
    await app.state.http.aclose()
    await app.state.pool.close()


app = FastAPI(title="pgeo", version=ENGINE_VERSION, lifespan=lifespan, docs_url=None, redoc_url=None)


@app.exception_handler(BadRequest)
async def bad_request(_: Request, exc: BadRequest) -> JSONResponse:
    return JSONResponse(status_code=400, content=envelope({}, [], errors=[str(exc)]))


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
    if len(v) > MAX_TEXT:
        raise BadRequest(f"'{name}' is longer than {MAX_TEXT} characters")
    return v


def finite(v: str | None, name: str, lo: float, hi: float) -> float | None:
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except ValueError as e:
        raise BadRequest(f"'{name}' must be a number") from e
    if not math.isfinite(f) or not lo <= f <= hi:
        raise BadRequest(f"'{name}' must be between {lo} and {hi}")
    return f


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
    return max(1, min(n, 40))


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
    radius = finite(q.get("boundary.circle.radius"), "boundary.circle.radius", 0.001, 1000)
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
        finite(q.get(f"boundary.rect.{k}"), f"boundary.rect.{k}", -180, 180)
        for k in ("min_lon", "min_lat", "max_lon", "max_lat")
    ]
    if any(r is not None for r in rect) and any(r is None for r in rect):
        raise BadRequest("boundary.rect needs min_lon, min_lat, max_lon, max_lat")
    return {
        "focus_lon": flon,
        "focus_lat": flat,
        "rect": rect if rect[0] is not None else None,
        "layers": layers_param(q.get("layers")),
        "sources": sources_param(q.get("sources")),
        "size": size_param(q.get("size")),
        "circle": circle(q),
    } | extras(q)


# ---- parsing -------------------------------------------------------------------------------


async def parse(request: Request, text: str) -> Parsed:
    mode = request.app.state.settings.parse_mode
    override = request.query_params.get("pgeo.parse")  # experiments only
    if override in ("service", "extension", "none"):
        mode = override
    if mode == "service":
        try:
            r = await request.app.state.http.get("/parse", params={"address": text})
            r.raise_for_status()
            return merge_rule_fallback(from_libpostal(text, r.json()), request.app.state.rules.parse(text))
        except (httpx.HTTPError, ValueError):
            log.warning("libpostal service unavailable; falling back to rule parser")
    elif mode == "extension":
        async with request.app.state.pool.acquire() as con:
            comp = await con.fetchval("SELECT postal_parse($1)", text)
        lp = from_libpostal(text, json.loads(comp) if isinstance(comp, str) else comp)
        return merge_rule_fallback(lp, request.app.state.rules.parse(text))
    return request.app.state.rules.parse(text)


# ---- output --------------------------------------------------------------------------------


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
        "country": "United States",
        "country_gid": "whosonfirst:country:85633793",
        "country_a": "USA",
        "country_code": "US",
        "region": r["region"],
        "region_a": r["region_a"],
        # Pelias (WOF) names counties "Cumberland County"
        "county": r["county"] if not r["county"] or r["county"].endswith(" County") else f"{r['county']} County",
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

SEARCH_SQL = "SELECT * FROM geocode.search($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)"


async def run_search(request: Request, text: str | None, p: Parsed, c: dict) -> list:
    async with request.app.state.pool.acquire() as con:
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
        )


@app.get("/v1/search")
async def search(request: Request, text: Annotated[str | None, Query()] = None):
    q = dict(request.query_params)
    text = clean_text(text)
    c = common(q)
    p = await parse(request, text)
    rows = await run_search(request, text, p, c)
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
    async with request.app.state.pool.acquire() as con:
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
            c["country"],
        )
    return envelope({k: v for k, v in fields.items() if v} | {"size": c["size"]}, rows, parsed=p)


@app.get("/v1/autocomplete")
async def autocomplete(request: Request, text: Annotated[str | None, Query()] = None):
    q = dict(request.query_params)
    text = clean_text(text)
    c = common(q)
    async with request.app.state.pool.acquire() as con:
        rows = await con.fetch(
            "SELECT * FROM geocode.autocomplete($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)",
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
        )
    return envelope({"text": text, "size": c["size"]}, rows)


@app.get("/v1/reverse")
async def reverse(request: Request):
    q = dict(request.query_params)
    lat = finite(q.get("point.lat"), "point.lat", -90, 90)
    lon = finite(q.get("point.lon"), "point.lon", -180, 180)
    if lat is None or lon is None:
        raise BadRequest("point.lat and point.lon are required")
    radius = finite(q.get("boundary.circle.radius"), "boundary.circle.radius", 0.001, 50)
    c = common({k: v for k, v in q.items() if not k.startswith(("focus.", "boundary.rect", "boundary.circle"))})
    async with request.app.state.pool.acquire() as con:
        rows = await con.fetch(
            "SELECT * FROM geocode.reverse($1, $2, $3, $4, $5, $6, $7, $8, $9)",
            lon,
            lat,
            c["layers"],
            c["sources"],
            radius,
            c["size"],
            c["gid"],
            c["categories"],
            c["country"],
        )
    return envelope({"point.lat": lat, "point.lon": lon, "size": c["size"]}, rows)


@app.get("/v1/place")
async def place(request: Request, ids: Annotated[str | None, Query()] = None):
    ids = clean_text(ids, "ids")
    gids = [g.strip() for g in ids.split(",") if g.strip()][:40]
    async with request.app.state.pool.acquire() as con:
        rows = await con.fetch("SELECT * FROM geocode.place($1)", gids)
    return envelope({"ids": gids}, rows)


@app.get("/v1/attribution", response_class=HTMLResponse)
async def attribution(request: Request) -> HTMLResponse:
    """The data licences as an HTML page, as Pelias serves them.

    The page is built in SQL (geocode_api.v1_attribution), so both front ends and the
    pure-SQL path serve exactly the same text."""
    async with request.app.state.pool.acquire() as con:
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
    radius = finite(q.get("radius"), "radius", 0.001, 5)
    ids = q.get("ids")
    text = q.get("text")
    unit = q.get("unit")
    for name, v in (("ids", ids), ("text", text), ("unit", unit)):
        if v is not None and len(v) > 2000:
            raise BadRequest(f"{name} is too long")
    async with request.app.state.pool.acquire() as con:
        try:
            doc = await con.fetchval(
                "SELECT geocode_api.v1_address($1, $2, $3, $4, $5, $6)", ids, text, lat, lon, radius, unit
            )
        except asyncpg.exceptions.InvalidParameterValueError as e:  # SQLSTATE 22023: bad input
            raise BadRequest(e.message) from None
    doc = json.loads(doc) if isinstance(doc, str) else doc
    if doc.get("geocoding", {}).get("errors"):  # the SQL function returns input errors as data
        return JSONResponse(status_code=400, content=doc)
    return doc


@app.get("/health")
async def health(request: Request):
    async with request.app.state.pool.acquire() as con:
        await con.fetchval("SELECT 1")
    return {"status": "ok"}
