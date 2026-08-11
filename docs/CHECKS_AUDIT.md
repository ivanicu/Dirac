# Checks Audit — "a check that cannot fire"

Hunt for one bug class across the whole repo, triggered by today's
`ecp_for()` incident in `backend/field_server.py` (fixed 2026-08-11): a
`try/except` was wrapped around `gto.basis.load_ecp(basis, s)`, but pyscf
returns an **empty list** for an element the basis defines no ECP for — it
does not raise. The `except` branch was dead code; every heavy element got an
ECP entry unconditionally, and iodine ran all-electron under `sto-3g` while
`meta['ecp']` told the UI and the database that a pseudopotential had been
applied. Wrong physics, wearing the green light of the function written to
prevent it.

Instances of this class travel in families — a fix in one module rarely
propagates to a sibling module written by a different session. This hunt
looked for that specifically: not new bugs in general, but **this exact
shape**, reading every occurrence in `backend/*.py`, `backend/physics/*.py`,
`backend/db/*.sql`, `backend/db/migrations/*.sql`,
`src/app/**`, `src/app.frontend.facets.molstar-rdkit.editable/**`,
`src/chemistry.backend.perception.rdkit-wasm.editable/**`, `scripts/*.mjs`,
`design/check_palette.py`.

Method: for every candidate, name the exact `file:line`, state what the
check is supposed to catch, and **prove** the failure path is unreachable by
running something (a Python snippet against the real library, a live SQL
query against the `dirac` database, a Node snippet against the real JSON/JS
semantics) — never by inspection alone. A candidate that could not be proven
is reported as UNVERIFIED, not padded into the confirmed list.

## Scoreboard

| | count |
|---|---|
| files in the named hunt zone | ~117 (13 backend `.py` + 12 `.sql` + 3 `src/app` + 15 `molstar-rdkit` `.ts` + 56 `rdkit-wasm` `.ts` + 15 `scripts/*` + 3 `design/*`) |
| files read in full | ~35 (every backend `.py` and every migration `.sql` that defines a `CHECK`/function; all of `design/`; most of `scripts/`) |
| files grep-swept for the class's textual signatures (`except`, `.match(`, `JSON.parse`, `isfinite`/`isnan`, `>=`/`<=` beside a budget/tolerance name) but not read start-to-end | the remainder, concentrated in `src/chemistry.backend.perception.rdkit-wasm.editable/visual-r4/**` and the `molstar-rdkit` facet subdirectories |
| **CONFIRMED** (proven, witness test in `backend/tests/test_cannot_fire.py`) | **3** — all three FIXED 2026-08-11, see RESOLUTION |
| UNVERIFIED / near-miss (documented below, no witness — not exploitable or not provably reachable) | 4 |

A hunt with no denominator is not a measurement: the honest statement is "3
confirmed out of ~35 files read closely enough to see this shape, inside a
~117-file zone swept at least once for its textual signatures." Deep coverage
of `visual-r4/**` (the WebGPU representation compiler, ~25 files) was not
completed — it was grep-swept for `except`/`catch`/comparison patterns and
nothing surfaced, but a false negative there is more likely than in the files
read in full, and it is called out rather than silently folded into "checked."

---

## CONFIRMED

### F1 — `backend/physics/mep_surface.py:96-109`, `_ecp_for()`

**What it's supposed to catch:** whether the basis actually defines an
effective core potential for a heavy element (Z≥37), so that the correct
ECP is attached to the Mole and the core is genuinely replaced — the same
question `field_server.py`'s `ecp_for()` answers.

```python
def _ecp_for(atoms, basis: str) -> dict:
    ...
    for symbol in {s for s, _ in atoms}:
        if table.GetAtomicNumber(symbol) < ECP_FROM_Z:
            continue
        try:
            gto.basis.load_ecp(basis, symbol)
        except Exception:                       # noqa: BLE001 — basis defines none
            continue
        ecp[symbol] = basis
    return ecp
```

**Why the except branch is unreachable:** `gto.basis.load_ecp` returns `[]`
(falsy, not an exception) when the basis defines no ECP for the element.
Proven directly against pyscf:

```
$ backend/env/bin/python3 -c "
from pyscf import gto
r = gto.basis.load_ecp('sto-3g', 'I')
print(repr(r), bool(r))"
[] False
```

The return value is never inspected — only whether the call raised. So
`_ecp_for(['I','C'], 'sto-3g')` returns `{'I': 'sto-3g'}` unconditionally,
claiming an ECP that does not exist. Reproduced with the exact code:

```
mep_surface._ecp_for(sto-3g) claims ECP for: {'I': 'sto-3g'}
but gto.basis.load_ecp(sto-3g, I) actually returns: []
```

This is **the same defect field_server.py's `ecp_for()` had until today's
fix** (`git`-visible as the `FIXED 2026-08-11` comment at
`backend/field_server.py:718-727`, and the regression witness
`test_an_ecp_claim_must_mean_core_electrons_were_replaced` in
`backend/tests/test_physics_contracts.py`), reintroduced in the sibling
module that computes the σ-hole surface. `_ecp_for` is called at three sites
(`mep_surface.py:321, 440, 471`) and its output is reported verbatim as
`meta['ecp']` — the same UI/DB-facing claim that was wrong in the fixed
incident.

**Reach:** `compute_surface_mep` (the only implementation behind
`/surface/mep` in `backend/physics/server.py`) and `mep_at_points` both call
`_ecp_for`, and `backend/physics/server.py` places **no basis whitelist** on
top of it (unlike `field_server.py`'s `ALLOWED_BASIS` check) — any basis
string the caller sends reaches `_ecp_for` directly.

**Witness:** `test_mep_surface_ecp_claim_must_mean_core_electrons_were_replaced`
in `backend/tests/test_cannot_fire.py`. Currently FAILS (see run log below).

---

### F2 — `backend/physics/mep_surface.py:339` + `backend/physics/server.py:116`

**What it's supposed to catch:** refuse a request whose predicted SCF cost
exceeds the caller's wall-clock budget, before any compute starts.

```python
predicted = estimated_scf_seconds(mol.nao) / (GPU_SPEEDUP if on_gpu else 1.0)
if max_seconds and predicted > max_seconds:
    raise ValueError(...)
```

**Why it cannot fire when `max_seconds` is NaN:** NaN is truthy in Python,
so the `if max_seconds and ...` short-circuit does not save it — but
`predicted > max_seconds` is `False` for **any** `predicted` when
`max_seconds` is NaN. Proven directly:

```
$ backend/env/bin/python3 -c "
import json
req = json.loads(b'{\"molfile\": \"x\", \"max_seconds\": NaN}')
max_seconds = float(req.get('max_seconds', 120.0))
predicted = 999999.0
print('guard fires?', bool(max_seconds and predicted > max_seconds))"
guard fires? False
```

Python's `json.loads` accepts the bare literal `NaN` in a request body by
default (it is a documented extension, on unless `parse_constant`/strict
mode is set), so a single JSON token from any caller of
`POST /surface/mep` disables the guard.

**No second line of defence:** unlike `field_server.py`'s `run_scf`, which
also installs an in-loop SCF watchdog (`mf.callback = _watchdog`, checking
`time.time() > deadline` on every cycle), `compute_surface_mep` sets
`mf.max_cycle = 120` and calls `mf.kernel()` with **no callback at all**. Once
the pre-flight check above is defeated, nothing bounds the wall clock except
the SCF's own convergence.

**This is the exact class `field_server.py` was fixed for twice** — once
inside `run_scf` (`backend/field_server.py:818-820`,
`if not math.isfinite(max_seconds): max_seconds = DEFAULT_MAX_SECONDS`) and
once in the HTTP handler (`backend/field_server.py:1178-1181`). Neither
`mep_surface.py`'s `compute_surface_mep` nor `physics/server.py`'s
`Handler.do_POST` (which passes
`float(req.get('max_seconds', DEFAULT_MAX_SECONDS))` straight through, line
116) carries either fix.

**Witnesses** (both in `backend/tests/test_cannot_fire.py`, currently FAIL):
- `test_mep_surface_max_seconds_is_never_finiteness_checked` — fast,
  source-level: confirms neither `mep_surface.compute_surface_mep` nor
  `physics.server.Handler.do_POST` contains `isfinite`/`isnan` anywhere.
- `test_mep_surface_nan_budget_disables_the_predicted_cost_gate` — slow
  (~30 s), end-to-end: monkeypatches `estimated_scf_seconds` to report a
  predicted cost of `1e9` seconds, sends `max_seconds=nan`, and shows the
  real (unpatched) function runs a full methane/sto-3g SCF to completion
  instead of refusing. Gated behind `DIRAC_TESTS_SKIP_SLOW=1`.

> **⚠ LIVE UPDATE, observed mid-hunt** (another session is actively editing
> `backend/physics/mep_surface.py` and `backend/physics/server.py` right
> now — `git diff` shows in-flight, uncommitted changes). Two things landed
> while this audit was being written:
> ① `physics/server.py` gained an `ALLOWED_BASIS` whitelist +
> `validated_basis()`, closing the "no basis whitelist" gap noted above as
> a related-but-unwitnessed observation.
> ② `mep_surface.py` gained an **in-loop SCF watchdog**
> (`PhysicsBudgetExceeded` / `_install_watchdog`, installed via
> `mf.callback` in both `compute_surface_mep` and `mep_at_points`) — this is
> the "second line of defence" this audit noted as ABSENT.
> **Both witness tests were re-run against this in-progress state and still
> FAIL**, because the new watchdog computes
> `deadline = time.time() + max_seconds` with **no `isfinite` clamp on
> `max_seconds` anywhere in the diff** — so a NaN budget produces
> `deadline = nan`, and `time.time() > nan` is `False` forever, exactly the
> mechanism this finding names, now defeating TWO guards (the pre-flight
> predicted-cost check AND the brand-new in-loop watchdog) instead of one.
> The root cause was never "missing a watchdog" — it is "`max_seconds` is
> never checked for finiteness anywhere in this module or its HTTP layer,"
> and that is still true after this in-flight change. Re-verify this section
> and the witness output once that session's edit settles.

**Related, not separately witnessed** (same file, same absent-defense
pattern, out of scope for a "cannot fire" witness because there is no guard
to defeat — it is simply missing): `mep_at_points` (`mep_surface.py:462-482`)
takes no `max_seconds` at all and runs an unbounded `mf.kernel()`; and
`physics/server.py` has no basis whitelist, so an arbitrary basis string
(e.g. a large Pople/Dunning basis) reaches `gto.M(...)` directly — the exact
risk `field_server.py`'s `ALLOWED_BASIS` comment warns about ("an arbitrary
basis... allocates unbounded memory in the init-guess phase the deadline
provably cannot see").

---

### F3 — `design/check_palette.py:120-128`, the diverging-pair ΔE gate

**What it's supposed to catch:** that each `+`/`-` colour pair
(`DIVERGING_PAIRS`, e.g. the colourblind-safe `viz-cb-pos`/`viz-cb-neg`) stays
perceptually separable (ΔE ≥ 0.10) after the mid-saturation ruling desaturates
the palette. This check is **wired into CI**:
`.github/workflows/dirac.yml` Gate 3, `scripts/gates.sh` `gate-3-palette`.

```python
for a, b, label in DIVERGING_PAIRS:
    if a not in tokens or b not in tokens:
        continue
    separation = delta_e(tokens[a], tokens[b])
    ...
    if separation < MIN_PAIR_DE:
        failures.append(...)
```

**Why it cannot fire on a renamed/dropped token:** the pair is looked up by
exact key. If either half is renamed (the file's own docstring names exactly
this failure mode as already having happened once, with the Tailwind
defaults), `a not in tokens` is `True` and the pair is silently skipped —
**not counted as a failure**. The ceiling and contrast checks still run on
whatever key IS present (they iterate `tokens.items()` directly), so only
the pairing/separability guarantee — the one this loop exists for — goes
dark.

Proven on a scratch copy of the real `design/tokens.css` (never the checked-in
file), renaming `--viz-cb-neg` → `--viz-cb-neg-renamed` in both themes and
running the real `main()`:

```
Night — background #0a0e14
    highest chroma 0.106 against a 0.106 ceiling
    electrostatic potential +/-      ΔE 0.151  ok
    orbital phase                    ΔE 0.171  ok
    lipophilic / polar               ΔE 0.156  ok
...
palette OK — every token inside the ceiling, legible, and separable
exit code: 0
```

The colourblind-safe pair is silently absent from every printed line, and
the gate still reports success — a false green on a CI-enforced check.

**Witness:** `test_check_palette_diverging_pair_gate_silently_skips_a_renamed_token`
in `backend/tests/test_cannot_fire.py`. Currently FAILS.

---

## UNVERIFIED / near-miss (no witness, listed honestly rather than padded)

These were investigated with the same rigor as the three above and either
turned out **not exploitable today** (a sibling guard already closes the
gap) or **not reachable** through any path found. Recorded so nobody
re-discovers the same non-bug from scratch, and so a future change that
removes the sibling guard has something to check against.

1. **`backend/db/migrations/005_numeric_hygiene.sql:93-95`,
   `result_positive_where_physical`.** `CHECK (... OR value_num > 0)` is
   individually NaN-permeable — proven live against the `dirac` database:
   `SELECT 'NaN'::numeric > 0` returns `t`. In isolation this constraint
   would let a NaN "potency" through. **Not exploitable**: the same
   migration's mechanical sweep also adds `result_value_num_finite`
   (`CHECK (meta.is_finite(value_num))`) to the same column, confirmed
   present on the live `bio.result` table (`\d bio.result`), and Postgres
   rejects an INSERT if *any* CHECK on the row fails — so NaN is still
   rejected overall. Flagged because a future migration that touches this
   table without noticing the redundancy could drop the wrong constraint.

2. **`backend/db/migrations/006_producer_identity.sql:117-173`, the coarse
   cache key's "READ CONTRACT."** The migration comment specifies, in
   detail, a Kabsch-superposition + `assert rmsd < 0.1 Å` check that a coarse
   cache hit (`compound_id` + `conformer_hash`) must pass before being served
   — "without it the coarse key is a mechanism that can only fail silently."
   Grepped the whole backend: the coarse key is **written**
   (`field_server.py:271-318`, `conformer_hash_for`) but there is **no code
   anywhere that reads it** — `db_get_cube` only does the exact
   `(molfile_sha256, kind, basis)` lookup. Not a "cannot fire" bug in the
   audited sense (there is no guard to defeat because the read path, and
   therefore the RMSD assert, does not exist yet) — but worth naming because
   the day someone adds the coarse-key read, this repo's own migration
   comment is the checklist and the assert is not optional.

3. **`backend/method_registry.py`'s `register_all` / migration 007's job
   ledger.** Both exist, are individually correct (`register_all`'s own
   `main()` self-check passes), and are **never called** from
   `field_server.py` or `physics/server.py` — `db_init()` in
   `field_server.py` only calls `meta.register_producer`, never
   `method_registry.register_all`. Not the audited bug class (nothing
   silently passes; the seam is simply unconnected), but relevant context:
   the `app.job` state-machine CHECK constraints in
   `backend/db/check_constraints.sql` are exercised only by that file's own
   synthetic gate fixtures, never by a running service.

4. **`src/app.frontend.facets.molstar-rdkit.editable/facets/pharmacophore-designer/model.ts:251`**,
   `fromJSON`'s radius parsing:
   `radius: typeof raw.radius === 'number' ? Math.min(3, Math.max(0.5, raw.radius)) : DefaultFeatureRadius[...]`.
   This differs from its own neighbours three lines up — `position` and
   `direction` are validated with `Number.isFinite(v)`, but `radius` uses a
   bare `typeof === 'number'`, and `typeof NaN === 'number'` is `true` in
   JS, so if `raw.radius` were ever `NaN` it would take the "clamp" branch
   and `Math.min`/`Math.max` would propagate the NaN silently past the
   `[0.5, 3]` range this exists to enforce. **Checked both reachable
   entrances and found neither can produce it**: (a) `fromJSON`'s only input
   is `JSON.parse(text)`, and standard `JSON.parse` throws a `SyntaxError` on
   a bare `NaN` token (verified: `JSON.parse('{"radius":NaN}')` throws) — it
   *can* produce `Infinity` from an overflowing literal like `1e400`, but
   `Math.min(3, Math.max(0.5, Infinity))` correctly clamps to `3`, so
   Infinity is not a problem here, only NaN would be, and NaN cannot arrive
   this way; (b) the only live setter, `DesignerModel.setRadius`, is called
   from exactly one place
   (`pharmacophore-designer/index.ts:200`,
   `this.model.setRadius(f.id, Number(radius.value))`) where `radius` is an
   `<input type="range" min="0.5" max="3" step="0.1">` — a browser range
   input's `.value` is always a valid in-range numeric string, so
   `Number(radius.value)` cannot be NaN either. The inconsistency is real
   and worth fixing for defence-in-depth (a future third caller of
   `setRadius`, or a change to accept a free-text radius field, would
   inherit the gap silently), but it is not a live, provable defect today,
   so it is not in the confirmed list or the witness file.

---

## RESOLUTION — 2026-08-11, all three fixed by the session that owns the files

Reported across sessions rather than patched here, because both files were
under live edit; the witnesses were written so that no change to this file is
needed when the fix lands. All three went green on `dac99d6`.

| | fix | how the fix was confirmed |
|---|---|---|
| **F1** ECP | `if not gto.basis.load_ecp(basis, s): continue` — the falsy `[]` that made the `except` unreachable | `test_..._ecp_claim_must_mean_core_electrons_were_replaced` asserts the CONSEQUENCE (`nelectron < ΣZ`), not the claim, so it cannot be satisfied by editing `meta['ecp']` |
| **F2** NaN budget | `clamp_budget()` at BOTH entry points (`compute_surface_mep`, `mep_at_points`); non-finite or negative → default, **zero preserved** | the slow end-to-end witness went **30.40 s → 0.00 s**: the pre-flight refusal now fires instead of the SCF running to completion. That timing IS the measurement |
| **F3** palette gate | a missing token is now a failure naming the token, not `continue` | renaming `--viz-cb-neg` exits 1; restoring it exits 0 (both directions run) |

The peer session's own reading, worth keeping: *"a guard that never fires and a
guard that never needs to fire produce identical output, and my own coverage
sweep reports those paths green."* That is why this hunt had to come from
outside the file — the instrument that would have caught it is the one the
defect disables.

### Two of my own witnesses cried wolf, and both are recorded rather than amended

Neither was a false acquittal (the dangerous direction) — both were false
alarms on correct code. They are written down because they are the same defect
class as the bugs being hunted, committed by the hunter, twice in one file:

1. **`'isfinite' in inspect.getsource(...)`** — the fix was a CALL to a named
   `clamp_budget()`, so the property held while the proxy could not see it.
   A grep for a token encodes the INSTANCE the check was written against.
   Rewritten to call the normaliser, then to prove via a recorder that every
   entry point routes its raw input through it. The peer's framing was right:
   writing `isfinite` into the function to satisfy a grep would have been
   encoding the instance one level deeper.
2. **`max_seconds=0.001` in the watchdog witness** — the pre-flight cost gate
   refused *before* the SCF existed, so no watchdog was installed and none
   needed to be. A witness must REACH the step it judges; it now passes a
   generous budget and aborts from inside a recorder.

## Run log — `backend/tests/test_cannot_fire.py`

```
$ backend/env/bin/python3 backend/tests/test_cannot_fire.py
"check that cannot fire" witnesses — 4 tests, pytest ABSENT (standalone mode)
every test below is EXPECTED TO FAIL until its named finding is fixed
────────────────────────────────────────────────────────────────────────────────────────────────────
# 2026-08-11 11:2x — after dac99d6. Kept ABOVE the original red run, not
# instead of it: a green board with no memory of having been red is exactly
# the artifact this audit exists to distrust.
PASS    [F1] test_mep_surface_ecp_claim_must_mean_core_electrons_were_replaced      0.26s
PASS    [--] test_mep_surface_normalises_a_non_finite_budget_at_every_entry_point   2.04s
PASS    [--] test_mep_surface_in_loop_watchdog_is_installed_on_both_scf_paths       0.02s
PASS    [F2] test_mep_surface_nan_budget_disables_the_predicted_cost_gate           0.00s  <- was 30.40s
PASS    [F3] test_check_palette_diverging_pair_gate_silently_skips_a_renamed_token  0.00s
5 passed (fixed) · 0 failed · 0 skipped

# 2026-08-11 ~09:00 — the original hunt, all four red as designed:
FAIL    [F1] test_mep_surface_ecp_claim_must_mean_core_electrons_were_replaced   0.43s  (EXPECTED — confirmed unfixed defect)
FAIL    [F2] test_mep_surface_max_seconds_is_never_finiteness_checked         0.10s  (EXPECTED — confirmed unfixed defect)
FAIL    [F2] test_mep_surface_nan_budget_disables_the_predicted_cost_gate    30.40s  (EXPECTED — confirmed unfixed defect)
FAIL    [F3] test_check_palette_diverging_pair_gate_silently_skips_a_renamed_token   0.00s  (EXPECTED — confirmed unfixed defect)
────────────────────────────────────────────────────────────────────────────────────────────────────
0 passed (fixed) · 4 failed (confirmed defects still present) · 0 skipped

All red, as expected: every confirmed finding in docs/CHECKS_AUDIT.md is
still present in the code. This exit code is intentionally non-zero — do not
"fix" this suite by weakening an assertion; fix the file the assertion names.
```

**Red is correct right now.** All three findings above are unfixed as of
this writing. Each test will flip to PASS, individually, the moment whichever
session owns `backend/physics/mep_surface.py` / `backend/physics/server.py`
/ `design/check_palette.py` lands the corresponding fix — no change to
`test_cannot_fire.py` is required for that. Run
`DIRAC_TESTS_SKIP_SLOW=1 backend/env/bin/python3 backend/tests/test_cannot_fire.py`
to skip the one ~30 s real-SCF witness during iteration.

## What was explicitly ruled OUT (checked and found sound)

Named because a "nothing found here" that was never tested is not an
acquittal (P6/P5 discipline): `backend/field_server.py` (already fixed
today, both isfinite clamps present, `ALLOWED_BASIS` whitelist present,
`np.isfinite` guards on Gasteiger/Crippen charges); `backend/physics/torsion.py`
(`_verdict`'s fallthrough fails *safe* on NaN — reports `'severe'`, not a
silent pass); `backend/physics/coverage.py`, `backend/field_coverage.py`
(coverage sweeps with their own positive controls — `field_coverage.py`'s
`MALFORMED` block exists specifically to prove BROKEN/REFUSED/OK are
distinguishable); `backend/db/check_constraints.sql` (the schema's own attack
suite; already requires an expected SQLSTATE and positive controls);
`backend/db/migrations/004_scf_method_split.sql` (`parse_scf_method` raises
on an unrecognised label rather than defaulting); `scripts/check_css_braces.mjs`
(explicitly hardened against "a gate pointed at a file it cannot read... is a
misconfiguration, not a pass" — its own docstring names the trap this hunt
was looking for, and it defends against it); `scripts/check-pharmacophore-library.mjs`
(counts its own probe molecules and fails if fewer than expected are found —
guards against a silent zero). `scripts/check-chem-packs.mjs`,
`scripts/check-mn-r4-compiler.mjs`, and the `package.json` scripts
`test:chem-packs`/`test:mn-r4-compiler`/`build:lib-extra` all reference a
stale path (`src/mol-plugin-chem`, renamed at some point to
`src/chemistry.backend.perception.rdkit-wasm.editable` /
`src/app.frontend.facets.molstar-rdkit.editable`) and **error out immediately**
on import resolution — the opposite failure direction from this hunt's class
(loud and always-red, not silently green), and none of the three is wired
into `scripts/gates.sh` or the CI workflow, so nobody is currently trusting a
false pass from them. Left as-is per the task's scope (proof, not patches).
