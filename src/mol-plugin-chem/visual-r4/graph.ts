import type { R4GraphExecutionContext, R4GraphNode, R4Input, R4Operator, R4OperatorHandler, R4RepresentationGraph, R4StructureSnapshot } from './types';

function inputValue(input: R4Input, values: ReadonlyMap<string, unknown>) {
    if ('value' in input) return input.value;
    if (!values.has(input.node)) throw new Error(`R4 graph input references unresolved node '${input.node}'`);
    const value = values.get(input.node);
    if (!input.output) return value;
    if (!value || typeof value !== 'object' || !(input.output in value)) {
        throw new Error(`R4 graph node '${input.node}' has no output '${input.output}'`);
    }
    return (value as Record<string, unknown>)[input.output];
}

function dependencies(node: R4GraphNode) {
    return Object.values(node.inputs ?? {}).flatMap(input => 'node' in input ? [input.node] : []);
}

export function orderR4Graph(graph: R4RepresentationGraph): R4GraphNode[] {
    const nodes = new Map(graph.nodes.map(node => [node.id, node]));
    if (nodes.size !== graph.nodes.length) throw new Error(`R4 graph '${graph.id}' contains duplicate node ids`);

    const ordered: R4GraphNode[] = [];
    const permanent = new Set<string>();
    const temporary = new Set<string>();

    const visit = (id: string) => {
        if (permanent.has(id)) return;
        if (temporary.has(id)) throw new Error(`R4 graph '${graph.id}' contains a cycle at '${id}'`);
        const node = nodes.get(id);
        if (!node) throw new Error(`R4 graph '${graph.id}' references missing node '${id}'`);
        temporary.add(id);
        for (const dependency of dependencies(node)) visit(dependency);
        temporary.delete(id);
        permanent.add(id);
        ordered.push(node);
    };

    for (const node of graph.nodes) visit(node.id);
    return ordered;
}

export class R4GraphRuntime {
    private readonly handlers = new Map<R4Operator, R4OperatorHandler>();

    register(operator: R4Operator, handler: R4OperatorHandler) {
        if (this.handlers.has(operator)) throw new Error(`R4 operator '${operator}' is already registered`);
        this.handlers.set(operator, handler);
        return this;
    }

    async execute(graph: R4RepresentationGraph, snapshot: R4StructureSnapshot) {
        const values = new Map<string, unknown>();
        for (const node of orderR4Graph(graph)) {
            const handler = this.handlers.get(node.operator);
            if (!handler) throw new Error(`R4 graph '${graph.id}' requires unregistered operator '${node.operator}'`);
            const inputs = Object.fromEntries(Object.entries(node.inputs ?? {}).map(([key, input]) => [key, inputValue(input, values)]));
            const context: R4GraphExecutionContext = { snapshot, values };
            values.set(node.id, await handler(context, node, inputs));
        }
        return Object.fromEntries(Object.entries(graph.outputs).map(([key, input]) => [key, inputValue(input, values)]));
    }
}
