-- 032 · Reject non-finite physical sample quantities at the database boundary.
-- PostgreSQL numeric considers NaN greater than finite values, so amount >= 0
-- alone is not a finite-number guarantee.

BEGIN;

ALTER TABLE chem.sample
    ADD CONSTRAINT sample_amount_finite CHECK (
        amount_value NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric)
    );

INSERT INTO meta.migration (filename,sha256,content_sha256,hash_source)
VALUES ('032_reference_numeric_hardening.sql','\x1721984dcbdadf2e66c7c3051ff45c8283b0bdd01d848a84829dc693fc33b248'::bytea,
        '\x1721984dcbdadf2e66c7c3051ff45c8283b0bdd01d848a84829dc693fc33b248'::bytea,'content')
ON CONFLICT (filename) DO NOTHING;

COMMIT;
