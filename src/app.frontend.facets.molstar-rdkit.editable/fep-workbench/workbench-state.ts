export type OperationScope = Readonly<{
    id: string;
    kind: string;
    sessionRevision: number;
    editRevision: number;
    storageRevision: number;
}>;

export type ScopeCheck = {
    edits?: boolean;
    storage?: boolean;
};

/**
 * One ownership clock for every asynchronous workbench operation.
 * A deliberate transition may advance and adopt its own scope; every competing
 * operation remains stale. User navigation and cross-tab writes advance the
 * clock without an owner and invalidate every outstanding scope.
 */
export class OperationCoordinator {
    private sessionRevision = 0;
    private editRevision = 0;
    private storageRevision = 0;
    private sequence = 0;
    private readonly latestByKind = new Map<string, string>();

    constructor(private readonly ownerId: string) {}

    begin(kind: string): OperationScope {
        this.sequence += 1;
        const scope = {
            id: `${this.ownerId}:${kind}:${this.sequence}`,
            kind,
            sessionRevision: this.sessionRevision,
            editRevision: this.editRevision,
            storageRevision: this.storageRevision,
        };
        this.latestByKind.set(kind, scope.id);
        return scope;
    }

    current(scope: OperationScope, check: ScopeCheck = {}): boolean {
        return this.latestByKind.get(scope.kind) === scope.id
            && scope.sessionRevision === this.sessionRevision
            && (!check.edits || scope.editRevision === this.editRevision)
            && (check.storage === false || scope.storageRevision === this.storageRevision);
    }

    /** Advance the scientific/navigation session and let only owner continue. */
    transition(owner?: OperationScope): OperationScope | null {
        this.sessionRevision += 1;
        this.latestByKind.clear();
        if (!owner) return null;
        const adopted = {
            ...owner,
            sessionRevision: this.sessionRevision,
            editRevision: this.editRevision,
            storageRevision: this.storageRevision,
        };
        this.latestByKind.set(adopted.kind, adopted.id);
        return adopted;
    }

    edit(): void {
        this.editRevision += 1;
    }

    externalStorageWrite(): void {
        this.storageRevision += 1;
        this.sessionRevision += 1;
        this.latestByKind.clear();
    }

    invalidate(kind: string): void { this.latestByKind.delete(kind); }

    get edits(): number { return this.editRevision; }
    get session(): number { return this.sessionRevision; }
    get storage(): number { return this.storageRevision; }
}

/**
 * Audit copies may see only the server campaign whose UUID is already owned by
 * their namespaced cache.  An empty copy must not adopt the server's latest
 * campaign merely because it is first in a global list.
 */
export function campaignsVisibleToCopy<T>(
    campaigns: readonly T[],
    copyId: string,
    cachedCampaignId: string | null,
    campaignId: (campaign: T) => string,
): T[] {
    if (copyId === 'main') return [...campaigns];
    if (!cachedCampaignId) return [];
    return campaigns.filter(campaign => campaignId(campaign) === cachedCampaignId);
}

function canonicalValue(value: unknown): unknown {
    if (Array.isArray(value)) return value.map(canonicalValue);
    if (value && typeof value === 'object') {
        return Object.fromEntries(Object.entries(value as Record<string, unknown>)
            .sort(([left], [right]) => left.localeCompare(right))
            .map(([key, item]) => [key, canonicalValue(item)]));
    }
    return value;
}

export function canonicalJson(value: unknown): string {
    return JSON.stringify(canonicalValue(value));
}

export type ExactRef = { kind: string; id: string; sha256: string };

export function exactRef(value: unknown): ExactRef | null {
    if (!value || typeof value !== 'object') return null;
    const candidate = value as Record<string, unknown>;
    const kind = String(candidate.kind || '');
    const id = String(candidate.id || '');
    const sha256 = String(candidate.sha256 || '');
    return kind && id && /^sha256:[0-9a-f]{64}$/.test(sha256) ? { kind, id, sha256 } : null;
}

export function sameExactRef(left: unknown, right: unknown): boolean {
    const a = exactRef(left), b = exactRef(right);
    return !!a && !!b && a.kind === b.kind && a.id === b.id && a.sha256 === b.sha256;
}

export type ExactRunBinding = {
    requestKey: string;
    campaign: { id: string; version: number; sha256: string };
    edgeId: string;
    specDigest: string;
    edgeSpecRef: ExactRef;
    edgeNetworkRef: ExactRef;
    complexTransformationRef: ExactRef;
    solventTransformationRef: ExactRef;
};

export type ExactOperationBinding = ExactRunBinding & {
    planNetworkJobId: string;
    planNetworkRef: ExactRef;
    preparedSystemRef: ExactRef;
    parentPoseRef: ExactRef;
    proposalPoseRef: ExactRef;
};

export function exactOperationBindingMatches(
    current: ExactOperationBinding | null,
    expected: ExactOperationBinding | null,
): boolean {
    return !!current && !!expected && canonicalJson(current) === canonicalJson(expected);
}

export function exactRunBindingMatches(data: Record<string, unknown>, expected: ExactRunBinding): boolean {
    const campaign = data.campaign_scientific_ref as Record<string, unknown> | undefined;
    return data.request_key === expected.requestKey
        && campaign?.kind === 'rbfe_campaign'
        && campaign?.id === expected.campaign.id
        && campaign?.version === expected.campaign.version
        && campaign?.sha256 === expected.campaign.sha256
        && data.edge_id === expected.edgeId
        && sameExactRef(data.edge_spec_ref, expected.edgeSpecRef)
        && sameExactRef(data.edge_network_ref, expected.edgeNetworkRef)
        && sameExactRef(data.complex_transformation_ref, expected.complexTransformationRef)
        && sameExactRef(data.solvent_transformation_ref, expected.solventTransformationRef);
}

export type ExactAggregateArm = ExactRunBinding & { expiresAt: number };

export function aggregateArmMatches(arm: ExactAggregateArm | null, expected: ExactRunBinding, now: number): boolean {
    if (!arm || arm.expiresAt < now) return false;
    const { expiresAt: _expiresAt, ...armedBinding } = arm;
    return canonicalJson(armedBinding) === canonicalJson(expected);
}

export const CHEMISTRY_DIMENSIONS = [
    'SCOPE',
    'ELEMENT',
    'CONNECTIVITY',
    'BOND_ORDER',
    'FORMAL_CHARGE',
    'STEREO',
    'RING_CYCLE_RANK',
    'UNMAPPED',
    'PROTONATION_TAUTOMER',
] as const;

export type ChemistryDimension = typeof CHEMISTRY_DIMENSIONS[number];
export type ChemistryVerdict = 'CONFIRMED' | 'CHANGED' | 'UNVERIFIED';
export type ChemistryEvidenceRow = {
    dimension: ChemistryDimension;
    verdict: ChemistryVerdict;
    summary: string;
    witnesses: Array<Record<string, unknown>>;
};
export type ChemistryEvidence = {
    schema_version: 'rbfe-chemistry-change.v1';
    verdict: ChemistryVerdict;
    full_heavy_atom_coverage: boolean;
    ledger: ChemistryEvidenceRow[];
};
export type ExecutionEligibility = {
    verdict: ChemistryVerdict;
    reasons: Array<Record<string, unknown>>;
};
export type ChemistryLedgerViewRow = {
    label: string;
    value: string;
    state: 'confirmed' | 'changed' | 'unverified';
};
export type ChemistryEvidenceView = {
    evidence: ChemistryEvidence | null;
    verdict: ChemistryVerdict;
    summary: string;
    ledger: ChemistryLedgerViewRow[];
};

const CHEMISTRY_VERDICTS = new Set<ChemistryVerdict>(['CONFIRMED', 'CHANGED', 'UNVERIFIED']);
const CHEMISTRY_DIMENSION_SET = new Set<string>(CHEMISTRY_DIMENSIONS);

function record(value: unknown): Record<string, unknown> | null {
    return !!value && typeof value === 'object' && !Array.isArray(value)
        ? value as Record<string, unknown>
        : null;
}

function chemistryVerdict(value: unknown): ChemistryVerdict | null {
    return typeof value === 'string' && CHEMISTRY_VERDICTS.has(value as ChemistryVerdict)
        ? value as ChemistryVerdict
        : null;
}

/**
 * Accept only the complete, versioned backend evidence contract. A partial or
 * novel shape cannot be interpreted safely and therefore remains UNVERIFIED.
 */
export function chemistryEvidenceFrom(value: unknown): ChemistryEvidence | null {
    const candidate = record(value);
    if (!candidate
        || candidate.schema_version !== 'rbfe-chemistry-change.v1'
        || typeof candidate.full_heavy_atom_coverage !== 'boolean') return null;
    const overall = chemistryVerdict(candidate.verdict);
    if (!overall || !Array.isArray(candidate.ledger)
        || candidate.ledger.length !== CHEMISTRY_DIMENSIONS.length) return null;
    const byDimension = new Map<ChemistryDimension, ChemistryEvidenceRow>();
    for (const raw of candidate.ledger) {
        const row = record(raw), verdict = chemistryVerdict(row?.verdict);
        const dimension = String(row?.dimension || '');
        if (!row || !verdict || !CHEMISTRY_DIMENSION_SET.has(dimension)
            || byDimension.has(dimension as ChemistryDimension)
            || typeof row.summary !== 'string' || !row.summary.trim()
            || !Array.isArray(row.witnesses)) return null;
        const witnesses = row.witnesses.map(record);
        if (witnesses.some(witness => !witness)) return null;
        byDimension.set(dimension as ChemistryDimension, {
            dimension: dimension as ChemistryDimension,
            verdict,
            summary: row.summary,
            witnesses: witnesses as Array<Record<string, unknown>>,
        });
    }
    if (byDimension.size !== CHEMISTRY_DIMENSIONS.length) return null;
    const ledger = CHEMISTRY_DIMENSIONS.map(dimension => byDimension.get(dimension)!);
    const verdicts = new Set(ledger.map(row => row.verdict));
    const derivedOverall: ChemistryVerdict = verdicts.has('UNVERIFIED')
        ? 'UNVERIFIED'
        : verdicts.has('CHANGED') ? 'CHANGED' : 'CONFIRMED';
    if (overall !== derivedOverall) return null;
    return {
        schema_version: 'rbfe-chemistry-change.v1',
        verdict: overall,
        full_heavy_atom_coverage: candidate.full_heavy_atom_coverage,
        ledger,
    };
}

export function executionEligibilityFrom(value: unknown): ExecutionEligibility | null {
    const candidate = record(value), verdict = chemistryVerdict(candidate?.verdict);
    if (!candidate || !verdict || !Array.isArray(candidate.reasons)) return null;
    const reasons = candidate.reasons.map(record);
    return reasons.some(reason => !reason) ? null : {
        verdict,
        reasons: reasons as Array<Record<string, unknown>>,
    };
}

function ledgerState(verdict: ChemistryVerdict): ChemistryLedgerViewRow['state'] {
    return verdict.toLowerCase() as ChemistryLedgerViewRow['state'];
}

/** Render only server-owned verdicts, summaries, and witnesses. */
export function chemistryEvidenceView(value: unknown): ChemistryEvidenceView {
    const evidence = chemistryEvidenceFrom(value);
    if (!evidence) {
        const ledger = CHEMISTRY_DIMENSIONS.map(dimension => ({
            label: dimension.replace(/_/g, ' '),
            value: 'UNVERIFIED · server chemistry evidence missing or malformed · WITNESS NONE',
            state: 'unverified' as const,
        }));
        return {
            evidence: null,
            verdict: 'UNVERIFIED',
            summary: `SERVER UNVERIFIED · 0 CHANGED · ${ledger.length} UNVERIFIED`,
            ledger,
        };
    }
    const ledger = evidence.ledger.map(row => ({
        label: row.dimension.replace(/_/g, ' '),
        value: `${row.verdict} · ${row.summary} · WITNESS ${row.witnesses.length ? canonicalJson(row.witnesses) : 'NONE'}`,
        state: ledgerState(row.verdict),
    }));
    const changed = evidence.ledger.filter(row => row.verdict === 'CHANGED').length;
    const unverified = evidence.ledger.filter(row => row.verdict === 'UNVERIFIED').length;
    return {
        evidence,
        verdict: evidence.verdict,
        summary: `SERVER ${evidence.verdict} · ${changed} CHANGED · ${unverified} UNVERIFIED`,
        ledger,
    };
}
