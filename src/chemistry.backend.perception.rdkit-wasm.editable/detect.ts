import { resolveChemPacks } from './compose';
import { ChemPack } from './types';

export function getChemFileExtension(name: string): string {
    const normalized = name.toLowerCase().split(/[?#]/, 1)[0];
    const withoutCompression = normalized.replace(/\.(gz|zip)$/i, '');
    const index = withoutCompression.lastIndexOf('.');
    return index < 0 ? '' : withoutCompression.slice(index + 1);
}

/** Suggest only from the pack catalog supplied by the product. */
export function suggestChemPacks(fileNames: readonly string[], catalog: readonly ChemPack[]): ChemPack[] {
    const extensions = new Set(fileNames.map(getChemFileExtension).filter(Boolean));
    return resolveChemPacks(catalog.filter(pack => pack.fileExtensions?.some(ext => extensions.has(ext))));
}
