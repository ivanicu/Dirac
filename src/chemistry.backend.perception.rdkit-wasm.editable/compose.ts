import { DefaultPluginSpec, PluginSpec } from '../mol-plugin/spec';
import { ChemPack } from './types';

export function resolveChemPacks(packs: readonly ChemPack[]): ChemPack[] {
    const resolved: ChemPack[] = [];
    const seen = new Map<string, ChemPack>();
    const visiting = new Set<string>();

    const visit = (pack: ChemPack) => {
        const existing = seen.get(pack.id);
        if (existing) {
            if (existing !== pack) throw new Error(`Conflicting chemistry packs use id '${pack.id}'`);
            return;
        }
        if (visiting.has(pack.id)) throw new Error(`Circular chemistry pack dependency at '${pack.id}'`);

        visiting.add(pack.id);
        for (const dependency of pack.dependencies ?? []) visit(dependency);
        visiting.delete(pack.id);
        seen.set(pack.id, pack);
        resolved.push(pack);
    };

    for (const pack of packs) visit(pack);
    return resolved;
}

export function composeChemSpec(packs: readonly ChemPack[], base: PluginSpec = DefaultPluginSpec()): PluginSpec {
    const resolved = resolveChemPacks(packs);
    return {
        ...base,
        actions: [
            ...(base.actions ?? []),
            ...resolved.flatMap(pack => pack.spec?.actions ?? []),
        ],
        behaviors: [
            ...base.behaviors,
            ...resolved.flatMap(pack => pack.spec?.behaviors ?? []),
        ],
        animations: [
            ...(base.animations ?? []),
            ...resolved.flatMap(pack => pack.spec?.animations ?? []),
        ],
        customFormats: [
            ...(base.customFormats ?? []),
            ...resolved.flatMap(pack => pack.spec?.customFormats ?? []),
        ],
        config: [
            ...(base.config ?? []),
            ...resolved.flatMap(pack => pack.spec?.config ?? []),
        ],
    };
}
