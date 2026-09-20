# pgeo slide deck

A [Slidev](https://sli.dev) deck covering pgeo's architecture, requirements, flexibility,
features, use and limits, with the numbers taken from the study (`docs/REPORT.md`).

## Running it

```bash
scripts/slides.sh            # present and edit: dev server with hot reload, opens a browser
scripts/slides.sh serve      # build if needed, then serve at http://127.0.0.1:8099
scripts/slides.sh build      # static HTML into slides/dist (3.8 MB, no server needed to host)
scripts/slides.sh pdf        # export to docs/pgeo-slides.pdf
```

The first run installs dependencies with npm (Node 20+). Nothing else is required.

`serve` exists because a Slidev build is a single-page application: a deep link such as
`/5` must fall back to `index.html`, which `python3 -m http.server` does not do — the deck
loads at `/` and then 404s on any direct slide link. The script serves the build with that
fallback.

Presenting: <kbd>f</kbd> full screen, <kbd>o</kbd> slide overview, <kbd>d</kbd> dark mode,
<kbd>?</kbd> for the rest.

## Files

| | |
|---|---|
| `slides.md` | the deck; one `---` separated section per slide |
| `public/` | images: screenshots of the demo app and figures from the report |
| `package.json` | Slidev and the default theme |

Images in `public/` are copies, so the deck is self-contained and a report rebuild cannot
change a slide underneath you. To refresh them after new figures or screenshots:

```bash
cp docs/report_figures/fig_{architecture,query_pipeline,frontier,accuracy_category}.png slides/public/
cp docs/screenshots/desktop-light-0{1,4,6,7,8}-*.png slides/public/
```

## A note on `npm audit`

`npm audit` reports advisories against Slidev's own dependency tree — `lodash-es` (reached
through mermaid and chevrotain) and `image-size` (reached through the PPTX exporter). They are
real advisories, and they are not fixable from here: they belong to upstream packages, and
`npm audit fix --force` downgrades Slidev to a version with more of them.

They also do not apply to how this deck is used. Slidev is an authoring tool that runs on a
developer's machine or produces static HTML; it is not part of the service, it is not deployed,
and nothing in `slides/` is reachable from the geocoder. The `lodash` advisory needs
attacker-controlled template input and the `image-size` one needs a malicious image handed to
the PPTX exporter, which this deck never runs.

That is why **only the static build is published**: `slides/dist` is committed and
`slides/node_modules` is not. Reading the deck needs no Node, no npm install and none of the
packages above — a browser and a static file server are enough. The dependencies are only
needed to *edit* the deck.

`playwright-chromium` is a dev dependency, used solely by `scripts/slides.sh pdf`.
