import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createServer } from 'node:net';
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
