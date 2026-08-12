/**
 * Dirac — SAVE, the runtime half.
 *
 * THE PROBLEM, in the user's words: you tune the VFX in one panel, then switch the
 * molecule or the representation, and the whole thing snaps back to default. Every
 * adjustment is lost, every time, silently.
 *
 * The app rebuilds its scene from a preset on those two events, and the preset knows
 * nothing about what you had turned on. So the fix is not "stop resetting" — it is to
 * give each panel a memory of its own and re-assert it after the reset lands.
 *
 * WHAT THIS ADDS
 *   · a SAVE button in the header of every control panel that owns any toggle
 *   · the saved set lives in localStorage, so it survives a reload, not just a switch
 *   · after a structure or representation change, every saved panel is re-applied
 *     and a toast says which panels came back and how many controls each restored
 *
 * WHAT IT DELIBERATELY DOES NOT DO
 *   · it does not fight you while you work. Between saves your clicks are yours; the
 *     saved set is only re-asserted after a reset event, never on a timer.
 *   · it does not save what it cannot restore. A control is only recorded if it can
 *     be found again by its own label text — see keyOf().
 *
 * Kept out of index.ts on purpose, like theme-fascia.js: this is a behaviour layer
 * over public DOM, and removing the file removes the feature completely.
 */
(function () {
    var LS = 'dirac.panel.save.v1';
    var BTN = 'dirac-save-btn';

    function txt(el) { return ((el && el.textContent) || '').replace(/\s+/g, ' ').trim(); }

    // A control is addressed by its own label text, not by index. Index breaks the
    // moment a panel renders a conditional row; the label is what the user sees and
    // is what the app itself keys its descriptions on.
    function keyOf(input) {
        var lab = input.closest('label') || input.parentElement;
        return txt(lab).slice(0, 90);
    }

    function panels() {
        var out = [];
        var seen = new Set();
        var inputs = document.querySelectorAll('input[type=checkbox]');
        for (var i = 0; i < inputs.length; i++) {
            var sec = inputs[i].closest('section, fieldset');
            if (!sec || seen.has(sec)) continue;
            seen.add(sec);
            out.push(sec);
        }
        return out;
    }

    function panelName(sec) {
        var h = sec.querySelector(':scope > header, :scope > h2, :scope > h3, header, h2, h3');
        var n = txt(h).slice(0, 40);
        return n || 'panel-' + Array.prototype.indexOf.call(document.querySelectorAll('section, fieldset'), sec);
    }

    function load() {
        try { return JSON.parse(localStorage.getItem(LS) || '{}'); } catch (e) { return {}; }
    }
    function store(o) {
        try { localStorage.setItem(LS, JSON.stringify(o)); } catch (e) { /* private mode */ }
    }

    function snapshot(sec) {
        var m = {};
        var cbs = sec.querySelectorAll('input[type=checkbox]');
        for (var i = 0; i < cbs.length; i++) {
            var k = keyOf(cbs[i]);
            if (k) m[k] = !!cbs[i].checked;
        }
        var sels = sec.querySelectorAll('select');
        var sv = {};
        for (var j = 0; j < sels.length; j++) {
            var sk = keyOf(sels[j]) || ('select-' + j);
            sv[sk] = sels[j].value;
        }
        var rgs = sec.querySelectorAll('input[type=range], input[type=number]');
        var rv = {};
        for (var r = 0; r < rgs.length; r++) {
            var rk = keyOf(rgs[r]) || ('range-' + r);
            rv[rk] = rgs[r].value;
        }
        return { cb: m, sel: sv, rng: rv };
    }

    // React does not see a programmatic `.checked =`, so a checkbox is restored by
    // CLICKING it — and only when it actually differs, so restoring is a no-op when
    // nothing was reset.
    function restore(sec, snap) {
        if (!snap) return 0;
        var changed = 0;
        var cbs = sec.querySelectorAll('input[type=checkbox]');
        for (var i = 0; i < cbs.length; i++) {
            var k = keyOf(cbs[i]);
            if (!(k in snap.cb)) continue;
            if (!!cbs[i].checked !== snap.cb[k]) { cbs[i].click(); changed++; }
        }
        var sels = sec.querySelectorAll('select');
        for (var j = 0; j < sels.length; j++) {
            var sk = keyOf(sels[j]) || ('select-' + j);
            if (!(sk in snap.sel)) continue;
            if (sels[j].value !== snap.sel[sk]) {
                sels[j].value = snap.sel[sk];
                sels[j].dispatchEvent(new Event('change', { bubbles: true }));
                changed++;
            }
        }
        var rgs = sec.querySelectorAll('input[type=range], input[type=number]');
        for (var r = 0; r < rgs.length; r++) {
            var rk = keyOf(rgs[r]) || ('range-' + r);
            if (!(rk in snap.rng)) continue;
            if (rgs[r].value !== snap.rng[rk]) {
                rgs[r].value = snap.rng[rk];
                rgs[r].dispatchEvent(new Event('input', { bubbles: true }));
                rgs[r].dispatchEvent(new Event('change', { bubbles: true }));
                changed++;
            }
        }
        return changed;
    }

    // ── toast ────────────────────────────────────────────────────────────────
    var toastEl = null, toastT = null;
    function toast(msg) {
        if (!toastEl) {
            toastEl = document.createElement('div');
            toastEl.id = 'dirac-save-toast';
            document.body.appendChild(toastEl);
        }
        toastEl.textContent = msg;
        toastEl.setAttribute('data-on', '1');
        clearTimeout(toastT);
        toastT = setTimeout(function () { toastEl.removeAttribute('data-on'); }, 3200);
    }

    // ── buttons ──────────────────────────────────────────────────────────────
    function label(sec, saved) {
        return saved ? 'Saved · re-save' : 'SAVE';
    }

    function mount() {
        var saved = load();
        panels().forEach(function (sec) {
            if (sec.querySelector(':scope > header > .' + BTN) || sec.querySelector('.' + BTN)) return;
            var head = sec.querySelector(':scope > header') || sec.querySelector('header') || sec.firstElementChild;
            if (!head) return;
            var name = panelName(sec);

            var wrap = document.createElement('span');
            wrap.className = BTN + '-wrap';

            var b = document.createElement('button');
            b.type = 'button';
            b.className = BTN;
            b.textContent = label(sec, !!saved[name]);
            b.title = 'Remember this panel\'s current settings. They come back automatically after a molecule or representation change.';
            b.addEventListener('click', function (ev) {
                ev.preventDefault(); ev.stopPropagation();
                var s = load();
                s[name] = snapshot(sec);
                store(s);
                b.textContent = label(sec, true);
                b.setAttribute('data-saved', '1');
                clr.hidden = false;
                var n = Object.keys(s[name].cb).length + Object.keys(s[name].sel).length + Object.keys(s[name].rng).length;
                toast('Saved ' + n + ' control' + (n === 1 ? '' : 's') + ' for \u201c' + name.slice(0, 18) + '\u201d');
            });

            var clr = document.createElement('button');
            clr.type = 'button';
            clr.className = BTN + '-clr';
            clr.textContent = 'Clear';
            clr.hidden = !saved[name];
            clr.title = 'Forget the settings saved for this panel';
            clr.addEventListener('click', function (ev) {
                ev.preventDefault(); ev.stopPropagation();
                var s = load(); delete s[name]; store(s);
                b.textContent = label(sec, false);
                b.removeAttribute('data-saved');
                clr.hidden = true;
                toast('Cleared \u201c' + name.slice(0, 18) + '\u201d');
            });

            if (saved[name]) b.setAttribute('data-saved', '1');
            wrap.appendChild(b);
            wrap.appendChild(clr);
            head.appendChild(wrap);
        });
    }

    // ── re-assert after a reset ──────────────────────────────────────────────
    function restoreAll(why) {
        var saved = load();
        var names = Object.keys(saved);
        if (!names.length) return;
        var total = 0, hit = [];
        panels().forEach(function (sec) {
            var n = panelName(sec);
            if (!saved[n]) return;
            var c = restore(sec, saved[n]);
            if (c) { total += c; hit.push(n.slice(0, 14) + ' ' + c); }
        });
        if (total) toast('Restored ' + total + ' control' + (total === 1 ? '' : 's') + ' · ' + hit.join(' / ') + (why ? ' · ' + why : ''));
    }

    // The two things that wipe the scene are the structure select and the
    // representation select. Both are plain <select>s, so listen on the document and
    // re-assert once the app has finished rebuilding.
    document.addEventListener('change', function (e) {
        var t = e.target;
        if (!t || t.tagName !== 'SELECT') return;
        if (t.closest && t.closest('.' + BTN + '-wrap')) return;
        var opts = Array.prototype.map.call(t.options || [], function (o) { return o.value; });
        var isStructure = opts.indexOf('1CBS') >= 0 || opts.indexOf('1XKK') >= 0;
        var isRepr = opts.indexOf('polymer-and-ligand') >= 0 || opts.indexOf('molecular-surface') >= 0;
        if (!isStructure && !isRepr) return;
        // the rebuild is async and staged; re-assert across the settle window rather
        // than guessing one delay. restore() is a no-op when nothing differs, so the
        // extra passes cost nothing once the scene has settled.
        [900, 2200, 4500, 8000].forEach(function (d) {
            setTimeout(function () { mount(); restoreAll(d === 900 ? 'auto-restored after switch' : ''); }, d);
        });
    }, true);

    // panels are rendered late and re-rendered often; keep the buttons attached
    var boot = setInterval(mount, 700);
    setTimeout(function () { clearInterval(boot); setInterval(mount, 2000); }, 20000);

    // expose for a console check and for the verification harness
    window.diracSave = {
        panels: function () { return panels().map(panelName); },
        saved: load,
        snapshot: function (name) { var s = panels().filter(function (p) { return panelName(p) === name; })[0]; return s ? snapshot(s) : null; },
        restoreAll: restoreAll,
        clearAll: function () { store({}); mount(); },
    };
})();
