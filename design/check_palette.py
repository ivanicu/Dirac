#!/usr/bin/env python3
"""Palette gate: "mid-saturation" as a number, not a memory.

    python3 design/check_palette.py

Ivan's ruling was 「颜色整体应该中饱和度一点,不要那么高的饱和度」. A note in a
style guide decays the first time someone reaches for a Tailwind default — which
is exactly how this happened: the offending tokens were #3b82f6 / #f43f5e /
#eab308, stock Tailwind, sitting at 1.5–2.0× the chroma of everything Dirac had
hand-tuned. So the ruling is enforced here instead.

THE CEILING IS NOT INVENTED. It is the chroma of #e0af68, the gold Ivan already
settled on for ivan.icu — a colour he has approved rather than a number I chose.
Dirac's own hand-tuned palette (accent, warn, danger) already sits near 0.088,
which is the working band; the ceiling is the outer limit.

Measured in OKLCH, not HSL. HSL "saturation" is not perceptual: #eab308 and
#22d3ee both read as S=100% while differing by 20% in real chroma, so an HSL
cap would pass colours that still glare.

Three checks, because desaturation can break two other things:
  1. chroma  ≤ ceiling                      — the ruling itself
  2. contrast vs its own theme background   — legibility must not be spent on it
  3. ΔE within each diverging pair          — +/- must stay tellable apart, or the
                                              science is unreadable however calm
"""
from __future__ import annotations

import math
import re
import sys
from pathlib import Path

TOKENS = Path(__file__).with_name('tokens.css')
CEILING = 0.106          # OKLCH chroma of #e0af68
BAND = 0.088             # where Dirac's hand-tuned colours already live
MIN_CONTRAST = 3.0       # non-text UI colour against its own background
MIN_PAIR_DE = 0.10       # a diverging pair below this reads as one colour

DIVERGING_PAIRS = [
    ('viz-mep-pos', 'viz-mep-neg', 'electrostatic potential +/-'),
    ('viz-orb-pos', 'viz-orb-neg', 'orbital phase'),
    ('viz-mlp-pos', 'viz-mlp-neg', 'lipophilic / polar'),
    ('viz-cb-pos', 'viz-cb-neg', 'colourblind-safe alternates'),
]
EXEMPT_PREFIX = ('bg', 'surface', 'border', 'text', 'elev', 'scene')


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def oklch(hex_str: str) -> tuple[float, float, float]:
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


def relative_luminance(hex_str: str) -> float:
    h = hex_str.lstrip('#')
    r, g, b = (_lin(int(h[i:i + 2], 16) / 255) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = relative_luminance(a), relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def delta_e(a: str, b: str) -> float:
    La, Ca, Ha = oklch(a)
    Lb, Cb, Hb = oklch(b)
    return math.hypot(La - Lb,
                      Ca * math.cos(math.radians(Ha)) - Cb * math.cos(math.radians(Hb)),
                      Ca * math.sin(math.radians(Ha)) - Cb * math.sin(math.radians(Hb)))


def themes(source: str) -> dict[str, dict[str, str]]:
    """Split the file into theme scopes; each carries its own background."""
    head, _, tail = source.partition('[data-theme="chamber"]')
    def parse(block: str) -> dict[str, str]:
        return dict(re.findall(r'--([\w-]+):\s*(#[0-9a-fA-F]{6})', block))
    night = parse(head)
    chamber = dict(night)
    chamber.update(parse(tail))
    return {'Night': night, 'Chamber (light)': chamber}


def main() -> int:
    source = TOKENS.read_text()
    failures: list[str] = []

    for theme_name, tokens in themes(source).items():
        background = tokens.get('bg', '#000000')
        print(f'{theme_name} — background {background}')

        worst = 0.0
        for name, value in sorted(tokens.items()):
            if name.startswith(EXEMPT_PREFIX) or name.endswith(('soft', '-bg')):
                continue
            _, chroma, _ = oklch(value)
            worst = max(worst, chroma)
            if chroma > CEILING:
                failures.append(f'{theme_name} --{name} {value}: chroma {chroma:.3f} '
                                f'over the {CEILING} ceiling ({chroma / CEILING:.1f}×)')
            ratio = contrast(value, background)
            if ratio < MIN_CONTRAST:
                failures.append(f'{theme_name} --{name} {value}: contrast {ratio:.1f}:1 '
                                f'under {MIN_CONTRAST}:1 on its own background')
        print(f'    highest chroma {worst:.3f} against a {CEILING} ceiling')

        for a, b, label in DIVERGING_PAIRS:
            if a not in tokens or b not in tokens:
                # A GATE THAT COULD NOT FAIL. `continue` here read a renamed or
                # deleted token as "nothing to check", so the colourblind-safe
                # pair could be removed from tokens.css and this file would
                # still print "palette OK" and exit 0 — forever, silently, in
                # CI. The absence of the thing being checked is the loudest
                # possible failure, not a reason to skip.
                missing = [t for t in (a, b) if t not in tokens]
                failures.append(f'{theme_name} {label}: token(s) '
                                f'{", ".join("--" + m for m in missing)} '
                                f'MISSING from tokens.css — the pair cannot be '
                                f'checked, so it is not passing')
                print(f'    {label:32s} MISSING {missing}')
                continue
            separation = delta_e(tokens[a], tokens[b])
            mark = 'ok' if separation >= MIN_PAIR_DE else 'TOO CLOSE'
            print(f'    {label:32s} ΔE {separation:.3f}  {mark}')
            if separation < MIN_PAIR_DE:
                failures.append(f'{theme_name} {label}: ΔE {separation:.3f} — '
                                f'desaturation has merged a diverging pair')
        print()

    if failures:
        print(f'{len(failures)} palette violations:')
        for f in failures:
            print(f'  ✗ {f}')
        return 1
    print('palette OK — every token inside the ceiling, legible, and separable')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
