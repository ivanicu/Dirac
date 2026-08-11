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
      // POST DIRECTLY, with the app's own molfile and a budget no interactive
      // click should ever have. Clicking the buttons warms the same rows but
      // inherits the panel's 60 s client budget — which is why lapatinib came
      // back "SCF exceeded its 60 s budget after 10 cycles" and cached nothing.
      // A warm is not an interaction: nobody is waiting, so the only correct
      // budget is a generous one.
      var hook = window.diracFields;
      if (!hook || !hook.molfile) {
        log.push('NO MOLFILE HOOK');
      } else {
        for (var f = 0; f < FIELDS.length; f++) {
          var kind = FIELDS[f];
          try {
            var r = await fetch(hook.backend + '/field', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ molfile: hook.molfile, kind: kind,
                                     basis: 'sto-3g', store: true,
                                     max_seconds: 900 }),
            });
            var j = await r.json();
            log.push(kind + (j.ok ? ':OK' : ':REFUSED(' + String(j.error).slice(0, 50) + ')'));
          } catch (e) { log.push(kind + ':ERR(' + e + ')'); }
        }
        // and render one so the screenshot shows a real field
        var b0 = document.querySelector('.field-btn[data-field="mep"]');
        if (b0 && !b0.disabled) { b0.click(); await wait(6000); }
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
  timeout 900 google-chrome --headless=new --user-data-dir="$CHROME_PROFILE" --enable-unsafe-swiftshader \
      --use-angle=swiftshader --window-size=1600,1000 \
      --virtual-time-budget=800000 \
      --screenshot="$OUT/warm_$MOL.png" \
      "http://127.0.0.1:$PORT/index.html" >/dev/null 2>&1
  cleanup_chrome
  echo "  -> $OUT/warm_$MOL.png"
done

kill "$(cat "$OUT/.srv")" 2>/dev/null
rm -f "$OUT/.srv" "$STAGE/index.html.driver"
echo
echo "DB rows now cached (the ones that survive a restart):"
psql -U ivan -d dirac -tAc \
  "select kind, basis, count(*) from app.field_cube group by 1,2 order by 1,2" 2>/dev/null \
  || echo "  (psql unavailable — check the daemon log for '[db] cached')"
