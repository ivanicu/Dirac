# Dirac physics backend

Two quantities that medicinal-chemistry software does not show and a physicist
computes without thinking. Both run on the CPU in seconds, both return numbers
a chemist can act on, and both ship with positive controls that fail loudly.

```bash
backend/env/bin/python backend/physics/validate.py       # 8 gates — run first
backend/env/bin/python backend/physics/server.py         # 127.0.0.1:8902
```

Separate daemon from `field_server.py` on purpose: that file belongs to the
Field Wells workstream. The physics itself lives in importable modules
(`mep_surface`, `torsion`), so merging into one process later is an import,
not a copy.

## σ-hole — electrostatic potential ON the surface

`POST /surface/mep {molfile, basis?, isovalue?, points_per_atom?}`

Standard tools render the electrostatic potential as a volume isosurface. That
picture cannot show the thing halogen bonding depends on: around a halogen the
potential is **anisotropic** — a small positive cap sits beyond the atom along
the C–X axis (the σ-hole) while a negative belt wraps the same atom
perpendicular to it. One isovalue through the volume shows neither.

This computes V(r) on the **0.001 a.u. isodensity surface** (Politzer and
Murray's definition, the one V_S,max is defined on) and returns the surface
point cloud, the per-point potential, and the extrema table — including, for
every maximum on a halogen or chalcogen, the C–X···max angle that decides
whether it is a σ-hole at all.

Measured on 4-bromobenzonitrile, a real halogen-bond donor: σ-hole **+20.6
kcal/mol on Br at 177.5°** from the C–Br bond, V_S,min −38.8 on the nitrile
nitrogen, 3.2 s at RHF/6-31G\* for 13 atoms.

`POST /surface/mep_at {molfile, points | points_b64}` evaluates the same
potential at caller-supplied coordinates, so the front end can colour **mol\*'s
own** molecular surface instead of rendering a second one. Verified to agree
with the cloud to 0.0000 kcal/mol.

Why it matters at the bench: a halogen with no σ-hole cannot make a halogen
bond no matter how good the geometry looks, and the magnitude tells you whether
swapping Cl→Br is worth a synthesis.

## Torsional strain — where on the curve does this pose sit?

`POST /torsion/strain {molfile, steps?, relax_hydrogens?, max_torsions?, variant?}`

A deposited ligand is fitted into density, not minimised, and routinely sits
several kcal/mol above its own relaxed geometry. That energy comes out of the
binding free energy, so a docking score computed on a strained pose is partly
fiction — a well-documented problem in PDB ligand validation and invisible in
design software, which shows the pose and never what it cost.

Every rotatable bond gets a relaxed scan; the response carries the curve, where
this conformer sits on it, the local strain, and a verdict
(negligible / mild / notable / severe).

Three honesty constraints are in the code, not the docs:

- **Hydrogens are relaxed first.** X-ray coordinates contain no hydrogens;
  whatever was added is arbitrary, and an unrelaxed H contact invents strain
  that belongs to the model. A gate checks that a 90° twist of a hydroxyl
  torsion is erased by this step.
- **The scan is bidirectional and relaxed.** Setting a dihedral is a rigid
  rotation of half the molecule; scanning that way from a fixed geometry drives
  atoms through each other and reports the collision as a barrier. On aspirin
  that mistake produced a 34 kcal/mol barrier and a 1099 kcal/mol "strain".
  Walking incrementally in both directions and keeping the lower envelope fixes
  it, and makes the observed angle comparable to the rest of the curve.
- **Per-torsion strains are not summed.** Torsions are coupled; adding them
  double-counts. `total_strain_kcal` is a separate quantity —
  E(as given, H-relaxed) − E(fully minimised).

The energy is MMFF94s: fast enough to be interactive, wrong enough that the
number is triage rather than thermodynamics. `meta.method` says so on every
response.

## Validation

`validate.py` is the reason to trust any of the above. Every gate is a known
mechanism the instrument must localise, with the answer fixed by chemistry
rather than by a previous run:

| gate | expectation |
|---|---|
| CF₃Cl > CH₃Cl | electron withdrawal switches the σ-hole on (+18.6 vs −2.8) |
| CH₃Br > CH₃Cl | polarizability orders the halogens (+5.8 vs −2.8) |
| CF₃Br largest | both effects compound (+26.2) |
| minimised conformer | reports no strain |
| conformer on a barrier top | reports that barrier (+14.4 against 13.8) |
| hydrogen-only twist | erased by hydrogen relaxation |
| no barrier above 40 kcal/mol | a collision is not a barrier |

Two implementations died to these gates and neither was visible in the output
shape: the first σ-hole code mixed Ångström and Bohr between the nuclear and
electronic terms and returned −6753 kcal/mol; the first torsion scan turned a
steric collision into a 1099 kcal/mol strain. Absolute σ-hole values depend on
basis and surface definition, so the gates test **orderings**, which do not.

## Wire format

Point clouds and potentials are base64 little-endian float32 — `points_b64` as
xyz triples in Ångström in the molfile's own frame (so everything lands
registered with the mol\* scene), `values_b64` one potential per point in
kcal/mol. JSON numbers would triple a 10 000-point payload for no precision.

## Cost

`int1e_grids` materialises an (npoints, nao, nao) tensor; 10 000 points over
400 basis functions is 12.8 GB in one call. Evaluation is chunked to a ~200 MB
working set. QM is capped at 120 atoms, and an unconverged SCF raises instead
of returning a field — the same rule the fields backend follows, because
V_S,max read off an unconverged density is a number with no referent.
