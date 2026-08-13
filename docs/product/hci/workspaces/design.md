# Design Projection Brief

**Status:** normative design; Phase 2 thin-slice target

## Human question

What molecular change should we make, why, and can this exact chemical identity
be promoted without ambiguity or duplication?

## Entry profiles and cost

Medicinal chemist (continuous author, very high identity error cost), structural
scientist (collaborating consumer), portfolio lead (promotion authority), agent
(proposal only unless delegated).

## Primary projection

Molecule canvas plus structure context, analog/series comparison, objective
scorecard, hypothesis/evidence inspector, and draft lineage. Suggestions are
directly inspectable and editable; raw SMILES is an optional expert import, not
the normal creation path.

## Identity and actions

Drafts are opaque UUIDs and versioned separately from canonical compounds.
Represent stereochemistry, isotopes, tautomer/protonation normalization policy,
salt/form, atom mapping, and provenance. Actions: branch draft, edit, compare,
request compute, attach rationale, preview promotion, promote/supersede. The
authority returns new, exact duplicate, normalized duplicate, or conflict
without leaking inaccessible compound existence.

## Failure and concurrency

Concurrent molecule edits branch; they never field-merge chemistry. A stale
structure/site/objective preview returns a semantic diff. Promotion may require
structured reason codes/evidence and human accountability; explanatory text is
not treated as clerical friction.

## Acceptance witness

Accept a structural handoff, modify a versioned stereocentre, compare normalized
identities, preview consequences, promote through the authoritative action, and
observe the same canonical compound in Campaigns without re-entry.
