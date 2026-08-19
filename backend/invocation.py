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
import inspect
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

import artifacts as A
import catalog as C
import execution as E
import failures
from execution_control.protocol import CancellationToken
from execution_control.identity import (
    EXECUTOR_ADAPTERS,
    ExecutionIdentity,
    sha256_digest,
)


_RESOURCE_CLASSES = frozenset({
    'cpu', 'cpu-classical', 'cpu-cheminformatics', 'cpu-qm', 'gpu', 'external-api',
})


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
    execution_digest: str | None = None
    execution_adapter: str | None = None
    execution_identity: ExecutionIdentity | None = None
    actor: dict[str, str] | None = None
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
    # Narrow data-plane capabilities.  New Motif handlers receive only the
    # Artifact IDs authorized by their ExecutionRequest; they never receive a
    # database connection or arbitrary filesystem path.
    artifact_reader: Any = None
    artifact_writer: Any = None
    checkpoint_writer: Any = None
    rbfe_reference_resolver: Any = None
    ai_provider_registry: Any = None
    # Server-owned admission facts are outside the client payload. Remote
    # executors seal them into their fenced input manifest after resolving
    # capabilities that intentionally do not cross the worker sandbox.
    server_attestations: dict[str, Any] = field(default_factory=dict)
    cancellation_token: CancellationToken = field(default_factory=CancellationToken)
    dispatch_fence: Callable[[Any | None], None] | None = None

    def check_budget(self) -> None:
        """A handler calls this at its own checkpoints.

        The service cannot interrupt a running SCF from outside — pyscf is in C — so a
        deadline is only real where the handler cooperates. Making that explicit is
        better than a timeout the architecture cannot honour: an uncooperative handler
        overruns visibly here rather than silently everywhere.
        """
        self.cancellation_token.check()
        if self.deadline is not None and time.time() > self.deadline:
            raise failures.DiracBudgetExceeded(
                f'{self.method_id} exceeded its {self.budget_seconds}s budget',
                details={'budget_seconds': self.budget_seconds})

    def assert_dispatch(self, cursor: Any | None = None) -> None:
        if self.dispatch_fence is not None:
            self.dispatch_fence(cursor)


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
    # Execution-control ownership is not public scientific provenance. A remote
    # executor hands the fenced claim to InvocationService so artifact persistence
    # and terminal publication can share the authoritative commit barrier.
    attempt_claim: Any | None = None


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
                 executor: Any | None = None,
                 trace_store: Any | None = None,
                 artifact_reader: Any | None = None,
                 artifact_writer: Any | None = None,
                 checkpoint_writer: Any | None = None,
                 rbfe_reference_resolver: Any | None = None,
                 ai_provider_registry: Any | None = None,
                 attempt_store: Any | None = None,
                 motif_governance: Any | None = None,
                 program_repository: Any | None = None,
                 execution_identity_resolver: Callable[[C.MethodSpec, dict], ExecutionIdentity] | None = None,
                 production_execution: bool = False,
                 toolkit_versions: dict[str, str] | None = None) -> None:
        self.catalog = catalog
        self.store = store or A.MemoryArtifactStore()
        self.ledger = ledger
        self.cache = cache
        self.executor = executor or E.InlineExecutor()
        self.command_traces = trace_store
        self.artifact_reader = artifact_reader
        self.artifact_writer = artifact_writer
        self.checkpoint_writer = checkpoint_writer
        self.rbfe_reference_resolver = rbfe_reference_resolver
        self.ai_provider_registry = ai_provider_registry
        self.attempt_store = attempt_store
        self.motif_governance = motif_governance
        self.program_repository = program_repository
        self.execution_identity_resolver = execution_identity_resolver
        self.production_execution = production_execution
        self.toolkit_versions = toolkit_versions or {}
        self._futures: dict[str, Any] = {}
        self._cancellation_tokens: dict[str, CancellationToken] = {}
        self._job_execution_adapters: dict[str, str] = {}
        self._futures_lock = threading.Lock()
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

    # ── job control surface ─────────────────────────────────────────────────
    def capabilities(self) -> dict:
        executor_adapter = self._executor_adapter()
        probe = getattr(self.executor, 'capabilities', None)
        executor_health: dict[str, Any] = {}
        if callable(probe):
            try:
                executor_health = dict(probe())
            except Exception as error:  # noqa: BLE001 - readiness fails closed
                executor_health = {
                    'protocol_valid': False,
                    'scheduler_healthy': False,
                    'gpu_execution': False,
                    'health_error': f'{type(error).__name__}: {error}',
                }
        gpu_execution = bool(executor_health.get('gpu_execution', False))
        ai_profiles: list[dict[str, Any]] = []
        ai_health = 'unconfigured'
        if self.ai_provider_registry is not None:
            try:
                ai_profiles = list(self.ai_provider_registry.list_public())
                ai_health = ('configured' if any(
                    item.get('configured') for item in ai_profiles) else 'degraded')
            except Exception:  # noqa: BLE001 - AI cannot take down scientific health
                ai_health = 'degraded'
        return {
            'job_store': {
                'kind': getattr(self.ledger, 'kind', 'none') if self.ledger else 'none',
                'durability': (getattr(self.ledger, 'durability', 'none')
                               if self.ledger else 'none'),
            },
            'artifact_store': {
                'kind': getattr(self, 'store_kind', type(self.store).__name__),
                'durability': ('durable' if getattr(self, 'store_kind', '') == 'postgres'
                               else 'process'),
            },
            'executor': {
                'kind': getattr(self.executor, 'kind', 'unknown'),
                'adapter': executor_adapter,
                # An adapter name is routing metadata, not evidence that its
                # protocol, scheduler, inventory, or worker is usable.
                'gpu_execution': gpu_execution,
                'readiness': executor_health,
            },
            'command_traces': {
                'kind': getattr(self.command_traces, 'kind', 'none'),
                'durability': getattr(self.command_traces, 'durability', 'none'),
            },
            'motif_governance': {
                'kind': getattr(self.motif_governance, 'kind', 'none'),
                'durability': getattr(self.motif_governance, 'durability', 'none'),
            },
            'program_repository': {
                'kind': getattr(self.program_repository, 'kind', 'none'),
                'durability': getattr(self.program_repository, 'durability', 'none'),
            },
            'ai_reasoning': {
                'configured': ai_health == 'configured',
                'state': ai_health,
                'profiles': ai_profiles,
            },
            'research_loop': {
                'ready': getattr(self, 'research_loop_controller', None) is not None,
                'durability': getattr(
                    getattr(self, 'research_loop_controller', None),
                    'durability', 'unavailable'),
                'provider_configured': ai_health == 'configured',
                'physical_fep_execution': gpu_execution,
            },
            'cancellation': ('route-specific' if callable(getattr(
                self.executor, 'cancellation_capability_for', None))
                else 'queued-only'),
        }

    def _executor_adapter(self, spec: C.MethodSpec | None = None) -> str:
        """Return the contract adapter represented by the injected executor.

        A generic ``RemoteExecutor`` is only a callback boundary; it is not proof
        that Kubernetes or Slurm is actually connected.  Deployments therefore
        have to set ``adapter_kind`` explicitly on a remote executor before a
        method that requires that adapter may run.
        """
        route = getattr(self.executor, 'execution_adapter_for', None)
        if spec is not None and callable(route):
            routed = str(route(spec) or '').strip()
            if not routed:
                return 'unconfigured'
            return routed
        explicit = getattr(self.executor, 'adapter_kind', None)
        if explicit:
            return str(explicit)
        return {
            'inline': 'inline',
            'thread': 'local_cpu',
            'process': 'local_cpu',
        }.get(getattr(self.executor, 'kind', 'unknown'), 'unconfigured')

    def _require_executor_compatibility(
            self, spec: C.MethodSpec, *, execution_adapter: str | None = None) -> str:
        """Fail closed when a Method would escape its declared adapter set."""
        adapter = execution_adapter or self._executor_adapter(spec)
        resource_class = str(spec.execution.get('resource_class') or '')
        supported = tuple(spec.execution.get('supported_adapters') or ())
        reason = None
        if resource_class not in _RESOURCE_CLASSES:
            reason = 'missing_or_unknown_resource_class'
        elif adapter not in EXECUTOR_ADAPTERS:
            reason = 'missing_or_unknown_executor_adapter'
        elif not supported:
            reason = 'missing_supported_adapters'
        elif any(item not in EXECUTOR_ADAPTERS for item in supported):
            reason = 'unknown_supported_adapter'
        elif adapter not in supported:
            reason = 'adapter_not_supported'
        if reason is not None:
            raise failures.DiracUnsupported(
                f'{spec.method_id} cannot run through execution adapter '
                f'{adapter!r}',
                details={
                    'method_id': spec.method_id,
                    'resource_class': resource_class,
                    'executor_kind': getattr(self.executor, 'kind', 'unknown'),
                    'executor_adapter': adapter,
                    'supported_adapters': list(supported),
                    'reason': reason,
                    'recovery': ('configure one of the Method contract adapters; '
                                 'the API will not silently change execution route'),
                })
        return adapter

    def _cancellation_capability(
            self, spec: C.MethodSpec | None, execution_adapter: str | None) -> str:
        if spec is None or execution_adapter not in EXECUTOR_ADAPTERS:
            return 'queued-only'
        resolver = getattr(self.executor, 'cancellation_capability_for', None)
        if not callable(resolver):
            return 'queued-only'
        try:
            capability = str(resolver(
                spec, execution_adapter=execution_adapter) or 'queued-only')
        except Exception:  # noqa: BLE001 - cancellation claims fail closed
            return 'queued-only'
        return capability if capability in {
            'queued-only', 'cooperative', 'cooperative+remote-hard'
        } else 'queued-only'

    def list_jobs(self, *, actor: dict[str, str] | None = None,
                  state: str | None = None, limit: int = 100) -> list[dict]:
        if self.ledger is None or not hasattr(self.ledger, 'list'):
            return []
        principal = self._actor(actor)
        return self.ledger.list(
            actor_kind=principal['kind'], actor_id=principal['id'],
            state=state, limit=limit)

    def list_attention(self, *, actor: dict[str, str] | None = None,
                       limit: int = 100) -> list[dict]:
        if self.ledger is None or not hasattr(self.ledger, 'list_attention'):
            return []
        principal = self._actor(actor)
        return self.ledger.list_attention(
            actor_kind=principal['kind'], actor_id=principal['id'], limit=limit)

    def get_job(self, job_id: str, *,
                actor: dict[str, str] | None = None) -> dict:
        principal = self._actor(actor)
        row = self.ledger.get(
            job_id, actor_kind=principal['kind'], actor_id=principal['id']) \
            if self.ledger is not None and hasattr(self.ledger, 'get') else None
        if row is None:
            raise failures.DiracNotFound(
                f'no job {job_id!r}', details={'job_id': job_id})
        return row

    def cancel_job(self, job_id: str, *,
                   actor: dict[str, str] | None = None) -> dict:
        # Ownership is checked before touching the in-memory cancellation token.
        # Otherwise an unauthorized caller could stop another principal's work
        # even though the durable operation later returned NOT_FOUND.
        principal = self._actor(actor)
        row = self.ledger.request_cancel(
            job_id, actor_kind=principal['kind'], actor_id=principal['id']) \
            if self.ledger is not None and hasattr(
                self.ledger, 'request_cancel') else None
        if row is None:
            raise failures.DiracNotFound(
                f'no job {job_id!r}', details={'job_id': job_id})
        with self._futures_lock:
            future = self._futures.get(job_id)
            token = self._cancellation_tokens.get(job_id)
            execution_adapter = self._job_execution_adapters.get(job_id)
        if future is not None and not future.running() and not future.done():
            future.cancel()
        spec = None
        method_id = row.get('method_id')
        if isinstance(method_id, str):
            try:
                spec = self.catalog.get(method_id)
            except failures.DiracFailure:
                spec = None
        if execution_adapter is None and spec is not None:
            # Recovery after a controller restart has no in-memory admission
            # record. The immutable descriptor can reconstruct the route, but a
            # custom/global executor capability is never trusted as a substitute.
            execution_adapter = self._executor_adapter(spec)
        capability = self._cancellation_capability(spec, execution_adapter)
        if token is not None and capability != 'queued-only':
            token.request('job.cancel command')
            row['cancel'] = {
                'requested': True,
                'accepted': True,
                'capability': capability,
                'terminal_pending': row.get('state') not in (
                    'done', 'failed', 'cancelled'),
            }
        return row

    def wait_job(self, job_id: str, *, actor: dict[str, str] | None = None,
                 timeout: float = 300.0,
                 poll: float = 0.1) -> dict:
        """Wait for a handle without coupling the caller to executor internals."""
        deadline = time.time() + max(0.0, float(timeout))
        while True:
            row = self.get_job(job_id, actor=actor)
            if row.get('state') in ('done', 'failed', 'cancelled'):
                return row
            if time.time() >= deadline:
                row['wait'] = {'timed_out': True, 'timeout_seconds': float(timeout)}
                return row
            time.sleep(max(0.01, float(poll)))

    def submit(self, method_id: str, payload: dict, *,
               inline_max: int | None = None,
               budget_seconds: float | None = None,
               request_id: str | None = None,
               actor: dict[str, str] | None = None,
        command_id: str | None = None) -> dict:
        """Create a reconnectable queued Job and execute it on the injected executor."""
        payload = self._snapshot_payload(payload)
        spec = self.catalog.get(method_id)
        self.catalog.validate(method_id, payload)
        execution_adapter = self._require_executor_compatibility(spec)
        handler = spec.handler()  # fail before minting a handle that can never run
        execution_identity = self._execution_identity(
            spec, payload, handler, execution_adapter=execution_adapter)
        execution_request_digest = execution_identity.cache_key(
            payload, seed_scope_digest=None)
        if 'job' not in spec.execution.get('supported_modes', []):
            raise failures.DiracUnsupported(
                f'{method_id} does not declare job execution',
                details={'method_id': method_id,
                         'supported_modes': spec.execution.get('supported_modes', [])})
        if self.ledger is None:
            raise failures.DiracInternal(
                'job submission requires a JobStore; this kernel has none')
        if not getattr(self.executor, 'supports_submission', False):
            raise failures.DiracInternal(
                'job submission requires an executor with submit(); '
                f'got {getattr(self.executor, "kind", type(self.executor).__name__)}')
        budget = budget_seconds
        if budget is None:
            budget = float((payload.get('parameters') or {}).get('max_seconds')
                           or spec.execution.get('default_budget_seconds') or 0.0) or None
        actor_ref = self._actor(actor)
        request_key = payload.get('request_key')
        if request_key is not None:
            if command_id is None:
                raise failures.DiracInvalidParameters(
                    'request_key is valid only through a registered command',
                    details={'method_id': method_id,
                             'request_key': request_key,
                             'required_endpoint': '/v2/execute'})
            request_key = str(request_key)
            if (self.production_execution
                    and getattr(self.ledger, 'durability', 'none') != 'durable'):
                raise failures.DiracFailure(
                    'DB_UNAVAILABLE',
                    'production request-key admission requires a durable JobStore',
                    details={
                        'command_id': command_id,
                        'request_key': request_key,
                        'job_durability': getattr(
                            self.ledger, 'durability', 'none'),
                        'required_migration':
                            '046_job_command_request_key.sql',
                    })
        job_id, conflicted = self._open_job(
            spec, payload, budget, queued=True, actor=actor_ref,
            command_id=command_id, request_id=request_id,
            execution_identity=execution_identity,
            request_key=request_key)
        if job_id is None:
            raise failures.DiracInternal(
                'the JobStore could not create or resolve a durable job handle')
        dispatch_claim = None
        if request_key is not None:
            claim_dispatch = getattr(self.ledger, 'claim_dispatch', None)
            if not callable(claim_dispatch):
                raise failures.DiracFailure(
                    'DB_UNAVAILABLE',
                    'request-key JobStore has no durable dispatch fencing',
                    details={'required_migration':
                             '047_job_dispatch_fence.sql'})
            dispatch_claim = claim_dispatch(
                job_id,
                bytes.fromhex(execution_request_digest.removeprefix('sha256:')))
        if not conflicted or dispatch_claim is not None:
            cancellation_token = CancellationToken()
            try:
                future = self.executor.submit(
                    self._run_submitted, job_id, method_id, payload,
                    inline_max, budget, request_id, actor_ref, command_id,
                    cancellation_token, execution_identity,
                    execution_request_digest, execution_adapter, dispatch_claim)
                if dispatch_claim is not None:
                    self.ledger.mark_dispatch_submitted(dispatch_claim)
            except Exception:
                if dispatch_claim is not None:
                    self.ledger.release_dispatch(dispatch_claim)
                raise
            with self._futures_lock:
                self._futures[job_id] = future
                self._cancellation_tokens[job_id] = cancellation_token
                self._job_execution_adapters[job_id] = execution_adapter
            future.add_done_callback(lambda _f, jid=job_id: self._forget_future(jid))
        data = {'job': self.get_job(job_id, actor=actor_ref)}
        if request_key is not None:
            data['request_key'] = request_key
        return {
            'ok': True,
            'data': data,
            'artifacts': [], 'warnings': [],
            'meta': {'envelope': 2, 'method_id': method_id,
                     'version': spec.version, 'job_id': job_id,
                     'execution_digest': execution_identity.digest,
                     'execution_mode': 'job', 'deduplicated': conflicted,
                     'request_id': request_id, 'actor': actor_ref,
                     'command': command_id,
                     **({'request_key': request_key}
                        if request_key is not None else {})},
        }

    def _run_submitted(self, job_id: str, method_id: str, payload: dict,
                       inline_max: int | None, budget_seconds: float | None,
                       request_id: str | None, actor: dict[str, str],
                       command_id: str | None,
                       cancellation_token: CancellationToken,
                       execution_identity: ExecutionIdentity,
                       execution_request_digest: str,
                       execution_adapter: str,
                       dispatch_claim: Any | None = None) -> None:
        row = self.ledger.get(
            job_id, actor_kind=actor['kind'], actor_id=actor['id'])
        if row is None or row.get('state') == 'cancelled':
            return
        if dispatch_claim is not None:
            try:
                dispatch_claim = self.ledger.start_dispatch(dispatch_claim)
            except Exception:
                return
        else:
            self.ledger.start(job_id)
        heartbeat_stop = threading.Event()
        heartbeat = None
        if dispatch_claim is not None:
            def beat() -> None:
                while not heartbeat_stop.wait(20.0):
                    try:
                        self.ledger.renew_dispatch(dispatch_claim)
                    except Exception:
                        return
            heartbeat = threading.Thread(
                target=beat, name=f'dispatch-heartbeat-{job_id}', daemon=True)
            heartbeat.start()
        try:
            self.invoke(method_id, payload, inline_max=inline_max,
                        budget_seconds=budget_seconds, request_id=request_id,
                        actor=actor, command_id=command_id,
                        _preopened_job_id=job_id,
                        _cancellation_token=cancellation_token,
                        _precomputed_execution_identity=execution_identity,
                        _preopened_request_digest=execution_request_digest,
                        _precomputed_execution_adapter=execution_adapter,
                        _dispatch_claim=dispatch_claim)
        finally:
            heartbeat_stop.set()
            if heartbeat is not None:
                heartbeat.join(timeout=1.0)

    def recover_keyed_dispatches(self, *, limit: int = 100) -> int:
        """Recover only pending, expired, or proven-dead local dispatches."""
        if self.ledger is None or not hasattr(
                self.ledger, 'list_recoverable_dispatches'):
            return 0
        import jobs as J
        recovered = 0
        for record in self.ledger.list_recoverable_dispatches(limit=limit):
            if record.get('execution_adapter') != 'local_cpu':
                continue
            owner = record.get('lease_owner')
            recover_owner = None
            if (record.get('dispatch_state') != 'pending'
                    and not bool(record.get('lease_expired'))):
                if owner and J._worker_is_alive(str(owner)):
                    continue
                recover_owner = str(owner) if owner else None
            payload = self._snapshot_payload(record['payload'])
            spec = self.catalog.get(str(record['method_id']))
            self.catalog.validate(spec.method_id, payload)
            adapter = self._require_executor_compatibility(spec)
            handler = spec.handler()
            identity = self._execution_identity(
                spec, payload, handler, execution_adapter=adapter)
            digest = identity.cache_key(payload, seed_scope_digest=None)
            stored = str(record['execution_digest']).removeprefix('sha256:')
            if digest.removeprefix('sha256:') != stored:
                continue
            claim = self.ledger.claim_dispatch(
                str(record['job_id']), bytes.fromhex(stored),
                recover_owner=recover_owner)
            if claim is None:
                continue
            token = CancellationToken()
            try:
                future = self.executor.submit(
                    self._run_submitted, str(record['job_id']), spec.method_id,
                    payload, None, None, record.get('request_id'),
                    {'kind': record['actor_kind'], 'id': record['actor_id']},
                    record.get('command_id'), token, identity, digest, adapter,
                    claim)
                self.ledger.mark_dispatch_submitted(claim)
            except Exception:
                self.ledger.release_dispatch(claim)
                continue
            with self._futures_lock:
                self._futures[str(record['job_id'])] = future
                self._cancellation_tokens[str(record['job_id'])] = token
                self._job_execution_adapters[str(record['job_id'])] = adapter
            future.add_done_callback(
                lambda _f, jid=str(record['job_id']): self._forget_future(jid))
            recovered += 1
        return recovered

    def _forget_future(self, job_id: str) -> None:
        with self._futures_lock:
            self._futures.pop(job_id, None)
            self._cancellation_tokens.pop(job_id, None)
            self._job_execution_adapters.pop(job_id, None)

    # ── the write side ────────────────────────────────────────────────────────
    def invoke(self, method_id: str, payload: dict, *,
               inline_max: int | None = None,
               budget_seconds: float | None = None,
               request_id: str | None = None,
               actor: dict[str, str] | None = None,
               command_id: str | None = None,
               _preopened_job_id: str | None = None,
               _cancellation_token: CancellationToken | None = None,
               _precomputed_execution_identity: ExecutionIdentity | None = None,
               _preopened_request_digest: str | None = None,
               _precomputed_execution_adapter: str | None = None,
               _dispatch_claim: Any | None = None) -> dict:
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
        job_id = _preopened_job_id
        spec = None
        attempt_claim = None
        try:
            payload = self._snapshot_payload(payload)
            actor_ref = self._actor(actor)
            spec = self.catalog.get(method_id)
            self.catalog.validate(method_id, payload)
            supported_modes = set(spec.execution.get('supported_modes', []))
            execution_adapter = (_precomputed_execution_adapter
                                 or self._executor_adapter(spec))
            # A GPU Method reaching an in-process executor is the more dangerous
            # violation and must be reported even when the caller also chose the
            # wrong transport.  CPU job-only Methods, by contrast, first receive
            # the actionable durable-Job recovery path.
            gpu_method = spec.execution.get('resource_class') == 'gpu'
            if gpu_method:
                self._require_executor_compatibility(
                    spec, execution_adapter=execution_adapter)
            if _preopened_job_id is None and 'sync' not in supported_modes:
                raise failures.DiracUnsupported(
                    f'{method_id} is job-only and cannot run through synchronous invoke',
                    details={
                        'method_id': method_id,
                        'supported_modes': sorted(supported_modes),
                        'required_endpoint': '/v2/jobs',
                    })
            if _preopened_job_id is not None and 'job' not in supported_modes:
                raise failures.DiracInternal(
                    f'{method_id} was dispatched as a Job without declaring job execution')
            if not gpu_method:
                self._require_executor_compatibility(
                    spec, execution_adapter=execution_adapter)
            handler = spec.handler()
            execution_identity = (_precomputed_execution_identity
                                  or self._execution_identity(
                                      spec, payload, handler,
                                      execution_adapter=execution_adapter))
            if execution_identity.method_id != spec.method_id:
                raise failures.DiracInternal(
                    'precomputed execution identity belongs to another Method')
            if (execution_identity.executor_adapter is not None
                    and execution_identity.executor_adapter != execution_adapter):
                raise failures.DiracInternal(
                    'execution identity adapter differs from the admitted route')
            if self.production_execution and execution_identity.executor_adapter is None:
                raise failures.DiracInternal(
                    'production execution identity does not attest the admitted route')
            actual_request_digest = execution_identity.cache_key(
                payload, seed_scope_digest=None)
            if (_preopened_request_digest is not None
                    and actual_request_digest != _preopened_request_digest):
                raise failures.DiracInternal(
                    'queued Job payload no longer matches its admitted execution witness')

            budget = budget_seconds
            if budget is None:
                budget = float(
                    (payload.get('parameters') or {}).get('max_seconds')
                    or spec.execution.get('default_budget_seconds') or 0.0) or None

            # Server-owned admission is part of the API boundary, not worker
            # execution. It therefore precedes every cache read. A byte-identical
            # historical payload may no longer be admitted against the current
            # server generation even though an old result exists in cache.
            ctx = InvocationContext(
                method_id=method_id, version=spec.version,
                execution_digest=execution_identity.digest, actor=actor_ref,
                execution_adapter=execution_adapter,
                execution_identity=execution_identity,
                budget_seconds=budget,
                job_id=job_id, spec=spec,
                artifact_reader=self.artifact_reader,
                artifact_writer=self.artifact_writer,
                checkpoint_writer=self.checkpoint_writer,
                rbfe_reference_resolver=self.rbfe_reference_resolver,
                ai_provider_registry=self.ai_provider_registry,
                cancellation_token=_cancellation_token or CancellationToken(),
                dispatch_fence=(
                    (lambda cursor=None: self.ledger.assert_dispatch(
                        _dispatch_claim, cursor=cursor))
                    if _dispatch_claim is not None else None),
                deadline=(t0 + budget) if budget else None)
            admitted = self._run_server_admission(spec, payload, ctx)
            if admitted:
                # The resolver is a controller-only authority. Execution receives
                # only the sealed JSON witness, so a local handler and a remote
                # worker cannot repeat the DB check or introduce a second side effect.
                ctx.rbfe_reference_resolver = None

            # The cache is consulted BEFORE the budget, exactly as the HTTP path
            # does, and for the same reason written down there: no work is done on a
            # hit, so there is no budget to refuse against, and refusing would mean
            # withholding a free answer.
            hit = None
            if spec.cacheable and self.cache is not None:
                hit = self.cache.lookup(
                    method_id, payload, execution_digest=execution_identity.digest)
            if hit is not None:
                self.counters['cache_hit'] += 1
                hit = self._seal_server_attestations(hit, ctx)
                # VALIDATED, exactly like a computed result. Written without this first, and
                # the very first hit returned native_units 'amp' where the descriptor declares
                # 'amplitude' — undetected, because the contract was being enforced only on
                # the COMPUTED path while cache hits are the majority of responses. A rule
                # that applies to the minority of answers is not a rule.
                self.catalog.validate_output(method_id, hit.result)
                # Cache content identity is global; tenant ownership is not. Mint
                # (or join) a handle inside this principal's boundary before the
                # cached bytes are linked. The producer's Job ID is intentionally
                # absent from HandlerResult and is never reused here.
                if hit.artifacts and job_id is None and self.ledger is not None:
                    job_id, _ = self._open_job(
                        spec, payload, budget, actor=actor_ref,
                        command_id=command_id, request_id=request_id,
                        execution_identity=execution_identity)
                authorized_store = hasattr(self.store, 'read_authorized')
                if hit.artifacts and (self.production_execution or authorized_store):
                    durability = getattr(self.ledger, 'durability', 'none') \
                        if self.ledger is not None else 'none'
                    if (job_id is None or self.ledger is None
                            or (self.production_execution
                                and durability != 'durable')):
                        raise failures.DiracFailure(
                            'DB_UNAVAILABLE',
                            'a private cache-hit Artifact requires a current-actor '
                            'durable Job handle before it can be returned',
                            details={
                                'method_id': method_id,
                                'actor': actor_ref,
                                'job_durability': durability,
                                'cache': 'db',
                                'source_job_reused': False,
                            })
                env = self._envelope(spec, hit, t0, cache='db',
                                     inline_max=inline_max, request_id=request_id,
                                     job_id=job_id, actor=actor_ref,
                                     command_id=command_id,
                                     execution_identity=execution_identity)
                self._project_completion(spec, payload, hit, env, actor_ref, job_id)
                self.catalog.validate_output(method_id, hit.result)
                if job_id is not None and self.ledger is not None:
                    terminal = (self.ledger.done_claimed
                                if _dispatch_claim is not None
                                else self.ledger.done)
                    terminal(
                        _dispatch_claim if _dispatch_claim is not None else job_id,
                        seconds=round(time.time() - t0, 3),
                        result_summary={'ok': True, 'cache': 'db',
                                        'data': hit.result,
                                        'warnings': hit.warnings,
                                        'provenance': hit.provenance,
                                        'result_keys': sorted(hit.result),
                                        'artifact_roles': [r['role'] for r in env['artifacts']]})
                return env

            if self.ledger is not None and job_id is None:
                job_id, _ = self._open_job(
                    spec, payload, budget, actor=actor_ref,
                    command_id=command_id, request_id=request_id,
                    execution_identity=execution_identity)
            ctx.job_id = job_id
            # Handlers receive their own copy.  A legacy handler that mutates its
            # input cannot rewrite the request used for identity, cache, governance,
            # or the durable Job witness after admission.
            handler_payload = self._snapshot_payload(payload)
            out = self.executor.execute(handler, handler_payload, ctx)
            if not isinstance(out, HandlerResult):
                raise failures.DiracInternal(
                    f'{method_id}: its handler returned {type(out).__name__}, not a '
                    f'HandlerResult. A handler that returns an untyped dict makes the '
                    f'service guess which key is the result and which is provenance, '
                    f'and every transport would guess differently.')
            out = self._seal_server_attestations(out, ctx)
            attempt_claim = out.attempt_claim
            self._require_declared_artifacts(spec, out)
            # THE CONTRACT, ENFORCED IN BOTH DIRECTIONS. Until now only inputs were
            # validated, so a handler could quietly return a shape no client was told to
            # expect — and the only symptom was a renderer showing `undefined`.
            self.catalog.validate_output(method_id, out.result)
            env = self._envelope(spec, out, t0, cache=out.cache,
                                 inline_max=inline_max, request_id=request_id,
                                 job_id=job_id, actor=actor_ref,
                                 command_id=command_id,
                                 execution_identity=execution_identity)
            self._project_completion(spec, payload, out, env, actor_ref, job_id)
            # Completion projectors may add governed Dataset/Model release refs.
            # Validate again so the augmented terminal result is also contractual.
            self.catalog.validate_output(method_id, out.result)
            if attempt_claim is not None:
                self._commit_attempt_success(
                    attempt_claim, spec, out, env, execution_identity.digest)
            # Cache only after ArtifactStore has minted stable references and governed
            # completion projection has passed. Generic caches persist those references;
            # specialised field caches may still use the bytes in HandlerResult. A cache
            # outage remains non-authoritative and cannot undo a valid terminal result.
            if spec.cacheable and self.cache is not None and hasattr(self.cache, 'store'):
                try:
                    self.cache.store(method_id, payload, out,
                                     seconds=round(time.time() - t0, 3), job_id=job_id,
                                     envelope=env,
                                     execution_digest=execution_identity.digest)
                except Exception as e:                              # noqa: BLE001
                    print(f'[invoke] cache write unavailable ({type(e).__name__}: {e}) — '
                          f'the result is valid but was not persisted', file=sys.stderr,
                          flush=True)
            if job_id is not None and self.ledger is not None:
                terminal = (self.ledger.done_claimed
                            if _dispatch_claim is not None
                            else self.ledger.done)
                terminal(
                    _dispatch_claim if _dispatch_claim is not None else job_id,
                    seconds=round(time.time() - t0, 3),
                    result_summary={'ok': True,
                                    'cache': out.cache,
                                    'data': out.result,
                                    'warnings': out.warnings,
                                    'provenance': out.provenance,
                                    'result_keys': sorted(out.result),
                                    'artifact_roles': [role for role, _ in out.artifacts]})
            return env

        except failures.DiracFailure as f:
            is_bug = f.code == 'INTERNAL'
            self._complete_attempt_failure(attempt_claim, f.code, f.message)
            self.counters['failed' if is_bug else 'refused'] += 1
            if job_id is not None and self.ledger is not None:
                try:
                    terminal = (self.ledger.failed_claimed
                                if _dispatch_claim is not None
                                else self.ledger.failed)
                    terminal(
                        _dispatch_claim if _dispatch_claim is not None else job_id,
                        code=f.code, detail=f.message,
                        seconds=round(time.time() - t0, 3),
                        retryable=f.retryable)
                except Exception:                                  # noqa: BLE001
                    pass          # the refusal is the answer; a ledger write is not
            return {
                'ok': False,
                'error': f.to_error_payload(),
                'meta': {'envelope': 2, 'method_id': method_id,
                         'version': spec.version if spec else None,
                         'seconds': round(time.time() - t0, 3),
                         'request_id': request_id, 'job_id': job_id,
                         'actor': actor if actor is not None else
                                  {'kind': 'service', 'id': 'dirac-kernel'},
                         'command': command_id},
            }
        except Exception as e:                                      # noqa: BLE001
            # An untyped escape is OUR fault by definition: everything the science is
            # allowed to refuse for has a code. Converted here rather than left to the
            # transport, so all four transports report a bug identically.
            self.counters['failed'] += 1
            f = failures.DiracInternal(e)
            self._complete_attempt_failure(attempt_claim, 'INTERNAL', str(e))
            if job_id is not None and self.ledger is not None:
                try:
                    terminal = (self.ledger.failed_claimed
                                if _dispatch_claim is not None
                                else self.ledger.failed)
                    terminal(
                        _dispatch_claim if _dispatch_claim is not None else job_id,
                        code='INTERNAL', detail=str(e),
                        seconds=round(time.time() - t0, 3), retryable=True)
                except Exception:                                  # noqa: BLE001
                    pass
            return {'ok': False, 'error': f.to_error_payload(),
                    'meta': {'envelope': 2, 'method_id': method_id,
                             'version': spec.version if spec else None,
                             'seconds': round(time.time() - t0, 3),
                             'request_id': request_id, 'job_id': job_id,
                             'actor': actor if actor is not None else
                                      {'kind': 'service', 'id': 'dirac-kernel'},
                             'command': command_id}}

    def _commit_attempt_success(self, claim: Any, spec: C.MethodSpec,
                                out: HandlerResult, envelope: dict,
                                execution_digest: str) -> None:
        if self.attempt_store is None:
            raise failures.DiracInternal(
                "remote fenced execution returned an Attempt without an AttemptStore")
        now = __import__('datetime').datetime.now(__import__('datetime').timezone.utc)
        artifacts = []
        required_roles = {item.role for item in spec.artifacts if item.required}
        for ref in envelope.get('artifacts', []):
            artifacts.append({
                'role': ref['role'], 'sha256': 'sha256:' + ref['sha256'],
                'size_bytes': ref['size_bytes'],
                'media_type': ref['media_type'].split(';', 1)[0],
                'encoding': ref.get('encoding', 'identity'),
                'required': ref['role'] in required_roles,
            })
        manifest = {
            'schema_version': '1.0', 'job_id': claim.job_id,
            'attempt_id': claim.attempt_id, 'fencing_token': claim.fencing_token,
            'execution_digest': execution_digest, 'artifacts': artifacts,
            'result_summary': {'result_keys': sorted(out.result)},
            'warnings': out.warnings,
            'started_at': now.isoformat(),
            'finished_at': now.isoformat(),
        }
        data = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()
        artifact = self.store.put(data, role='output.manifest',
                                  media_type='application/json',
                                  method_version=spec.version)
        event_key = f"attempt:{claim.attempt_id}:succeeded:{claim.fencing_token}"
        self.attempt_store.commit_success(
            claim, manifest=manifest, manifest_artifact_id=artifact.id,
            required_roles=sorted(required_roles), artifact_reader=self.store,
            event_key=event_key)
        envelope['artifacts'].append(artifact.to_reference())

    def _complete_attempt_failure(self, claim: Any, code: str, message: str) -> None:
        if claim is None or self.attempt_store is None:
            return
        try:
            self.attempt_store.complete(
                claim, state='failed',
                event_key=f"attempt:{claim.attempt_id}:failed:{claim.fencing_token}",
                payload={'error_code': code, 'message': message})
        except Exception:  # noqa: BLE001
            pass

    # ── internals ─────────────────────────────────────────────────────────────
    def _open_job(self, spec: C.MethodSpec, payload: dict,
                  budget: float | None, *, queued: bool = False,
                  actor: dict[str, str], command_id: str | None,
                  request_id: str | None,
                  execution_identity: ExecutionIdentity | None = None,
                  request_key: str | None = None) -> tuple[str | None, bool]:
        """Open a ledger row, and never let the ledger break the invocation.

        A job row is OBSERVABILITY. If the database is down, the science must still
        run — the daemon already treats the cache that way — and the provenance must
        say `job_id: null` rather than imply a row exists.
        """
        try:
            import hashlib
            import json as _json
            canonical = _json.dumps(
                payload, sort_keys=True, separators=(',', ':'),
                ensure_ascii=False, allow_nan=False)
            job_id, conflict = self.ledger.open(
                method_row_id=getattr(self.ledger, 'method_row_for',
                                      lambda _m: None)(spec.method_id),
                input_sha256=hashlib.sha256(canonical.encode()).digest(),
                params=dict(payload.get('parameters') or {}),
                request_digest=(bytes.fromhex(execution_identity.cache_key(
                    payload, seed_scope_digest=None).removeprefix('sha256:'))
                    if execution_identity else None),
                budget_seconds=budget, queued=queued,
                actor_kind=actor['kind'], actor_id=actor['id'],
                command_id=command_id, request_id=request_id,
                request_key=request_key,
                dispatch_payload=(payload if request_key is not None else None),
                execution_adapter=(execution_identity.executor_adapter
                                   if request_key is not None
                                      and execution_identity is not None
                                   else None))
            return job_id, conflict
        except failures.DiracIdempotencyConflict:
            raise
        except failures.DiracFailure:
            if request_key is not None:
                raise
            return None, False
        except Exception as e:                                     # noqa: BLE001
            if request_key is not None:
                raise failures.DiracFailure(
                    'DB_UNAVAILABLE',
                    'durable request-key admission is unavailable',
                    details={'command_id': command_id,
                             'request_key': request_key,
                             'required_migration':
                                 '046_job_command_request_key.sql'}) from e
            print(f'[invoke] ledger unavailable ({type(e).__name__}: {e}) — running '
                  f'anyway, and provenance will say job_id: null', file=sys.stderr,
                  flush=True)
            return None, False

    @staticmethod
    def _actor(actor: dict[str, str] | None) -> dict[str, str]:
        actor_ref = actor or {'kind': 'service', 'id': 'dirac-kernel'}
        if (actor_ref.get('kind') not in ('human', 'agent', 'service')
                or not str(actor_ref.get('id', '')).strip()):
            raise failures.DiracInvalidParameters(
                'actor must be a human, agent, or service with a non-empty id')
        return {'kind': str(actor_ref['kind']), 'id': str(actor_ref['id'])}

    @staticmethod
    def _snapshot_payload(payload: dict) -> dict:
        """Detach a JSON request from every caller-owned mutable reference."""
        try:
            canonical = json.dumps(
                payload, sort_keys=True, separators=(',', ':'),
                ensure_ascii=False, allow_nan=False)
            snapshot = json.loads(canonical)
        except (TypeError, ValueError) as error:
            raise failures.DiracInvalidParameters(
                'Method payload must be finite JSON and cannot contain live objects',
                details={'reason': str(error)}) from error
        if not isinstance(snapshot, dict):
            raise failures.DiracInvalidParameters('Method payload must be a JSON object')
        return snapshot

    def _run_server_admission(
            self, spec: C.MethodSpec, payload: dict,
            ctx: InvocationContext) -> bool:
        """Run a descriptor-owned, API-side admission hook exactly once.

        Admission is deliberately a property of the immutable Method descriptor,
        not a method-id branch in an executor. It runs before *every* cache lookup:
        a cached historical result is still forbidden when a server-owned reference
        (for example an RBFE Campaign generation) is no longer current.

        The hook receives a detached request and the API-only context. Its return is
        round-tripped through strict JSON before being sealed into the execution
        context, so neither caller-owned objects nor a mutable hook return can rewrite
        the witness after admission.
        """
        invocation = spec.descriptor.get('invocation') or {}
        declaration = invocation.get('admission')
        if declaration is None:
            return False
        if not isinstance(declaration, dict):
            raise failures.DiracInternal(
                f'{spec.method_id}: invocation.admission must be an object')
        expected_keys = {'handler', 'attestation_key'}
        if set(declaration) != expected_keys:
            raise failures.DiracInternal(
                f'{spec.method_id}: invocation.admission must contain exactly '
                f'{sorted(expected_keys)}')
        handler_ref = declaration.get('handler')
        attestation_key = declaration.get('attestation_key')
        if (not isinstance(handler_ref, str) or not handler_ref.strip()
                or not isinstance(attestation_key, str)
                or not attestation_key.strip()):
            raise failures.DiracInternal(
                f'{spec.method_id}: invocation.admission handler and '
                'attestation_key must be non-empty strings')
        hook = spec._resolve(handler_ref)
        witness = hook(self._snapshot_payload(payload), ctx)
        if not isinstance(witness, dict):
            raise failures.DiracInternal(
                f'{spec.method_id}: server admission hook returned '
                f'{type(witness).__name__}, not a JSON object')
        ctx.server_attestations = self._snapshot_payload({attestation_key: witness})
        return True

    def _seal_server_attestations(
            self, out: HandlerResult, ctx: InvocationContext) -> HandlerResult:
        """Project the fresh API witness without mutating cached producer state.

        Cache entries are content-shared while an admission witness is current-request
        and may contain the current principal. Mutating ``out.provenance`` in place
        would let one cache consumer rewrite what a later consumer sees. A detached
        HandlerResult keeps the cached scientific bytes global and the authorization
        witness request-local.
        """
        if not ctx.server_attestations:
            return out
        provenance = dict(out.provenance or {})
        provenance['server_attestations'] = self._snapshot_payload(
            ctx.server_attestations)
        return HandlerResult(
            result=out.result,
            artifacts=list(out.artifacts),
            provenance=provenance,
            warnings=list(out.warnings),
            parameters_used=dict(out.parameters_used),
            cache=out.cache,
            cache_record=out.cache_record,
            attempt_claim=out.attempt_claim,
        )

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

    def _project_completion(self, spec: C.MethodSpec, payload: dict,
                            out: HandlerResult, envelope: dict,
                            actor: dict[str, str], job_id: str | None) -> None:
        governed = {'data.motif.snapshot', 'ml.motif.train', 'ml.motif.mesh.train'}
        if spec.method_id not in governed:
            return
        if self.motif_governance is None or not hasattr(
                self.motif_governance, 'project_completion'):
            raise failures.DiracFailure(
                'DB_UNAVAILABLE',
                f'{spec.method_id} requires durable Motif release registration; '
                'the completion projector is unavailable')
        projected = self.motif_governance.project_completion(
            method_id=spec.method_id, payload=payload, result=out.result,
            artifacts=list(envelope.get('artifacts') or []),
            envelope_meta=dict(envelope.get('meta') or {}),
            actor=actor, job_id=job_id)
        if projected:
            out.result.update(projected)

    def _envelope(self, spec: C.MethodSpec, out: HandlerResult, t0: float, *,
                  cache: str, inline_max: int | None, request_id: str | None,
                  job_id: str | None, actor: dict[str, str],
                  command_id: str | None,
                  execution_identity: ExecutionIdentity) -> dict:
        refs = []
        requires_ownership_link = bool(out.artifacts) and cache != 'computed' and (
            self.production_execution or hasattr(self.store, 'read_authorized'))
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
                except Exception as error:                         # noqa: BLE001
                    if requires_ownership_link:
                        raise failures.DiracFailure(
                            'DB_UNAVAILABLE',
                            'cache-hit Artifact ownership could not be linked to '
                            'the current actor Job',
                            details={'job_id': job_id, 'artifact_id': art.id,
                                     'role': role,
                                     'reason': f'{type(error).__name__}: {error}'}) \
                            from error
            elif requires_ownership_link:
                raise failures.DiracFailure(
                    'DB_UNAVAILABLE',
                    'the ArtifactStore cannot link a private cache hit to the '
                    'current actor Job',
                    details={'job_id': job_id, 'artifact_id': art.id, 'role': role})
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
                'actor': actor,
                'command': command_id,
                'parameters_used': out.parameters_used,
                'toolkits': self.toolkit_versions,
                'provenance': out.provenance,
                'execution_digest': execution_identity.digest,
                'execution_identity': execution_identity.to_dict(),
            },
        }

    def _execution_identity(
            self, spec: C.MethodSpec, payload: dict, handler: Callable, *,
            execution_adapter: str) -> ExecutionIdentity:
        if self.execution_identity_resolver is not None:
            resolver = self.execution_identity_resolver
            try:
                parameters = inspect.signature(resolver).parameters.values()
                accepts_route = any(
                    item.name == 'execution_adapter'
                    or item.kind is inspect.Parameter.VAR_KEYWORD
                    for item in parameters)
            except (TypeError, ValueError):
                accepts_route = False
            if self.production_execution and not accepts_route:
                raise failures.DiracInternal(
                    'production ExecutionIdentity resolver must consume the route '
                    'frozen by invocation admission')
            identity = (resolver(
                spec, payload, execution_adapter=execution_adapter)
                if accepts_route else resolver(spec, payload))
            if identity.method_id != spec.method_id:
                raise failures.DiracInternal(
                    'execution identity method_id does not match the invoked Method')
            if (identity.executor_adapter is not None
                    and identity.executor_adapter != execution_adapter):
                raise failures.DiracInternal(
                    'execution identity resolver changed the admitted execution route')
            return identity
        if self.production_execution:
            raise failures.DiracInternal(
                'production execution requires a complete ExecutionIdentity resolver')
        descriptor_digest = sha256_digest(json.dumps(
            spec.descriptor, sort_keys=True, separators=(',', ':')))
        try:
            source = inspect.getsource(handler)
        except (OSError, TypeError):
            source = f'{handler.__module__}:{handler.__qualname__}'
        source = f'{source}\nregistered-method-version:{spec.version}'
        checkpoint = ((payload.get('checkpoint') or {}).get('digest')
                      if isinstance(payload.get('checkpoint'), dict) else None)
        calibration = ((payload.get('calibration') or {}).get('digest')
                       if isinstance(payload.get('calibration'), dict) else None)
        return ExecutionIdentity.build(
            method_id=spec.method_id,
            method_descriptor_digest=descriptor_digest,
            handler_source_digest=sha256_digest(source),
            executor_adapter=execution_adapter,
            checkpoint_digests=[checkpoint] if checkpoint else (),
            calibration_digest=calibration,
            # The old fallback hashed only payload["parameters"]. Most canonical
            # Methods expose typed fields at the payload root, so two different
            # molecules or training datasets could receive the same execution
            # identity. Hash every scientific request field while excluding only
            # release-presentation metadata; retain the Dataset Snapshot reference.
            parameter_digest=sha256_digest(json.dumps({
                **{key: value for key, value in payload.items() if key != 'registration'},
                **({'registration': {
                    key: payload['registration'][key]
                    for key in ('dataset_snapshot_ref', 'program_ref', 'campaign_ref',
                                'identity_policy_release_id', 'source_commit')
                    if key in payload['registration']}}
                   if isinstance(payload.get('registration'), dict) else {}),
            }, sort_keys=True, separators=(',', ':'), allow_nan=False)),
        )
