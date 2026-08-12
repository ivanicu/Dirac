"""Transport-neutral command dispatcher."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import failures

from .registry import CommandRegistry


@dataclass(frozen=True)
class CommandContext:
    kernel: Any
    actor: dict[str, str]
    request_id: str | None = None


class CommandDispatcher:
    def __init__(self, kernel: Any, registry: CommandRegistry | None = None) -> None:
        self.kernel = kernel
        self.registry = registry or CommandRegistry.load()

    def execute(self, command_id: str, input: dict | None = None, *,
                actor: dict[str, str] | None = None,
                request_id: str | None = None) -> dict:
        started = time.time()
        definition = self.registry.get(command_id)
        payload = dict(input or {})
        self.registry.validate_input(definition, payload)
        actor_ref = actor or {'kind': 'human', 'id': 'local'}
        if actor_ref.get('kind') not in ('human', 'agent', 'service') or not actor_ref.get('id'):
            raise failures.DiracInvalidParameters(
                'actor must be a human, agent, or service with a non-empty id')
        ctx = CommandContext(
            kernel=self.kernel,
            actor=actor_ref,
            request_id=request_id)
        try:
            result = definition.handler()(payload, ctx)
            if isinstance(result, dict) and 'ok' in result:
                envelope = result
            else:
                envelope = {'ok': True, 'data': result,
                            'artifacts': [], 'warnings': [], 'meta': {}}
            if envelope.get('ok'):
                self.registry.validate_output(definition, envelope.get('data') or {})
                if definition.job_policy == 'required':
                    job_id = ((envelope.get('meta') or {}).get('job_id')
                              or (((envelope.get('data') or {}).get('job') or {}).get('id')))
                    if not job_id:
                        raise failures.DiracInternal(
                            f'{definition.id} requires a Job but its handler returned no job')
        except failures.DiracFailure as error:
            envelope = {'ok': False, 'error': error.to_error_payload(), 'meta': {}}
        except Exception as error:                                  # noqa: BLE001
            failure = failures.DiracInternal(error)
            envelope = {'ok': False, 'error': failure.to_error_payload(), 'meta': {}}
        envelope.setdefault('meta', {}).update({
            'envelope': 2,
            'command': definition.id,
            'command_version': definition.version,
            'command_seconds': round(time.time() - started, 3),
            'actor': ctx.actor,
            'request_id': request_id,
        })
        return envelope
