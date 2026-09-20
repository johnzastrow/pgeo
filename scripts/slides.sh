#!/usr/bin/env bash
# The pgeo slide deck (Slidev), from the repository.
#
#   scripts/slides.sh            present and edit: dev server with hot reload, opens a browser
#   scripts/slides.sh build      build static HTML into slides/dist
#   scripts/slides.sh serve      build if needed, then serve slides/dist at http://127.0.0.1:8099
#   scripts/slides.sh pdf        export to docs/pgeo-slides.pdf
#
# "serve" exists because a Slidev build is a single-page app: a deep link such as /5 has to fall
# back to index.html, which `python3 -m http.server` does not do - the deck loads at / and then
# 404s on any direct slide link.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SLIDES="$ROOT/slides"
PORT="${SLIDES_PORT:-8099}"
cd "$SLIDES"

command -v npm >/dev/null || { echo "npm is required (Node 20+)" >&2; exit 1; }
[[ -d node_modules ]] || { echo "== installing (first run)"; npm install; }

# Opened as a file:// page a Vite build loads none of its assets and shows nothing; leave a
# message in the HTML that appears only in that case (slides/file_hint.py explains why).
build_deck() { npm run build && python3 file_hint.py dist/index.html; }

case "${1:-dev}" in
  dev)   exec npm run dev ;;
  build) build_deck; exit $? ;;
  pdf)   exec npm run export ;;
  serve)
    [[ -f dist/index.html ]] || build_deck
    echo "== http://127.0.0.1:$PORT  (Ctrl-C to stop)"
    exec python3 - "$PORT" <<'PY'
import sys, functools, http.server, socketserver, pathlib

DIST = pathlib.Path("dist").resolve()


class SPA(http.server.SimpleHTTPRequestHandler):
    """Serve the built deck, falling back to index.html for slide routes."""

    def send_head(self):
        path = pathlib.Path(self.translate_path(self.path))
        if not path.exists() and "." not in path.name:
            self.path = "/index.html"
        return super().send_head()

    def log_message(self, fmt, *args):  # one line per request is enough
        sys.stderr.write("%s %s\n" % (self.command, self.path))


handler = functools.partial(SPA, directory=str(DIST))
with socketserver.TCPServer(("127.0.0.1", int(sys.argv[1])), handler) as httpd:
    httpd.serve_forever()
PY
    ;;
  -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
  *) echo "unknown mode: $1 (see --help)" >&2; exit 2 ;;
esac
