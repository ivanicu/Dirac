#!/usr/bin/env python3
"""OKLCH → sRGB, plus the ramp generator the night candidates are built from.

WHY A GENERATOR AND NOT 300 HAND-TYPED HEX VALUES: a theme is ~25 colour tokens,
and fifteen candidates is 375 of them. Hand-typing that many produces palettes
whose internal relationships are accidental — two surfaces 0.01 apart in
lightness in one theme and 0.06 apart in the next, so a comparison between
candidates measures the typing, not the design. Here each world declares a few
ANCHORS (a background hue, an accent, a counter-accent, a chroma budget) and the
ramp is derived, so the candidates differ only in what was actually chosen.

WHY OKLCH: lightness in OKLab is perceptually even, so "one step darker" means
the same thing at every hue. In HSL it does not, which is how a palette ends up
with a green that reads three steps lighter than the blue at the same L.

THE CONVERSION IS ROUND-TRIP TESTED against design/check_palette.py's own
hex→OKLCH (already in the repo — reused, not rewritten). An unverified colour
conversion would put every number in the candidate gallery downstream of a guess.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_palette import contrast, delta_e, oklch          # noqa: E402  (reuse)

# ── OKLCH → sRGB ────────────────────────────────────────────────────────────
# Björn Ottosson's OKLab matrices. Written out rather than pulled from a
# dependency because the whole design system is three files and a gate; adding a
# colour library to convert 375 values would be the larger cost.
_LMS_FROM_OKLAB = (
    (1.0, 0.3963377774, 0.2158037573),
    (1.0, -0.1055613458, -0.0638541728),
    (1.0, -0.0894841775, -1.2914855480),
)
_RGB_FROM_LMS = (
    (4.0767416621, -3.3077115913, 0.2309699292),
    (-1.2684380046, 2.6097574011, -0.3413193965),
    (-0.0041960863, -0.7034186147, 1.7076147010),
)


def _gamma(c: float) -> float:
    """Linear → sRGB. The 0.0031308 knee is the standard, not a fudge."""
    if c <= 0.0031308:
        return 12.92 * c
    return 1.055 * (c ** (1 / 2.4)) - 0.055


def oklch_to_rgb(L: float, C: float, h_deg: float) -> tuple[float, float, float]:
    h = math.radians(h_deg)
    a, b = C * math.cos(h), C * math.sin(h)
    lms_ = [m[0] * L + m[1] * a + m[2] * b for m in _LMS_FROM_OKLAB]
    lms = [v ** 3 for v in lms_]
    return tuple(sum(m[i] * lms[i] for i in range(3)) for m in _RGB_FROM_LMS)


def in_gamut(L: float, C: float, h: float) -> bool:
    return all(-1e-4 <= v <= 1 + 1e-4 for v in oklch_to_rgb(L, C, h))


def oklch_hex(L: float, C: float, h: float) -> str:
    """Nearest in-gamut sRGB hex, reducing CHROMA only.

    Clipping RGB channels instead would shift the HUE — a blue pushed out of
    gamut clips to a purple, and the palette silently stops being the palette
    that was designed. Reducing chroma keeps hue and lightness, which are the
    two things a token's role depends on.
    """
    if not in_gamut(L, C, h):
        lo, hi = 0.0, C
        for _ in range(40):
            mid = (lo + hi) / 2
            if in_gamut(L, mid, h):
                lo = mid
            else:
                hi = mid
        C = lo
    r, g, b = (min(1.0, max(0.0, _gamma(v))) for v in oklch_to_rgb(L, C, h))
    return '#%02x%02x%02x' % (round(r * 255), round(g * 255), round(b * 255))


# ── the ramp: a world's anchors → a full token set ──────────────────────────
# Lightness steps are FIXED across every candidate. That is the point: two
# candidates then differ in hue and chroma choices only, so the gallery compares
# design decisions instead of comparing accidental lightness.
L_BG, L_S1, L_S2, L_S3 = 0.16, 0.22, 0.26, 0.31
L_BORDER, L_BORDER_STRONG = 0.28, 0.36
L_TEXT, L_TEXT2, L_TEXT3 = 0.94, 0.72, 0.56
L_ACCENT, L_SOFT = 0.80, 0.28
L_VIZ = 0.72                    # field colours sit above the UI, not in it


def build(world: dict) -> dict[str, str]:
    """Anchors → the 30 tokens tokens.css declares for a dark theme."""
    bh, bc = world['bg_hue'], world['bg_chroma']
    ah, ac = world['accent_hue'], world['accent_chroma']
    ch = world['counter_hue']
    vc = world.get('viz_chroma', ac)
    t = {
        # surfaces: one hue, a chroma that stays low so text can sit on it
        '--bg': oklch_hex(L_BG, bc, bh),
        '--surface': oklch_hex(L_S1, bc, bh),
        '--surface-2': oklch_hex(L_S2, bc * 1.1, bh),
        '--surface-3': oklch_hex(L_S3, bc * 1.2, bh),
        '--border': oklch_hex(L_BORDER, bc * 1.3, bh),
        '--border-strong': oklch_hex(L_BORDER_STRONG, bc * 1.5, bh),
        # content: a whisper of the background hue keeps grey from going dead,
        # but text chroma above ~0.02 starts to tint and fatigue at small sizes
        '--text': oklch_hex(L_TEXT, min(bc, 0.02), bh),
        '--text-2': oklch_hex(L_TEXT2, min(bc * 1.2, 0.03), bh),
        '--text-3': oklch_hex(L_TEXT3, min(bc * 1.2, 0.03), bh),
        # semantic
        '--accent': oklch_hex(L_ACCENT, ac, ah),
        '--accent-soft': oklch_hex(L_SOFT, ac * 0.45, ah),
        '--ok': oklch_hex(L_ACCENT, ac * 0.9, world.get('ok_hue', 150)),
        '--ok-soft': oklch_hex(L_SOFT, ac * 0.4, world.get('ok_hue', 150)),
        '--warn': oklch_hex(L_ACCENT, ac * 0.95, world.get('warn_hue', 75)),
        '--warn-soft': oklch_hex(L_SOFT, ac * 0.4, world.get('warn_hue', 75)),
        '--danger': oklch_hex(L_ACCENT, ac * 0.95, world.get('danger_hue', 22)),
        '--danger-soft': oklch_hex(L_SOFT, ac * 0.4, world.get('danger_hue', 22)),
        '--info': oklch_hex(L_ACCENT, ac * 0.85, ch),
        '--info-soft': oklch_hex(L_SOFT, ac * 0.4, ch),
        # data-viz. The DIVERGING PAIRS carry chemistry convention and must stay
        # separable at the same lightness — that is what the gate measures, and
        # it is why the pair hues are anchored per world rather than derived.
        '--viz-mep-pos': oklch_hex(L_VIZ, vc, world['mep'][0]),
        '--viz-mep-neg': oklch_hex(L_VIZ, vc, world['mep'][1]),
        '--viz-orb-pos': oklch_hex(L_VIZ, vc, world['orb'][0]),
        '--viz-orb-neg': oklch_hex(L_VIZ, vc, world['orb'][1]),
        '--viz-mlp-pos': oklch_hex(L_VIZ, vc, world['mlp'][0]),
        '--viz-mlp-neg': oklch_hex(L_VIZ, vc, world['mlp'][1]),
        '--viz-density': oklch_hex(L_VIZ, vc * 0.95, world['density']),
        # the 3D canvas is part of the theme: a viewport that does not match the
        # panel makes the molecule look pasted onto the app
        '--scene-bg': oklch_hex(max(L_BG - 0.05, 0.02), bc * 0.8, bh),
        '--glow': oklch_hex(0.62, ac * 1.1, ah),
    }
    return t


# ── measurement, on the gate's own criteria ────────────────────────────────
MIN_CONTRAST_TEXT = 4.5      # WCAG AA body text
MIN_CONTRAST_UI = 3.0        # the gate's bar for non-text UI colour
MIN_PAIR_DE = 0.10           # the gate's bar: below this a pair reads as one


def measure(t: dict[str, str]) -> dict:
    pairs = {
        'mep': ('--viz-mep-pos', '--viz-mep-neg'),
        'orb': ('--viz-orb-pos', '--viz-orb-neg'),
        'mlp': ('--viz-mlp-pos', '--viz-mlp-neg'),
    }
    chroma = {k: oklch(v)[1] for k, v in t.items()}
    return {
        'text_on_bg': contrast(t['--text'], t['--bg']),
        'text2_on_surface': contrast(t['--text-2'], t['--surface']),
        'accent_on_surface': contrast(t['--accent'], t['--surface']),
        'danger_on_surface': contrast(t['--danger'], t['--surface']),
        'pair_de': {k: delta_e(t[a], t[b]) for k, (a, b) in pairs.items()},
        'max_chroma': max(chroma.values()),
        'max_chroma_token': max(chroma, key=chroma.get),
        'viz_max_chroma': max(v for k, v in chroma.items() if k.startswith('--viz')),
    }


def verdict(m: dict) -> tuple[str, list[str]]:
    """PASS / WARN / FAIL against the criteria the repo already enforces."""
    problems = []
    if m['text_on_bg'] < MIN_CONTRAST_TEXT:
        problems.append(f"body text {m['text_on_bg']:.1f}:1 < {MIN_CONTRAST_TEXT}")
    if m['text2_on_surface'] < MIN_CONTRAST_TEXT:
        problems.append(f"secondary text {m['text2_on_surface']:.1f}:1 < {MIN_CONTRAST_TEXT}")
    for name in ('accent', 'danger'):
        if m[f'{name}_on_surface'] < MIN_CONTRAST_UI:
            problems.append(f"{name} {m[f'{name}_on_surface']:.1f}:1 < {MIN_CONTRAST_UI}")
    for k, de in m['pair_de'].items():
        if de < MIN_PAIR_DE:
            problems.append(f"{k} pair ΔE {de:.3f} < {MIN_PAIR_DE} — reads as one colour")
    return ('FAIL' if problems else 'PASS'), problems


def _selftest() -> None:
    """The conversion is round-tripped, because everything downstream is a
    number produced by it. Convert a known hex to OKLCH with the GATE's code,
    convert it back with mine, and require the same hex."""
    print('── selftest: OKLCH round-trip against check_palette.py ──')
    worst = 0.0
    for hex_in in ('#0a0e14', '#e8edf5', '#7dd3c0', '#bd777b', '#e0af68',
                   '#6788bc', '#131a23', '#ffffff', '#000000'):
        L, C, h = oklch(hex_in)
        hex_out = oklch_hex(L, C, h)
        d = max(abs(int(hex_in[i:i + 2], 16) - int(hex_out[i:i + 2], 16))
                for i in (1, 3, 5))
        worst = max(worst, d)
        print(f'   {hex_in} → L={L:.4f} C={C:.4f} h={h:6.1f} → {hex_out}  Δ={d}')
    assert worst <= 1, (
        f'round-trip differs by {worst}/255 — the conversion is wrong and every '
        f'number in the gallery is downstream of it')
    # And a control in the other direction: an out-of-gamut chroma must reduce
    # chroma, never rotate hue.
    L, C, h = 0.72, 0.40, 150.0
    got = oklch_hex(L, C, h)
    _, c2, h2 = oklch(got)
    assert abs(h2 - h) < 4.0, (
        f'gamut mapping moved the hue {h}→{h2:.1f}: it is clipping RGB channels '
        f'instead of reducing chroma, which silently redesigns the palette')
    assert c2 < C, 'gamut mapping did not reduce chroma'
    print(f'   out-of-gamut C=0.40 h=150 → {got} (C={c2:.3f}, hue held at {h2:.1f})')
    print('SELFTEST PASS — conversion verified in both directions')


if __name__ == '__main__':
    _selftest()
