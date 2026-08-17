export type BenchmarkResult = {
    id: string;
    name: string;
    smiles: string;
    experimentalDg: number;
    experimentalSigma: number;
    calculatedDg: number;
    calculatedSigma: number;
};

export const T4lBenchmarkResults: BenchmarkResult[] = [
    { id: 'T4L-BEN',name: 'BENZENE',smiles: 'c1ccccc1',experimentalDg: -5.19,experimentalSigma: .16,calculatedDg: -7.68,calculatedSigma: .22 },
    { id: 'T4L-TOL',name: 'TOLUENE',smiles: 'Cc1ccccc1',experimentalDg: -5.52,experimentalSigma: .04,calculatedDg: -6.60,calculatedSigma: .24 },
    { id: 'T4L-OXY',name: 'O-XYLENE',smiles: 'Cc1ccccc1C',experimentalDg: -4.60,experimentalSigma: .06,calculatedDg: -2.90,calculatedSigma: .38 },
    { id: 'T4L-PXY',name: 'P-XYLENE',smiles: 'Cc1ccc(C)cc1',experimentalDg: -4.67,experimentalSigma: .06,calculatedDg: -4.98,calculatedSigma: .30 },
    { id: 'T4L-ETB',name: 'ETHYLBENZENE',smiles: 'CCc1ccccc1',experimentalDg: -5.76,experimentalSigma: .07,calculatedDg: -6.95,calculatedSigma: .25 },
    { id: 'T4L-BZF',name: 'BENZOFURAN',smiles: 'c1ccc2occc2c1',experimentalDg: -5.46,experimentalSigma: .03,calculatedDg: -7.21,calculatedSigma: .26 },
    { id: 'T4L-IDN',name: 'INDENE',smiles: 'c1ccc2c(c1)CC=C2',experimentalDg: -5.13,experimentalSigma: .01,calculatedDg: -5.87,calculatedSigma: .40 },
    { id: 'T4L-IDL',name: 'INDOLE',smiles: 'c1ccc2[nH]ccc2c1',experimentalDg: -4.89,experimentalSigma: .06,calculatedDg: -4.35,calculatedSigma: .32 },
];

export function benchmarkResult(id: string): BenchmarkResult {
    const result=T4lBenchmarkResults.find(row=>row.id===id);
    if (!result) throw new Error(`Unknown T4L benchmark compound ${id}`);
    return result;
}

export function benchmarkEdgeResult(leftId: string,rightId: string) {
    const left=benchmarkResult(leftId),right=benchmarkResult(rightId);
    const experimentalDdg=right.experimentalDg-left.experimentalDg;
    const calculatedDdg=right.calculatedDg-left.calculatedDg;
    return {
        experimentalDdg,
        calculatedDdg,
        residual: calculatedDdg-experimentalDdg,
        experimentalSigma: Math.hypot(left.experimentalSigma,right.experimentalSigma),
        calculatedSigma: Math.hypot(left.calculatedSigma,right.calculatedSigma),
    };
}

export function benchmarkMetrics() {
    const errors=T4lBenchmarkResults.map(row=>row.calculatedDg-row.experimentalDg);
    return {
        mue: errors.reduce((sum,error)=>sum+Math.abs(error),0)/errors.length,
        rmse: Math.sqrt(errors.reduce((sum,error)=>sum+error*error,0)/errors.length),
        maxAbsError: Math.max(...errors.map(Math.abs)),
    };
}
