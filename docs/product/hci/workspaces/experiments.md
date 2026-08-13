# Experiments Projection Brief

**Status:** normative design; Phase 4 target

## Human question

What experiment will answer the question, which exact samples and wells are
used, and are the execution, deviations, QC, and results trustworthy?

## Entry profiles and cost

Assay scientist (author/operator), material operator (sample provider), data
scientist (result consumer), reviewer (QC/release), automation service.

## Primary projection

Protocol/version card, plate-map editor, sample allocation tray, dilution plan,
schedule/resource timeline, live execution/QC surface, result visualization, and
return-to-question action. Domain-specific protocol fields are valid; generic
JSON and canonical-ID forms are not.

## Selection and actions

`PlateSelection` names plate/layout version and exact wells. Actions: design
experiment, assign/reserve samples, calculate dilution, randomize/blind, schedule,
start, record deviation, apply QC, complete/abort, release dataset version, and
return evidence. Atomic start consumes/reserves only the accepted quantities.

## Failure and accessibility

Layout conflicts return well-level diffs. Insufficient/revoked samples,
instrument conflicts, protocol supersession, cancellation race, partial upload,
failed QC, and unblinding are explicit. The plate exposes ARIA grid semantics,
keyboard range/fill, textual layout, scope preview, and live announcements.

## Acceptance witness

Accept a released sample, build a 384-well randomized/blinded plan, preview
consumption, handle a concurrent layout edit, start atomically, record a
deviation/QC failure, release a dataset version, and return evidence to SAR.
