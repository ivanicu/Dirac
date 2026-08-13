# Dirac documentation map

This directory contains three different classes of material. Keep their status explicit:

1. **Current product and architecture guidance** describes the working tree.
2. **Accepted decisions and contracts** define invariants that code and tests enforce.
3. **Historical evidence** records what was measured at a particular point in time and
   must not be read as current state.

## Current entry points

| Question | Source |
|---|---|
| What is Dirac and how do I run it? | [`../README.md`](../README.md) |
| What is actually connected? | [`../STATUS.md`](../STATUS.md) |
| How does the system work? | [`../ARCHITECTURE.md`](../ARCHITECTURE.md) |
| What is the product model? | [`product/PRODUCT_ARCHITECTURE.md`](product/PRODUCT_ARCHITECTURE.md) |
| What are the canonical objects and commands? | [`product/DOMAIN_MODEL.md`](product/DOMAIN_MODEL.md), [`product/COMMAND_MODEL.md`](product/COMMAND_MODEL.md), and `../contracts/` |
| What is the HCI contract? | [`product/HUMAN_INTERFACE_CHARTER.md`](product/HUMAN_INTERFACE_CHARTER.md) and the `product/HCI_*` documents |
| How is Motif designed and tested? | [`product/motif-v3/README.md`](product/motif-v3/README.md) |
| How is the local runtime operated? | [`../deploy/README.md`](../deploy/README.md) and [`../backend/README.md`](../backend/README.md) |
| How is remote access secured? | [`security/REMOTE.md`](security/REMOTE.md) |

`adr/` contains accepted architecture decisions. `architecture/` contains the generated,
source-derived architecture twin. `screenshots/` contains documentation assets.

`product/motif-v3/` is the current Motif semantic and execution baseline.
`product/motif-v2/` is retained as the immediately preceding versioned design record; its
versioned name is intentional and it is not current implementation guidance.

## Historical material

`archive/YYYY-MM-DD/` contains time-bounded audits, measurements and superseded plans.
Archived documents retain their original detail for provenance, but their commands,
counts, ports and maturity claims are not maintained as current facts.

## Upstream mol\* manual

`docs/` and `mkdocs.yml` are the inherited mol\* developer manual. They are kept for the
vendored engine and built by the docs workflow; they are not the Dirac product manual.

To build that manual locally:

```bash
python3 -m pip install mkdocs-material
cd docs
mkdocs build
```

## Maintenance rule

- Put live counts in `STATUS.md` and make them source-derived.
- Put stable boundaries in `ARCHITECTURE.md` or an ADR.
- Put product semantics in `product/`.
- Move completed audits and superseded plans to `archive/`; do not leave them beside
  current guidance with an ambiguous title.
- Do not duplicate Commands, ObjectKinds or Method schemas in prose when a link to
  `contracts/` is sufficient.
