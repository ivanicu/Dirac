"""DiracClient — the one object a Python caller, a CLI and an MCP adapter all use.

    from dirac import DiracClient
    c = DiracClient()                                  # in-process if possible
    r = c.field('homo', molfile=open('lig.mol').read(), basis='def2-svp')
    print(r.homo_ev, r.artifact('field.cube').sha256)
    cube = r.bytes('field.cube')                       # verified against the digest

TWO LAYERS, deliberately, because they fail differently:

  invoke()   the GENERIC surface. Returns the raw v2 envelope, raises nothing for a
             refusal. This is what an MCP adapter and `dirac invoke --json` use, because
             a machine consumer wants the typed object, not an exception.
  field()    the ERGONOMIC surface. Raises a typed exception on refusal and returns a
             Result with attribute access. This is what a human writing a script wants.

Both go through the identical transport call, so they cannot disagree about what
happened — only about how it is presented. An SDK where the convenient path took a
different route from the machine path would have two behaviours and one of them would be
the untested one.

TRANSPORT SELECTION, and why the default is 'auto': the same script should work on the
machine that has the science stack and on one that does not, without an edit. `auto`
prefers in-process (no serialisation, same code path as the daemon, real tracebacks) and
falls back to HTTP. Which one answered is always recorded in `meta.transport`, because a
silent fallback is how you spend an afternoon debugging the wrong process.
"""
from __future__ import annotations

import os
from typing import Any

from . import errors
from .transport import HttpTransport, LocalTransport, Transport


class Result:
    """A successful invocation, with the envelope kept whole underneath.

    Attribute access reaches into `data` so `r.homo_ev` works, but `r.envelope` is always
    there — an SDK that only exposed a flattened view would force a caller who needs
    provenance to reconstruct it, and provenance that has to be reconstructed is
    provenance nobody checks.
    """

    def __init__(self, envelope: dict, client: DiracClient,
                 method_id: str) -> None:
        self.envelope = envelope
        self.data = envelope.get('data') or {}
        self.meta = envelope.get('meta') or {}
        self.warnings = envelope.get('warnings') or []
        self.artifacts = envelope.get('artifacts') or []
        self.method_id = method_id
        self._client = client

    # ── provenance, promoted to first-class attributes ───────────────────────
    @property
    def version(self) -> str | None:
        """The digest of the SOURCE that ran. Two results with the same version came
        from identical code; two with different versions are not comparable, however
        similar the numbers look."""
        return self.meta.get('version')

    @property
    def cache(self) -> str | None:
        return self.meta.get('cache')

    @property
    def seconds(self) -> float | None:
        return self.meta.get('seconds')

    def artifact(self, role: str) -> dict:
        for a in self.artifacts:
            if a.get('role') == role:
                return a
        raise errors.DiracError(
            f'{self.method_id} returned no artifact in role {role!r}; it produced '
            f'{[a.get("role") for a in self.artifacts] or "none"}',
            envelope=self.envelope, method_id=self.method_id)

    def bytes(self, role: str = 'field.cube') -> bytes:
        """The artifact's bytes, VERIFIED against the digest in the reference.

        Identical whether they arrived inline or by reference — that symmetry is the
        whole point of the artifact design, and it is why this method has no branch a
        caller can see.
        """
        return self._client.fetch(self.artifact(role))

    def save(self, path: str, role: str = 'field.cube') -> str:
        data = self.bytes(role)
        with open(path, 'wb') as fh:
            fh.write(data)
        return path

    def __getattr__(self, name: str) -> Any:
        # Reached only when normal lookup fails, so it never shadows a real attribute.
        for container in (self.data, self.data.get('wavefunction') or {},
                          self.data.get('field') or {}):
            if name in container:
                return container[name]
        raise AttributeError(
            f'{name!r} is not in this result. data keys: {sorted(self.data)}; '
            f'wavefunction keys: {sorted((self.data.get("wavefunction") or {}))}')

    def __repr__(self) -> str:                                       # pragma: no cover
        return (f'<Result {self.method_id} version={self.version} '
                f'cache={self.cache} artifacts={[a.get("role") for a in self.artifacts]}>')


class DiracClient:
    """The client. One transport, two surfaces, no reimplemented semantics."""

    def __init__(self, transport: str | Transport = 'auto', *,
                 base_url: str | None = None, timeout: float = 600.0,
                 backend_path: str | None = None) -> None:
        self.transport = self._pick(transport, base_url, timeout, backend_path)

    @staticmethod
    def _pick(spec: str | Transport, base_url: str | None, timeout: float,
              backend_path: str | None) -> Transport:
        if not isinstance(spec, str):
            return spec
        url = base_url or os.environ.get('DIRAC_URL') or 'http://127.0.0.1:8901'
        if spec == 'http':
            return HttpTransport(url, timeout=timeout)
        if spec == 'local':
            return LocalTransport(backend_path)
        if spec != 'auto':
            raise ValueError(f'transport must be auto|local|http or a Transport, '
                             f'not {spec!r}')
        # auto: try in-process, and be explicit in the fallback rather than silent.
        local = LocalTransport(backend_path)
        try:
            local._catalog()                       # descriptors only — cheap, no science
            import importlib.util
            if importlib.util.find_spec('rdkit') is not None:
                return local
        except Exception:                                          # noqa: BLE001
            pass
        return HttpTransport(url, timeout=timeout)

    # ── generic surface ───────────────────────────────────────────────────────
    def invoke(self, method_id: str, payload: dict, **kw) -> dict:
        """The raw envelope. Refusals come back as `ok: false`, never as exceptions."""
        env = self.transport.invoke(method_id, payload, **kw)
        env.setdefault('meta', {}).setdefault('transport', self.transport.name)
        return env

    def execute(self, command_id: str, input: dict | None = None, **kw) -> dict:
        """Execute semantic application behavior, independent of transport routes."""
        env = self.transport.execute(command_id, input or {}, **kw)
        env.setdefault('meta', {}).setdefault('transport', self.transport.name)
        return env

    def commands(self) -> list[dict]:
        return self.transport.list_commands()

    def command(self, command_id: str) -> dict:
        return self.transport.describe_command(command_id)

    def health(self) -> dict:
        return self.execute('system.health')

    def job_get(self, job_id: str) -> dict:
        return self.execute('job.get', {'job_ref': {'kind': 'job', 'id': job_id}})

    def jobs(self, *, state: str | None = None, limit: int = 100) -> dict:
        return self.execute('job.list', {
            **({'state': state} if state else {}), 'limit': limit})

    def job_wait(self, job_id: str, *, timeout: float = 300) -> dict:
        return self.execute('job.wait', {
            'job_ref': {'kind': 'job', 'id': job_id}, 'timeout': timeout})

    def job_cancel(self, job_id: str) -> dict:
        return self.execute('job.cancel', {
            'job_ref': {'kind': 'job', 'id': job_id}})

    def molecule_describe(self, molecule: dict) -> dict:
        return self.execute('molecule.describe', {'molecule': molecule})

    def field_compute(self, *, molecule: dict, field_kind: str,
                      parameters: dict | None = None,
                      budget_seconds: float | None = None) -> dict:
        return self.execute('structure.field.compute', {
            'molecule': molecule, 'field_kind': field_kind,
            **({'parameters': parameters} if parameters else {}),
            **({'budget_seconds': budget_seconds}
               if budget_seconds is not None else {})})

    def methods(self) -> list[dict]:
        return self.transport.list_methods()

    def describe(self, method_id: str) -> dict:
        return self.transport.describe(method_id)

    def estimate(self, method_id: str, payload: dict) -> dict:
        return self.transport.estimate(method_id, payload)

    def fetch(self, ref: dict) -> bytes:
        return self.transport.fetch_artifact(ref)

    # ── ergonomic surface ─────────────────────────────────────────────────────
    def run(self, method_id: str, payload: dict, **kw) -> Result:
        env = self.invoke(method_id, payload, **kw)
        if not env.get('ok'):
            raise errors.from_envelope(env, method_id=method_id)
        return Result(env, self, method_id)

    def field(self, kind: str, *, molfile: str, basis: str | None = None,
              spin: int | None = None, max_seconds: float | None = None,
              inline_max: int | None = None) -> Result:
        """A field, by the short kind name a chemist actually says.

        `kind` is mapped to a method_id through the CATALOG rather than a dict in this
        file: a second mapping here would drift from the descriptors, and the SDK would
        then accept a kind the server does not have (or refuse one it does).
        """
        method_id = self.method_for_kind(kind)
        params: dict[str, Any] = {}
        if basis is not None:
            params['basis'] = basis
        if spin is not None:
            params['spin'] = spin
        payload = {'molecule': {'kind': 'molfile', 'content': molfile,
                                'dimensionality': 3}}
        if params:
            payload['parameters'] = params
        kw: dict[str, Any] = {}
        if inline_max is not None:
            kw['inline_max'] = inline_max
        if max_seconds is not None:
            kw['budget_seconds'] = max_seconds
        return self.run(method_id, payload, **kw)

    def method_for_kind(self, kind: str) -> str:
        """`homo` → `fields.qm.homo`, resolved against what the server actually has.

        An exact method_id passes through untouched, so the shortcut never blocks the
        full name. An unknown kind lists what IS available instead of guessing, because
        a wrong guess here would send a caller's molecule to a different physics.
        """
        ids = [m['method_id'] for m in self.methods()]
        if kind in ids:
            return kind
        matches = [m for m in ids if m.split('.')[-1] == kind]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise errors.DiracError(
                f'no method for kind {kind!r}. Available: {ids}',
                details={'kind': kind, 'available': ids})
        raise errors.DiracError(
            f'kind {kind!r} is ambiguous between {matches}; pass the full method_id',
            details={'kind': kind, 'matches': matches})
