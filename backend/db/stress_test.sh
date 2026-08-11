#!/usr/bin/env bash
# Brute-force load and recall test for the Dirac schema.
#
#   bash backend/db/stress_test.sh [N_COMPOUNDS] [N_QUERIES]
#
# Runs in a THROWAWAY database (dirac_stress), created from the migrations and
# dropped at the end, so it simultaneously answers a production question the
# migrations alone do not: can this database be rebuilt from scratch?
#
# What it measures, and why each number matters:
#
#   schema rebuild    the migrations must apply to an empty database, in order,
#                     with no manual step. If they cannot, the only copy of the
#                     schema is the running server.
#   insert throughput registration is a bulk operation during a library load.
#   index build       HNSW build time is the cost of adding a fingerprint kind.
#   RECALL@10         THE POINT OF THIS SCRIPT. HNSW is an APPROXIMATE index:
#                     it returns plausible neighbours and never reports the
#                     ones it missed. Similarity search that silently drops
#                     true neighbours is a scientific defect, not a latency
#                     issue, and it cannot be noticed by reading results.
#   latency           exact scan vs index, so the recall cost has a price tag.
#
# Fingerprints are generated in CLUSTERED FAMILIES (a scaffold plus mutated
# members), never uniformly at random: with uniform bits every pairwise
# Tanimoto collapses to the same value and a recall measurement over it is
# meaningless — a null result manufactured by the test's own data.
set -euo pipefail

N=${1:-50000}
Q=${2:-200}
DB=dirac_stress
PSQL="psql -U ivan -v ON_ERROR_STOP=1 -q"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "=== Dirac stress test: ${N} compounds, ${Q} recall queries ==="

dropdb -U ivan --if-exists "$DB"
createdb -U ivan "$DB"
trap 'dropdb -U ivan --if-exists "$DB"' EXIT

echo "--- schema rebuild from migrations"
t0=$(date +%s.%N)
for f in "$ROOT"/backend/db/migrations/*.sql; do
    $PSQL -d "$DB" -f "$f" > /dev/null
done
t1=$(date +%s.%N)
printf 'rebuilt %s tables in %.1f s\n' \
    "$($PSQL -d "$DB" -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema IN ('meta','chem','bio','design','app','audit') AND table_type='BASE TABLE'")" \
    "$(echo "$t1 - $t0" | bc)"

$PSQL -d "$DB" <<SQL > /dev/null
CREATE FUNCTION rnd_fp(nset int) RETURNS bit(2048) LANGUAGE plpgsql AS \$\$
DECLARE b bit(2048) := repeat('0', 2048)::bit(2048);
BEGIN
    FOR i IN 1..nset LOOP b := set_bit(b, floor(random()*2048)::int, 1); END LOOP;
    RETURN b;
END \$\$;

CREATE FUNCTION mutate_fp(src bit(2048), nflip int) RETURNS bit(2048) LANGUAGE plpgsql AS \$\$
DECLARE b bit(2048) := src; p int;
BEGIN
    FOR i IN 1..nflip LOOP
        p := floor(random()*2048)::int;
        b := set_bit(b, p, 1 - get_bit(b, p));
    END LOOP;
    RETURN b;
END \$\$;

-- Synthetic registrations. InChIKeys are format-valid and unique; this is a
-- load test of the storage and index, not of the standardizer.
-- Base-26 so distinct inputs give distinct keys. The first attempt mapped
-- every digit to one letter, which collided at n=26 and was caught by the
-- schema's own UNIQUE constraint rather than by the generator.
CREATE FUNCTION b26(n bigint, width int) RETURNS text LANGUAGE plpgsql IMMUTABLE AS \$\$
DECLARE s text := ''; x bigint := n;
BEGIN
    WHILE length(s) < width LOOP
        s := chr(65 + (x % 26)::int) || s;
        x := x / 26;
    END LOOP;
    RETURN s;
END \$\$;

CREATE FUNCTION synth_key(n int) RETURNS char(27) LANGUAGE sql IMMUTABLE AS \$\$
    SELECT (b26(n, 14) || '-' || b26(n::bigint * 7 + 13, 10) || '-N')::char(27);
\$\$;

INSERT INTO meta.toolkit (name, version) VALUES ('stress', '1');
INSERT INTO chem.standardizer (label, toolkit_id, rules)
SELECT 'stress', id, '["none"]'::jsonb FROM meta.toolkit WHERE name='stress';
SQL

echo "--- bulk registration"
t0=$(date +%s.%N)
$PSQL -d "$DB" <<SQL > /dev/null
INSERT INTO chem.compound (inchikey, inchi, smiles, formula, mw_monoisotopic, stereo, standardizer_id)
SELECT synth_key(g), 'InChI=1S/stress' || g, 'C', 'CH4', 100 + (g % 400),
       'no_stereocenters', (SELECT id FROM chem.standardizer WHERE label='stress')
  FROM generate_series(1, $N) g;
SQL
t1=$(date +%s.%N)
printf 'registered %d compounds in %.1f s (%.0f rows/s)\n' "$N" \
    "$(echo "$t1 - $t0" | bc)" "$(echo "$N / ($t1 - $t0)" | bc -l)"

echo "--- clustered fingerprints (families of 100 around a scaffold)"
t0=$(date +%s.%N)
$PSQL -d "$DB" <<SQL > /dev/null
CREATE TEMP TABLE scaffold AS
SELECT g AS fam, rnd_fp(60) AS bits FROM generate_series(1, GREATEST($N/100, 1)) g;

INSERT INTO chem.fingerprint (compound_id, kind, radius, nbits, bits, popcount, toolkit_id)
SELECT c.id, 'morgan2', 2, 2048, m.b, length(replace(m.b::text, '0', '')),
       (SELECT id FROM meta.toolkit WHERE name='stress')
  FROM (SELECT id, row_number() OVER (ORDER BY registry_id) AS rn FROM chem.compound) c
  JOIN LATERAL (
      SELECT mutate_fp(s.bits, 8) AS b
        FROM scaffold s WHERE s.fam = ((c.rn - 1) / 100) + 1
  ) m ON true;
SQL
t1=$(date +%s.%N)
printf 'fingerprinted in %.1f s\n' "$(echo "$t1 - $t0" | bc)"

echo "--- HNSW index build"
t0=$(date +%s.%N)
$PSQL -d "$DB" -c "REINDEX INDEX chem.fingerprint_morgan2_hnsw" > /dev/null
t1=$(date +%s.%N)
printf 'index built in %.1f s · index size %s · table size %s\n' \
    "$(echo "$t1 - $t0" | bc)" \
    "$($PSQL -d "$DB" -tAc "SELECT pg_size_pretty(pg_relation_size('chem.fingerprint_morgan2_hnsw'))")" \
    "$($PSQL -d "$DB" -tAc "SELECT pg_size_pretty(pg_relation_size('chem.fingerprint'))")"

echo "--- RECALL@10: approximate index vs exact scan"
$PSQL -d "$DB" <<SQL
CREATE TEMP TABLE q AS
SELECT bits FROM chem.fingerprint ORDER BY random() LIMIT $Q;

-- Two recalls, because they answer different questions:
--   id_recall       did the index return the same ROWS as the exact scan?
--   distance_recall did it return rows that are AS CLOSE as the exact ones?
-- When many candidates sit at an identical Jaccard distance, top-k membership
-- is arbitrary and id_recall measures tie-breaking rather than misses. Only a
-- gap in DISTANCE recall is a real retrieval defect. Reporting the first
-- number alone would have condemned the index for the test data's own ties.
CREATE OR REPLACE FUNCTION recall_at(ef int, k int)
RETURNS TABLE(recall numeric, dist_recall numeric, ties numeric, ann_ms numeric, exact_ms numeric)
LANGUAGE plpgsql AS \$\$
DECLARE
    qb bit(2048); hit int := 0; total int := 0; dist_hit int := 0; tie_total int := 0;
    t0 timestamptz; ann_total interval := '0'; exact_total interval := '0';
    ann_ids uuid[]; exact_ids uuid[]; ann_d numeric[]; exact_d numeric[]; i int;
BEGIN
    EXECUTE format('SET LOCAL hnsw.ef_search = %s', ef);
    FOR qb IN SELECT bits FROM q LOOP
        SET LOCAL enable_seqscan = on;
        SET LOCAL enable_indexscan = off;
        t0 := clock_timestamp();
        SELECT array_agg(id ORDER BY id), array_agg(d ORDER BY d) INTO exact_ids, exact_d FROM (
            SELECT compound_id AS id, (bits <%> qb)::numeric AS d FROM chem.fingerprint
             WHERE kind='morgan2' ORDER BY bits <%> qb LIMIT k) e;
        exact_total := exact_total + (clock_timestamp() - t0);

        SET LOCAL enable_seqscan = off;
        SET LOCAL enable_indexscan = on;
        t0 := clock_timestamp();
        SELECT array_agg(id ORDER BY id), array_agg(d ORDER BY d) INTO ann_ids, ann_d FROM (
            SELECT compound_id AS id, (bits <%> qb)::numeric AS d FROM chem.fingerprint
             WHERE kind='morgan2' ORDER BY bits <%> qb LIMIT k) a;
        ann_total := ann_total + (clock_timestamp() - t0);

        SELECT hit + count(*) INTO hit FROM unnest(ann_ids) x WHERE x = ANY(exact_ids);
        FOR i IN 1..k LOOP
            IF ann_d[i] IS NOT DISTINCT FROM exact_d[i] THEN dist_hit := dist_hit + 1; END IF;
        END LOOP;
        -- How many rows in the whole table tie with the exact k-th distance?
        -- If this is large, id-level recall cannot reach 1 no matter the index.
        SELECT tie_total + count(*) INTO tie_total FROM chem.fingerprint
         WHERE kind='morgan2' AND (bits <%> qb)::numeric = exact_d[k];
        total := total + k;
    END LOOP;
    RETURN QUERY SELECT round(hit::numeric / total, 4),
                        round(dist_hit::numeric / total, 4),
                        round(tie_total::numeric / $Q, 1),
                        round(extract(epoch FROM ann_total) * 1000 / $Q, 2),
                        round(extract(epoch FROM exact_total) * 1000 / $Q, 2);
END \$\$;

SELECT 'ef_search=' || ef AS setting, recall AS id_recall, dist_recall, ties AS avg_rows_tied_at_kth,
       ann_ms || ' ms' AS ann_latency, exact_ms || ' ms' AS exact_latency,
       round(exact_ms / NULLIF(ann_ms, 0), 1) || '×' AS speedup
  FROM (VALUES (40), (100), (400)) v(ef), LATERAL recall_at(v.ef, 10);
SQL

echo "=== done (dirac_stress dropped) ==="
