BEGIN;

ALTER TABLE design.program_event DROP CONSTRAINT program_event_event_kind_check;
ALTER TABLE design.program_event ADD CONSTRAINT program_event_event_kind_check CHECK (event_kind IN (
    'program.created','program.updated','objective.recorded','hypothesis.recorded',
    'decision.recorded','milestone.recorded','object.linked','snapshot.created',
    'portfolio.assigned','member.assigned','stage_gate.recorded','work_package.recorded',
    'work_item.transitioned','work_execution.linked','evidence.attached','lineage.recorded',
    'target_disease.linked','substance_registration.recorded','batch.registered',
    'sample.created','sample.transferred','work_comment.recorded','work_attachment.recorded',
    'gate_criterion.assessed','protocol.recorded','dataset_version.committed','experiment.recorded',
    'structure_observation.recorded','annotation.recorded','review.recorded',
    'analysis_snapshot.created','external_evidence_release.imported','external_evidence.recorded'
));

INSERT INTO meta.migration (filename,sha256,content_sha256,hash_source)
VALUES ('036_program_batch_registration.sql',
        '\x51d1d3ebeca316012b092cfa37f401482b92d8cc392ca74b0e412181f0582779'::bytea,
        '\x51d1d3ebeca316012b092cfa37f401482b92d8cc392ca74b0e412181f0582779'::bytea,
        'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
