"""API keys at the edge (Phase 11).

The edge refuses /v1/* without a known key in the X-API-Key header; keys are stored as SHA-256
hashes and never appear in a URL or a log. These tests hold that against a live edge and against
the configuration that produces it.

    PGEO_AUTH_EDGE      the edge to test (default: the dev edge, http://127.0.0.1:8088)
    PGEO_AUTH_PREFIX    pgeo's path prefix on it ("/pgeo-sql" on the dev edge, "/pgeo" on VM 120)
    PGEO_API_KEY        a key the edge accepts (or data/dev_api_key from scripts/dev_web.sh)

Run: uv run --project pgeo pytest tests/security/test_api_keys.py -q
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

httpx = pytest.importorskip("httpx")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
import apikey

EDGE = os.environ.get("PGEO_AUTH_EDGE", "http://127.0.0.1:8088")
PREFIX = os.environ.get("PGEO_AUTH_PREFIX", "/pgeo-sql")
KEY = apikey.api_key()
GOOD = {"X-API-Key": KEY}
WELL_FORMED_WRONG = "pgeo_" + "x" * 43

API_PATHS = ["/v1/autocomplete?text=portland", "/v1/search?text=portland",
             "/v1/search/structured?address=389+congress+st&locality=portland",
             "/v1/reverse?point.lat=43.6591&point.lon=-70.2568"]  # fmt: skip


def get(path: str, **kw) -> httpx.Response:
    try:
        return httpx.get(f"{EDGE}{path}", timeout=15, **kw)
    except httpx.HTTPError as e:
        pytest.skip(f"{EDGE} not reachable: {e}")


def need_key():
    if not KEY:
        pytest.skip("no API key: set PGEO_API_KEY or run scripts/dev_web.sh")


# ---- the check itself -----------------------------------------------------------------------------


@pytest.mark.parametrize("path", API_PATHS)
def test_the_api_refuses_a_request_with_no_key(path):
    r = get(PREFIX + path)
    assert r.status_code == 401
    body = r.json()
    assert body["geocoding"]["errors"] and "X-API-Key" in body["geocoding"]["errors"][0]
    assert body["features"] == [], "a refusal must carry no data"


def test_the_refusal_says_how_to_authenticate_and_is_not_sniffable():
    r = get(PREFIX + API_PATHS[0])
    assert r.status_code == 401
    assert "X-API-Key" in r.headers.get("www-authenticate", "")
    assert r.headers.get("content-type", "").startswith("application/json")
    assert r.headers.get("x-content-type-options") == "nosniff"


@pytest.mark.parametrize("bad", [WELL_FORMED_WRONG, "pgeo_short", "not-a-key", "pgeo_" + "x" * 44,
                                 "Bearer " + WELL_FORMED_WRONG, "a" * 4000])  # fmt: skip
def test_a_wrong_or_malformed_key_is_refused(bad):
    r = get(PREFIX + API_PATHS[0], headers={"X-API-Key": bad})
    assert r.status_code == 401


def test_a_key_in_the_query_string_is_refused_not_honoured():
    """Pelias clients sometimes send api_key= in the URL. A URL lands in browser history, referers
    and any proxy's log, so the edge does not accept a key there - even a valid one."""
    need_key()
    r = get(PREFIX + f"/v1/autocomplete?text=portland&api_key={KEY}")
    assert r.status_code == 401


@pytest.mark.parametrize("path", API_PATHS)
def test_a_known_key_is_accepted_on_every_endpoint(path):
    need_key()
    r = get(PREFIX + path, headers=GOOD)
    assert r.status_code == 200, r.text[:200]
    assert r.json()["type"] == "FeatureCollection"


def test_the_pelias_paths_are_guarded_too():
    """When Pelias is served beside pgeo, its canonical /v1/* paths carry the same check."""
    r = get("/v1/autocomplete?text=portland")
    if r.status_code == 404:
        pytest.skip("no Pelias on this edge")
    assert r.status_code == 401


@pytest.mark.parametrize("path", ["/", "/engines.json", "/js/app.js", "/css/app.css"])
def test_static_files_and_the_page_shell_need_no_key(path):
    """They hold no data; requiring a key would only stop the page from asking for one."""
    r = get(path)
    assert r.status_code == 200


def test_the_key_is_case_sensitive_in_the_header_value():
    need_key()
    r = get(PREFIX + API_PATHS[0], headers={"X-API-Key": KEY.swapcase()})
    assert r.status_code == 401


# ---- what the server keeps and logs ------------------------------------------------------------------


def test_the_access_log_names_the_client_and_never_the_key():
    """Checked on the dev edge, whose log is the container's stdout. On the VM the same format
    is deployed by the same template (test_the_template_logs_the_client_name below)."""
    need_key()
    try:
        running = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True,
                                 text=True, timeout=10, check=False).stdout.split()  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        pytest.skip("docker not available")
    if "pelias_maine_web_dev" not in running:
        pytest.skip("dev edge container not running")
    get(PREFIX + "/v1/autocomplete?text=logcheck", headers=GOOD)
    get(PREFIX + "/v1/autocomplete?text=logcheck")
    log = subprocess.run(["docker", "logs", "--tail", "50", "pelias_maine_web_dev"],
                         capture_output=True, text=True, timeout=10, check=False).stdout  # fmt: skip
    assert KEY not in log, "the API key appeared in the access log"
    assert KEY[:12] not in log
    lines = [l for l in log.splitlines() if "/v1/autocomplete" in l]
    assert any(" - dev [" in l and " 200 " in l for l in lines), (
        "the accepted request should be logged under its client name"
    )
    assert any(" -  [" in l and " 401 " in l for l in lines), (
        "the refused request should be logged with no client"
    )
    assert "logcheck" not in log, "the query string must not be logged"


# ---- the key generator -----------------------------------------------------------------------------


def gen(name: str) -> tuple[str, str]:
    out = subprocess.run([str(ROOT / "scripts/edge_apikey.sh"), "new", name],
                         capture_output=True, text=True, timeout=10, check=True).stdout  # fmt: skip
    key = re.search(r"^\s+(pgeo_[A-Za-z0-9_-]{43})$", out, re.MULTILINE).group(1)
    digest = re.search(r"sha256: ([0-9a-f]{64})", out).group(1)
    return key, digest


def test_a_generated_key_has_the_documented_shape_and_entropy():
    key, digest = gen("unit-test")
    assert len(key) == 48 and key.startswith("pgeo_")
    assert hashlib.sha256(key.encode()).hexdigest() == digest, (
        "the printed hash must be of the printed key"
    )


def test_generated_keys_are_unique():
    assert len({gen("unit-test")[0] for _ in range(5)}) == 5


def test_the_generator_prints_the_key_once_and_stores_it_nowhere():
    before = {
        p: p.stat().st_mtime
        for p in (ROOT / "infra/ansible/group_vars/pelias").glob("*.yml")
    }
    key, _ = gen("unit-test")
    after = {
        p: p.stat().st_mtime
        for p in (ROOT / "infra/ansible/group_vars/pelias").glob("*.yml")
    }
    assert before == after, "the generator must not write the inventory"
    tracked = subprocess.run(
        ["git", "grep", "-l", key[:16]],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    assert tracked == "", f"a generated key is in a tracked file: {tracked}"


@pytest.mark.parametrize("name", ["", "A", "-x", "has space", "x" * 40])
def test_the_generator_rejects_a_bad_client_name(name):
    r = subprocess.run(
        [str(ROOT / "scripts/edge_apikey.sh"), "new", name],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert r.returncode != 0


# ---- the configuration that produces the edge ----------------------------------------------------------


TEMPLATE = ROOT / "infra/ansible/roles/edge/templates/pelias.conf.j2"
BASE_VARS = {
    "edge_rate_search": 10, "edge_rate_autocomplete": 25, "edge_listen_port": 8080, "edge_server_name": "host",
    "edge_demo_root": "/srv/demo", "edge_allowed_sources": ["192.0.2.254"], "edge_trusted_proxies": ["192.0.2.254"],
    "ansible_facts": {"default_ipv4": {"address": "127.0.0.1"}}, "pelias_enabled": True, "pgeo_enabled": True,
    "edge_api_keys": [{"name": "dispatch", "sha256": "a" * 64}],
}  # fmt: skip


def render(**flags) -> str:
    jinja2 = pytest.importorskip("jinja2")  # only the template tests need it
    env = jinja2.Environment(
        undefined=jinja2.ChainableUndefined, keep_trailing_newline=True
    )
    env.filters["bool"] = lambda v: str(v).lower() in ("true", "yes", "1", "on")
    return env.from_string(TEMPLATE.read_text()).render(**{**BASE_VARS, **flags})


def test_every_proxied_api_location_is_guarded():
    """Each location that proxies to an engine must refuse an unknown client before it does."""
    conf = render()
    blocks = re.findall(r"location[^{]*\{(.*?)\n    \}", conf, re.DOTALL)
    proxied = [b for b in blocks if "proxy_pass" in b]
    assert len(proxied) >= 7
    for b in proxied:
        assert 'if ($api_client = "") { return 401; }' in b, b[:120]
        # and the guard comes before the proxy_pass, not after
        assert b.index("if ($api_client") < b.index("proxy_pass")


def test_the_template_stores_hashes_and_names_only():
    conf = render()
    assert '"' + "a" * 64 + '" "dispatch";' in conf
    assert not re.search(r"pgeo_[A-Za-z0-9_-]{43}", conf), (
        "no key-shaped string may appear in the rendered configuration"
    )


def test_the_template_logs_the_client_name_not_the_request_line():
    conf = render()
    fmt = re.search(r"log_format pathonly (.+?';)\n", conf, re.DOTALL).group(1)
    assert "$api_client" in fmt and "$uri" in fmt
    # $request is the whole request line, query string included; $request_method is fine
    assert (
        not re.search(r"\$request\b(?!_method)", fmt)
        and "$args" not in fmt
        and "$http_x_api_key" not in fmt
    )


def test_with_the_check_on_and_no_keys_nothing_is_accepted():
    """Fail closed: an empty key list must not turn into open access."""
    conf = render(edge_api_keys=[])
    m = re.search(r"map \$api_key_hash \$api_client \{(.*?)\}", conf, re.DOTALL).group(
        1
    )
    assert 'default "";' in m and "anonymous" not in m


def test_the_off_switch_is_explicit_and_named():
    conf = render(edge_require_api_key=False)
    assert 'default "anonymous";' in conf
    assert "edge_require_api_key" in conf, "the exception must say what turned it off"


def test_the_deployed_inventory_has_the_check_on():
    """The security posture the report describes is the one with keys required."""
    main = (ROOT / "infra/ansible/group_vars/pelias/main.yml").read_text()
    assert re.search(r"^edge_require_api_key:\s*true\s*$", main, re.MULTILINE)
    local = ROOT / "infra/ansible/group_vars/pelias/zz-local.yml"
    if local.exists():
        assert not re.search(
            r"^edge_require_api_key:\s*false", local.read_text(), re.MULTILINE
        ), "zz-local.yml turns the key check off"


def test_every_guarded_location_resolves_401_to_the_json_handler():
    """error_page directives do not merge in nginx: a location that declares its own error_page
    (the pgeo locations do, for 404) inherits none from the server, and its 401 would fall to
    nginx's HTML page with no WWW-Authenticate. Found on VM 120 after the first deploy."""
    conf = render()
    blocks = re.findall(r"location[^{]*\{(.*?)\n    \}", conf, re.DOTALL)
    for b in blocks:
        if "if ($api_client" not in b:
            continue
        if "error_page" in b:
            assert "error_page 401 = @unauthorized;" in b, b[:160]
