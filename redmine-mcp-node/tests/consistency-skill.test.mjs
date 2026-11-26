import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { select_skill, can_update_redmine } from '../../skills/issue-code-consistency-check/scripts/consistency_gate.mjs';

const tool = fileURLToPath(new URL('../../skills/issue-code-consistency-check/scripts/collect_issue_changes.mjs', import.meta.url));

test('consistency collector resolves branches, issues and explicit commits, with per-group patches', (t) => {
  const repo = mkdtempSync(join(tmpdir(), 'redmine-collector-'));
  t.after(() => rmSync(repo, { recursive: true, force: true }));
  function git(...args) {
    return execFileSync('git', ['-C', repo, ...args], { encoding: 'utf8', windowsHide: true }).trim();
  }
  function collect(...args) {
    return JSON.parse(execFileSync(process.execPath, [tool, '--repo', repo, ...args], { encoding: 'utf8' }));
  }
  git('init', '-q');
  git('config', 'user.name', 'Fixture');
  git('config', 'user.email', 'fixture@example.invalid');
  git('config', 'commit.gpgsign', 'false');
  git('commit', '--allow-empty', '-qm', 'baseline');
  const base = git('rev-parse', 'HEAD');
  writeFileSync(join(repo, 'entry.txt'), '資料🙂\n');
  git('add', 'entry.txt');
  git('commit', '-qm', '#9000 shared', '-m', '涉及 #3010');
  const id = git('rev-parse', 'HEAD');
  writeFileSync(join(repo, 'entry.txt'), 'updated\n');
  git('add', 'entry.txt');
  git('commit', '-qm', '#0000 unrelated');

  const data = collect('--range', base + '..HEAD', '--with-diff', '--pretty');
  assert.deepEqual(data.summary, { issue_count: 2, commit_count: 2, unlabeled_count: 1 });
  const shared = data.issues['9000'].commits[0];
  assert.equal(shared.full_hash, id);
  assert.equal(shared.body, '涉及 #3010');
  assert.deepEqual(shared.issues, ['9000', '3010']);
  assert.match(shared.files[0].diff.patch, /\+資料🙂/);
  assert.equal(data.issues['3010'].commits[0].files[0].diff.omitted, 'duplicate');
  assert.deepEqual(data.issues['3010'].files, [{ path: 'entry.txt', status: 'A', added: 1, deleted: 0, commits: [shared.hash] }]);
  const partial = collect('--range', base + '..HEAD', '--issues', '3010,7777', '--with-diff', '--max-total-diff-chars', '6');
  assert.equal(partial.issues['3010'].commits[0].files[0].diff.patch, 'diff -');
  assert.deepEqual(partial.issues['7777'], { commits: [], files: [] });
  assert.deepEqual(partial.out_of_scope_issues['9000'], [shared.hash]);
  assert.equal(partial.unlabeled[0].files[0].diff.omitted, 'total_budget');
  assert.equal(collect('--issues', '3010,9000').summary.commit_count, 1);
  assert.equal(collect('--commits', id).issues['9000'].commits[0].full_hash, id);
});

test('consistency selection and evidence gates retain their documented decisions', () => {
  const candidates = [{ name: 'issue-code-consistency-check' }, { name: 'generic-consistency', description: 'issue code' }];
  assert.deepEqual(select_skill({ availableSkills: candidates }), { status: 'UNAVAILABLE', skill: null });
  assert.deepEqual(select_skill({ needsCodeConsistency: true, issueSystem: 'Redmine', availableSkills: candidates }),
    { status: 'SELECTED', skill: 'issue-code-consistency-check' });
  const evidence = { consistency: 'PASS', screenshots: { allMasked: true, attachmentsVerified: true } };
  assert.equal(can_update_redmine(evidence), true);
  for (const key of ['failed', 'blocked', 'notRun']) {
    assert.equal(can_update_redmine({ ...evidence, bdd: { [key]: 1 } }), false);
  }
  assert.equal(can_update_redmine({ ...evidence, screenshots: {} }), false);
  assert.equal(can_update_redmine({ ...evidence, consistency: 'UNVERIFIED' }), false);
});
