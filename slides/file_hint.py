"""Explain the blank page when the built deck is opened as a file.

A Slidev build is a Vite application. Opened from a `file://` URL its origin is "null", so the
browser refuses to load the stylesheets and scripts and the reader gets a white page with nothing
in the console unless they go looking. This adds a message that only appears in that case: the
inline script removes it whenever the page was served over HTTP.

    python3 slides/file_hint.py slides/dist/index.html
"""

from __future__ import annotations

import pathlib
import sys

HINT = """<div id="slidev-file-hint" style="position:fixed;inset:0;z-index:99999;display:flex;
align-items:center;justify-content:center;background:#fff;color:#111;
font:16px/1.6 system-ui,sans-serif;text-align:center;padding:2rem">
<div style="max-width:34rem">
<h1 style="font-size:1.5rem;margin:0 0 1rem">Open this deck over HTTP</h1>
<p>A browser will not load a page's stylesheets or scripts from a <code>file://</code> URL, so
this build shows nothing when the file is opened directly. Nothing is broken.</p>
<p style="margin-top:1rem">Run
<code style="background:#eee;padding:.15rem .4rem;border-radius:3px">scripts/slides.sh serve</code>
and open <a href="http://127.0.0.1:8099">http://127.0.0.1:8099</a>, or read
<code style="background:#eee;padding:.15rem .4rem;border-radius:3px">docs/pgeo-slides.pdf</code>.</p>
</div></div>
<script>if(location.protocol!=="file:"){document.getElementById("slidev-file-hint").remove()}</script>
"""


def main() -> int:
    page = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "dist/index.html")
    html = page.read_text()
    if "slidev-file-hint" in html:
        return 0
    if "</body>" not in html:
        print(f"{page}: no </body> to inject into", file=sys.stderr)
        return 1
    page.write_text(html.replace("</body>", HINT + "</body>", 1))
    print(f"   file:// hint added to {page}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
