/** Canonical domain identity is generated from contracts, never hand-maintained here. */
export type { ObjectKind, ObjectRef, RelationKind, CommandId, ActorRef } from '../generated/commands';

import type { ObjectKind, ObjectRef } from '../generated/commands';

export function objectRef<K extends ObjectKind>(kind: K, id: string): ObjectRef<K> {
    if (!id) throw new Error(`ObjectRef<${kind}> requires a non-empty id`);
    return { kind, id };
}

export function sameObject(a?: ObjectRef | null, b?: ObjectRef | null): boolean {
    return a === b || (!!a && !!b && a.kind === b.kind && a.id === b.id);
}
