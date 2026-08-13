# Domain Model

Every cross-layer identity is `{ kind: ObjectKind, id: string }`; naked UUIDs are not
domain objects. `contracts/domain/object-kinds.json` owns the 83 ObjectKinds and
`contracts/domain/relations.json` owns the controlled relation vocabulary.

Molecule is immutable chemical identity; Compound is a program-scoped candidate.
Protein and ProteinStructure differ; Complex and Pose differ; Prediction and Measurement
differ in epistemic status; Hypothesis is a belief while Decision is an action.

Durable scientific state, durable execution state, session scientific context, and
presentation-only state are separate classes. Presentation state never becomes evidence.
