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
ALL=(tsc build palette css migrations)
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
wanted build   && run_gate 'gate-2-build'      node ./scripts/build.mjs -a dirac --prd
# 3. palette: Ivan's mid-saturation ruling, enforced as OKLCH chroma.
wanted palette && run_gate 'gate-3-palette'    python3 design/check_palette.py
# 4. css: brace balance in the lab's inline <style> (the a93c175 incident).
wanted css     && run_gate 'gate-4-css-braces' node scripts/check_css_braces.mjs "$LAB_HTML"
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
