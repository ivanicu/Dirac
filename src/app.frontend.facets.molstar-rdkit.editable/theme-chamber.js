/**
 * Dirac — "White Chamber" theme, runtime half.
 *
 * The CSS cannot reach inside the WebGL canvas, so the one thing that must be
 * done in script is repainting the mol* renderer background to the paper tone.
 * Kept out of index.ts on purpose: index.ts is shared substrate under active
 * edit, and a theme should not need a line in the app's own lifecycle.
 */
(function () {
    var PAPER = 0xfcfcfb;

    function paint() {
        var lab = window.molecularVfxLab;
        var wb = lab && lab.workbench;
        if (!wb || typeof wb.setBackground !== 'function') return false;
        try {
            wb.setBackground(PAPER);
            return true;
        } catch (e) {
            return false;
        }
    }

    // The workbench is created asynchronously during lab.init(); poll briefly,
    // then keep a light watch so a preset that repaints the canvas (the VFX
    // upgrades set their own background) is brought back to paper.
    var tries = 0;
    var boot = setInterval(function () {
        if (paint() || ++tries > 120) clearInterval(boot);
    }, 120);

    setInterval(paint, 1500);
})();
