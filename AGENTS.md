# Dirac contributor and agent guide

Dirac is one integrated repository with multiple product surfaces. Work lands on `main`
as small verified commits; product facets are directory boundaries, not long-lived forks.

## Canonical repository

```text
origin   https://github.com/ivanicu/Dirac.git   canonical
upstream https://github.com/molstar/molstar.git reference/vendor updates only
```

- `main` is the integrated product state.
- Short-lived `wip/<topic>` branches are acceptable for isolated work, but must merge or
  close promptly.
- `master` may remain a local upstream mol\* pointer; never push it to `origin`.
- Never use `git reset --hard` or `git clean -fd` in the shared worktree.

## Runtime topology

Use the existing supervised services on the canonical workstation:

| Port | Surface |
|---:|---|
| `1360` | full Dirac Workspace (`build/dirac`) |
| `1370` | focused Motif Workbench with FEP and Field (`build/discovery-lab`) |
| `1355` | read-only operations view |
| `8901` | application and scientific backend |

Do not start duplicate servers on these ports or invent another port for verification.
Rebuild the intended bundle and reload its existing service. On a clean checkout, the
commands in `README.md` may be used to start one local instance.

## First-party boundaries

| Area | Responsibility |
|---|---|
| `contracts/` | canonical domain, Command, Method and Error contracts |
| `backend/dirac_app/` | application registry, dispatcher and handlers |
| `backend/motif/` | governed molecular-design and scientific workflows |
| `backend/execution_control/`, `backend/executors/` | durable execution, admission and placement |
| `backend/db/` | PostgreSQL schema and forward-only migrations |
| `python/` | Python SDK, CLI and safe agent adapter |
| `src/app/` | Dirac Workspace shell, context, scene ownership and clients |
| `src/app.frontend.facets.molstar-rdkit.editable/` | Dirac Workspace and Motif Workbench frontends |
| `src/chemistry.backend.perception.rdkit-wasm.editable/` | shared RDKit-JS chemistry substrate |

Vendored/upstream ownership is documented in `src/VENDORED.md`. Do not edit a vendored
area as though it were a first-party Dirac module.

## Shared-worktree discipline

1. Inspect `git status` and recent commits before editing.
2. Preserve changes you did not create. Never rewrite a shared file from stale context.
3. Use anchored, incremental edits when concurrent work exists.
4. Commit one logical action at a time and inspect the staged diff before committing.
5. Push verified commits promptly; an unpushed commit is not delivered.
6. Regenerate derived files only after the source change is stable.

Shared integration surfaces include `package.json`, `package-lock.json`, canonical
contracts, shell registries, RDKit substrate, root documentation and CI workflows. Changes
there require product-wide verification, not only a facet test.

## Architecture rules

- Scientific behavior belongs below adapters. Browser, HTTP, Python, CLI and agent
  surfaces share semantic Commands.
- Scientific computation is a versioned Method; long work returns a durable Job.
- Large outputs are content-addressed Artifacts linked to exact provenance.
- PostgreSQL owns durable scientific and execution state.
- Dirac Workspace owns one `ScientificContextStore` and one persistent
  `SceneService`.
- Motif Workbench may provide focused projections, but may not create a private Command
  registry, Job state machine, artifact identity or campaign-generation clock.
- Planned, refused, stale, unverified and completed are distinct states. Never render one
  as another.

## Verification before push

Run checks proportional to the change. The portable release floor is:

```bash
node scripts/check_docs_facts.mjs
node scripts/check_contract_drift.mjs --redproof
python3 scripts/gen_commands.py --check
python3 scripts/gen_contracts.py --check
node_modules/.bin/tsc --noEmit --incremental false -p tsconfig.json
npm run build:dirac
npm run build:motif-workbench
```

For broad backend, database or cross-surface work, run:

```bash
bash scripts/gates.sh
```

Some gates require PostgreSQL, the scientific daemon or configured executors. A skipped
dependency-bound gate is `UNVERIFIED`, not green. CI must be green before a change is
called delivered.

## Build commands

```bash
npm run build:dirac
npm run build:motif-workbench
```

Do not commit `build/`, `node_modules/`, browser profiles, credentials, generated local
state, or `*.tsbuildinfo`.

## RDKit-JS boundary

- Reuse `src/app.frontend.facets.molstar-rdkit.editable/assets/rdkit/`; never duplicate the
  WASM bundle per frontend.
- Unavailable browser APIs must be refused or routed through a declared backend Method.
- Preserve the atom-index contract through molfile construction, RDKit parsing, SMARTS,
  SVG interaction and mol\* selection.
- PDB V2000 molfile writers must keep the spec-exact counts line used by desktop RDKit.

## Commit messages

Use:

```text
[type.area.impact.Dx{valence}] reason
```

The subject states why the change exists. The body records the decision, evidence and
important boundary; it does not narrate the diff.

Examples:

```text
[feat.discovery.λ.D8+] expose FEP and Field through one focused navigation contract
[fix.execution.ρ.D8+] fence terminal publication to the admitted worker identity
[docs.meta.λ.D8+] make repository claims derive from canonical registries
```

## Documentation ownership

- `README.md` — product, workflows and first-run path
- `STATUS.md` — source-derived connected capability and evidence boundary
- `ARCHITECTURE.md` — stable technical boundaries and invariants
- `CHANGELOG.md` — user-visible changes
- `docs/README.md` — documentation map
- `CONTRIBUTING.md` — contributor workflow
- `SECURITY.md` — vulnerability reporting and deployment boundary

Live counts belong in `STATUS.md`; README should link to them rather than duplicate them.
Superseded plans belong under `docs/archive/`, not beside current guidance.
