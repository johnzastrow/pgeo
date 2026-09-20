"""The demo page and the edge with one engine or two.

The deployment the study recommends is pgeo alone on a small VPS, so the page has to work with
no Pelias behind it: the shared client must talk to the announced engine, the Compare tab must
disappear, and the edge must serve pgeo on the canonical /v1/* paths.

    uv run --project pgeo pytest tests/web/test_engine_modes.py -q
"""

from __future__ import annotations

import pathlib

import pytest

jinja2 = pytest.importorskip("jinja2")

ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "infra/ansible/roles/edge/templates/pelias.conf.j2"
APP_JS = ROOT / "web/js/app.js"

BASE_VARS = dict(
    edge_rate_search=10, edge_rate_autocomplete=25, edge_listen_port=8080,
    edge_server_name="host", edge_demo_root="/srv/demo",
    edge_allowed_sources=["192.0.2.254"],
    ansible_facts={"default_ipv4": {"address": "127.0.0.1"}},
)  # fmt: skip


def render(**flags) -> str:
    env = jinja2.Environment(undefined=jinja2.ChainableUndefined, keep_trailing_newline=True)
    env.filters["bool"] = lambda v: str(v).lower() in ("true", "yes", "1", "on")  # Ansible's
    return env.from_string(TEMPLATE.read_text()).render(**BASE_VARS, **flags)


def locations(conf: str) -> list[str]:
    return [line.strip() for line in conf.splitlines() if line.strip().startswith("location")]


# ---- both engines -------------------------------------------------------------------------


def test_with_both_engines_pelias_keeps_the_canonical_paths():
    conf = render(pelias_enabled=True, pgeo_enabled=True)
    assert "upstream pelias_api" in conf
    assert any(loc.startswith("location = /v1/autocomplete") for loc in locations(conf))
    assert any("/pgeo/v1/search" in loc for loc in locations(conf))


def test_with_both_engines_the_page_is_offered_a_switch():
    conf = render(pelias_enabled=True, pgeo_enabled=True)
    assert '"base":""' in conf and '"base":"/pgeo"' in conf


# ---- pgeo alone ---------------------------------------------------------------------------


def test_without_pelias_there_is_no_pelias_upstream_or_route():
    conf = render(pelias_enabled=False, pgeo_enabled=True)
    assert "upstream pelias_api" not in conf
    assert "proxy_pass http://pelias_api" not in conf


def test_without_pelias_pgeo_answers_on_the_canonical_paths():
    """An existing Pelias client should need no change: same URLs, different engine."""
    locs = locations(render(pelias_enabled=False, pgeo_enabled=True))
    for path in ("/v1/autocomplete", "/v1/search", "/v1/search/structured", "/v1/attribution"):
        assert any(path in loc for loc in locs), f"{path} is not served"
    assert not any("/pgeo/v1/" in loc for loc in locs), "pgeo should not also sit under /pgeo"


def test_without_pelias_the_page_is_told_there_is_one_engine():
    conf = render(pelias_enabled=False, pgeo_enabled=True)
    assert '"kind":"pgeo"' in conf
    assert '"label":"Pelias"' not in conf


# ---- the page's own behaviour ---------------------------------------------------------------


def test_the_shared_client_follows_the_announced_engine():
    """With one engine the switch never renders, so the client has to be pointed at it
    explicitly - otherwise every search goes to whatever sits at /v1/* by default."""
    src = APP_JS.read_text()
    i = src.index("async function setupEngines")
    j = src.index("if (engines.length < 2)", i)
    assert "client.baseUrl = engines[0].base" in src[i:j], (
        "client.baseUrl must be set before the two-engine branch returns early"
    )


def test_the_compare_tab_needs_both_engines():
    src = APP_JS.read_text()
    assert "$('#tab-compare').hidden = !hasBoth" in src


def test_the_address_tab_needs_pgeo():
    src = APP_JS.read_text()
    assert "$('#tab-address').hidden = !hasPgeo" in src
