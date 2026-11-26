#!/usr/bin/env node
import { spawnSync } from 'node:child_process';
import { parseArgs } from 'node:util';

const options = Object.fromEntries(
  ['repo', 'range', 'issues', 'commits', 'max-commits', 'max-diff-lines', 'max-total-diff-lines', 'max-total-diff-chars']
    .map((name) => [name, { type: 'string' }]),
);
options['with-diff'] = { type: 'boolean' };
options.pretty = { type: 'boolean' };
options.help = { type: 'boolean', short: 'h' };

const decoders = ['utf-8', 'big5', 'windows-1252'].map((encoding) =>
  new TextDecoder(encoding, { fatal: true, ignoreBOM: true }));

function decode(bytes) {
  // Old repositories can mix UTF-8 with CP950/Big5 author names and messages.
  for (const decoder of decoders) {
    try { return decoder.decode(bytes); } catch { /* try the next encoding */ }
  }
  return bytes.toString('utf8');
}

function git(repo, args, allowFailure = false) {
  const result = spawnSync('git', ['-C', repo, ...args], { maxBuffer: Infinity, windowsHide: true });
  if (result.error) throw result.error;
  if (result.status === 0) return result.stdout;
  if (allowFailure) return Buffer.alloc(0);
  throw new Error('git ' + args.join(' ') + ' 失敗：' + decode(result.stderr).trim());
}

function splitLines(text) {
  if (!text) return [];
  const lines = text.split(/\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]/u);
  if (lines.at(-1) === '') lines.pop();
  return lines;
}

function csv(text) {
  return (text ?? '').split(',').map((entry) => entry.trim()).filter(Boolean);
}

function issuesIn(subject, body) {
  const found = Array.from((subject + '\n' + body).matchAll(/#(\p{Decimal_Number}{3,6})/gu), ([, issue]) => issue);
  return [...new Set(found.filter((issue) => issue !== '0000'))];
}

function fileChanges(block) {
  const status = new Map();
  const stats = [];
  for (const line of splitLines(block)) {
    const columns = line.split('\t');
    if (line.startsWith(':')) {
      status.set(columns.at(-1), columns[0].split(' ').at(-1)[0]);
    } else if (columns.length >= 3) {
      stats.push(columns);
    }
  }
  return stats.map((columns) => ({
    path: columns.at(-1), status: status.get(columns.at(-1)) ?? 'M',
    added: columns[0] === '-' ? null : Number(columns[0]),
    deleted: columns[1] === '-' ? null : Number(columns[1]),
  }));
}

function listCommits(repo, revisions, limit) {
  const format = '\x1e' + ['%H', '%h', '%an', '%ad', '%s', '%b'].join('\x1f') + '\x1e';
  const bytes = git(repo, ['log', '--no-merges', '--pretty=format:' + format, '--date=short',
    '--raw', '--numstat', '-M', ...(limit ? ['-n' + limit] : []), ...revisions]);
  const commits = [];
  let start = bytes.indexOf(0x1e);
  while (start !== -1) {
    const separator = bytes.indexOf(0x1e, start + 1);
    if (separator < 0) break;
    const next = bytes.indexOf(0x1e, separator + 1);
    const header = decode(bytes.subarray(start + 1, separator)).split('\x1f');
    if (header.length >= 6) {
      const [full_hash, hash, author, date, subject, body] = header;
      commits.push({ hash, full_hash, author, date, subject, body: body.trim(), issues: issuesIn(subject, body),
        files: fileChanges(decode(bytes.subarray(separator + 1, next < 0 ? bytes.length : next))) });
    }
    start = next;
  }
  return commits;
}

function resolve(repo, args, wanted, limit) {
  if (args.commits) {
    const refs = csv(args.commits);
    return { commits: refs.flatMap((ref) => {
      const found = listCommits(repo, ['-1', ref]);
      if (!found.length) throw new Error('找不到 commit：' + ref);
      return found;
    }), mode: 'commits', spec: refs.join(','), merges: 0 };
  }
  if (args.range) {
    const commits = listCommits(repo, [args.range], limit);
    const merges = splitLines(decode(git(repo, ['log', '--merges', '--pretty=format:%h', args.range], true)))
      .filter((line) => line.trim()).length;
    return { commits, merges, mode: wanted.length ? 'range+issues' : 'range',
      spec: args.range + (wanted.length ? ' 限定單號 ' + wanted.join(',') : '') };
  }
  if (!wanted.length) throw new Error('請至少指定 --range、--issues 或 --commits 其中一種範圍');
  const commits = new Map();
  for (const issue of wanted) {
    for (const commit of listCommits(repo, ['--all', '--grep=#' + issue], limit)) commits.set(commit.full_hash, commit);
  }
  return { commits: [...commits.values()], mode: 'issues', spec: wanted.join(','), merges: 0 };
}

function boundedDiff(repo, commit, file, lineLimit, charLimit) {
  const source = decode(git(repo, ['show', '--format=', '-M', commit.full_hash, '--', file.path], true));
  const lines = splitLines(source);
  const patch = lines.slice(0, lineLimit).join('\n');
  let chars = 0;
  let end = 0;
  for (const char of patch) {
    if (chars === charLimit) break;
    end += char.length;
    chars++;
  }
  const text = patch.slice(0, end);
  return { diff: { patch: text, truncated: lines.length > lineLimit || end < patch.length, total_lines: lines.length },
    chars, lines: splitLines(text).length };
}

function addDiffs(repo, groups, limits) {
  const seen = new Set();
  let lines = 0;
  let chars = 0;
  for (const commits of groups) {
    for (const commit of commits) {
      for (const file of commit.files) {
        const key = commit.full_hash + '\0' + file.path;
        if (seen.has(key)) file.diff = { omitted: 'duplicate', source_commit: commit.hash };
        else if (lines >= limits.totalLines || chars >= limits.totalChars) file.diff = { omitted: 'total_budget', truncated: true };
        else {
          const part = boundedDiff(repo, commit, file, Math.min(limits.lines, limits.totalLines - lines), limits.totalChars - chars);
          file.diff = part.diff;
          chars += part.chars;
          lines += part.lines;
        }
        seen.add(key);
      }
    }
  }
  return { max_total_lines: limits.totalLines, emitted_lines: lines, max_total_chars: limits.totalChars, emitted_chars: chars };
}

function collect(repo, args, wanted, range, limits) {
  const issues = new Map();
  const outOfScope = new Map();
  const unlabeled = [];
  function group(issue) {
    if (!issues.has(issue)) issues.set(issue, { commits: [], files: [] });
    return issues.get(issue);
  }
  for (const commit of range.commits) {
    if (!commit.issues.length) unlabeled.push(commit);
    for (const issue of commit.issues) {
      if (wanted.length && !wanted.includes(issue)) {
        if (!outOfScope.has(issue)) outOfScope.set(issue, []);
        outOfScope.get(issue).push(commit.hash);
      } else {
        // A commit can appear in several output groups with different diff budgets.
        group(issue).commits.push(args['with-diff'] ? structuredClone(commit) : commit);
      }
    }
  }
  for (const issue of wanted) group(issue);
  for (const entry of issues.values()) {
    const merged = new Map();
    for (const commit of entry.commits) {
      for (const file of commit.files) {
        if (!merged.has(file.path)) merged.set(file.path, { path: file.path, status: file.status, added: 0, deleted: 0, commits: [] });
        const row = merged.get(file.path);
        row.added += file.added ?? 0;
        row.deleted += file.deleted ?? 0;
        if (!row.commits.includes(commit.hash)) row.commits.push(commit.hash);
      }
    }
    entry.files = [...merged.values()].sort((a, b) => Buffer.compare(Buffer.from(a.path), Buffer.from(b.path)));
  }
  const output = { repo, mode: range.mode, spec: range.spec, merge_commits: range.merges, issues: Object.fromEntries(issues),
    unlabeled, out_of_scope_issues: Object.fromEntries(outOfScope),
    summary: { issue_count: issues.size, commit_count: range.commits.length, unlabeled_count: unlabeled.length } };
  if (args['with-diff']) output.diff_budget = addDiffs(repo, [...Array.from(issues.values(), (entry) => entry.commits), unlabeled], limits);
  return output;
}

function integer(raw, name, fallback) {
  if (raw === undefined) return fallback;
  if (!/^[-+]?\d+$/.test(raw) || !Number.isSafeInteger(Number(raw))) throw new Error('--' + name + ' 必須為整數');
  return Number(raw);
}

try {
  const { values: args } = parseArgs({ options });
  if (args.help) {
    console.log('node collect_issue_changes.mjs [--repo DIR] --range REV|--issues IDS|--commits REFS [--max-commits N] [--with-diff] [--max-diff-lines N] [--max-total-diff-lines N] [--max-total-diff-chars N] [--pretty]');
  } else {
    const repo = args.repo ?? '.';
    const wanted = csv(args.issues).map((id) => id.replace(/^#+/, ''));
    const limits = { lines: integer(args['max-diff-lines'], 'max-diff-lines', 400),
      totalLines: integer(args['max-total-diff-lines'], 'max-total-diff-lines', 1200),
      totalChars: integer(args['max-total-diff-chars'], 'max-total-diff-chars', 48000) };
    if (Math.min(...Object.values(limits)) < 1) throw new Error('diff 行數上限必須大於 0');
    const range = resolve(repo, args, wanted, integer(args['max-commits'], 'max-commits'));
    console.log(JSON.stringify(collect(repo, args, wanted, range, limits), null, args.pretty ? 2 : undefined));
  }
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
