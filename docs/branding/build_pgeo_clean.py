"""Rebuild the pgeo wordmark from exact geometry.

    python3 docs/branding/build_pgeo_clean.py  ->  docs/branding/pgeo-clean.svg

The traced original (docs/branding/pgeo.svg) is bumpy because its round forms are not round: the
'e' sweeps nearly a whole circle in one cubic segment, the 'o' ring is two irregular arcs, and the
counters sit off-centre from their bowls. Nudging control points cannot fix that - the geometry
has to be regenerated. This does, from one set of parameters, so every letter shares a baseline,
an x-height, a stroke weight and a rhythm. Every curve below is a true circular arc.

Shapes that are unions or differences (the 'e' aperture, the 'g' stem and hook) are composed by
Inkscape's boolean operations rather than by overlapping subpaths and fill rules, which is what
keeps the outlines free of hairline seams.

Preserved: the four colours, the single-storey 'g' and its globe, the 'p' with its angled stem
cut, the split-ring 'o', the mark's overall width.

Corrected, with the original's measurement first:

  x-height top     p 280.0, e 308.4, o 304.5   ->  306 for all four
  baseline         p 586.6, e 618.5, o 627.4   ->  620 for all four
  stroke weight    p ring 64.6-70.9, stem 88.3 ->  68 everywhere
  p counter        3.1 units below its bowl    ->  concentric
  letter gaps      20.0, 12.8, 8.6             ->  16.2, even
"""

from __future__ import annotations

import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# ---- the one set of parameters ------------------------------------------------------------------

W = 68.0                      # stroke weight, everywhere
R = 157.0                     # outer radius of every bowl
r = R - W                     # 89.0
CY = 463.0                    # centre line of the x-height
DESC = 749.36                 # descender depth, kept from the original

LEFT, RIGHT = 118.03, 1422.50
CX_P = LEFT + R
CX_O = RIGHT - R
STEP = (CX_O - CX_P) / 3      # 330.16: four evenly spaced bowls
CX_G, CX_E = CX_P + STEP, CX_P + 2 * STEP

INK, BLUE, GREEN = "#0264a4", "#298eca", "#62a887"

HERE = Path(__file__).resolve().parent
SRC = HERE / "pgeo.svg"
# pgeo-clean.svg is the hand-finished master and is NOT written by this script:
# it was generated once, then refined by hand. Re-running would destroy that work.
OUT = HERE / "pgeo-generated.svg"
GLOBE_SCALE = 0.80
SLOT = 21.0                   # width of the two cuts in the 'o'
A_TERM = 52.0                 # where the 'e' terminates, degrees below east

NS = 'xmlns="http://www.w3.org/2000/svg" xmlns:svg="http://www.w3.org/2000/svg"'


# ---- arc helpers ---------------------------------------------------------------------------------


def pt(cx: float, cy: float, rad: float, deg: float) -> tuple[float, float]:
    a = math.radians(deg)
    return cx + rad * math.cos(a), cy + rad * math.sin(a)


def f(*vals: float) -> str:
    return " ".join(f"{v:.4f}" for v in vals)


def circle(cx: float, cy: float, rad: float, cw: bool = True) -> str:
    s = 1 if cw else 0
    return (f"M {f(cx - rad, cy)} A {f(rad, rad)} 0 0 {s} {f(cx + rad, cy)} "
            f"A {f(rad, rad)} 0 0 {s} {f(cx - rad, cy)} Z")  # fmt: skip


def annulus(cx: float, cy: float, ro: float, ri: float) -> str:
    """Outer circle clockwise, inner circle anticlockwise: a ring with a real hole."""
    return circle(cx, cy, ro) + " " + circle(cx, cy, ri, cw=False)


def band(cx: float, cy: float, ro: float, ri: float, t1: float, t2: float) -> str:
    """An annular sector from t1 to t2 degrees (y down, 0 = east, increasing clockwise)."""
    laf = 1 if abs(t2 - t1) > 180 else 0
    return (f"M {f(*pt(cx, cy, ro, t1))} A {f(ro, ro)} 0 {laf} 1 {f(*pt(cx, cy, ro, t2))} "
            f"L {f(*pt(cx, cy, ri, t2))} A {f(ri, ri)} 0 {laf} 0 {f(*pt(cx, cy, ri, t1))} Z")  # fmt: skip


def rect(x0: float, y0: float, x1: float, y1: float) -> str:
    return f"M {f(x0, y0)} L {f(x1, y0)} L {f(x1, y1)} L {f(x0, y1)} Z"


# ---- the pieces each letter is composed from -----------------------------------------------------


def p_parts() -> list[tuple[str, str]]:
    """Bowl plus a stem whose left edge is tangent to it, so the join has no kink."""
    x0, x1 = CX_P - R, CX_P - R + W
    drop = W * (49.36 / 87.35)                 # the original's cut angle, at the new stem width
    stem = (f"M {f(x0, CY - 1)} L {f(x1, CY - 1)} "
            f"L {f(x1, DESC - drop)} L {f(x0, DESC)} Z")  # fmt: skip
    return [("p_bowl", annulus(CX_P, CY, R, r)), ("p_stem", stem)]


def g_parts() -> list[tuple[str, str]]:
    """Closed bowl, straight right stem, and a hook that leaves the stem tangentially.

    The hook's centre is chosen so that at due east both its edges meet the stem's edges exactly:
    the join is continuous rather than merely close.
    """
    top_l = CY - math.sqrt(R * R - r * r)      # 333.7: stem's left edge meets the bowl
    y_hook = 552.0
    rho = 160.0
    hx = CX_G + R - W / 2 - rho
    stem = rect(CX_G + r, top_l, CX_G + R, y_hook + 1)
    hook = band(hx, y_hook, rho + W / 2, rho - W / 2, -2, 120)
    return [("g_bowl", annulus(CX_G, CY, R, r)), ("g_stem", stem), ("g_hook", hook)]


def e_parts() -> tuple[list[tuple[str, str]], tuple[str, str]]:
    """Ring and a bar on the centre line, less an aperture cut from the lower right.

    The counters are holes, not a white shape painted on top, so the mark works on any
    background - the original's near-white counter only worked on white.
    """
    hb = W / 2
    bar_x = math.sqrt(R * R - hb * hb)         # the bar meets the outer circle here
    bar = rect(CX_E - bar_x, CY - hb, CX_E + bar_x, CY + hb)
    # The aperture is the wedge between the bar's underside and the terminal ray: bounded above by
    # y = CY + hb and below by the ray at A_TERM, opening outwards from where those two cross.
    # A radial band would have cut into the bar, and stopping it at the inner circle left a
    # one-unit wall that sealed the lower counter - which is what made the 'e' read as a circle.
    t = math.radians(A_TERM)
    apex = (CX_E + hb / math.tan(t), CY + hb)
    far = R + 140
    ap = (f"M {f(*apex)} L {f(CX_E + far, CY + hb)} "
          f"L {f(CX_E + far * math.cos(t), CY + far * math.sin(t))} Z")  # fmt: skip
    return [("e_ring", annulus(CX_E, CY, R, r)), ("e_bar", bar)], ("e_ap", ap)


def o_parts() -> tuple[list[tuple[str, str]], tuple[str, str]]:
    """A split ring, cut by one slot of constant width laid across the centre on the diagonal.

    A radial cut makes a wedge - wider at the outer edge than the inner. One rectangle through
    the centre gives both slots at once, with parallel sides and the same width all the way
    across, which is what a slot should look like.
    """
    a = math.radians(-45)
    ux, uy = math.cos(a), math.sin(a)           # along the slot
    nx, ny = -uy, ux                            # across it
    half_len, half_w = R + 40, SLOT / 2
    pts = [(CX_O + half_len * ux + half_w * nx, CY + half_len * uy + half_w * ny),
           (CX_O + half_len * ux - half_w * nx, CY + half_len * uy - half_w * ny),
           (CX_O - half_len * ux - half_w * nx, CY - half_len * uy - half_w * ny),
           (CX_O - half_len * ux + half_w * nx, CY - half_len * uy + half_w * ny)]  # fmt: skip
    slot = "M " + " L ".join(f(x, y) for x, y in pts) + " Z"
    return [("o_ring", annulus(CX_O, CY, R, r))], ("o_slot", slot)


# ---- booleans, via Inkscape ----------------------------------------------------------------------


def _run(ds_in: list[str], op: str) -> str:
    """One boolean over exactly the given paths, in document order, via select-all.

    Addressing the operands by id looked tidier but was wrong: a union adopts one operand's id,
    and which one is not something to rely on, so the follow-up difference silently subtracted
    from the wrong object. select-all over a document holding exactly the intended operands
    leaves nothing to guess - for a difference that means two paths, the cut on top.
    """
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "in.svg", Path(td) / "out.svg"
        body = "\n".join(f'<path d="{d}" fill="#000" fill-rule="evenodd"/>' for d in ds_in)
        src.write_text(f'<svg {NS} viewBox="0 0 1536 1024">{body}</svg>')
        cmd = ["inkscape", str(src),
               f"--actions=select-all;{op};export-filename:{dst};export-plain-svg;export-do"]  # fmt: skip
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if not dst.exists():
            sys.exit(f"inkscape produced nothing\n{res.stdout}\n{res.stderr}")
        out = dst.read_text()
    ds = re.findall(r'\bd="([^"]+)"', out)
    if len(ds) != 1:
        sys.exit(f"{op} left {len(ds)} paths, expected 1")
    return ds[0]


def union(parts: list[tuple[str, str]]) -> str:
    return _run([d for _, d in parts], "path-union")


def union_then_difference(parts: list[tuple[str, str]], cut: tuple[str, str]) -> str:
    """Union the parts, then subtract the cut, as two separate unambiguous operations."""
    return _run([union(parts), cut[1]], "path-difference")


# ---- the globe, carried over ----------------------------------------------------------------------


def globe() -> str:
    """The globe inside the 'g': artwork rather than letterform, so it is moved, not redrawn."""
    src = SRC.read_text()
    # path17 and path20 sit at x 1144-1175 - they are details on the 'o', not the globe.
    ids = ("path8", "path10", "path11", "path12", "path13",
           "path14", "path15", "path16", "path18")  # fmt: skip
    out = []
    for pid in ids:
        m = re.search(rf'<path\b[^>]*?id="{pid}"[^>]*?/?>', src, re.DOTALL)
        if not m:
            continue
        d = re.search(r'\bd="([^"]*)"', m.group(0))
        style = re.search(r'style="([^"]*)"', m.group(0))
        # Some of these carry their own transform; dropping it flings the path across the canvas.
        tr = re.search(r'\btransform="([^"]*)"', m.group(0))
        if not d:
            continue
        fill = "#62a887"
        if style:
            fm = re.search(r"fill:\s*(#[0-9A-Fa-f]{6})", style.group(1))
            if fm:
                fill = fm.group(1)
        t = f' transform="{tr.group(1)}"' if tr else ""
        out.append(f'<path d="{d.group(1)}"{t} fill="{fill}"/>')
    return (f'<g transform="translate({CX_G:.3f},{CY:.3f}) scale({GLOBE_SCALE}) '
            f'translate({-646.505:.3f},{-463.435:.3f})">\n    '
            + "\n    ".join(out) + "\n  </g>")  # fmt: skip


def main() -> None:
    e_base, e_cut = e_parts()
    p_d, g_d = union(p_parts()), union(g_parts())
    o_base, o_cut = o_parts()
    e_d = union_then_difference(e_base, e_cut)
    o_d = union_then_difference(o_base, o_cut)
    body = "\n  ".join([
        f'<path fill="{INK}" fill-rule="evenodd" d="{p_d}"/>',
        f'<path fill="{INK}" fill-rule="evenodd" d="{g_d}"/>',
        globe(),
        f'<path fill="{BLUE}" fill-rule="evenodd" d="{e_d}"/>',
        f'<path fill="{GREEN}" fill-rule="evenodd" d="{o_d}"/>',
    ])  # fmt: skip
    OUT.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1536 1024" width="1536"'
        ' height="1024" role="img" aria-label="pgeo">\n'
        f"  <title>pgeo</title>\n  {body}\n</svg>\n"
    )
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
