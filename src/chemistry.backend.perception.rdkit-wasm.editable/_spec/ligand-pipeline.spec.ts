/**
 * Violation witnesses for the V2000 counts line.
 *
 * This line has been written wrong three times: two builders on 2026-08-10,
 * then again when the pipeline dedup merged them. Every previous fix was a
 * comment. This is the check, so the fourth time is a red test instead of six
 * dead fields.
 */
import { countsLine } from '../ligand-pipeline';

describe('V2000 counts line', () => {
    it('places V2000 at its fixed column (8 zero fields, not 9)', () => {
        const line = countsLine(22, 22);
        // Column 34 begins the 6-char version field: "vvvvvv" per the CTfile spec.
        expect(line.length).toBe(39);
        expect(line.slice(33)).toBe(' V2000');
        expect(line).toBe(' 22 22  0  0  0  0  0  0  0  0999 V2000');
    });

    it('is what desktop RDKit accepts — the 9-field variant is the regression', () => {
        const broken = ' 22 22  0  0  0  0  0  0  0  0  0999 V2000';
        expect(countsLine(22, 22)).not.toBe(broken);
        // The witness: the broken form shifts the version off column 34, which
        // is exactly what makes MolFromMolBlock report an invalid CTAB version.
        expect(broken.slice(33)).not.toBe(' V2000');
    });

    it('right-aligns counts in 3-column fields', () => {
        expect(countsLine(5, 4).slice(0, 6)).toBe('  5  4');
        expect(countsLine(120, 119).slice(0, 6)).toBe('120119');
    });

    it('refuses to silently overflow the 3-column field', () => {
        expect(() => countsLine(1000, 10)).toThrow(/cannot express/);
        expect(() => countsLine(10, 1000)).toThrow(/cannot express/);
        expect(() => countsLine(999, 999)).not.toThrow();
    });
});
