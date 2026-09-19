"""pgeo API: Pelias-compatible /v1 endpoints over the SQL functions in schema geocode.

Run:  uvicorn pgeo.api.app:app --host 127.0.0.1 --port 4500 --workers 2
The API connects as the read-only role pgeo_api. All SQL is parameterized.
"""

from __future__ import annotations

import json
import logging
import math
import time
from contextlib import asynccontextmanager
from typing import Annotated

import asyncpg
import httpx
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

from pgeo.api.parse import Parsed, RuleParser, from_libpostal
from pgeo.settings import Settings

log = logging.getLogger("pgeo.api")

LAYERS = {"address", "venue", "street", "neighbourhood", "locality", "localadmin", "county", "region", "postalcode"}
LAYER_ALIASES = {"coarse": ["neighbourhood", "locality", "localadmin", "county", "region", "postalcode"]}
SOURCES = {"openaddresses", "openstreetmap", "whosonfirst", "gnis", "zcta", "overture", "interpolation"}
SOURCE_ALIASES = {"oa": "openaddresses", "osm": "openstreetmap", "wof": "whosonfirst"}
MAX_TEXT = 200
ATTRIBUTION = "https://geocoder.example.org/ (pgeo: OSM, OpenAddresses, WOF, USGS GNIS, US Census, Overture)"


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


app = FastAPI(title="pgeo", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)


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


def common(q: dict) -> dict:
    """focus.point, boundary.rect, layers, sources, size (Pelias parameter names)."""
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
    }


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
            return from_libpostal(text, r.json())
        except (httpx.HTTPError, ValueError):
            log.warning("libpostal service unavailable; falling back to rule parser")
    elif mode == "extension":
        async with request.app.state.pool.acquire() as con:
            comp = await con.fetchval("SELECT postal_parse($1)", text)
        return from_libpostal(text, json.loads(comp) if isinstance(comp, str) else comp)
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
        "country_a": "USA",
        "region": r["region"],
        "region_a": r["region_a"],
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
        "engine": {"name": "pgeo", "author": "pelias_maine", "version": "0.1.0"},
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

SEARCH_SQL = "SELECT * FROM geocode.search($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)"


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
        )
    return envelope({k: v for k, v in fields.items() if v} | {"size": c["size"]}, rows, parsed=p)


@app.get("/v1/autocomplete")
async def autocomplete(request: Request, text: Annotated[str | None, Query()] = None):
    q = dict(request.query_params)
    text = clean_text(text)
    c = common(q)
    async with request.app.state.pool.acquire() as con:
        rows = await con.fetch(
            "SELECT * FROM geocode.autocomplete($1, $2, $3, $4, $5, $6, $7)",
            text,
            c["focus_lon"],
            c["focus_lat"],
            c["layers"],
            c["sources"],
            c["rect"],
            c["size"],
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
    c = common({k: v for k, v in q.items() if not k.startswith(("focus.", "boundary.rect"))})
    async with request.app.state.pool.acquire() as con:
        rows = await con.fetch(
            "SELECT * FROM geocode.reverse($1, $2, $3, $4, $5, $6)",
            lon,
            lat,
            c["layers"],
            c["sources"],
            radius,
            c["size"],
        )
    return envelope({"point.lat": lat, "point.lon": lon, "size": c["size"]}, rows)


@app.get("/v1/place")
async def place(request: Request, ids: Annotated[str | None, Query()] = None):
    ids = clean_text(ids, "ids")
    gids = [g.strip() for g in ids.split(",") if g.strip()][:40]
    async with request.app.state.pool.acquire() as con:
        rows = await con.fetch("SELECT * FROM geocode.place($1)", gids)
    return envelope({"ids": gids}, rows)


@app.get("/health")
async def health(request: Request):
    async with request.app.state.pool.acquire() as con:
        await con.fetchval("SELECT 1")
    return {"status": "ok"}
