// GENERATED — do not edit by hand.
// Source: contracts/errors.json (version 1).
// Regenerate: node scripts/gen_error_codes.mjs
//
// One home, two languages: backend/envelope.py reads the same JSON file for
// the Python side. A code that exists in one and not the other is exactly
// the drift contracts/errors.json's own $comment describes — see there for
// the three-vocabularies incident this file exists to prevent.

/** Per-code caller-facing copy. `points_at` is the working alternative when
 *  one exists (e.g. UNPARAMETERIZED -> the QM field that doesn't need params). */
export const ERROR_CODES = {
    PARSE: { user_copy: "This molecule could not be parsed.", retryable: false, points_at: null },
    UNCONVERGED: { user_copy: "The wavefunction did not converge — refusing to ship a decorative field.", retryable: true, points_at: null },
    UNPARAMETERIZED: { user_copy: "This method cannot parameterize {elements}.", retryable: false, points_at: "fields.qm.mep_qm" },
    BUDGET: { user_copy: "This would take longer than the budget allows.", retryable: true, points_at: null },
    OPEN_SHELL_SPIN_REQUIRED: { user_copy: "This metal centre needs an explicit spin multiplicity.", retryable: true, points_at: null },
    UNSUPPORTED: { user_copy: "Not supported for this molecule or these settings.", retryable: false, points_at: null },
    TOO_LARGE: { user_copy: "Too large for this format or this endpoint.", retryable: false, points_at: null },
    BAD_HOST: { user_copy: "Refused: unrecognised host.", retryable: false, points_at: null },
    CANCELLED: { user_copy: "Cancelled.", retryable: true, points_at: null },
    INTERNAL: { user_copy: "Something failed on our side.", retryable: true, points_at: null },
    NOT_FOUND: { user_copy: "Not found.", retryable: false, points_at: null },
    DB_UNAVAILABLE: { user_copy: "The database is unreachable — showing no data is not the same as there being none.", retryable: true, points_at: null },
} as const;

/** The full, and only, error vocabulary — derived from the object above so
 *  it can never list a code ERROR_CODES does not also carry. */
export type ErrorCode = keyof typeof ERROR_CODES;
