"""The version the demo page prints must be the project's: VERSION is the source, the page's
text is a copy that a release has to update (see CHANGELOG.md, "Versioning")."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_the_page_prints_the_project_version():
    version = (ROOT / "VERSION").read_text().strip()
    html = (ROOT / "web" / "index.html").read_text()
    m = re.search(r'<span id="ver-page">([^<]*)</span>', html)
    assert m, "web/index.html has no #ver-page element"
    assert m.group(1) == version, f"web/index.html says {m.group(1)}, VERSION says {version}"
