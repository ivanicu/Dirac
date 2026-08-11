#!/usr/bin/env bash
# Shoot every field, one screenshot each, against the built app.
#
#   scripts/shoot-fields.sh [outdir]
#
# Ivan: 每个都要截图看，截图调 — a field's colour and alpha are claims about
# what the picture looks like, and the only instrument that settles them is the
# picture. Reasoning about OKLCH gets the colour into the right neighbourhood;
# it cannot tell you the lobe reads as an empty outline once xray shading meets
# a white ground.
#
# Serves a COPY of build/dirac with a driver appended, so the shipped app never
# contains test scaffolding. Software WebGL (swiftshader) because this runs
# headless -- the geometry and the compositing are real, the frame rate is not.
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
OUT=${1:-/tmp/claude-1000/-home-ivan/ff9c7c07-88fe-4345-a0cc-59d17b42683e/scratchpad/shots}
STAGE=$OUT/_app
PORT=1344
# A structure with a DEPOSITED LIGAND. The default fixture is GFP, whose
# chromophore is part of the polymer chain, so the panel correctly reports "no
# ligand" and every field button stays disabled -- a screenshot of a disabled
# panel proves nothing about a field.
MOLECULE=${MOLECULE:-1CBS}
FIELDS=${FIELDS:-"mep mlp mep_qm homo lumo density"}
# SMILES= drives the "Import molecule · SMILES → 3D" path instead of picking a
# deposited structure. That route builds the ligand from the backend's own
# embedder rather than reconstructing a molblock from scene loci, so it is the
# only way to shoot fields while the loci→molblock builder is broken — and it
# is also the cleaner subject: a bare ligand, no cartoon in front of the field.
SMILES=${SMILES:-}

mkdir -p "$OUT"
rm -rf "$STAGE"; cp -r "$ROOT/build/dirac" "$STAGE"

for FIELD in $FIELDS; do
cat > "$STAGE/index.html.driver" <<EOF
<script>
(function () {
  var FIELD = '$FIELD', MOLECULE = '$MOLECULE', SMILES = '$SMILES';
  var q = function (s) { return document.querySelector(s); };
  var txt = function (s) { var e = q(s); return e ? e.textContent.trim() : ''; };
  function wait(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  (async function () {
    await wait(9000);
    if (SMILES) {
      var inp = document.getElementById('import-smiles');
      var run = document.getElementById('import-run');
      inp.value = SMILES;
      inp.dispatchEvent(new Event('input', { bubbles: true }));
      run.click();
      await wait(14000);
    }
    // pick a structure that has a ligand
    var sel = SMILES ? null : document.querySelector('#molecule-select, select');
    if (sel) {
      for (var i = 0; i < sel.options.length; i++) {
        if (sel.options[i].value.indexOf(MOLECULE) >= 0 ||
            sel.options[i].textContent.indexOf(MOLECULE) >= 0) {
          sel.selectedIndex = i;
          sel.dispatchEvent(new Event('change', { bubbles: true }));
          break;
        }
      }
    }
    await wait(11000);
    // PHYSICS mode: the two backend/physics quantities, which live on their
    // own tab and their own daemon.
    if (FIELD === 'physics') {
      var pt = document.querySelector('[data-jump="physics"]');
      if (pt) { pt.click(); await wait(800); }
      for (var w = 0; w < 25; w++) {
        var b = document.getElementById('phys-run-torsion');
        if (b && !b.disabled) break;
        await wait(1000);
      }
      var tors = document.getElementById('phys-run-torsion');
      if (tors && !tors.disabled) {
        tors.click();
        for (var k1 = 0; k1 < 40; k1++) { await wait(1000);
          if (/rotors scanned|unreach|refus|cannot/i.test(txt('#phys-torsion-status'))) break; }
      }
      var surf = document.getElementById('phys-run-surface');
      if (surf && !surf.disabled) {
        surf.click();
        for (var k2 = 0; k2 < 90; k2++) { await wait(1000);
          if (/electrostatics for|unreach|refus|cannot|exceed/i.test(txt('#phys-surface-status'))) break; }
      }
      await wait(2500);
      var bar0 = document.createElement('div');
      bar0.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:99999;'
        + 'font:11px ui-monospace,monospace;padding:6px 10px;background:#111;color:#fff';
      bar0.textContent = 'physics | ' + txt('#phys-summary') + ' | torsion: '
        + txt('#phys-torsion-status') + ' | surface: ' + txt('#phys-surface-status');
      document.body.appendChild(bar0);
      document.title = 'SHOT_READY';
      return;
    }
    var tab = document.querySelector('[data-jump="fields"], [data-tab="fields"]');
    if (tab) { tab.click(); await wait(600); }
    var btn = document.querySelector('.field-btn[data-field="' + FIELD + '"]');
    if (btn) {
      for (var t = 0; t < 25 && btn.disabled; t++) await wait(1000);
      if (!btn.disabled) {
        btn.click();
        for (var k = 0; k < 60; k++) {
          await wait(1000);
          if (/rendered|refus|unreach|cannot|exceed|budget/i.test(txt('#field-status'))) break;
        }
      }
    }
    await wait(2500);
    // Stamp the outcome INTO the page: a screenshot of a field that silently
    // failed is indistinguishable from one that worked, at a glance.
    var bar = document.createElement('div');
    bar.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:99999;'
      + 'font:11px ui-monospace,monospace;padding:6px 10px;background:#111;color:#fff';
    // Which buttons are actually live, on the record in every shot. Ivan
    // asked why only one field could be opened; that is a question about
    // button state, and reading it off a screenshot's pixels is guessing.
    var states = [];
    document.querySelectorAll('.field-btn[data-field]').forEach(function (b) {
      states.push(b.dataset.field + (b.disabled ? ':OFF' : ':on'));
    });
    bar.textContent = FIELD + ' | ' + MOLECULE + ' | ' + txt('#fields-summary')
      + ' | ' + txt('#field-status')
      + ' || ' + states.join(' ') + ' || ' + txt('#field-prefetch');
    document.body.appendChild(bar);
    document.title = 'SHOT_READY';
  })();
})();
</script>
EOF
  cat "$ROOT/build/dirac/index.html" "$STAGE/index.html.driver" > "$STAGE/index.html"

  ( cd "$STAGE" && python3 -m http.server $PORT --bind 127.0.0.1 >/dev/null 2>&1 & echo $! > "$OUT/.srv" )
  sleep 1
  timeout 220 google-chrome --headless=new --user-data-dir="$CHROME_PROFILE" --enable-unsafe-swiftshader \
      --use-angle=swiftshader --window-size=1600,1000 \
      --virtual-time-budget=120000 \
      --screenshot="$OUT/field_$FIELD.png" \
      "http://127.0.0.1:$PORT/index.html" >/dev/null 2>&1
  kill "$(cat "$OUT/.srv")" 2>/dev/null
  sleep 1
  cleanup_chrome
  echo "shot $FIELD -> $OUT/field_$FIELD.png"
done
rm -f "$OUT/.srv" "$STAGE/index.html.driver"
