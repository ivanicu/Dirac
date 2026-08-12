/**
 * Dirac — light / dark toggle, top right.
 *
 * Sets data-theme on <html> and remembers the choice. theme-dark.css does the rest.
 *
 * The one thing that is not CSS: the 3D scene. The WebGL clear colour is not a
 * stylesheet property — mol* holds it in renderer state — so switching the theme has
 * to push the new --scene-bg into canvas3d as well, or the chrome goes dark and the
 * viewport stays paper white. theme-fascia.js already paints that colour on a timer
 * from its own constant, so it is told the new value rather than raced against.
 */
(function () {
    var LS = 'dirac.theme';
    var root = document.documentElement;

    function stored() {
        try { return localStorage.getItem(LS); } catch (e) { return null; }
    }
    function current() {
        return stored() || (window.matchMedia &&
            window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    }

    function sceneColour() {
        var v = getComputedStyle(root).getPropertyValue('--scene-bg').trim();
        var m = v.match(/^#([0-9a-f]{6})$/i);
        return m ? parseInt(m[1], 16) : 0xf1f0eb;
    }

    function paintScene() {
        var lab = window.molecularVfxLab;
        var wb = lab && lab.workbench;
        var col = sceneColour();
        // tell the theme runtime first: it repaints the background on its own interval
        // from a constant, and would otherwise put the old colour back a second later
        window.__diracSceneBg = col;
        try { if (wb && typeof wb.setBackground === 'function') wb.setBackground(col); } catch (e) { }
        try { wb.plugin.canvas3d.setProps({ renderer: { backgroundColor: col } }); } catch (e) { }
    }

    function apply(mode) {
        if (mode === 'dark') root.setAttribute('data-theme', 'dark');
        else root.removeAttribute('data-theme');
        try { localStorage.setItem(LS, mode); } catch (e) { }
        paint();
        // the scene needs a frame for the new custom property to have resolved
        requestAnimationFrame(paintScene);
        setTimeout(paintScene, 400);
    }

    var btn = null;
    function paint() {
        if (!btn) return;
        var dark = root.getAttribute('data-theme') === 'dark';
        btn.querySelector('span').textContent = dark ? 'DARK' : 'LIGHT';
        btn.setAttribute('aria-pressed', dark ? 'true' : 'false');
        btn.title = dark ? '切换到亮色' : '切换到暗色';
    }

    function mount() {
        if (btn && document.body.contains(btn)) return;
        var tail = document.querySelector('.topbar-tail') || document.querySelector('.topbar');
        if (!tail) return;
        btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'theme-toggle';
        btn.innerHTML = '<i></i><span>LIGHT</span>';
        btn.addEventListener('click', function () {
            apply(root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
        });
        tail.appendChild(btn);
        paint();
    }

    apply(current());
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
    else mount();
    var t = setInterval(function () { mount(); }, 500);
    setTimeout(function () { clearInterval(t); setInterval(mount, 2500); }, 15000);

    // the scene is built after the page, so paint it once it exists
    var s = setInterval(function () {
        var lab = window.molecularVfxLab;
        if (lab && lab.workbench && lab.workbench.plugin) { paintScene(); clearInterval(s); }
    }, 300);
    setTimeout(function () { clearInterval(s); }, 30000);

    window.diracTheme = { get: current, set: apply };
})();
