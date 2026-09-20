"""Security posture of a running deployment: the controls the report claims, asserted.

    uv run --project pgeo pytest tests/security -q                  # local dev edge (:4700)
    PGEO_EDGE=https://geocoder.example.org/pgeo \\
    PELIAS_EDGE=https://geocoder.example.org \\
        uv run --project pgeo pytest tests/security -q              # the deployed service

These are properties of a deployment, not of the source, so each test skips when the thing it
checks is not reachable rather than failing a developer's laptop run.
"""

from __future__ import annotations

import os
import subprocess

import pytest

httpx = pytest.importorskip("httpx")

PGEO = os.environ.get("PGEO_EDGE", "http://127.0.0.1:4700")
PELIAS = os.environ.get("PELIAS_EDGE", "http://127.0.0.1:4000")
VM = os.environ.get("PGEO_VM_SSH", "")  # e.g. jcz@192.0.2.20; host checks skip without it


def get(base: str, path: str, **kw) -> httpx.Response:
    try:
        return httpx.get(f"{base}{path}", timeout=15, **kw)
    except httpx.HTTPError as e:
        pytest.skip(f"{base} not reachable: {e}")


def ssh(cmd: str) -> str:
    if not VM:
        pytest.skip("set PGEO_VM_SSH to check host-level posture")
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", VM, cmd],  # noqa: S603, S607
                       capture_output=True, text=True, timeout=30)  # fmt: skip
    if r.returncode != 0:
        pytest.skip(f"ssh failed: {r.stderr.strip()[:80]}")
    return r.stdout


# ---- transport and headers --------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "must_contain"),
    [("content-security-policy", "default-src 'self'"),
     ("x-content-type-options", "nosniff"),
     ("x-frame-options", "DENY"),
     ("referrer-policy", "strict-origin"),
     ("permissions-policy", "camera=()")],  # fmt: skip
)
def test_the_demo_page_carries_its_security_headers(header, must_contain):
    """The page is same-origin with a strict CSP: no CDN can be added without noticing."""
    r = get(PELIAS, "/")
    if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
        pytest.skip("no demo page at this base")
    assert must_contain in r.headers.get(header, ""), r.headers.get(header)


def test_the_csp_allows_no_remote_script_source():
    r = get(PELIAS, "/")
    if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
        pytest.skip("no demo page at this base")
    csp = r.headers.get("content-security-policy", "")
    assert "script-src 'self'" in csp
    assert "http://" not in csp and "https://" not in csp, f"CSP names a remote origin: {csp}"


def test_the_server_version_is_not_advertised():
    r = get(PGEO, "/v1/search", params={"text": "bangor", "size": 1})
    assert "/" not in r.headers.get("server", "nginx"), r.headers.get("server")


# ---- methods and input ------------------------------------------------------------------


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_only_get_is_allowed_on_the_api(method):
    """A read-only service should refuse write verbs at the edge, not in the engine."""
    try:
        r = httpx.request(method, f"{PGEO}/v1/search", params={"text": "x"}, timeout=15)
    except httpx.HTTPError as e:
        pytest.skip(f"not reachable: {e}")
    assert r.status_code in (403, 405), f"{method} returned {r.status_code}"


def test_an_unknown_parameter_is_refused_rather_than_ignored():
    r = get(PGEO, "/v1/search", params={"text": "bangor", "exec": "rm -rf /"})
    assert r.status_code == 400
    assert r.json()["geocoding"]["errors"]


@pytest.mark.parametrize(
    "hostile",
    ["'; DROP TABLE pgeo.feature; --", "<script>alert(1)</script>", "../../etc/passwd",
     "${jndi:ldap://x/a}", "%00", "a" * 5000],  # fmt: skip
)
def test_hostile_text_is_data_not_code(hostile):
    """Every query reaches SQL as a bind parameter. The engine may answer or refuse, but it must
    not fail, and the response must stay JSON: Pelias and pgeo both echo the query back in
    geocoding.query, so the echo is only safe because it is JSON-encoded and served as JSON with
    nosniff. The demo page's own protection is tested separately."""
    r = get(PGEO, "/v1/search", params={"text": hostile, "size": 1})
    assert r.status_code in (200, 400), r.status_code
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert body["type"] == "FeatureCollection"
    if hostile in str(body.get("geocoding", {}).get("query", {})):
        assert "text/html" not in r.headers["content-type"]


UNSAFE_DOM = ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(")


@pytest.mark.parametrize("api", UNSAFE_DOM)
def test_the_demo_page_never_writes_markup_from_data(api):
    """The page shows labels that came from the data and from the user's query. It builds them
    with textContent only; one innerHTML would turn an echoed query into script."""
    from pathlib import Path

    web = Path(__file__).resolve().parents[2] / "web" / "js"
    if not web.is_dir():
        pytest.skip("no web/js in this checkout")
    offenders = [f"{f.name}:{i}" for f in web.glob("*.js")
                 for i, line in enumerate(f.read_text().splitlines(), 1) if api in line]  # fmt: skip
    assert not offenders, f"{api} used in {offenders}"


def test_the_database_still_answers_after_the_hostile_inputs():
    """The point of the previous test: nothing it sent changed the database."""
    r = get(PGEO, "/v1/search", params={"text": "bangor", "size": 1})
    assert r.status_code == 200 and r.json()["features"]


# ---- database privileges ----------------------------------------------------------------


def psql(sql: str) -> str:
    out = ssh(f"sudo -n docker exec pgeo_db psql -U pgeo -d pgeo -Atc \"{sql}\"")
    return out.strip()


def test_the_api_role_cannot_log_in_as_a_superuser():
    assert psql("SELECT rolsuper FROM pg_roles WHERE rolname='pgeo_api'") == "f"


def test_the_api_role_holds_no_write_privilege():
    privs = psql("SELECT DISTINCT privilege_type FROM information_schema.table_privileges "
                 "WHERE grantee='pgeo_api'")  # fmt: skip
    assert set(privs.split()) <= {"SELECT"}, privs


def test_postgresql_does_not_log_query_parameters():
    """A query is an address someone searched for; for LANCER it is a client address."""
    assert psql("SELECT setting FROM pg_settings WHERE name='log_parameter_max_length'") == "0"
    assert psql("SELECT setting FROM pg_settings WHERE name='log_statement'") == "none"


# ---- host ---------------------------------------------------------------------------------


def test_no_engine_port_is_reachable_beyond_loopback():
    """Only the edge listens on the LAN; Elasticsearch, PostgREST and the Pelias API must not."""
    listening = ssh("sudo -n ss -ltn | awk 'NR>1 {print $4}'")
    exposed = [a for a in listening.split()
               if not a.startswith(("127.", "[::1]", "0.0.0.0:22", "[::]:22", "127.0.0.53",
                                    "127.0.0.54", "0.0.0.0:5355", "[::]:5355"))]  # fmt: skip
    engine_ports = {"4000", "4100", "4200", "4300", "4400", "4500", "4600", "9200", "9300", "5433"}
    bad = [a for a in exposed if a.rsplit(":", 1)[-1] in engine_ports]
    assert not bad, f"engine ports reachable off-host: {bad}"


def test_the_access_log_does_not_record_query_strings():
    """The default nginx format logs the whole request line; a geocoder's query string is the
    address itself."""
    conf = ssh("sudo -n grep -h 'access_log\\|log_format' /etc/nginx/sites-enabled/* 2>/dev/null")
    assert "pathonly" in conf, conf
    assert "$uri" in conf and "$request " not in conf


def test_the_firewall_denies_by_default():
    status = ssh("sudo -n ufw status verbose")
    assert "Status: active" in status
    assert "deny (incoming)" in status
