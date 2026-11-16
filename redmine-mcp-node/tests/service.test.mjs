import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createServer, connect as connectSocket } from 'node:net';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { fileURLToPath } from 'node:url';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
const env = { ...process.env, REDMINE_URL: 'https://example.invalid', REDMINE_API_KEY: 'test-key', REDMINE_CONFIG: 'missing-test-config.toml' };
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
