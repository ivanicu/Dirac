-- 023 · governed Motif endpoint, objective and protocol-resolved measurement ingestion.
--
-- bio.result is intentionally a dense numeric projection: value_num is NOT NULL.
-- It therefore cannot truthfully represent not_tested, missing, interval-censored or
-- failed-QC observations.  This ledger preserves the complete v2 measurement contract
-- and lets later projections include only semantically compatible rows.
BEGIN;

CREATE TABLE bio.measurement_v2 (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    measurement_key text NOT NULL UNIQUE,
    endpoint_definition_id uuid NOT NULL REFERENCES design.endpoint_definition(id),
    sample_ref jsonb NOT NULL,
    batch_id uuid REFERENCES chem.batch(id),
    compound_id uuid REFERENCES chem.compound(id),
    assay_id uuid NOT NULL REFERENCES bio.assay(id),
    protocol_ref jsonb NOT NULL,
    qualifier text NOT NULL CHECK (qualifier IN (
        'equal','less_than','less_or_equal','greater_than','greater_or_equal',
        'interval','not_tested','missing')),
    value_num numeric,
    lower_num numeric,
    upper_num numeric,
    unit text NOT NULL,
    quantity_dimension text,
    qc_status text NOT NULL CHECK (qc_status IN ('pass','warn','fail','not_assessed')),
    qc_reason_codes text[] NOT NULL DEFAULT '{}',
    missing_reason text,
    value_status text NOT NULL DEFAULT 'raw'
        CHECK (value_status IN ('raw','normalized','derived')),
    measured_at timestamptz NOT NULL,
    source_artifact_id uuid NOT NULL REFERENCES app.artifact(id),
    payload_artifact_id uuid NOT NULL REFERENCES app.artifact(id),
    payload jsonb NOT NULL,
    digest bytea NOT NULL UNIQUE CHECK (octet_length(digest) = 32),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    recorded_by_kind app.actor_kind NOT NULL,
    recorded_by_id text NOT NULL,
    CHECK (sample_ref->>'kind' = 'sample' AND coalesce(sample_ref->>'id','') <> ''),
    CHECK (protocol_ref->>'kind' = 'protocol' AND coalesce(protocol_ref->>'id','') <> ''),
    CHECK (
        (qualifier = 'equal' AND value_num IS NOT NULL AND lower_num IS NULL AND upper_num IS NULL
            AND missing_reason IS NULL)
        OR (qualifier IN ('less_than','less_or_equal') AND value_num IS NULL
            AND lower_num IS NULL AND upper_num IS NOT NULL AND missing_reason IS NULL)
        OR (qualifier IN ('greater_than','greater_or_equal') AND value_num IS NULL
            AND lower_num IS NOT NULL AND upper_num IS NULL AND missing_reason IS NULL)
        OR (qualifier = 'interval' AND value_num IS NULL AND lower_num IS NOT NULL
            AND upper_num IS NOT NULL AND lower_num <= upper_num AND missing_reason IS NULL)
        OR (qualifier IN ('not_tested','missing') AND value_num IS NULL
            AND lower_num IS NULL AND upper_num IS NULL AND missing_reason IS NOT NULL)
    )
);

CREATE INDEX measurement_v2_endpoint_time_idx
    ON bio.measurement_v2 (endpoint_definition_id, measured_at DESC);
CREATE INDEX measurement_v2_compound_idx
    ON bio.measurement_v2 (compound_id, endpoint_definition_id, measured_at DESC)
    WHERE compound_id IS NOT NULL;
CREATE INDEX measurement_v2_attention_idx
    ON bio.measurement_v2 (qc_status, measured_at DESC)
    WHERE qc_status IN ('warn','fail') OR qualifier IN ('not_tested','missing');

COMMENT ON TABLE bio.measurement_v2 IS
    'Protocol-resolved immutable measurement ledger. Missing, censored and failed-QC '
    'observations remain explicit and are never coerced into numeric bio.result rows.';
COMMENT ON COLUMN bio.measurement_v2.payload_artifact_id IS
    'Canonical immutable JSON record registered atomically with this ledger row.';

CREATE OR REPLACE VIEW bio.v_measurement_v2_attention AS
SELECT id, measurement_key, endpoint_definition_id, compound_id, qualifier,
       qc_status, qc_reason_codes, missing_reason, measured_at
  FROM bio.measurement_v2
 WHERE qc_status IN ('warn','fail') OR qualifier IN ('not_tested','missing');

INSERT INTO meta.migration (filename, sha256, content_sha256, hash_source)
VALUES ('023_motif_governance_ingest.sql', '\x9241e27d4fa4c6c14d9554da22fdeeda54018cd826df89a3e385e6ad37d311ba'::bytea,
        '\x9241e27d4fa4c6c14d9554da22fdeeda54018cd826df89a3e385e6ad37d311ba'::bytea, 'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
