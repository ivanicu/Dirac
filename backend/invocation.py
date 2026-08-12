"""The InvocationService: one place where "run a method" means something.

WHAT IS SCATTERED TODAY, which is the audit's finding stated as a list of files: to
invoke `fields.qm.homo` the system must validate parameters, consult a cache, open a
job row, acquire a concurrency slot, run science, register artifacts, normalise
metadata, close the job and shape a response. Every one of those steps exists and
works — inside a 200-line HTTP handler. So the knowledge of what an invocation IS can
only be reached by speaking HTTP, which is why a CLI would have to be an HTTP client
and an MCP adapter would have to spawn a CLI.

This module is that knowledge with the HTTP taken out. The transports become adapters:

    HTTP /v2/invoke   parse a request  → service.invoke() → serialise the envelope
    CLI               parse argv       → service.invoke() → print JSON or a table
    Python SDK        typed kwargs     → service.invoke() (in-process or over HTTP)
    MCP               a tool call      → service.invoke() → a tool result

WHAT AN INVOCATION RETURNS, and every field of it is load-bearing for a client that
is not a human looking at a screen:

    method_id + version    WHICH SOURCE RAN. The version is the registry's digest, so
                           two results with the same version came from identical code.
    result                 the scientific answer, shaped by the descriptor's output
                           schema
    artifacts[]            REFERENCES, with the bytes inline only when small enough
    provenance             typed, not prose: toolkit versions, cache status, seconds,
                           the job id, the parameters ACTUALLY used
    warnings[]             typed caveats a client can render or ignore per code

DELIBERATELY NOT A REWRITE. The science is called through the existing functions, and
the existing HTTP route is untouched by this file. Both paths are then run against the
same input and their cube bytes compared — because the only safe way to move a
200-line orchestration is to have two of them agreeing first, and the parity harness
is what makes deleting the old one a measurement rather than an act of faith.

DEPENDENCY DIRECTION (ADR-001, gate 11): stdlib + failures + artifacts + catalog +
envelope. No HTTP, no psycopg, no RDKit, no pyscf. The store, the ledger and the cache
arrive as INJECTED OBJECTS, so an offline CLI passes a MemoryArtifactStore and no
ledger and gets a working invocation with honest provenance saying so.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

import artifacts as A
import catalog as C
import failures


@dataclass
class InvocationContext:
    """What a handler is allowed to know about the world it runs in.

    Narrow on purpose. A handler that could reach the HTTP request, the socket or the
    database connection would immediately start using them, and the next transport
    would have to fake an HTTP request to call the science. Everything here is either
    a value or an injected capability with a no-op fallback.
    """

    method_id: str
    version: str | None = None
    budget_seconds: float | None = None
    job_id: str | None = None
    # THE CONTRACT ITSELF, handed to the handler. Not a convenience: without it a handler
    # has to hard-code facts the descriptor already states — and the first version of
    # field_handler did exactly that, carrying a `_UNITS` dict that disagreed with the
    # `native_units` const in four of six descriptors. Nobody noticed until output
    # validation landed, because six transports shared the one wrong dict and therefore
    # agreed perfectly. A handler that READS the contract cannot drift from it.
    spec: Any = None
    # A handler reports progress by calling this. Default is a sink, so a handler
    # never has to check whether anyone is listening.
    on_progress: Callable[[str, float], None] = lambda stage, frac: None
    deadline: float | None = None

    def check_budget(self) -> None:
        """A handler calls this at its own checkpoints.

        The service cannot interrupt a running SCF from outside — pyscf is in C — so a
        deadline is only real where the handler cooperates. Making that explicit is
        better than a timeout the architecture cannot honour: an uncooperative handler
        overruns visibly here rather than silently everywhere.
        """
        if self.deadline is not None and time.time() > self.deadline:
            raise failures.DiracBudgetExceeded(
                f'{self.method_id} exceeded its {self.budget_seconds}s budget',
                details={'budget_seconds': self.budget_seconds})


@dataclass
class HandlerResult:
    """What a handler hands back. Bytes here; addresses are the service's job.

    A handler that registered its own artifacts would need the store, and then a
    handler could not be tested without one. It returns bytes and roles; the service
    stores them, applies the inline decision and builds the references.
    """

    result: dict[str, Any]
    artifacts: list[tuple[str, bytes]] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    parameters_used: dict[str, Any] = field(default_factory=dict)
    cache: str = 'computed'
    # Private producer material for an injected cache writer. It is deliberately
    # outside the envelope: clients may depend only on the validated public result.
    cache_record: dict[str, Any] | None = None


class Ledger(Protocol):
    def open(self, **kw) -> tuple[str | None, bool]: ...
    def done(self, job_id: str, **kw) -> None: ...
    def failed(self, job_id: str, **kw) -> None: ...


class InvocationService:
    """`invoke(method_id, payload)` — the only entry point any transport needs.

    Every collaborator is optional. With none of them this is still a correct
    invocation service: it validates, runs, addresses artifacts in memory and returns
    honest provenance recording that nothing was cached and no job was recorded. That
    is not a degraded mode for tests — it is the mode an offline CLI runs in, and the
    fact that the same code path serves both is why the acceptance test can compare
    them.
    """

    def __init__(self, catalog: C.MethodCatalog, *,
                 store: Any | None = None,
                 ledger: Any | None = None,
                 cache: Any | None = None,
                 toolkit_versions: dict[str, str] | None = None) -> None:
        self.catalog = catalog
        self.store = store or A.MemoryArtifactStore()
        self.ledger = ledger
        self.cache = cache
        self.toolkit_versions = toolkit_versions or {}
        self.counters = {'invoked': 0, 'cache_hit': 0, 'refused': 0, 'failed': 0,
                         'artifacts_registered': 0}

    # ── the read side, which needs no science and no database ─────────────────
    def list_methods(self, *, surface: str | None = None) -> list[dict]:
        specs = self.catalog.all()
        if surface:
            specs = [s for s in specs if s.exposure.get(surface)]
        return [{'method_id': s.method_id, 'version': s.version,
                 'summary': s.summary, 'executable': s.is_executable,
                 'cacheable': s.cacheable, 'artifacts': [a.role for a in s.artifacts]}
                for s in specs]

    def describe(self, method_id: str) -> dict:
        return self.catalog.describe(method_id)

    def estimate(self, method_id: str, payload: dict) -> dict:
        """What this would cost, WITHOUT running it.

        The reason a budget refusal can arrive in milliseconds instead of after ninety
        seconds of SCF, and the reason an agent can decide not to ask. Returns
        `available: false` rather than a guessed number when the method declares no
        estimator — a fabricated cost is worse than none, because a client would
        plan against it.
        """
        spec = self.catalog.get(method_id)
        self.catalog.validate(method_id, payload)
        est = spec.estimator()
        if est is None:
            return {'available': False, 'method_id': method_id,
                    'reason': 'this method declares no invocation.estimate; its cost '
                              'is not predictable without running it'}
        out = dict(est(payload) or {})
        out.setdefault('available', True)
        out['method_id'] = method_id
        out['version'] = spec.version
        return out

    # ── the write side ────────────────────────────────────────────────────────
    def invoke(self, method_id: str, payload: dict, *,
               inline_max: int | None = None,
               budget_seconds: float | None = None,
               request_id: str | None = None) -> dict:
        """Run a method and return a v2 envelope. Never raises for a REFUSAL.

        A refusal is a RESULT — the molecule is too large, the basis does not cover
        iodine — and a transport that had to catch an exception to learn that would
        have to decide, itself, whether the exception was a refusal or a bug. That
        decision is exactly the `isinstance(e, ValueError)` guess PR-03 deleted, and
        putting it back in every adapter would be the same defect three times.

        A BUG still raises: DiracInternal comes back as an envelope too, but the
        counters and the ledger record it as failed, because a system that reports its
        own faults as the caller's fault is the one failure mode with no recovery.
        """
        t0 = time.time()
        self.counters['invoked'] += 1
        job_id = None
        spec = None
        try:
            spec = self.catalog.get(method_id)
            self.catalog.validate(method_id, payload)
            handler = spec.handler()

            budget = budget_seconds
            if budget is None:
                budget = float(
                    (payload.get('parameters') or {}).get('max_seconds')
                    or spec.execution.get('default_budget_seconds') or 0.0) or None

            # The cache is consulted BEFORE the budget, exactly as the HTTP path
            # does, and for the same reason written down there: no work is done on a
            # hit, so there is no budget to refuse against, and refusing would mean
            # withholding a free answer.
            hit = None
            if spec.cacheable and self.cache is not None:
                hit = self.cache.lookup(method_id, payload)
            if hit is not None:
                self.counters['cache_hit'] += 1
                # VALIDATED, exactly like a computed result. Written without this first, and
                # the very first hit returned native_units 'amp' where the descriptor declares
                # 'amplitude' — undetected, because the contract was being enforced only on
                # the COMPUTED path while cache hits are the majority of responses. A rule
                # that applies to the minority of answers is not a rule.
                self.catalog.validate_output(method_id, hit.result)
                return self._envelope(spec, hit, t0, cache='db',
                                      inline_max=inline_max, request_id=request_id,
                                      job_id=None)

            if self.ledger is not None:
                job_id = self._open_job(spec, payload, budget)

            ctx = InvocationContext(
                method_id=method_id, version=spec.version, budget_seconds=budget,
                job_id=job_id, spec=spec,
                deadline=(t0 + budget) if budget else None)
            out = handler(payload, ctx)
            if not isinstance(out, HandlerResult):
                raise failures.DiracInternal(
                    f'{method_id}: its handler returned {type(out).__name__}, not a '
                    f'HandlerResult. A handler that returns an untyped dict makes the '
                    f'service guess which key is the result and which is provenance, '
                    f'and every transport would guess differently.')
            self._require_declared_artifacts(spec, out)
            # THE CONTRACT, ENFORCED IN BOTH DIRECTIONS. Until now only inputs were
            # validated, so a handler could quietly return a shape no client was told to
            # expect — and the only symptom was a renderer showing `undefined`.
            self.catalog.validate_output(method_id, out.result)
            # Persistence follows validation and never gates a valid scientific result.
            # The collaborator owns whether the write is queued or synchronous.
            if spec.cacheable and self.cache is not None and hasattr(self.cache, 'store'):
                try:
                    self.cache.store(method_id, payload, out,
                                     seconds=round(time.time() - t0, 3), job_id=job_id)
                except Exception as e:                              # noqa: BLE001
                    print(f'[invoke] cache write unavailable ({type(e).__name__}: {e}) — '
                          f'the result is valid but was not persisted', file=sys.stderr,
                          flush=True)
            env = self._envelope(spec, out, t0, cache=out.cache,
                                 inline_max=inline_max, request_id=request_id,
                                 job_id=job_id)
            if job_id is not None and self.ledger is not None:
                self.ledger.done(job_id, seconds=round(time.time() - t0, 3))
            return env

        except failures.DiracFailure as f:
            is_bug = f.code == 'INTERNAL'
            self.counters['failed' if is_bug else 'refused'] += 1
            if job_id is not None and self.ledger is not None:
                try:
                    self.ledger.failed(job_id, code=f.code, detail=f.message,
                                       retryable=f.retryable)
                except Exception:                                  # noqa: BLE001
                    pass          # the refusal is the answer; a ledger write is not
            return {
                'ok': False,
                'error': f.to_error_payload(),
                'meta': {'envelope': 2, 'method_id': method_id,
                         'version': spec.version if spec else None,
                         'seconds': round(time.time() - t0, 3),
                         'request_id': request_id, 'job_id': job_id},
            }
        except Exception as e:                                      # noqa: BLE001
            # An untyped escape is OUR fault by definition: everything the science is
            # allowed to refuse for has a code. Converted here rather than left to the
            # transport, so all four transports report a bug identically.
            self.counters['failed'] += 1
            f = failures.DiracInternal(e)
            if job_id is not None and self.ledger is not None:
                try:
                    self.ledger.failed(job_id, code='INTERNAL', detail=str(e),
                                       retryable=True)
                except Exception:                                  # noqa: BLE001
                    pass
            return {'ok': False, 'error': f.to_error_payload(),
                    'meta': {'envelope': 2, 'method_id': method_id,
                             'version': spec.version if spec else None,
                             'seconds': round(time.time() - t0, 3),
                             'request_id': request_id, 'job_id': job_id}}

    # ── internals ─────────────────────────────────────────────────────────────
    def _open_job(self, spec: C.MethodSpec, payload: dict,
                  budget: float | None) -> str | None:
        """Open a ledger row, and never let the ledger break the invocation.

        A job row is OBSERVABILITY. If the database is down, the science must still
        run — the daemon already treats the cache that way — and the provenance must
        say `job_id: null` rather than imply a row exists.
        """
        try:
            import hashlib
            import json as _json
            canonical = _json.dumps(payload, sort_keys=True, separators=(',', ':'))
            job_id, _conflict = self.ledger.open(
                method_row_id=getattr(self.ledger, 'method_row_for',
                                      lambda _m: None)(spec.method_id),
                input_sha256=hashlib.sha256(canonical.encode()).digest(),
                params={'method_id': spec.method_id},
                budget_seconds=budget, queued=False)
            return job_id
        except Exception as e:                                     # noqa: BLE001
            print(f'[invoke] ledger unavailable ({type(e).__name__}: {e}) — running '
                  f'anyway, and provenance will say job_id: null', file=sys.stderr,
                  flush=True)
            return None

    def _require_declared_artifacts(self, spec: C.MethodSpec,
                                    out: HandlerResult) -> None:
        """A descriptor that promises an artifact and a handler that returns none is a
        broken contract, and it must fail HERE rather than produce a response whose
        `artifacts: []` a client reads as "this method produces nothing"."""
        produced = {role for role, _ in out.artifacts}
        missing = [a.role for a in spec.artifacts if a.required
                   and a.role not in produced]
        if missing:
            raise failures.DiracInternal(
                f'{spec.method_id} declares required artifact(s) {missing} and its '
                f'handler produced {sorted(produced) or "none"}. A client that read '
                f'the descriptor is waiting for a reference that will never come.')
        undeclared = produced - {a.role for a in spec.artifacts}
        if undeclared:
            # Not fatal — an extra artifact harms nobody — but it is a contract drift
            # that must be visible, because the descriptor is what agents plan from.
            out.warnings.append({
                'code': 'UNDECLARED_ARTIFACT',
                'message': f'produced {sorted(undeclared)}, which the descriptor does '
                           f'not declare; a client planning from the contract will '
                           f'not know to look for it'})

    def _envelope(self, spec: C.MethodSpec, out: HandlerResult, t0: float, *,
                  cache: str, inline_max: int | None, request_id: str | None,
                  job_id: str | None) -> dict:
        refs = []
        for role, data in out.artifacts:
            declared = next((a for a in spec.artifacts if a.role == role), None)
            art = self.store.put(
                data, role=role,
                media_type=declared.media_type if declared else None,
                method_version=spec.version)
            self.counters['artifacts_registered'] += 1
            if job_id and hasattr(self.store, 'link_to_job'):
                try:
                    self.store.link_to_job(job_id, art.id, role)
                except Exception:                                  # noqa: BLE001
                    pass
            inline = data if A.should_inline(len(data),
                                            requested_max=inline_max) else None
            refs.append(art.to_reference(inline=inline))

        return {
            'ok': True,
            'data': out.result,
            'artifacts': refs,
            'warnings': out.warnings,
            'meta': {
                'envelope': 2,
                'method_id': spec.method_id,
                'version': spec.version,
                'cache': cache,
                'seconds': round(time.time() - t0, 3),
                'request_id': request_id,
                'job_id': job_id,
                'parameters_used': out.parameters_used,
                'toolkits': self.toolkit_versions,
                'provenance': out.provenance,
            },
        }
