import { describe,expect,it,jest } from '@jest/globals';
import type { ReviewPose } from './pose-reviewer';

jest.mock('three/examples/jsm/controls/OrbitControls.js',()=>({ OrbitControls: class {} }));

const { HARD_CLASH_DISTANCE_ANGSTROM,NEAR_CLASH_DISTANCE_ANGSTROM,poseGeometryBlockers,poseGeometryRisk }=require('./pose-reviewer') as typeof import('./pose-reviewer');

const pose=(distance:number|undefined):Pick<ReviewPose,'minimum_heavy_atom_distance_angstrom'>=>({
    minimum_heavy_atom_distance_angstrom: distance,
});

describe('FEP pose geometry risk',()=>{
    it('keeps hard and near clashes as separate acceptance-blocking states',()=>{
        expect(poseGeometryRisk(pose(HARD_CLASH_DISTANCE_ANGSTROM-.001))).toBe('hard-clash');
        expect(poseGeometryRisk(pose(HARD_CLASH_DISTANCE_ANGSTROM))).toBe('near-clash');
        expect(poseGeometryRisk(pose(1.636))).toBe('near-clash');
        expect(poseGeometryRisk(pose(NEAR_CLASH_DISTANCE_ANGSTROM))).toBe('near-clash');
        expect(poseGeometryRisk(pose(NEAR_CLASH_DISTANCE_ANGSTROM+.001))).toBe('clear');
        expect(poseGeometryRisk(pose(undefined))).toBe('clear');
    });

    it('classifies every member without dropping an eight-pose campaign',()=>{
        const campaign=[2.83,2.78,2.78,2.78,1.636,2.23,2.06,2.19].map(distance=>pose(distance));
        const risks=campaign.map(poseGeometryRisk);
        expect(risks).toHaveLength(8);
        expect(risks.filter(risk=>risk==='near-clash')).toHaveLength(1);
        expect(risks.filter(risk=>risk==='clear')).toHaveLength(7);
        expect(poseGeometryBlockers(campaign)).toEqual({ hardClashes: 0,nearClashes: 1 });
    });
});
