/**
 * Dirac — "Instrument Fascia" theme (NX-01), runtime half.
 *
 * Two things CSS cannot reach:
 *   1. the WebGL clear colour — repainted to the display well's tone;
 *   2. the polymer's colour theme — the fascia is strictly achromatic, so the
 *      protein is set to a uniform graphite. Semantic layers still paint over
 *      it, which is the point: on an achromatic body, a layer's colour IS the
 *      information.
 *
 * Kept out of index.ts on purpose: index.ts is shared substrate under active
 * edit, and a theme should not need a line in the app's own lifecycle. Both
 * calls go through public plugin APIs and are undone by removing this file.
 */
(function () {
    var WELL = 0xf1f0eb;      // recessed display well
    var GRAPHITE = 0x8e8d87;  // machined body tone for the polymer

    function lab() { return window.molecularVfxLab; }
    function wb() { var l = lab(); return l && l.workbench; }

    function paintBackground() {
        var w = wb();
        if (!w || typeof w.setBackground !== 'function') return false;
        var col = (typeof window.__diracSceneBg === 'number') ? window.__diracSceneBg : WELL;
        try { w.setBackground(col); return true; } catch (e) { return false; }
    }

    // Uniform graphite on every structure component, so the only colour in the
    // scene is whatever a semantic layer deliberately puts there.
    var lastStructureKey = null;
    function neutralise() {
        var w = wb();
        var plugin = w && w.plugin;
        var mgr = plugin && plugin.managers && plugin.managers.structure;
        if (!mgr || !mgr.hierarchy || !mgr.component) return;

        var structures = mgr.hierarchy.current && mgr.hierarchy.current.structures;
        if (!structures || !structures.length) return;

        // only act when the loaded structure changes — never fight the user
        var key = structures.map(function (s) { return s.cell.transform.ref; }).join('|');
        if (key === lastStructureKey) return;
        lastStructureKey = key;

        // The app applies its own representation preset shortly after a structure
        // lands, so a single attempt loses the race. Re-apply across the settle
        // window, then stop — anything the user switches on afterwards must survive.
        [0, 700, 1800, 3500].forEach(function (delay) {
            setTimeout(function () {
                var c = mgr.hierarchy.current && mgr.hierarchy.current.structures;
                if (!c) return;
                c.forEach(function (s) {
                    try {
                        mgr.component.updateRepresentationsTheme(s.components, {
                            color: 'uniform',
                            colorParams: { value: GRAPHITE }
                        });
                    } catch (e) { /* a representation that refuses a uniform theme keeps its own */ }
                });
            }, delay);
        });
    }

    var tries = 0;
    var boot = setInterval(function () {
        if (paintBackground() || ++tries > 120) clearInterval(boot);
    }, 120);

    setInterval(function () { paintBackground(); neutralise(); }, 1200);
})();
