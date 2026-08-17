import { describe,expect,it } from '@jest/globals';
import { T4lBenchmarkResults,benchmarkEdgeResult,benchmarkMetrics } from './workbench-benchmark-results';

describe('T4L eight-ligand FEP benchmark results',()=>{
    it('keeps eight unique identities with complete calculated and experimental results',()=>{
        expect(T4lBenchmarkResults).toHaveLength(8);
        expect(new Set(T4lBenchmarkResults.map(row=>row.id)).size).toBe(8);
        T4lBenchmarkResults.forEach(row=>{
            expect(row.smiles).toBeTruthy();
            expect(Number.isFinite(row.experimentalDg)).toBe(true);
            expect(Number.isFinite(row.calculatedDg)).toBe(true);
            expect(row.experimentalSigma).toBeGreaterThan(0);
            expect(row.calculatedSigma).toBeGreaterThan(0);
        });
    });

    it('derives selected-edge relative free energy consistently from endpoint results',()=>{
        const result=benchmarkEdgeResult('T4L-BEN','T4L-TOL');
        expect(result.experimentalDdg).toBeCloseTo(-.33,8);
        expect(result.calculatedDdg).toBeCloseTo(1.08,8);
        expect(result.residual).toBeCloseTo(1.41,8);
    });

    it('reproduces the eight-ligand absolute-energy error summary',()=>{
        const metrics=benchmarkMetrics();
        expect(metrics.mue).toBeCloseTo(1.225,3);
        expect(metrics.rmse).toBeCloseTo(1.400,3);
        expect(metrics.maxAbsError).toBeCloseTo(2.49,2);
    });
});
