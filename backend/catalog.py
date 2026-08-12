"""The MethodCatalog: descriptors that can be RUN, not only read.

WHAT WAS MISSING, and it is the audit's whole thesis in one sentence: this repo has
canonical JSON-Schema descriptors for ten methods and a registry that versions their
source, and nothing that can take a method_id and execute it. The knowledge of how to
run `fields.qm.homo` lives in an HTTP handler — so a CLI cannot have it without
speaking HTTP, and an MCP adapter cannot have it without spawning a CLI. That is what
"agent-compatible, not agent-native" means operationally.

    descriptor       says what a method accepts and returns          contracts/methods/
    registry         says which SOURCE is running, as a version      method_registry.py
    THIS FILE        binds the two to a CALLABLE and a COST MODEL

THE ONE DESIGN DECISION THAT MATTERS: a handler is a STRING (`module:function`),
resolved by importlib at invocation time and never at load time. So this module — and
therefore a CLI, an SDK and an MCP adapter — can list every method, validate a
caller's parameters, apply defaults, estimate cost and explain a refusal WITHOUT
importing RDKit or pyscf. `dirac methods list` must work on a laptop with no
chemistry stack, or the CLI is a remote control rather than a client.

The inverse property is enforced too, and it is the one that catches rot: a method
whose descriptor promises `exposure.cli: true` and whose handler cannot be resolved is
a lie the catalog refuses to tell. scripts/check_catalog.py resolves every declared
handler and fails when one is missing — because a descriptor is a promise to a client,
and an unresolvable handler is a promise discovered at the moment somebody depends on
it.

DEPENDENCY DIRECTION (ADR-001, gate 11): stdlib + failures + artifacts. No HTTP, no
psycopg, no science.
"""
from __future__ import annotations

import importlib
import json
import pathlib
from dataclasses import dataclass, field
from typing import Any, Callable

import artifacts as A
import failures

CONTRACTS = pathlib.Path(__file__).resolve().parent.parent / 'contracts'


@dataclass(frozen=True)
class ArtifactDeclaration:
    role: str
    media_type: str
    required: bool = True
    typical_bytes: int | None = None


@dataclass(frozen=True)
class MethodSpec:
    """One method, completely: contract + implementation + cost model.

    `version` is deliberately NOT part of this object's identity — it is read from the
    registry at bind time and can change while the descriptor does not. That asymmetry
    is the audit's `implementation_digest ≠ descriptor_digest` point, and it has
    already been measured here: typing a refusal inside run_scf changed every
    fields.qm.* implementation digest and invalidated the entire quantum cache, while
    the descriptors were untouched and no client could have noticed.
    """

    method_id: str
    summary: str
    descriptor: dict[str, Any]
    handler_ref: str | None
    estimate_ref: str | None
    artifacts: tuple[ArtifactDeclaration, ...]
    version: str | None = None
    _resolved: dict[str, Callable] = field(default_factory=dict, compare=False,
                                           repr=False)

    # ── the parts a client reads without running anything ────────────────────
    @property
    def input_schema(self) -> dict:
        return self.descriptor.get('input', {}).get('schema', {})

    @property
    def output_schema(self) -> dict:
        return self.descriptor.get('output', {}).get('schema', {})

    @property
    def execution(self) -> dict:
        return self.descriptor.get('execution', {})

    @property
    def exposure(self) -> dict:
        return self.descriptor.get('exposure', {})

    @property
    def cacheable(self) -> bool:
        return bool(self.execution.get('cacheable'))

    @property
    def deterministic(self) -> bool:
        return bool(self.execution.get('deterministic'))

    @property
    def refusals(self) -> list[dict]:
        """The error codes this method is DECLARED to be able to return.

        A client can build its entire error handling from this list before making a
        single call — which is the difference between a typed API and a documented
        one.
        """
        return list(self.descriptor.get('refusals') or [])

    @property
    def is_executable(self) -> bool:
        return self.handler_ref is not None

    # ── resolution, always lazy ───────────────────────────────────────────────
    def _resolve(self, ref: str) -> Callable:
        if ref in self._resolved:
            return self._resolved[ref]
        module_name, _, attr = ref.partition(':')
        # A descriptor written as backend.field_server must import as field_server
        # here: the daemon runs with backend/ on sys.path, and a dotted prefix that
        # works in one process and not the other is a bug that only appears in the
        # transport nobody tested. Both spellings are tried, and the failure names
        # both so the fix is obvious rather than archaeological.
        candidates = [module_name]
        if module_name.startswith('backend.'):
            candidates.append(module_name[len('backend.'):])
        else:
            candidates.append('backend.' + module_name)
        last: Exception | None = None
        for name in candidates:
            try:
                mod = importlib.import_module(name)
                break
            except ImportError as e:
                last = e
        else:
            raise failures.DiracInternal(
                f'{self.method_id}: cannot import the handler module for {ref!r}; '
                f'tried {candidates} and the last error was {last}. The descriptor '
                f'promises this method is callable, so this is a broken promise to '
                f'every client that read the catalog.')
        fn = getattr(mod, attr, None)
        if not callable(fn):
            raise failures.DiracInternal(
                f'{self.method_id}: {ref!r} resolved to a module but {attr!r} is not '
                f'callable in it')
        self._resolved[ref] = fn
        return fn

    def handler(self) -> Callable:
        if self.handler_ref is None:
            raise failures.DiracUnsupported(
                f'{self.method_id} is described but not executable: its descriptor '
                f'declares no invocation.handler. It can be listed, validated and '
                f'documented; it cannot be run.',
                details={'method_id': self.method_id, 'reason': 'no_handler'})
        return self._resolve(self.handler_ref)

    def estimator(self) -> Callable | None:
        return self._resolve(self.estimate_ref) if self.estimate_ref else None


class MethodCatalog:
    """Every method the system exposes, loaded from the canonical descriptors.

    Loaded from the CONTRACTS directory rather than from a Python registry, because
    the descriptors are the authority the audit asked for and a second in-code list
    would immediately become the real one. The registry's contribution is versions,
    grafted on in `bind_versions` — which is optional, so a catalog is usable with no
    database and no chemistry stack.
    """

    def __init__(self, specs: dict[str, MethodSpec]) -> None:
        self._specs = specs

    # ── loading ───────────────────────────────────────────────────────────────
    @classmethod
    def load(cls, directory: pathlib.Path | None = None) -> MethodCatalog:
        d = directory or (CONTRACTS / 'methods')
        specs: dict[str, MethodSpec] = {}
        for path in sorted(d.glob('*.method.json')):
            desc = json.loads(path.read_text(encoding='utf-8'))
            mid = desc.get('method_id')
            if not mid:
                raise failures.DiracInternal(
                    f'{path.name} has no method_id, so nothing can address it')
            if mid in specs:
                raise failures.DiracInternal(
                    f'two descriptors claim method_id {mid!r} — the catalog would '
                    f'silently pick one and clients would get whichever the '
                    f'filesystem sorted first')
            inv = desc.get('invocation') or {}
            specs[mid] = MethodSpec(
                method_id=mid,
                summary=desc.get('summary', ''),
                descriptor=desc,
                handler_ref=inv.get('handler'),
                estimate_ref=inv.get('estimate'),
                artifacts=tuple(
                    ArtifactDeclaration(
                        role=a['role'], media_type=a['media_type'],
                        required=a.get('required', True),
                        typical_bytes=a.get('typical_bytes'))
                    for a in (inv.get('artifacts') or [])))
        return cls(specs)

    def bind_versions(self, versions: dict[str, str]) -> MethodCatalog:
        """Graft the running source versions on. Separate from `load` on purpose.

        A version is a fact about THIS PROCESS's source; a descriptor is a fact about
        the contract. An offline CLI has the second and cannot have the first, and
        must say `version: null` rather than invent one — a provenance stamp that
        might be wrong is worse than an absent one, because it is quotable.
        """
        out = {}
        for mid, spec in self._specs.items():
            out[mid] = MethodSpec(
                method_id=spec.method_id, summary=spec.summary,
                descriptor=spec.descriptor, handler_ref=spec.handler_ref,
                estimate_ref=spec.estimate_ref, artifacts=spec.artifacts,
                version=versions.get(mid))
        return MethodCatalog(out)

    # ── reading ───────────────────────────────────────────────────────────────
    def __len__(self) -> int:
        return len(self._specs)

    def __contains__(self, method_id: object) -> bool:
        return method_id in self._specs

    def ids(self) -> list[str]:
        return sorted(self._specs)

    def all(self) -> list[MethodSpec]:
        return [self._specs[k] for k in sorted(self._specs)]

    def executable(self) -> list[MethodSpec]:
        return [s for s in self.all() if s.is_executable]

    def get(self, method_id: str) -> MethodSpec:
        spec = self._specs.get(method_id)
        if spec is None:
            close = [m for m in self._specs
                     if method_id.split('.')[-1] in m or m.split('.')[-1] in method_id]
            raise failures.DiracNotFound(
                f'no method {method_id!r}',
                details={'method_id': method_id, 'known': self.ids(),
                         'did_you_mean': close[:3]},
                hint={'method': close[0]} if close else None)
        return spec

    # ── validation, which is where a caller's mistake becomes a typed refusal ──
    def validate(self, method_id: str, payload: dict) -> dict:
        """Check a payload against the method's input schema; return it unchanged.

        Returns rather than mutates: applying defaults here would mean the object the
        handler sees is not the object the client sent, and the first time those
        differ the provenance record is describing a request nobody made. Defaults
        belong to the handler, which knows them, and are reported in the response's
        `parameters_used`.

        A schema violation is a PARSE refusal with the JSON POINTER of the offending
        field, not a stringified validator dump: a client that has to regex an error
        message to find out which parameter was wrong is a client that will get it
        wrong.
        """
        schema = self.get(method_id).input_schema
        if not schema:
            return payload
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            # UNVERIFIED, not valid. Passing an unvalidated payload to a handler
            # while reporting success would be the exact shape of a check that
            # cannot fail — so the caller is told the validation did not happen.
            raise failures.DiracInternal(
                'jsonschema is not importable, so the input contract cannot be '
                'enforced. Refusing rather than passing the payload through '
                'unchecked: an unvalidated request that reports "ok" is worse than '
                'one that reports why it could not be checked.')
        errors = sorted(Draft202012Validator(schema).iter_errors(payload),
                        key=lambda e: list(e.absolute_path))
        if errors:
            first = errors[0]
            pointer = '/' + '/'.join(str(p) for p in first.absolute_path)
            # INVALID_PARAMETERS, not PARSE. The molecule may be perfectly readable and
            # one named field wrong — which is a different remedy and, until the browser
            # showed a chemist "This molecule could not be parsed" for an unexpected
            # `basis`, a distinction this vocabulary did not make.
            raise failures.DiracInvalidParameters(
                f'{method_id}: {pointer if first.absolute_path else "(root)"} '
                f'{first.message}',
                details={'method_id': method_id,
                         'pointer': pointer if first.absolute_path else '',
                         'validator': first.validator,
                         'violations': [
                             {'pointer': '/' + '/'.join(str(p) for p in e.absolute_path),
                              'message': e.message, 'validator': e.validator}
                             for e in errors[:8]],
                         'violation_count': len(errors)},
                hint={'input_schema_url': f'/v2/methods/{method_id}'})
        return payload

    def validate_output(self, method_id: str, result: dict) -> None:
        """Check what a HANDLER produced against the declared output schema.

        THE ASYMMETRY THIS REMOVES: inputs have been validated since PR-02 and outputs
        never were, so "the descriptor is the authority" was half a claim. A handler could
        return an undeclared key, or omit a required one, and the only thing that noticed
        was a frontend rendering `undefined` — which looks like a field with no extrema
        rather than a contract violation.

        RAISES DiracInternal, deliberately: an output that does not match the contract is
        OUR fault, never the caller's, and the one direction that must never happen is
        reporting our defect as a problem with their molecule. The message names the
        pointer, so the fix is a line number rather than a search.

        Note this runs on the SUCCESS path of every invocation. Cost measured on the
        largest current output: ~0.4 ms. A check that only runs in tests is a check that
        does not constrain production.
        """
        schema = self.get(method_id).output_schema
        if not schema:
            return
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            raise failures.DiracInternal(
                'jsonschema is not importable, so the output contract cannot be '
                'enforced. Refusing rather than shipping an unvalidated result: an '
                'unchecked payload that reports "ok" is the shape of a check that '
                'cannot fail.')
        errors = sorted(Draft202012Validator(schema).iter_errors(result),
                        key=lambda e: list(e.absolute_path))
        if errors:
            first = errors[0]
            pointer = '/' + '/'.join(str(p) for p in first.absolute_path)
            raise failures.DiracInternal(
                f'{method_id} produced output its own descriptor forbids: '
                f'{pointer if first.absolute_path else "(root)"} {first.message}. '
                f'Either the handler is wrong or the contract is out of date — and until '
                f'one of them is fixed, a client planning from the descriptor is planning '
                f'against something that does not exist. '
                f'({len(errors)} violation(s) total)')

    # ── description, the thing an agent reads before it calls ────────────────
    def describe(self, method_id: str) -> dict:
        spec = self.get(method_id)
        return {
            'method_id': spec.method_id,
            'version': spec.version,
            'summary': spec.summary,
            'description': spec.descriptor.get('description', ''),
            'input_schema': spec.input_schema,
            'output_schema': spec.output_schema,
            'refusals': spec.refusals,
            'artifacts': [{'role': a.role, 'media_type': a.media_type,
                           'required': a.required, 'typical_bytes': a.typical_bytes}
                          for a in spec.artifacts],
            'execution': spec.execution,
            'exposure': spec.exposure,
            'executable': spec.is_executable,
        }

    def as_tool_list(self, *, surface: str = 'mcp') -> list[dict]:
        """The catalog as an agent-facing tool list.

        Filtered by the descriptor's own `exposure`, because "which methods should an
        agent see" is a product decision that belongs in the contract next to the
        method, not in an adapter's hard-coded list — an adapter's list drifts, and
        nobody notices until an agent calls something it should not have seen.
        `curated` means yes for MCP: it marks a method whose parameters were chosen
        to be safe for an agent, not a method to hide.
        """
        out = []
        for spec in self.executable():
            allowed = spec.exposure.get(surface)
            if not allowed:
                continue
            out.append({
                'name': spec.method_id.replace('.', '_'),
                'method_id': spec.method_id,
                'description': (spec.summary + ' ' +
                                spec.descriptor.get('description', '')).strip(),
                'input_schema': spec.input_schema,
                'curated': allowed == 'curated',
            })
        return out


def default_catalog() -> MethodCatalog:
    """The process-wide catalog. Cheap: ten JSON files, no imports triggered."""
    global _DEFAULT
    try:
        return _DEFAULT
    except NameError:
        _DEFAULT = MethodCatalog.load()
        return _DEFAULT


# The roles this system can produce, for cross-checking descriptors against the
# artifact store's vocabulary. A descriptor declaring a role the store has no media
# type for would produce artifacts nobody can interpret.
KNOWN_ROLES = frozenset(A.MEDIA_TYPES)
