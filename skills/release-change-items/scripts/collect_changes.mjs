#!/usr/bin/env node
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { parseArgs } from 'node:util';

const help = '用法：node collect_changes.mjs [--repo .] (--range main..HEAD | --issues 4590,4432) [--pretty]\n可同時指定 --range 與 --issues。省略範圍時從目前 HEAD 的歷史搜尋單號。';
const rules = [
  ['sql', /\.sql$/i],
  ['config', /appsettings[^/\\]*\.json$|web\.config$|environments[/\\][^/\\]+\.ts$|(^|[/\\])\.env|nginx[^/\\]*\.conf$|[/\\]Dockerfile$|docker-compose[^/\\]*\.ya?ml$/i],
  ['doc', /\.md$|^(docs|openspec|說明文件)[/\\]|[/\\]docs[/\\]/i],
  ['build', /package(-lock)?\.json$|\.csproj$|\.sln$|angular\.json$|tsconfig[^/\\]*\.json$/i],
];
const numericOrder = new Intl.Collator('en', { numeric: true, sensitivity: 'base' });

// Read one commit at a time, so a large diff cannot hit a child-process output buffer limit.
async function readLog(repo, range, grepIssue, consume) {
  const args = ['log', '--no-merges', '--name-status', '--date=short', '--pretty=format:\x02%H\x01%s\x01%an\x01%ad'];
  if (grepIssue) args.push('--grep', '#' + grepIssue);
  if (range) args.push('--end-of-options', range);
  const child = spawn('git', ['-C', repo, ...args], { stdio: ['ignore', 'pipe', 'inherit'], windowsHide: true });
  const outcome = new Promise(resolve => {
    child.once('error', resolve);
    child.once('close', code => resolve(code));
  });
  let commit;
  child.stdout.setEncoding('utf8');
  for await (const line of createInterface({ input: child.stdout, crlfDelay: Infinity })) {
    if (line.startsWith('\x02')) {
      if (commit) consume(commit);
      const [sha, subject, , date] = line.slice(1).split('\x01');
      commit = date === undefined ? undefined : { sha: sha.slice(0, 8), subject, date, files: [] };
    } else if (commit) {
      const columns = line.trim().split('\t');
      if (columns.length >= 2) commit.files.push([columns[0], columns.at(-1)]);
    }
  }
  const result = await outcome;
  if (result instanceof Error) throw result;
  if (result !== 0) throw new Error('git 指令失敗：git ' + args.join(' '));
  if (commit) consume(commit);
}

async function main() {
  const { values: opts } = parseArgs({ options: {
    repo: { type: 'string', default: '.' }, range: { type: 'string' }, issues: { type: 'string' },
    pretty: { type: 'boolean' }, help: { type: 'boolean', short: 'h' },
  } });
  if (opts.help) { console.log(help); return; }
  const wanted = new Set((opts.issues ?? '').split(',').map(s => s.trim()).filter(Boolean).map(s => s.replace(/^#+/, '')));
  if (!opts.range && !wanted.size) throw new Error('請至少提供 --range 或 --issues 其中之一。');

  const groups = new Map();
  function add(commit) {
    const ids = [...commit.subject.matchAll(/#(\p{Decimal_Number}{3,6})/gu)].map(m => m[1]).filter(id => id !== '0000');
    for (const issue of ids.length ? ids : ['未標號']) {
      if (wanted.size && !wanted.has(issue)) continue;
      if (!groups.has(issue)) groups.set(issue, { issue, commits: [], sql: new Set(), config: new Set(), code: new Set(), doc: new Set(), build: new Set() });
      const group = groups.get(issue);
      group.commits.push([commit.sha, commit.date, commit.subject].join(' '));
      for (const [status, path] of commit.files) {
        const bucket = rules.find(([, rule]) => rule.test(path))?.[0] ?? 'code';
        group[bucket].add(status + ' ' + path);
      }
    }
  }
  if (opts.range) await readLog(opts.repo, opts.range, null, add);
  else {
    const seen = new Set();
    for (const issue of [...wanted].sort()) {
      await readLog(opts.repo, null, issue, commit => {
        if (seen.has(commit.sha)) return;
        seen.add(commit.sha);
        add(commit);
      });
    }
  }
  const issues = [...groups.values()].sort((a, b) =>
    Number(a.issue === '未標號') - Number(b.issue === '未標號') || numericOrder.compare(a.issue, b.issue));
  const output = { repo: opts.repo, range: opts.range ?? null, issue_count: issues.length, issues };
  console.log(JSON.stringify(output, (_, v) => v instanceof Set ? [...v] : v, opts.pretty ? 1 : undefined));
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
