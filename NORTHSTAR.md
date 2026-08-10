# Dirac — North Star

**Dirac is the open-source, browser-native upgrade of Schrödinger's commercial molecular design suite.** Every feature must make a medicinal chemist more effective at single-molecule design — perceiving chemistry, exploring conformations, evaluating drug-likeness — without install, license, or backend.

## We do pursue

- **Single-molecule depth over multi-molecule breadth.** One ligand, fully characterized, beats a thousand ligands superficially.
- **Browser-native.** WASM computes everything that can be computed; Python is escalation, not baseline.
- **Orthogonal visual channels.** Color, geometry, label, and halo each carry one piece of chemistry information. They never compete.
- **mol\* as engine, RDKit as cheminformatics, our code as the design surface.**

## We do NOT pursue (non-goals)

- **Multi-molecule SAR databases.** That is Schrödinger LiveDesign's whole-product scope; we are the per-molecule complement.
- **Protein engineering / mutation effect prediction.** Different domain (FoldX / Rosetta), different audience.
- **Molecular dynamics simulation.** We visualize MD results if you bring them; we do not run MD.
- **FEP / ΔΔG computation.** We will visualize FEP networks if you bring the data; we will not fake the numbers.
- **Backend-dependent features disguised as client-only.** If a feature needs a server, the UI says so explicitly.

## Why these non-goals

Scope creep is the dominant failure mode for visualization tools. Every "we should also do X" halves the chance that any single feature becomes excellent. The non-goals above are the recurring temptations that would dilute Dirac's identity.

## When in doubt

If a new feature request does not advance "a medicinal chemist more effective at single-molecule design in the browser without backend", it does not belong in Dirac. Open an issue, tag it `[scope-challenge]`, and argue for it explicitly.
