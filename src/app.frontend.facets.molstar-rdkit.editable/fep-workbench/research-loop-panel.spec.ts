import { describe, expect, it, jest } from '@jest/globals';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { ResearchLoopClient } from './research-loop-client';
import type { DiracClient, Envelope } from '../../app/services/dirac-client';

const here = __dirname;

class FakeClient {
    calls: Array<{ command: string; input: Record<string, unknown> }> = [];
    envelopes = new Map<string, Envelope>();
    artifact = '{"summary":"bounded"}';

    async execute(command: string, input: Record<string, unknown> = {}): Promise<Envelope> {
        this.calls.push({ command, input });
        return this.envelopes.get(command) || { ok: true, data: {} };
    }

    async fetchArtifact(): Promise<{ text: () => string; bytes: Uint8Array; verified: boolean }> {
        return { text: () => this.artifact, bytes: new Uint8Array(), verified: true };
    }
}

describe('Research Loop Drawer contract', () => {
    it('uses only the six attachment-defined public commands and never exposes advance', async () => {
        const fake = new FakeClient();
        fake.envelopes.set('ai.provider.list', { ok: true, data: { profiles: [] } });
        fake.envelopes.set('program.list', { ok: true, data: { programs: [] } });
        fake.envelopes.set('research.loop.get', { ok: true, data: { run_ref: { kind: 'run', id: 'r' } } });
        const client = new ResearchLoopClient(fake as unknown as DiracClient);
        await client.providers(); await client.programs(); await client.get('r');
        const source = readFileSync(resolve(here, 'research-loop-client.ts'), 'utf8');
        for (const command of [
            'ai.provider.list', 'research.loop.create', 'research.loop.get',
            'research.loop.approve', 'research.loop.reject', 'research.loop.control',
        ]) expect(source).toContain(`'${command}'`);
        expect(source).not.toContain('research.loop.advance');
        expect(fake.calls.map(row => row.command)).toEqual([
            'ai.provider.list', 'program.list', 'research.loop.get',
        ]);
    });

    it('fetches referenced context/proposal Artifacts rather than trusting command prose', async () => {
        const fake = new FakeClient();
        fake.artifact = '{"summary":"model proposal, not evidence"}';
        fake.fetchArtifact = jest.fn(fake.fetchArtifact.bind(fake));
        const client = new ResearchLoopClient(fake as unknown as DiracClient);
        const proposal = await client.proposal({
            kind: 'artifact', id: '00000000-0000-4000-8000-000000000001',
            sha256: 'sha256:' + 'a'.repeat(64),
        });
        expect(proposal?.summary).toBe('model proposal, not evidence');
        expect(fake.fetchArtifact).toHaveBeenCalledTimes(1);
    });

    it('contains the required information architecture, status badges and exact CTAs', () => {
        const panel = readFileSync(resolve(here, 'research-loop-panel.ts'), 'utf8');
        for (const heading of [
            'GOAL', 'WHAT CHANGED', 'CURRENT DRAFT HYPOTHESES',
            'RECOMMENDED NEXT ACTION', 'APPROVAL · EXACT CONSEQUENCES',
            'BUDGET', 'ATTENTION', 'TIMELINE',
        ]) expect(panel).toContain(heading);
        for (const status of [
            'AI DRAFT', 'METHOD RESULT · COMPLETED UNVALIDATED', 'TYPED EVIDENCE',
            'HUMAN ATTESTATION', 'SYSTEM STATE', 'STALE',
        ]) expect(panel).toContain(status);
        for (const cta of [
            'APPROVE EXACT ACTION', 'REJECT', 'REVISE GOAL', 'CHANGE PROVIDER', 'OPEN FEP REVIEW',
        ]) expect(panel).toContain(cta);
        expect(panel).toContain("['resume', 'cancel']");
        expect(panel).toContain("['retry', 'pause', 'cancel']");
        expect(panel).not.toContain('.innerHTML');
        expect(panel).toContain("role', 'status");
        expect(panel).toContain("aria-live', 'polite");
        expect(panel).toContain("Executed automatically under Ivan's loop grant");
    });

    it('keeps the drawer reachable at 960px and prevents horizontal overflow', () => {
        const css = readFileSync(resolve(here, 'research-loop-panel.css'), 'utf8');
        expect(css).toContain('@media (max-width: 960px)');
        expect(css).toContain('max-width: 100vw');
        expect(css).toContain('overflow: hidden auto');
        const shell = readFileSync(resolve(here, 'workbench-shell.ts'), 'utf8');
        expect(shell).toContain('research-loop-toggle');
        expect(shell).toContain('research-loop-drawer');
    });
});
