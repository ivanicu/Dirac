# Scientific facets

Facets are browser modules over Dirac's shared scientific context, RDKit session and
single mol\* scene. They do not own routing, a second context store or private backend
APIs.

Current facet directories:

| Directory | Capability |
|---|---|
| `bond-atlas/` | shared ligand bond information |
| `field-wells/` | field computation and 3D artifact overlays |
| `halogen-audit/` | geometry/QM evidence boundary for halogen interactions |
| `ligand-physics/` | surface MEP and torsion Jobs |
| `pharmacophore-designer/` | editable pharmacophore model and screening |
| `property-cockpit/` | RDKit descriptor and rule summaries |

The AppShell registry in `src/app/shell/registries.ts` assigns these capabilities to
Views through Module definitions. A facet directory existing on disk does not by itself
make a View connected; the registry's `implemented` and `delivery` state is the product
truth.

Shared chemistry lives in
`src/chemistry.backend.perception.rdkit-wasm.editable/`. Facets consume that substrate
and the semantic application client; they must not duplicate the RDKit WASM, atom-index
mapping, Command schemas or Job state.

See [`../README.md`](../README.md) for browser-application boundaries and the root
[`STATUS.md`](../../../STATUS.md) for current connected capability.
