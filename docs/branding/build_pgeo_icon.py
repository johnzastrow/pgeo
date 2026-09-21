"""Square icon marks derived from the pgeo wordmark.

    python3 docs/branding/build_pgeo_icon.py

The wordmark is 3:1 and illegible below about 120px wide, so an icon has to be a different
drawing rather than a crop. Two are produced, both on a 512 square:

    pgeo-icon.svg       the split ring with a simplified globe inside
    pgeo-icon-g.svg     the 'g' and its globe, lifted from the wordmark

Both take their proportions from the master (docs/branding/pgeo-clean.svg), where every bowl is
R=157 with a 68 stroke and the 'o' is cut by two 21-wide parallel slots on the diagonal, so the
icon and the wordmark stay visibly related.

The globe in pgeo-icon.svg is redrawn rather than reused. The wordmark's globe carries nine
overlapping paths of continent shapes, which turn to mud below about 48px; the icon's is a
sphere, an equator and one meridian, which is what survives at 16.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from build_pgeo_clean import _run

HERE = Path(__file__).resolve().parent
MASTER = HERE / "pgeo-clean.svg"

# Proportions carried over from the wordmark.
W_OVER_R = 68.0 / 157.0        # stroke weight relative to the bowl radius
SLOT_OVER_R = 21.0 / 157.0     # slot width relative to the bowl radius

INK, BLUE, GREEN = "#0264a4", "#298eca", "#62a887"

SIZE = 512.0
C = SIZE / 2
PAD = 40.0
R = C - PAD                    # 216: outer radius of the icon's ring


def f(*vals: float) -> str:
    return " ".join(f"{v:.3f}" for v in vals)


def circle(cx: float, cy: float, rad: float, cw: bool = True) -> str:
    s = 1 if cw else 0
    return (f"M {f(cx - rad, cy)} A {f(rad, rad)} 0 0 {s} {f(cx + rad, cy)} "
            f"A {f(rad, rad)} 0 0 {s} {f(cx - rad, cy)} Z")  # fmt: skip


def _slot(cx: float, cy: float, ri: float, ro: float, deg: float, half_w: float) -> str:
    """One parallel-sided slot, spanning the ring band only."""
    a = math.radians(deg)
    ux, uy = math.cos(a), math.sin(a)
    nx, ny = -uy, ux
    r0, r1 = ri - ro * 0.25, ro * 1.25          # past both edges; the boolean trims it
    pts = [(cx + r0 * ux + half_w * nx, cy + r0 * uy + half_w * ny),
           (cx + r1 * ux + half_w * nx, cy + r1 * uy + half_w * ny),
           (cx + r1 * ux - half_w * nx, cy + r1 * uy - half_w * ny),
           (cx + r0 * ux - half_w * nx, cy + r0 * uy - half_w * ny)]  # fmt: skip
    return "M " + " L ".join(f(x, y) for x, y in pts) + " Z"


def split_ring(cx: float, cy: float, ro: float) -> str:
    """The 'o': a ring notched by two parallel-sided slots on the diagonal.

    In the wordmark one rectangle laid across the centre makes both slots at once. At icon size
    that rectangle reads as a slash drawn through the whole mark - the "no entry" gesture - so
    here each slot is cut separately and stops at the counter, leaving the middle clear for the
    globe. The slots are trimmed by boolean difference rather than by winding, because a slot
    that reaches into the counter would otherwise fill rather than cut.
    """
    ri = ro * (1 - W_OVER_R)
    half_w = ro * SLOT_OVER_R / 2
    ring = f"{circle(cx, cy, ro)} {circle(cx, cy, ri, cw=False)}"
    for deg in (-45, 135):
        ring = _run([ring, _slot(cx, cy, ri, ro, deg, half_w)], "path-difference")
    return ring


def simple_globe(cx: float, cy: float, rad: float) -> list[str]:
    """A sphere, an equator and one meridian: what is left of a globe at 16px."""
    band = rad * 0.34                     # how open the meridian is
    # Line weight is what decides whether this survives: at 32px the sphere is 12px across, so
    # a stroke thinner than about a sixth of the radius disappears into the fill. Three lines at
    # that weight read; five thinner ones turn to mud, which is why the far side of the equator
    # and the second meridian are not drawn.
    lw = rad * 0.16
    return [
        f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="{rad:.3f}" fill="{BLUE}"/>',
        (f'<path d="M {f(cx - rad, cy)} L {f(cx + rad, cy)}" fill="none" stroke="#ffffff" '
         f'stroke-width="{lw:.2f}" stroke-linecap="round"/>'),
        (f'<path d="M {f(cx, cy - rad)} A {f(band, rad)} 0 0 0 {f(cx, cy + rad)} '
         f'A {f(band, rad)} 0 0 0 {f(cx, cy - rad)} Z" fill="none" stroke="#ffffff" '
         f'stroke-width="{lw:.2f}"/>'),
    ]


def svg(body: str, label: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE:.0f} {SIZE:.0f}"'
        f' width="{SIZE:.0f}" height="{SIZE:.0f}" role="img" aria-label="{label}">\n'
        f"  <title>{label}</title>\n  {body}\n</svg>\n"
    )


def icon_ring() -> str:
    """The split ring with a simplified globe seated in its counter."""
    ri = R * (1 - W_OVER_R)
    parts = [f'<path fill="{GREEN}" fill-rule="evenodd" d="{split_ring(C, C, R)}"/>']
    parts += simple_globe(C, C, ri * 0.78)
    return svg("\n  ".join(parts), "pgeo")


def icon_g() -> str:
    """The 'g' and its globe, lifted from the master and fitted to the square."""
    src = MASTER.read_text()
    m = re.search(r'<path\b[^>]*?id="path2"[^>]*?/?>', src, re.DOTALL)
    g = re.search(r'<g\b[^>]*?id="g11".*?</g>', src, re.DOTALL)
    if not m or not g:
        raise SystemExit("could not find the 'g' (path2) and its globe (g11) in the master")
    # The 'g' occupies x 448.19..762.19, y 306..746 in the wordmark.
    gx0, gy0, gw, gh = 448.19, 306.0, 314.0, 440.0
    scale = (SIZE - 2 * PAD) / gh
    tx = C - (gx0 + gw / 2) * scale
    ty = C - (gy0 + gh / 2) * scale
    body = (f'<g transform="translate({tx:.3f},{ty:.3f}) scale({scale:.5f})">\n'
            f"    {m.group(0)}\n    {g.group(0)}\n  </g>")  # fmt: skip
    return svg(body, "pgeo")


def main() -> None:
    for name, content in (("pgeo-icon.svg", icon_ring()), ("pgeo-icon-g.svg", icon_g())):
        (HERE / name).write_text(content)
        print(f"wrote {HERE / name} ({len(content)} bytes)")


if __name__ == "__main__":
    main()
