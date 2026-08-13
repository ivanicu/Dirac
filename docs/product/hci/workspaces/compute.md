# Compute & Automation Projection Brief

**Status:** normative design; Phase 5 target

## Human question

What delegated work is running, waiting, failing, or complete, and are its
environment, provenance, and results valid for the originating question?

## Entry profiles and cost

Computational scientist (frequent submit/retry/cancel), scientist consumer
(result reader), platform operator (quota/incident authority), agent supervisor.
Compute is a cross-cutting work queue, not a scientific lifecycle stage.

## Primary projection

Global attention/work-queue entry plus dedicated Mission/Run/Attempt/Job graph,
resource/quota view, live logs, environment/artifact inspector, retry lineage,
and result-return panel. Each task preserves its originating object and route.

## Semantics and actions

Mission is intent, Run is one governed execution request, Attempt is a deliberate
scientific/operational try, and Job is an executor unit. Actions: preview cost,
submit, approve, reprioritize, pause/resume where supported, cancel, retry with
new attempt, transport-retry with same idempotency, inspect artifacts, validate,
and return results.

## Failure and scale

Quota, queue, partial output, executor loss, stale input, environment drift,
cancel/complete race, duplicate transport, and invalid result are distinct.
Large job sets use server aggregates and virtualized lists. Progress includes
source and accuracy; “100%” does not imply scientifically valid.

## Acceptance witness

Submit from a Campaign object, observe Mission/Run/Attempt/Job lineage, duplicate
the transport safely, perform a deliberate retry, race cancellation with
completion, invalidate an environment, and return only a provenance-valid result.
