# Dirac Product Architecture

Dirac is a scientific operating system for molecular discovery. Its stable top level is
eight human work contexts: Programs, Design, Structures, Campaigns, Synthesis,
Experiments, Knowledge, and Runs. Algorithms such as docking, QM, MD, FEP, ADME and AI
are Methods reached through Commands; they are not navigation destinations.

The executable substrate is:

`ObjectRef -> Command Registry -> Dispatcher -> Method/Job/Artifact/Provenance -> SDKs`

The browser adds `ScientificContextStore -> AppShell -> Workspace/View/Module registries`
over one persistent mol* `SceneService`. Agents use the same commands as people. A
Mission is delegated intent, a Run is one attempt to fulfill it, and a Job is one
computational unit. This distinction prevents infrastructure records from masquerading
as scientific intent.

Views compose reusable Modules. Only implemented Views are surfaced; all 8 Workspaces
and 30 approved Views live in one checked registry so adding a real capability is
additive and an empty page cannot present itself as progress.
