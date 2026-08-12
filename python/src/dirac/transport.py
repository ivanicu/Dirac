"""Transports. The SDK's whole reason for existing is that these are interchangeable.

THE AUDIT'S STRUCTURAL POINT, restated as code: `MCP → spawn dirac CLI → parse stdout`
is wrong, and so is `CLI → HTTP → server`. Both put a serialisation boundary where a
function call belongs. The correct shape is

    CLI  ─┐
    MCP  ─┼─→  DiracClient  ─→  Transport  ─→  { in-process kernel | HTTP server }
    SDK  ─┘

so every surface gets identical semantics, and the only thing that varies is WHERE the
work happens. A CLI on the same machine as the daemon should not pay JSON both ways;
the same CLI pointed at a remote host must behave identically apart from latency.

WHAT A TRANSPORT IS RESPONSIBLE FOR, and it is a short list on purpose: turning
(method_id, payload) into an envelope, and turning an artifact reference into bytes.
Everything else — parameter validation, the inline decision, typed refusals, provenance
— belongs to the kernel and must NOT be reimplemented per transport. A transport that
validated its own parameters would drift from the descriptor within a week, and the two
transports would then disagree about what is legal.

THE PROPERTY THAT MUST HOLD, and which scripts/acceptance_parity.py measures: for the
same input, LocalTransport and HttpTransport return the same method version, the same
scientific values, the same artifact SHA-256 and the same typed provenance. If they
ever differ, one of them is lying about which system it is talking to.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Protocol


class Transport(Protocol):
    def execute(self, command_id: str, payload: dict, **kw) -> dict: ...
    def list_commands(self) -> list[dict]: ...
    def describe_command(self, command_id: str) -> dict: ...
    def invoke(self, method_id: str, payload: dict, **kw) -> dict: ...
    def list_methods(self) -> list[dict]: ...
    def describe(self, method_id: str) -> dict: ...
    def estimate(self, method_id: str, payload: dict) -> dict: ...
    def fetch_artifact(self, ref: dict) -> bytes: ...


class LocalTransport:
    """In-process. Imports the kernel and calls it — no socket, no JSON round trip.

    This is the transport that makes the SDK worth having rather than being a thin
    requests wrapper: a script on this machine gets the same typed result with none of
    the serialisation, and — more importantly — the same code path the daemon uses. A
    bug reproduced under LocalTransport is a bug in the kernel, not in the HTTP layer,
    which is the single most useful thing a debugging session can know up front.

    Requires the science stack, because it runs the science. `list_methods` and
    `describe` do NOT — they are answered from the descriptors, so an offline SDK is
    still a useful catalog client. That asymmetry is deliberate and is the reason
    _kernel() is lazy.
    """

    name = 'local'

    def __init__(self, backend_path: str | None = None) -> None:
        self.backend_path = backend_path or self._guess_backend()
        self._svc = None

    @staticmethod
    def _guess_backend() -> str:
        # Walk up from this file to a directory containing backend/invocation.py. An
        # installed SDK will be told explicitly; a repo checkout should just work,
        # because requiring an env var for the common case is how a tool acquires a
        # reputation for being fiddly.
        here = os.path.abspath(os.path.dirname(__file__))
        for _ in range(6):
            cand = os.path.join(here, 'backend')
            if os.path.isfile(os.path.join(cand, 'invocation.py')):
                return cand
            here = os.path.dirname(here)
        return os.environ.get('DIRAC_BACKEND', '')

    def _ensure_path(self) -> None:
        if self.backend_path and self.backend_path not in sys.path:
            sys.path.insert(0, self.backend_path)

    def _catalog(self):
        self._ensure_path()
        import catalog
        return catalog.MethodCatalog.load()

    def _kernel(self):
        """The service, assembled by the BACKEND rather than by this client.

        Written the other way first — this method imported psycopg, built a
        PostgresArtifactStore and fell back to memory — and check_layering.py's new law
        ("the SDK imports no science, DB or HTTP library") failed it. The failure was
        right: which artifact store a deployment uses is a fact about how the SERVER is
        wired, and a client that decides it has an opinion about the server's storage.
        Two clients would wire it two ways and the one that guessed wrong would write
        cubes nobody could fetch. backend/kernel.build() is now the single home for that
        assembly, and the daemon will use the same call.
        """
        if self._svc is not None:
            return self._svc
        self._ensure_path()
        import kernel
        self._svc = kernel.build()
        self._store = self._svc.store
        return self._svc

    # ── the surface ───────────────────────────────────────────────────────────
    def invoke(self, method_id: str, payload: dict, **kw) -> dict:
        return self._kernel().invoke(method_id, payload, **kw)

    def _dispatcher(self):
        self._ensure_path()
        from dirac_app import CommandDispatcher
        if not hasattr(self, '_command_dispatcher'):
            self._command_dispatcher = CommandDispatcher(self._kernel())
        return self._command_dispatcher

    def execute(self, command_id: str, payload: dict, **kw) -> dict:
        return self._dispatcher().execute(command_id, payload, **kw)

    def list_commands(self) -> list[dict]:
        return self._dispatcher().registry.list()

    def describe_command(self, command_id: str) -> dict:
        return self._dispatcher().registry.describe(command_id)

    def list_methods(self) -> list[dict]:
        # Answered from the descriptors, so this works with no science stack at all.
        cat = self._catalog()
        return [{'method_id': s.method_id, 'summary': s.summary,
                 'executable': s.is_executable, 'cacheable': s.cacheable,
                 'artifacts': [a.role for a in s.artifacts]} for s in cat.all()]

    def describe(self, method_id: str) -> dict:
        return self._catalog().describe(method_id)

    def estimate(self, method_id: str, payload: dict) -> dict:
        return self._kernel().estimate(method_id, payload)

    def fetch_artifact(self, ref: dict) -> bytes:
        self._ensure_path()
        import artifacts as A
        if ref.get('inline_base64'):
            return A.decode_inline(ref)
        self._kernel()
        art, data = self._store.read(ref.get('id') or f"sha256:{ref['sha256']}")
        A.verify_bytes(data, ref['sha256'])
        return data


class HttpTransport:
    """Over the wire, to a daemon that may be on another machine.

    urllib rather than requests, and that is a deliberate dependency decision: an SDK
    whose import fails on a machine without requests is an SDK that cannot be used in
    the place it is most needed — someone else's environment. The whole client is
    stdlib.
    """

    name = 'http'

    def __init__(self, base_url: str = 'http://127.0.0.1:8901',
                 timeout: float = 600.0, token: str | None = None) -> None:
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.token = token or os.environ.get('DIRAC_TOKEN')

    def _request(self, method: str, path: str, body: dict | None = None,
                 headers: dict | None = None) -> tuple[int, dict, bytes]:
        h = {'Accept': 'application/json'}
        if body is not None:
            h['Content-Type'] = 'application/json'
        if self.token:
            h['Authorization'] = f'Bearer {self.token}'
        h.update(headers or {})
        req = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as fh:
                return fh.status, dict(fh.headers), fh.read()
        except urllib.error.HTTPError as e:
            # A refusal is a RESULT, and the server sends it as an envelope with a
            # non-200 status for some codes (413, 404, 403). Raising here would make
            # every caller catch an exception to read a body that already says exactly
            # what happened, and the typed error would be reconstructed from a status
            # code — the same guess PR-03 deleted from the server.
            return e.code, dict(e.headers), e.read()

    def invoke(self, method_id: str, payload: dict, **kw) -> dict:
        # v1 shim while PR-07's single logical endpoint is unlanded. Isolated in ONE
        # method so switching to /v2/invoke is a local edit, and marked in the
        # returned envelope so a caller can tell which surface answered — an SDK that
        # hid this would make the acceptance test's http leg untraceable.
        status, _h, raw = self._request('POST', '/v2/invoke',
                                        {'method_id': method_id, 'input': payload,
                                         **kw})
        if status == 404:
            return self._invoke_via_v1(method_id, payload, **kw)
        env = json.loads(raw or b'{}')
        env.setdefault('meta', {})['transport'] = 'http:/v2/invoke'
        return env

    def execute(self, command_id: str, payload: dict, **kw) -> dict:
        _status, _headers, raw = self._request(
            'POST', '/v2/execute', {'command': command_id, 'input': payload, **kw})
        env = json.loads(raw or b'{}')
        env.setdefault('meta', {})['transport'] = 'http:/v2/execute'
        return env

    def list_commands(self) -> list[dict]:
        _status, _headers, raw = self._request('GET', '/v2/commands')
        return (json.loads(raw or b'{}').get('data') or {}).get('commands', [])

    def describe_command(self, command_id: str) -> dict:
        _status, _headers, raw = self._request(
            'GET', f'/v2/commands/{urllib.parse.quote(command_id)}')
        return json.loads(raw or b'{}').get('data') or {}

    def _invoke_via_v1(self, method_id: str, payload: dict, **kw) -> dict:
        """Translate to the legacy /field route and shape its answer as an envelope.

        This exists so the SDK is usable TODAY against the running daemon, and it is
        the only place in the SDK that knows v1 exists. It is not a compatibility
        layer to keep: when /v2/invoke lands, this method is deleted, and the
        acceptance test is what proves the deletion changed nothing.
        """
        from . import compat
        return compat.invoke_via_v1(self, method_id, payload, **kw)

    def list_methods(self) -> list[dict]:
        status, _h, raw = self._request('GET', '/v2/methods')
        if status == 404:
            from . import compat
            return compat.list_methods_from_descriptors()
        return (json.loads(raw or b'{}').get('data') or {}).get('methods', [])

    def describe(self, method_id: str) -> dict:
        status, _h, raw = self._request('GET', f'/v2/methods/{method_id}')
        if status == 404:
            from . import compat
            return compat.describe_from_descriptors(method_id)
        return json.loads(raw or b'{}').get('data') or {}

    def estimate(self, method_id: str, payload: dict) -> dict:
        status, _h, raw = self._request('POST', f'/v2/methods/{method_id}/estimate',
                                        {'input': payload})
        if status == 404:
            return {'available': False,
                    'reason': 'this server has no /v2 estimate endpoint (PR-07 has '
                              'not landed there); an estimate cannot be computed '
                              'remotely without it, and inventing one locally would '
                              'be a number about a different machine'}
        return json.loads(raw or b'{}').get('data') or {}

    def fetch_artifact(self, ref: dict) -> bytes:
        """Bytes by digest, verified before they are returned.

        The verification is the client's own, over what actually arrived: a digest the
        server asserts about bytes the server sent is not a check, it is a claim. This
        is the one line that makes a reference safe to pass around.
        """
        from . import errors
        if ref.get('inline_base64'):
            return errors.decode_and_verify(ref)
        url = ref.get('url') or f"/v2/artifacts/sha256:{ref['sha256']}"
        status, headers, raw = self._request('GET', url)
        if status >= 400:
            raise errors.from_envelope(json.loads(raw or b'{}'), method_id=None)
        errors.verify(raw, ref['sha256'],
                      advertised_by=headers.get('X-Dirac-Sha256'))
        return raw
