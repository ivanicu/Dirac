#!/usr/bin/env bash
#
# S1 gate suite — the same four gates CI runs, runnable locally in one command.
#
#   bash scripts/gates.sh            # all four
#   bash scripts/gates.sh css tsc    # only the named ones
#
# Every gate runs even if an earlier one fails: one red gate must not hide the
# other three, and a suite that stops at the first failure trains people to fix
# one thing per run. Exit status is non-zero if ANY gate failed.
#
# Deliberately NOT here: a bundle-size budget (build/dirac/dirac.js is ~3 MB
# because mol* IS the bundle — a gate red at birth teaches people to bypass
# gates), jest/xvfb (needs a GL stack), and any wall-clock/latency assertion
# (meaningless on a shared runner).

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 2

# Overridable only so the gate can be pointed at a deliberately-broken copy to
# prove it still goes red. CI never sets it.
LAB_HTML="${LAB_HTML:-src/app.frontend.facets.molstar-rdkit.editable/index.html}"

if [ -t 1 ]; then
    GREEN=$'\033[32m'; RED=$'\033[31m'; DIM=$'\033[2m'; OFF=$'\033[0m'
else
    GREEN=''; RED=''; DIM=''; OFF=''
fi

FAILED=()
PASSED=()

run_gate() {
    local name="$1"; shift
    printf '%s\n' "${DIM}──────── ${name}: $* ────────${OFF}"
    if "$@"; then
        PASSED+=("$name")
        printf '%s\n\n' "${GREEN}PASS${OFF} ${name}"
    else
        local code=$?
        FAILED+=("$name")
        printf '%s\n\n' "${RED}FAIL${OFF} ${name} (exit ${code})"
    fi
}

# ---- gate selection -------------------------------------------------------
ALL=(tsc build palette css migrations docs contracts physics commits portability layering golden parity)
if [ "$#" -eq 0 ]; then
    WANT=("${ALL[@]}")
else
    WANT=("$@")
    # An unrecognised name must abort, never silently select nothing: a typo that
    # runs zero gates and exits 0 is a suite that cannot fail.
    for g in "${WANT[@]}"; do
        case " ${ALL[*]} " in
            *" $g "*) ;;
            *) printf '%s\n' "${RED}FAIL${OFF} unknown gate '$g' — pick from: ${ALL[*]}"; exit 2 ;;
        esac
    done
fi
wanted() {
    local g
    for g in "${WANT[@]}"; do [ "$g" = "$1" ] && return 0; done
    return 1
}

# ---- preflight ------------------------------------------------------------
if wanted tsc || wanted build; then
    if [ ! -d node_modules ]; then
        printf '%s\n' "${RED}FAIL${OFF} preflight: node_modules is missing — run 'npm ci' first"
        exit 2
    fi
fi
if wanted tsc && [ ! -x node_modules/.bin/tsc ]; then
    printf '%s\n' "${RED}FAIL${OFF} preflight: node_modules/.bin/tsc is missing — run 'npm ci' first"
    exit 2
fi

printf '%s\n' "${DIM}repo   ${ROOT}${OFF}"
printf '%s\n' "${DIM}node   $(node --version 2>/dev/null || echo 'MISSING')${OFF}"
printf '%s\n\n' "${DIM}python $(python3 --version 2>&1 || echo 'MISSING')${OFF}"

# ---- the gates ------------------------------------------------------------
# 1. types: the whole library compiles.
wanted tsc     && run_gate 'gate-1-typecheck'  node_modules/.bin/tsc --noEmit -p tsconfig.json
# 2. build: the dirac app actually bundles in production mode.
# BUILDS EVERYTHING, not just the app that ships. Measured 2026-08-11: `-a dirac` is
# 0.79 s and `-a -e` is 5.34 s, and that 4.5 s bought the discovery that `npm run build`
# had been failing for THIRTY HOURS — a vendored demo's directory was renamed and this
# list still pointed at the old path. A gate named `build` that cannot see a broken build
# is scoped narrower than its name, and the scope was invisible from the gate's own output.
wanted build   && run_gate 'gate-2-build'      node ./scripts/build.mjs -a -e --prd
# 3. palette: Ivan's mid-saturation ruling, enforced as OKLCH chroma.
wanted palette && run_gate 'gate-3-palette'    python3 design/check_palette.py
# 4. css: brace balance in the lab's inline <style> (the a93c175 incident).
wanted css     && run_gate 'gate-4-css-braces' node scripts/check_css_braces.mjs "$LAB_HTML"
# 6. docs: every host/port and command a doc claims must match the code.
wanted docs    && run_gate 'gate-6-docs-facts'  node scripts/check_docs_facts.mjs

# Gate 7 · contract drift. THREE-VALUED on purpose, because folding the third
# value into either of the other two is how a false pardon gets manufactured:
#   exit 0  everything agrees
#   exit 1  contracts/ disagree with the code -> FAIL, this gate's own scope
#   exit 2  contracts/ are clean but src/ has drifted -> reported, not blocking.
# The frontend interface is owned by another session and is legitimately behind
# a backend that gained keys this morning; failing the suite on it would train
# whoever runs this to pass --skip, and a suite people skip enforces nothing.
# The FIND lines are printed either way, so it cannot rot silently into "clean".
# Gate 8 · the physics daemon's four SCF protections, on EVERY route. Runs its
# own selftest first: a gate that has never convicted has not been shown to
# have resolution, and this one starts green against the real files, so its
# only evidence of working is the crafted broken source it must convict.
# Gate 9 · commit hygiene. This repo is PUBLIC and Ivan's standing rule forbids
# naming the tooling in its history; the harness's default convention appends
# exactly that, so the rule has to be mechanised or the default wins. Selftest
# first, same as 7 and 8.
# Gate 10 · test portability ratchet. Measures how much of the suite can be
# imported without the science stack — the invocation-kernel extraction's progress
# stated as a number rather than as a diagram. Fails when coupling GROWS.
# Gate 11 · the dependency laws. Four are ENFORCED, two are RATCHETS on the
# violations that exist today, and three report N/A because their subject (SDK,
# CLI, MCP) does not exist yet — deliberately not counted as passing, since a law
# that passes for lack of a subject reads exactly like one being obeyed.
# Gate 12 · the v1 compatibility surface. ADR-005 turns v1 into a codec over v2,
# and that is only safe if v1's observable behaviour is pinned FIRST — from the
# running service, not from a reading of the handler that is about to change.
# SKIPPED, loudly, without a daemon: a golden comparison that cannot reach the
# service must not report "unchanged".
if wanted golden; then
    if curl -s --max-time 3 http://127.0.0.1:8901/health >/dev/null 2>&1; then
        run_gate 'gate-12-v1-golden' backend/env/bin/python scripts/capture_v1_golden.py
    else
        printf '%s\n' "${YEL:-}SKIP${OFF} gate-12-v1-golden (no daemon on 8901 — UNVERIFIED, not clean)"
    fi
fi

# Gate 13 · THE ACCEPTANCE TEST, as a gate rather than as a paragraph in a plan.
# The external audit's first decisive criterion: the same fields.qm.homo invocation
# through every transport must yield the same method version, the same science, the
# same artifact SHA-256 and the same typed provenance. Two transports exist today
# (core and the v1 route) and the others are reported ABSENT, never as passing.
# It earns its place in the gate list because it has already found two real defects
# no other check could see: a version stamped on one path and not the other, and
# pyscf writing the wall clock into every cube so that no two transports could ever
# agree on a digest.
if wanted parity; then
    if curl -s --max-time 3 http://127.0.0.1:8901/health >/dev/null 2>&1; then
        run_gate 'gate-13-acceptance-parity' \
            backend/env/bin/python scripts/acceptance_parity.py
    else
        printf '%s\n' "${YEL:-}SKIP${OFF} gate-13-acceptance-parity (no daemon on 8901 \
— UNVERIFIED, not clean)"
    fi
fi

if wanted layering; then
    run_gate 'gate-11-layering' python3 scripts/check_layering.py
fi

if wanted portability; then
    run_gate 'gate-10-portability' python3 scripts/test_portability.py
fi

if wanted commits; then
    if bash scripts/check_commit_hygiene.sh --selftest >/dev/null 2>&1; then
        run_gate 'gate-9-commit-hygiene' bash scripts/check_commit_hygiene.sh
    else
        printf '%s\n' "${RED}FAIL${OFF} gate-9-commit-hygiene — its own selftest did not convict"
        FAILED+=('gate-9-commit-hygiene-selftest')
    fi
fi

if wanted physics; then
    if node scripts/check_physics_contract.mjs --selftest >/dev/null 2>&1; then
        run_gate 'gate-8-physics' node scripts/check_physics_contract.mjs
    else
        printf '%s\n' "${RED}FAIL${OFF} gate-8-physics — its own selftest did not convict the known-broken source"
        FAILED+=('gate-8-physics-selftest')
    fi
fi

if wanted contracts; then
    # ADR-002: the canonical schemas are the root source, so a schema edited without
    # regenerating must be a red build — otherwise "generated" means "generated once".
    # Runs BEFORE the drift proof because a stale generator makes every downstream
    # comparison a comparison against yesterday.
    if ! run_gate 'gate-7a-contract-codegen' python3 scripts/gen_contracts.py --check; then :; fi
    # Its own red proof first, same rule as gate 8: this gate is green against
    # the real contracts, so a crafted conviction is its only evidence of
    # resolution. It runs on a COPY, so it cannot leave a defect behind.
    if ! redproof_out="$(node scripts/check_contract_drift.mjs --redproof 2>&1)"; then
        printf '%s\n' "$redproof_out"
        printf '%s\n' "${RED}FAIL${OFF} gate-7-contracts — its own red proof did not convict"
        FAILED+=('gate-7-contracts-redproof')
    fi
    contract_out="$(node scripts/check_contract_drift.mjs 2>&1)"; contract_code=$?
    printf '%s\n' "$contract_out"
    case "$contract_code" in
        0) PASSED+=('gate-7-contracts') ;;
        # ⚠ EXIT 2 IS NOW A FAILURE. It was non-blocking while the frontend
        # interface was legitimately 21-25 keys behind a backend that gained keys
        # by the hour — failing then would have trained everyone to pass --skip,
        # and a suite people skip enforces nothing. As of 2026-08-11 the gap
        # reached ZERO, and the whole value of reaching zero is that the next key
        # to diverge is caught while it is one key instead of twenty-five. Twice
        # today an undeclared key was live in production; both times the drift was
        # found by this gate within the hour, and both fixes were two minutes.
        2) FAILED+=('gate-7-contracts (frontend FieldMeta has drifted again — see FIND above)') ;;
        *) FAILED+=('gate-7-contracts') ;;
    esac
fi
# 5. migrations: an applied migration's file must still BE the applied file.
#    Skipped rather than failed when PG is unreachable (exit 2), because a
#    developer without the database must still be able to run the other four —
#    a gate that cannot run and a gate that failed are different verdicts.
if wanted migrations; then
    if bash backend/db/check_migration_hashes.sh >/dev/null 2>&1; then
        run_gate 'gate-5-migrations' bash backend/db/check_migration_hashes.sh
    elif [ "$?" -eq 2 ]; then
        printf '%s\n' "${YEL:-}SKIP${OFF} gate-5-migrations (no database reachable)"
    else
        run_gate 'gate-5-migrations' bash backend/db/check_migration_hashes.sh
    fi
fi

# ---- verdict --------------------------------------------------------------
printf '%s\n' "════════ SUMMARY ════════"
for g in "${PASSED[@]:-}"; do [ -n "$g" ] && printf '  %sPASS%s  %s\n' "$GREEN" "$OFF" "$g"; done
for g in "${FAILED[@]:-}"; do [ -n "$g" ] && printf '  %sFAIL%s  %s\n' "$RED" "$OFF" "$g"; done

if [ "${#FAILED[@]}" -gt 0 ]; then
    printf '%s\n' "${RED}${#FAILED[@]} gate(s) failed${OFF}"
    exit 1
fi
if [ "${#PASSED[@]}" -eq 0 ]; then
    printf '%s\n' "${RED}FAIL no gate ran — refusing to report success${OFF}"
    exit 2
fi
printf '%s\n' "${GREEN}all ${#PASSED[@]} gates passed${OFF}"
