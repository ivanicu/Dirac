-- 012 · the servable view carries app.field_cube.meta.
--
-- WHY THIS EXISTS AS ITS OWN MIGRATION, and it is the more useful half of 011:
-- adding a column to a base table does NOT add it to a view built on that table.
-- Migration 011 added field_cube.meta; the read path selects `fc.meta` from
-- app.v_field_cube_servable, which is a VIEW from 009 — so every cache read failed
-- with "column fc.meta does not exist".
--
-- AND THE FAILURE MODE IS THE ONE THIS PROJECT KEEPS PAYING FOR: db_get_cube
-- catches the exception, logs one line, and returns None, which the caller reads as
-- "not cached" and degrades to computing. So the service kept answering every
-- request correctly while its persistent cache was ENTIRELY DEAD — three
-- consecutive requests for the same molecule all reported cache=computed, and the
-- only evidence was one log line per request. A fallback that hides the primary's
-- death, again, in a code path written this morning.
--
-- The lesson worth keeping in SQL rather than in a commit message: a migration that
-- adds a column must state which VIEWS project that table, or the column exists and
-- nothing can see it. As of today: v_field_cube_servable (here),
-- v_field_cube_current and v_field_cube_stale (006 — they project producer identity
-- and are not on the read path, so they are deliberately left alone), and
-- v_cache_health / v_cache_meta_coverage (aggregates, no per-row columns).

BEGIN;

CREATE OR REPLACE VIEW app.v_field_cube_servable AS
SELECT c.id, c.molfile_sha256, c.kind, c.basis, c.blob_sha256, c.scf_energy_ha,
       c.converged, c.n_atoms, c.n_basis, c.homo_ev, c.lumo_ev, c.seconds,
       c.toolkit_id, c.computed_at, c.scf_reference, c.scf_converger, c.producer_id,
       c.compound_id, c.conformer_hash, c.method_row_id,
       m.method_id, m.version AS method_version,
       p.service AS producer_service, p.version AS producer_version,
       c.meta
  FROM app.field_cube c
  JOIN meta.method m ON m.id = c.method_row_id
  LEFT JOIN meta.producer p ON p.id = c.producer_id
 WHERE m.superseded_at IS NULL;

COMMENT ON VIEW app.v_field_cube_servable IS
    'Servable on METHOD currency, not producer identity (009). Carries c.meta as of '
    '012 so a cache hit returns the same VALUES as a fresh compute and not merely '
    'the same keys — the column was added in 011 and invisible here for the length '
    'of one commit, during which every cache read failed and silently degraded to '
    'recompute.';

INSERT INTO meta.migration (filename, sha256, content_sha256, hash_source)
VALUES ('012_servable_view_carries_meta.sql',
        '\x5e503ccca719d70aa2fa6c7b8f84817562484db771c2a106860ea5771f3e4860'::bytea,
        '\x5e503ccca719d70aa2fa6c7b8f84817562484db771c2a106860ea5771f3e4860'::bytea, 'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
