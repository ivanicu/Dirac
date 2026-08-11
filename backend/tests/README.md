# backend/tests — the physics contracts

**THE ONE RULE: this suite must pass before any backend refactor lands.**
Not "should", not "ideally". `backend/field_server.py` owns pyscf, the ECP path,
the Gasteiger path, the cube writer and the wall-clock deadline, and before this
directory existed it contained **zero assertions** — `grep -c "assert "
backend/field_server.py` returned `0`. Splitting a 1121-line file with no
assertions into a library is not a refactor, it is a rewrite with no way to know
whether the physics survived. Run this first, run it again after, and the two
outputs must match.

```bash
backend/env/bin/python backend/tests/test_physics_contracts.py     # ~35 s
DIRAC_TESTS_SKIP_SLOW=1 backend/env/bin/python backend/tests/test_physics_contracts.py   # ~1 s, no SCF
backend/env/bin/pytest backend/tests/test_physics_contracts.py     # only if pytest is ever installed
```

**pytest is NOT installed in `backend/env`** (verified 2026-08-11:
`backend/env/bin/python -c "import pytest"` → `ModuleNotFoundError`). The file is
therefore dual-mode: plain pytest style, plus its own `main()` with PASS / FAIL /
FINDING / SKIP verdicts, a final count, and a non-zero exit on failure. A gate
that depends on a package which is not there is not a gate. The standalone
runner's four verdict paths were each fired with a planted test before this
README was written — FAIL, ERROR, XPASS, SKIP all report and all exit 1 where
they should.

## Why each test exists — the scar tissue

Every check below is a defect that **shipped**, and every one of them passed the
backend's own honesty gates (converged · charge balanced · far field → 0) while
it was wrong. That is the whole reason this file is written against *external*
anchors and *cross-language joins* wherever it can be.

| test | the incident it is scar tissue of |
|---|---|
| `test_module_imports_without_a_database` | the library must import with Postgres down; `db_init()` belongs to `__main__` only. If it migrates to import time, every test below silently becomes a test of the database. |
| `test_ecp_attached_for_iodine_under_def2svp` | **Iodine ran ALL-ELECTRON under def2-svp.** pyscf does not auto-attach the ECP that def2 bases require from Rb (Z=37) up. SCF converged, charge balanced, the far field decayed — and the sigma-hole came out **58 kcal/mol wrong with the WRONG SIGN**. Fixed by `ecp_for()`. Verified here against pyscf itself: with the ECP, I+H is 26 electrons and I's effective charge is 25; without, 54 and 53. |
| `test_ecp_absent_for_bromine_under_def2svp` | the mirror case — def2-svp defines no Br ECP, so none may be invented. Read out of `gto.basis.load_ecp`, not out of anyone's memory. Also pins `ECP_FROM_Z` between Br and Rb. |
| `test_sto3g_defines_no_ecp_for_iodine_which_is_why_it_must_not_be_used` | the **pairing**, as one fact: sto-3g has no iodine ECP, therefore sto-3g + iodine *is* the all-electron regime that produced the wrong sign. `DEFAULT_BASIS` is `sto-3g`, so this is the path a /field request with no `basis` takes. |
| `test_only_def2svp_covers_iodine_among_the_allowed_bases` | an external fact about the *(basis, element)* pair, read from pyscf: 6-31g covers neither I nor Br; 6-31g\* covers Br but not I; sto-3g covers I all-electron. **Exactly one whitelisted basis handles a heavy halogen correctly.** The whitelist validates the basis NAME and nothing validates the pair. |
| `test_iodobenzene_homo_matches_the_experimental_ionisation_potential` | the strongest check in the file, because its anchor is **outside our chain**: −HOMO vs the measured first vertical IP of iodobenzene, 8.7 eV. Nothing we compute, cache, write or report can bend a photoelectron spectrum. Measured 2026-08-11: −8.83 eV, off by 0.13 eV, in 19–29 s. If the ECP ever comes off, the iodine core reappears and this moves by far more than the 1.5 eV tolerance — which no internal consistency gate could see. |
| `test_basis_default_has_exactly_one_home` | the ECP fix landed on **one of two paths** because the HTTP layer kept its own literal `'sto-3g'` default (commit `07f703b`). Two homes for one constant. Asserted over the **AST**, not a grep, so comments and docstrings cannot fake a pass: one `DEFAULT_BASIS` assignment, every `.get('basis', X)` defaults through the *name*, and no basis literal exists anywhere else. |
| `test_positive_control_the_basis_literal_scanner_can_fire` | a `0 found` from an instrument that has never returned non-zero is silence, not an acquittal. A second home is planted and must be detected. |
| `test_allowed_basis_equals_the_db_check_set` | `ALLOWED_BASIS` and `app.field_cube`'s `basis` CHECK are one fact in two languages, and **this join has drifted before**. A basis Python accepts and SQL rejects means the field computes, the blob is written, and the row is refused — an orphan blob and a request that paid for nothing. Read from the **migration chain** (later migration wins), so the gate runs with Postgres down. Also pins `'none'` as the classical key: in the DB set, never in `ALLOWED_BASIS`. |
| `test_positive_control_the_db_check_parser_can_fire` | the SQL parser must distinguish *found nothing* from *found something*, and must honour a later override. Otherwise the join above passes by returning an empty set. |
| `test_pf6_classical_mep_refuses_and_names_the_elements` | **PF6- shipped a uniformly ZERO classical MEP as a normal result.** Gasteiger yields NaN on hypervalent P; `nan_to_num` laundered silence into a field — a picture of nothing, indistinguishable from a molecule with no electrostatics. The refusal must name the elements (`F/P`), the method, and the path that works (`mep_qm`). |
| `test_positive_control_benzene_classical_mep_has_resolution` | an instrument that only ever refuses has no resolution. The PF6- refusal is evidence only if the same code path yields a real field where it should: benzene, vmin ≈ −51, vmax ≈ +24 kcal/mol. |
| `test_the_cube_writer_states_the_geometry_it_was_given` | the cube writer converts Å → Bohr, and dimensions agreeing is not units agreeing. The cube is parsed back: atom count, grid dims, voxel count, min/max against meta, and the origin in Bohr × `BOHR` back to the padded bounding box. A unit inversion is otherwise completely silent. |
| `test_deadline_fires_from_inside_the_scf_loop` | **a 43-heavy-atom molecule (HEM) held 22 cores for 36 minutes.** HF is O(nao⁴) per *iteration* and the iteration count is unbounded, so `MAX_QM_ATOMS` bounds SIZE and nothing else. Benzene is 12 atoms — a tenth of the cap — and a 0 s budget must still stop it, which can only happen from a check *inside* the loop. Positive control in the same test: a real budget converges. Also asserts a refused SCF is **not** cached (else every retry fails instantly forever) and that `_eri` is dropped (what killed the daemon mid-sweep). |
| `test_http_path_clamps_non_finite_max_seconds` | `max_seconds: "nan"` would have failed the deadline **OPEN**: NaN fails every comparison, so `min(max(nan, 1.0), 900.0)` is nan and `time.time() > nan` is False forever. One JSON token, no deadline. Made *discriminating* by shrinking `DEFAULT_MAX_SECONDS` for the duration — if nan falls back to the default the request must be refused with `reason='budget'`; if it leaks through, the request succeeds. A generous finite budget goes through the same path as the positive control, so the refusal cannot be credited to something structural. |

## FINDING is a verdict, not a failure

Three tests are marked `@known_defect`. They assert a **contract** the code
violates today. They are written against the property rather than the behaviour,
they print loudly, they do **not** fail the suite — and under both runners they
become a **hard failure the day the code is fixed and the marker is left
behind** (strict xfail). This is the opposite of weakening a test until it
passes: nothing here was softened to make the run green.

| open finding | what is actually wrong |
|---|---|
| `test_an_ecp_claim_must_mean_core_electrons_were_replaced` | `ecp_for()` detects a missing ECP with `try/except`, but pyscf's `load_ecp` **returns an empty list instead of raising** — so the `except` branch can never fire and *every* element with Z ≥ 37 gets an ECP entry whether the basis defines one or not. `ecp_for(['I'],'sto-3g')` → `{'I': 'sto-3g'}`; pyscf then prints `ECP sto-3g not found for I` and builds the **all-electron** molecule while `meta['ecp']` reports `['I']` to the UI and to `app.field_cube`. **Incident 1 wearing its own green light.** Fix: test the return value, not the exception. |
| `test_an_uncovered_element_is_refused_as_chemistry_not_as_an_internal_error` | `BasisNotFoundError` is a `RuntimeError`, not a `ValueError`, and the handler maps non-`ValueError` to `reason='internal'` + a traceback. So `/field` with `basis='6-31g'` on any bromo- or iodo- compound shows a chemist a red **internal** failure for a request whose remedy is obvious (use def2-svp). Fix: validate `(basis, element)` coverage before `gto.M` and raise `ValueError` naming both plus the basis that works. |
| `test_run_scf_cannot_be_disabled_by_a_non_finite_budget` | the non-finite guard lives **only in `Handler.do_POST`**. No callable clamps its own budget, so `run_scf(mol, basis, max_seconds=float('nan'))` runs to completion with no bound. Today the only caller is the HTTP handler, so the shipped daemon is safe — **but this is precisely the hazard the library split creates.** The moment a CLI, job worker, notebook or sweep script calls `run_scf`/`field_quantum` directly, the deadline that cost 36 minutes to learn is one `nan` away from being off, and nothing raises. Fix during the refactor: clamp inside `run_scf` and `field_quantum`, and let the handler pass the request straight through. |

## Rules for adding to this file

- **A test you cannot make pass is a FINDING, never something to weaken.** Mark
  it `@known_defect` with the mechanism written out, or leave it failing. Do not
  loosen a tolerance, do not delete an assertion, do not narrow a case.
- **Every null needs a positive control.** A refusal, a zero, a "not found" or
  an empty scan is inadmissible until the same instrument has been shown to
  return the other answer. Three tests here exist for exactly that reason.
- **Prefer an anchor the code cannot move**: a photoelectron spectrum, a
  constraint in the SQL, pyscf's own basis tables, the electron count. An
  assertion that only compares our output to our output cannot detect a
  consistent lie, and every incident above was a consistent lie.
- **No database, no network, no daemon.** The SQL join reads the migration files
  in git. The HTTP test starts its own server on an ephemeral loopback port and
  shuts it down.
