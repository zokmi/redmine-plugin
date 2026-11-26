import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { once } from 'node:events';
import { createServer } from 'node:http';
import { resolve } from 'node:path';
import test from 'node:test';
import { promisify } from 'node:util';

const run = promisify(execFile);
for (const shell of ['powershell.exe', 'pwsh.exe']) {
  test(`${shell} retries an authenticated startup timeout and rejects invalid responses`, {skip: process.platform !== 'win32'}, async t => {
    const requests = new Map();
    const server = createServer(async (req, res) => {
      const scenario = req.headers.authorization?.replace(/^Bearer /, '');
      const count = (requests.get(scenario) ?? 0) + 1;
      requests.set(scenario, count);
      let body = '';
      for await (const chunk of req) body += chunk;
      if (req.url !== '/mcp' || req.method !== 'POST' || JSON.parse(body).method !== 'initialize') {
        res.writeHead(400).end();
        return;
      }
      if ((scenario === 'delayed-once' && count === 1) || scenario === 'doctor-timeout') {
        // Hold the reply so each PowerShell version emits its native timeout exception.
        return;
      }
      const status = { unauthorized: 401, forbidden: 403, unavailable: 503 }[scenario] ?? 200;
      const result = scenario === 'incomplete' ? {serverInfo: {}, protocolVersion: '2025-03-26'} :
        {serverInfo: {name: 'redmine', version: 'fixture'}, protocolVersion: '2025-03-26', capabilities: {tools: {}}};
      res.writeHead(status, {'content-type': 'application/json'});
      res.end(scenario === 'malformed' ? '{' : JSON.stringify({jsonrpc: '2.0', id: 1, result}));
    });
    server.listen(0, '127.0.0.1');
    await once(server, 'listening');
    t.after(() => { server.closeAllConnections(); return new Promise(done => server.close(done)); });
    await run(shell, ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', resolve('tests/fixtures/startup-health.ps1'),
      resolve('../scripts/install-local-service.ps1'), String(server.address().port), String(process.pid)], {windowsHide: true});
    assert.equal(requests.get('delayed-once'), 2);
    assert.equal(requests.has('wrong-owner'), false);
    assert.equal(requests.has('expired'), false);
  });
}
