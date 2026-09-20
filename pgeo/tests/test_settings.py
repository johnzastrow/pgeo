"""Configuration loading (pgeo/src/pgeo/settings.py).

Settings carry the database password, so the tests cover precedence and parsing rather than
values, and never print what they load.
"""

from __future__ import annotations

import dataclasses
from urllib.parse import unquote, urlsplit

import pytest

from pgeo import settings as S
from pgeo.settings import Settings


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """No ambient environment and no real secrets file: every test states its own input."""
    for k in list(S.os.environ):
        if k.startswith("PGEO_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(S, "SECRETS_FILE", tmp_path / "pgeo.secrets")
    return tmp_path


def write_secrets(tmp_path, text: str) -> None:
    (tmp_path / "pgeo.secrets").write_text(text)


# ---- the secrets file -------------------------------------------------------------------


def test_reads_key_value_pairs(_isolate):
    write_secrets(_isolate, "PGEO_DSN=postgresql://u:p@h:1/db\n")
    assert Settings.load().dsn == "postgresql://u:p@h:1/db"


def test_ignores_comments_and_blank_lines(_isolate):
    write_secrets(_isolate, "# a comment\n\n  \nPGEO_DSN=postgresql://u:p@h:1/db\n")
    assert Settings.load().dsn.endswith("/db")


def test_keeps_equals_signs_inside_a_value(_isolate):
    """A generated password may contain '='; splitting on every '=' would truncate it. The DSN
    percent-encodes it, so the check is on the password the URL actually carries."""
    write_secrets(_isolate, "PGEO_DB_PASSWORD=ab=cd=ef\n")
    assert unquote(urlsplit(Settings.load().dsn).password or "") == "ab=cd=ef"


def test_strips_surrounding_whitespace(_isolate):
    write_secrets(_isolate, "  PGEO_PARSE_MODE = none  \nPGEO_DB_PASSWORD=x\n")
    assert Settings.load().parse_mode == "none"


def test_a_missing_secrets_file_is_not_an_error_when_the_environment_has_what_is_needed(monkeypatch, _isolate):
    monkeypatch.setenv("PGEO_DSN", "postgresql://u:p@h:1/db")
    assert Settings.load().dsn.endswith("/db")


def test_the_environment_wins_over_the_file(monkeypatch, _isolate):
    """Compose passes settings as environment variables; they must override the checked-in
    developer defaults, not the other way round."""
    write_secrets(_isolate, "PGEO_PARSE_MODE=none\nPGEO_DB_PASSWORD=x\n")
    monkeypatch.setenv("PGEO_PARSE_MODE", "service")
    assert Settings.load().parse_mode == "service"


# ---- the DSN ----------------------------------------------------------------------------


def test_builds_a_dsn_from_a_password_with_the_documented_defaults(_isolate):
    write_secrets(_isolate, "PGEO_DB_PASSWORD=secret\n")
    assert Settings.load().dsn == "postgresql://pgeo:secret@127.0.0.1:5433/pgeo"


def test_host_and_port_can_be_overridden(_isolate):
    write_secrets(_isolate, "PGEO_DB_PASSWORD=secret\nPGEO_DB_HOST=db\nPGEO_DB_PORT=5432\n")
    assert Settings.load().dsn == "postgresql://pgeo:secret@db:5432/pgeo"


def test_an_explicit_dsn_is_used_as_given(_isolate):
    write_secrets(_isolate, "PGEO_DSN=postgresql://other@elsewhere/x\nPGEO_DB_PASSWORD=ignored\n")
    assert Settings.load().dsn == "postgresql://other@elsewhere/x"


@pytest.mark.parametrize("password", ["p@ss", "p/ss", "p:ss", "p#ss", "p ss", "p%ss"])
def test_a_password_with_url_characters_still_produces_the_right_dsn(_isolate, password):
    """Reserved characters have to be percent-encoded, or the host is read out of the password
    ("p@ss" would make the host "ss") and the connection silently goes somewhere else."""
    write_secrets(_isolate, f"PGEO_DB_PASSWORD={password}\n")
    parts = urlsplit(Settings.load().dsn)
    assert parts.hostname == "127.0.0.1"
    assert parts.port == 5433
    assert unquote(parts.password or "") == password


def test_refuses_to_start_without_a_password_or_dsn(_isolate):
    with pytest.raises(RuntimeError, match="PGEO_DSN or PGEO_DB_PASSWORD"):
        Settings.load()


# ---- the other settings -----------------------------------------------------------------


@pytest.mark.parametrize("mode", ["service", "extension", "none"])
def test_accepts_every_documented_parse_mode(_isolate, mode):
    write_secrets(_isolate, f"PGEO_DB_PASSWORD=x\nPGEO_PARSE_MODE={mode}\n")
    assert Settings.load().parse_mode == mode


def test_rejects_an_unknown_parse_mode_rather_than_falling_back(_isolate):
    write_secrets(_isolate, "PGEO_DB_PASSWORD=x\nPGEO_PARSE_MODE=magic\n")
    with pytest.raises(RuntimeError, match="service|extension|none"):
        Settings.load()


def test_defaults_are_the_documented_ones(_isolate):
    write_secrets(_isolate, "PGEO_DB_PASSWORD=x\n")
    s = Settings.load()
    assert s.parse_mode == "service"
    assert s.libpostal_url == "http://127.0.0.1:4401"
    assert s.pool_max == 8


def test_pool_max_is_read_as_a_number(_isolate):
    write_secrets(_isolate, "PGEO_DB_PASSWORD=x\nPGEO_POOL_MAX=3\n")
    assert Settings.load().pool_max == 3


def test_settings_are_frozen_so_a_caller_cannot_rewrite_the_dsn(_isolate):
    write_secrets(_isolate, "PGEO_DB_PASSWORD=x\n")
    s = Settings.load()
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.dsn = "postgresql://elsewhere"  # type: ignore[misc]
