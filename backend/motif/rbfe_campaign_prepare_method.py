"""Durable Method wrapper for server-owned RBFE campaign preparation.

The scientific builder remains in ``PostgresRbfeReferenceResolver``.  This module
only turns that already-governed operation into an Invocation Method so the Job
ledger, authenticated actor, request digest and reconnect semantics surround the
minutes-long native preparation phase.
"""
from __future__ import annotations

from typing import Any

import failures
from invocation import HandlerResult, InvocationContext


def prepare_campaign_handler(
        payload: dict, ctx: InvocationContext) -> HandlerResult:
    """Prepare one campaign through injected, durable server capabilities.

    Cancellation is checked before any scientific side effect.  Once the resolver
    starts it may commit content-addressed artifacts and a campaign generation, so a
    late cancellation request must not turn a successfully committed generation into
    a false cancelled result.  The Job remains reconnectable while that phase runs.
    """
    ctx.check_budget()
    resolver = ctx.rbfe_reference_resolver
    if resolver is None:
        raise failures.DiracFailure(
            "DB_UNAVAILABLE",
            "campaign preparation requires the registered receptor/pose resolver")
    writer = ctx.artifact_writer
    if writer is None:
        raise failures.DiracFailure(
            "DB_UNAVAILABLE",
            "campaign preparation requires the durable artifact writer")
    if not ctx.actor:
        raise failures.DiracInternal(
            "campaign preparation requires an authenticated Invocation actor")
    if not ctx.job_id:
        raise failures.DiracInternal(
            "campaign preparation is job-only and requires its durable Job id")

    ctx.on_progress("campaign_preparation_admitted", 0.01)
    result = resolver.prepare_campaign(
        payload, writer, ctx.actor, job_id=ctx.job_id,
        dispatch_fence=ctx.assert_dispatch)
    if not isinstance(result, dict):
        raise failures.DiracInternal(
            "campaign preparation resolver returned no typed result")
    ctx.on_progress("campaign_preparation_committed", 1.0)
    return HandlerResult(
        result=result,
        provenance={
            "operation": "server_owned_rbfe_campaign_preparation",
            "durability": "job_and_postgres_artifacts",
            "actor": dict(ctx.actor),
            "job_id": ctx.job_id,
            "artifact_lineage": "app.job_artifact+app.rbfe_campaign_artifact",
            "cancellation_boundary": "before_scientific_side_effects",
        },
        warnings=[{
            "code": "PREPARATION_CANCELLATION_BOUNDARY",
            "message": (
                "A cancellation request is guaranteed before preparation starts; "
                "an active native preparation phase may finish and publish its "
                "durable campaign generation."
            ),
            "affects": ["job.cancellation"],
        }],
    )


def prepare_campaign_estimate(payload: dict[str, Any]) -> dict[str, Any]:
    """Conservative admission estimate; the native tools retain their own cap."""
    compounds = len(payload.get("compounds") or ())
    receptor_bytes = len(str(payload.get("receptor_pdb") or "").encode())
    return {
        "available": True,
        "resource_class": "cpu",
        "estimated_seconds": max(15.0, compounds * 8.0 + receptor_bytes / 50_000),
        "estimated_peak_memory_bytes": max(
            1 << 30, receptor_bytes * 32 + compounds * (64 << 20)),
        "checkpointable": False,
        "cancellation": "pre_start_guaranteed_active_phase_may_commit",
    }
