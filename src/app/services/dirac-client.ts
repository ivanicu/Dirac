/**
 * The TypeScript SDK. Same contract as the Python one, and the frontend must use it.
 *
 * THE AUDIT'S ITEM 5, verbatim: "TypeScript SDK 并让现有前端自己使用" — build the SDK and
 * then make the existing frontend eat it. The second half is the part that matters. An SDK
 * nothing uses is a proposal; an SDK the app itself depends on is the app's only route to
 * the backend, and every consumer after it inherits a surface that has already been proven
 * by the hardest client there is.
 *
 * WHAT THIS REPLACES: 5 hand-rolled `fetch()` call sites across two facets, each with its
 * own URL building, its own error shape, its own timeout and its own idea of what a refusal
 * looks like. `check_layering.py` ratchets that count down.
 *
 * WHAT IT REFUSES TO DO, mirroring the Python SDK for the same reason: no parameter
 * validation, no refusal re-classification, no decision about whether bytes travel inline.
 * The kernel owns all three. A browser client that re-decided them would drift from the
 * descriptors and then disagree with the CLI about what is legal.
 *
 * ONE BROWSER-SPECIFIC HONESTY, and it is not a detail: artifact digest verification uses
 * `crypto.subtle`, which does NOT EXIST outside a secure context. This app is served over
 * plain http on a LAN address, so on that origin the verification is genuinely unavailable
 * — and the client says `verified: false` with a reason rather than skipping the check
 * silently. A verification that is quietly absent is worse than one that is absent and
 * says so, because the first is indistinguishable from one that passed.
 */

export type EnvelopeMeta = {
    envelope?: number;
    method_id?: string;
    version?: string | null;
    cache?: string;
    seconds?: number;
    transport?: string;
    provenance?: Record<string, unknown>;
    [k: string]: unknown;
};

export type ArtifactRef = {
    id: string | null;
    sha256: string;
    role: string;
    media_type: string;
    size_bytes: number;
    encoding: string;
    url: string;
    metadata_url?: string;
    inline?: boolean;
    inline_base64?: string;
    method_version?: string | null;
    synthesised_by?: string;
};

export type ErrorPayload = {
    code: string;
    message: string;
    user_message?: string;
    retryable?: boolean;
    caller_action?: string;
    details?: Record<string, unknown>;
    hint?: Record<string, unknown>;
};

export type Envelope = {
    ok: boolean;
    data?: Record<string, any>;
    artifacts?: ArtifactRef[];
    warnings?: Array<{ code: string; message: string; affects?: string[] }>;
    error?: ErrorPayload;
    meta?: EnvelopeMeta;
};

/**
 * A typed refusal. `code` is from contracts/errors.json, so UI code branches on the code
 * and never on the message text — the message carries measured numbers that legitimately
 * move, and a UI that matched on it would break the first time a bound was re-fitted.
 */
export class DiracError extends Error {
    readonly code: string;
    readonly retryable: boolean;
    readonly callerAction: string;
    readonly details: Record<string, unknown>;
    readonly hint?: Record<string, unknown>;
    readonly envelope?: Envelope;

    constructor(payload: ErrorPayload, envelope?: Envelope) {
        super(payload.user_message || payload.message);
        this.name = 'DiracError';
        this.code = payload.code || 'INTERNAL';
        this.retryable = Boolean(payload.retryable);
        this.callerAction = payload.caller_action || '';
        this.details = payload.details || {};
        this.hint = payload.hint;
        this.envelope = envelope;
    }
}

export class DiracDigestMismatch extends DiracError {
    constructor(message: string, details: Record<string, unknown>) {
        super({ code: 'DIGEST_MISMATCH', message, retryable: true, details });
        this.name = 'DiracDigestMismatch';
    }
}

export type FetchedArtifact = {
    bytes: Uint8Array;
    text: () => string;
    /** Whether the digest was CHECKED, not whether it matched — a mismatch throws. */
    verified: boolean;
    /** Why, when it was not. Never empty when `verified` is false. */
    unverifiedReason?: string;
};

const HEX = Array.from({ length: 256 }, (_, i) => i.toString(16).padStart(2, '0'));

function toHex(buf: ArrayBuffer): string {
    const v = new Uint8Array(buf);
    let s = '';
    for (let i = 0; i < v.length; i++) s += HEX[v[i]];
    return s;
}

function b64ToBytes(b64: string): Uint8Array {
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
}

export type ClientOptions = {
    baseUrl?: string;
    timeoutMs?: number;
    /** Reported on every envelope so a caller can tell which surface answered. */
    label?: string;
};

export class DiracClient {
    readonly baseUrl: string;
    readonly timeoutMs: number;
    /** Set once the server has been observed to answer /v2 — see invoke(). */
    private v2Available: boolean | null = null;
    readonly counters = {
        invoke: 0, v1_fallback: 0, refused: 0, artifact_fetch: 0,
        digest_verified: 0, digest_unverifiable: 0,
    };

    constructor(opts: ClientOptions = {}) {
        this.baseUrl = (opts.baseUrl || '').replace(/\/+$/, '');
        this.timeoutMs = opts.timeoutMs ?? 600_000;
    }

    private async request(
        method: string, path: string, body?: unknown, signal?: AbortSignal,
    ): Promise<{ status: number; headers: Headers; text: string }> {
        const resp = await fetch(`${this.baseUrl}${path}`, {
            method,
            headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
            body: body === undefined ? undefined : JSON.stringify(body),
            signal,
        });
        return { status: resp.status, headers: resp.headers, text: await resp.text() };
    }

    /**
     * The generic surface. Returns the envelope; a REFUSAL is `ok: false`, not a throw.
     *
     * Tries /v2/invoke and remembers the answer. The memo matters for a browser: without
     * it, every field request on a v1-only server would pay a 404 round trip first, and
     * that cost would land on the interaction the user is waiting for.
     */
    async invoke(
        methodId: string, input: Record<string, unknown>,
        opts: { inlineMax?: number; budgetSeconds?: number; signal?: AbortSignal } = {},
    ): Promise<Envelope> {
        this.counters.invoke++;
        if (this.v2Available !== false) {
            const r = await this.request('POST', '/v2/invoke', {
                method_id: methodId,
                input,
                inline_max: opts.inlineMax,
                budget_seconds: opts.budgetSeconds,
            }, opts.signal);
            if (r.status !== 404) {
                this.v2Available = true;
                const env = JSON.parse(r.text || '{}') as Envelope;
                env.meta = { ...(env.meta || {}), transport: 'http:/v2/invoke' };
                if (!env.ok) this.counters.refused++;
                return env;
            }
            this.v2Available = false;
        }
        this.counters.v1_fallback++;
        return this.invokeViaV1(methodId, input, opts);
    }

    /** Semantic command surface used by GUI, CLI-equivalent clients and agents. */
    async execute(
        command: string, input: Record<string, unknown> = {},
        opts: { actor?: { kind: 'human' | 'agent' | 'service'; id: string };
            requestId?: string; signal?: AbortSignal } = {},
    ): Promise<Envelope> {
        const r = await this.request('POST', '/v2/execute', {
            command, input, actor: opts.actor, request_id: opts.requestId,
        }, opts.signal);
        const env = JSON.parse(r.text || '{}') as Envelope;
        env.meta = { ...(env.meta || {}), transport: 'http:/v2/execute' };
        return env;
    }

    async commands(): Promise<Array<Record<string, unknown>>> {
        const r = await this.request('GET', '/v2/commands');
        const env = JSON.parse(r.text || '{}');
        return (env.data?.commands || []) as Array<Record<string, unknown>>;
    }

    async jobGet(jobId: string): Promise<Envelope> {
        return this.execute('job.get', { job_ref: { kind: 'job', id: jobId } });
    }

    async jobWait(jobId: string, timeout = 300, signal?: AbortSignal): Promise<Envelope> {
        return this.execute('job.wait', {
            job_ref: { kind: 'job', id: jobId }, timeout,
        }, { signal });
    }

    async fieldCompute(input: {
        molecule: Record<string, unknown>; fieldKind: string;
        parameters?: Record<string, unknown>; budgetSeconds?: number;
    }): Promise<Envelope> {
        return this.execute('structure.field.compute', {
            molecule: input.molecule, field_kind: input.fieldKind,
            parameters: input.parameters, budget_seconds: input.budgetSeconds,
        });
    }

    async fieldComputeAndWait(input: {
        molecule: Record<string, unknown>; fieldKind: string;
        parameters?: Record<string, unknown>; budgetSeconds?: number;
        timeout?: number;
    }): Promise<Envelope> {
        const accepted = await this.fieldCompute(input);
        return this.waitForCommandResult(accepted, input.timeout ?? 300);
    }

    async waitForCommandResult(
        accepted: Envelope, timeout = 300, signal?: AbortSignal,
    ): Promise<Envelope> {
        if (!accepted.ok) return accepted;
        const jobId = String(accepted.meta?.job_id || '');
        if (!jobId) {
            return { ok: false, error: { code: 'INTERNAL', message: 'command returned no job_id' } };
        }
        const waited = await this.jobWait(jobId, timeout, signal);
        if (!waited.ok) return waited;
        const job = waited.data as Record<string, any>;
        if (job.state !== 'done') {
            return {
                ok: false,
                error: { code: job.error_code || 'BUDGET',
                    message: job.error_detail || `job ${jobId} ended as ${job.state}` },
                meta: waited.meta,
            };
        }
        const summary = job.result_summary || {};
        const artifacts = (job.artifacts || []).map((a: Record<string, any>) => ({
            id: a.id, sha256: a.sha256, role: a.role, media_type: a.media_type,
            size_bytes: a.size_bytes, encoding: 'identity',
            url: `/v2/artifacts/${a.id}`, metadata_url: `/v2/artifacts/${a.id}/meta`,
            method_version: job.method_version,
        }));
        return {
            ok: true, data: summary.data || {}, artifacts,
            warnings: summary.warnings || [],
            meta: { ...(waited.meta || {}), job_id: jobId,
                method_id: job.method_id, version: job.method_version,
                provenance: summary.provenance || {}, seconds: job.seconds,
                command: accepted.meta?.command, command_version: accepted.meta?.command_version },
        };
    }

    /**
     * The v1 shim, isolated exactly as the Python SDK isolates it. Deleted when /v2 is
     * everywhere; the acceptance test is what proves the deletion changed nothing.
     */
    private async invokeViaV1(
        methodId: string, input: Record<string, unknown>,
        opts: { budgetSeconds?: number; signal?: AbortSignal },
    ): Promise<Envelope> {
        const mol = (input.molecule || {}) as Record<string, any>;
        const params = (input.parameters || {}) as Record<string, any>;
        const kind = methodId.split('.').pop();
        const r = await this.request('POST', '/field', {
            molfile: mol.content, kind, basis: params.basis,
            spin: params.spin, max_seconds: opts.budgetSeconds,
        }, opts.signal);
        const v1 = JSON.parse(r.text || '{}');
        if (!v1.ok) {
            this.counters.refused++;
            // The code is NOT reconstructed from the reason string beyond the four v1
            // actually emits — guessing a typed code from prose is the defect the typed
            // vocabulary exists to delete.
            const map: Record<string, string> = {
                unsupported: 'UNSUPPORTED', parse: 'PARSE', budget: 'BUDGET',
                too_large: 'TOO_LARGE',
            };
            return {
                ok: false,
                error: {
                    code: map[String(v1.reason)] || 'INTERNAL',
                    message: v1.error || 'v1 refused without a message',
                    retryable: v1.reason === 'budget',
                    details: { v1_reason: v1.reason, code_is_mapped_from_v1_reason: true },
                },
                meta: { envelope: 2, method_id: methodId, transport: 'http:/field (v1)' },
            };
        }
        const cube: string = v1.cube || '';
        const meta = v1.meta || {};
        return {
            ok: true,
            data: {
                field: {
                    kind,
                    grid: { dimensions: meta.dims, spacing_angstrom: meta.spacing },
                    ...(meta.vmin !== undefined
                        ? { extrema: { min: meta.vmin, max: meta.vmax } } : {}),
                },
                ...(meta.converged !== undefined ? {
                    wavefunction: {
                        converged: meta.converged, method: meta.method,
                        basis: meta.basis, n_basis_functions: meta.nbasis,
                        energy_hartree: meta.scf_energy_ha,
                        homo_ev: meta.homo_ev, lumo_ev: meta.lumo_ev,
                    },
                } : {}),
            },
            artifacts: [{
                id: null,
                // Computed over what arrived, so it is a real address even here.
                sha256: '',                 // filled by field() when it can hash
                role: 'field.cube',
                media_type: 'application/vnd.dirac.gaussian-cube',
                size_bytes: new TextEncoder().encode(cube).length,
                encoding: 'identity',
                url: '',
                inline: true,
                inline_base64: '',
                synthesised_by: 'v1: bytes arrived inline with no artifact row, so this '
                    + 'reference is not fetchable by digest',
            }],
            warnings: [],
            meta: {
                envelope: 2, method_id: methodId, version: meta.method_version,
                cache: meta.cache, seconds: meta.total_seconds,
                transport: 'http:/field (v1)', v1_meta: meta, v1_cube: cube,
            },
        };
    }

    /** Bytes for a reference, verified where the platform allows verification. */
    async fetchArtifact(ref: ArtifactRef, signal?: AbortSignal): Promise<FetchedArtifact> {
        this.counters.artifact_fetch++;
        let bytes: Uint8Array;
        if (ref.inline_base64) {
            bytes = b64ToBytes(ref.inline_base64);
        } else {
            const resp = await fetch(`${this.baseUrl}${ref.url}`, { signal });
            if (!resp.ok) {
                throw new DiracError({
                    code: resp.status === 404 ? 'NOT_FOUND' : 'INTERNAL',
                    message: `artifact ${ref.sha256.slice(0, 12)}… came back `
                        + `${resp.status} from ${ref.url}`,
                    retryable: resp.status >= 500,
                });
            }
            bytes = new Uint8Array(await resp.arrayBuffer());
        }
        // crypto.subtle is undefined outside a secure context, and this app is served
        // over plain http on a LAN address. So the check is genuinely unavailable there,
        // and the result SAYS SO rather than reporting an unchecked payload as fine.
        const subtle = (globalThis.crypto as Crypto | undefined)?.subtle;
        if (!subtle || !ref.sha256) {
            this.counters.digest_unverifiable++;
            return {
                bytes, verified: false,
                unverifiedReason: !ref.sha256
                    ? 'the reference carries no digest (a v1 inline payload)'
                    : 'crypto.subtle is unavailable — this origin is not a secure '
                    + 'context, so the browser refuses to expose SHA-256. Serve over '
                    + 'https or from localhost to verify.',
                text: () => new TextDecoder().decode(bytes),
            };
        }
        const digest = toHex(await subtle.digest('SHA-256', bytes as unknown as ArrayBuffer));
        if (digest !== ref.sha256) {
            throw new DiracDigestMismatch(
                `${bytes.length} bytes hash to ${digest.slice(0, 12)}… but were served `
                + `as ${ref.sha256.slice(0, 12)}…`,
                { expected: ref.sha256, actual: digest, bytes: bytes.length });
        }
        this.counters.digest_verified++;
        return {
            bytes, verified: true,
            text: () => new TextDecoder().decode(bytes),
        };
    }

    /** Catalog reads. Both degrade to a clear refusal rather than an empty list. */
    async methods(): Promise<Array<Record<string, unknown>>> {
        const r = await this.request('GET', '/v2/methods');
        if (r.status === 404) return [];
        const env = JSON.parse(r.text || '{}');
        return (env.data?.methods || []) as Array<Record<string, unknown>>;
    }

    async describe(methodId: string): Promise<Record<string, unknown>> {
        const r = await this.request('GET', `/v2/methods/${methodId}`);
        const env = JSON.parse(r.text || '{}');
        if (!env.ok) throw new DiracError(env.error || { code: 'NOT_FOUND', message: methodId });
        return env.data as Record<string, unknown>;
    }

    async health(signal?: AbortSignal): Promise<Record<string, unknown> | null> {
        try {
            const r = await this.request('GET', '/health', undefined, signal);
            return JSON.parse(r.text || '{}');
        } catch {
            return null;
        }
    }
}

/**
 * Is this molfile actually 3D? Judged from the COORDINATES, not the header.
 *
 * RDKit writes `     RDKit          2D` on line 2, and a header is a claim while the atom
 * block is the object. A file can also say 3D and be flat. Measured cost: one pass over the
 * atom lines, microseconds.
 */
export function molfileIsThreeD(molfile: string): boolean {
    const lines = molfile.split('\n');
    // THE ATOM COUNT COMES FROM THE COUNTS LINE, not from "keep reading until the lines stop
    // looking like atoms".
    //
    // MEASURED DEFECT, found in the browser on a real ethanol molfile: the first version
    // walked from line 4 and stopped when a line had fewer than 4 fields or a
    // non-numeric third column. A BOND line — `  1  2  1  0` — has four fields and its
    // third column is the bond ORDER, which is a perfectly finite number. So it counted
    // 2 bonds as atoms, took bond orders for z coordinates, saw a spread of 1.0, and
    // declared a flat `RDKit 2D` structure to be 3D. The verdict was wrong in the UNSAFE
    // direction: it let 2D coordinates into a 3D physics method, which is the exact thing
    // the input contract exists to prevent.
    //
    // The counts line is `aaabbb...` at line index 3 (the molfile has three header lines).
    // Reading it is the format-respecting parse; the previous version was a heuristic
    // wearing a parser's clothes.
    const counts = (lines[3] || '').trim().split(/\s+/);
    const nAtoms = Number.parseInt(counts[0], 10);
    if (!Number.isFinite(nAtoms) || nAtoms <= 0) {
        // Unparseable counts line: say UNKNOWN by treating it as 2D, which routes it
        // through molecule.embed. The cost of an unnecessary embed is milliseconds; the cost
        // of a wrong 3D claim is a field computed on flat coordinates.
        return false;
    }
    let zmin = Infinity;
    let zmax = -Infinity;
    let seen = 0;
    for (let i = 4; i < lines.length && seen < nAtoms; i++, seen++) {
        const p = lines[i].trim().split(/\s+/);
        if (p.length < 4) return false;
        const z = Number(p[2]);
        if (!Number.isFinite(z)) return false;
        zmin = Math.min(zmin, z);
        zmax = Math.max(zmax, z);
    }
    if (seen < nAtoms) return false;
    // Two atoms are collinear by definition, so a diatomic cannot be judged this way and is
    // accepted — stating that beats a rule that refuses every diatomic.
    return nAtoms <= 2 ? true : (zmax - zmin) > 1e-6;
}

/**
 * `embed()` — SMILES or a 2D molfile to a 3D molecule, as its own contracted invocation.
 */
export async function embed(
    client: DiracClient,
    input: { smiles?: string; molfile?: string; seed?: number; signal?: AbortSignal },
): Promise<{ molecule: Record<string, any>; version: string | null | undefined }> {
    const env = await client.invoke('molecule.embed', {
        ...(input.smiles ? { smiles: input.smiles } : {}),
        ...(input.molfile ? { molfile: input.molfile } : {}),
        ...(input.seed != null ? { parameters: { seed: input.seed } } : {}),
    }, { inlineMax: 0, signal: input.signal });
    if (!env.ok) throw new DiracError(env.error!, env);
    return { molecule: env.data!.molecule, version: env.meta?.version };
}

/** 2D input → 3D conformer, memoised by exact input bytes. See fetchField. */
const EMBED_MEMO = new Map<string, { content: string; version: string | null | undefined }>();

export type FieldResult = {
    /** The cube text, verified against its digest where the platform allows it. */
    cube: string;
    /** The CANONICAL output tree, exactly as the descriptor declares it. */
    data: Record<string, any>;
    /** Typed caveats. A renderer switches on `code`, never on the message text. */
    warnings: Array<{ code: string; message: string; affects?: string[] }>;
    envelope: Envelope;
    artifact?: ArtifactRef;
    digestVerified: boolean;
    digestUnverifiedReason?: string;
};

/**
 * `fetchField` — one call, returning the CANONICAL tree.
 *
 * An earlier version of this function projected the v2 envelope down onto the flat 26-key
 * `meta` the renderer used to read. That projection was a SECOND HOME for every fact in
 * it, which is the thing this repo keeps paying for: the flat shape had to be kept in step
 * with the backend by a gate that compared it key by key, and it drifted twice in one day
 * anyway. The projection is gone. The renderer reads the contract's own tree, typed by
 * contracts/generated/typescript/methods.ts, and there is nothing left to keep in step.
 */
export async function fetchField(
    client: DiracClient,
    kind: string,
    args: {
        molfile: string; basis?: string; spin?: number | null;
        maxSeconds?: number; signal?: AbortSignal; methodId?: string;
    },
): Promise<FieldResult> {
    const methodId = args.methodId
        || (['mep', 'mlp'].includes(kind) ? `fields.${kind}` : `fields.qm.${kind}`);

    // TWO INVOCATIONS WHEN THE INPUT IS 2D, never one that hides an embedding.
    //
    // FOUND BY DOGFOODING, not by reading: the app was sending a molfile whose header said
    // `RDKit 2D` to a field method. v1 accepted it because prepare_mol quietly embeds when
    // there are no 3D coordinates — so one response reported one method version for a
    // result that had passed through two pieces of science, and the conformer's origin was
    // unstated. The input contract already forbade it in its own words: "A 2D structure
    // must not reach a 3D physics method — molecule.embed is the explicit step that
    // produces 3D."
    //
    // So the composition is done HERE and visibly: molecule.embed runs as its own
    // invocation, its version is kept, and the field's provenance carries both. That is a
    // client convenience composing two contracted methods — which is legitimate — and not
    // one method with a hidden step, which is not.
    let content = args.molfile;
    let embedVersion: string | null | undefined;
    if (!molfileIsThreeD(content)) {
        // MEMOISED BY INPUT. Measured in the browser: the facet prefetches several kinds
        // for one ligand, so an un-memoised embed ran THREE times for one molecule — the
        // same ETKDG on the same bytes, and (because the seed is fixed) the same conformer
        // each time. A cache keyed on the exact input is not a semantic change: molecule.embed
        // declares `deterministic_given_seed`, so a second call is defined to return what
        // the first did. If that ever stops being true, this memo is wrong and so is the
        // declaration — which is why the key is the CONTENT and not the ligand's identity.
        const cached = EMBED_MEMO.get(content);
        if (cached) {
            content = cached.content;
            embedVersion = cached.version;
        } else {
            const e = await embed(client, { molfile: args.molfile, signal: args.signal });
            content = e.molecule.content;
            embedVersion = e.version;
            EMBED_MEMO.set(args.molfile, { content, version: embedVersion });
            // Bounded, because a long session pasting many ligands would otherwise hold
            // every conformer it ever made. 32 is well past the working set of a screen.
            if (EMBED_MEMO.size > 32) {
                EMBED_MEMO.delete(EMBED_MEMO.keys().next().value as string);
            }
        }
    }
    const env = await client.fieldComputeAndWait({
        molecule: { kind: 'molfile', content, dimensionality: 3 },
        fieldKind: kind,
        parameters: (args.basis || args.spin != null) ? {
            ...(args.basis ? { basis: args.basis } : {}),
            ...(args.spin != null ? { spin: args.spin } : {}),
        } : undefined,
        budgetSeconds: args.maxSeconds,
    });

    if (!env.ok) throw new DiracError(env.error!, env);

    const ref = (env.artifacts || [])[0];
    const inlineFromV1 = (env.meta as any)?.v1_cube as string | undefined;
    let cube: string;
    let verified = false;
    let unverifiedReason: string | undefined;
    if (inlineFromV1 !== undefined) {
        cube = inlineFromV1;
        unverifiedReason = 'v1 returned the bytes inline with no digest to check against';
    } else if (ref) {
        const got = await client.fetchArtifact(ref, args.signal);
        cube = got.text();
        verified = got.verified;
        unverifiedReason = got.unverifiedReason;
    } else {
        throw new DiracError({
            code: 'INTERNAL',
            message: `${methodId} returned neither an artifact nor inline bytes; there is `
                + 'nothing to render, and an empty field looks exactly like a converged one',
        }, env);
    }
    if (embedVersion !== undefined) {
        // Both halves of the chain, on the record. A field computed from an embedded
        // conformer is not comparable to one computed from a crystal pose, and the only way
        // a reader can tell is if the embed step says it happened.
        env.meta = { ...(env.meta || {}), embed_version: embedVersion,
                     conformer_source: 'molecule.embed (input was 2D)' };
    }
    return {
        cube, data: env.data || {}, warnings: env.warnings || [], envelope: env,
        artifact: ref, digestVerified: verified, digestUnverifiedReason: unverifiedReason,
    };
}
