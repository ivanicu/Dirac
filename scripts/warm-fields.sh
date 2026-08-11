#!/usr/bin/env bash
# Precompute and CACHE every field for every bundled molecule, then screenshot.
#
#   scripts/warm-fields.sh [outdir]
#
# Ivan needs this for an interview: nothing may be slow or fail live.
#
# THE TRAP THIS FILE EXISTS TO AVOID. The durable cache is keyed on
# sha256(molfile), and the molfile the app sends is reconstructed by mol* from
# scene loci — not one I can generate from a SMILES. Warming the cache with my
# own molfiles produces perfectly good rows that the app will never hit,
# because the hashes differ. So the warmer drives the REAL APP: every cache key
# it writes is a key the app will ask for, by construction.
#
# One browser session per MOLECULE (not per field): booting mol* costs ~20 s and
# clicking six fields afterwards costs nothing, so per-field boots would have
# multiplied the boot cost by six for no benefit.
#
# Both caches are warmed by one pass. The browser cache dies with the tab; the
# DB cache is the one that matters tomorrow, and `[db] cached ...` in the
# daemon log is the evidence it landed.
set -u

ROOT=/home/ivan/dirac
OUT=${1:-/tmp/claude-1000/-home-ivan/ff9c7c07-88fe-4345-a0cc-59d17b42683e/scratchpad/warm}
STAGE=$OUT/_app
PORT=1347
# Every bundled fixture. Most carry no small-molecule ligand and will report so
# — that is a RESULT, not a failure, and the warmer records which is which so
# nobody discovers it live in front of an interviewer.
MOLECULES=${MOLECULES:-"1CBS 1XKK 4HHB 1EMA 1CRN 1GRM 1BNA 1TUP 2POR 7QPD"}
# QM first. If the run is cut short, the expensive things are already cached and
# the sub-second classical ones can be recomputed live without anyone noticing.
FIELDS=${FIELDS:-"mep_qm homo lumo density mep mlp"}

mkdir -p "$OUT"
rm -rf "$STAGE"; cp -r "$ROOT/build/dirac" "$STAGE"
( cd "$STAGE" && python3 -m http.server $PORT --bind 127.0.0.1 >/dev/null 2>&1 & echo $! > "$OUT/.srv" )
sleep 1

for MOL in $MOLECULES; do
cat > "$STAGE/index.html.driver" <<EOF
<script>
(function () {
  var MOL = '$MOL', FIELDS = '$FIELDS'.split(' ');
  var txt = function (s) { var e = document.querySelector(s); return e ? e.textContent.trim() : ''; };
  function wait(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  var log = [];
  (async function () {
    await wait(9000);
    var sel = document.getElementById('molecule');
    for (var i = 0; i < sel.options.length; i++) {
      if (sel.options[i].textContent.indexOf(MOL) >= 0) {
        sel.selectedIndex = i;
        sel.dispatchEvent(new Event('change', { bubbles: true }));
        break;
      }
    }
    await wait(13000);
    document.querySelector('[data-jump="fields"]').click();
    await wait(1200);

    var summary = txt('#fields-summary');
    if (/no ligand/i.test(summary)) {
      log.push('NO LIGAND — nothing to precompute');
    } else {
      for (var f = 0; f < FIELDS.length; f++) {
        var btn = document.querySelector('.field-btn[data-field="' + FIELDS[f] + '"]');
        if (!btn) { log.push(FIELDS[f] + ':no-button'); continue; }
        for (var w = 0; w < 30 && btn.disabled; w++) await wait(1000);
        if (btn.disabled) { log.push(FIELDS[f] + ':still-disabled'); continue; }
        btn.click();
        var done = false;
        for (var k = 0; k < 240; k++) {
          await wait(1000);
          var st = txt('#field-status');
          if (/rendered/i.test(st)) { log.push(FIELDS[f] + ':OK'); done = true; break; }
          if (/refus|unreach|cannot|exceed|budget|not quotable/i.test(st)) {
            log.push(FIELDS[f] + ':REFUSED(' + st.slice(0, 60) + ')'); done = true; break;
          }
        }
        if (!done) log.push(FIELDS[f] + ':TIMEOUT');
      }
      // The physics panel too — same molecule, same session, both daemons warm.
      var pt = document.querySelector('[data-jump="physics"]');
      if (pt) {
        pt.click(); await wait(900);
        var tors = document.getElementById('phys-run-torsion');
        if (tors && !tors.disabled) {
          tors.click();
          for (var a = 0; a < 90; a++) { await wait(1000);
            if (/rotors scanned|unreach|refus|cannot/i.test(txt('#phys-torsion-status'))) break; }
          log.push('torsion:' + txt('#phys-torsion-status').slice(0, 40));
        }
        var surf = document.getElementById('phys-run-surface');
        if (surf && !surf.disabled) {
          surf.click();
          for (var b = 0; b < 300; b++) { await wait(1000);
            if (/electrostatics for|unreach|refus|cannot|exceed/i.test(txt('#phys-surface-status'))) break; }
          log.push('sigma:' + txt('#phys-surface-status').slice(0, 40));
        }
        document.querySelector('[data-jump="fields"]').click();
        await wait(800);
      }
    }
    await wait(1500);
    var bar = document.createElement('div');
    bar.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:99999;'
      + 'font:10px ui-monospace,monospace;padding:5px 9px;background:#111;color:#0f0';
    bar.textContent = MOL + ' | ' + summary + ' | ' + log.join('  ');
    document.body.appendChild(bar);
    document.title = 'WARM_DONE';
    window.__warm = log;
  })();
})();
</script>
EOF
  cat "$ROOT/build/dirac/index.html" "$STAGE/index.html.driver" > "$STAGE/index.html"
  echo "warming $MOL ..."
  timeout 900 google-chrome --headless=new --enable-unsafe-swiftshader \
      --use-angle=swiftshader --window-size=1600,1000 \
      --virtual-time-budget=800000 \
      --screenshot="$OUT/warm_$MOL.png" \
      "http://127.0.0.1:$PORT/index.html" >/dev/null 2>&1
  echo "  -> $OUT/warm_$MOL.png"
done

kill "$(cat "$OUT/.srv")" 2>/dev/null
rm -f "$OUT/.srv" "$STAGE/index.html.driver"
echo
echo "DB rows now cached (the ones that survive a restart):"
psql -U ivan -d dirac -tAc \
  "select kind, basis, count(*) from app.field_cube group by 1,2 order by 1,2" 2>/dev/null \
  || echo "  (psql unavailable — check the daemon log for '[db] cached')"
