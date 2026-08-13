"""Canonical semantic command and ObjectRef registries."""
from __future__ import annotations

import importlib
import json
import pathlib
from dataclasses import dataclass, field
from typing import Any, Callable

import failures
from contracts.validation import check_schema, violations

ROOT = pathlib.Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ObjectRef:
    kind: str
    id: str

    def to_dict(self) -> dict[str, str]:
        return {'kind': self.kind, 'id': self.id}


@dataclass(frozen=True)
class CommandDefinition:
    id: str
    version: int
    descriptor: dict[str, Any]
    _resolved: dict[str, Callable] = field(default_factory=dict, compare=False,
                                                   repr=False)

    @property
    def job_policy(self) -> str:
        return self.descriptor['job_policy']

    def handler(self) -> Callable:
        ref = self.descriptor['handler']
        if ref in self._resolved:
            return self._resolved[ref]
        module, _, name = ref.partition(':')
        mod = importlib.import_module(f'dirac_app.{module}')
        fn = getattr(mod, name, None)
        if not callable(fn):
            raise failures.DiracInternal(
                f'command {self.id} declares missing handler {ref}')
        self._resolved[ref] = fn
        return fn


class CommandRegistry:
    def __init__(self, definitions: dict[str, CommandDefinition],
                 object_kinds: frozenset[str], object_ref_schema: dict) -> None:
        self._definitions = definitions
        self.object_kinds = object_kinds
        self.object_ref_schema = object_ref_schema

    @classmethod
    def load(cls) -> 'CommandRegistry':
        domain = json.loads((ROOT / 'contracts/domain/object-kinds.json').read_text())
        raw = json.loads((ROOT / 'contracts/commands/registry.json').read_text())
        defs: dict[str, CommandDefinition] = {}
        errors = set(json.loads((ROOT / 'contracts/errors.json').read_text())['codes'])
        kinds = frozenset(domain['kinds'])
        required = {
            'id', 'version', 'input_schema', 'output_schema', 'category',
            'mutability', 'execution_class', 'executors', 'idempotency_policy',
            'job_policy', 'provenance_policy', 'input_object_kinds',
            'output_object_kinds', 'errors', 'handler',
        }
        for item in raw['commands']:
            missing = required - set(item)
            if missing:
                raise failures.DiracInternal(
                    f'command {item.get("id", "<unknown>")} misses {sorted(missing)}')
            cid = item['id']
            if cid in defs:
                raise failures.DiracInternal(f'duplicate command id {cid}')
            unknown_kinds = (set(item['input_object_kinds'])
                             | set(item['output_object_kinds'])) - kinds
            if unknown_kinds:
                raise failures.DiracInternal(
                    f'{cid} refers to unknown ObjectKinds {sorted(unknown_kinds)}')
            unknown_errors = set(item['errors']) - errors
            if unknown_errors:
                raise failures.DiracInternal(
                    f'{cid} refers to unknown errors {sorted(unknown_errors)}')
            if item['job_policy'] == 'required' and item['execution_class'] != 'long':
                raise failures.DiracInternal(
                    f'{cid}: a required Job must be declared long')
            for direction in ('input_schema', 'output_schema'):
                schema = _expand_object_ref(item[direction], domain['object_ref'])
                try:
                    check_schema(schema)
                except Exception as exc:
                    raise failures.DiracInternal(
                        f'{cid} declares an invalid {direction}: {exc}') from exc
            defs[cid] = CommandDefinition(cid, int(item['version']), item)
        registry = cls(defs, kinds, domain['object_ref'])
        registry.validate_handlers()
        return registry

    def validate_handlers(self) -> None:
        for d in self.all():
            d.handler()

    def all(self) -> list[CommandDefinition]:
        return [self._definitions[k] for k in sorted(self._definitions)]

    def get(self, command_id: str) -> CommandDefinition:
        try:
            return self._definitions[command_id]
        except KeyError:
            raise failures.DiracNotFound(
                f'no command {command_id!r}',
                details={'command_id': command_id,
                         'known': sorted(self._definitions)}) from None

    def describe(self, command_id: str) -> dict:
        return dict(self.get(command_id).descriptor)

    def list(self) -> list[dict]:
        return [{k: d.descriptor[k] for k in
                 ('id', 'version', 'category', 'mutability', 'execution_class',
                  'executors', 'job_policy', 'provenance_policy')}
                for d in self.all()]

    def validate_input(self, definition: CommandDefinition, value: dict) -> None:
        self._validate_schema(definition, value, 'input_schema', 'input')
        _validate_refs(value, self.object_kinds)

    def validate_output(self, definition: CommandDefinition, value: dict) -> None:
        self._validate_schema(definition, value, 'output_schema', 'output')
        _validate_refs(value, self.object_kinds)

    def _validate_schema(self, definition: CommandDefinition, value: dict,
                         schema_key: str, direction: str) -> None:
        schema = _expand_object_ref(definition.descriptor[schema_key],
                                    self.object_ref_schema)
        errors = violations(schema, value)
        if errors:
            message = f'{definition.id} {direction}: {errors[0].message}'
            details = {'pointer': errors[0].pointer, 'command_id': definition.id,
                       'direction': direction}
            if direction == 'input':
                raise failures.DiracInvalidParameters(message, details=details)
            raise failures.DiracInternal(message, details=details)


def _expand_object_ref(value: Any, schema: dict) -> Any:
    if isinstance(value, dict):
        if value == {'$ref': 'object-ref'}:
            expanded = json.loads(json.dumps(schema))
            expanded['properties']['kind']['enum'] = sorted(
                json.loads((ROOT / 'contracts/domain/object-kinds.json').read_text())['kinds'])
            return expanded
        return {k: _expand_object_ref(v, schema) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_object_ref(v, schema) for v in value]
    return value


def _validate_refs(value: Any, kinds: frozenset[str]) -> None:
    if isinstance(value, dict):
        # ActorRef deliberately has the same compact shape as ObjectRef.  It is a
        # provenance identity, not a domain object, so validate it in the Command /
        # document schema rather than rejecting its actor kind as an ObjectKind.
        actor_kinds = {'human', 'agent', 'service'}
        if (set(value) == {'kind', 'id'} and value.get('kind') not in kinds
                and value.get('kind') not in actor_kinds):
            raise failures.DiracInvalidParameters(
                f'unknown ObjectKind {value.get("kind")!r}',
                details={'known': sorted(kinds)})
        for child in value.values():
            _validate_refs(child, kinds)
    elif isinstance(value, list):
        for child in value:
            _validate_refs(child, kinds)
