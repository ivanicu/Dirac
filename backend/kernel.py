"""How to assemble a working InvocationService. ONE home for the wiring.

WHY THIS FILE EXISTS, and it was the dependency gate that forced it: the SDK's
LocalTransport was building its own store — `import psycopg`, construct a
PostgresArtifactStore, fall back to memory. That failed check_layering.py's new law
("the SDK imports no science, DB or HTTP library") and the failure was CORRECT rather
than pedantic: deciding which artifact store to use is a fact about how this deployment
is wired, and a client that decides it has an opinion about the server's storage. Two
clients would then wire it two ways, and the one that guessed wrong would write cubes
nobody could fetch.

So the assembly lives here, next to the things being assembled, and every consumer asks
for a kernel rather than building one:

    LocalTransport   → kernel.build()          (an SDK, in-process)
    the HTTP daemon  → kernel.build()          (PR-06, when the route is rewired)
    an MCP adapter   → the SDK, which asks here

WHAT "AVAILABLE" MEANS, stated because the degraded case must not be silent: a kernel
with no database is a CORRECT kernel — it computes, it addresses artifacts in memory, and
its provenance says so. What it cannot do is hand out a reference that outlives the
process. `build()` reports which store it got in `store_kind`, so a caller can tell the
difference instead of discovering it when a 404 arrives.
"""
from __future__ import annotations

import contextlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

import artifacts
import catalog
import execution
import invocation
import jobs
import traces

# Deployment and test boundaries must be selectable before the kernel is built.
# Keeping the fallback preserves the single-machine default; honoring DIRAC_DSN
# prevents an "isolated" browser rehearsal from silently mutating the main DB.
DEFAULT_DSN = os.environ.get('DIRAC_DSN', 'dbname=dirac user=ivan')
DEFAULT_MOTIF_WORKER_IMAGE = (
    'nvcr.io/nvidia/gpu-operator@sha256:'
    '6584c36f153d18cfce284f7e5bc477887ce3c1ac566dc795bd80c9af6c6488f7')
DEFAULT_POLICY_IMAGE = (
    'docker.io/library/busybox@sha256:'
    '9db7b59979c38555a39def84a31fb98b5296952f9e3afd4f6f11f05b07adfab0')


def default_executor():
    """Select the deployed executor explicitly; never infer GPU capability.

    The Kubernetes mode is a single-node deployment bridge: the immutable base
    image supplies the container boundary while deployment-owned PersistentVolumes
    mount a read-only Dirac runtime snapshot and a narrowly scoped fenced exchange.
    A registry-built worker plus object storage can replace those volumes without
    changing InvocationService or the worker protocol.
    """
    mode = os.environ.get('DIRAC_EXECUTOR', 'thread').strip().lower()
    if mode in ('', 'thread'):
        return execution.ThreadExecutor(max_workers=2)
    if mode != 'kubernetes':
        raise RuntimeError(
            f"unsupported DIRAC_EXECUTOR={mode!r}; expected thread or kubernetes")
    from executors.kubernetes_invocation import KubernetesInvocationExecutor
    from executors.kubernetes_kueue import (
        KubernetesKueueAdapter, StaticPvcMount)

    repository = Path(__file__).resolve().parents[1]
    exchange = Path(os.environ.get(
        'DIRAC_KUBERNETES_EXCHANGE_HOST',
        repository / '.runtime/pv/exchange')).resolve()
    worker_repository = Path('/home/ivan/dirac')
    worker_exchange = worker_repository / '.runtime/kubernetes-exchange'
    worker_image = os.environ.get('DIRAC_MOTIF_WORKER_IMAGE',
                                  DEFAULT_MOTIF_WORKER_IMAGE)
    policy_image = os.environ.get('DIRAC_KUBERNETES_POLICY_IMAGE',
                                  DEFAULT_POLICY_IMAGE)
    worker = worker_repository / 'backend/env/bin/python'
    entrypoint = worker_repository / 'backend/motif_worker.py'
    adapter = KubernetesKueueAdapter(
        worker_command=[str(worker), str(entrypoint),
                        '--exchange-root', str(worker_exchange)],
        allowed_images=[worker_image], policy_init_image=policy_image,
        static_pvc_mounts=[
            StaticPvcMount('dirac-runtime', 'dirac-motif-runtime',
                           str(worker_repository), True),
            StaticPvcMount(
                'dirac-posix-shell', 'dirac-motif-runtime', '/bin/sh', True,
                sub_path='runtime-bin/dash'),
            StaticPvcMount('dirac-exchange', 'dirac-motif-exchange',
                           str(worker_exchange), False),
        ])
    from motif.resource_broker import PostgresResourceBroker
    from execution_control.attempt_store import PostgresAttemptStore
    import psycopg
    pages = os.sysconf("SC_AVPHYS_PAGES")
    page_size = os.sysconf("SC_PAGE_SIZE")
    available_ram = int(pages * page_size)
    gpu_vram = 0
    try:
        probe = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            check=True, capture_output=True, text=True, timeout=5)
        gpu_vram = int(probe.stdout.splitlines()[0].strip()) * (1 << 20)
    except (OSError, ValueError, subprocess.SubprocessError, IndexError):
        pass
    broker = PostgresResourceBroker(
        lambda: psycopg.connect(DEFAULT_DSN), {
            "cpu_cores": max(1, (os.cpu_count() or 1) - 4),
            "ram_bytes": int(available_ram * .85),
            "gpus": 1 if gpu_vram else 0,
            "gpu_vram_bytes": int(gpu_vram * .875),
            "scratch_bytes": max(0, shutil.disk_usage(exchange).free - (100 << 30)),
            "persistent_growth_bytes": max(0, shutil.disk_usage(exchange).free - (100 << 30)),
            "process_slots": 20, "scf_slots": 2, "campaign_credits": 1e12,
        })
    attempt_store = PostgresAttemptStore(lambda: psycopg.connect(DEFAULT_DSN))
    return KubernetesInvocationExecutor(
        adapter=adapter, exchange_root=exchange, container_image=worker_image,
        resource_broker=broker, attempt_store=attempt_store)


def toolkit_versions() -> dict[str, str]:
    """What is actually loaded, asked of the modules rather than remembered."""
    out: dict[str, str] = {}
    for name in ('rdkit', 'pyscf', 'numpy'):
        try:
            out[name] = getattr(__import__(name), '__version__', 'unknown')
        except ImportError:
            pass
    return out


def source_identities() -> dict[str, dict[str, str]]:
    """method_id → full running source witness.

    Imports field_server, and therefore RDKit — which is why this is a SEPARATE function
    from `catalog.MethodCatalog.load()`. A catalog client that only lists methods must
    not pay for a chemistry toolkit.  Production execution needs BOTH the short display
    version and the full SHA-256; truncating the latter into a cache identity would turn
    a UI convenience into the scientific collision boundary.
    """
    try:
        import field_server as FS
        import method_registry as MR
        return {
            row['method_id']: {
                'version': row['version'],
                'digest': 'sha256:' + bytes(row['sha256']).hex(),
            }
            for row in MR.plan(FS)
        }
    except Exception as e:                                          # noqa: BLE001
        print(f'[kernel] source identities unavailable ({type(e).__name__}: {e}); '
              f'non-production provenance will report version: null rather than a guess',
              file=sys.stderr, flush=True)
        return {}


def source_versions() -> dict[str, str]:
    """Compatibility projection used by catalog/read-only callers."""
    return {method_id: row['version']
            for method_id, row in source_identities().items()}


def default_store(dsn: str = DEFAULT_DSN) -> tuple[Any, str]:
    """The artifact store this deployment should use, and a NAME for which one it is.

    Returning the name is not decoration: a memory store and a Postgres store behave
    identically until the process exits, at which point every reference the memory store
    minted becomes a 404. A caller that cannot tell them apart will hand out references
    it cannot honour.
    """
    try:
        import psycopg
        import artifacts_pg
        # Prove the connection rather than assume it: constructing the store touches
        # nothing, so a store built against a dead database looks healthy until the first
        # write — in a background thread, where the failure is a log line nobody reads.
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute('SELECT 1 FROM app.artifact LIMIT 0')
        return artifacts_pg.PostgresArtifactStore(
            lambda: psycopg.connect(dsn, autocommit=True)), 'postgres'
    except Exception as e:                                          # noqa: BLE001
        print(f'[kernel] persistent artifact store unavailable '
              f'({type(e).__name__}: {e}) — using an in-memory store. Artifacts are '
              f'addressable and verifiable, but their references do NOT survive this '
              f'process.', file=sys.stderr, flush=True)
        return artifacts.MemoryArtifactStore(), 'memory'


def default_rbfe_reference_resolver(dsn: str = DEFAULT_DSN):
    """Resolve only registered target/protein-pose pairs for RBFE preflight."""
    try:
        import psycopg
        from motif.rbfe_references import PostgresRbfeReferenceResolver
        # This resolver is a transactional aggregate, not a collection of
        # independent statements.  Probe campaign state AND explicit artifact
        # ownership up front so preparation cannot publish bytes that the HTTP
        # authorization layer cannot safely attribute afterwards.
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT to_regclass('app.rbfe_campaign') IS NOT NULL, "
                "to_regclass('app.rbfe_campaign_revision') IS NOT NULL, "
                "to_regclass('app.rbfe_campaign_system_import') IS NOT NULL, "
                "EXISTS (SELECT 1 FROM information_schema.columns "
                "WHERE table_schema='app' AND table_name='rbfe_campaign' "
                "AND column_name='state_digest'), "
                "to_regclass('app.rbfe_campaign_owner_updated_idx') IS NOT NULL, "
                "to_regclass('app.rbfe_campaign_artifact') IS NOT NULL, "
                "EXISTS (SELECT 1 FROM pg_constraint "
                "WHERE conrelid=to_regclass('app.rbfe_campaign_artifact') "
                "AND contype='f' "
                "AND conname='rbfe_campaign_artifact_role_fk' "
                "AND pg_get_constraintdef(oid) LIKE "
                "'%%FOREIGN KEY (artifact_id, role)%%'), "
                "EXISTS (SELECT 1 FROM information_schema.columns "
                "WHERE table_schema='app' AND table_name='job' "
                "AND column_name='request_key') "
                "AND EXISTS (SELECT 1 FROM pg_constraint "
                "WHERE conrelid=to_regclass('app.job') AND contype='c' "
                "AND conname='job_request_key_nonempty' "
                "AND pg_get_constraintdef(oid) = format("
                "'CHECK (((request_key IS NULL) OR (request_key ~ "
                "((%L::text || %L::text) || %L::text))))', "
                "'[^[:space:]', U&'\\00A0\\2007\\202F\\FEFF', ']')) "
                "AND EXISTS (SELECT 1 FROM pg_constraint "
                "WHERE conrelid=to_regclass('app.job') AND contype='c' "
                "AND conname='job_request_key_length' "
                "AND pg_get_constraintdef(oid) = "
                "'CHECK (((request_key IS NULL) OR "
                "(length(request_key) <= 256)))') "
                "AND EXISTS (SELECT 1 FROM pg_constraint "
                "WHERE conrelid=to_regclass('app.job') AND contype='c' "
                "AND conname='job_request_key_has_command' "
                "AND pg_get_constraintdef(oid) = "
                "'CHECK (((request_key IS NULL) OR "
                "(command_id IS NOT NULL)))'), "
                "EXISTS (SELECT 1 FROM pg_index "
                "WHERE indexrelid=to_regclass('app.job_command_request_key_once') "
                "AND indisunique "
                "AND pg_get_indexdef(indexrelid) LIKE "
                "'CREATE UNIQUE INDEX job_command_request_key_once ON app.job USING btree "
                "(actor_kind, actor_id, command_id, request_key) WHERE %%' "
                "AND pg_get_expr(indpred, indrelid) = '(request_key IS NOT NULL)'), "
                "to_regclass('app.job_dispatch') IS NOT NULL")
            capability = cur.fetchone()
        if capability is None or not all(capability):
            capability = tuple(capability or (False,) * 9)
            missing = []
            if not all(capability[:5]):
                missing.append('040_rbfe_campaign_state.sql')
            if not all(capability[5:7]):
                missing.append('045_rbfe_campaign_artifact_ownership.sql')
            if not all(capability[7:9]):
                missing.append('046_job_command_request_key.sql')
            if not bool(capability[9]):
                missing.append('047_job_dispatch_fence.sql')
            raise RuntimeError(
                'RBFE campaign persistence requires complete migrations: '
                + ', '.join(missing))
        return PostgresRbfeReferenceResolver(
            # Do not enable autocommit: mutators use SELECT FOR UPDATE and must
            # commit campaign rows, revisions, dependency invalidations and
            # import receipts atomically when their connection context exits.
            lambda: psycopg.connect(dsn))
    except Exception as error:                                    # noqa: BLE001
        print(f'[kernel] RBFE reference resolver unavailable '
              f'({type(error).__name__}: {error})', file=sys.stderr, flush=True)
        return None


def default_cache():
    """The durable cube cache, or None with a reason printed.

    Injected rather than imported by the service, for the same reason the store is: the kernel
    must run with no cache at all. Requires field_server (it owns db_get_cube and the producer
    identity), so this is the second thing in this module that pulls in the science stack — and
    like the first, it is opt-in.
    """
    try:
        import cache_fields
        import cube as CU
        import field_server as FS
        # THE KERNEL RUNS db_init ITSELF, and this line is the fix for something that had been
        # silently true since the SDK landed: `_db_ok` is set by the daemon's startup, NOT at
        # import, so in any OTHER process — an SDK LocalTransport, a CLI invocation, a test —
        # it was False and db_get_cube returned None unconditionally. The CLI has never had a
        # cache and nothing said so; it just recomputed, correctly and slowly, and a 6-minute
        # SCF looked like the price of the method rather than the price of a missing init.
        #
        # Idempotent by construction: db_init upserts the producer and the compute units, so
        # calling it once per process costs seven statements and makes this process a
        # first-class producer instead of a guest.
        if not getattr(FS, '_db_ok', False):
            try:
                # The daemon historically logs startup on stdout. Reached through a CLI,
                # the same lines are diagnostics and must not corrupt `--json`.
                with contextlib.redirect_stdout(sys.stderr):
                    FS.db_init()
            except Exception as e:                                  # noqa: BLE001
                print(f'[kernel] db_init failed ({type(e).__name__}: {e}) — no cache',
                      file=sys.stderr, flush=True)
                return None
        if not getattr(FS, '_db_ok', False):
            print('[kernel] no durable cube cache: db_init ran and the database is still '
                  'reported off. Every invocation will compute — correct, and slower.',
                  file=sys.stderr, flush=True)
            return None

        def get_cube_for_kernel(molfile_sha, kind, basis):
            return FS.db_get_cube(molfile_sha, kind, basis, include_internal=True)

        return cache_fields.FieldCubeCache(
            get_cube_for_kernel, put_cube=FS.db_put_cube, prepare_mol=FS.prepare_mol,
            canonicalise=CU.canonicalise)
    except Exception as e:                                          # noqa: BLE001
        print(f'[kernel] cube cache unavailable ({type(e).__name__}: {e}) — computing every '
              f'invocation', file=sys.stderr, flush=True)
        return None


def default_result_cache(store: Any, dsn: str = DEFAULT_DSN):
    """Generic durable cache, enabled only with the same durable ArtifactStore.

    A database cache paired with a memory artifact store would return references that
    the process cannot resolve, so assembly rejects that incoherent pairing up front.
    """
    try:
        import psycopg
        import cache_fields
        import cache_results
        import artifacts_pg
        if not isinstance(store, artifacts_pg.PostgresArtifactStore):
            return None
        connect = lambda: psycopg.connect(dsn, autocommit=True)
        with connect() as conn, conn.cursor() as cur:
            cur.execute('SELECT 1 FROM app.v_result_cache_servable LIMIT 0')
        return cache_results.PostgresResultCache(
            connect, store, excluded_methods=cache_fields.CACHEABLE_KIND)
    except Exception as e:                                          # noqa: BLE001
        print(f'[kernel] generic result cache unavailable ({type(e).__name__}: {e}) — '
              'deterministic non-field methods will compute', file=sys.stderr, flush=True)
        return None


def default_jobs():
    """The deployment's JobStore, with an explicit process-durable fallback."""
    try:
        import field_server as FS
        if not getattr(FS, '_db_ok', False):
            FS.db_init()
        if getattr(FS, '_db_ok', False):
            current = getattr(FS, '_jobs', None)
            if current is not None:
                current.bind_method_rows(getattr(FS, '_method_ids', {}))
                return current
    except Exception as e:                                          # noqa: BLE001
        print(f'[kernel] durable job store unavailable ({type(e).__name__}: {e}) — '
              'job handles survive only this process', file=sys.stderr, flush=True)
    return jobs.MemoryJobStore()


def default_traces(dsn: str = DEFAULT_DSN):
    """Durable command observations, with an explicit process-local fallback."""
    try:
        import psycopg
        connect = lambda: psycopg.connect(dsn, autocommit=True)
        with connect() as conn, conn.cursor() as cur:
            cur.execute('SELECT 1 FROM app.command_trace LIMIT 0')
        return traces.PostgresCommandTraceStore(connect)
    except Exception as e:                                          # noqa: BLE001
        print(f'[kernel] durable command traces unavailable '
              f'({type(e).__name__}: {e}) — observations survive only this process',
              file=sys.stderr, flush=True)
        return traces.MemoryCommandTraceStore()


def default_motif_governance(dsn: str = DEFAULT_DSN):
    """Durable Motif semantic mutations, or None rather than process-local fiction."""
    try:
        import psycopg
        from motif.governance import PostgresMotifGovernanceStore
        connect = lambda: psycopg.connect(dsn)
        with connect() as conn, conn.cursor() as cur:
            cur.execute('SELECT 1 FROM bio.measurement_v2 LIMIT 0')
        return PostgresMotifGovernanceStore(connect)
    except Exception as e:                                          # noqa: BLE001
        print(f'[kernel] Motif governance unavailable '
              f'({type(e).__name__}: {e}) — governance Commands fail closed',
              file=sys.stderr, flush=True)
        return None


def default_program_repository(dsn: str = DEFAULT_DSN):
    """Durable Program aggregate repository, or None so mutations fail closed."""
    try:
        import psycopg
        from programs.repository import PostgresProgramRepository
        connect = lambda: psycopg.connect(dsn)
        with connect() as conn, conn.cursor() as cur:
            cur.execute('SELECT 1 FROM design.program_event LIMIT 0')
        return PostgresProgramRepository(connect)
    except Exception as e:                                          # noqa: BLE001
        print(f'[kernel] Program repository unavailable '
              f'({type(e).__name__}: {e}) — Program Commands fail closed',
              file=sys.stderr, flush=True)
        return None


def build(*, dsn: str = DEFAULT_DSN, with_versions: bool = True,
          store: Any | None = None, cache: Any | None = None,
          with_cache: bool = True, job_store: Any | None = None,
          executor: Any | None = None,
          trace_store: Any | None = None,
          motif_governance: Any | None = None,
          program_repository: Any | None = None,
          production_execution: bool = True) -> invocation.InvocationService:
    """A ready kernel. The only supported way to get one.

    `with_versions=False` skips the field_server import, for a caller that wants a
    catalog-only service on a machine with no chemistry stack — it will report
    `version: null`, which is honest, rather than failing to start.
    """
    cat = catalog.MethodCatalog.load()
    running_sources: dict[str, dict[str, str]] = {}
    if with_versions:
        running_sources = source_identities()
        v = {method_id: row['version']
             for method_id, row in running_sources.items()}
        if v:
            cat = cat.bind_versions(v)
    elif production_execution:
        raise RuntimeError(
            'production kernel construction cannot disable running source identities; '
            'pass production_execution=False explicitly for a catalog-only/dev kernel')
    st, kind = (store, 'injected') if store is not None else default_store(dsn)
    # The cache is what makes a kernel invocation equivalent to the route's, and therefore
    # what makes deleting the route's orchestration a refactor rather than a regression:
    # without it every cache hit becomes a fresh SCF.
    if cache is not None:
        ca = cache
    elif with_cache:
        import cache_results
        specialised = default_cache()
        generic = default_result_cache(st, dsn)
        repositories = [c for c in (specialised, generic) if c is not None]
        ca = cache_results.CompositeCache(*repositories) if repositories else None
    else:
        ca = None
    js = job_store if job_store is not None else default_jobs()
    trace = trace_store if trace_store is not None else default_traces(dsn)
    governance = (motif_governance if motif_governance is not None
                  else default_motif_governance(dsn))
    programs = (program_repository if program_repository is not None
                else default_program_repository(dsn))
    rbfe_references = default_rbfe_reference_resolver(dsn)
    # A ThreadExecutor still executes sync calls inline, while also making descriptor
    # default_mode=job truthful for /v2/jobs submissions.
    ex = executor or default_executor()
    if getattr(ex, "adapter_kind", None) == "kubernetes":
        ex.artifact_reader = st
    identity_resolver = None
    if production_execution:
        from execution_control.production_identity import (
            build_production_identity_resolver)
        identity_resolver = build_production_identity_resolver(
            executor=ex,
            method_sources=running_sources,
            repository=Path(__file__).resolve().parents[1],
            dependency_lock_path=(
                Path(__file__).resolve().parent / 'motif/requirements.lock.txt'))
    svc = invocation.InvocationService(cat, store=st, cache=ca, ledger=js, executor=ex,
                                      trace_store=trace,
                                      artifact_reader=st,
                                      artifact_writer=st,
                                      attempt_store=getattr(ex, 'attempt_store', None),
                                      motif_governance=governance,
                                      program_repository=programs,
                                      rbfe_reference_resolver=rbfe_references,
                                      execution_identity_resolver=identity_resolver,
                                      production_execution=production_execution,
                                      toolkit_versions=toolkit_versions())
    svc.store_kind = kind                    # type: ignore[attr-defined]
    svc.cache_kind = ('injected' if cache is not None
                      else ('composite' if ca is not None else 'none'))  # type: ignore[attr-defined]
    svc.job_store_kind = getattr(js, 'kind', 'injected')  # type: ignore[attr-defined]
    svc.job_durability = getattr(js, 'durability', 'unknown')  # type: ignore[attr-defined]
    svc.executor_kind = getattr(ex, 'kind', 'injected')  # type: ignore[attr-defined]
    svc.execution_identity_mode = (  # type: ignore[attr-defined]
        'production' if production_execution else 'development')
    try:
        import psycopg
        from motif.closed_loop import ClosedLoopController
        controller = ClosedLoopController(
            svc, lambda: psycopg.connect(dsn, autocommit=True))
    except Exception as error:  # noqa: BLE001
        print(f'[kernel] closed-loop controller unavailable '
              f'({type(error).__name__}: {error})', file=sys.stderr, flush=True)
        controller = None
    svc.closed_loop_controller = controller  # type: ignore[attr-defined]
    try:
        from motif.rbfe_runset import RbfeRunSetController
        rbfe_runsets = RbfeRunSetController(
            svc, lambda: psycopg.connect(dsn, autocommit=True))
    except Exception as error:  # noqa: BLE001
        print(f'[kernel] RBFE RunSet controller unavailable '
              f'({type(error).__name__}: {error})', file=sys.stderr, flush=True)
        rbfe_runsets = None
    svc.rbfe_runset_controller = rbfe_runsets  # type: ignore[attr-defined]
    if getattr(js, 'durability', 'none') == 'durable':
        try:
            svc.recover_keyed_dispatches()
        except Exception as error:  # noqa: BLE001
            print(f'[kernel] durable dispatch recovery unavailable '
                  f'({type(error).__name__}: {error})',
                  file=sys.stderr, flush=True)
    return svc
