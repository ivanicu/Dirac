#!/usr/bin/env node
/** Extract function-level TypeScript structure using the compiler, not regex. */
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import ts from 'typescript';

const ROOT = path.resolve(import.meta.dirname, '..');
const scope = JSON.parse(fs.readFileSync(path.join(ROOT, 'scripts/digital_twin_scope.json'), 'utf8'));
const roots = scope.include_roots.map(x => x.replace(/\/$/, ''));
const externalRoots = scope.external_roots.map(x => x.replace(/\/$/, ''));
const rootFiles = new Set(scope.include_root_files);
const inScope = rel => (rootFiles.has(rel) || roots.some(root => rel === root || rel.startsWith(`${root}/`))
        || (scope.auto_include_code_extensions.includes(path.extname(rel))
            && !externalRoots.some(root => rel === root || rel.startsWith(`${root}/`))))
    && !scope.exclude_fragments.some(fragment => `/${rel}`.includes(fragment))
    && !scope.exclude_suffixes.some(suffix => rel.endsWith(suffix));
const discovered = execFileSync('git', ['ls-files', '--cached', '--others', '--exclude-standard', '-z'],
    { cwd: ROOT, encoding: 'utf8' }).split('\0').filter(Boolean);
const files = discovered.filter(rel => inScope(rel) && /\.(?:tsx?|m?js)$/.test(rel) && !/\.d\.ts$/.test(rel))
    .map(rel => path.join(ROOT, rel)).filter(file => fs.existsSync(file));
const config = ts.readConfigFile(path.join(ROOT, 'tsconfig.json'), ts.sys.readFile);
const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, ROOT);
const program = ts.createProgram(files, { ...parsed.options, allowJs: true, checkJs: false, noEmit: true });
const checker = program.getTypeChecker();
const nodes = [];
const edges = [];
const declToId = new Map();
const symbolToId = new Map();
const moduleIds = new Map();

const rel = file => path.relative(ROOT, file).replaceAll(path.sep, '/');
const language = file => /\.(?:m?js)$/.test(file) ? 'javascript' : 'typescript';
const layer = file => {
    const p = rel(file);
    if (p.includes('/_spec/')) return 'verification';
    if (p.startsWith('scripts/')) return 'tooling';
    if (p.startsWith('src/app/shell/')) return 'application-shell';
    if (p.startsWith('src/app/context/') || p.startsWith('src/app/domain/')) return 'scientific-context';
    if (p.startsWith('src/app/services/')) return 'client-sdk';
    if (p.startsWith('src/app.frontend')) return 'presentation';
    if (p.startsWith('src/chemistry')) return 'browser-compute';
    if (p.startsWith('src/mol-plugin-chem')) return 'molstar-extension';
    return 'frontend-core';
};
const position = (sf, node) => {
    const start = sf.getLineAndCharacterOfPosition(node.getStart(sf));
    const end = sf.getLineAndCharacterOfPosition(node.getEnd());
    return { line: start.line + 1, column: start.character + 1,
        end_line: end.line + 1, end_column: end.character + 1 };
};
const moduleId = sf => {
    const p = rel(sf.fileName);
    let id = moduleIds.get(p);
    if (!id) {
        id = `module:ts:${p}`;
        moduleIds.set(p, id);
        nodes.push({ id, type: 'module', language: language(sf.fileName), name: path.basename(p),
            path: p, layer: layer(sf.fileName), ...position(sf, sf) });
    }
    return id;
};
const nodeName = node => {
    if (node.name?.getText) return node.name.getText().replace(/^['"]|['"]$/g, '');
    if (ts.isConstructorDeclaration(node)) return 'constructor';
    if (ts.isArrowFunction(node) || ts.isFunctionExpression(node)) {
        const parent = node.parent;
        if (ts.isVariableDeclaration(parent)) return parent.name.getText();
        if (ts.isPropertyAssignment(parent)) return parent.name.getText();
    }
    return '<anonymous>';
};
const ownerName = node => {
    let p = node.parent;
    while (p) {
        if (ts.isClassDeclaration(p) || ts.isInterfaceDeclaration(p)) return p.name?.getText() || '<anonymous-class>';
        p = p.parent;
    }
    return undefined;
};
const isFunctionLike = node => ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node)
    || ts.isConstructorDeclaration(node) || ts.isGetAccessorDeclaration(node)
    || ts.isSetAccessorDeclaration(node) || ts.isArrowFunction(node)
    || ts.isFunctionExpression(node);

for (const sf of program.getSourceFiles().filter(s => files.includes(s.fileName))) {
    const mid = moduleId(sf);
    const visit = node => {
        if (isFunctionLike(node)) {
            const pos = position(sf, node);
            const owner = ownerName(node);
            const name = nodeName(node);
            const id = `function:ts:${rel(sf.fileName)}:${pos.line}:${pos.column}:${owner ? owner + '.' : ''}${name}`;
            const params = (node.parameters || []).map(p => p.name.getText());
            nodes.push({ id, type: ts.isMethodDeclaration(node) || ts.isConstructorDeclaration(node)
                || ts.isGetAccessorDeclaration(node) || ts.isSetAccessorDeclaration(node) ? 'method' : 'function',
                language: language(sf.fileName), name, qualified_name: owner ? `${owner}.${name}` : name,
                owner, parameters: params, async: Boolean(node.modifiers?.some(m => m.kind === ts.SyntaxKind.AsyncKeyword)),
                exported: Boolean(node.modifiers?.some(m => m.kind === ts.SyntaxKind.ExportKeyword)),
                path: rel(sf.fileName), layer: layer(sf.fileName), ...pos });
            edges.push({ source: mid, target: id, type: 'contains' });
            declToId.set(node, id);
            const symbol = node.name ? checker.getSymbolAtLocation(node.name) : undefined;
            if (symbol) symbolToId.set(symbol, id);
            if ((ts.isArrowFunction(node) || ts.isFunctionExpression(node)) && ts.isVariableDeclaration(node.parent)) {
                const varSymbol = checker.getSymbolAtLocation(node.parent.name);
                if (varSymbol) symbolToId.set(varSymbol, id);
            }
        }
        ts.forEachChild(node, visit);
    };
    visit(sf);
}

function enclosingFunction(node) {
    let p = node.parent;
    while (p) {
        if (declToId.has(p)) return declToId.get(p);
        p = p.parent;
    }
    return undefined;
}

for (const sf of program.getSourceFiles().filter(s => files.includes(s.fileName))) {
    const mid = moduleId(sf);
    const visit = node => {
        if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier)) {
            const resolved = ts.resolveModuleName(node.moduleSpecifier.text, sf.fileName,
                parsed.options, ts.sys).resolvedModule?.resolvedFileName;
            if (resolved && files.includes(resolved)) {
                edges.push({ source: mid, target: moduleId(program.getSourceFile(resolved)), type: 'imports' });
            }
        }
        if (ts.isCallExpression(node)) {
            const source = enclosingFunction(node);
            if (source) {
                let symbol = checker.getSymbolAtLocation(node.expression);
                if (symbol?.flags & ts.SymbolFlags.Alias) symbol = checker.getAliasedSymbol(symbol);
                let target = symbol ? symbolToId.get(symbol) : undefined;
                if (!target) {
                    const sig = checker.getResolvedSignature(node);
                    let decl = sig?.declaration;
                    while (decl && !declToId.has(decl)) decl = decl.parent;
                    target = decl ? declToId.get(decl) : undefined;
                }
                if (target && target !== source) edges.push({ source, target, type: 'calls' });
            }
        }
        ts.forEachChild(node, visit);
    };
    visit(sf);
}

const uniqueEdges = [...new Map(edges.map(e => [`${e.source}|${e.type}|${e.target}`, e])).values()];

function unwrap(node) {
    while (node && (ts.isAsExpression(node) || ts.isSatisfiesExpression(node)
        || ts.isParenthesizedExpression(node))) node = node.expression;
    return node;
}

function literal(node) {
    node = unwrap(node);
    if (!node) return undefined;
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text;
    if (ts.isNumericLiteral(node)) return Number(node.text);
    if (node.kind === ts.SyntaxKind.TrueKeyword) return true;
    if (node.kind === ts.SyntaxKind.FalseKeyword) return false;
    if (node.kind === ts.SyntaxKind.NullKeyword) return null;
    if (ts.isArrayLiteralExpression(node)) return node.elements.map(literal);
    if (ts.isObjectLiteralExpression(node)) {
        const out = {};
        for (const p of node.properties) {
            if (!ts.isPropertyAssignment(p)) continue;
            out[p.name.getText().replace(/^['"]|['"]$/g, '')] = literal(p.initializer);
        }
        return out;
    }
    return undefined;
}

const registries = { workspaces: [], views: [], modules: [] };
const registryFile = program.getSourceFiles().find(sf => rel(sf.fileName) === 'src/app/shell/registries.ts');
if (registryFile) {
    for (const statement of registryFile.statements) {
        if (!ts.isVariableStatement(statement)) continue;
        for (const declaration of statement.declarationList.declarations) {
            const name = declaration.name.getText();
            const init = unwrap(declaration.initializer);
            if (name === 'WORKSPACES') registries.workspaces = literal(init) || [];
            if (name === 'MODULES') registries.modules = literal(init) || [];
            if (name === 'VIEWS' && ts.isArrayLiteralExpression(init)) {
                registries.views = init.elements.map(element => {
                    element = unwrap(element);
                    if (!ts.isCallExpression(element) || element.expression.getText() !== 'view') return literal(element);
                    const a = element.arguments.map(literal);
                    const primary = a[6] || [];
                    return { id: a[0], workspace: a[1], label: a[2], route: a[3],
                        implemented: a[4] ?? false, shellReady: true,
                        modules: a[5] || [], primaryObjectKinds: primary,
                        actions: a[7] || [], acceptedContext: primary };
                });
            }
        }
    }
}

// This tool is a structural AST extractor. The repository's build/test gates own
// semantic checking; here we only reject syntax that would make the twin incomplete.
const diagnostics = program.getSyntacticDiagnostics()
    .map(d => ({ code: d.code,
        path: d.file ? rel(d.file.fileName) : undefined,
        line: d.file && d.start !== undefined
            ? d.file.getLineAndCharacterOfPosition(d.start).line + 1 : undefined,
        message: ts.flattenDiagnosticMessageText(d.messageText, '\n') }));
process.stdout.write(JSON.stringify({ nodes, edges: uniqueEdges, registries, diagnostics }));
