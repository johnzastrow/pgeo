"""Configuration from the environment (never from code). Loaded once per process."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PGEO_ROOT = REPO_ROOT / "pgeo"
SQL_DIR = PGEO_ROOT / "sql"
DATA_DIR = REPO_ROOT / "data"


def _read_env_file(path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE reader for pgeo/.env (no shell expansion, no sourcing)."""
    out: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


@dataclass(frozen=True)
class Settings:
    dsn: str
    libpostal_url: str
    parse_mode: str  # service | extension | none
    pool_max: int

    @staticmethod
    def load() -> Settings:
        env = _read_env_file(PGEO_ROOT / ".env") | dict(os.environ)
        dsn = env.get("PGEO_DSN")
        if not dsn:
            pw = env.get("PGEO_DB_PASSWORD")
            if not pw:
                raise RuntimeError("set PGEO_DSN or PGEO_DB_PASSWORD (pgeo/.env)")
            host = env.get("PGEO_DB_HOST", "127.0.0.1")
            port = env.get("PGEO_DB_PORT", "5433")
            dsn = f"postgresql://pgeo:{pw}@{host}:{port}/pgeo"
        mode = env.get("PGEO_PARSE_MODE", "service")
        if mode not in ("service", "extension", "none"):
            raise RuntimeError(f"PGEO_PARSE_MODE must be service|extension|none, not {mode!r}")
        return Settings(
            dsn=dsn,
            libpostal_url=env.get("PGEO_LIBPOSTAL_URL", "http://127.0.0.1:4401"),
            parse_mode=mode,
            pool_max=int(env.get("PGEO_POOL_MAX", "8")),
        )
