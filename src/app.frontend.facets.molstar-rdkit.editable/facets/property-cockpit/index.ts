/**
 * Property Optimization Cockpit facet.
 *
 * Renders Lipinski/Veber/lead-likeness dashboard for the focused ligand.
 *
 * Integration contract: the lab already computes the focused ligand's molfile
 * for the 2D depiction panel; it passes that same molfile + label to
 * `renderPropertiesPanel(molfile, label)`. The facet is a pure consumer of
 * the chemistry substrate — no knowledge of the lab's internal state.
 *
 * This is the first Dirac facet built on the new shared substrate. It
 * establishes the integration pattern: facet imports from chemistry/,
 * lab calls the facet from its existing lifecycle.
 */

import { computeLigandDescriptors, type DescriptorReport } from '../../../chemistry.backend.perception.rdkit-wasm.editable/descriptors';

function byId<T extends HTMLElement>(id: string): T | null {
    return document.getElementById(id) as T | null;
}

function fmt(n: number, digits = 1): string {
    if (!Number.isFinite(n)) return '—';
    if (Math.abs(n) >= 1000) return n.toFixed(0);
    return n.toFixed(digits);
}

function descCell(label: string, value: string, unit: string, threshold: string, pass: boolean, layer?: string): string {
    const clickAttr = layer ? ` data-toggle-layer="${layer}" style="cursor:pointer"` : '';
    return `
        <div class="desc-cell" data-pass="${pass}"${clickAttr}>
            <div class="desc-cell-label">${label}</div>
            <div class="desc-cell-value">${value}<span class="desc-cell-unit">${unit}</span></div>
            <div class="desc-cell-threshold">${threshold}${layer ? ' · click to highlight' : ''}</div>
        </div>`;
}

function ruleLine(name: string, status: string, pass: boolean): string {
    return `
        <div class="rule-line">
            <span>${name}</span>
            <span class="rule-line-status" data-pass="${pass}">${status}</span>
        </div>`;
}

function ruleSummary(title: string, rulesHtml: string, overallPass: boolean, verdictText: string): string {
    return `
        <div class="rule-summary" data-overall="${overallPass}">
            <div class="rule-summary-heading">
                <strong>${title}</strong>
                <span class="rule-summary-verdict" data-pass="${overallPass}">${verdictText}</span>
            </div>
            ${rulesHtml}
        </div>`;
}

/**
 * Build the full properties panel HTML for a given descriptor report.
 * Pure function — no DOM mutation. Exposed for testing and for future
 * facets that may want to embed property fragments.
 */
export function renderPropertiesHtml(report: DescriptorReport): string {
    const d = report.descriptors;
    const e = report.evaluation;

    const grid = `
        <div class="desc-grid">
            ${descCell('MW', fmt(d.molecularWeight, 1), 'Da', `≤ 500`, e.lipinski.mwPass)}
            ${descCell('LogP', fmt(d.logP, 2), '', `≤ 5.0`, e.lipinski.logPPass)}
            ${descCell('TPSA', fmt(d.tpsa, 1), 'Å²', `≤ 140`, e.veber.tpsaPass)}
            ${descCell('Rot. bonds', String(d.numRotatableBonds), '', `≤ 10`, e.veber.rotatableBondsPass)}
            ${descCell('HBD', String(d.hbd), '', `≤ 5`, e.lipinski.hbdPass, 'donor-acceptor-rdkit')}
            ${descCell('HBA', String(d.hba), '', `≤ 10`, e.lipinski.hbaPass, 'donor-acceptor-rdkit')}
            ${descCell('Heavy atoms', String(d.numHeavyAtoms), '', '', true)}
            ${descCell('Rings', String(d.numRings), '', `ar ${d.numAromaticRings} / ali ${d.numAliphaticRings}`, true, 'ring-atoms-rdkit')}
            ${descCell('Heterocycles', String(d.numHeterocycles), '', `${d.numAromaticHeterocycles} aromatic`, true)}
            ${descCell('Stereo centers', String(d.numAtomStereoCenters), '', `${d.numUnspecifiedStereoCenters} unspecified`, d.numUnspecifiedStereoCenters === 0, 'stereo-rdkit')}
            ${descCell('Fraction sp³', fmt(d.fractionCSP3 * 100, 0), '%', '', true, 'sp3-carbons-rdkit')}
            ${descCell('Amide bonds', String(d.numAmideBonds), '', '', true)}
        </div>`;

    const lipinskiRules =
        ruleLine('MW ≤ 500', `${fmt(d.molecularWeight, 1)} Da`, e.lipinski.mwPass) +
        ruleLine('LogP ≤ 5', fmt(d.logP, 2), e.lipinski.logPPass) +
        ruleLine('HBD ≤ 5', String(d.hbd), e.lipinski.hbdPass) +
        ruleLine('HBA ≤ 10', String(d.hba), e.lipinski.hbaPass);

    const veberRules =
        ruleLine('Rotatable ≤ 10', String(d.numRotatableBonds), e.veber.rotatableBondsPass) +
        ruleLine('TPSA ≤ 140', `${fmt(d.tpsa, 1)} Å²`, e.veber.tpsaPass);

    const leadRules =
        ruleLine('MW ≤ 300', `${fmt(d.molecularWeight, 1)} Da`, e.leadLike.mwPass) +
        ruleLine('LogP ≤ 3', fmt(d.logP, 2), e.leadLike.logPPass) +
        ruleLine('Rings ≤ 3', String(d.numRings), e.leadLike.numRingsPass) +
        ruleLine('Rot. bonds ≤ 5', String(d.numRotatableBonds), e.leadLike.rotatableBondsPass);

    const summaries =
        ruleSummary('Lipinski (Rule of 5)', lipinskiRules, e.lipinski.overallPass,
            e.lipinski.overallPass ? 'PASS' : `${e.lipinski.violations} viol.`) +
        ruleSummary('Veber (oral bioavailability)', veberRules, e.veber.overallPass,
            e.veber.overallPass ? 'PASS' : 'FAIL') +
        ruleSummary('Lead-likeness', leadRules, e.leadLike.overallPass,
            e.leadLike.overallPass ? 'PASS' : 'FAIL');

    return grid + summaries;
}

/**
 * Render the properties panel. Lab calls this with the same molfile it
 * already produced for the 2D depiction panel, plus the ligand label.
 *
 * When ligand is null/empty, renders the "no ligand" state.
 */
export async function renderPropertiesPanel(molfile: string | null, ligandLabel: string | null): Promise<void> {
    const content = byId<HTMLElement>('properties-content');
    const summary = byId<HTMLElement>('properties-summary');
    const summaryStats = byId<HTMLElement>('properties-summary-stats');
    if (!content || !summary || !summaryStats) return;

    if (!molfile || !ligandLabel) {
        content.innerHTML = '<p class="ledger-empty">No deposited ligand in this structure.</p>';
        summary.textContent = 'No deposited ligand';
        summaryStats.textContent = '';
        return;
    }

    summary.textContent = 'Computing descriptors…';
    summaryStats.textContent = '';
    content.innerHTML = '';

    const report = await computeLigandDescriptors(molfile);
    if (!report) {
        content.innerHTML = '<p class="ledger-empty">RDKit descriptor computation unavailable for this ligand.</p>';
        summary.textContent = 'Descriptors unavailable';
        return;
    }

    content.innerHTML = renderPropertiesHtml(report);
    summary.textContent = ligandLabel;
    const d = report.descriptors;
    summaryStats.textContent = `MW ${fmt(d.molecularWeight, 0)} · LogP ${fmt(d.logP, 1)} · ${d.numRings}R · ${d.numRotatableBonds}RB`;
}
