#!/usr/bin/env bash
# Gate: this repository is PUBLIC, and its history must not name the tooling.
#
# Ivan's standing rule, and it OVERRIDES the harness's default commit convention:
# no "Claude" and no "ZhenD" in public commit history, scrubbed BEFORE the first
# push and never after. The default convention appends
#     Co-Authored-By: Claude <model> <noreply@anthropic.com>
#     Claude-Session: https://claude.ai/code/session_...
# and a session URL is worse than the name: it is a live pointer from a public
# repository to a private transcript.
#
# MEASURED 2026-08-11, which is why this file exists: 17 of ~100 commits pushed
# that day carried both trailers. They are mine. The rule was in memory, I knew
# it, and I appended the trailer anyway for the first half of the day — because a
# rule I hold in my head competes with a default that fires automatically, and the
# default wins every time it is not mechanised.
#
# WHAT THIS DOES AND DOES NOT DO: it convicts commits it can still see, so it
# stops the NEXT one. It cannot clean history — that needs a rewrite and a force
# push, which destroys other clones and is a decision only Ivan makes.
#
# Usage:  bash scripts/check_commit_hygiene.sh [--range <git range>] [--selftest]
#         default range: origin/main..HEAD if it resolves, else the last 40 commits
# Exit:   0 clean · 1 a forbidden string found · 2 could not run
set -uo pipefail

cd "$(dirname "$0")/.."

# Case-insensitive, and anchored where it matters: 'Co-Authored-By: Claude' and a
# claude.ai session URL are the two the default emits. The bare word 'Claude' in a
# body is deliberately NOT forbidden — a commit that explains a decision may need
# to name the tool honestly, and a gate that forbids discussing the subject is a
# gate people route around. Attribution trailers and private URLs are the target.
# LINE-ANCHORED, and that is the whole correctness of this gate. A trailer is a
# LINE; a commit body that DISCUSSES trailers is not a leak. Unanchored, this
# counted a mention as a use — the rewrite commit that explains what was
# stripped quotes both strings, so on a provably clean history (GitHub's own
# API, line-anchored: 0) the gate reported 1 and failed against a baseline of 0.
# A gate that convicts the commit documenting the fix is a gate people route
# around, which is the failure this file's own header warns about.
#
# Trailers are never indented by convention, so ^ costs no real detection. The
# selftest below is what proves that: it still convicts genuine trailers.
#
# The URL pattern requires the SCHEME and the session path, because what leaks
# is a RESOLVABLE LINK — not the domain name appearing in a sentence. Anchoring
# alone was not enough: a body explaining the rule wrapped so that
# "claude.ai/code URL. It deliberately does NOT forbid..." began a line, and the
# gate convicted the commit that documents the policy. Twice now this file has
# counted a mention as a use, and both times the victim was the commit whose
# whole subject is the leak — which is the strongest possible hint that the
# property being matched was the wrong one.
FORBIDDEN=(
    '^co-authored-by:[[:space:]]*claude'
    '^claude-session:'
    'https?://claude\.ai/code/session'   # the RESOLVABLE link, not the domain in prose
    'zhend'
)

scan() {   # scan <text> ; prints the matching pattern names, returns 1 on a hit
    local text="$1" hit=0
    for pat in "${FORBIDDEN[@]}"; do
        if grep -qiE "$pat" <<<"$text"; then
            echo "        matches /$pat/"
            hit=1
        fi
    done
    return $hit
}

if [[ "${1:-}" == "--selftest" ]]; then
    # A gate that has never convicted has not been shown to have resolution, and
    # this one is expected to be green on new commits from here on — so the only
    # evidence it works is a crafted body it must reject.
    echo "── selftest: crafted bodies ──"
    ok=1
    for bad in 'Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>' \
               'Claude-Session: https://claude.ai/code/session_01ABC' \
               'co-authored-by:   claude opus 5'; do
        # `scan` returns 1 ON A HIT, so a conviction is the FAILING branch. The
        # first version of this selftest read it the other way round and reported
        # every case backwards — missed convictions AND false positives at the
        # same time, on a gate whose real scan was working. Had I trusted the
        # selftest I would have "fixed" a correct gate.
        if ! scan "$bad" >/dev/null; then
            echo "  OK    convicted: ${bad:0:52}"
        else
            echo "  FAIL  missed:    ${bad:0:52}"
            ok=0
        fi
    done
    for good in 'the harness appends a trailer; this gate is why it no longer does' \
                'measured with claude-free tooling' ; do
        if ! scan "$good" >/dev/null; then
            echo "  FAIL  false positive on a legitimate body: ${good:0:44}"
            ok=0
        else
            echo "  OK    allowed:   ${good:0:52}"
        fi
    done
    [[ $ok == 1 ]] && { echo 'SELFTEST PASS — convicts the trailers, allows prose'; exit 0; }
    echo 'SELFTEST FAIL'; exit 1
fi

# ── two modes, because one of them was vacuous ─────────────────────────────
# The first version defaulted to origin/main..HEAD. That is right for PREVENTION
# (scrub before the push) and it is EMPTY the moment a push succeeds — so in CI,
# where origin/main == HEAD, it scanned nothing and passed. A gate that cannot fail
# in the place it runs automatically is the defect this repo has spent a day
# hunting, and I shipped it in the gate written to enforce a rule about my own
# discipline.
#
# So: --pending scans the unpushed range and prevents the next leak. --audit
# (default, and what gates.sh runs) scans the recent history and RATCHETS against a
# recorded baseline: the existing leak is a fact, its size is written down, and the
# gate fails when it GROWS. That refuses to paper over history without holding the
# suite red on a decision only Ivan can make.
BASELINE_FILE='scripts/.commit_hygiene_baseline'
# FULL history, not a fixed depth. A depth window is wrong in a way that hides the
# thing the ratchet exists to catch: as clean commits accumulate, old dirty ones
# fall OUT of the window, the count drops on its own, and the gate reports "the
# leak shrank" for a history that never changed. The count must only move when
# history moves.
MODE='audit'
RANGE=''
case "${1:-}" in
    --pending) MODE='pending' ;;
    --range)   MODE='range'; RANGE="${2:-}" ;;
    --audit|'') MODE='audit' ;;
esac
if [[ "$MODE" == 'pending' ]]; then
    if git rev-parse --verify --quiet origin/main >/dev/null; then
        RANGE='origin/main..HEAD'
    else
        echo 'check_commit_hygiene: origin/main does not resolve, so "unpushed" is'
        echo 'undefined. Refusing to report clean.' >&2
        exit 2
    fi
elif [[ "$MODE" == 'audit' ]]; then
    RANGE='HEAD'
fi
if [[ -z "$RANGE" ]]; then
    echo 'check_commit_hygiene: no range' >&2; exit 2
fi

# `git log <range>` with an empty range prints nothing and exits 0, which would be
# a clean bill of health for a scan that examined nothing. So the count is checked
# and an empty range says so instead of passing.
# One pass. Records separated by RS (0x1e), fields by US (0x1f), because a commit
# body contains newlines and any line-based split would mis-attribute a trailer to
# the wrong commit.
records="$(git log --format='%H%x1f%h%x1f%an%x1f%ae%x1f%s%x1f%b%x1e' "$RANGE" 2>/dev/null)"
total=0
problems=0
while IFS= read -r -d $'\x1e' rec; do
    [[ -z "${rec//[[:space:]]/}" ]] && continue
    total=$((total + 1))
    # SCAN THE WHOLE RECORD, and do not try to split it into fields first. The
    # previous version did `IFS=$'\x1f' read -r sha short an ae subj body` and
    # reported 0 dirty over the entire history — because `read` stops at the first
    # NEWLINE, so `body` held only its first line and every trailer, which lives at
    # the END of a body, was invisible. Third instrument bug in this one gate: the
    # inverted selftest, the vacuous empty range, and now a parser that truncated
    # exactly the region the strings live in. The gate is 40 lines and its
    # measurement apparatus has been wrong three times; the code being small is not
    # the same as the measurement being right.
    if out="$(scan "$rec")"; then :; else
        short="$(awk -F'\x1f' '{print $2; exit}' <<<"$rec")"
        subj="$(awk -F'\x1f' '{print $5; exit}' <<<"$rec")"
        echo "DIRTY   $(printf '%s %s' "$short" "$subj" | cut -c1-78)"
        problems=$((problems + 1))
    fi
done <<<"$records"

if [[ $total -eq 0 ]]; then
    echo "check_commit_hygiene: range '$RANGE' contains NO commits — nothing was"
    echo "scanned, which is not the same as clean."
    exit 0
fi

echo "─────────────────────────────────────────────────────────────"
echo "$total commit(s) scanned in '$RANGE' · $problems with a forbidden string"

if [[ "$MODE" == 'audit' ]]; then
    baseline=0
    [[ -f "$BASELINE_FILE" ]] && baseline="$(grep -oE '^[0-9]+' "$BASELINE_FILE" | head -1)"
    echo "recorded baseline: $baseline (see $BASELINE_FILE)"
    if [[ $problems -gt $baseline ]]; then
        echo
        echo "THE LEAK GREW: $problems > $baseline. A new commit named the tooling."
        echo "Fix the commit before it is pushed (git commit --amend), or if it is"
        echo "already public, raise the baseline DELIBERATELY and say why."
        exit 1
    fi
    if [[ $problems -lt $baseline ]]; then
        echo "the leak SHRANK ($problems < $baseline) — history was cleaned; lower"
        echo "the baseline to $problems so the ratchet cannot slip back."
    fi
    exit 0
fi

if [[ $problems -gt 0 ]]; then
    cat <<'MSG'

This repository is PUBLIC. A commit that names the tooling, or links a private
session, cannot be fixed by editing the message after it is pushed — the fix is a
history rewrite and a force push, which destroys other clones. That is Ivan's
decision, not this gate's. What the gate can do is stop the next one.
MSG
    exit 1
fi
exit 0
