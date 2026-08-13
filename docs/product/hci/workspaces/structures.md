# Structures Projection Brief

**Status:** normative design; Phase 2 thin-slice target

## Human question

What does this versioned structure show, exactly which substructure supports the
hypothesis, and is that evidence fit to hand to Design?

## Entry profiles and cost

Structural scientist (frequent author, high interpretation cost), medicinal
chemist (frequent consumer), reviewer (episodic acceptance), agent (bounded
annotation proposal).

## Primary projection

Use the existing Mol* scene as the main evidence surface with structure tree,
comparison/alignment controls, named selections, semantic layers, snapshot
history, and evidence/QC inspector. Global chrome may collapse in full-screen
scene mode but context and recovery remain accessible.

## Selection and actions

`StructureSelection` records structure/version, model, assembly, chain,
residues, atoms, altloc, and optional scene snapshot. Actions include save named
site, compare/alignment, annotate, review, preserve snapshot, and offer site to
Design. Camera and transient selection are non-durable; a saved site is durable.

## Failure and accessibility

Changed structure versions invalidate or map selections explicitly; ambiguous
mapping cannot silently move residues. Large structures stream with partialness.
Keyboard users navigate structure tree/residue/atom; screen readers receive
selection/QC summaries; colour is never the only semantic channel.

## Acceptance witness

On a versioned structure, select exact residues/atoms, save a site, change the
source version and observe a stale diff, then create a frozen handoff offer whose
receipt can be accepted from Design.
