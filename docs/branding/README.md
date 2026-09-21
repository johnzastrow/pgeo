# pgeo marks

Four options, all plain SVG, no external fonts required for the icons (the wordmark uses a
system sans stack and should be converted to outlines before use anywhere the font is uncertain).
Palette matches the report: `#1f5f8b` (the deep blue used for headings) and `#c0392b` (the brick
red used for pgeo's series in every chart).

| File | What it is | Best for |
|---|---|---|
| `pgeo-monogram.svg` | Rounded square, `pg` in white, a located dot | App icon, favicon, avatar — the only one that works at 16px |
| `pgeo-pin-stack.svg` | Map pin whose interior is a database's three bands | Primary mark: says "the place and the store are one object" |
| `pgeo-cylinder.svg` | Database cylinder with a point located beneath it | Diagrams and slides, where a literal database reads better |
| `pgeo-wordmark.svg` | `pge` with the `o` drawn as a map pin | Headers, README, the title slide |

Deliberately avoided: anything resembling the PostgreSQL elephant. The name already borrows the
`pg` prefix; borrowing the mascot would imply an affiliation that does not exist.

## The wordmark

`pgeo.svg` is the mark as supplied. `pgeo-clean.svg` is the same design rebuilt from exact
geometry by `build_pgeo_clean.py`; it is the one to use.

The original's curves are bumpy because its round forms are not round. The `e` sweeps almost a
whole circle in a single cubic segment, which no cubic can do accurately; the `o` is two
irregular arcs; and each counter sits off-centre from its bowl. Control points cannot be nudged
into a circle, so the letterforms are regenerated as true circular arcs from one set of
parameters, which also puts them on a common baseline and x-height.

| | Original | Clean |
|---|---|---|
| x-height top | p 280.0, e 308.4, o 304.5 | 306 for all four |
| Baseline | p 586.6, e 618.5, o 627.4 | 620 for all four |
| Stroke weight | ring 64.6-70.9, p stem 88.3 | 68 everywhere |
| `p` counter | 3.1 units below its bowl | concentric |
| Letter gaps | 20.0, 12.8, 8.6 | 16.2, even |
| Counters | painted near-white, so white-only | real holes, any background |
| `o` slots | radial, so wedge-shaped | parallel sides, 21 units wide |
| `e` aperture | tapering sliver, lower counter sealed | open bay, terminal at 52 degrees |

The overall width, the four colours, and the globe are unchanged - the globe is artwork rather
than letterform, so it is moved and scaled to its new counter, not redrawn. To adjust the mark,
edit the parameters at the top of `build_pgeo_clean.py` and re-run it rather than editing the
SVG: hand edits are lost on the next build.

## The icon

The wordmark is 3:1 and illegible below about 120px wide, so the icon is a different drawing
rather than a crop. `build_pgeo_icon.py` produces two, both on a 512 square:

| File | What it is |
|---|---|
| `pgeo-icon.svg` | The split ring with a simplified globe. The default: purpose-built for small sizes and still legible at 16px |
| `pgeo-icon-g.svg` | The `g` and its globe, lifted from the wordmark. More obviously pgeo, but the globe's detail muddies below 48px |

Both take their proportions from the master, so the icon and the wordmark stay visibly related.

Two things the icon does differently, and why. Its globe is redrawn as a sphere, an equator and
one meridian: the wordmark's globe is nine overlapping paths of continent shapes, which turn to
mud at icon size. And its two slots are cut separately, stopping at the counter, where the
wordmark makes both with one rectangle laid across the centre - at icon size that rectangle reads
as a slash drawn through the mark, which is the "no entry" gesture.
