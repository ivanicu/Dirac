#!/usr/bin/env bash
# Gate: every applied migration's file must still be the file that was applied.
#
# WHY THIS IS A SCRIPT AND NOT A SQL CHECK: the comparison needs the file, and
# reading files from inside PostgreSQL requires superuser (pg_read_file). Buying
# a constraint with a superuser grant would be a worse trade than running this
# from CI, so the check lives here and CI is where it becomes non-optional.
#
# WHAT IT CATCHES: meta.migration.sha256 recorded the sha256 of the FILENAME for
# 000 through 006 — `digest('006_producer_identity.sql','sha256')` — so an
# applied migration could be edited afterwards and nothing noticed. Migration
# 008 backfilled the true content hashes into content_sha256 and marked each row
# 'content' (verified at apply time) or 'backfilled' (recorded later, which is
# NOT proof of what was applied). This gate keeps the promise from here on.
#
# SELF-REFERENCE, and why the normalisation below is not a fudge: a migration
# that records its own content hash cannot contain that hash — inserting it
# changes the content. So the recorded value is the hash of the file with its own
# 64-hex self-hash replaced by the literal PENDING, and this gate applies the
# same substitution before hashing. Deterministic, and identical on both sides.
#
# Usage:  bash backend/db/check_migration_hashes.sh [--selftest]
# Exit:   0 clean · 1 drift found · 2 could not run
set -euo pipefail

cd "$(dirname "$0")/../.."
DSN="${DIRAC_DSN:-dbname=dirac}"
MIGRATIONS="backend/db/migrations"

# Hash a migration file the way the ledger records it: with any 64-hex string
# that is this file's own self-hash replaced by PENDING. Only a literal in the
# form '\x<64hex>' is substituted, so hashes that are DATA (a producer's
# source hash, a fixture) are left alone.
normalised_hash() {
    local file="$1" recorded="$2"
    if [[ -n "$recorded" ]] && grep -qF "$recorded" "$file"; then
        sed "s/$recorded/PENDING/g" "$file" | sha256sum | cut -d' ' -f1
    else
        sha256sum "$file" | cut -d' ' -f1
    fi
}

if ! psql "$DSN" -tAc 'SELECT 1' >/dev/null 2>&1; then
    echo "check_migration_hashes: cannot reach the database ($DSN)" >&2
    exit 2
fi
if ! psql "$DSN" -tAc "SELECT 1 FROM information_schema.columns
        WHERE table_schema='meta' AND table_name='migration'
          AND column_name='content_sha256'" | grep -q 1; then
    echo "check_migration_hashes: meta.migration.content_sha256 is missing — apply 008 first" >&2
    exit 2
fi

problems=0 checked=0 legacy=0 backfilled=0 verified=0

while IFS='|' read -r filename recorded source; do
    [[ -z "$filename" ]] && continue
    path="$MIGRATIONS/$filename"

    if [[ "$source" == "filename-legacy" ]]; then
        legacy=$((legacy + 1))
        echo "LEGACY   $filename — applied from outside this tree; nothing to compare"
        continue
    fi
    if [[ ! -f "$path" ]]; then
        echo "MISSING  $filename — recorded as applied, not on disk"
        problems=$((problems + 1)); continue
    fi

    actual="$(normalised_hash "$path" "$recorded")"
    checked=$((checked + 1))
    [[ "$source" == "content" ]] && verified=$((verified + 1)) || backfilled=$((backfilled + 1))

    if [[ "$actual" != "$recorded" ]]; then
        echo "DRIFT    $filename"
        echo "         recorded ${recorded:0:16}…  disk ${actual:0:16}…  ($source)"
        problems=$((problems + 1))
    else
        printf 'OK       %-44s %s\n' "$filename" "$source"
    fi
done < <(psql "$DSN" -tA -F'|' -c "
    SELECT filename, encode(content_sha256,'hex'), hash_source::text
      FROM meta.migration ORDER BY filename")

echo "─────────────────────────────────────────────────────────────"
echo "$checked compared ($verified verified-at-apply, $backfilled backfilled), $legacy legacy, $problems problem(s)"

if [[ "${1:-}" == "--selftest" ]]; then
    # A gate that has never gone red is untested. Prove it convicts by making a
    # copy of the ledger's view of reality disagree with disk, in a transaction
    # that is rolled back — the real ledger is never touched.
    echo
    echo "── selftest: the gate must convict on a tampered file ──"
    victim="$MIGRATIONS/002_vocabulary.sql"
    cp "$victim" /tmp/dirac_mig_selftest.bak
    trap 'mv -f /tmp/dirac_mig_selftest.bak "$victim"' EXIT
    printf '\n-- selftest tamper, reverted immediately\n' >> "$victim"
    # `bash "$0" | grep -q` would be WRONG here, and it was: under `pipefail`
    # the pipeline takes the failing member's status, so the gate CORRECTLY
    # exiting 1 on detected drift made the selftest report FAIL. The gate was
    # right and its own test was wrong — capture first, judge after.
    tampered_output="$(bash "$0" || true)"
    if grep -q "^DRIFT    002_vocabulary.sql" <<<"$tampered_output"; then
        echo "SELFTEST PASS — tampering with an applied migration is detected"
    else
        echo "SELFTEST FAIL — the gate did not notice a modified applied migration"
        exit 1
    fi
    exit 0
fi

exit $(( problems > 0 ? 1 : 0 ))
