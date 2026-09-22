"""The API key the test harnesses send to an edge that requires one (Phase 11).

    export PGEO_API_KEY=pgeo_...        # or leave data/dev_api_key from scripts/dev_web.sh

Read from the environment first, then from the file the dev launcher writes. Empty when neither
is set, which is right for the engines' own ports (4000, 4500, 4700): only the edge checks keys.
"""

from __future__ import annotations

import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def api_key() -> str:
    key = os.environ.get("PGEO_API_KEY", "").strip()
    if not key:
        try:
            key = (_ROOT / "data" / "dev_api_key").read_text().strip()
        except OSError:
            key = ""
    return key


def headers() -> dict[str, str]:
    """Headers to add to a request: the key when there is one, nothing otherwise."""
    key = api_key()
    return {"X-API-Key": key} if key else {}
