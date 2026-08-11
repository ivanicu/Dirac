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

mkdir -p "$OUT"
rm -rf "$STAGE"; cp -r "$ROOT/build/dirac" "$STAGE"

for FIELD in $FIELDS; do
cat > "$STAGE/index.html.driver" <<EOF
<script>
(function () {
  var FIELD = '$FIELD', MOLECULE = '$MOLECULE';
  var q = function (s) { return document.querySelector(s); };
  var txt = function (s) { var e = q(s); return e ? e.textContent.trim() : ''; };
  function wait(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  (async function () {
    await wait(9000);
    // pick a structure that has a ligand
    var sel = document.querySelector('#molecule-select, select');
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
    bar.textContent = FIELD + ' | ' + MOLECULE + ' | ' + txt('#fields-summary')
      + ' | ' + txt('#field-status');
    document.body.appendChild(bar);
    document.title = 'SHOT_READY';
  })();
})();
</script>
EOF
  cat "$ROOT/build/dirac/index.html" "$STAGE/index.html.driver" > "$STAGE/index.html"

  ( cd "$STAGE" && python3 -m http.server $PORT --bind 127.0.0.1 >/dev/null 2>&1 & echo $! > "$OUT/.srv" )
  sleep 1
  timeout 220 google-chrome --headless=new --enable-unsafe-swiftshader \
      --use-angle=swiftshader --window-size=1600,1000 \
      --virtual-time-budget=120000 \
      --screenshot="$OUT/field_$FIELD.png" \
      "http://127.0.0.1:$PORT/index.html" >/dev/null 2>&1
  kill "$(cat "$OUT/.srv")" 2>/dev/null
  sleep 1
  echo "shot $FIELD -> $OUT/field_$FIELD.png"
done
rm -f "$OUT/.srv" "$STAGE/index.html.driver"
