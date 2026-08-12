# ADR-003 · Large results are artifacts, never inline payloads

    status: ACCEPTED · 2026-08-11
    enforcement: PENDING — app.artifact + app.job_artifact, then a gate asserting no
                 response body carries more than the inline threshold

## Decision

A Gaussian cube, a point cloud, a trajectory or a mesh is an ARTIFACT with a
content address, a media type, a role and a size. Responses carry a REFERENCE.
Only artifacts under a small inline threshold (32–64 KiB) may be embedded.

## Why

Today a cube is a JSON string field, and the physics daemon base64-encodes point
arrays into JSON. That is survivable for a browser demo and wrong for every other
consumer:

- an MCP tool result containing 2 MB of base64 destroys the token economy of the
  conversation it is part of;
- a CLI cannot stream, range-request or verify what it cannot address;
- `app.job` has a method-specific `field_cube_id`, and the path from there leads to
  `docking_result_id`, `fep_result_id`, `md_trajectory_id` — schema explosion.

The substrate already exists: `app.blob` enforces `digest(bytes,'sha256') =
sha256`, so the store cannot hold a mislabelled blob. What is missing is promoting
it to a first-class API object.

## Consequences

- `app.artifact` (id, blob_sha256, media_type, role, size, metadata) and
  `app.job_artifact` (job → artifact, role, ordinal). `field_cube_id` stays as a
  compatibility/query column and stops being the pattern.
- `GET /v2/artifacts/{id}` and `/metadata`, with range requests.
- CLI gets `artifacts inspect|fetch|verify`; `verify` re-computes SHA-256 from the
  downloaded bytes rather than trusting the declared digest.
- MCP returns `dirac://artifacts/sha256:…` and never the bytes.

## How this ADR fails

The first response body over the inline threshold that ships a payload instead of
a reference.
