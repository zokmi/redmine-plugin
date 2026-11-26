import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createServer, connect as connectSocket } from 'node:net';
import { createServer as createHttpServer } from 'node:http';
import { createHash } from 'node:crypto';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { fileURLToPath } from 'node:url';
import { mkdir, mkdtemp, readFile, readdir, realpath, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { setTimeout as delay } from 'node:timers/promises';
import { basename, dirname, join } from 'node:path';
let sandbox, env;
function serviceEnvironment(home) {
  const inherited = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
    !/^(REDMINE_|XDG_)|^(HOME|USERPROFILE|LOCALAPPDATA|APPDATA|HOMEDRIVE|HOMEPATH|NODE_OPTIONS|NODE_PATH)$/i.test(key)));
  return { ...inherited, HOME: home, USERPROFILE: home, LOCALAPPDATA: join(home, 'Local'), APPDATA: join(home, 'Roaming'),
    XDG_CONFIG_HOME: join(home, 'config'), XDG_DATA_HOME: join(home, 'data'),
    REDMINE_URL: 'https://example.invalid', REDMINE_API_KEY: 'test-key', REDMINE_CONFIG: join(home, 'missing.toml') };
}
test.before(async () => {
  sandbox = await realpath(await mkdtemp(join(tmpdir(), 'redmine-service-')));
  env = serviceEnvironment(sandbox);
});
test.after(() => rm(sandbox, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 }));
const cli = fileURLToPath(new URL('../dist/cli.js', import.meta.url));
const token = 'a'.repeat(32);
async function freePort() { const server = createServer(); server.listen(0, '127.0.0.1'); await once(server, 'listening'); const port = server.address().port; await new Promise(r => server.close(r)); return port; }
async function ready(child) { let output = ''; for await (const chunk of child.stdout) { output += chunk; if (output.includes('\n')) return output; } throw new Error('process exited before ready: ' + output); }
function client() { return new Client({name:'runtime-test', version:'1'}); }
test('stdio works with existing configuration', {timeout: 30000}, async t => {
  const dir = await mkdtemp(join(tmpdir(), 'redmine-cli-test-'));
  t.after(() => rm(dir, {recursive:true, force:true}));
  const config = join(dir, 'config.toml');
  await writeFile(config, '[sites.north]\nurl = "https://example.invalid"\napi_key = "fixture-key"\n');
  const transport = new StdioClientTransport({ command: process.execPath, args: [cli], env: {...env, REDMINE_CONFIG:config, REDMINE_URL:'', REDMINE_API_KEY:''} });
  const c = client(); t.after(() => c.close()); await c.connect(transport);
  const result = await c.callTool({name:'list_sites', arguments:{}});
  assert.equal(result.structuredContent.configured_sites[0].url, 'https://example.invalid');
});
test('serve authenticates and accepts concurrent SDK sessions', {timeout: 30000}, async t => {
  const port = await freePort();
  const proc = spawn(process.execPath, [cli, 'serve', '--port', String(port)], {env:{...env, REDMINE_MCP_LOCAL_TOKEN:token}, stdio:['pipe','pipe','pipe']});
  t.after(() => proc.kill()); await ready(proc);
  const url = new URL(`http://127.0.0.1:${port}/mcp`);
  assert.equal((await fetch(url)).status, 401);
  const clients = [client(),client()]; t.after(() => Promise.all(clients.map(c => c.close())));
  await Promise.all(clients.map(c => c.connect(new StreamableHTTPClientTransport(url, {requestInit:{headers:{Authorization:`Bearer ${token}`}}}))));
  for (const c of clients) assert.ok((await c.listTools()).tools.some(x => x.name === 'list_sites'));
});

test('Windows cold start serves authenticated MCP while scanning 220 attachment records', {
  skip: process.platform !== 'win32', timeout: 180_000,
}, async t => {
  const native = await import('../dist/storage/windows-handles.js');
  const home = await mkdtemp(join(sandbox, 'cold-start-'));
  const root = join(home, 'cache');
  const siteKey = createHash('sha256').update('https://example.invalid').digest('hex');
  const bytes = Buffer.from('cold startup fixture');
  const hash = createHash('sha256').update(bytes).digest('hex');
  const fresh = new Date().toISOString().replace('Z', '+00:00');
  const old = new Date(Date.now() - 8 * 86400000).toISOString().replace('Z', '+00:00');
  const recordCount = 220;
  const idOf = index => index.toString(16).padStart(32, '0');
  function writeNative(parent, name, data) {
    const file = native.openFile(parent, name, 'create');
    try { native.writeHandle(file, data); } finally { native.closeHandle(file); }
  }
  const handles = [];
  function keep(handle) { handles.push(handle); return handle; }
  try {
    const parent = keep(native.openDirectory(home));
    const cache = keep(native.createPrivateDirectory(parent, 'cache'));
    const site = keep(native.createDirectory(cache, siteKey));
    const active = keep(native.createDirectory(site, 'active'));
    const records = keep(native.createDirectory(site, 'records'));
    keep(native.createDirectory(site, 'locks'));
    for (let index = 1; index <= recordCount; index++) {
      const id = idOf(index), timestamp = index === recordCount ? old : fresh;
      const item = native.createDirectory(active, id);
      try { writeNative(item, 'sample.txt', bytes); } finally { native.closeHandle(item); }
      const record = { site_key: siteKey, item_id: id, filename: 'sample.txt', created_at: timestamp,
        last_used_at: timestamp, state: 'active', size: bytes.length, sha256: hash, archived_at: null, schema_version: 1 };
      writeNative(records, id + '.json', Buffer.from(JSON.stringify(record)));
    }
  } finally { for (const handle of handles.reverse()) native.closeHandle(handle); }

  const port = await freePort();
  const proc = spawn(process.execPath, [cli, 'serve', '--port', String(port)], {
    env: { ...serviceEnvironment(home), REDMINE_MCP_STORAGE_ROOT: root, REDMINE_MCP_LOCAL_TOKEN: token },
    stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true,
  });
  const c = client();
  t.after(async () => {
    try { await c.close(); } finally {
      if (proc.exitCode === null && proc.signalCode === null) {
        const exited = once(proc, 'exit'); proc.kill(); await exited;
      }
    }
  });
  await ready(proc);
  const started = performance.now();
  const url = new URL(`http://127.0.0.1:${port}/mcp`);
  await c.connect(new StreamableHTTPClientTransport(url, {requestInit: {headers: {Authorization: `Bearer ${token}`}}}));
  const elapsed = Math.round(performance.now() - started);
  t.diagnostic(`Authenticated cold initialization: ${elapsed} ms for ${recordCount} records`);
  assert.ok(elapsed < 5000, `Cold initialization exceeded the 5 s health probe: ${elapsed} ms`);
  assert.ok((await c.listTools({}, {timeout: 5000})).tools.some(tool => tool.name === 'get_issue'));

  const sitePath = join(root, siteKey);
  const dueRecord = join(sitePath, 'records', idOf(recordCount) + '.json');
  const archive = idOf(recordCount) + '.zip';
  const deadline = performance.now() + 60_000;
  const rootHandle = native.openDirectory(root);
  try {
    // Wait for the real sweep to release its lock before inspecting the record it replaces.
    // Looking for the ZIP first ensures this observer cannot suppress the startup sweep.
    for (;;) {
      const names = await readdir(join(sitePath, 'archives')).catch(error => {
        if (error.code === 'ENOENT') return [];
        throw error;
      });
      if (names.includes(archive)) {
        const idle = native.tryLock(rootHandle, '.sweep.lock');
        if (idle) { native.closeHandle(idle); break; }
      }
      assert.ok(performance.now() < deadline, 'Startup sweep did not finish archiving the due record');
      await delay(100);
    }
  } finally { native.closeHandle(rootHandle); }
  assert.equal(JSON.parse(await readFile(dueRecord, 'utf8')).state, 'archived');
  assert.deepEqual(await readFile(join(sitePath, 'active', idOf(1), 'sample.txt')), bytes);
  assert.ok((await readFile(join(sitePath, 'archives', archive))).length > 0);
});

test('stdio and HTTP download into the existing attachment root and reuse each other’s saved bytes', {timeout: 60_000}, async t => {
  const home = await mkdtemp(join(sandbox, 'attachment-home-'));
  const runtimeEnv = serviceEnvironment(home);
  const root = process.platform === 'darwin' ? join(home, 'Library', 'Application Support', 'redmine-mcp', 'attachments') :
    join(process.platform === 'win32' ? runtimeEnv.LOCALAPPDATA : runtimeEnv.XDG_DATA_HOME, 'redmine-mcp', 'attachments');
  if (process.platform === 'win32') {
    const { provisionStorageRoot } = await import('../dist/storage-preflight.js');
    runtimeEnv.REDMINE_MCP_STORAGE_ROOT = root;
    provisionStorageRoot(root);
  }
  const bodies = [Buffer.from([0, 7, 255]), Buffer.from([255, 8, 0])];
  const uploaded = [];
  const redmine = createHttpServer(async (req, res) => {
    if (req.headers['x-redmine-api-key'] !== 'fixture-key') return res.writeHead(403).end();
    const { pathname, searchParams, origin } = new URL(req.url, 'http://127.0.0.1:' + redmine.address().port);
    if (req.method === 'POST' && pathname === '/uploads.json') {
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      uploaded.push({ filename: searchParams.get('filename'), bytes: Buffer.concat(chunks) });
      return res.writeHead(201, {'Content-Type': 'application/json'}).end(JSON.stringify({ upload: { token: 'test-upload' } }));
    }
    const metadata = /^\/attachments\/([12])\.json$/.exec(pathname);
    if (metadata) {
      return res.writeHead(200, {'Content-Type': 'application/json'}).end(JSON.stringify({ attachment: {
        id: Number(metadata[1]), filename: '檔案' + metadata[1] + '.bin', content_url: origin + '/files/' + metadata[1],
      } }));
    }
    const file = /^\/files\/([12])$/.exec(pathname);
    if (file) return res.writeHead(200, {'Content-Type': 'application/octet-stream'}).end(bodies[Number(file[1]) - 1]);
    res.writeHead(404).end();
  });
  redmine.listen(0, '127.0.0.1');
  await once(redmine, 'listening');
  t.after(() => { redmine.closeAllConnections(); return new Promise(resolve => redmine.close(resolve)); });
  const redmineUrl = 'http://127.0.0.1:' + redmine.address().port;
  const siteKey = createHash('sha256').update(redmineUrl).digest('hex');
  delete runtimeEnv.REDMINE_URL;
  delete runtimeEnv.REDMINE_API_KEY;
  runtimeEnv.REDMINE_CONFIG = join(home, 'config.toml');
  await writeFile(runtimeEnv.REDMINE_CONFIG, '[sites.one]\nurl = "' + redmineUrl + '"\napi_key = "fixture-key"\nallow_insecure_http = true\n');
  const legacyBytes = Buffer.from('已存在的附件');
  let legacyPath;
  if (process.platform !== 'win32') {
    const record = JSON.parse(await readFile(new URL('./fixtures/storage-v1-python.json', import.meta.url)));
    const now = new Date().toISOString().replace('Z', '+00:00');
    Object.assign(record, { site_key: siteKey, size: legacyBytes.length, sha256: createHash('sha256').update(legacyBytes).digest('hex'),
      created_at: now, last_used_at: now });
    const siteDir = join(root, siteKey);
    legacyPath = join(siteDir, 'active', record.item_id, record.filename);
    for (const dir of [dirname(legacyPath), join(siteDir, 'records'), join(siteDir, 'locks')]) {
      await mkdir(dir, { recursive: true, mode: 0o700 });
    }
    await writeFile(legacyPath, legacyBytes, {mode: 0o600});
    await writeFile(join(siteDir, 'records', record.item_id + '.json'), JSON.stringify(record), {mode: 0o600});
  }

  const stdio = client(), http = client();
  const port = await freePort();
  const proc = spawn(process.execPath, [cli, 'serve', '--port', String(port)], {
    env: {...runtimeEnv, REDMINE_MCP_LOCAL_TOKEN: token}, stdio: ['pipe', 'pipe', 'pipe'],
  });
  t.after(async () => {
    try { await Promise.all([stdio.close(), http.close()]); } finally {
      if (proc.exitCode === null && proc.signalCode === null) {
        const exited = once(proc, 'exit'); proc.kill(); await exited;
      }
    }
  });
  await ready(proc);
  await stdio.connect(new StdioClientTransport({command: process.execPath, args: [cli], env: runtimeEnv}));
  await http.connect(new StreamableHTTPClientTransport(new URL('http://127.0.0.1:' + port + '/mcp'), {
    requestInit: {headers: { Authorization: 'Bearer ' + token }},
  }));
  async function call(c, name, args) {
    const result = await c.callTool({name, arguments: args});
    assert.equal(result.isError ?? false, false, JSON.stringify(result));
    return result.structuredContent;
  }
  async function download(c, id) {
    const entry = await call(c, 'download_attachment', {attachment_id: id});
    const itemDir = dirname(entry.path);
    assert.equal(dirname(itemDir), join(root, siteKey, 'active'));
    assert.match(basename(itemDir), /^[a-f0-9]{32}$/);
    assert.equal(basename(entry.path), id + '_檔案' + id + '.bin');
    assert.deepEqual(await readFile(entry.path), bodies[id - 1]);
    const record = JSON.parse(await readFile(join(root, siteKey, 'records', basename(itemDir) + '.json')));
    assert.equal(record.state, 'active');
    assert.equal(record.sha256, createHash('sha256').update(bodies[id - 1]).digest('hex'));
    return entry.path;
  }
  async function upload(c, path, bytes) {
    assert.equal((await call(c, 'upload_attachment', {file_path: path})).token, 'test-upload');
    assert.deepEqual(uploaded.at(-1), {filename: basename(path), bytes});
  }
  if (legacyPath) await upload(stdio, legacyPath, legacyBytes);
  const stdioFile = await download(stdio, 1);
  await upload(http, stdioFile, bodies[0]);
  const httpFile = await download(http, 2);
  await upload(stdio, httpFile, bodies[1]);
});

test('private worker rejects other tokens, limits body and exits on parent pipe EOF', {timeout: 30000}, async t => {
  const worker = fileURLToPath(new URL('../dist/worker.js', import.meta.url));
  const proc = spawn(process.execPath, [worker], {env:{...env, REDMINE_MCP_WORKER_TOKEN:token, REDMINE_MAX_ATTACHMENT_MB:'1'}, stdio:['pipe','pipe','pipe']});
  t.after(() => proc.kill());
  const port = Number((await ready(proc)).trim());
  const url = `http://127.0.0.1:${port}/mcp`;
  assert.equal((await fetch(url, {headers:{Authorization:`Bearer ${'b'.repeat(32)}`}})).status, 401);
  assert.equal((await fetch(url, {method:'POST', headers:{Authorization:`Bearer ${token}`}, body:'x'.repeat(4 * 1024 * 1024 + 1)})).status, 413);
  const exited = once(proc, 'exit'); proc.stdin.end(); await exited;
});

test('unexpected worker exit returns 503 then reconnects', {skip: process.platform !== 'win32', timeout: 30000}, async t => {
  const { execFileSync } = await import('node:child_process');
  const port = await freePort();
  const proc = spawn(process.execPath, [cli, 'serve', '--port', String(port)], {env:{...env, REDMINE_MCP_LOCAL_TOKEN:token}, stdio:['pipe','pipe','pipe']});
  t.after(() => proc.kill()); await ready(proc);
  const childPid = Number(execFileSync('powershell.exe', ['-NoProfile','-Command', `(Get-CimInstance Win32_Process -Filter "ParentProcessId = ${proc.pid}").ProcessId`], {encoding:'utf8'}).trim());
  assert.ok(childPid > 0); process.kill(childPid);
  const url = new URL(`http://127.0.0.1:${port}/mcp`);
  let status;
  for (let i = 0; i < 30; i++) { status = (await fetch(url, {headers:{Authorization:`Bearer ${token}`}})).status; if (status === 503) break; await new Promise(r => setTimeout(r, 50)); }
  assert.equal(status, 503);
  let c;
  for (let i = 0; i < 60; i++) {
    c = client();
    try { await c.connect(new StreamableHTTPClientTransport(url, {requestInit:{headers:{Authorization:`Bearer ${token}`}}})); break; }
    catch { await c.close(); c = undefined; await new Promise(r => setTimeout(r, 100)); }
  }
  assert.ok(c, 'worker did not recover'); t.after(() => c.close());
  assert.ok((await c.listTools()).tools.some(x => x.name === 'list_sites'));
});

test('worker bounds sessions and evicts idle sessions without evicting active streams', {timeout: 30000}, async t => {
  const { createWorkerHttp } = await import('../dist/worker-http.js');
  const { loadSettings } = await import('../dist/config.js');
  let now = 0;
  const service = createWorkerHttp(await loadSettings(env), token, {maxSessions: 2, idleMs: 250, sweepMs: 50, now: () => now});
  let closed = false; t.after(() => { if (!closed) return service.close(); });
  const port = await service.listen();
  const url = `http://127.0.0.1:${port}/mcp`;
  const headers = {Authorization:`Bearer ${token}`, 'Content-Type':'application/json', Accept:'application/json, text/event-stream'};
  async function initialize(contentType = 'application/json') {
    return fetch(url, {method:'POST', headers:{...headers, 'Content-Type':contentType}, body:JSON.stringify({jsonrpc:'2.0', id:1, method:'initialize', params:{protocolVersion:'2025-03-26', capabilities:{}, clientInfo:{name:'test', version:'1'}}})});
  }
  const bad = await initialize('text/plain'); assert.equal(bad.status, 415); await bad.text();
  const abandoned = connectSocket(port, '127.0.0.1'); await once(abandoned, 'connect');
  abandoned.write(`POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:${port}\r\nAuthorization: Bearer ${token}\r\nContent-Type: application/json\r\nContent-Length: 1000\r\n\r\n{`);
  await new Promise(r => setTimeout(r, 50)); abandoned.destroy();
  const first = await initialize(); const id1 = first.headers.get('mcp-session-id'); await first.text(); assert.ok(id1);
  const second = await initialize(); const id2 = second.headers.get('mcp-session-id'); await second.text(); assert.ok(id2);
  await new Promise(r => setTimeout(r, 350));
  const full = await initialize(); assert.equal(full.status, 503); await full.text();
  const controller = new AbortController();
  const stream = await fetch(url, {headers:{Authorization:`Bearer ${token}`, Accept:'text/event-stream', 'mcp-session-id':id1, 'mcp-protocol-version':'2025-03-26'}, signal:controller.signal});
  assert.equal(stream.status, 200);
  const slow = connectSocket(port, '127.0.0.1'); await once(slow, 'connect');
  const body = JSON.stringify({jsonrpc:'2.0', id:3, method:'tools/list'});
  slow.write(`POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:${port}\r\nAuthorization: Bearer ${token}\r\nContent-Type: application/json\r\nAccept: application/json, text/event-stream\r\nmcp-session-id: ${id2}\r\nmcp-protocol-version: 2025-03-26\r\nContent-Length: ${Buffer.byteLength(body)}\r\nConnection: close\r\n\r\n${body.slice(0, 1)}`);
  await new Promise(r => setTimeout(r, 100));
  now += 300;
  await new Promise(r => setTimeout(r, 150));
  slow.write(body.slice(1));
  let response = ''; for await (const chunk of slow) response += chunk;
  assert.match(response, /^HTTP\/1\.1 200/);
  const active = await fetch(url, {method:'POST', headers:{...headers, 'mcp-session-id':id1, 'mcp-protocol-version':'2025-03-26'}, body:JSON.stringify({jsonrpc:'2.0', id:2, method:'tools/list'})});
  assert.equal(active.status, 200); await active.text();
  controller.abort();
  await new Promise(r => setTimeout(r, 100));
  now += 300;
  await new Promise(r => setTimeout(r, 150));
  const expired = await fetch(url, {method:'POST', headers:{...headers, 'mcp-session-id':id2}, body:'{}'});
  assert.equal(expired.status, 404); await expired.text();
  const afterDisconnect = await fetch(url, {method:'POST', headers:{...headers, 'mcp-session-id':id1}, body:'{}'});
  assert.equal(afterDisconnect.status, 404); await afterDisconnect.text();
  const replacement = await initialize(); assert.equal(replacement.status, 200); const replacementId = replacement.headers.get('mcp-session-id'); await replacement.text();
  const openStream = await fetch(url, {headers:{Authorization:`Bearer ${token}`, Accept:'text/event-stream', 'mcp-session-id':replacementId, 'mcp-protocol-version':'2025-03-26'}});
  assert.equal(openStream.status, 200);
  const reader = openStream.body.getReader();
  const streamClosed = (async () => { try { while (!(await reader.read()).done) {} } catch {} })();
  await service.close(); closed = true;
  await Promise.race([streamClosed, new Promise((_, reject) => setTimeout(() => reject(new Error('stream remained open')), 2000))]);
});

test('supervisor survives a client disconnect during request body', {timeout: 30000}, async t => {
  const port = await freePort();
  const proc = spawn(process.execPath, [cli, 'serve', '--port', String(port)], {env:{...env, REDMINE_MCP_LOCAL_TOKEN:token}, stdio:['pipe','pipe','pipe']});
  t.after(() => proc.kill()); await ready(proc);
  const socket = connectSocket(port, '127.0.0.1'); await once(socket, 'connect');
  socket.write(`POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:${port}\r\nAuthorization: Bearer ${token}\r\nContent-Length: 10000\r\nContent-Type: application/json\r\n\r\n{`);
  await new Promise(r => setTimeout(r, 100)); socket.destroy();
  await new Promise(r => setTimeout(r, 500));
  assert.equal(proc.exitCode, null);
  const c = client(); t.after(() => c.close());
  await c.connect(new StreamableHTTPClientTransport(new URL(`http://127.0.0.1:${port}/mcp`), {requestInit:{headers:{Authorization:`Bearer ${token}`}}}));
  assert.ok((await c.listTools()).tools.some(x => x.name === 'list_sites'));
});
