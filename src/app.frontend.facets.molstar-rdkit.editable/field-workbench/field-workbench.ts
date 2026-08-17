import * as THREE from 'three';
import { MarchingCubes } from 'three/examples/jsm/objects/MarchingCubes.js';
import { DiracClient, DiracError, fetchField, type FieldResult } from '../../app/services/dirac-client';
import { LigandDepiction, type AtomPosition } from '../../chemistry.backend.perception.rdkit-wasm.editable/ligand-depiction';
import { discoveryWorkspaceNavigation } from '../discovery-navigation';

type Atom = { x: number; y: number; z: number; element: string; name?: string; residue?: string; residueId?: string };
type Bond = { a: number; b: number; order: number };
type Molecule = { name: string; molfile: string; atoms: Atom[]; bonds: Bond[] };
type Residue = { id: string; name: string; seq: string; atoms: Atom[]; distance: number };
type Contact = { residue: Residue; distance: number; type: string };
type Cube = { dimensions: [number, number, number]; lengths: [number, number, number]; center: THREE.Vector3; orientation: THREE.Matrix4; values: Float32Array; min: number; max: number };
type Volume = { kind: 'mep' | 'mlp'; cube: Cube; values: Float32Array; geometry: THREE.BufferGeometry; result: FieldResult; iso: number };
type SceneView = { el: HTMLElement; scene: THREE.Scene; camera: THREE.PerspectiveCamera; root: THREE.Group; molecule: Molecule; frameCenter: THREE.Vector3; atomMeshes: THREE.Mesh[]; highlight: THREE.Mesh; residueMeshes: THREE.Object3D[]; kind: 'pocket' | 'field'; volume?: Volume };

// RDKit MCS for the bundled klr_22 / 1oiy-1 pair. The interface is deliberately
// a pair list so a backend MCS result can replace this fixture without changing
// the selection bus. Unmatched substituent atoms remain one-sided selections.
const CurrentPairMcs: ReadonlyArray<readonly [number, number]> = [
    [2, 4], [3, 3], [4, 26], [5, 25], [6, 6], [25, 5], [48, 30],
    [7, 7], [8, 8], [9, 9], [10, 10], [11, 11], [12, 12], [13, 13],
    [14, 14], [15, 15], [16, 16], [17, 17], [18, 18], [44, 43],
    [45, 44], [42, 41], [43, 42], [40, 39], [41, 40], [38, 37],
    [39, 38], [36, 35], [37, 36], [35, 34], [33, 32], [34, 33],
    [19, 19], [23, 23], [24, 24], [22, 22], [21, 21], [20, 20],
    [46, 45], [47, 46], [32, 31], [31, 47], [30, 48],
];
const parentToProposal = new Map<number, number>(CurrentPairMcs);
const proposalToParent = new Map<number, number>(CurrentPairMcs.map(([a, b]) => [b, a]));

const query = new URLSearchParams(location.search);
const apiBase = query.get('api') || `http://${location.hostname}:8901`;
const client = new DiracClient({ baseUrl: apiBase, timeoutMs: 600_000 });
const app = document.getElementById('workbench');
const stage = document.getElementById('stage') as HTMLCanvasElement | null;
if (!app || !stage) throw new Error('Field workbench mount points are missing');

app.innerHTML = `<div class="app">
  <header class="topbar"><div class="brand"><i class="brand-mark"></i><b>DIRAC</b><span>DISCOVERY LAB · FIELD</span></div>
    <div class="context"><span><small>DATASET</small><b>DOCKING REFERENCE SET · 3D</b></span><span><small>PAIR</small><b id="pair-name">LOADING</b></span><span><small>ENGINE</small><b>${apiBase}</b></span><span><small>STATE</small><b class="live">● LINKED</b></span></div>
    <div class="top-actions">${discoveryWorkspaceNavigation('field')}<button id="sync-camera" class="active">CAMERAS LINKED</button><button id="reset-camera">RESET VIEW</button></div>
  </header>
  <div class="statusbar"><span><i></i>ONE ATOM SELECTION BUS</span><span>2D ↔ 3D ↔ FIELDS ↔ EVIDENCE</span><span class="warn">PAIRING: RDKIT MCS · 43 / 49 ATOMS</span><em class="status-text" id="status">LOADING REAL ASSETS</em></div>
  <main class="workspace">
    <section class="column left">
      <article class="panel depiction"><div class="panel-head"><b>PARENT · 2D STRUCTURE</b><span id="parent-label">—</span></div><div class="depiction-body" id="parent-2d"></div></article>
      <article class="panel depiction"><div class="panel-head"><b>PROPOSAL · 2D STRUCTURE</b><span id="proposal-label">—</span></div><div class="depiction-body" id="proposal-2d"></div></article>
      <article class="panel mapping"><div class="panel-head" style="margin:-10px -10px 0"><b>ATOM CORRESPONDENCE</b><span>RDKIT MCS</span></div><div class="map-row"><span>COVERAGE</span><div class="track"><i id="map-track" style="width:0"></i></div><b id="map-count">—</b></div><div class="map-row"><span>SELECTED</span><b id="map-selection">—</b><em>MCS LINK</em></div><p>RDKit MCS defines the linked core. Six unmatched substituent atoms remain one-sided; they are never forced into a false correspondence.</p></article>
    </section>
    <section class="column center">
      <article class="panel pocket-panel"><div class="panel-head"><b>PARENT / PROPOSAL · RECEPTOR POCKET</b><span>SAME CAMERA · 5 Å CONTACT SHELL</span></div><div class="dual-view"><div class="viewport" id="parent-pocket"><div class="viewport-label">PARENT<b id="parent-pocket-label">—</b></div><div class="view-hud">CLICK ATOM IN 2D OR 3D<br><span class="selected" id="parent-3d-atom">ATOM —</span></div></div><div class="viewport" id="proposal-pocket"><div class="viewport-label">PROPOSAL<b id="proposal-pocket-label">—</b></div><div class="view-hud">SYNCHRONIZED SELECTION<br><span class="selected" id="proposal-3d-atom">ATOM —</span></div></div></div><div class="pocket-foot"><span><i class="legend-dot lig"></i>ligand</span><span><i class="legend-dot res"></i>nearby residue</span><span><i class="legend-dot contact"></i>selected contact</span><span>DRAG TO ROTATE · WHEEL TO ZOOM · NO AUTO-ROTATION</span></div></article>
      <section class="fields">
        <article class="panel field-panel"><div class="panel-head"><b>MEP · SIGNED ISOPOTENTIAL</b><span>SHARED RECEPTOR FRAME</span></div><div class="field-view"><div class="viewport" id="parent-mep"><div class="viewport-label">A · PARENT</div><div class="loading" id="parent-mep-loading">COMPUTING REAL FIELD</div></div><div class="viewport" id="proposal-mep"><div class="viewport-label">B · PROPOSAL</div><div class="loading" id="proposal-mep-loading">COMPUTING REAL FIELD</div></div></div><div class="field-foot"><span>BLUE / RED<b>positive / negative</b></span><span>CONTOUR<b id="mep-iso">—</b></span></div></article>
        <article class="panel field-panel"><div class="panel-head"><b>MLP · LIPOPHILIC POTENTIAL</b><span>SHARED RECEPTOR FRAME</span></div><div class="field-view"><div class="viewport" id="parent-mlp"><div class="viewport-label">A · PARENT</div><div class="loading" id="parent-mlp-loading">COMPUTING REAL FIELD</div></div><div class="viewport" id="proposal-mlp"><div class="viewport-label">B · PROPOSAL</div><div class="loading" id="proposal-mlp-loading">COMPUTING REAL FIELD</div></div></div><div class="field-foot"><span>GOLD SURFACE<b>contracted MLP cube</b></span><span>CONTOUR<b id="mlp-iso">—</b></span></div></article>
      </section>
    </section>
    <aside class="column right">
      <article class="panel"><div class="panel-head"><b>LINKED SELECTION</b><span>ATOM-LEVEL</span></div><div class="selection-card"><div class="atom-orb" id="atom-symbol">—</div><div class="atom-meta"><div class="metric"><small>PARENT</small><b id="sel-parent">—</b></div><div class="metric"><small>PROPOSAL</small><b id="sel-proposal">—</b></div><div class="metric"><small>POCKET CONTACTS</small><b class="pos" id="sel-contacts">—</b></div><div class="metric"><small>NEAREST</small><b id="sel-nearest">—</b></div></div></div></article>
      <article class="panel residue-panel"><div class="panel-head"><b>RESIDUES & INTERACTIONS</b><span id="residue-count">—</span></div><div class="residue-list" id="residue-list"></div></article>
      <article class="panel probe-panel"><div class="panel-head"><b>PROBE & A−B DIFFERENCE</b><span>SELECTED ATOM COORDINATE</span></div><div class="probe-grid"><div class="probe-cell"><small>MEP · PARENT</small><b id="mep-a">—</b></div><div class="probe-cell"><small>MEP · PROPOSAL</small><b id="mep-b">—</b></div><div class="probe-cell delta"><small>MEP · B−A</small><b id="mep-delta">—</b></div><div class="probe-cell delta"><small>MLP · B−A</small><b id="mlp-delta">—</b></div><div class="difference-bars"><div class="difference-bar"><span>MEP</span><i id="mep-bar"></i><b id="mep-bar-value">—</b></div><div class="difference-bar"><span>MLP</span><i id="mlp-bar"></i><b id="mlp-bar-value">—</b></div></div></div></article>
    </aside>
  </main>
</div>`;

function setText(id: string, value: string, error = false): void { const el = document.getElementById(id); if (el) { el.textContent = value; el.classList.toggle('error', error); } }
function compact(v: number): string { if (!Number.isFinite(v)) return '—'; const a = Math.abs(v); return a && (a < .001 || a >= 1000) ? v.toExponential(2) : v.toFixed(a < .1 ? 4 : 3); }
function parseSdfRecords(text: string): Molecule[] {
    return text.split(/\$\$\$\$/).map(record => record.trim()).filter(Boolean).map(record => {
        const lines = record.split(/\r?\n/); const counts = lines[3] || ''; const atomCount = Number(counts.slice(0, 3)); const bondCount = Number(counts.slice(3, 6));
        const atoms: Atom[] = [], bonds: Bond[] = [];
        for (let i = 0; i < atomCount; i++) { const line = lines[4 + i] || ''; atoms.push({ x: Number(line.slice(0, 10)), y: Number(line.slice(10, 20)), z: Number(line.slice(20, 30)), element: line.slice(31, 34).trim().toUpperCase() || 'C' }); }
        for (let i = 0; i < bondCount; i++) { const line = lines[4 + atomCount + i] || ''; bonds.push({ a: Number(line.slice(0, 3)) - 1, b: Number(line.slice(3, 6)) - 1, order: Number(line.slice(6, 9)) || 1 }); }
        const nameMatch = record.match(/>\s+<ligandName>[^\n]*\n([^\n]+)/); const molEnd = lines.findIndex(line => line.trim() === 'M  END');
        return { name: nameMatch?.[1]?.trim() || lines[0].trim() || 'LIGAND', molfile: lines.slice(0, molEnd + 1).join('\n'), atoms, bonds };
    });
}
function parsePdb(text: string): Residue[] {
    const groups = new Map<string, Residue>();
    for (const line of text.split(/\r?\n/)) { if (!line.startsWith('ATOM') && !line.startsWith('HETATM')) continue; const name = line.slice(12, 16).trim(); const residue = line.slice(17, 20).trim(); const chain = line.slice(21, 22).trim() || 'A'; const seq = line.slice(22, 26).trim(); const element = (line.slice(76, 78).trim() || name.replace(/[^A-Za-z]/g, '').slice(0, 1)).toUpperCase(); const atom = { x: Number(line.slice(30, 38)), y: Number(line.slice(38, 46)), z: Number(line.slice(46, 54)), element, name, residue, residueId: `${chain}:${seq}` }; if (![atom.x, atom.y, atom.z].every(Number.isFinite)) continue; const id = `${residue} ${chain}:${seq}`; let group = groups.get(id); if (!group) { group = { id, name: residue, seq: `${chain}:${seq}`, atoms: [], distance: Infinity }; groups.set(id, group); } group.atoms.push(atom); }
    return [...groups.values()];
}
function centerOf(m: Molecule): THREE.Vector3 { return m.atoms.reduce((v, a) => v.add(new THREE.Vector3(a.x, a.y, a.z)), new THREE.Vector3()).multiplyScalar(1 / m.atoms.length); }
function distance(a: Atom, b: Atom): number { return Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z); }
function nearbyResidues(molecule: Molecule, all: Residue[], cutoff = 5): Residue[] { return all.map(r => ({ ...r, distance: Math.min(...r.atoms.filter(a => a.element !== 'H').flatMap(a => molecule.atoms.filter(b => b.element !== 'H').map(b => distance(a, b)))) })).filter(r => r.distance <= cutoff).sort((a, b) => a.distance - b.distance); }
function contactType(ligand: Atom, receptor: Atom, residue: string): string { const polar = ['N', 'O', 'S']; if (polar.includes(ligand.element) && polar.includes(receptor.element)) return 'H-BOND GEOMETRY'; if (['ASP', 'GLU', 'LYS', 'ARG', 'HIS'].includes(residue) && polar.includes(ligand.element)) return 'POLAR / IONIC'; return 'VDW CONTACT'; }
function contactsFor(atom: Atom, residues: Residue[]): Contact[] { return residues.map(residue => { const candidates = residue.atoms.filter(a => a.element !== 'H').map(a => ({ atom: a, d: distance(atom, a) })).sort((a, b) => a.d - b.d); return { residue, distance: candidates[0]?.d ?? Infinity, type: candidates[0] ? contactType(atom, candidates[0].atom, residue.name) : '—' }; }).filter(c => c.distance <= 5).sort((a, b) => a.distance - b.distance); }

function parseCube(text: string): Cube {
    const lines = text.trim().split(/\r?\n/); const header = lines[2].trim().split(/\s+/).map(Number); const atomCount = Math.abs(header[0]); const axes = [3, 4, 5].map(i => lines[i].trim().split(/\s+/).map(Number)); const dimensions = axes.map(a => Math.abs(a[0])) as [number, number, number]; const bohr = .529177210903; const vectors = axes.map(a => new THREE.Vector3(a[1], a[2], a[3]).multiplyScalar(bohr)); const lengths = vectors.map((v, i) => v.length() * dimensions[i]) as [number, number, number]; const center = new THREE.Vector3(header[1], header[2], header[3]).multiplyScalar(bohr); vectors.forEach((v, i) => center.addScaledVector(v, dimensions[i] / 2)); const orientation = new THREE.Matrix4().makeBasis(vectors[0].clone().normalize(), vectors[1].clone().normalize(), vectors[2].clone().normalize()); const expected = dimensions[0] * dimensions[1] * dimensions[2]; const raw = lines.slice(6 + atomCount).join(' ').trim().split(/\s+/).map(Number).slice(0, expected); const values = Float32Array.from(raw); let min = Infinity, max = -Infinity; values.forEach(v => { min = Math.min(min, v); max = Math.max(max, v); }); return { dimensions, lengths, center, orientation, values, min, max };
}
function sampleCube(cube: Cube, world: Atom): number {
    const local = new THREE.Vector3(world.x, world.y, world.z).sub(cube.center).applyMatrix4(cube.orientation.clone().invert()); const q = [local.x / cube.lengths[0] + .5, local.y / cube.lengths[1] + .5, local.z / cube.lengths[2] + .5]; const [nx, ny, nz] = cube.dimensions; const c = (v: number, n: number) => THREE.MathUtils.clamp(v * (n - 1), 0, n - 1); const [fx, fy, fz] = [c(q[0], nx), c(q[1], ny), c(q[2], nz)]; const [x0, y0, z0] = [Math.floor(fx), Math.floor(fy), Math.floor(fz)], [x1, y1, z1] = [Math.min(nx - 1, x0 + 1), Math.min(ny - 1, y0 + 1), Math.min(nz - 1, z0 + 1)]; const at = (x: number, y: number, z: number) => cube.values[(x * ny + y) * nz + z]; const tx = fx - x0, ty = fy - y0, tz = fz - z0; const a = THREE.MathUtils.lerp(at(x0, y0, z0), at(x1, y0, z0), tx), b = THREE.MathUtils.lerp(at(x0, y1, z0), at(x1, y1, z0), tx), d = THREE.MathUtils.lerp(at(x0, y0, z1), at(x1, y0, z1), tx), e = THREE.MathUtils.lerp(at(x0, y1, z1), at(x1, y1, z1), tx); return THREE.MathUtils.lerp(THREE.MathUtils.lerp(a, b, ty), THREE.MathUtils.lerp(d, e, ty), tz);
}
function resample(cube: Cube, n = 46): Float32Array { const out = new Float32Array(n ** 3), [nx, ny, nz] = cube.dimensions, at = (x: number, y: number, z: number) => cube.values[(x * ny + y) * nz + z]; for (let z = 0; z < n; z++) for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) { const fx = x * (nx - 1) / (n - 1), fy = y * (ny - 1) / (n - 1), fz = z * (nz - 1) / (n - 1), x0 = Math.floor(fx), y0 = Math.floor(fy), z0 = Math.floor(fz), x1 = Math.min(nx - 1, x0 + 1), y1 = Math.min(ny - 1, y0 + 1), z1 = Math.min(nz - 1, z0 + 1), tx = fx - x0, ty = fy - y0, tz = fz - z0; const c00 = THREE.MathUtils.lerp(at(x0, y0, z0), at(x1, y0, z0), tx), c10 = THREE.MathUtils.lerp(at(x0, y1, z0), at(x1, y1, z0), tx), c01 = THREE.MathUtils.lerp(at(x0, y0, z1), at(x1, y0, z1), tx), c11 = THREE.MathUtils.lerp(at(x0, y1, z1), at(x1, y1, z1), tx); out[x + y * n + z * n * n] = THREE.MathUtils.lerp(THREE.MathUtils.lerp(c00, c10, ty), THREE.MathUtils.lerp(c01, c11, ty), tz); } return out; }
function isoGeometry(values: Float32Array, iso: number): THREE.BufferGeometry { const n = Math.round(Math.cbrt(values.length)); const material = new THREE.MeshBasicMaterial(); const mc = new MarchingCubes(n, material, false, false, 260000); for (let i = 0; i < values.length; i++) mc.field[i] = values[i]; mc.isolation = iso; mc.update(); const geometry = mc.geometry.clone(); geometry.setDrawRange(0, mc.count); geometry.computeVertexNormals(); material.dispose(); mc.geometry.dispose(); return geometry; }

const renderer = new THREE.WebGLRenderer({ canvas: stage, antialias: true, alpha: true, powerPreference: 'high-performance', preserveDrawingBuffer: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5)); renderer.outputColorSpace = THREE.SRGBColorSpace; renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.15; renderer.setScissorTest(true);
const sphere = new THREE.SphereGeometry(.24, 18, 12), hSphere = new THREE.SphereGeometry(.13, 12, 8), bondGeometry = new THREE.CylinderGeometry(.055, .055, 1, 8), residueSphere = new THREE.SphereGeometry(.14, 10, 8);
const elementColor: Record<string, number> = { C: 0x26363d, H: 0xaec0c5, N: 0x6488ff, O: 0xff6678, S: 0xffc24f, F: 0x63e99b, CL: 0x63e99b };
const materialFor = (element: string) => new THREE.MeshPhysicalMaterial({ color: elementColor[element] || 0x8ba0a6, emissive: elementColor[element] || 0x8ba0a6, emissiveIntensity: .13, roughness: .25, metalness: .08, clearcoat: .55 });
function bondMesh(a: THREE.Vector3, b: THREE.Vector3, material: THREE.Material): THREE.Mesh { const d = b.clone().sub(a), mesh = new THREE.Mesh(bondGeometry, material); mesh.scale.y = d.length(); mesh.position.copy(a).add(b).multiplyScalar(.5); mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), d.normalize()); return mesh; }
function createBaseView(id: string, molecule: Molecule, kind: 'pocket' | 'field', residues: Residue[] = [], frameCenter = centerOf(molecule)): SceneView {
    const el = document.getElementById(id)!; const scene = new THREE.Scene(); const camera = new THREE.PerspectiveCamera(29, 1, .1, 120); const root = new THREE.Group(); root.rotation.set(-.34, .45, -.08); scene.add(root); const center = frameCenter; const atomMeshes: THREE.Mesh[] = [], residueMeshes: THREE.Object3D[] = []; const bondMat = new THREE.MeshPhysicalMaterial({ color: 0xd6e4e7, emissive: 0x294049, emissiveIntensity: .16, roughness: .33, metalness: .1 });
    molecule.bonds.forEach(b => { const a = molecule.atoms[b.a], c = molecule.atoms[b.b]; if (!a || !c) return; root.add(bondMesh(new THREE.Vector3(a.x, a.y, a.z).sub(center), new THREE.Vector3(c.x, c.y, c.z).sub(center), bondMat)); });
    molecule.atoms.forEach((a, index) => { const mesh = new THREE.Mesh(a.element === 'H' ? hSphere : sphere, materialFor(a.element)); mesh.position.set(a.x - center.x, a.y - center.y, a.z - center.z); mesh.userData.atomIndex = index; mesh.renderOrder = 5; root.add(mesh); atomMeshes.push(mesh); });
    if (kind === 'pocket') { const resMat = new THREE.MeshPhysicalMaterial({ color: 0xc39b48, emissive: 0x6f5420, emissiveIntensity: .18, roughness: .36, transparent: true, opacity: .58, depthWrite: false }); residues.slice(0, 12).forEach(r => { const visible = r.atoms.filter(a => a.element !== 'H' && molecule.atoms.some(l => distance(a, l) <= 5)); visible.forEach(a => { const mesh = new THREE.Mesh(residueSphere, resMat.clone()); mesh.position.set(a.x - center.x, a.y - center.y, a.z - center.z); mesh.userData.residueId = r.id; mesh.renderOrder = 2; root.add(mesh); residueMeshes.push(mesh); }); for (let i = 0; i < visible.length; i++) for (let j = i + 1; j < visible.length; j++) { const d = distance(visible[i], visible[j]); if (d < 1.05 || d > 1.95) continue; const bond = bondMesh(new THREE.Vector3(visible[i].x, visible[i].y, visible[i].z).sub(center), new THREE.Vector3(visible[j].x, visible[j].y, visible[j].z).sub(center), resMat.clone()); bond.scale.x = bond.scale.z = .62; bond.userData.residueId = r.id; bond.renderOrder = 1; root.add(bond); residueMeshes.push(bond); } }); }
    const highlight = new THREE.Mesh(new THREE.SphereGeometry(.42, 24, 16), new THREE.MeshBasicMaterial({ color: 0x74edff, wireframe: true, transparent: true, opacity: .95 })); highlight.visible = false; highlight.renderOrder = 20; root.add(highlight); scene.add(new THREE.HemisphereLight(0xe3f6ff, 0x010407, 1.05)); const key = new THREE.DirectionalLight(0xffffff, 2.4); key.position.set(5, 7, 9); scene.add(key); const rim = new THREE.DirectionalLight(0x5edff4, 1.2); rim.position.set(-5, 1, 4); scene.add(rim); camera.position.set(0, 0, kind === 'pocket' ? 28 : 25); camera.lookAt(0, 0, 0); const view = { el, scene, camera, root, molecule, frameCenter, atomMeshes, highlight, residueMeshes, kind } as SceneView; bindViewInteraction(view); return view;
}
let cameraLinked = true, selectedParentAtom: number | null = null, selectedProposalAtom: number | null = null, parent: Molecule, proposal: Molecule, parentResidues: Residue[] = [], proposalResidues: Residue[] = [], parentPositions: AtomPosition[] = [];
const views: SceneView[] = [], volumes = new Map<string, Volume>(); const raycaster = new THREE.Raycaster();
function bindViewInteraction(view: SceneView): void { let dragging = false, x = 0, y = 0, downX = 0, downY = 0; view.el.addEventListener('pointerdown', e => { dragging = true; x = downX = e.clientX; y = downY = e.clientY; view.el.setPointerCapture(e.pointerId); }); view.el.addEventListener('pointermove', e => { if (!dragging) return; const dx = e.clientX - x, dy = e.clientY - y; x = e.clientX; y = e.clientY; const targets = cameraLinked ? views : [view]; targets.forEach(v => { v.root.rotation.y += dx * .006; v.root.rotation.x = THREE.MathUtils.clamp(v.root.rotation.x + dy * .006, -1.35, 1.35); }); }); view.el.addEventListener('pointerup', e => { const moved = Math.hypot(e.clientX - downX, e.clientY - downY); dragging = false; view.el.releasePointerCapture(e.pointerId); if (moved < 4) { const rect = view.el.getBoundingClientRect(); raycaster.setFromCamera(new THREE.Vector2((e.clientX - rect.left) / rect.width * 2 - 1, -(e.clientY - rect.top) / rect.height * 2 + 1), view.camera); const hit = raycaster.intersectObjects(view.atomMeshes, false)[0]; if (hit) void selectAtom(Number(hit.object.userData.atomIndex), view.molecule === proposal ? 'proposal' : 'parent'); } }); view.el.addEventListener('wheel', e => { e.preventDefault(); const factor = Math.exp(e.deltaY * .001); (cameraLinked ? views : [view]).forEach(v => { v.camera.position.z = THREE.MathUtils.clamp(v.camera.position.z * factor, 5, 34); }); }, { passive: false }); }
function addFieldToView(view: SceneView, volume: Volume): void { view.volume = volume; const group = new THREE.Group(); group.position.copy(volume.cube.center.clone().sub(view.frameCenter)); group.scale.set(volume.cube.lengths[0] / 2, volume.cube.lengths[1] / 2, volume.cube.lengths[2] / 2); group.quaternion.setFromRotationMatrix(volume.cube.orientation); const color = volume.kind === 'mlp' ? 0xffbd42 : 0x438dff, opacity = volume.kind === 'mlp' ? .28 : .24; const material = new THREE.MeshPhysicalMaterial({ color, emissive: color, emissiveIntensity: .13, roughness: .3, metalness: .02, clearcoat: .7, transparent: true, opacity, depthWrite: false, side: THREE.DoubleSide }); group.add(new THREE.Mesh(volume.geometry, material)); if (volume.kind === 'mep' && volume.cube.min < -volume.iso) { const negative = isoGeometry(Float32Array.from(volume.values, v => -v), volume.iso); group.add(new THREE.Mesh(negative, new THREE.MeshPhysicalMaterial({ color: 0xff4164, emissive: 0xff4164, emissiveIntensity: .13, roughness: .3, clearcoat: .7, transparent: true, opacity, depthWrite: false, side: THREE.DoubleSide }))); } view.root.add(group); }
function frameView(view: SceneView): void { const rect = view.el.getBoundingClientRect(); view.camera.aspect = Math.max(.3, rect.width / Math.max(1, rect.height)); view.camera.updateProjectionMatrix(); }

function alignProposalSvg(svg: SVGSVGElement, positions: AtomPosition[], reference: AtomPosition[]): AtomPosition[] {
    const parentPoints = new Map(reference.map(p => [p.idx, p]));
    const proposalPoints = new Map(positions.map(p => [p.idx, p]));
    const pairs = CurrentPairMcs.map(([a, b]) => [parentPoints.get(a), proposalPoints.get(b)] as const).filter((pair): pair is readonly [AtomPosition, AtomPosition] => Boolean(pair[0] && pair[1]));
    if (pairs.length < 3) return positions;
    const parentCenter = pairs.reduce((p, [a]) => ({ x: p.x + a.x, y: p.y + a.y }), { x: 0, y: 0 });
    const proposalCenter = pairs.reduce((p, [, b]) => ({ x: p.x + b.x, y: p.y + b.y }), { x: 0, y: 0 });
    parentCenter.x /= pairs.length; parentCenter.y /= pairs.length; proposalCenter.x /= pairs.length; proposalCenter.y /= pairs.length;
    let dot = 0, cross = 0, denominator = 0;
    pairs.forEach(([a, b]) => { const px = a.x - parentCenter.x, py = a.y - parentCenter.y, qx = b.x - proposalCenter.x, qy = b.y - proposalCenter.y; dot += qx * px + qy * py; cross += qx * py - qy * px; denominator += qx * qx + qy * qy; });
    if (denominator < 1e-8) return positions;
    const a = dot / denominator, b = cross / denominator, e = parentCenter.x - a * proposalCenter.x + b * proposalCenter.y, f = parentCenter.y - b * proposalCenter.x - a * proposalCenter.y;
    const group = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    group.setAttribute('class', 'mcs-aligned-proposal'); group.setAttribute('transform', `matrix(${a} ${b} ${-b} ${a} ${e} ${f})`);
    // RDKit emits a full-canvas background rect. It must stay untransformed;
    // only molecular drawing primitives belong in the scaffold alignment.
    [...svg.children].filter(child => !['defs', 'metadata', 'style', 'title', 'desc', 'rect'].includes(child.tagName.toLowerCase())).forEach(child => group.appendChild(child));
    svg.appendChild(group);
    return positions.map(p => ({ idx: p.idx, x: a * p.x - b * p.y + e, y: b * p.x + a * p.y + f }));
}
async function renderDepiction(targetId: string, molecule: Molecule, selected: number | null, source: 'parent' | 'proposal', assign: (p: AtomPosition[]) => void, reference: AtomPosition[] = []): Promise<void> {
    const atomHighlights = selected == null ? [] : [{ atomIndex: selected, color: '#61e8ff', alpha: .7 }];
    const result = await LigandDepiction.depict(molecule.molfile, { atomHighlights }); const target = document.getElementById(targetId)!;
    if (!result) { target.innerHTML = '<div class="loading error">RDKIT DEPICTION FAILED</div>'; return; }
    target.innerHTML = result.svgString; const svg = target.querySelector('svg') as SVGSVGElement | null; if (!svg) return;
    const displayedPositions = source === 'proposal' && reference.length ? alignProposalSvg(svg, result.atomPositions, reference) : result.atomPositions;
    assign(displayedPositions);
    svg.addEventListener('click', e => { const index = LigandDepiction.getAtomIndexFromClick(svg, displayedPositions, e.clientX, e.clientY, 32); if (index >= 0) void selectAtom(index, source); });
}
function updateBar(id: string, value: number): void { const el = document.getElementById(id); if (!el) return; if (!Number.isFinite(value)) { el.style.setProperty('--w', '0%'); return; } const width = `${Math.min(50, 7 + Math.abs(value) * 18)}%`; el.style.setProperty('--w', width); el.style.setProperty('--x', value >= 0 ? '0' : '-100%'); el.style.setProperty('--c', value >= 0 ? '#56e69b' : '#ff6078'); }
async function selectAtom(index: number, source: 'parent' | 'proposal' = 'parent'): Promise<void> {
    if (!parent || !proposal) return;
    if (source === 'parent') { selectedParentAtom = THREE.MathUtils.clamp(index, 0, parent.atoms.length - 1); selectedProposalAtom = parentToProposal.get(selectedParentAtom) ?? null; } else { selectedProposalAtom = THREE.MathUtils.clamp(index, 0, proposal.atoms.length - 1); selectedParentAtom = proposalToParent.get(selectedProposalAtom) ?? null; }
    const pa = selectedParentAtom == null ? null : parent.atoms[selectedParentAtom], pb = selectedProposalAtom == null ? null : proposal.atoms[selectedProposalAtom];
    views.forEach(view => {
        const atom = view.molecule === parent ? pa : pb; view.highlight.visible = Boolean(atom);
        if (atom) view.highlight.position.set(atom.x - view.frameCenter.x, atom.y - view.frameCenter.y, atom.z - view.frameCenter.z);
        if (view.kind === 'pocket') { const active = new Set(atom ? contactsFor(atom, view.molecule === parent ? parentResidues : proposalResidues).map(x => x.residue.id) : []); view.residueMeshes.forEach(mesh => { const material = (mesh as THREE.Mesh).material as THREE.MeshPhysicalMaterial; const on = active.has(String(mesh.userData.residueId)); material.color.setHex(on ? 0x55e59a : 0xc39b48); material.emissive.setHex(on ? 0x1d8154 : 0x6f5420); material.opacity = on ? .9 : .35; }); }
    });
    const focusAtom = source === 'parent' ? pa : pb, focusResidues = source === 'parent' ? parentResidues : proposalResidues;
    setText('atom-symbol', focusAtom?.element ?? '—');
    setText('parent-3d-atom', pa ? `ATOM ${selectedParentAtom! + 1} · ${pa.element}` : 'UNMAPPED SUBSTITUENT');
    setText('proposal-3d-atom', pb ? `ATOM ${selectedProposalAtom! + 1} · ${pb.element}` : 'UNMAPPED SUBSTITUENT');
    setText('sel-parent', pa ? `#${selectedParentAtom! + 1} ${pa.element}` : '— UNMAPPED'); setText('sel-proposal', pb ? `#${selectedProposalAtom! + 1} ${pb.element}` : '— UNMAPPED');
    setText('map-selection', `A:${selectedParentAtom == null ? '—' : selectedParentAtom + 1} ↔ B:${selectedProposalAtom == null ? '—' : selectedProposalAtom + 1}`);
    const contacts = focusAtom ? contactsFor(focusAtom, focusResidues) : [];
    setText('sel-contacts', String(contacts.length)); setText('sel-nearest', contacts[0] ? `${contacts[0].residue.name} ${contacts[0].distance.toFixed(2)} Å` : 'NONE ≤5 Å'); setText('residue-count', `${source === 'parent' ? 'A' : 'B'} · ${contacts.length} CONTACTS`);
    const list = document.getElementById('residue-list'); if (list) list.innerHTML = contacts.length ? contacts.map((c, i) => `<div class="residue-row${i === 0 ? ' active' : ''}"><b>${c.residue.id}</b><em>${c.type}</em><span>${c.distance.toFixed(2)} Å</span></div>`).join('') : '<div class="residue-row"><b>NO CONTACT</b><em>WITHIN 5 Å</em><span>—</span></div>';
    await renderDepiction('parent-2d', parent, selectedParentAtom, 'parent', p => parentPositions = p);
    await renderDepiction('proposal-2d', proposal, selectedProposalAtom, 'proposal', () => undefined, parentPositions);
    updateProbe();
}
function updateProbe(): void {
    if (!parent || !proposal) return; const mepA = volumes.get('parent-mep'), mepB = volumes.get('proposal-mep'), mlpA = volumes.get('parent-mlp'), mlpB = volumes.get('proposal-mlp'); const pa = selectedParentAtom == null ? null : parent.atoms[selectedParentAtom], pb = selectedProposalAtom == null ? null : proposal.atoms[selectedProposalAtom];
    const a = mepA && pa ? sampleCube(mepA.cube, pa) : NaN, b = mepB && pb ? sampleCube(mepB.cube, pb) : NaN, c = mlpA && pa ? sampleCube(mlpA.cube, pa) : NaN, d = mlpB && pb ? sampleCube(mlpB.cube, pb) : NaN, dm = b - a, dl = d - c;
    setText('mep-a', compact(a)); setText('mep-b', compact(b)); setText('mep-delta', compact(dm)); setText('mlp-delta', compact(dl)); setText('mep-bar-value', compact(dm)); setText('mlp-bar-value', compact(dl)); updateBar('mep-bar', dm); updateBar('mlp-bar', dl);
}
async function loadVolume(key: string, kind: 'mep' | 'mlp', molecule: Molecule, view: SceneView): Promise<void> { const loading = document.getElementById(`${key}-loading`); try { const result = await fetchField(client, kind, { molfile: molecule.molfile, maxSeconds: 300 }); const cube = parseCube(result.cube), values = resample(cube), maxAbs = Math.max(Math.abs(cube.min), Math.abs(cube.max)), nominal = kind === 'mep' ? 10 : .25, iso = Math.min(nominal, maxAbs * .75); if (!(iso > 0)) throw new Error(`${kind} cube has no contour`); const volume = { kind, cube, values, geometry: isoGeometry(values, iso), result, iso }; volumes.set(key, volume); addFieldToView(view, volume); if (loading) loading.hidden = true; setText(`${kind}-iso`, `±${compact(iso)} · backend units`); updateProbe(); } catch (error) { if (loading) { loading.textContent = error instanceof DiracError ? `${error.code} · FIELD UNAVAILABLE` : error instanceof Error ? error.message : String(error); loading.classList.add('error'); } } }

async function boot(): Promise<void> {
    try {
        const [ligandResponse, receptorResponse] = await Promise.all([fetch('./assets/ligand.sdf'), fetch('./assets/receptor.pdb')]);
        if (!ligandResponse.ok || !receptorResponse.ok) throw new Error('Reference assets are unavailable');
        const molecules = parseSdfRecords(await ligandResponse.text()), receptor = parsePdb(await receptorResponse.text());
        if (molecules.length < 2) throw new Error('Two 3D ligand records are required');
        [parent, proposal] = molecules; parentResidues = nearbyResidues(parent, receptor); proposalResidues = nearbyResidues(proposal, receptor);
        setText('pair-name', `${parent.name} → ${proposal.name}`); ['parent-label','parent-pocket-label'].forEach(id => setText(id, parent.name)); ['proposal-label','proposal-pocket-label'].forEach(id => setText(id, proposal.name));
        const denominator = Math.max(parent.atoms.length, proposal.atoms.length); setText('map-count', `${CurrentPairMcs.length} / ${denominator}`); const track = document.getElementById('map-track'); if (track) track.style.width = `${CurrentPairMcs.length / denominator * 100}%`;
        // Both ligands and all field cubes stay in the receptor's docking frame.
        // The shared origin is visual normalization only; no pose is re-fitted.
        const sharedFrameCenter = centerOf(parent);
        const parentPocket = createBaseView('parent-pocket', parent, 'pocket', parentResidues, sharedFrameCenter), proposalPocket = createBaseView('proposal-pocket', proposal, 'pocket', proposalResidues, sharedFrameCenter), parentMep = createBaseView('parent-mep', parent, 'field', [], sharedFrameCenter), proposalMep = createBaseView('proposal-mep', proposal, 'field', [], sharedFrameCenter), parentMlp = createBaseView('parent-mlp', parent, 'field', [], sharedFrameCenter), proposalMlp = createBaseView('proposal-mlp', proposal, 'field', [], sharedFrameCenter);
        views.push(parentPocket, proposalPocket, parentMep, proposalMep, parentMlp, proposalMlp);
        await selectAtom(CurrentPairMcs[0][0]);
        setText('status', `POCKET READY · ${parentResidues.length}/${proposalResidues.length} RESIDUES · FIELDS COMPUTING`);
        void Promise.all([loadVolume('parent-mep', 'mep', parent, parentMep), loadVolume('proposal-mep', 'mep', proposal, proposalMep), loadVolume('parent-mlp', 'mlp', parent, parentMlp), loadVolume('proposal-mlp', 'mlp', proposal, proposalMlp)]).then(() => setText('status', `${volumes.size}/4 REAL FIELD ARTIFACTS READY`));
    } catch (error) { setText('status', error instanceof Error ? error.message : String(error), true); }
}
document.getElementById('sync-camera')?.addEventListener('click', e => { cameraLinked = !cameraLinked; (e.currentTarget as HTMLButtonElement).classList.toggle('active', cameraLinked); (e.currentTarget as HTMLButtonElement).textContent = cameraLinked ? 'CAMERAS LINKED' : 'CAMERAS INDEPENDENT'; });
document.getElementById('reset-camera')?.addEventListener('click', () => views.forEach(v => { v.root.rotation.set(-.34, .45, -.08); v.camera.position.set(0, 0, v.kind === 'pocket' ? 28 : 25); }));
function animate(): void { requestAnimationFrame(animate); const width = innerWidth, height = innerHeight; renderer.setSize(width, height, false); renderer.setScissorTest(false); renderer.setClearColor(0x000000, 0); renderer.clear(); renderer.setScissorTest(true); views.forEach(view => { const r = view.el.getBoundingClientRect(); if (r.width < 2 || r.height < 2 || r.bottom < 0 || r.top > height) return; frameView(view); const left = Math.max(0, r.left), right = Math.min(width, r.right), top = Math.max(0, r.top), bottom = Math.min(height, r.bottom); renderer.setViewport(left, height - bottom, right - left, bottom - top); renderer.setScissor(left, height - bottom, right - left, bottom - top); renderer.setClearColor(view.kind === 'pocket' ? 0x031016 : 0x020b10, 1); renderer.clear(); renderer.render(view.scene, view.camera); }); }
void boot(); requestAnimationFrame(animate);
