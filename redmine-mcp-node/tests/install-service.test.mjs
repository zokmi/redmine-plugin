import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { openSync, closeSync } from 'node:fs';
import { mkdtemp, readFile, writeFile, mkdir, cp, copyFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { createServer } from 'node:net';
import { once } from 'node:events';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
const sourceScript = resolve('../scripts/install-local-service.ps1');
const sourceNode = resolve('.');
test('Node opt-in preserves verified context across restart and rollback', {skip:process.platform !== 'win32', timeout:480000}, async t => {
  const root = await mkdtemp(join(tmpdir(), 'redmine-install-'));
  let pid;
  t.after(async () => { if (pid) { try { process.kill(pid); } catch {} } await rm(root, {recursive:true, force:true, maxRetries:10, retryDelay:200}); });
  await mkdir(join(root, 'scripts'));
  const nodeFixture = join(root, 'redmine-mcp-node');
  await mkdir(nodeFixture);
  for (const name of ['package.json', 'package-lock.json', 'tsconfig.json', 'src']) {
    await cp(join(sourceNode, name), join(nodeFixture, name), {recursive:true});
  }
  const script = join(root, 'scripts', 'install-local-service.ps1');
  for (const name of ['validate-service-config.mjs', 'validate-service-config.py']) {
    await copyFile(resolve('../scripts', name), join(root, 'scripts', name));
  }
  const original = await readFile(sourceScript, 'utf8');
  let fixtureScript = original.replace("[Environment]::GetEnvironmentVariable($tokenName, 'User')", '$env:INSTALL_FIXTURE_USER_TOKEN');
  assert.notEqual(fixtureScript, original);
  const beforeInjection = fixtureScript;
  fixtureScript = fixtureScript.replace('    $started = Start-ServiceDeployment $item', "    if ($env:INSTALL_FIXTURE_FAIL) { throw 'fixture startup failure' }\n    $started = Start-ServiceDeployment $item");
  assert.notEqual(fixtureScript, beforeInjection); await writeFile(script, fixtureScript);
  const beforeResolverInjection = fixtureScript;
  fixtureScript = fixtureScript.replace('function Resolve-ServiceConfig($item, [bool]$legacyCombined = $false) {', "function Resolve-ServiceConfig($item, [bool]$legacyCombined = $false) {\n    if ($env:INSTALL_FIXTURE_DIFFERENT_SOURCE -and $item.deployment -ne $env:INSTALL_FIXTURE_OLD_DEPLOYMENT) { return $env:INSTALL_FIXTURE_DIFFERENT_SOURCE }");
  assert.notEqual(fixtureScript, beforeResolverInjection);
  fixtureScript = fixtureScript.replace("$ErrorActionPreference = 'Stop'", `$ErrorActionPreference = 'Stop'
function Register-ScheduledTask($TaskName, $Xml, [switch]$Force) {
    @{ name = $TaskName; xml = $Xml } | ConvertTo-Json -Compress | Set-Content -LiteralPath $env:INSTALL_FIXTURE_TASK_SPEC -Encoding UTF8
}
# Reuse the already installed dependency tree so the lifecycle fixture can run offline.
function npm.cmd {
    if ($args[0] -eq 'ci') {
        $modules = Join-Path (Get-Location) 'node_modules'
        try {
            $null = New-Item -ItemType Junction -Path $modules -Target $env:INSTALL_FIXTURE_NODE_MODULES
        } catch {
            $null = & robocopy.exe $env:INSTALL_FIXTURE_NODE_MODULES $modules /E /MT:16
            if ($LASTEXITCODE -ge 8) { throw 'Preparing the offline dependency fixture failed.' }
        }
        $script:LASTEXITCODE = 0
    } elseif ($args[0] -eq 'prune') {
        $script:LASTEXITCODE = 0
    } else {
        & (Get-Command npm.cmd -CommandType Application | Select-Object -First 1).Source @args
    }
}`);
  assert.match(fixtureScript, /function Register-ScheduledTask/);
  await writeFile(script, fixtureScript);
  const s = createServer().listen(0, '127.0.0.1'); await once(s, 'listening'); const port = s.address().port; await new Promise(r => s.close(r));
  const config = join(root, '使用者設定.toml'); const fixture = 'download_dir = "unused-legacy-setting"\n[sites.test]\nurl = "https://example.invalid"\napi_key = "fixture-key"\n'; await writeFile(config, fixture);
  const storage = join(root, '附件');
  const pwsh = execFileSync('where.exe', ['pwsh.exe'], {encoding:'utf8'}).trim().split(/\r?\n/)[0];
  const env = {...process.env};
  for (const name of Object.keys(env)) if (/^redmine_/i.test(name) || /^path$/i.test(name) || /^(pythonpath|pythonhome|node_options|node_path|node_extra_ca_certs|ssl_cert_file|ssl_cert_dir|requests_ca_bundle|curl_ca_bundle|https_proxy|http_proxy|all_proxy|no_proxy)$/i.test(name)) delete env[name];
  const specPath = join(root, 'task.json');
  const systemPath = [join(process.env.SystemRoot, 'System32'), process.env.SystemRoot].join(';');
  Object.assign(env, {PATH:[dirname(process.execPath), systemPath].join(';'), LOCALAPPDATA:root, REDMINE_MCP_STORAGE_ROOT:storage, REDMINE_MCP_LOCAL_TOKEN:'a'.repeat(32), INSTALL_FIXTURE_USER_TOKEN:'a'.repeat(32), INSTALL_FIXTURE_TASK_SPEC:specPath, INSTALL_FIXTURE_NODE_MODULES:join(sourceNode, 'node_modules'), REDMINE_CONFIG:config});
  const invoke = async (args, expected = 0, extra = {}, scriptPath = script) => {
    const log = join(root, 'installer.log');
    const fd = openSync(log, 'w');
    const childEnv = {...env, ...extra};
    for (const name of Object.keys(childEnv)) if (childEnv[name] === undefined) delete childEnv[name];
    const portArgument = args.includes('-Port') || args.includes('-Recover') ? [] : ['-Port', String(port)];
    const shell = scriptPath === script ? pwsh : join(process.env.SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
    const child = spawn(shell, ['-NoProfile','-File',scriptPath,...args,...portArgument], {env:childEnv, stdio:['ignore',fd,fd]});
    const [code] = await once(child, 'exit'); closeSync(fd);
    const output = await readFile(log, 'utf8');
    assert.equal(code, expected, output);
    return output;
  };
  const initialize = () => fetch(`http://127.0.0.1:${port}/mcp`, {method:'POST', headers:{Authorization:`Bearer ${'a'.repeat(32)}`, Accept:'application/json, text/event-stream', 'Content-Type':'application/json'}, body:JSON.stringify({jsonrpc:'2.0', id:1, method:'initialize', params:{protocolVersion:'2025-03-26', capabilities:{}, clientInfo:{name:'fixture',version:'1'}}})});
  await invoke(['-UseNode']);
  const manifest = join(root, 'redmine-mcp', 'service', 'active.json');
  let state = JSON.parse((await readFile(manifest, 'utf8')).replace(/^\uFEFF/, ''));
  pid = state.pid; assert.equal(state.engine, 'node');
  assert.doesNotMatch(JSON.stringify(state), /fixture-key|aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/);
  const attachment = join(storage, 'fixture.bin'); await writeFile(attachment, 'attachment fixture');
  assert.equal((await fetch(`http://127.0.0.1:${port}/mcp`)).status, 401);
  const {envHash: adoptedHash, ...combinedState} = state;
  await writeFile(manifest, JSON.stringify(combinedState));
  await writeFile(config, fixture + '# changed\n');
  await invoke(['-Restart'], 1);
  assert.match(await readFile(join(root, 'installer.log'), 'utf8'), /combined context fingerprint cannot be verified/i);
  await writeFile(config, fixture);
  await invoke(['-Restart']);
  state = JSON.parse((await readFile(manifest, 'utf8')).replace(/^\uFEFF/, ''));
  assert.ok(state.envHash); pid = state.pid;
  let reached = false;
  const hostile = createServer(socket => { reached = true; socket.end('HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n'); }).listen(0, '127.0.0.1');
  await once(hostile, 'listening');
  await writeFile(manifest, JSON.stringify({...state, port:hostile.address().port}));
  await invoke(['-Restart'], 1); assert.equal(reached, false);
  await new Promise(r => hostile.close(r));
  const {contextSalt, contextHash, envHash, port: savedPort, ...legacy} = state;
  await writeFile(manifest, JSON.stringify(legacy));
  await invoke(['-UseNode', '-Port', String(port)], 1);
  await invoke(['-Restart', '-Port', String(port)], 1);
  assert.equal((await fetch(`http://127.0.0.1:${port}/mcp`)).status, 401);
  await writeFile(manifest, JSON.stringify(state));
  await invoke(['-Restart'], 1, {INSTALL_FIXTURE_USER_TOKEN:'b'.repeat(32)});
  await invoke(['-Restart'], 1, {PYTHONPATH:join(root, 'different-python-path')});
  await writeFile(config, '[sites.broken\n');
  await invoke(['-Restart'], 1);
  assert.equal((await fetch(`http://127.0.0.1:${port}/mcp`)).status, 401);
  const updatedFixture = fixture.replace('sites.test', 'sites.changed');
  await writeFile(config, updatedFixture);
  await invoke(['-UseNode'], 1);
  assert.match(await readFile(join(root, 'installer.log'), 'utf8'), /restart the existing deployment before upgrading/i);
  await invoke(['-Restart']);
  state = JSON.parse((await readFile(manifest, 'utf8')).replace(/^\uFEFF/, ''));
  pid = state.pid;
  const client = new Client({name:'installer-fixture', version:'1'});
  await client.connect(new StreamableHTTPClientTransport(new URL(`http://127.0.0.1:${port}/mcp`), {requestInit:{headers:{Authorization:`Bearer ${'a'.repeat(32)}`}}}));
  const sites = await client.callTool({name:'list_sites', arguments:{}});
  assert.equal(sites.structuredContent.configured_sites[0].name, 'changed');
  await client.close();
  const heldLock = openSync(join(root, 'redmine-mcp', 'service', 'install.lock'), 'r+');
  try { await invoke(['-Restart'], 1); } finally { closeSync(heldLock); }
  assert.equal((await fetch(`http://127.0.0.1:${port}/mcp`)).status, 401);
  await invoke(['-Restart'], 0, {REDMINE_MCP_LOCAL_TOKEN:'b'.repeat(32)});
  state = JSON.parse((await readFile(manifest, 'utf8')).replace(/^\uFEFF/, ''));
  assert.equal(state.engine, 'node'); assert.notEqual(state.pid, pid); pid = state.pid;
  await invoke(['-UseNode'], 1, {INSTALL_FIXTURE_OLD_DEPLOYMENT:state.deployment, INSTALL_FIXTURE_DIFFERENT_SOURCE:join(root, 'other.toml')});
  assert.match(await readFile(join(root, 'installer.log'), 'utf8'), /configuration source differs/i);
  assert.equal((await fetch(`http://127.0.0.1:${port}/mcp`)).status, 401);
  assert.equal(JSON.parse((await readFile(manifest, 'utf8')).replace(/^\uFEFF/, '')).pid, pid);
  const blocker = createServer().listen(0, '127.0.0.1'); await once(blocker, 'listening');
  const occupied = blocker.address().port; t.after(() => blocker.close());
  await invoke(['-UseNode', '-Port', String(occupied)], 1);
  state = JSON.parse((await readFile(manifest, 'utf8')).replace(/^\uFEFF/, ''));
  assert.equal(state.engine, 'node'); assert.equal(state.port, port); assert.notEqual(state.pid, pid); pid = state.pid;
  assert.equal(await readFile(config, 'utf8'), updatedFixture); assert.equal(await readFile(attachment, 'utf8'), 'attachment fixture');
  const initialized = await initialize(); assert.equal(initialized.status, 200); assert.match(await initialized.text(), /serverInfo/);
  assert.equal((await fetch(`http://127.0.0.1:${port}/mcp`)).status, 401);
  await writeFile(manifest, JSON.stringify({...state, startedAt:'1'}));
  await invoke(['-Restart'], 1);
  assert.equal((await fetch(`http://127.0.0.1:${port}/mcp`)).status, 401);
  await writeFile(manifest, JSON.stringify(state));
  await t.test('recovers a dead supervisor without restaging or trusting a different listener', async () => {
    const {recoveryPaths, ...beforeMigration} = state;
    await writeFile(manifest, JSON.stringify(beforeMigration));
    assert.match(await invoke(['-Recover'], 1), /run -Restart once/i);
    await writeFile(manifest, JSON.stringify(state));
    await invoke(['-Recover']);
    assert.equal(JSON.parse((await readFile(manifest, 'utf8')).replace(/^\uFEFF/, '')).pid, pid);

    const spec = JSON.parse((await readFile(specPath, 'utf8')).replace(/^\uFEFF/, ''));
    assert.match(spec.name, /^Redmine MCP Recovery \(S-1-/);
    assert.match(spec.xml, /<Interval>PT1M<\/Interval>/);
    assert.match(spec.xml, /<LogonTrigger>/);
    assert.match(spec.xml, /<LogonType>InteractiveToken<\/LogonType>/);
    assert.match(spec.xml, /<MultipleInstancesPolicy>IgnoreNew<\/MultipleInstancesPolicy>/);
    assert.match(spec.xml, /-WindowStyle Hidden.* -Recover<\/Arguments>/);
    assert.doesNotMatch(JSON.stringify(spec), /fixture-key|aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/);
    const scheduledScript = join(dirname(manifest), 'recovery', 'install-local-service.ps1');
    const scheduledEnv = {PATH:systemPath, REDMINE_CONFIG:undefined, REDMINE_MCP_STORAGE_ROOT:undefined, REDMINE_MCP_LOCAL_TOKEN:'b'.repeat(32), INSTALL_FIXTURE_NODE_MODULES:undefined};

    process.kill(pid);
    const occupiedPort = createServer();
    t.after(() => occupiedPort.close());
    let reached = false;
    occupiedPort.on('connection', socket => { reached = true; socket.destroy(); });
    for (let attempt = 0; ; ++attempt) {
      try {
        occupiedPort.listen(port, '127.0.0.1');
        await once(occupiedPort, 'listening');
        break;
      } catch (error) {
        if (error.code !== 'EADDRINUSE' || attempt === 49) throw error;
        await new Promise(resolve => setTimeout(resolve, 100));
      }
    }
    assert.match(await invoke(['-Recover'], 1, scheduledEnv, scheduledScript), /port.*occupied/i);
    assert.equal(reached, false);
    await new Promise(resolve => occupiedPort.close(resolve));

    await writeFile(manifest, JSON.stringify({...state, pid:process.pid, startedAt:'1'}));
    assert.match(await invoke(['-Recover'], 1, scheduledEnv, scheduledScript), /PID identity/i);
    await writeFile(manifest, JSON.stringify(state));
    await invoke(['-Recover'], 1, {...scheduledEnv, INSTALL_FIXTURE_USER_TOKEN:undefined}, scheduledScript);
    await invoke(['-Recover'], 1, {...scheduledEnv, PYTHONPATH:join(root, 'different-python-path')}, scheduledScript);
    await writeFile(config, updatedFixture + '# unverified recovery configuration\n');
    await invoke(['-Recover'], 1, scheduledEnv, scheduledScript);
    await writeFile(config, updatedFixture);
    await invoke(['-Recover'], 0, scheduledEnv, scheduledScript);
    const recovered = JSON.parse((await readFile(manifest, 'utf8')).replace(/^\uFEFF/, ''));
    assert.notEqual(recovered.pid, pid);
    pid = recovered.pid;
    assert.equal(recovered.deployment, state.deployment);
    assert.equal(recovered.port, port);
    assert.equal(recovered.engine, 'node');
    assert.doesNotMatch(JSON.stringify(recovered), /fixture-key|aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/);
    assert.equal((await initialize()).status, 200);
    state = recovered;
  });
  const laterFixture = updatedFixture.replace('sites.changed', 'sites.later');
  await writeFile(config, laterFixture);
  await invoke(['-Restart'], 1, {INSTALL_FIXTURE_FAIL:'1'});
  assert.match(await readFile(join(root, 'installer.log'), 'utf8'), /previous service was not restored/i);
  assert.equal(await readFile(config, 'utf8'), laterFixture);
  await assert.rejects(fetch(`http://127.0.0.1:${port}/mcp`));
});
