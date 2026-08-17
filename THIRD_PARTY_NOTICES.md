# Third-party notices

Dirac contains or distributes third-party software. This file records the principal
boundaries; the upstream source and license remain authoritative.

## mol\*

Dirac uses and vendors portions of [mol\*](https://github.com/molstar/molstar),
Copyright (c) 2018–2026 mol\* contributors, under the MIT License.

The physical source boundary and upstream synchronization procedure are documented in
[`src/VENDORED.md`](src/VENDORED.md). The upstream contributor history remains available in
the mol\* repository.

## RDKit and RDKit-JS

The browser runtime at
`src/app.frontend.facets.molstar-rdkit.editable/assets/rdkit/RDKit_minimal.{js,wasm}`
is distributed as part of [RDKit](https://github.com/rdkit/rdkit) under the
BSD 3-Clause License. See the [RDKit license](https://github.com/rdkit/rdkit/blob/master/license.txt).

## JavaScript and Python dependencies

Package-specific copyright and license terms for npm and Python dependencies remain with
their respective packages. `package-lock.json` and the backend environment/lock files
identify the resolved dependency sets used by Dirac.
