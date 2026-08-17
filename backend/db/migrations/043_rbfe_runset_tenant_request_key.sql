-- 043 · A RunSet request key is idempotent only inside one authenticated actor.
--
-- The original global UNIQUE(request_key) let one principal reserve another
-- principal's idempotency key and made key existence observable across tenants.
-- Preserve reconnectable deduplication while making ownership part of the key.
BEGIN;

ALTER TABLE app.rbfe_run_set
    DROP CONSTRAINT rbfe_run_set_request_key_key;

ALTER TABLE app.rbfe_run_set
    ADD CONSTRAINT rbfe_run_set_actor_request_key_key
    UNIQUE (actor_kind, actor_id, request_key);

COMMENT ON CONSTRAINT rbfe_run_set_actor_request_key_key ON app.rbfe_run_set IS
    'A reconnectable RBFE RunSet request key is unique only for its authenticated owner.';

INSERT INTO meta.migration (filename,sha256,content_sha256,hash_source)
VALUES ('043_rbfe_runset_tenant_request_key.sql','\x0cc409072bbddb7bf62d40f62076ed075e7b9409832fa67898ba133ceaa2a53b'::bytea,
        '\x0cc409072bbddb7bf62d40f62076ed075e7b9409832fa67898ba133ceaa2a53b'::bytea,'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
