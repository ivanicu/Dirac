#!/usr/bin/env python3
"""Fifteen night worlds for Dirac, plus the incumbent, rendered on the real UI.

Ivan: day = a tech company, night = cyberpunk neon. These are the night
candidates.

TWO RULES THIS FILE OBEYS, both of them scars:

① A CANDIDATE IS A WORLD, NOT A PALETTE SWAP. Fifteen shifts of the same hue is
   one candidate shown fifteen times. Each world below declares a different HUE
   ARCHITECTURE — where the accent sits, what opposes it, whether the background
   is cold or warm, and how much chroma it spends — so choosing between them is
   choosing between places, not between sliders.

② SWATCHES LIE. A colour is not a colour until it is next to the thing it will
   actually be next to: a 2 px border against a panel, 11 px secondary text, a
   selected button beside four unselected ones, an isosurface over a dark
   viewport. So every candidate is rendered onto the REAL Dirac panel markup, at
   the real sizes, and the gallery is the test bench rather than an illustration
   of one.

Every candidate also carries the numbers the repo's own gate measures — body-text
contrast, secondary-text contrast, accent and danger against their panel, the ΔE
of each diverging viz pair, and the peak chroma — with a PASS/FAIL against those
same bars. A neon palette that fails 4.5:1 on body text is not a style choice,
it is an unreadable app, and the only way to know which is which is to compute it.

Run:  python3 design/night_candidates.py
Out:  design/night-candidates.html   (open it; no build, no server needed)
"""
from __future__ import annotations

import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from palette_lab import build, measure, verdict                      # noqa: E402
from check_palette import oklch                                       # noqa: E402

# ── the worlds ──────────────────────────────────────────────────────────────
# hue degrees are OKLCH: 20 red · 60 orange · 90 yellow-green · 145 green
# 190 cyan · 250 blue · 290 violet · 330 magenta
WORLDS = [
    dict(key='incumbent', name='Night', tag='the incumbent, for comparison',
         story='What ships today. Mid-saturation, teal accent, no neon. It is in '
               'the gallery because the alternative with zero build cost must be '
               'killed last and hardest, not omitted.',
         bg_hue=258, bg_chroma=0.014, accent_hue=178, accent_chroma=0.088,
         counter_hue=259, ok_hue=155, warn_hue=75, danger_hue=22,
         mep=(259, 16), orb=(155, 300), mlp=(75, 200), density=60,
         viz_chroma=0.088),

    dict(key='kowloon', name='Kowloon Rain', tag='cyan signage in standing water',
         story='The canonical one: cold blue-black street, cyan sign, magenta '
               'counter-sign. Chroma spent on two hues 135° apart so the accent '
               'and the alarm can never be confused at a glance.',
         bg_hue=250, bg_chroma=0.030, accent_hue=195, accent_chroma=0.160,
         counter_hue=330, ok_hue=165, warn_hue=70, danger_hue=350,
         mep=(240, 350), orb=(165, 300), mlp=(70, 195), density=45,
         viz_chroma=0.150),

    dict(key='vending', name='Tokyo Vending', tag='machine-white light at 3 a.m.',
         story='Lit from inside, not from a sign. Cool white-blue primary with a '
               'hot pink that only appears on state, so the interface is calm '
               'until something happens.',
         bg_hue=265, bg_chroma=0.022, accent_hue=215, accent_chroma=0.140,
         counter_hue=345, ok_hue=160, warn_hue=80, danger_hue=345,
         mep=(230, 355), orb=(160, 290), mlp=(85, 205), density=55,
         viz_chroma=0.140),

    dict(key='acid', name='Acid Terminal', tag='phosphor green meets neon',
         story='A CRT that grew up. Green-black ground, phosphor accent, amber '
               'for warnings — the two colours a terminal has ever had, pushed '
               'to neon. The most legible of the loud options.',
         bg_hue=150, bg_chroma=0.018, accent_hue=135, accent_chroma=0.180,
         counter_hue=85, ok_hue=140, warn_hue=85, danger_hue=25,
         mep=(210, 25), orb=(135, 300), mlp=(85, 190), density=60,
         viz_chroma=0.160),

    dict(key='reactor', name='Magenta Reactor', tag='violet core, cyan coolant',
         story='Magenta leads and cyan supports — the inverse of every other '
               'cyberpunk theme, which is why it reads as ours. Deep indigo '
               'ground keeps the magenta from vibrating.',
         bg_hue=285, bg_chroma=0.035, accent_hue=330, accent_chroma=0.185,
         counter_hue=195, ok_hue=165, warn_hue=65, danger_hue=15,
         mep=(250, 340), orb=(190, 320), mlp=(70, 195), density=40,
         viz_chroma=0.155),

    dict(key='ion', name='Ion Blue', tag='one hue, pushed hard',
         story='Near-monochromatic: the accent IS the background hue at high '
               'chroma. The closest a cyberpunk night gets to the day theme, so '
               'it is the safest pairing if the two must feel like one product.',
         bg_hue=250, bg_chroma=0.020, accent_hue=250, accent_chroma=0.150,
         counter_hue=205, ok_hue=170, warn_hue=75, danger_hue=20,
         mep=(255, 20), orb=(200, 305), mlp=(80, 200), density=50,
         viz_chroma=0.140),

    dict(key='sodium', name='Sodium Vapor', tag='street lamps, not signage',
         story='The least clichéd of the set: sodium-orange light against '
               'steel-blue shadow. Warm accent on a cold ground is rare in this '
               'genre and reads expensive rather than loud.',
         bg_hue=245, bg_chroma=0.028, accent_hue=60, accent_chroma=0.150,
         counter_hue=220, ok_hue=150, warn_hue=45, danger_hue=20,
         mep=(240, 25), orb=(160, 295), mlp=(60, 210), density=55,
         viz_chroma=0.140),

    dict(key='sakura', name='Chrome Sakura', tag='pale pink over gunmetal',
         story='Cyberpunk with the volume down. Charcoal-violet ground, pale '
               'pink accent, chrome-blue secondary. The one candidate a chemist '
               'could stare at for eight hours.',
         bg_hue=300, bg_chroma=0.014, accent_hue=350, accent_chroma=0.115,
         counter_hue=215, ok_hue=160, warn_hue=70, danger_hue=25,
         mep=(240, 355), orb=(165, 310), mlp=(75, 200), density=45,
         viz_chroma=0.115),

    dict(key='lime', name='Nuclear Lime', tag='hazard green against purple',
         story='The loudest world here. Lime at 0.20 chroma with a violet '
               'counter — maximum separation, maximum presence. Included to mark '
               'the ceiling: if this is too much, the answer is a number below it.',
         bg_hue=270, bg_chroma=0.030, accent_hue=120, accent_chroma=0.200,
         counter_hue=300, ok_hue=130, warn_hue=80, danger_hue=15,
         mep=(250, 20), orb=(120, 300), mlp=(90, 195), density=55,
         viz_chroma=0.170),

    dict(key='blade', name='Blade Amber', tag='dust, orange air, one teal light',
         story='Warm black ground — almost nothing else does this — with amber '
               'dominant and a single teal for interaction. Reads as heat and '
               'distance rather than rain and glass.',
         bg_hue=40, bg_chroma=0.025, accent_hue=55, accent_chroma=0.160,
         counter_hue=190, ok_hue=150, warn_hue=50, danger_hue=20,
         mep=(235, 25), orb=(190, 320), mlp=(55, 195), density=60,
         viz_chroma=0.150),

    dict(key='abyssal', name='Abyssal Bloom', tag='bioluminescence, not electricity',
         story='The only world whose light is alive: abyss navy with '
               'green-cyan bloom. Thematically closest to what the app actually '
               'draws — a field glowing around a molecule.',
         bg_hue=220, bg_chroma=0.035, accent_hue=165, accent_chroma=0.170,
         counter_hue=285, ok_hue=155, warn_hue=75, danger_hue=350,
         mep=(230, 350), orb=(165, 290), mlp=(80, 190), density=45,
         viz_chroma=0.160),

    dict(key='void', name='Void Violet', tag='violet on true black',
         story='Ground pushed to near-black so the accents float. Violet with a '
               'rose counter; the highest apparent contrast in the set without '
               'the highest chroma.',
         bg_hue=300, bg_chroma=0.020, accent_hue=300, accent_chroma=0.175,
         counter_hue=15, ok_hue=160, warn_hue=70, danger_hue=15,
         mep=(255, 15), orb=(300, 175), mlp=(70, 200), density=50,
         viz_chroma=0.155),

    dict(key='copper', name='Circuit Copper', tag='the board, not the city',
         story='Dark green solder mask, copper traces, cyan vias. Cyberpunk from '
               'inside the hardware — a good fit for a tool whose subject is '
               'literally computed.',
         bg_hue=160, bg_chroma=0.020, accent_hue=45, accent_chroma=0.145,
         counter_hue=195, ok_hue=145, warn_hue=60, danger_hue=25,
         mep=(215, 25), orb=(150, 300), mlp=(50, 195), density=55,
         viz_chroma=0.140),

    dict(key='foil', name='Hologram Foil', tag='iridescent on graphite',
         story='Two accents of equal weight, 135° apart, on a nearly neutral '
               'graphite. The interface shifts hue as you move through it — the '
               'only world here that is about the TRANSITION between colours.',
         bg_hue=265, bg_chroma=0.012, accent_hue=185, accent_chroma=0.170,
         counter_hue=320, ok_hue=165, warn_hue=75, danger_hue=340,
         mep=(185, 320), orb=(160, 300), mlp=(75, 195), density=45,
         viz_chroma=0.160),

    dict(key='ghost', name='Ghost Protocol', tag='restraint, with one live wire',
         story='Almost no chroma anywhere, and a single electric accent that is '
               'the only saturated thing on screen. The most likely to still '
               'look right in two years, and the least likely to feel cyberpunk '
               'in a screenshot.',
         bg_hue=240, bg_chroma=0.008, accent_hue=220, accent_chroma=0.095,
         counter_hue=15, ok_hue=160, warn_hue=75, danger_hue=15,
         mep=(245, 20), orb=(160, 295), mlp=(75, 205), density=55,
         viz_chroma=0.110),

    dict(key='hazard', name='Hazard Bay', tag='industrial, lit for work',
         story='Yellow-green hazard light with a warning orange — the palette of '
               'a place where something dangerous is running. Fits a tool that '
               'refuses requests and says why.',
         bg_hue=235, bg_chroma=0.030, accent_hue=95, accent_chroma=0.170,
         counter_hue=25, ok_hue=140, warn_hue=70, danger_hue=20,
         mep=(240, 20), orb=(150, 300), mlp=(85, 200), density=60,
         viz_chroma=0.155),
]

# ── the real UI fragment every candidate is rendered onto ───────────────────
PANEL = """
<div class="app">
  <header class="topbar">
    <span class="brand">Dirac</span>
    <span class="sel">1CBS · RETINOID-BINDING PROTEIN</span>
    <span class="pill">READY</span>
  </header>
  <div class="body">
    <aside class="panel">
      <div class="tabs">
        <span>FOCUS</span><span>LIGAND</span><span class="on">FIELDS</span><span>PHYSICS</span>
      </div>
      <div class="phead">ENERGY FIELD WELLS</div>
      <div class="pmeta">REA · A:200 &nbsp;·&nbsp; <em>backend online</em></div>
      <div class="grid">
        <button class="sel">V Electrostatic well</button>
        <button>Ψ QM potential</button>
        <button>φ HOMO</button>
        <button>φ* LUMO</button>
        <button>ρ e⁻ density</button>
        <button>logP Lipophilicity</button>
      </div>
      <div class="slider"><span>Isovalue</span><i></i><b>±10.0 kcal/mol</b></div>
      <div class="rows">
        <div><span>Method</span><b>gasteiger</b></div>
        <div><span>Net charge</span><b>−0.02 e</b></div>
        <div><span>Compute time</span><b>0.21 s</b></div>
        <div><span>Cache</span><b>db · 12 ms</b></div>
      </div>
      <div class="refusal">
        <b>σ-hole not representable</b>
        Gasteiger point charges cannot carry σ-hole anisotropy — the Physics tab
        answers this with a QM surface.
      </div>
      <div class="legend">
        <i style="--c:var(--viz-mep-pos)"></i><span>MEP +</span>
        <i style="--c:var(--viz-mep-neg)"></i><span>MEP −</span>
        <i style="--c:var(--viz-orb-pos)"></i><span>φ +</span>
        <i style="--c:var(--viz-orb-neg)"></i><span>φ −</span>
        <i style="--c:var(--viz-mlp-pos)"></i><span>logP</span>
        <i style="--c:var(--viz-mlp-neg)"></i><span>polar</span>
        <i style="--c:var(--viz-density)"></i><span>ρ</span>
      </div>
    </aside>
    <div class="viewport">
      <div class="well"></div>
      <div class="mol"></div>
      <span class="vlabel">1CBS · β-barrel · field ±10.0 kcal/mol</span>
    </div>
  </div>
</div>
"""

CSS = """
*{box-sizing:border-box}
body{margin:0;background:#07080b;color:#e8edf5;
  font:13px/1.5 ui-monospace,'SF Mono',Menlo,monospace;padding:28px}
h1{font-size:19px;letter-spacing:.14em;text-transform:uppercase;margin:0 0 6px}
.intro{color:#8b93a5;max-width:82ch;margin:0 0 26px;font-size:12.5px}
.intro b{color:#e8edf5}
.card{margin:0 0 34px;border:1px solid #1b2130;border-radius:10px;overflow:hidden;
  background:#0b0e14}
.chead{display:flex;gap:14px;align-items:baseline;padding:12px 16px;
  border-bottom:1px solid #1b2130;flex-wrap:wrap}
.num{font-size:11px;letter-spacing:.06em;color:#6b7689}
.cname{font-size:15px;letter-spacing:.1em;text-transform:uppercase}
.ctag{color:#8b93a5;font-size:12px}
.verdict{margin-left:auto;font-size:11px;letter-spacing:.1em;padding:3px 9px;
  border-radius:4px;border:1px solid}
.pass{color:#7dc9aa;border-color:#24503c;background:#0f2018}
.fail{color:#e1a18e;border-color:#54302a;background:#20100c}
.story{padding:10px 16px 0;color:#98a1b3;font-size:12px;max-width:96ch}
.stage{padding:16px}
.metrics{display:flex;gap:18px;flex-wrap:wrap;padding:0 16px 14px;font-size:11px;
  color:#7c8698}
.metrics b{color:#c7cedb;font-weight:400}
.metrics .bad{color:#e1a18e}
.tokens{padding:0 16px 16px;display:flex;gap:5px;flex-wrap:wrap}
.tokens i{width:34px;height:16px;border-radius:3px;border:1px solid #ffffff14;
  display:block}
.tokens span{font-size:10px;color:#5f6878;align-self:center}

/* ── the app fragment: real markup at real sizes ───────────────────────── */
.app{border:1px solid var(--border);border-radius:8px;overflow:hidden;
  background:var(--bg);color:var(--text);font-size:12px}
.topbar{display:flex;align-items:center;gap:14px;padding:9px 13px;
  background:var(--surface);border-bottom:1px solid var(--border)}
.brand{font-size:15px;letter-spacing:.16em;color:var(--accent)}
.sel{color:var(--text-2);font-size:11px;letter-spacing:.05em}
.pill{margin-left:auto;font-size:10px;letter-spacing:.1em;padding:2px 8px;
  border:1px solid var(--border-strong);border-radius:3px;color:var(--ok)}
.body{display:grid;grid-template-columns:320px 1fr;min-height:352px}
.panel{background:var(--surface);border-right:1px solid var(--border);padding:11px}
.tabs{display:flex;gap:11px;font-size:10px;letter-spacing:.09em;
  color:var(--text-3);padding-bottom:9px;border-bottom:1px solid var(--border)}
.tabs .on{color:var(--accent);border-bottom:2px solid var(--accent);padding-bottom:7px}
.phead{margin:11px 0 3px;font-size:11px;letter-spacing:.11em;color:var(--text)}
.pmeta{font-size:10px;color:var(--text-3);margin-bottom:10px}
.pmeta em{color:var(--ok);font-style:normal}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:5px}
.grid button{font:inherit;font-size:10.5px;text-align:left;padding:6px 7px;
  background:var(--surface-2);color:var(--text-2);border:1px solid var(--border);
  border-radius:4px;cursor:pointer}
.grid button.sel{background:var(--accent-soft);color:var(--accent);
  border-color:var(--accent)}
.slider{display:flex;align-items:center;gap:8px;margin:11px 0 3px;font-size:10px;
  color:var(--text-3)}
.slider i{flex:1;height:3px;background:var(--surface-3);border-radius:2px;
  position:relative}
.slider i::after{content:'';position:absolute;left:52%;top:-4px;width:11px;
  height:11px;border-radius:50%;background:var(--accent)}
.slider b{color:var(--text-2);font-weight:400}
.rows{margin-top:11px;border-top:1px solid var(--border);padding-top:8px}
.rows div{display:flex;justify-content:space-between;font-size:10.5px;
  padding:2.5px 0;color:var(--text-3)}
.rows b{color:var(--text-2);font-weight:400}
.refusal{margin-top:11px;padding:8px 9px;border-radius:4px;font-size:10px;
  line-height:1.45;background:var(--warn-soft);color:var(--text-2);
  border-left:2px solid var(--warn)}
.refusal b{display:block;color:var(--warn);margin-bottom:3px;font-weight:400}
.legend{display:grid;grid-template-columns:repeat(4,auto);gap:5px 8px;
  margin-top:11px;font-size:9.5px;color:var(--text-3);align-items:center;
  justify-content:start}
.legend i{width:11px;height:11px;border-radius:2px;background:var(--c);
  display:inline-block}
/* ── NEON IS A LIGHT BEHAVIOUR, NOT A HUE ───────────────────────────────
   The first draft of this gallery was palette-only, and every candidate read
   as "competent dark UI" rather than cyberpunk — because the colours were
   PAINTED ON rather than EMITTING. A neon sign is legible because it throws
   light onto what surrounds it. So the treatment below is token-driven (it
   uses --accent and --glow, nothing hardcoded) and travels with every
   candidate: a selected control glows, the active tab has a lit underline,
   the brand halates, the viewport carries a faint grid the field lights up.
   The palette is still the variable; this is the world it is shown in. */
.brand{text-shadow:0 0 12px color-mix(in oklab,var(--accent) 55%,transparent),
                   0 0 34px color-mix(in oklab,var(--accent) 22%,transparent)}
.tabs .on{text-shadow:0 0 10px color-mix(in oklab,var(--accent) 45%,transparent);
  box-shadow:0 2px 9px -3px var(--accent)}
.grid button.sel{box-shadow:0 0 0 1px var(--accent),
  0 0 14px -2px color-mix(in oklab,var(--accent) 55%,transparent),
  inset 0 0 22px -12px var(--accent);
  text-shadow:0 0 9px color-mix(in oklab,var(--accent) 40%,transparent)}
.slider i::after{box-shadow:0 0 11px 1px color-mix(in oklab,var(--accent) 65%,transparent)}
.pill{box-shadow:inset 0 0 12px -8px var(--ok),
      0 0 10px -6px color-mix(in oklab,var(--ok) 60%,transparent)}
.refusal{box-shadow:inset 2px 0 12px -8px var(--warn)}
.refusal b{text-shadow:0 0 8px color-mix(in oklab,var(--warn) 45%,transparent)}
.legend i{box-shadow:0 0 7px -1px var(--c)}
.viewport{position:relative;background:var(--scene-bg);overflow:hidden;
  /* the grid is what makes a dark rectangle read as a SPACE. 2 % of the accent
     — any more and it competes with the molecule it exists to sit behind. */
  background-image:
    linear-gradient(color-mix(in oklab,var(--accent) 5%,transparent) 1px,transparent 1px),
    linear-gradient(90deg,color-mix(in oklab,var(--accent) 5%,transparent) 1px,transparent 1px);
  background-size:100% 26px,26px 100%}
.well{position:absolute;left:50%;top:47%;width:224px;height:224px;
  transform:translate(-50%,-50%);border-radius:50%;
  background:radial-gradient(circle at 38% 34%,
    color-mix(in oklab,var(--viz-mep-pos) 62%,transparent) 0%,
    color-mix(in oklab,var(--viz-mep-pos) 16%,transparent) 34%,transparent 62%),
   radial-gradient(circle at 66% 68%,
    color-mix(in oklab,var(--viz-mep-neg) 58%,transparent) 0%,
    color-mix(in oklab,var(--viz-mep-neg) 14%,transparent) 36%,transparent 64%);
  filter:blur(.4px)}
.well::after{content:'';position:absolute;inset:14%;border-radius:50%;
  border:1px solid color-mix(in oklab,var(--viz-mep-pos) 34%,transparent);
  box-shadow:0 0 26px -6px color-mix(in oklab,var(--viz-mep-pos) 45%,transparent),
             inset 0 0 30px -10px color-mix(in oklab,var(--viz-mep-neg) 40%,transparent)}
.mol{position:absolute;left:50%;top:47%;width:118px;height:6px;
  transform:translate(-50%,-50%) rotate(-16deg);border-radius:3px;
  background:linear-gradient(90deg,var(--text-3),var(--text-2),var(--text-3));
  box-shadow:0 0 20px 2px color-mix(in oklab,var(--glow) 55%,transparent),
             0 0 54px 10px color-mix(in oklab,var(--glow) 22%,transparent)}
.vlabel{position:absolute;left:12px;bottom:10px;font-size:9.5px;
  color:var(--text-3);letter-spacing:.05em}
"""


def metric_html(m: dict, problems: list[str]) -> str:
    def cell(label, value, ok, fmt='{:.1f}:1'):
        cls = '' if ok else ' class="bad"'
        return f'<span>{label} <b{cls}>{fmt.format(value)}</b></span>'
    parts = [
        cell('text/bg', m['text_on_bg'], m['text_on_bg'] >= 4.5),
        cell('text-2/panel', m['text2_on_surface'], m['text2_on_surface'] >= 4.5),
        cell('accent/panel', m['accent_on_surface'], m['accent_on_surface'] >= 3.0),
        cell('danger/panel', m['danger_on_surface'], m['danger_on_surface'] >= 3.0),
    ]
    for k, de in m['pair_de'].items():
        parts.append(cell(f'ΔE {k}', de, de >= 0.10, '{:.3f}'))
    parts.append(f"<span>peak chroma <b>{m['max_chroma']:.3f}</b> "
                 f"({html.escape(m['max_chroma_token'])})</span>")
    parts.append(f"<span>viz chroma <b>{m['viz_max_chroma']:.3f}</b></span>")
    if problems:
        parts.append('<span class="bad">' + html.escape(' · '.join(problems)) + '</span>')
    return '<div class="metrics">' + ''.join(parts) + '</div>'


def main() -> int:
    cards = []
    summary = []
    for i, w in enumerate(WORLDS):
        tokens = build(w)
        m = measure(tokens)
        v, problems = verdict(m)
        summary.append((w['name'], v, m))
        style = ';'.join(f'{k}:{val}' for k, val in tokens.items())
        swatches = ''.join(
            f'<i style="background:{val}" title="{html.escape(k)} {val}"></i>'
            for k, val in tokens.items()
            if k in ('--bg', '--surface', '--surface-2', '--surface-3', '--border',
                     '--text', '--text-2', '--accent', '--ok', '--warn', '--danger',
                     '--info', '--viz-mep-pos', '--viz-mep-neg', '--viz-orb-pos',
                     '--viz-orb-neg', '--viz-mlp-pos', '--viz-mlp-neg',
                     '--viz-density'))
        cards.append(f"""
<section class="card">
  <div class="chead">
    <span class="num">{i:02d}</span>
    <span class="cname">{html.escape(w['name'])}</span>
    <span class="ctag">{html.escape(w['tag'])}</span>
    <span class="verdict {'pass' if v == 'PASS' else 'fail'}">{v}</span>
  </div>
  <p class="story">{html.escape(w['story'])}</p>
  {metric_html(m, problems)}
  <div class="tokens">{swatches}<span>bg → viz</span></div>
  <div class="stage" style="{style}">{PANEL}</div>
</section>""")

    passes = sum(1 for _, v, _ in summary if v == 'PASS')
    doc = f"""<!doctype html>
<meta charset="utf-8">
<title>Dirac · night candidates</title>
<style>{CSS}</style>
<h1>Night candidates · {len(WORLDS)} worlds</h1>
<p class="intro">
Day is a tech company; night is cyberpunk. Each card below is a different
<b>hue architecture</b>, not a shift of the same one — where the accent sits,
what opposes it, whether the ground is cold or warm, and how much chroma it
spends. Every one is rendered on the <b>real Fields panel markup at real sizes</b>,
because a colour is not a colour until it is next to 10 px secondary text and a
2 px border: swatches agree with each other far more than interfaces do.
<br><br>
The numbers are the repo's own bars, computed per candidate, not opinions:
body text needs <b>4.5:1</b>, non-text UI colour <b>3.0:1</b>, and a diverging
viz pair needs <b>ΔE ≥ 0.10</b> or blue-positive and red-negative read as one
colour on a screenshot. <b>{passes} of {len(WORLDS)} pass all of them.</b> Peak
chroma is stated because it is the dial: the incumbent spends 0.088, the loudest
here spends 0.20, and "how neon" is that one number.
<br><br>
<b>One thing deliberately does NOT follow the theme.</b> The field colours keep
chemistry convention — blue positive, red negative — even where that clashes with
the accent, which you can see most clearly in <b>Nuclear Lime</b>: a lime
interface around a blue/red field. Theming those two would make a chemist ask
which palette they were looking at before they could read a sign, and the whole
point of a convention is that the answer never depends on that. The UI is the
theme's; the data is the discipline's.
</p>
{''.join(cards)}
"""
    out = Path(__file__).resolve().parent / 'night-candidates.html'
    out.write_text(doc, encoding='utf-8')

    print(f'{"#":>3}  {"world":<16} {"verdict":<6} {"text":>6} {"txt2":>6} '
          f'{"accent":>7} {"ΔEmep":>7} {"chroma":>7}')
    for i, (name, v, m) in enumerate(summary):
        print(f'{i:>3}  {name:<16} {v:<6} {m["text_on_bg"]:>6.1f} '
              f'{m["text2_on_surface"]:>6.1f} {m["accent_on_surface"]:>7.1f} '
              f'{m["pair_de"]["mep"]:>7.3f} {m["max_chroma"]:>7.3f}')
    print(f'\n{passes}/{len(WORLDS)} pass every bar · wrote {out}')
    return 0


def emit(key: str) -> int:
    """Print one candidate as a paste-ready :root block.

    Exists so choosing is a command rather than 30 copy-pastes: the gallery is
    where a decision is made, and a decision that then needs half an hour of
    transcription is a decision that gets made badly to avoid the transcription.
    """
    world = next((w for w in WORLDS if w['key'] == key), None)
    if world is None:
        print(f'no candidate {key!r}. keys: '
              + ', '.join(w['key'] for w in WORLDS), file=sys.stderr)
        return 2
    tokens = build(world)
    m = measure(tokens)
    v, problems = verdict(m)
    print(f'/* Dirac Night · {world["name"]} — {world["tag"]}')
    print(f' * {v}: text {m["text_on_bg"]:.1f}:1 · text-2 {m["text2_on_surface"]:.1f}:1 '
          f'· accent {m["accent_on_surface"]:.1f}:1 · peak chroma {m["max_chroma"]:.3f}')
    print(f' * generated by design/night_candidates.py --apply {key}; the ANCHORS in')
    print(f' * that file are the source, not this block — edit there and regenerate,')
    print(f' * or the palette gains a second home the moment it is tuned. */')
    print(':root {')
    for k, val in tokens.items():
        print(f'    {k}: {val};')
    print('}')
    if problems:
        print('/* ⚠ ' + ' · '.join(problems) + ' */')
    return 0


if __name__ == '__main__':
    if '--apply' in sys.argv:
        i = sys.argv.index('--apply')
        sys.exit(emit(sys.argv[i + 1] if i + 1 < len(sys.argv) else ''))
    sys.exit(main())
