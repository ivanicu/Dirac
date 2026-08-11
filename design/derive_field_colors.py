#!/usr/bin/env python3
"""Re-derive the scientific field colours for a different scene background.

    python3 design/derive_field_colors.py                 # for the fascia well
    python3 design/derive_field_colors.py --bg 0d141b     # for Dirac Night

The fields were tuned against a near-black scene (#0d141b). The interface is
now a machined light panel and the WebGL clear colour is #f1f0eb, so every one
of those colours is being asked to carry meaning against a background 30x
brighter than the one it was chosen for. Eyeballing replacements would put a
taste back where a measured number belongs, and taste does not survive the next
theme.

WHAT IS HELD AND WHAT MOVES, and why that split is the whole method:

  HUE      held exactly. Hue is the only channel carrying MEANING here -- blue
           is positive potential, red is negative, gold is greasy, aqua is
           polar. Moving a hue to fix contrast silently rewrites the chemistry
           the picture is asserting.
  CHROMA   held at or below the ceiling (the chroma of #e0af68, the gold Ivan
           settled on) -- the mid-saturation ruling, unchanged.
  LIGHTNESS  the only free variable. Solved for, per colour, until the mark
           clears the contrast floor against ITS OWN background.

So a theme swap moves lightness and nothing else, and a series keeps its
identity across the swap. Diverging pairs are re-checked afterwards, because
squeezing two colours toward the same lightness is exactly how a +/- pair
merges into one colour that reads as a single blob.
"""
from __future__ import annotations

import argparse
import math

CEILING = 0.106
MIN_CONTRAST = 3.0
MIN_PAIR_DE = 0.10

# The dark-tuned values in flight today (index.html :root / field-wells).
FIELDS = {
    'viz-mep-pos': '#6788bc',
    'viz-mep-neg': '#bd777b',
    'viz-orb-pos': '#7fc7a5',
    'viz-orb-neg': '#a397d3',
    'viz-mlp-pos': '#d5b979',
    'viz-mlp-neg': '#74ccdd',
    'viz-density': '#d8aa75',
}
PAIRS = [('viz-mep-pos', 'viz-mep-neg'), ('viz-orb-pos', 'viz-orb-neg'),
         ('viz-mlp-pos', 'viz-mlp-neg')]


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _unlin(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def to_oklch(hex_str: str) -> tuple[float, float, float]:
    h = hex_str.lstrip('#')
    r, g, b = (_lin(int(h[i:i + 2], 16) / 255) for i in (0, 2, 4))
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = l ** (1 / 3), m ** (1 / 3), s ** (1 / 3)
    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    A = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    B = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return L, math.hypot(A, B), math.degrees(math.atan2(B, A)) % 360


def from_oklch(L: float, C: float, H: float) -> tuple[str, bool]:
    """OKLCH -> hex. The flag reports whether the request was IN GAMUT; a
    silently clipped colour is a different colour, and clipping is exactly how
    a held hue stops being held."""
    a = C * math.cos(math.radians(H))
    b = C * math.sin(math.radians(H))
    l_ = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    r = +4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_
    g = -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_
    bb = -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_
    in_gamut = all(-0.001 <= v <= 1.001 for v in (r, g, bb))
    out = ''.join(f'{round(255 * min(max(_unlin(min(max(v, 0.0), 1.0)), 0), 1)):02x}'
                  for v in (r, g, bb))
    return '#' + out, in_gamut


def luminance(hex_str: str) -> float:
    h = hex_str.lstrip('#')
    r, g, b = (_lin(int(h[i:i + 2], 16) / 255) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def delta_e(a: str, b: str) -> float:
    La, Ca, Ha = to_oklch(a)
    Lb, Cb, Hb = to_oklch(b)
    return math.hypot(La - Lb,
                      Ca * math.cos(math.radians(Ha)) - Cb * math.cos(math.radians(Hb)),
                      Ca * math.sin(math.radians(Ha)) - Cb * math.sin(math.radians(Hb)))


def solve(hex_src: str, bg: str) -> tuple[str, float, float, bool]:
    """Walk lightness toward the background's opposite until the mark clears
    the contrast floor. Hue fixed, chroma capped -- lightness is all that moves."""
    L0, C0, H = to_oklch(hex_src)
    C = min(C0, CEILING)
    darkening = luminance(bg) > 0.18          # light ground -> go darker
    best = None
    steps = [i / 200 for i in range(0, 201)]
    for L in (reversed(steps) if darkening else steps):
        cand, in_gamut = from_oklch(L, C, H)
        if not in_gamut:
            continue
        if contrast(cand, bg) >= MIN_CONTRAST:
            best = (cand, L, C, True)
            break
    if best is None:                          # unreachable at this chroma
        cand, _ = from_oklch(0.35 if darkening else 0.85, C, H)
        best = (cand, 0.35 if darkening else 0.85, C, False)
    return best


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--bg', default='f1f0eb', help='scene background, hex without #')
    args = ap.parse_args()
    bg = '#' + args.bg.lstrip('#')

    print(f'scene background {bg}  (relative luminance {luminance(bg):.3f})')
    print(f'holding hue, chroma <= {CEILING}, solving lightness for '
          f'>= {MIN_CONTRAST}:1\n')
    print(f'{"token":16s} {"was":9s} {"now":9s} {"hue":>6s} {"C":>6s} '
          f'{"was:1":>7s} {"now:1":>7s}')

    out = {}
    for name, src in FIELDS.items():
        new, L, C, reached = solve(src, bg)
        out[name] = new
        _, _, H = to_oklch(src)
        flag = '' if reached else '  <- FLOOR UNREACHABLE at this chroma'
        print(f'{name:16s} {src:9s} {new:9s} {H:6.1f} {C:6.3f} '
              f'{contrast(src, bg):6.2f}  {contrast(new, bg):6.2f}{flag}')

    print('\ndiverging pairs after the move (squeezing lightness is how a pair merges):')
    ok = True
    for a, b in PAIRS:
        de = delta_e(out[a], out[b])
        mark = 'ok' if de >= MIN_PAIR_DE else 'MERGED'
        if de < MIN_PAIR_DE:
            ok = False
        print(f'  {a} / {b}: dE {de:.3f}  {mark}')

    print('\ncss:')
    for name, value in out.items():
        print(f'    --{name}: {value};')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
