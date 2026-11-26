import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

function workspace(t) {
  const repo = mkdtempSync(join(tmpdir(), 'release changes '));
  t.after(() => rmSync(repo, { recursive: true, force: true }));
  return repo;
}

function write(repo, name, contents = name) {
  const path = join(repo, name);
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, contents);
}

function run(name, args, input) {
  const script = fileURLToPath(new URL(`../scripts/${name}.mjs`, import.meta.url));
  const result = spawnSync(process.execPath, [script, ...args], { input, encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout);
}

test('Git range and history preserve grouping, status, renamed destinations and merge exclusion', (t) => {
  const repo = workspace(t);
  const git = (...args) => execFileSync('git', ['-C', repo, ...args], { encoding: 'utf8' }).trim();
  git('init', '-q', '-b', 'main');
  git('config', 'user.name', 'Release Test');
  git('config', 'user.email', 'test@example.invalid');
  git('config', 'commit.gpgSign', 'false');
  write(repo, 'old.txt');
  git('add', '.'); git('commit', '-qm', 'baseline'); git('tag', 'base');
  git('switch', '-qc', 'topic');
  git('mv', 'old.txt', 'renamed file.txt');
  for (const name of ['schema.sql', 'appsettings.Prod.json', 'docs/readme.md', 'api.csproj', 'lib.cs']) write(repo, name);
  git('add', '.'); git('commit', '-qm', '#321 and #456 變更');
  git('switch', '-q', 'main');
  git('merge', '-q', '--no-ff', 'topic', '-m', '#999 merge');
  write(repo, 'misc.ts'); git('add', '.'); git('commit', '-qm', '#0000 misc');
  const result = run('collect_changes', ['--repo', repo, '--range', 'base..HEAD', '--pretty']);
  assert.deepEqual(result.issues.map(i => i.issue), ['321', '456', '未標號']);
  assert.equal(result.issue_count, 3);
  const first = result.issues[0];
  assert.match(first.commits[0], /^[0-9a-f]{8} \d{4}-\d{2}-\d{2} #321 and #456 變更$/);
  assert.deepEqual(first.sql, ['A schema.sql']);
  assert.deepEqual(first.config, ['A appsettings.Prod.json']);
  assert.deepEqual(first.doc, ['A docs/readme.md']);
  assert.deepEqual(first.build, ['A api.csproj']);
  assert.deepEqual(first.code, ['A lib.cs', 'R100 renamed file.txt']);
  const history = run('collect_changes', ['--repo', repo, '--issues', '#456,321']);
  assert.equal(history.range, null);
  assert.deepEqual(history.issues, result.issues.slice(0, 2));
  assert.deepEqual(run('collect_changes', ['--repo', repo, '--range=base..HEAD', '--issues', '456']).issues, [result.issues[1]]);
});

test('Angular routes use lazy prefixes, distinguish containers and consume JSON or file arguments', (t) => {
  const repo = workspace(t);
  write(repo, 'CRMAG/app/routes.ts', `
    import { ShellComponent } from './pages/shell.component';
    const routes = [{ path: '', component: ShellComponent },
      { path: 'reports', loadChildren: () => import('./pages/reports/reports.module') }];
  `);
  write(repo, 'CRMAG/app/pages/reports/reports-routing.module.ts', `
    import { SalesComponent } from './sales/sales.component';
    const routes = [{ path: '銷售', component: SalesComponent }, { path: 'daily', component: SalesComponent }];
  `);
  write(repo, 'node_modules/vendor/routes.ts', "import { XComponent } from './x/file'; const r = { path: 'wrong', component: XComponent };");
  const dir = 'CRMAG/app/pages/reports/sales', paths = ['/reports/daily', '/reports/銷售'];
  assert.deepEqual(run('map_features', ['--repo', repo, '--list-all']), {
    route_count: 3, index: { 'CRMAG/app/pages': ['/(預設頁)'], [dir]: paths },
  });
  const source = dir + '/parts/item.ts';
  const changes = { issues: [{ code: ['M ' + source, 'A CRMAG/app/pages/shared/a.ts'], sql: ['M db.sql'], doc: ['M ignored.md'] }] };
  const expected = { repo, routes: [{ app: 'CRMAG', dir, routes: paths, files: [source, source] }], unmapped: ['CRMAG/app/pages/shared/a.ts', 'db.sql'] };
  assert.deepEqual(run('map_features', ['--repo', repo, '--files', source.replaceAll('/', '\\'), '--files-from', '-'], JSON.stringify(changes)), expected);
  write(repo, 'changes.json', JSON.stringify(changes));
  assert.deepEqual(run('map_features', ['--files-from', join(repo, 'changes.json'), '--files', source, '--repo', repo]), expected);
});
