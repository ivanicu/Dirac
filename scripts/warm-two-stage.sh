#!/usr/bin/env bash
# Two-stage warm: the browser supplies IDENTITY, the shell does the WAITING.
#
#   scripts/warm-two-stage.sh
#
# Three attempts got here and each failed for its own reason, which is why the
# split is what it is:
#
#   1. Warm from SMILES in Python. Fast, patient — and writes rows the app can
#      never hit, because the durable cache is keyed on sha256(molfile) and the
#      app's molfile is reconstructed by mol* from scene loci, not from a SMILES.
#   2. Warm by clicking the buttons. Exact keys — and inherits the panel's 60 s
#      client budget, so lapatinib returned "SCF exceeded its 60 s budget after
#      10 cycles" and cached nothing.
#   3. Warm by fetch() from the page with max_seconds=900. Exact keys, real
#      budget — and asks a browser to hold a socket for a quarter of an hour.
#      Every field came back "TypeError: Failed to fetch".
#
# The common error in 2 and 3 is the same one the physics job queue exists to
# fix: a long computation does not belong on the far end of a synchronous
# request that something impatient is holding open. So the browser is used for
# the ONE thing only it can do — produce the exact molfile the app will ask
# about — and then it leaves. Python computes, with no clock over it.
set -u

# Chrome must die with its run. `timeout` kills the launcher; the zygote and
# the GPU process survive it, and a headless GPU process idles at 100-300% CPU
# forever. Measured 2026-08-11: FOURTEEN orphans accumulated over one session,
# aged 1.5 to 20 hours, holding the box at load 163 — which then corrupted the
# very timings the screenshots were taken to check. The harness was poisoning
# its own measurement.
#
# Each run gets its own profile dir so cleanup targets exactly this run's
# processes and can never touch a peer's long-lived browser.
CHROME_PROFILE=$(mktemp -d /tmp/dirac-shot-XXXXXX)
cleanup_chrome() {
    pkill -f "user-data-dir=$CHROME_PROFILE" 2>/dev/null
    sleep 1
    pkill -9 -f "user-data-dir=$CHROME_PROFILE" 2>/dev/null
}
trap cleanup_chrome EXIT INT TERM

ROOT=/home/ivan/dirac
OUT=${1:-/tmp/claude-1000/-home-ivan/ff9c7c07-88fe-4345-a0cc-59d17b42683e/scratchpad/warm}
STAGE=$OUT/_dom
PORT=1348
MOLECULES=${MOLECULES:-"1CBS 1XKK 4HHB"}

mkdir -p "$OUT/molfiles"
rm -rf "$STAGE"; cp -r "$ROOT/build/dirac" "$STAGE"
( cd "$STAGE" && python3 -m http.server $PORT --bind 127.0.0.1 >/dev/null 2>&1 & echo $! > "$OUT/.srv2" )
sleep 1

# ── stage 1: identity ──────────────────────────────────────────────────────
for MOL in $MOLECULES; do
cat > "$STAGE/index.html.driver" <<EOF
<script>
(function () {
  var MOL = '$MOL';
  function wait(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  (async function () {
    await wait(9000);
    var sel = document.getElementById('molecule');
    for (var i = 0; i < sel.options.length; i++) {
      if (sel.options[i].textContent.indexOf(MOL) >= 0) {
        sel.selectedIndex = i;
        sel.dispatchEvent(new Event('change', { bubbles: true })); break;
      }
    }
    await wait(14000);
    document.querySelector('[data-jump="fields"]').click();
    await wait(1500);
    var hook = window.diracFields || {};
    // Into the DOM, where --dump-dom can reach it. No network, no waiting.
    var pre = document.createElement('pre');
    pre.id = 'warm-molfile';
    pre.textContent = hook.molfile || '';
    document.body.appendChild(pre);
    var lab = document.createElement('pre');
    lab.id = 'warm-label';
    lab.textContent = hook.label || '';
    document.body.appendChild(lab);
  })();
})();
</script>
EOF
  cat "$ROOT/build/dirac/index.html" "$STAGE/index.html.driver" > "$STAGE/index.html"
  echo "extracting $MOL ..."
  timeout 180 google-chrome --headless=new --user-data-dir="$CHROME_PROFILE" --enable-unsafe-swiftshader \
      --use-angle=swiftshader --virtual-time-budget=60000 --dump-dom \
      "http://127.0.0.1:$PORT/index.html" > "$OUT/molfiles/$MOL.dom" 2>/dev/null
  python3 - "$OUT/molfiles/$MOL.dom" "$OUT/molfiles/$MOL.mol" <<'PY'
import sys, re, html
dom = open(sys.argv[1], encoding='utf-8', errors='replace').read()
m = re.search(r'<pre id="warm-molfile">(.*?)</pre>', dom, re.S)
mol = html.unescape(m.group(1)) if m else ''
open(sys.argv[2], 'w').write(mol)
lines = mol.count('\n')
print(f'  -> {lines} lines' if lines > 4 else '  -> NO LIGAND (nothing to warm)')
PY
done
kill "$(cat "$OUT/.srv2")" 2>/dev/null; rm -f "$OUT/.srv2" "$STAGE/index.html.driver"

# ── stage 2: computation, with no clock over it ────────────────────────────
echo
"$ROOT/backend/env/bin/python" - "$OUT/molfiles" $MOLECULES <<'PY'
import json, os, sys, time, urllib.request

d, mols = sys.argv[1], sys.argv[2:]
B = 'http://127.0.0.1:8901'
KINDS = ['mep', 'mlp', 'mep_qm', 'homo', 'lumo', 'density']

def post(path, payload, timeout):
    req = urllib.request.Request(B + path, data=json.dumps(payload).encode(),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

print(f'{"molecule":9s} {"field":7s} {"outcome":9s} detail')
for mol in mols:
    p = os.path.join(d, f'{mol}.mol')
    text = open(p).read() if os.path.exists(p) else ''
    if len(text.splitlines()) < 5:
        print(f'{mol:9s} {"-":7s} {"NOLIGAND":9s} nothing to precompute')
        continue
    for kind in KINDS:
        t0 = time.time()
        try:
            # 1800 s and a socket to match. Nobody is waiting on this.
            out = post('/field', {'molfile': text, 'kind': kind,
                                  'basis': 'sto-3g', 'store': True,
                                  'max_seconds': 1800}, timeout=2100)
        except Exception as e:                                  # noqa: BLE001
            print(f'{mol:9s} {kind:7s} {"HARNESS":9s} {type(e).__name__}: {e}')
            continue
        el = time.time() - t0
        if out.get('ok'):
            m = out.get('meta', {})
            print(f'{mol:9s} {kind:7s} {"CACHED":9s} {el:6.1f}s '
                  f'cache={m.get("cache", "db")} nao={m.get("nbasis", "-")}')
        else:
            print(f'{mol:9s} {kind:7s} {"REFUSED":9s} {str(out.get("error"))[:80]}')
PY
echo
echo "DB rows that survive a restart:"
psql -U ivan -d dirac -tAc "select kind, basis, count(*) from app.field_cube group by 1,2 order by 1,2" 2>/dev/null
