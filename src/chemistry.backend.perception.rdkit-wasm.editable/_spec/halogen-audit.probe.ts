import { Vec3 } from '../../mol-math/linear-algebra';
import { auditHalogen, PocketAtom } from '../halogen-audit';

const C = Vec3.create(0, 0, 0);
const X = Vec3.create(1.74, 0, 0);                 // C–Cl
const along = (d: number, offDeg: number) => {
    const r = offDeg * Math.PI / 180;
    return Vec3.create(X[0] + d * Math.cos(r), d * Math.sin(r), 0);
};
// The acceptor's own neighbour must sit so that X···A-Y lands near PLIP's 120 deg,
// otherwise the fixture fails PLIP's acceptor-angle test and the comparison row can
// never fire — which is a broken test, not a broken verdict.
const acc = (d: number, off: number, label: string, element = 'O'): PocketAtom => {
    const A = along(d, off);
    const toX = Vec3.normalize(Vec3(), Vec3.sub(Vec3(), Vec3.create(1.74, 0, 0), A));
    const perp = Vec3.create(-toX[1], toX[0], 0);
    const dir = Vec3.add(Vec3(), Vec3.scale(Vec3(), toX, Math.cos(120 * Math.PI / 180)),
        Vec3.scale(Vec3(), perp, Math.sin(120 * Math.PI / 180)));
    return { position: A, element, label, neighbor: Vec3.scaleAndAdd(Vec3(), A, dir, 1.3) };
};

const cases: [string, PocketAtom[], { vsMax: number | null, anisotropy: number | null }][] = [
    ['on-axis acceptor, deep hole (a real halogen bond)', [acc(3.2, 8, 'HINGE MET793 O')], { vsMax: 19, anisotropy: 27 }],
    ['deep hole, acceptor 68 deg off axis (the eleven-month slide)', [acc(3.6, 68, 'HINGE MET793 O')], { vsMax: 19, anisotropy: 27 }],
    ['fluorine: perfect geometry, no hole at all', [acc(3.0, 5, 'THR790 OG1')], { vsMax: -19, anisotropy: 4 }],
    ['geometry passes, electronics do not (mis-called)', [acc(3.3, 12, 'ASP855 OD1')], { vsMax: 4, anisotropy: 9 }],
    ['deep hole, nothing anywhere near', [], { vsMax: 22, anisotropy: 31 }],
    ['deep hole, acceptor just inside the marginal band', [acc(3.1, 10, 'GLN791 OE1')], { vsMax: 10, anisotropy: 15 }],
];

for (const [name, pocket, qm] of cases) {
    const a = auditHalogen(C, X, qm.vsMax !== null && qm.vsMax < 0 ? 'F' : 'Cl', pocket, { ...qm, basis: 'def2-SVP', method: 'RHF' });
    console.log(`\n--- ${name}`);
    console.log(`    verdict   ${a.verdict}`);
    console.log(`    reading   ${a.reading}`);
    console.log(`    plip      ${a.plipVerdict}`);
}
