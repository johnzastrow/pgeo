"""Browser smoke test for the demo page.

Usage:
    python3 tests/web/smoke_demo.py [BASE_URL] [--shots DIR]
    BASE_URL defaults to http://127.0.0.1:8088 (scripts/dev_web.sh).

Checks: page loads with no console errors or CSP violations, the basemap renders,
autocomplete returns suggestions and selecting one shows the result card, reverse
geocoding works from a map click, and the batch sample completes. Exit code 1 on failure.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

VIEWPORTS = {
    "desktop": {"width": 1440, "height": 900},
    "mobile": {"width": 400, "height": 860},
}


def run(base: str, shots: Path) -> list[str]:
    failures: list[str] = []
    shots.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
        )
        for scheme in ("light", "dark"):
            for name, vp in VIEWPORTS.items():
                if scheme == "dark" and name == "mobile":
                    continue
                page = browser.new_page(viewport=vp, color_scheme=scheme)
                errors: list[str] = []
                page.on(
                    "console",
                    lambda m, e=errors: e.append(m.text) if m.type == "error" else None,
                )
                page.on("pageerror", lambda exc, e=errors: e.append(str(exc)))
                tag = f"{name}-{scheme}"

                page.goto(base + "/", wait_until="networkidle")
                page.wait_for_function(
                    "() => document.querySelector('.maplibregl-canvas') !== null",
                    timeout=15000,
                )
                page.wait_for_timeout(2500)  # tiles and glyphs
                page.screenshot(path=str(shots / f"{tag}-01-load.png"))

                # Autocomplete -> suggestions -> select
                page.fill(".ps-input", "")
                page.type(".ps-input", "389 congress st portland", delay=25)
                page.wait_for_selector(".ps-option", timeout=8000)
                n_opts = page.locator(".ps-option").count()
                if n_opts == 0:
                    failures.append(f"{tag}: no autocomplete options")
                page.screenshot(path=str(shots / f"{tag}-02-autocomplete.png"))
                page.keyboard.press("ArrowDown")
                page.keyboard.press("Enter")
                page.wait_for_selector("#result:not([hidden]) h2", timeout=8000)
                label = page.inner_text("#result h2")
                if "389 Congress" not in label:
                    failures.append(f"{tag}: unexpected selection {label!r}")
                page.wait_for_timeout(1800)
                page.screenshot(path=str(shots / f"{tag}-03-selected.png"))

                if name == "desktop":
                    # Reverse: click the map in the reverse tab
                    page.click("#tab-reverse")
                    page.mouse.click(vp["width"] * 0.62, vp["height"] * 0.5)
                    page.wait_for_selector("#reverse-results li .r-label", timeout=8000)
                    page.wait_for_timeout(800)
                    page.screenshot(path=str(shots / f"{tag}-04-reverse.png"))

                    # Batch sample
                    page.click("#tab-batch")
                    page.click("#batch-sample")
                    page.click("#batch-run")
                    page.wait_for_function(
                        "() => /Done/.test(document.querySelector('#batch-status').textContent)",
                        timeout=30000,
                    )
                    status = page.inner_text("#batch-status")
                    if "8 of 8" not in status:
                        failures.append(f"{tag}: batch status {status!r}")
                    # Regression: unquoted commas must not truncate the query
                    matches = page.locator(
                        "#batch-table tr td:nth-child(2)"
                    ).all_inner_texts()
                    if not any("Augusta" in m for m in matches) or not any(
                        "Bar Harbor" in m for m in matches
                    ):
                        failures.append(f"{tag}: batch matched wrong towns: {matches}")
                    page.wait_for_timeout(1500)
                    page.screenshot(path=str(shots / f"{tag}-05-batch.png"))

                    # Engine switch: present only on the dev server; every engine must answer
                    if page.is_visible("#engine-pick"):
                        page.click("#tab-search")
                        for value in page.locator("#engine option").evaluate_all("os => os.map(o => o.value)"):
                            page.select_option("#engine", value)
                            page.fill(".ps-input", "")
                            page.type(".ps-input", "389 congress st portland", delay=25)
                            page.wait_for_selector(".ps-option", timeout=8000)
                            page.keyboard.press("ArrowDown")
                            page.keyboard.press("Enter")
                            page.wait_for_selector("#result:not([hidden]) h2", timeout=8000)
                            label = page.inner_text("#result h2")
                            if "389 Congress" not in label:
                                failures.append(f"{tag}: engine {value or 'pelias'!r}: unexpected {label!r}")
                        page.screenshot(path=str(shots / f"{tag}-06-engine.png"))

                bad = [
                    e
                    for e in errors
                    if "Content Security Policy" in e or "Refused" in e
                ]
                if bad:
                    failures.append(f"{tag}: CSP violations: {bad[:3]}")
                other = [e for e in errors if e not in bad]
                if other:
                    failures.append(f"{tag}: console errors: {other[:3]}")
                page.close()
        browser.close()
    return failures


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base", nargs="?", default="http://127.0.0.1:8088")
    ap.add_argument("--shots", type=Path, default=Path("data/logs/web-shots"))
    args = ap.parse_args()
    failures = run(args.base.rstrip("/"), args.shots)
    for f in failures:
        print("FAIL", f)
    print(
        "OK" if not failures else f"{len(failures)} failure(s)",
        "- screenshots in",
        args.shots,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
