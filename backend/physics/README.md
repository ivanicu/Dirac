# Dirac physics backend

Two quantities that medicinal-chemistry software does not show and a physicist
computes without thinking. Both run on the CPU in seconds, both return numbers
a chemist can act on, and both ship with positive controls that fail loudly.

```bash
backend/env/bin/python backend/physics/validate.py       # 10 gates — run first
backend/env/bin/python backend/physics/server.py         # 0.0.0.0:8902
backend/env/bin/python backend/physics/coverage.py --set hard    # what it cannot do
```

Bound to all interfaces because Ivan drives this from a Mac on the LAN, and a
loopback-only daemon simply reports "offline" there. Stated rather than
discovered: it is **unauthenticated** and runs quantum chemistry on whatever is
posted to it. `DIRAC_PHYSICS_HOST=127.0.0.1` puts it back on loopback.

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

## What it cannot do — measured, not estimated

| limit | detail |
|---|---|
| cost | HF is O(N⁴), measured on this box as `seconds ≈ 5.9e-9 × nao^4.03` over 47 molecules. Requests are refused **before** running when the prediction exceeds `max_seconds` (default 120 s), with the estimate and a smaller-basis suggestion in the message. **This was true of `/surface/mep` only** until 2026-08-11: `/surface/mep_at` had no cost gate, no budget parameter and no basis whitelist, while this line claimed otherwise for the whole module. Both routes now carry both. Imatinib in def2-SVP is 673 basis functions ≈ 70 minutes: refused. |
| model scatter | the fit underestimates the middle of the range by up to 2.8×, so a 2.8× safety factor is applied. This is an order-of-magnitude screen. It is **not** the real protection and never was: a prediction bounds work whose SIZE is known, and what ran away on the sibling daemon was the ITERATION COUNT — 22 cores for 36 minutes under a prediction that said it would be fine. Both routes now install a per-cycle SCF watchdog that raises out of `kernel()` on a wall-clock deadline. A client socket timeout does not stop a pyscf job holding 24 cores, so it was never protection either. The budget is also clamped for finiteness: `predicted > nan` and `time.time() > t0 + nan` are both False, so one non-finite value used to disable the prediction gate and the watchdog together. Zero is preserved — it is a legitimate 'refuse immediately and tell me the cost'. |
| heavy elements | def2 needs an ECP from Rb (Z=37) up and pyscf does not attach it automatically. `_ecp_for()` does, per element, only where the basis defines one. **Without it iodine is wrong by 58 kcal/mol with no error** — see Validation. |
| MMFF | cannot type PF₆⁻, boronic acids, or selenomethionine. Torsional strain raises on those rather than guessing. |
| no rotatable bonds | 8 of the 68 library molecules (benzene, naphthalene, indole…) report zero strain **vacuously**. Zero because there is nothing to scan is not zero because nothing is strained. |
| ions | an anion's whole surface is negative, so a σ-hole there is negative too (PF₆⁻: −46 against a −154 belt). Sign cannot classify; use `anisotropy_kcal_per_mol` and `positive_cap`. |

Coverage over the 68-molecule screening library plus a 20-entry hard set is
reproducible with `coverage.py`. Note the library contains **no bromine and no
iodine** — it is the wrong population for σ-hole testing, which is why the hard
set exists and why several of its entries are expected to fail.

## How much of the number may be believed — measured, not assumed

The same molecule, run three ways by this code:

| molecule | HF/def2-SVP | HF/def2-TZVP | B3LYP/def2-SVP |
|---|---|---|---|
| bromobenzene | +9.9 | +10.3 | +10.3 |
| iodobenzene | +21.1 | +19.1 | +19.9 |
| CF₃I | +41.4 | +38.1 | +34.2 |

Basis moves V_S,max by **4–10%**; method by **3–22%**, worst on the strongly
polarised CF₃I (8.1 kcal/mol). Geometry (MMFF-optimised, not QM-optimised) and
surface point density are further terms that have not been measured. So:

- **Orderings are robust.** I > Br held under every combination tried, which is
  why the gates test orderings.
- **Absolute values carry ~25% uncertainty** and are reported with it
  (`meta.absolute_uncertainty_pct`). Printing "+21.1 kcal/mol" to one decimal is
  false precision; round before showing it to a chemist.
- `xc` selects a functional (`{"xc": "b3lyp"}`); HF remains the default because
  the cost model was calibrated on it, but B3LYP is closer to the level most
  published V_S,max values were computed at.

**What is still missing is an external ruler.** Every number above is this
code checked against itself at a better level of theory — convergence, not
correctness. The right external anchors are Laurence's diiodine basicity scale
(pK_BI₂) and the XB65/X40 benchmark interaction energies. Neither is wired up,
because the per-molecule reference values are paywalled and **entering them
from memory would fabricate the standard the test is supposed to provide** —
the one number in a validation that may never be recalled. Note also the
direction: pK_BI₂ is an ACCEPTOR (Lewis base) scale, so it tests V_S,min on the
base, not the σ-hole on the donor.

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
