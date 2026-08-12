/**
 * Dirac — substructure palettes, the runtime half.
 *
 * WHAT THIS IS. Until now the scene had exactly two colours: a uniform graphite for
 * the whole polymer and element colours on the deposited ligand. That is not a
 * colour scheme, it is the absence of one — a 4,586-atom protein painted as a single
 * object. This file breaks every structure into the parts a chemist actually names
 * and gives each part its own colour.
 *
 * 27 CLASSES, all derived from residue name + atom name. No external data, no
 * per-structure special cases, nothing hard-coded for any particular PDB entry:
 *   protein   backbone · carboxylate · guanidinium · ammonium · imidazole · amide
 *             hydroxyl · phenol · indole · phenyl · thiol · thioether · aliphatic
 *             proline · glycine
 *   nucleic   phosphate · sugar · purine · pyrimidine
 *   ligand    ring C · chain C · N · O · S · halogen
 *   other     ion · water
 *
 * THE CARTOON PROBLEM, and why the backbone is coloured twice. A cartoon draws only
 * the backbone trace — side chains are not rendered at all — so colouring side-chain
 * groups has almost no effect on a ribbon. Every backbone atom therefore carries a
 * SECOND label: the class of its own residue's side chain. That is what makes the
 * ribbon itself carry the annotation instead of staying one pale tube.
 *
 * THE PALETTES. Four, chosen 2026-08-11 out of twenty-two that were rendered and
 * compared. The rule they all obey, and which the rejected ones did not: the backbone
 * is 55-70% of the pixels, so it is the GROUND — near-neutral and light — and the
 * chroma budget is spent on the classes with the smallest screen area, which are the
 * ones that must be findable. Value hierarchy first, hue second.
 *
 * `--red` (#c24842) is not used by any palette. It stays reserved for EXCEEDED/FAULT.
 */
(function () {
    var LS = 'dirac.palette.v1';

    // ── classes, in reading order ────────────────────────────────────────────
    var CLASSES = [
        ['bb', '主链'], ['gly', '甘氨酸'], ['ali', '脂肪侧链'], ['pro', '脯氨酸环'],
        ['coo', '羧酸盐'], ['gua', '胍基'], ['nh3', '铵'], ['imi', '咪唑'], ['amd', '酰胺'],
        ['oh', '羟基'], ['phe_oh', '酚'], ['ind', '吲哚'], ['ph', '苯环'],
        ['sh', '硫醇'], ['sme', '硫醚'],
        ['pho', '磷酸'], ['sug', '糖'], ['pur', '嘌呤'], ['pyr', '嘧啶'],
        ['lringC', '配体环碳'], ['lchainC', '配体链碳'], ['lN', '配体 N'], ['lO', '配体 O'],
        ['lS', '配体 S'], ['hal', '卤素'], ['ion', '离子'], ['wat', '水'],
    ];
    var ORDER = CLASSES.map(function (c) { return c[0]; });
    // long-form names, shown in the option tooltip rather than in the option text
    var FULLNAME = {'off': '统一石墨', 'd2': '包豪斯 · 白地', 'd3': '北欧 · 冷灰与鼠尾草', 'd5': '中世纪现代 · 芥末与孔雀', 'c7': 'Okabe-Ito · 双色盲安全'};

    function pal(id, name, note, list) {
        var m = {};
        for (var i = 0; i < ORDER.length; i++) m[ORDER[i]] = parseInt(list[i].slice(1), 16);
        return { id: id, name: name, note: note, m: m };
    }

    var PALETTES = [
        { id: 'off', name: '石墨 · 默认', note: '今天的做法:整场一块灰。留作对照。', m: null },

        pal('d2', '包豪斯', '地几乎是白,只有蓝、黄和一个深梅携带色相,其余落在灰阶上。画面上任何有颜色的东西都是被强调的。',
            ['#eae7df', '#e2ded4', '#d5d1c6', '#c6c2b6',
             '#b0473a', '#2f5590', '#4a72ab', '#7a6aa5', '#9a9a8e',
             '#8d9aa5', '#6f6fa2', '#7d6690', '#8f8c93',
             '#d8b02a', '#a98d3c',
             '#b0473a', '#cfcabc', '#2f5590', '#6d8fb8',
             '#232321', '#4f4e49', '#1f4d94', '#b0392c', '#e0bc1f', '#0f8f6e', '#8b9094', '#efece4']),

        pal('d3', '北欧冷灰', '冷灰做地,鼠尾草绿、雾蓝、陶土做全部中景,只留一个暖点。最安静,长时间盯不累。',
            ['#dfdedb', '#d8d7d2', '#cdccc5', '#c0bfb7',
             '#a9645a', '#4f6f88', '#6d8fa3', '#8b83a0', '#8fa08c',
             '#7fa0a4', '#77809c', '#7c6f88', '#8d8d94',
             '#b2984f', '#96814f',
             '#a07260', '#c9c6ba', '#6f9078', '#8fae9c',
             '#33332f', '#5a5a53', '#3a6a9c', '#a95246', '#c2a134', '#1c8f7d', '#8d9296', '#e9e9e4']),

        pal('d5', '中世纪现代', 'Eames / Girard 那一路:暖灰做地,芥末黄、孔雀蓝、柿子红、橄榄绿四个方向。最有性格。',
            ['#ded9cd', '#d6d0c2', '#c9c2b0', '#bcb49f',
             '#b05540', '#2f6f82', '#4f8f9c', '#8a6f9c', '#8a9a5f',
             '#6f9fa8', '#63699c', '#8a5f7a', '#94868f',
             '#c9a02a', '#a08237',
             '#b06a3f', '#cdc4ac', '#6f9450', '#93b077',
             '#2b2a26', '#565349', '#2a5f9c', '#b04a30', '#d8ae1c', '#0f9478', '#8a9094', '#eae6db']),

        pal('c7', '色盲安全', '从公认的色盲安全色出发,按暖纸底重调,红色盲和绿色盲两种模拟同时验证过 —— 安全性最强的一版。',
            ['#bbb7ad', '#a69e8b', '#a69e8b', '#8a7f65',
             '#a9592b', '#2863ab', '#008fad', '#9a86bf', '#9b8a62',
             '#84a894', '#438463', '#66508b', '#b4935a',
             '#9c721c', '#72510d',
             '#853900', '#96b3a2', '#4576b4', '#9a87bc',
             '#674f00', '#231900', '#00468c', '#6c2d00', '#a77600', '#a6e1c0', '#002d5e', '#d2e1e6']),
    ];

    // ── classifier ───────────────────────────────────────────────────────────
    var BBAT = { N: 1, CA: 1, C: 1, O: 1, OXT: 1 };
    var AA = {};
    'ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL MSE SEC PYL'.split(' ')
        .forEach(function (r) { AA[r] = 1; });
    var NUC = {}; 'DA DC DG DT DU A C G U'.split(' ').forEach(function (r) { NUC[r] = 1; });
    var PUR = { DA: 1, DG: 1, A: 1, G: 1 };
    var WAT = { HOH: 1, WAT: 1, DOD: 1 };
    var PHOS = { P: 1, OP1: 1, OP2: 1, O1P: 1, O2P: 1, "O5'": 1, "O3'": 1 };
    var SUGA = { "C1'": 1, "C2'": 1, "C3'": 1, "C4'": 1, "C5'": 1, "O4'": 1, "O2'": 1 };
    var HALO = { F: 1, CL: 1, BR: 1, I: 1 };
    var IONEL = { ZN: 1, MG: 1, CA: 1, MN: 1, FE: 1, CU: 1, NA: 1, K: 1, CD: 1, NI: 1, CO: 1, HG: 1 };
    var SIDE = {
        ASP: { CG: 'coo', OD1: 'coo', OD2: 'coo', CB: 'ali' },
        GLU: { CD: 'coo', OE1: 'coo', OE2: 'coo', CB: 'ali', CG: 'ali' },
        ARG: { NE: 'gua', CZ: 'gua', NH1: 'gua', NH2: 'gua', CB: 'ali', CG: 'ali', CD: 'ali' },
        LYS: { CE: 'nh3', NZ: 'nh3', CB: 'ali', CG: 'ali', CD: 'ali' },
        HIS: { CG: 'imi', ND1: 'imi', CD2: 'imi', CE1: 'imi', NE2: 'imi', CB: 'ali' },
        ASN: { CG: 'amd', OD1: 'amd', ND2: 'amd', CB: 'ali' },
        GLN: { CD: 'amd', OE1: 'amd', NE2: 'amd', CB: 'ali', CG: 'ali' },
        SER: { CB: 'oh', OG: 'oh' }, THR: { CB: 'oh', OG1: 'oh', CG2: 'ali' },
        TYR: { CG: 'phe_oh', CD1: 'phe_oh', CD2: 'phe_oh', CE1: 'phe_oh', CE2: 'phe_oh', CZ: 'phe_oh', OH: 'phe_oh', CB: 'ali' },
        TRP: { CG: 'ind', CD1: 'ind', CD2: 'ind', NE1: 'ind', CE2: 'ind', CE3: 'ind', CZ2: 'ind', CZ3: 'ind', CH2: 'ind', CB: 'ali' },
        PHE: { CG: 'ph', CD1: 'ph', CD2: 'ph', CE1: 'ph', CE2: 'ph', CZ: 'ph', CB: 'ali' },
        CYS: { CB: 'sh', SG: 'sh' }, MET: { SD: 'sme', CE: 'sme', CB: 'ali', CG: 'ali' },
        MSE: { SE: 'sme', CE: 'sme', CB: 'ali', CG: 'ali' }, PRO: { CB: 'pro', CG: 'pro', CD: 'pro' },
    };

    // built per structure and cached; the cache key is the structure's own ref so a
    // newly dragged-in molecule gets classified the same way a bundled one does.
    var cache = {};
    var lastStats = null;

    function classify(structure, ref) {
        if (cache[ref]) return cache[ref];
        try { return classifyInner(structure, ref); }
        catch (e) { cache[ref] = new Map(); lastStats = { total: 0, unk: 0, hits: {}, error: String(e).slice(0, 90) }; return cache[ref]; }
    }
    function classifyInner(structure, ref) {
        var map = new Map(), bbAtoms = [], resClass = new Map();
        var stats = { total: 0, unk: 0, hits: {} };
        var units = (structure && structure.units) || [];
        for (var ui = 0; ui < units.length; ui++) {
            var u = units[ui];
            var h = u && u.model && u.model.atomicHierarchy;
            if (!h || !u.elements || typeof u.elements.length !== 'number') continue;
            var inRing = new Set();
            try {
                var R = u.rings;
                if (R && R.all) for (var ri = 0; ri < R.all.length; ri++)
                    for (var rj = 0; rj < R.all[ri].length; rj++) inRing.add(u.elements[R.all[ri][rj]]);
            } catch (e) { /* ring perception unavailable on this unit */ }
            for (var i = 0; i < u.elements.length; i++) {
                var e = u.elements[i];
                var comp = (h.atoms.label_comp_id.value(e) || '').toUpperCase();
                var an = h.atoms.label_atom_id.value(e) || '';
                var el = (h.atoms.type_symbol.value(e) || '').toUpperCase();
                var key = u.id + ':' + e, k;
                stats.total++;
                if (WAT[comp]) k = 'wat';
                else if (AA[comp]) {
                    if (BBAT[an]) { k = 'bb'; bbAtoms.push({ key: key, rk: u.id + '#' + h.residueAtomSegments.index[e] }); }
                    else {
                        k = (SIDE[comp] && SIDE[comp][an]) || 'ali';
                        var rk = u.id + '#' + h.residueAtomSegments.index[e];
                        if (!resClass.has(rk) || resClass.get(rk) === 'ali') resClass.set(rk, k);
                    }
                }
                else if (NUC[comp]) k = PHOS[an] ? 'pho' : SUGA[an] ? 'sug' : (PUR[comp] ? 'pur' : 'pyr');
                else if (HALO[el]) k = 'hal';
                else if (IONEL[el]) k = 'ion';
                else if (el === 'N') k = 'lN';
                else if (el === 'O') k = 'lO';
                else if (el === 'S' || el === 'SE') k = 'lS';
                else if (el === 'C') k = inRing.has(e) ? 'lringC' : 'lchainC';
                else if (el === 'H' || el === 'D') k = 'bb';
                else { k = 'lchainC'; stats.unk++; }
                map.set(key, k);
                stats.hits[k] = (stats.hits[k] || 0) + 1;
            }
        }
        // a cartoon draws only the backbone, so give each backbone atom its residue's
        // side-chain class as well — otherwise the ribbon carries no annotation at all
        for (var b = 0; b < bbAtoms.length; b++) {
            var a = bbAtoms[b];
            map.set(a.key, resClass.has(a.rk) ? resClass.get(a.rk) : 'gly');
        }
        lastStats = stats;
        cache[ref] = map;
        return map;
    }

    // ── plugin access ────────────────────────────────────────────────────────
    function plugin() {
        var l = window.molecularVfxLab;
        return l && l.workbench && l.workbench.plugin;
    }

    function active() {
        try { return localStorage.getItem(LS) || 'off'; } catch (e) { return 'off'; }
    }
    function setActive(id) {
        try { localStorage.setItem(LS, id); } catch (e) { }
        window.__diracPaletteActive = id !== 'off';
        cache = {};
        apply(true);
        render();
    }

    var registered = {};
    function ensureTheme(p, palette) {
        var name = 'dirac-palette-' + palette.id;
        if (registered[name]) return name;
        var reg = p.representation.structure.themes.colorThemeRegistry;
        if (reg.types.some(function (t) { return t[0] === name; })) { registered[name] = 1; return name; }
        var provider = {
            name: name, label: palette.name, category: 'Miscellaneous',
            factory: function (ctx, props) {
                var ref = (ctx.structure && ctx.structure.parent && ctx.structure.parent.label) || 'x';
                // classify against the WHOLE structure, not the component substructure:
                // a component only holds its own atoms, so deriving anything from ctx
                // alone would classify the ligand component with no protein in sight.
                var full = ctx.structure;
                try {
                    var cur = p.managers.structure.hierarchy.current.structures;
                    if (cur && cur.length && cur[0].cell.obj) { full = cur[0].cell.obj.data; ref = cur[0].cell.transform.ref; }
                } catch (e) { }
                var map = classify(full, ref + ':' + palette.id);
                return {
                    factory: provider.factory, granularity: 'group',
                    color: function (l) {
                        var k = map.get(l.unit && (l.unit.id + ':' + l.element));
                        var c = k === undefined ? undefined : palette.m[k];
                        return c === undefined ? 0x8e8d87 : c;
                    },
                    props: props, description: palette.name,
                };
            },
            getParams: function () { return {}; }, defaultValues: {}, isApplicable: function () { return true; },
        };
        try { reg.add(provider); registered[name] = 1; } catch (e) { }
        return name;
    }

    function apply(force) {
        var id = active();
        window.__diracPaletteActive = id !== 'off';
        if (id === 'off') return;
        var p = plugin();
        var mgr = p && p.managers && p.managers.structure;
        if (!mgr || !mgr.hierarchy || !mgr.hierarchy.current) return;
        var structures = mgr.hierarchy.current.structures;
        if (!structures || !structures.length) return;
        var palette = PALETTES.filter(function (x) { return x.id === id; })[0];
        if (!palette || !palette.m) return;
        var name = ensureTheme(p, palette);
        structures.forEach(function (s) {
            s.components.forEach(function (c) {
                var stale = (c.representations || []).some(function (r) {
                    var pr = r.cell.transform.params;
                    return !pr || !pr.colorTheme || pr.colorTheme.name !== name;
                });
                if (!force && !stale) return;
                try { mgr.component.updateRepresentationsTheme([c], { color: name }); } catch (e) { }
            });
        });
    }

    // ── picker ───────────────────────────────────────────────────────────────
    // A dropdown in the app's own control row, next to Molecule and Representation —
    // NOT a floating panel and NOT a UI theme. What it switches is how THIS MOLECULE
    // is painted; the interface's own colours are unaffected.
    var sel = null;
    function render() {
        var row = document.querySelector('.topbar-scene');
        if (!row) return;

        if (!sel || !document.body.contains(sel)) {
            var lab = document.createElement('label');
            lab.className = 'dirac-palette-label';
            lab.appendChild(document.createTextNode('Palette'));

            sel = document.createElement('select');
            sel.id = 'dirac-palette';
            PALETTES.forEach(function (p) {
                var o = document.createElement('option');
                o.value = p.id;
                o.textContent = p.name;
                o.title = (FULLNAME[p.id] ? FULLNAME[p.id] + ' — ' : '') + p.note;
                sel.appendChild(o);
            });
            sel.addEventListener('change', function () { setActive(sel.value); });
            lab.appendChild(sel);

            var repr = document.getElementById('representation');
            var after = repr && repr.closest('label');
            if (after && after.parentNode === row) row.insertBefore(lab, after.nextSibling);
            else row.appendChild(lab);
            row.classList.add('has-dirac-palette');
        }

        var id = active();
        if (sel.value !== id) sel.value = id;
    }

    // ── wiring ───────────────────────────────────────────────────────────────
    var wired = false;
    function wire() {
        var p = plugin();
        if (!p || wired) return;
        try {
            p.state.data.events.changed.subscribe(function () { apply(false); render(); });
            wired = true;
        } catch (e) { }
    }

    var tries = 0;
    var boot = setInterval(function () {
        wire(); apply(false);
        render();
        if ((wired && sel) || ++tries > 200) clearInterval(boot);
    }, 80);
    setInterval(function () { wire(); apply(false); }, 1500);

    window.diracPalette = {
        list: function () { return PALETTES.map(function (p) { return p.id; }); },
        get: active, set: setActive,
        stats: function () { return lastStats; },
        classes: CLASSES,
    };
})();
