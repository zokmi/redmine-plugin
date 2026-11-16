import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';

test('config show reads legacy and multisite files without exposing API keys', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'redmine-cli-'));
  try {
    const file = join(dir, 'config.toml');
    for (const source of ['url = "https://example.test"\napi_key = "a-secret-key"\n', '[sites.main]\nurl = "https://example.test"\napi_key = "a-secret-key"\n']) {
      await writeFile(file, source);
      const result = spawnSync(process.execPath, ['dist/cli.js', 'config', 'show'], { env: { ...process.env, REDMINE_CONFIG: file }, encoding: 'utf8' });
      assert.equal(result.status, 0, result.stderr);
      assert.match(result.stdout, /https:\/\/example\.test/);
      assert.doesNotMatch(result.stdout + result.stderr, /a-secret-key/);
    }
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('config set-global and remove-site preserve unrelated settings and comments', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'redmine-cli-'));
  try {
    const file = join(dir, 'config.toml');
    const source = '# comment\nmax_attachment_mb = 10 # limit\n[sites.main]\nurl = "https://example.test"\napi_key = "secret-value"\n\n# next\n[sites.other]\nurl = "https://other.test"\napi_key = "other-key"\n';
    await writeFile(file, source);
    const run = (...args) => spawnSync(process.execPath, ['dist/cli.js', 'config', ...args], {env: {...process.env, REDMINE_CONFIG: file}, encoding:'utf8'});
    const first = run('set-global', '--max-attachment-mb', '20', '--yes');
    assert.equal(first.status, 0, first.stderr);
    let text = await (await import('node:fs/promises')).readFile(file, 'utf8');
    assert.match(text, /max_attachment_mb = 20 # limit/);
    assert.match(text, /# comment/);
    const second = run('remove-site', 'main', '--yes');
    assert.equal(second.status, 0, second.stderr);
    text = await (await import('node:fs/promises')).readFile(file, 'utf8');
    assert.doesNotMatch(text, /\[sites.main\]/);
    assert.match(text, /# next\n\[sites.other\]/);
  } finally { await rm(dir, { recursive: true, force: true }); }
});


test('config set-site updates description without altering other fields', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'redmine-cli-'));
  try {
    const file = join(dir, 'config.toml');
    await writeFile(file, '[sites.main]\nurl = "https://example.test"\napi_key = "secret-value"\ndescription = "old" # comment\n');
    const result = spawnSync(process.execPath, ['dist/cli.js', 'config', 'set-site', 'main', '--description', 'new', '--yes'], {env:{...process.env, REDMINE_CONFIG:file},encoding:'utf8'});
    assert.equal(result.status, 0, result.stderr);
    const text = await (await import('node:fs/promises')).readFile(file, 'utf8');
    assert.match(text, /description = "new" # comment/);
    assert.match(text, /api_key = "secret-value"/);
  } finally { await rm(dir,{recursive:true,force:true}); }
});

test('set-site verifies changed URL against Redmine before writing', async () => {
  const { createServer } = await import('node:http');
  const { spawn } = await import('node:child_process');
  const dir = await mkdtemp(join(tmpdir(), 'redmine-cli-'));
  const server = createServer((req, res) => { assert.equal(req.headers['x-redmine-api-key'], 'fixture-key'); res.writeHead(200, {'content-type':'application/json'}); res.end('{"user":{"login":"fixture"}}'); });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    const url = `http://127.0.0.1:${server.address().port}`;
    const file = join(dir, 'config.toml');
    await writeFile(file, `[sites.main]\nurl = "${url}"\napi_key = "fixture-key"\nallow_insecure_http = true\n`);
    const child = spawn(process.execPath, ['dist/cli.js','config','set-site','main','--url',url,'--yes'], {env:{...process.env,REDMINE_CONFIG:file},stdio:['ignore','pipe','pipe']});
    let output = ''; child.stderr.on('data', chunk => output += chunk);
    const code = await new Promise(resolve => child.on('exit', resolve));
    assert.equal(code, 0, output);
  } finally { await new Promise(resolve => server.close(resolve)); await rm(dir,{recursive:true,force:true}); }
});

test('set-global preserves unrelated multiline TOML verbatim', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'redmine-cli-'));
  try {
    const file = join(dir, 'config.toml');
    const source = 'note = """first\n[sites.fake]\nmax_attachment_mb = 999\nlast"""\nmax_attachment_mb = 10\n[sites.main]\nurl = "https://example.test"\napi_key = "secret-value"\n';
    await writeFile(file, source);
    const result = spawnSync(process.execPath, ['dist/cli.js','config','set-global','--max-attachment-mb','20','--yes'], {env:{...process.env,REDMINE_CONFIG:file},encoding:'utf8'});
    assert.equal(result.status, 0, result.stderr);
    const text = await (await import('node:fs/promises')).readFile(file,'utf8');
    assert.equal(text, source.replace('max_attachment_mb = 10','max_attachment_mb = 20'));
  } finally { await rm(dir,{recursive:true,force:true}); }
});

test('config modification asks for confirmation when --yes is omitted', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'redmine-cli-'));
  try {
    const file = join(dir, 'config.toml');
    await writeFile(file, '[sites.main]\nurl = "https://example.test"\napi_key = "secret-value"\n');
    const result = spawnSync(process.execPath, ['dist/cli.js','config','set-site','main','--description','new'], {env:{...process.env,REDMINE_CONFIG:file},input:'y\n',encoding:'utf8'});
    assert.equal(result.status, 0, result.stderr);
  } finally { await rm(dir,{recursive:true,force:true}); }
});

test('set-site key refuses secret input without a terminal', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'redmine-cli-'));
  try {
    const file = join(dir, 'config.toml'); const source = '[sites.main]\nurl = "https://example.test"\napi_key = "secret-value"\n';
    await writeFile(file, source);
    const result = spawnSync(process.execPath, ['dist/cli.js','config','set-site','main','--key','--yes'], {env:{...process.env,REDMINE_CONFIG:file},input:'new-secret\n',encoding:'utf8'});
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /終端/);
    assert.doesNotMatch(result.stdout + result.stderr, /new-secret|secret-value/);
    assert.equal(await (await import('node:fs/promises')).readFile(file,'utf8'), source);
  } finally { await rm(dir,{recursive:true,force:true}); }
});

test('setup refuses unmasked key input and leaves missing config untouched', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'redmine-cli-'));
  try {
    const file = join(dir, 'config.toml');
    const result = spawnSync(process.execPath, ['dist/cli.js','setup'], {env:{...process.env, REDMINE_CONFIG:file},input:'main\nhttps://example.test\nsecret\n',encoding:'utf8'});
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /終端/);
    assert.doesNotMatch(result.stdout + result.stderr, /secret/);
    await assert.rejects((await import('node:fs/promises')).stat(file), {code:'ENOENT'});
  } finally { await rm(dir,{recursive:true,force:true}); }
});


test('private config creation creates missing parent without inheriting an open ACL', {skip:process.platform !== 'win32'}, async () => {
  const { writePrivate } = await import('../dist/config-write.js');
  const dir = await mkdtemp(join(tmpdir(), 'redmine-cli-'));
  try {
    const file = join(dir, 'new-private', 'config.toml');
    writePrivate(file, 'secret', false);
    assert.equal(await (await import('node:fs/promises')).readFile(file,'utf8'), 'secret');
  } finally { await rm(dir,{recursive:true,force:true}); }
});

test('legacy conversion preserves unrelated multiline settings and moves site fields', async () => {
  const { convertLegacy } = await import('../dist/config-cli.js');
  const source = '# keep\nnote = """line\nurl = fake\n"""\nurl = "https://example.test" # endpoint\napi_key = "secret-value"\nmax_attachment_mb = 10\n';
  const actual = convertLegacy(source);
  assert.match(actual, /note = """line\nurl = fake\n"""/);
  assert.match(actual, /max_attachment_mb = 10/);
  assert.match(actual, /\[sites.default\]\nurl = "https:\/\/example.test" # endpoint\napi_key = "secret-value"/);
});

test('set-global accepts repeated --clear for the supported global key', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'redmine-cli-'));
  try {
    const file = join(dir, 'config.toml');
    await writeFile(file, 'max_attachment_mb = 10\n[sites.main]\nurl = "https://example.test"\napi_key = "secret-value"\n');
    const result = spawnSync(process.execPath, ['dist/cli.js','config','set-global','--clear','max_attachment_mb','--clear','max_attachment_mb','--yes'], {env:{...process.env,REDMINE_CONFIG:file},encoding:'utf8'});
    assert.equal(result.status, 0, result.stderr);
    assert.doesNotMatch(await (await import('node:fs/promises')).readFile(file,'utf8'), /max_attachment_mb/);
  } finally { await rm(dir,{recursive:true,force:true}); }
});

test('secret prompt masks characters and restores raw mode', async () => {
  const { readSecret } = await import('../dist/config-cli.js');
  const { PassThrough } = await import('node:stream');
  const input = new PassThrough(); input.isTTY = true; input.isRaw = false; input.setRawMode = value => { input.isRaw = value; return input; };
  const output = new PassThrough(); output.isTTY = true;
  let displayed = ''; output.on('data', chunk => displayed += chunk);
  const pending = readSecret(input, output); input.write('private-key\r');
  assert.equal(await pending, 'private-key');
  assert.equal(input.isRaw, false);
  assert.doesNotMatch(displayed, /private-key/);
});
