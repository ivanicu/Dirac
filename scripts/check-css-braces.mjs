#!/usr/bin/env node
/**
 * S0 item 6: CSS brace balance gate.
 * Falsifies F8 (unclosed CSS brace drops all subsequent rules).
 * Run: node scripts/check-css-braces.mjs <html-file>
 * Exit 1 if unbalanced.
 */
import { readFileSync } from 'fs';
const file = process.argv[2] || 'src/app.frontend.facets.molstar-rdkit.editable/index.html';
const html = readFileSync(file, 'utf8');
const styleMatch = html.match(/<style[^>]*>([\s\S]*?)<\/style>/);
if (!styleMatch) { console.log('No <style> block found'); process.exit(0); }
const css = styleMatch[1];
const opens = (css.match(/{/g) || []).length;
const closes = (css.match(/}/g) || []).length;
if (opens !== closes) {
    console.error(`FAIL: CSS braces unbalanced in ${file}: ${opens} opens vs ${closes} closes (diff=${opens - closes})`);
    process.exit(1);
}
console.log(`OK: CSS braces balanced (${opens}/${closes}) in ${file}`);
