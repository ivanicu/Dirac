"""MCP over stdio, calling the SDK IN-PROCESS. No subprocess anywhere in this file.

THE LAW THIS FILE EXISTS TO OBEY, quoted from the audit: `MCP → spawn dirac CLI → parse
stdout` is wrong; the shape is `MCP → SDK/InvocationService`. check_layering.py enforces
it by failing if this module so much as mentions `subprocess`, `Popen`, `os.system` or
`shutil.which` — and it was reported N/A until this file existed, deliberately, because a
law that passes for lack of a subject reads exactly like one being obeyed.

WHY PR-04 (addressable artifacts) HAD TO COME FIRST, and it is the concrete reason this
adapter is possible at all: a tool result carrying a 6.7 MB Gaussian cube as base64 is
~9 MB of context. The model will never read it, cannot verify it, and the conversation is
over. So every invocation here runs with `inline_max=0` — nothing inline, ever — and the
tool result carries a REFERENCE: role, media type, size, sha256, url. Measured on this
box: 1,742 bytes describing 6,746,050. A separate `dirac_artifact_head` tool answers
"how big is it, what is it" and a deliberately-capped `dirac_artifact_text` returns a
bounded HEAD of the bytes, because a cube's first 200 bytes are its grid geometry and
that is usually the whole question.

WHAT IS NOT REIMPLEMENTED HERE, which is most of an MCP server's usual bulk: the tool
list comes from the descriptors' own `exposure` field, and the input schemas are the
canonical JSON Schemas. So "which methods may an agent see" is a product decision living
next to the method, not a hard-coded list in an adapter that drifts. Refusals are already
typed with `caller_action` and `hint`, so this file forwards them instead of inventing
prose.

Transport: newline-delimited JSON-RPC 2.0 on stdin/stdout, which is what an MCP stdio
server is. Nothing is printed to stdout except responses — a stray print would corrupt
the stream, so every diagnostic goes to stderr.
"""
from __future__ import annotations

import json
import sys
from typing import Any

from . import errors
from .client import DiracClient

PROTOCOL_VERSION = '2024-11-05'
SERVER_INFO = {'name': 'dirac', 'version': '0.1.0'}

# Nothing inline, ever. A client that genuinely wants bytes asks for them by digest, and
# then it is the client's context being spent knowingly rather than ours spending it by
# default.
INLINE_MAX = 0
# The bounded read for `dirac_artifact_text`. A cube's header is ~200 bytes and carries
# the grid; 8 KiB is generous for that and small enough that no answer here can flood a
# conversation.
TEXT_HEAD_LIMIT = 8192


def _log(msg: str) -> None:
    print(f'[mcp] {msg}', file=sys.stderr, flush=True)


class DiracMCP:
    def __init__(self, client: DiracClient | None = None) -> None:
        # 'auto' so this runs in-process where the science stack exists and over HTTP
        # where it does not, with no configuration difference for the agent.
        self.client = client or DiracClient('auto')
        self._tools: list[dict] | None = None

    # ── tool surface ──────────────────────────────────────────────────────────
    def tools(self) -> list[dict]:
        if self._tools is not None:
            return self._tools
        out: list[dict] = []
        for m in self.client.methods():
            # `executable` comes from the descriptor: a method with no handler can be
            # described and not run, and offering it as a tool would be a promise the
            # system cannot keep.
            if not m.get('executable'):
                continue
            mid = m['method_id']
            try:
                d = self.client.describe(mid)
            except errors.DiracError:
                continue
            out.append({
                'name': mid.replace('.', '_'),
                'description': (
                    f'{d.get("summary", "")} {d.get("description", "")}'.strip()
                    + '\n\nReturns a REFERENCE to the field artifact, not the bytes: a '
                      'Gaussian cube is megabytes and no model needs them in context. '
                      'Use dirac_artifact_head for its size and media type, or '
                      'dirac_artifact_text for a bounded head of the file (the first '
                      '~200 bytes are the grid geometry).'),
                'inputSchema': d.get('input_schema') or {'type': 'object'},
                '_method_id': mid,
            })
        out.extend([
            {'name': 'dirac_methods',
             'description': 'List every method this system can compute, with which are '
                            'executable and what artifacts they produce.',
             'inputSchema': {'type': 'object', 'properties': {}}},
            {'name': 'dirac_describe',
             'description': 'The full contract for one method: input schema, output '
                            'schema, the refusals it can return, and its artifacts.',
             'inputSchema': {'type': 'object', 'required': ['method_id'],
                             'properties': {'method_id': {'type': 'string'}}}},
            {'name': 'dirac_estimate',
             'description': 'What a method would cost BEFORE running it. Use this before '
                            'invoking anything expensive.',
             'inputSchema': {'type': 'object', 'required': ['method_id', 'input'],
                             'properties': {'method_id': {'type': 'string'},
                                            'input': {'type': 'object'}}}},
            {'name': 'dirac_artifact_head',
             'description': 'Size, media type and digest of a stored artifact. No bytes.',
             'inputSchema': {'type': 'object', 'required': ['address'],
                             'properties': {'address': {'type': 'string'}}}},
            {'name': 'dirac_artifact_text',
             'description': f'The first {TEXT_HEAD_LIMIT} bytes of an artifact as text. '
                            f'Bounded on purpose — a full cube would flood the context.',
             'inputSchema': {'type': 'object', 'required': ['address'],
                             'properties': {'address': {'type': 'string'},
                                            'bytes': {'type': 'integer',
                                                      'maximum': TEXT_HEAD_LIMIT}}}},
        ])
        self._tools = out
        return out

    def _method_for_tool(self, name: str) -> str | None:
        for t in self.tools():
            if t['name'] == name:
                return t.get('_method_id')
        return None

    # ── dispatch ──────────────────────────────────────────────────────────────
    def call_tool(self, name: str, args: dict) -> dict:
        """Returns an MCP tool result. A REFUSAL is a result with isError, not a crash.

        An agent that receives an exception learns nothing it can act on; an agent that
        receives `{code: UNSUPPORTED, caller_action: …, hint: {parameters: {basis: …}}}`
        can fix its own call. Those fields already exist because the kernel computes
        them — this method's whole job is not to lose them.
        """
        if name == 'dirac_methods':
            return self._ok({'methods': self.client.methods()})
        if name == 'dirac_describe':
            return self._ok(self.client.describe(args['method_id']))
        if name == 'dirac_estimate':
            return self._ok(self.client.estimate(args['method_id'],
                                                 args.get('input') or {}))
        if name in ('dirac_artifact_head', 'dirac_artifact_text'):
            return self._artifact(name, args)

        mid = self._method_for_tool(name)
        if mid is None:
            return self._error('NOT_FOUND', f'no tool named {name!r}',
                               {'available': [t['name'] for t in self.tools()]})
        env = self.client.invoke(mid, args, inline_max=INLINE_MAX)
        if not env.get('ok'):
            err = env.get('error') or {}
            return self._error(err.get('code', 'INTERNAL'),
                               err.get('user_message') or err.get('message', ''),
                               {k: v for k, v in err.items()
                                if k in ('details', 'hint', 'retryable',
                                         'caller_action')})
        # Strip inline_base64 defensively. INLINE_MAX=0 should mean it is never present,
        # and "should" is not a mechanism — one megabyte of base64 reaching a model
        # because a default changed elsewhere is exactly the failure this whole PR chain
        # exists to prevent.
        arts = []
        for a in env.get('artifacts') or []:
            arts.append({k: v for k, v in a.items()
                         if k not in ('inline_base64', 'inline')})
        return self._ok({'data': env.get('data'), 'artifacts': arts,
                         'warnings': env.get('warnings') or [],
                         'method_id': mid,
                         'version': (env.get('meta') or {}).get('version'),
                         'cache': (env.get('meta') or {}).get('cache'),
                         'seconds': (env.get('meta') or {}).get('seconds')})

    def _artifact(self, name: str, args: dict) -> dict:
        address = args['address']
        ref = {'sha256': address.replace('sha256:', ''),
               'url': f'/v2/artifacts/{address}'}
        if name == 'dirac_artifact_head':
            try:
                status, headers, raw = self.client.transport._request(  # type: ignore[attr-defined]
                    'HEAD', f'/v2/artifacts/{address}')
                return self._ok({'address': address,
                                 'size_bytes': int(headers.get('Content-Length', 0)),
                                 'media_type': headers.get('Content-Type'),
                                 'sha256': headers.get('X-Dirac-Sha256'),
                                 'role': headers.get('X-Dirac-Role')})
            except AttributeError:
                return self._error('UNSUPPORTED',
                                   'artifact metadata over the local transport is not '
                                   'wired yet; use the http transport for this tool')
        limit = min(int(args.get('bytes') or TEXT_HEAD_LIMIT), TEXT_HEAD_LIMIT)
        data = self.client.fetch(ref)
        head = data[:limit]
        return self._ok({'address': address, 'returned_bytes': len(head),
                         'total_bytes': len(data),
                         'truncated': len(head) < len(data),
                         'text': head.decode('utf-8', errors='replace')})

    @staticmethod
    def _ok(payload: Any) -> dict:
        return {'content': [{'type': 'text',
                             'text': json.dumps(payload, indent=2, default=str)}],
                'isError': False}

    @staticmethod
    def _error(code: str, message: str, extra: dict | None = None) -> dict:
        return {'content': [{'type': 'text',
                             'text': json.dumps({'code': code, 'message': message,
                                                 **(extra or {})}, indent=2,
                                                default=str)}],
                'isError': True}

    # ── JSON-RPC ──────────────────────────────────────────────────────────────
    def handle(self, req: dict) -> dict | None:
        method = req.get('method')
        rid = req.get('id')
        params = req.get('params') or {}
        try:
            if method == 'initialize':
                return self._result(rid, {
                    'protocolVersion': PROTOCOL_VERSION,
                    'capabilities': {'tools': {}},
                    'serverInfo': SERVER_INFO})
            if method in ('notifications/initialized', 'initialized'):
                return None                     # a notification takes no response
            if method == 'tools/list':
                return self._result(rid, {'tools': [
                    {k: v for k, v in t.items() if not k.startswith('_')}
                    for t in self.tools()]})
            if method == 'tools/call':
                return self._result(rid, self.call_tool(params.get('name', ''),
                                                        params.get('arguments') or {}))
            if method == 'ping':
                return self._result(rid, {})
            return self._rpc_error(rid, -32601, f'unknown method {method!r}')
        except errors.DiracError as e:
            # A typed refusal that escaped call_tool. Reported as a tool result rather
            # than a protocol error: the request was well-formed and the answer is "no".
            return self._result(rid, self._error(e.code, str(e), {'details': e.details}))
        except Exception as e:                                      # noqa: BLE001
            import traceback
            traceback.print_exc(file=sys.stderr)
            return self._rpc_error(rid, -32603, f'{type(e).__name__}: {e}')

    @staticmethod
    def _result(rid: Any, result: Any) -> dict:
        return {'jsonrpc': '2.0', 'id': rid, 'result': result}

    @staticmethod
    def _rpc_error(rid: Any, code: int, message: str) -> dict:
        return {'jsonrpc': '2.0', 'id': rid, 'error': {'code': code,
                                                       'message': message}}

    def serve(self, stdin=None, stdout=None) -> int:
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        _log(f'ready · transport={self.client.transport.name}')
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError as e:
                stdout.write(json.dumps(self._rpc_error(None, -32700, str(e))) + '\n')
                stdout.flush()
                continue
            resp = self.handle(req)
            if resp is not None:
                stdout.write(json.dumps(resp) + '\n')
                stdout.flush()
        return 0


def main(argv: list[str] | None = None) -> int:
    return DiracMCP().serve()


if __name__ == '__main__':
    sys.exit(main())
