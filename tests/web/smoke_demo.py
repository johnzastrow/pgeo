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
import re
import sys
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import apikey  # noqa: E402

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
        for scheme in ("light",):   # the page is light only
            for name, vp in VIEWPORTS.items():
                page = browser.new_page(viewport=vp, color_scheme=scheme)
                errors: list[str] = []
                # Record where the error came from: a bare "Error" says nothing when a run
                # fails once and passes on retry (a 429 from the edge looks exactly like that).
                page.on(
                    "console",
                    lambda m, e=errors: e.append(f"{m.text} @ {m.location.get('url', '?')}")
                    if m.type == "error" else None,  # fmt: skip
                )
                page.on("pageerror", lambda exc, e=errors: e.append(f"pageerror: {exc}"))
                page.on(
                    "response",
                    lambda r, e=errors: e.append(f"HTTP {r.status} {r.url}")
                    if r.status >= 400 else None,  # fmt: skip
                )
                tag = f"{name}-{scheme}"

                page.goto(base + "/", wait_until="networkidle")
                page.wait_for_function(
                    "() => document.querySelector('.maplibregl-canvas') !== null",
                    timeout=15000,
                )
                page.wait_for_timeout(2500)  # tiles and glyphs
                page.screenshot(path=str(shots / f"{tag}-01-load.png"))

                # API key (Phase 11). The page must ask for one and refuse to work without it;
                # the key goes in through the page's own form, as a person would enter it.
                key = apikey.api_key()
                if not key:
                    failures.append(f"{tag}: no API key (set PGEO_API_KEY or run scripts/dev_web.sh)")
                    page.close()
                    continue
                # The form appears on the first 401, not on load: a deployment can sit behind a
                # proxy that presents the key itself, and there the page must never ask. So the
                # test makes a request first, the way a visitor would.
                page.type("#search .ps-input", "portland", delay=20)
                try:
                    page.wait_for_selector("#apikey-form:not([hidden])", timeout=8000)
                except PlaywrightTimeoutError:
                    failures.append(f"{tag}: the page did not ask for an API key")
                page.fill("#apikey-input", "pgeo_" + "x" * 43)          # a well-formed wrong key
                page.click("#apikey-form button")
                page.wait_for_function(
                    "() => /not accepted/.test(document.querySelector('#apikey-note').textContent)", timeout=8000)
                page.fill("#apikey-input", key)
                page.click("#apikey-form button")
                page.wait_for_function("() => document.querySelector('#apikey-form').hidden", timeout=8000)
                # the wrong key above was meant to be refused: those 401s are the test, not a failure
                errors[:] = [e for e in errors if "401" not in e]
                # the key must not be visible anywhere in the document, nor persist beyond the tab
                if key in page.content():
                    failures.append(f"{tag}: the API key appears in the page")
                if page.evaluate("() => localStorage.getItem('pgeo-api-key')") is not None:
                    failures.append(f"{tag}: the API key was written to localStorage")

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

                    # Address tab (needs a pgeo engine): suggestion -> USPS block
                    if page.is_visible("#tab-address"):
                        page.click("#tab-address")
                        page.fill("#addr-search .ps-input", "")
                        page.type("#addr-search .ps-input", "just in time lewiston", delay=25)
                        page.wait_for_selector("#addr-search .ps-option", timeout=8000)
                        page.keyboard.press("ArrowDown")
                        page.keyboard.press("Enter")
                        page.wait_for_selector("#addr-result:not([hidden]) .usps-block", timeout=8000)
                        block = page.inner_text("#addr-result .usps-block")
                        if "LEWISTON ME" not in block:
                            failures.append(f"{tag}: address block {block!r}")
                        page.wait_for_timeout(1200)
                        page.screenshot(path=str(shots / f"{tag}-07-address.png"))
                        # free text with a unit -> best match
                        page.fill("#addr-search .ps-input", "389 Congress St, Portland ME")
                        page.fill("#addr-unit", "Apt 2")
                        page.click("#addr-find")
                        page.wait_for_function(
                            "() => /APT 2/.test(document.querySelector('#addr-result .usps-block')?.textContent || '')",
                            timeout=8000,
                        )

                    # Compare tab (needs Pelias and pgeo)
                    if page.is_visible("#tab-compare"):
                        page.click("#tab-compare")
                        page.fill("#compare-text", "Portlnd, ME")
                        page.click("#compare-form button")
                        page.wait_for_function(
                            "() => document.querySelectorAll('#compare-cols .cmp-col').length === 2",
                            timeout=10000,
                        )
                        summary = page.inner_text("#compare-summary")
                        if not re.search(r"agree|differ|Only one|Neither", summary):
                            failures.append(f"{tag}: compare summary {summary!r}")
                        page.wait_for_timeout(1200)
                        page.screenshot(path=str(shots / f"{tag}-08-compare.png"))

                    # Form filler (needs pgeo): the point follows the best candidate while
                    # typing, and Confirm fills the contact form from /v1/address.
                    if page.is_visible("#tab-form"):
                        page.click("#tab-form")
                        page.fill("#form-search .ps-input", "")
                        page.type("#form-search .ps-input", "portland city hall", delay=25)
                        page.wait_for_selector(".maplibregl-popup .callout strong", timeout=8000)
                        callout = page.inner_text(".maplibregl-popup .callout")
                        if "Portland City Hall" not in callout:
                            failures.append(f"{tag}: form-filler callout {callout!r}")
                        # a complete query gets a confidence; a half-typed one only a type-ahead match
                        page.wait_for_function(
                            "() => /confidence/.test(document.querySelector('.maplibregl-popup .callout')?.textContent || '')",
                            timeout=8000,
                        )
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(1500)
                        page.screenshot(path=str(shots / f"{tag}-09-form-filler.png"))
                        page.click("#form-confirm")
                        page.wait_for_function(
                            "() => document.querySelector('#contact-form [name=zip]').value.length === 5",
                            timeout=8000,
                        )
                        filled = {k: page.input_value(f"#contact-form [name={k}]") for k in ("venue", "street", "city", "state", "zip", "lat")}
                        if filled["venue"] != "Portland City Hall" or filled["city"] != "PORTLAND" or filled["state"] != "ME" or not filled["street"]:
                            failures.append(f"{tag}: form filled {filled!r}")
                        page.wait_for_timeout(800)
                        page.screenshot(path=str(shots / f"{tag}-10-form-filled.png"))
                        # an address is not a business: the venue field must stay empty
                        page.click("#form-clear")
                        page.type("#form-search .ps-input", "389 congress st portland", delay=25)
                        page.wait_for_selector(".maplibregl-popup .callout strong", timeout=8000)
                        page.keyboard.press("Escape")
                        page.click("#form-confirm")
                        page.wait_for_function(
                            "() => document.querySelector('#contact-form [name=zip]').value.length === 5",
                            timeout=8000,
                        )
                        if page.input_value("#contact-form [name=venue]") != "":
                            failures.append(f"{tag}: address filled the venue field")

                    # Confidence explorer: an ambiguous name is reported as such by pgeo
                    if page.is_visible("#engine-pick"):
                        page.select_option("#engine", page.locator("#engine option").evaluate_all(
                            "os => os.map(o => o.value).find(v => v.includes('pgeo')) ?? os[0].value"))
                    page.click("#tab-confidence")
                    page.fill("#conf-text", "Mud Pond")
                    page.click("#conf-form button")
                    page.wait_for_selector("#conf-results li", timeout=10000)
                    note = page.inner_text("#conf-note")
                    n_rows = page.locator("#conf-results li").count()
                    if n_rows < 2 or not re.search(r"ambiguous|no doubt expressed|confident|weak", note):
                        failures.append(f"{tag}: confidence note {note!r} with {n_rows} rows")
                    page.wait_for_timeout(1200)
                    page.screenshot(path=str(shots / f"{tag}-11-confidence.png"))

                    # Area: a drawn rectangle restricts the search to it
                    page.click("#tab-boundary")
                    w, h = vp["width"], vp["height"]
                    page.mouse.move(w * 0.55, h * 0.35)
                    page.mouse.down()
                    page.mouse.move(w * 0.75, h * 0.6, steps=8)
                    page.mouse.up()
                    page.wait_for_function(
                        "() => /Rectangle/.test(document.querySelector('#bnd-status').textContent)", timeout=5000)
                    page.fill("#bnd-text", "Main Street")
                    page.click("#bnd-form button")
                    page.wait_for_function(
                        "() => /inside/.test(document.querySelector('#bnd-status').textContent)", timeout=10000)
                    page.wait_for_timeout(1200)
                    page.screenshot(path=str(shots / f"{tag}-12-area.png"))

                    # Nearby: click the map, get an address here and a grouped list
                    page.click("#tab-nearby")
                    page.mouse.click(w * 0.62, h * 0.5)
                    page.wait_for_selector("#near-results .near-group", timeout=10000)
                    status = page.inner_text("#near-status")
                    if not re.search(r"here|no address", status):
                        failures.append(f"{tag}: nearby status {status!r}")
                    page.wait_for_timeout(1000)
                    page.screenshot(path=str(shots / f"{tag}-13-nearby.png"))

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
