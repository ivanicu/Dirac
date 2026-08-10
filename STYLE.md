# Dirac — Style

The visual and interaction language of Dirac. Every agent working on UI must read this first.

## Visual north star

**Calm, dense, honest.** A chemist using Dirac should feel they are looking at chemistry, not at a UI. The interface gets out of the way; the molecule carries the meaning.

## Color

| Role | Value | Usage rule |
|---|---|---|
| Background | `#0a0e14` | App background |
| Surface | `#131a23` | Sidebar, panels |
| Surface-2 | `#1a2230` | Inputs, cards inside panels |
| Border | `#232c3a` | Hairlines between regions |
| Text primary | `#e8edf5` | Body copy, headings |
| Text secondary | `#9aa5b8` | Labels, metadata |
| Text muted | `#6b7689` | Footnotes, hints |
| Accent | `#7dd3c0` | **Single accent.** Active master-tab underline, P1 marks, "ready" status pill, focused-atom highlight. Never use accent for body, decoration, or branding. |
| Warn | `#d4a574` | Caution notes, partial-charge unavailable |
| Negative | `#e15555` | H-bond acceptor, formal negative charge, error toast |
| Positive | `#4dabf7` | H-bond donor, formal positive charge |
| Aromatic | `0xc792ea` | Aromatic ring color (3D overpaint + 2D highlight + 3D disk) |
| Hydrophobic | `#868e96` | Hydrophobic halo in pharmacophore |

**Rules:**
- Color is a channel, not decoration. Each color above carries ONE meaning.
-CPK atom colors are exempt — they belong to mol*'s atomic-detail representation.
- Never introduce a new color without updating this table.

## Typography

- Family: `Inter, ui-sans-serif, system-ui, sans-serif`
- Size scale (px, hard-enforced): `9, 10, 11, 12, 13, 15, 17, 22`
- Weight: `400` body / `600` emphasis / `700` heading / `800` eyebrow + tabular metrics
- Numerics: `font-variant-numeric: tabular-nums` on every metric display
- Letter-spacing: `-0.02em` for ≥17px headings; `0.08em` uppercase eyebrows; `0` body

## Spacing

Scale (px, hard-enforced): `4, 8, 12, 16, 24`. Nothing between. If you reach for `7px` or `10px`, you are doing it wrong.

## Layout

- **Top bar:** 64px tall, brand left + scene controls center + status right. Always visible.
- **Sidebar:** 380px wide, never wider. Five master-tabs at top, detail panel scrollable, sticky diagnostics at bottom.
- **Main viewport:** flex-1, no chrome. The molecule owns this space.
- **Mobile (<900px):** top bar collapses; sidebar becomes a 320px drawer.

## Interaction

- **Master-detail.** Five master-tabs (Focus / Semantics / Ligand / VFX / Ledger). Only one section visible at a time.
- **Click-to-select is bidirectional.** Click a 2D atom → 3D selects + camera focuses. Click a 3D atom → 2D highlights (pending wire).
- **Every overlay is a toggle.** Nothing is forced on the user. Every layer can be turned off.
- **Status pill always tells the truth.** `Ready` / `Loading X…` / `Applying…` / full error message. No silent failures, no stuck spinners.
- **Honest unavailable.** If RDKit lacks an API in this build, the badge says "unavailable in this RDKit-JS build" — not silently missing.

## Anti-patterns

- ❌ Using the accent color for body text, decoration, or branding.
- ❌ Mixing visualization channels (e.g., coloring atoms by partial charge while aromaticity is also on).
- ❌ Blocking the UI on a WASM computation without showing a "Computing…" badge.
- ❌ Adding a feature without an availability badge.
- ❌ Introducing a color, font size, or spacing value not in this document.
- ❌ Long-lived feature branches (see `AGENTS.md`).
- ❌ Silently failing — if a backend is missing, the UI must say so.

## Component patterns

- **Panel:** rounded 12px, 1px border, padding 14px, surface background.
- **Upgrade row:** collapsed by default; click row to expand. Toggle / title / cost always visible; guidance / legend / debug button revealed on expand.
- **Master-tab:** underline-on-active; no fill change. Active color = accent.
- **Layer-tab (sub-category pill):** rounded 999px, border, fill on hover, accent border + accent-soft fill on active.
- **Field:** label uppercase 9px, input rounded 7px, surface-2 background, border-strong border; accent border + soft accent glow on focus.

## When you need to break a rule

Document it. Add a comment in the code pointing to the rule you broke, and open a `[style-exception]` issue so the breakage is visible to other agents.
