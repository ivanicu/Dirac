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
import sys
from typing import Any

import artifacts
import catalog
import invocation

DEFAULT_DSN = 'dbname=dirac user=ivan'


def toolkit_versions() -> dict[str, str]:
    """What is actually loaded, asked of the modules rather than remembered."""
    out: dict[str, str] = {}
    for name in ('rdkit', 'pyscf', 'numpy'):
        try:
            out[name] = getattr(__import__(name), '__version__', 'unknown')
        except ImportError:
            pass
    return out


def source_versions() -> dict[str, str]:
    """method_id → the digest of the running source.

    Imports field_server, and therefore RDKit — which is why this is a SEPARATE function
    from `catalog.MethodCatalog.load()`. A catalog client that only lists methods must
    not pay for a chemistry toolkit, and the acceptance test needs the digest. Both are
    true, so the expensive half is opt-in.
    """
    try:
        import field_server as FS
        import method_registry as MR
        return {mid: MR.unit_version(FS, u['fns'], u['consts'])[0]
                for mid, u in MR.UNITS.items()}
    except Exception as e:                                          # noqa: BLE001
        print(f'[kernel] source versions unavailable ({type(e).__name__}: {e}); '
              f'provenance will report version: null rather than a guess',
              file=sys.stderr, flush=True)
        return {}


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


def build(*, dsn: str = DEFAULT_DSN, with_versions: bool = True,
          store: Any | None = None, cache: Any | None = None,
          with_cache: bool = True) -> invocation.InvocationService:
    """A ready kernel. The only supported way to get one.

    `with_versions=False` skips the field_server import, for a caller that wants a
    catalog-only service on a machine with no chemistry stack — it will report
    `version: null`, which is honest, rather than failing to start.
    """
    cat = catalog.MethodCatalog.load()
    if with_versions:
        v = source_versions()
        if v:
            cat = cat.bind_versions(v)
    st, kind = (store, 'injected') if store is not None else default_store(dsn)
    # The cache is what makes a kernel invocation equivalent to the route's, and therefore
    # what makes deleting the route's orchestration a refactor rather than a regression:
    # without it every cache hit becomes a fresh SCF.
    ca = cache if cache is not None else (default_cache() if with_cache else None)
    svc = invocation.InvocationService(cat, store=st, cache=ca,
                                      toolkit_versions=toolkit_versions())
    svc.store_kind = kind                    # type: ignore[attr-defined]
    svc.cache_kind = ('injected' if cache is not None
                      else ('field_cube' if ca is not None else 'none'))  # type: ignore[attr-defined]
    return svc
