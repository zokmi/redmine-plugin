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
    writePrivate(file, 'secret', {exists:false});
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

test('concurrent external edit during verification aborts without losing changes', async () => {
  const {createServer} = await import('node:http'); const {spawn} = await import('node:child_process');
  const {readFile} = await import('node:fs/promises');
  const dir = await mkdtemp(join(tmpdir(), 'redmine-cli-'));
  let respond; const requested = new Promise(resolve => { respond = resolve; });
  let reply; const server = createServer((_req,res) => { reply = res; respond(); });
  await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
  try {
    const url = `http://127.0.0.1:${server.address().port}`; const file = join(dir,'config.toml');
    const original = `[sites.main]\nurl = "${url}"\napi_key = "fixture-key"\nallow_insecure_http = true\n`;
    await writeFile(file,original);
    const child = spawn(process.execPath,['dist/cli.js','config','set-site','main','--url',url,'--yes'],{env:{...process.env,REDMINE_CONFIG:file},stdio:['ignore','pipe','pipe']});
    let error=''; child.stderr.on('data', x => error += x); const finished = new Promise(resolve => child.on('exit',resolve));
    await requested; const changed = original + '# external edit\n'; await writeFile(file,changed);
    reply.writeHead(200,{'content-type':'application/json'}); reply.end('{"user":{"login":"fixture"}}');
    assert.notEqual(await finished,0,error); assert.equal(await readFile(file,'utf8'),changed);
  } finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); await rm(dir,{recursive:true,force:true}); }
});

test('clearing a field retains its inline comment', async () => {
  const {readFile} = await import('node:fs/promises'); const dir = await mkdtemp(join(tmpdir(), 'redmine-cli-'));
  try {
    const file = join(dir,'config.toml'); await writeFile(file,'[sites.main]\nurl = "https://example.test"\napi_key = "fixture-key"\ndescription = "something # value" # keep this note\n');
    const result = spawnSync(process.execPath,['dist/cli.js','config','set-site','main','--clear-description','--yes'],{env:{...process.env,REDMINE_CONFIG:file},encoding:'utf8'});
    assert.equal(result.status,0,result.stderr); const text = await readFile(file,'utf8'); assert.match(text,/^\s*# keep this note$/m); assert.doesNotMatch(text,/description/);
  } finally { await rm(dir,{recursive:true,force:true}); }
});

test('removing a site retains its standalone and inline comments', async () => {
  const {readFile} = await import('node:fs/promises'); const dir = await mkdtemp(join(tmpdir(), 'redmine-cli-'));
  try {
    const file = join(dir,'config.toml'); await writeFile(file,'[sites.main] # site note\n# internal note\nurl = "https://example.test" # url note\napi_key = "fixture-key"\n[sites.other]\nurl = "https://other.test"\napi_key = "other-key"\n');
    const result = spawnSync(process.execPath,['dist/cli.js','config','remove-site','main','--yes'],{env:{...process.env,REDMINE_CONFIG:file},encoding:'utf8'});
    assert.equal(result.status,0,result.stderr); const text = await readFile(file,'utf8');
    assert.match(text,/# site note/); assert.match(text,/# internal note/); assert.match(text,/# url note/); assert.doesNotMatch(text,/\[sites.main\]/);
  } finally { await rm(dir,{recursive:true,force:true}); }
});

test('snapshot rejects replacement even when external contents are identical', {skip:process.platform !== 'win32'}, async () => {
  const {captureSnapshot,writePrivate} = await import('../dist/config-write.js'); const dir = await mkdtemp(join(tmpdir(), 'redmine-cli-'));
  try {
    const {rename,readFile} = await import('node:fs/promises'); const file=join(dir,'config.toml'); const replacement=join(dir,'replacement');
    await writeFile(file,'same'); const snapshot=captureSnapshot(file); await writeFile(replacement,'same'); await rename(replacement,file);
    assert.throws(() => writePrivate(file,'changed',snapshot), /其他程序修改/); assert.equal(await readFile(file,'utf8'),'same');
  } finally { await rm(dir,{recursive:true,force:true}); }
});

test('snapshot of missing config never replaces a newly created config', {skip:process.platform !== 'win32'}, async () => {
  const {captureSnapshot,writePrivate} = await import('../dist/config-write.js'); const dir = await mkdtemp(join(tmpdir(), 'redmine-cli-'));
  try {
    const {readFile} = await import('node:fs/promises'); const file=join(dir,'config.toml'); const snapshot=captureSnapshot(file);
    await writeFile(file,'external'); assert.throws(() => writePrivate(file,'changed',snapshot)); assert.equal(await readFile(file,'utf8'),'external');
  } finally { await rm(dir,{recursive:true,force:true}); }
});

test('removing a site retains comments after multiline values', async () => {
  const {readFile} = await import('node:fs/promises'); const dir = await mkdtemp(join(tmpdir(), 'redmine-cli-'));
  try {
    const file=join(dir,'config.toml'); await writeFile(file,'[sites.main]\nurl = "https://example.test"\napi_key = "fixture-key"\ndescription = """first\n# inside value\nlast""" # keep multiline note\n[sites.other]\nurl = "https://other.test"\napi_key = "other-key"\n');
    const result=spawnSync(process.execPath,['dist/cli.js','config','remove-site','main','--yes'],{env:{...process.env,REDMINE_CONFIG:file},encoding:'utf8'});
    assert.equal(result.status,0,result.stderr); const text=await readFile(file,'utf8'); assert.match(text,/# keep multiline note/); assert.doesNotMatch(text,/# inside value/);
  } finally { await rm(dir,{recursive:true,force:true}); }
});

test('config writer refuses a cooperating writer holding the same lock', {skip:process.platform !== 'win32'}, async () => {
  const {captureSnapshot,writePrivate} = await import('../dist/config-write.js');
  const {openDirectory,tryLock,closeHandle} = await import('../dist/storage/windows-handles.js');
  const {createHash} = await import('node:crypto'); const {readFile} = await import('node:fs/promises');
  const dir=await mkdtemp(join(tmpdir(),'redmine-cli-')); let parent, lock;
  try {
    const file=join(dir,'config.toml'); await writeFile(file,'old'); const snapshot=captureSnapshot(file); parent=openDirectory(dir);
    const name=`.redmine-config-${createHash('sha256').update('config.toml').digest('hex')}.lock`; lock=tryLock(parent,name); assert.ok(lock);
    assert.throws(() => writePrivate(file,'new',snapshot),/另一個程序/); assert.equal(await readFile(file,'utf8'),'old');
  } finally { if(lock) closeHandle(lock); if(parent) closeHandle(parent); await rm(dir,{recursive:true,force:true}); }
});
