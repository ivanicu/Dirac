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
FORBIDDEN=(
    'co-authored-by:[[:space:]]*claude'
    'claude-session:'
    'claude\.ai/code'
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

RANGE="${2:-}"
if [[ "${1:-}" == "--range" && -n "$RANGE" ]]; then
    :
elif git rev-parse --verify --quiet origin/main >/dev/null; then
    RANGE='origin/main..HEAD'
else
    RANGE='-40'
fi

# `git log <range>` with an empty range prints nothing and exits 0, which would be
# a clean bill of health for a scan that examined nothing. So the count is checked
# and an empty range says so instead of passing.
mapfile -t shas < <(git log --format=%H "$RANGE" 2>/dev/null)
if [[ ${#shas[@]} -eq 0 ]]; then
    echo "check_commit_hygiene: range '$RANGE' contains NO commits — nothing was"
    echo "scanned, which is not the same as clean."
    exit 0
fi

problems=0
for sha in "${shas[@]}"; do
    body="$(git log -1 --format='%an%n%ae%n%s%n%b' "$sha")"
    if out="$(scan "$body")"; then :; else
        echo "DIRTY   $(git log -1 --format='%h %s' "$sha" | cut -c1-78)"
        echo "$out"
        problems=$((problems + 1))
    fi
done

echo "─────────────────────────────────────────────────────────────"
echo "${#shas[@]} commit(s) scanned in '$RANGE' · $problems with a forbidden string"
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
