# Synthesis Projection Brief

**Status:** normative design; Phase 4 target

## Human question

How will this canonical compound be made, what physical material exists, and can
the requested quantity be reserved, transferred, or released safely?

## Entry profiles and cost

Synthetic chemist (route author), material operator (custody authority), assay
scientist (consumer), external CRO (scoped delivery). Physical errors have very
high irreversibility and compliance cost.

## Primary projection

Route graph, make queue/timeline, batch genealogy, container/location view,
quantity ledger, QC/release panel, and scanner-first bench mode. Compound,
batch, sample, aliquot, container, and location remain distinct canonical
entities along one visible identity chain.

## Actions and invariants

Request/accept route work, version route, register batch, record yield/QC,
reserve quantity, split, transfer custody/location, release, quarantine, and
cancel reservation. Quantity operations declare units, precision, conditions,
reservation version, and transaction isolation. Conservation and custody are
authoritative server invariants.

## Failure and concurrency

Two users cannot reserve the same material. Partial execution records applied
and compensated effects. Scanner mismatch, unit mismatch, expired QC, location
conflict, quarantine, and offline mode block canonical material changes while
preserving a recoverable local note.

## Acceptance witness

Register a batch, split into two containers, race two reservations, prove no
over-allocation, transfer custody, fail QC, quarantine, re-release with approval,
and hand an exact reserved sample quantity to Experiments.
