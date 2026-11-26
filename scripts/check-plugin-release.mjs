import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const root = new URL('../', import.meta.url);
const readJson = async path => JSON.parse(await readFile(new URL(path, root), 'utf8'));
const tag = process.argv[2] ?? '';
assert.match(tag, /^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/, 'Expected vMAJOR.MINOR.PATCH');
const version = tag.slice(1);

for (const path of ['plugin.json', '.claude-plugin/plugin.json', '.codex-plugin/plugin.json']) {
  const manifest = await readJson(path);
  assert.equal(manifest.version, version, path);
  if (manifest.mcpServers) {
    const config = await readJson(manifest.mcpServers);
    assert.equal(config.mcpServers?.redmine?.url, 'http://127.0.0.1:18765/mcp', path);
  }
}
const pkg = await readJson('redmine-mcp-node/package.json');
const lock = await readJson('redmine-mcp-node/package-lock.json');
assert.equal(pkg.version, version, 'Node package');
assert.equal(lock.version, version, 'npm lock');
for (const key of ['name', 'version', 'engines', 'dependencies', 'devDependencies']) {
  assert.deepEqual(lock.packages[''][key], pkg[key], `npm lock root: ${key}`);
}
for (const skill of ['redmine-issue-writing', 'issue-code-consistency-check', 'release-change-items', 'redmine-setup', 'redmine-doctor']) {
  await readFile(new URL(`skills/${skill}/SKILL.md`, root));
}
for (const file of [
  'scripts/install-local-service.ps1', 'scripts/validate-service-config.mjs',
  'scripts/validate-service-config.py', 'scripts/doctor.ps1',
  'skills/issue-code-consistency-check/scripts/collect_issue_changes.mjs',
  'skills/issue-code-consistency-check/scripts/consistency_gate.mjs',
  'skills/release-change-items/scripts/collect_changes.mjs',
  'skills/release-change-items/scripts/map_features.mjs',
]) {
  await readFile(new URL(file, root));
}
console.log(`Release ${tag}: Node package, plugin versions and bundled resources verified`);
