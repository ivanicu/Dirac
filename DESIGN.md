# Dirac — Visual Identity & Design Constitution

Canonical tokens: `design/tokens.css`. Living showcase: `design/index.html`
(serve the repo root or `design/` with any static server). This file is the
WHY; the tokens file is the WHAT; the showcase is the proof.

## §1 · What the brand is

Dirac renders **the physics a molecule carries** — fields, orbitals, wells —
inside the scene where the chemistry happens. The visual identity follows
three commitments:

1. **The render is the brand.** The 3D scene (canvas tone, molecule
   materials, the solid-core + wire-cage well grammar) is a specified part of
   the VI, not decoration. Screenshot output must be publication-ready:
   colorbar, scale, provenance included — the decoder ships with the encoding.
2. **Numbers wear their uncertainty.** One decimal + `≈` when the method
   carries ≥0.5 eV; counts and identifiers exact; tabular numerals wherever a
   value can change. A second decimal on a ±25% number is false precision.
3. **Honest refusal beats decorative success.** Error copy names the actor
   and points at the working path ("Gasteiger cannot parameterize F/P — the
   QM potential handles it"). A zero is never displayed as a measurement.

## §2 · Theme law

A theme is a **token swap and nothing else**: it redefines custom properties
under a `[data-theme]` scope and stops. A theme may not add selectors, poll
the runtime, re-style components, or depend on stylesheet load order.

- Canonical themes: **Night** (`:root`, default) and **Chamber**
  (`[data-theme="chamber"]`, paper-light).
- The canvas is part of the theme: `--scene-bg` is applied once by a
  ThemeService call, never by a polling loop.
- `theme-fascia.*` and `theme-chamber.*` predate this law and violate it
  (selector redefinition by load order; 1.2 s repaint intervals). They are
  scheduled to be re-expressed as token swaps in architecture phase P2.

## §3 · Scientific color (brand assets)

| Field | Convention | Tokens |
|---|---|---|
| MEP / QM potential | blue = +, red = − (chemistry convention) | `--viz-mep-pos/neg` |
| Orbitals (HOMO/LUMO) | green / violet phases | `--viz-orb-pos/neg` |
| Lipophilicity | gold = greasy, aqua = polar | `--viz-mlp-pos/neg` |
| Density | single-signed amber | `--viz-density` |
| Colorblind-safe | Okabe–Ito blue/orange, user setting | `--viz-cb-pos/neg` |

Red/blue lobes are the highest colorblind-risk surface in the product; the
CB-safe pair is a first-class setting, not an afterthought.

## §4 · The well grammar (3D-as-brand)

Two shells per sign: a vivid x-ray-shaded **solid core** at the full
isovalue and a **whispered wireframe cage** at 0.62× — alpha 0.55/0.14,
emissive 0.55/0.22. Three stacked translucent skins read as fog; a glowing
core in a cage reads as a force field. The molecule must stay readable
inside its own field.

## §5 · Typography

UI: Inter (system fallback). Numbers/code: system mono stack. **No external
font links** — a LAN deployment must not block rendering on a CDN (measured
incident: Google Fonts stall on offline LAN). Scientific notation: real
sub/superscripts (`pK<sub>a</sub>`, kcal·mol⁻¹, Å), thin space before units.

## §6 · Component state law

Every component defines nine states: default / hover / active / focus /
disabled / **loading / error / empty / offline**. The last four are where
scientific software actually lives; a component missing them is unfinished.
System truth surfaces (status pills, provenance panels) may never lie:
degraded ≠ offline ≠ busy, and each has its own visual.

## §7 · Design inventory (the full scope)

The complete enumeration of everything to be designed (VI → tokens →
component library → per-persona screens → edge states) lives in the
2026-08-10 architecture/design session record; headline counts: ~30 generic
primitives, ~32 scientific components, 14 chemist screens, 9 auth screens,
10 admin screens, 8 platform pages, 4 mail templates. Priority: tokens →
scientific components → admin → auth (login last: it is the most
commoditized page in the set).

## §8 · The chroma ceiling — mid-saturation is a number

Ivan's ruling: 「颜色整体应该中饱和度一点,不要那么高的饱和度」.

**Ceiling: OKLCH chroma ≤ 0.106. Working band: 0.088.**

The ceiling is not chosen — it is the chroma of `#e0af68`, the gold already
settled for ivan.icu, so the limit is a colour he approved rather than a number
someone picked. Dirac's own hand-tuned palette (`accent` 0.088, `warn` 0.085,
`danger` 0.081) was already inside it; what broke the band was reaching for
Tailwind defaults (`#3b82f6` `#f43f5e` `#eab308`) for the scientific data
colours, which measured 1.5–2.0× over.

Measured in **OKLCH, never HSL**. HSL saturation is not perceptual: `#eab308`
and `#22d3ee` both report S=100% while differing by 20% in real chroma, so an
HSL cap passes colours that still glare.

Desaturation is done by **holding hue and lightness and moving chroma only** —
hue carries the meaning (blue = positive potential, red = negative; that is a
chemistry convention, not decoration) and lightness carries legibility.

Three things must survive it, and `design/check_palette.py` fails the build if
they do not:

1. every token at or under the ceiling;
2. every token ≥ 3:1 against **its own theme's** background — the light theme
   is a separate measurement, and the categorical chart ramp was found sitting
   at 1.7–2.5:1 there because the dark values had been inherited unchanged;
3. every diverging pair ≥ 0.10 ΔE apart — a calm palette that has merged `+`
   and `−` has destroyed the readout it was calming.

A rule written only in prose decays the first time someone is in a hurry. Run
the gate.
