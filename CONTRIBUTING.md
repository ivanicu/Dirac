# Contributing to Dirac

Dirac welcomes focused fixes, scientific-method improvements, documentation corrections
and product work that preserve its shared contracts and evidence boundaries.

## Start with the boundary

Before editing, identify which layer owns the change:

- domain and application vocabulary: `contracts/`
- application behavior: `backend/dirac_app/`
- scientific computation: `backend/motif/` and Method descriptors
- execution and persistence: `backend/execution_control/`, `backend/executors/`, `backend/db/`
- browser product: `src/app/` and `src/app.frontend.facets.molstar-rdkit.editable/`
- shared chemistry: `src/chemistry.backend.perception.rdkit-wasm.editable/`

Do not implement scientific behavior independently in HTTP, CLI, browser or agent adapters.
The semantic Command and Method boundaries are shared deliberately.

## Local setup

```bash
git clone https://github.com/ivanicu/Dirac.git
cd Dirac
npm ci
npm run build:dirac
npm run build:motif-workbench
```

Dirac Workspace and Motif Workbench have separate development builds. See the
[README](README.md#run-locally) for local serving and backend setup.

## Make a change

1. Start from current `main` and inspect the existing architecture and status.
2. Keep the change scoped to one decision or defect.
3. Preserve explicit states such as refused, stale and unverified; do not convert them into
   successful output for presentation.
4. Add focused tests at the layer that owns the behavior.
5. Update `CHANGELOG.md` when the change is user-visible.
6. Run the relevant checks and inspect the final diff.

## Verification floor

```bash
node scripts/check_docs_facts.mjs
node scripts/check_contract_drift.mjs --redproof
python3 scripts/gen_commands.py --check
python3 scripts/gen_contracts.py --check
node_modules/.bin/tsc --noEmit --incremental false -p tsconfig.json
npm run build:dirac
npm run build:motif-workbench
```

Backend, migration and cross-surface changes should also run `bash scripts/gates.sh` with
their real dependencies available. A skipped dependency-bound check is unverified.

## Pull requests

A useful pull request states:

- the user or scientific problem;
- the owning architecture boundary;
- what was deliberately left unchanged;
- the exact verification performed;
- screenshots for visible changes;
- migration, compatibility or scientific-validity implications.

CI must be green. Passing transport and execution tests does not, by itself, validate a
scientific Method.

## Upstream and vendored code

Most `src/mol-*` code is vendored from mol\*. Avoid editing it unless the change cannot live
in a first-party Dirac area. See [`src/VENDORED.md`](src/VENDORED.md).

## Security

Do not disclose vulnerabilities in a public issue. Follow [`SECURITY.md`](SECURITY.md).
