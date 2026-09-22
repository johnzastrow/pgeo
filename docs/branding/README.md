# pgeo marks

Two files are the brand: the wordmark and the icon. Both are plain SVG with no external fonts.
Four earlier proposals (a monogram, a pin, a cylinder, a set-in-type wordmark) were removed on
2026-09-22 once these two were settled; they are in the history before that date.

## The wordmark

`pgeo-clean.svg` is the wordmark - generated once from the supplied vector by
`build_pgeo_clean.py`, then finished by hand (a larger globe seated in the `g`, a rounder `e`
terminal). It is the master. The supplied vector it was built from has been removed, so the
generator is kept for the record of what was corrected, not to be re-run.

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

`pgeo-icon-g.svg`: the `g` and its globe, lifted from the wordmark onto a 512 square by
`build_pgeo_icon.py`. It is the favicon (`web/favicon.svg`) and the deck's icon. A split-ring
alternative was made and dropped in favour of it: the `g` is unmistakably pgeo where a ring with
a globe could be any map company.
