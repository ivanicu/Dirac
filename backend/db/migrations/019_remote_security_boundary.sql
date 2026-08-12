-- 019 · durable remote access quota and redacted HTTP audit evidence.
BEGIN;

CREATE TABLE app.remote_quota_usage (
    actor_kind app.actor_kind NOT NULL,
    actor_id text NOT NULL,
    usage_day date NOT NULL,
    request_count integer NOT NULL DEFAULT 0 CHECK (request_count >= 0),
    cost_units bigint NOT NULL DEFAULT 0 CHECK (cost_units >= 0),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (actor_kind, actor_id, usage_day)
);

CREATE TABLE audit.remote_request (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    received_at timestamptz NOT NULL DEFAULT now(),
    request_id text,
    actor_kind app.actor_kind,
    actor_id text,
    token_fingerprint text,
    http_method text NOT NULL CHECK (http_method IN ('GET','HEAD','POST','OPTIONS')),
    path text NOT NULL,
    required_scopes text[] NOT NULL DEFAULT '{}',
    status smallint NOT NULL CHECK (status BETWEEN 100 AND 599),
    error_code text,
    request_bytes integer NOT NULL DEFAULT 0 CHECK (request_bytes >= 0),
    response_bytes bigint NOT NULL DEFAULT 0 CHECK (response_bytes >= 0),
    cost_units integer NOT NULL DEFAULT 0 CHECK (cost_units >= 0),
    duration_ms integer NOT NULL CHECK (duration_ms >= 0),
    CHECK ((actor_kind IS NULL) = (actor_id IS NULL)),
    CHECK (token_fingerprint IS NULL OR token_fingerprint ~ '^[0-9a-f]{16}$')
);

CREATE INDEX remote_request_actor_at
    ON audit.remote_request (actor_kind, actor_id, received_at DESC);
CREATE INDEX remote_request_status_at
    ON audit.remote_request (status, received_at DESC);

COMMENT ON TABLE audit.remote_request IS
    'Redacted remote-boundary evidence. Never stores Authorization, raw tokens, cookies, request bodies or query values.';
COMMENT ON TABLE app.remote_quota_usage IS
    'UTC-day request and compute-cost reservation; updated before expensive work starts.';

INSERT INTO meta.migration (filename, sha256, content_sha256, hash_source)
VALUES ('019_remote_security_boundary.sql', '\x2baad406a86308e506940c01595a7f2a749f6ca50ced57a864ed7317eb4e7672'::bytea,
        '\x2baad406a86308e506940c01595a7f2a749f6ca50ced57a864ed7317eb4e7672'::bytea, 'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
