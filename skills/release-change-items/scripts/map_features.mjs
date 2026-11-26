#!/usr/bin/env node
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join, posix, relative } from 'node:path';
import { parseArgs } from 'node:util';

const help = '用法：node map_features.mjs [--repo 目錄] [--files 路徑 ...] [--files-from changes.json | -] [--list-all]';
const skip = new Set(['node_modules', 'dist', '.git', 'bin', 'obj', '.angular']);
const routingFile = /(routing\.module\.ts|(^|[/\\])routes\.ts)$/i;
const normalize = path => path.replaceAll('\\', '/').replace(/^[./]+/, '');
// UTF-8 byte order gives the same Unicode code point ordering as Python's sorted().
const compare = (a, b) => Buffer.compare(Buffer.from(a), Buffer.from(b));

function* routingFiles(directory) {
  let entries;
  try { entries = readdirSync(directory, { withFileTypes: true }); } catch { return; }
  const children = [];
  for (const entry of entries) {
    const full = join(directory, entry.name);
    if (entry.isDirectory()) {
      if (!skip.has(entry.name)) children.push(full);
    } else if (routingFile.test(full)) yield full;
  }
  for (const child of children) yield* routingFiles(child);
}

function buildIndex(repo) {
  const byDir = new Map(), prefixes = new Map();
  for (const file of routingFiles(repo)) {
    let text;
    try { text = readFileSync(file, 'utf8'); } catch { continue; }
    const directoryOf = spec => normalize(relative(repo, join(dirname(file), dirname(spec))));
    const components = new Map();
    for (const [, names, spec] of text.matchAll(/import\s*\{([^}]+)\}\s*from\s*['"](\.[^'"]+)['"]/g)) {
      for (const name of names.split(',').map(s => s.trim())) {
        if (name.endsWith('Component')) components.set(name, directoryOf(spec));
      }
    }
    for (const [, route, component] of text.matchAll(/path\s*:\s*['"]([^'"]*)['"]\s*,\s*component\s*:\s*([\p{L}\p{N}_]+)/gu)) {
      const dir = components.get(component);
      if (!dir) continue;
      if (!byDir.has(dir)) byDir.set(dir, new Set());
      byDir.get(dir).add(route || '(預設頁)');
    }
    for (const [, prefix, spec] of text.matchAll(/path\s*:\s*['"]([^'"]*)['"][^}]*?import\(\s*['"]([^'"]+)['"]/g)) {
      prefixes.set(directoryOf(spec), prefix);
    }
  }
  const index = new Map();
  for (const [dir, routes] of byDir) {
    let prefix = '', best = -1;
    for (const [parent, label] of prefixes) {
      if ((dir === parent || dir.startsWith(parent + '/')) && parent.length > best) {
        best = parent.length; prefix = label;
      }
    }
    index.set(dir, [...routes].map(route => prefix ? ('/' + prefix + '/' + route).replaceAll('//', '/') : '/' + route).sort(compare));
  }
  return index;
}

function mapFiles(index, paths) {
  const containers = new Set();
  for (const dir of index.keys()) {
    for (let parent = posix.dirname(dir); parent !== '.'; parent = posix.dirname(parent)) {
      if (index.has(parent)) containers.add(parent);
    }
  }
  const groups = new Map(), missing = new Set();
  for (const raw of paths) {
    const path = normalize(raw), dir = posix.dirname(path);
    let probe = dir;
    while (probe !== '.' && (!index.has(probe) || (probe !== dir && containers.has(probe)))) {
      probe = posix.dirname(probe);
    }
    if (probe === '.') { missing.add(path); continue; }
    if (!groups.has(probe)) groups.set(probe, { app: path.split('/')[0], dir: probe, routes: index.get(probe), files: [] });
    groups.get(probe).files.push(path);
  }
  return {
    routes: [...groups.values()].sort((a, b) => compare(a.app, b.app) || compare(a.dir, b.dir)),
    unmapped: [...missing].sort(compare),
  };
}

function main() {
  // --files accepts zero or more paths. As with argparse, its last occurrence wins.
  const args = process.argv.slice(2), other = [];
  let files = [];
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--files') {
      files = [];
      while (i + 1 < args.length && !args[i + 1].startsWith('-')) files.push(args[++i]);
    } else if (args[i].startsWith('--files=')) files = [args[i].slice('--files='.length)];
    else other.push(args[i]);
  }
  const { values: opts } = parseArgs({ args: other, options: {
    repo: { type: 'string', default: '.' }, 'files-from': { type: 'string' },
    'list-all': { type: 'boolean' }, help: { type: 'boolean', short: 'h' },
  } });
  if (opts.help) { console.log(help); return; }
  const index = buildIndex(opts.repo);
  if (opts['list-all']) {
    const route_count = [...index.values()].reduce((sum, routes) => sum + routes.length, 0);
    console.log(JSON.stringify({ route_count, index: Object.fromEntries(index) }, null, 1));
    return;
  }
  if (opts['files-from']) {
    const input = readFileSync(opts['files-from'] === '-' ? 0 : opts['files-from'], 'utf8');
    for (const issue of JSON.parse(input).issues ?? []) {
      for (const bucket of ['code', 'config', 'sql', 'build']) {
        for (const record of issue[bucket] ?? []) files.push(record.slice(record.indexOf(' ') + 1));
      }
    }
  }
  if (!files.length) throw new Error('請提供 --files 或 --files-from，或改用 --list-all。');
  console.log(JSON.stringify({ repo: opts.repo, ...mapFiles(index, files) }, null, 1));
}

try { main(); } catch (error) { console.error(error.message); process.exitCode = 1; }
