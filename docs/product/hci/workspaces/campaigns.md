# Campaigns Projection Brief

**Status:** normative design; Phase 2 thin-slice target

## Human question

Which candidates exist, how do they trade off against the current objective,
and which eligible set should advance to Make, Test, or Compute?

## Entry profiles and cost

Medicinal chemist (daily comparison), portfolio lead (promotion authority),
computational scientist (large virtual sets), executive/reviewer (summary).

## Primary projection

Virtualized compound landscape with molecular depictions, series lanes,
versioned objective scorecard, Pareto/frontier views, evidence freshness, and a
decision tray. Tables remain available for precise scanning/export but do not
replace visual comparison.

## Selection and actions

Selection may be explicit compounds or a versioned `DerivedSetSelection` with
digest/count/source versions. Mixed eligibility is summarized by reason with
inspectable members; bulk action previews eligible, excluded, and unknown
groups. Actions: add canonical candidate, compare, name set, prioritize,
supersede, request Make/Test/Compute, and record rationale.

## Failure and scale

Objective changes stale scores and previews. Million-row views execute filters
server-side and never materialize inaccessible member lists. Partial data and
unknown eligibility are not coerced to zero. High-consequence bulk promotion
requires a scope preview even if it adds a click.

## Acceptance witness

Receive the Design promotion receipt, show the identical compound identity,
compare against objective v4, create a large derived set, preview mixed
eligibility, and fan out accepted subsets with one per-effect receipt.
