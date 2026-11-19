import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { openSync, closeSync } from 'node:fs';
import { access, mkdtemp, readFile, writeFile, mkdir, cp, copyFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { createServer } from 'node:net';
import { once } from 'node:events';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
const sourceScript = resolve('../scripts/install-local-service.ps1');
const sourceNode = resolve('.');
const environmentFixture = resolve('tests/fixtures/installer-environment.ps1');

for (const shell of ['pwsh.exe', 'powershell.exe']) {
  test(`${shell} preserves PYTHONUTF8 during installer configuration probes`, {skip:process.platform !== 'win32'}, async t => {
    for (const [state, value] of [['absent', undefined], ['empty', ''], ['nonempty', '0']]) {
      await t.test(state, () => {
        const env = Object.fromEntries(Object.entries(process.env).filter(([name]) => !/^(PYTHONUTF8|REDMINE_CONFIG)$/i.test(name)));
        if (value !== undefined) env.PYTHONUTF8 = value;
        execFileSync(shell, ['-NoProfile', '-File', environmentFixture, sourceScript, state], {env, encoding:'utf8'});
      });
    }
  });
}

test('installer stops the launched process when environment restoration fails', {skip:process.platform !== 'win32'}, () => {
  execFileSync('pwsh.exe', ['-NoProfile', '-File', environmentFixture, sourceScript, 'launch-restore-failure'], {encoding:'utf8'});
});

test('doctor reports an absent installation from the bundled helper', {skip:process.platform !== 'win32'}, async t => {
  const root = await mkdtemp(join(tmpdir(), 'redmine-no-service-'));
  t.after(() => rm(root, {recursive:true, force:true}));
  const child = spawn(process.execPath, [join(sourceNode, 'dist', 'cli.js'), 'doctor'], {
    env:{...process.env, LOCALAPPDATA:root},
  });
  const chunks = [];
  child.stdout.on('data', chunk => chunks.push(chunk));
  child.stderr.on('data', chunk => chunks.push(chunk));
  const [code] = await once(child, 'close');
  const output = Buffer.concat(chunks).toString('utf8').trim();
  assert.equal(code, 1, output);
  assert.match(output, /^NOT_INSTALLED: [^\r\n]+$/);
  await assert.rejects(access(join(root, 'redmine-mcp')), {code:'ENOENT'});
});

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
  const supervisor = join(nodeFixture, 'src', 'supervisor.ts');
  const supervisorSource = await readFile(supervisor, 'utf8');
  const stuckSupervisor = supervisorSource.replace(
    "import { createServer, request } from 'node:http';",
    "import { existsSync } from 'node:fs';\nimport { createServer, request } from 'node:http';",
  ).replace(
    '  const headers = forwardedHeaders(incoming.headers);',
    "  if (existsSync('.fixture-unavailable-' + process.pid)) return end(outgoing, 503);\n  const headers = forwardedHeaders(incoming.headers);",
  ) + `
const watchFixtureClose = setInterval(() => {
  if (!existsSync('.fixture-close-' + process.pid)) return;
  clearInterval(watchFixtureClose);
  server.close();
  server.closeAllConnections();
}, 100);
watchFixtureClose.unref();
`;
  assert.match(stuckSupervisor, /existsSync\('\.fixture-unavailable-'/);
  await writeFile(supervisor, stuckSupervisor);
  const script = join(root, 'scripts', 'install-local-service.ps1');
  for (const name of ['validate-service-config.mjs', 'validate-service-config.py']) {
    await copyFile(resolve('../scripts', name), join(root, 'scripts', name));
  }
  const original = await readFile(sourceScript, 'utf8');
  let fixtureScript = original.replace("[Environment]::GetEnvironmentVariable($tokenName, 'User')", '$env:INSTALL_FIXTURE_USER_TOKEN');
  assert.notEqual(fixtureScript, original);
  const beforeInjection = fixtureScript;
  fixtureScript = fixtureScript.replace('    $started = Start-ServiceDeployment $item', "    if ($env:INSTALL_FIXTURE_FAIL) { throw 'fixture startup failure' }\n    $started = Start-ServiceDeployment $item");
  assert.notEqual(fixtureScript, beforeInjection);
  const beforeResolverInjection = fixtureScript;
  fixtureScript = fixtureScript.replace('function Resolve-ServiceConfig($item, [bool]$legacyCombined = $false) {', "function Resolve-ServiceConfig($item, [bool]$legacyCombined = $false) {\n    if ($env:INSTALL_FIXTURE_DIFFERENT_SOURCE -and $item.deployment -ne $env:INSTALL_FIXTURE_OLD_DEPLOYMENT) { return $env:INSTALL_FIXTURE_DIFFERENT_SOURCE }");
  assert.notEqual(fixtureScript, beforeResolverInjection);
  fixtureScript = fixtureScript.replace("$ErrorActionPreference = 'Stop'", `$ErrorActionPreference = 'Stop'
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
  await writeFile(script, fixtureScript);
  await copyFile(resolve('../scripts/doctor.ps1'), join(root, 'scripts', 'doctor.ps1'));
  const s = createServer().listen(0, '127.0.0.1'); await once(s, 'listening'); const port = s.address().port; await new Promise(r => s.close(r));
  const config = join(root, '使用者設定.toml'); const fixture = 'download_dir = "unused-legacy-setting"\n[sites.test]\nurl = "https://example.invalid"\napi_key = "fixture-key"\n'; await writeFile(config, fixture);
  const storage = join(root, 'redmine-mcp', 'attachments');
  const pwsh = execFileSync('where.exe', ['pwsh.exe'], {encoding:'utf8'}).trim().split(/\r?\n/)[0];
  const env = {...process.env};
  for (const name of Object.keys(env)) if (/^redmine_/i.test(name) || /^path$/i.test(name) || /^(pythonpath|pythonhome|node_options|node_path|node_extra_ca_certs|ssl_cert_file|ssl_cert_dir|requests_ca_bundle|curl_ca_bundle|https_proxy|http_proxy|all_proxy|no_proxy)$/i.test(name)) delete env[name];
  const systemPath = [join(process.env.SystemRoot, 'System32'), process.env.SystemRoot].join(';');
  Object.assign(env, {PATH:[dirname(process.execPath), systemPath].join(';'), LOCALAPPDATA:root, INSTALL_FIXTURE_USER_TOKEN:'a'.repeat(32), INSTALL_FIXTURE_NODE_MODULES:join(sourceNode, 'node_modules'), REDMINE_CONFIG:config});
  const run = async (program, args, expected = 0, extra = {}) => {
    const log = join(root, 'installer.log');
    const fd = openSync(log, 'w');
    const childEnv = {...env, ...extra};
    for (const name of Object.keys(childEnv)) if (childEnv[name] === undefined) delete childEnv[name];
    const child = spawn(program, args, {env:childEnv, stdio:['ignore',fd,fd]});
    const [code] = await once(child, 'exit'); closeSync(fd);
    const output = await readFile(log, 'utf8');
    assert.equal(code, expected, output);
    return output;
  };
  const invoke = (args, expected, extra) =>
    run(pwsh, ['-NoProfile', '-File', script, ...args, ...(args.includes('-Port') ? [] : ['-Port', String(port)])], expected, extra);
  const initialize = () => fetch(`http://127.0.0.1:${port}/mcp`, {method:'POST', headers:{Authorization:`Bearer ${'a'.repeat(32)}`, Accept:'application/json, text/event-stream', 'Content-Type':'application/json'}, body:JSON.stringify({jsonrpc:'2.0', id:1, method:'initialize', params:{protocolVersion:'2025-03-26', capabilities:{}, clientInfo:{name:'fixture',version:'1'}}})});
  await invoke(['-UseNode']);
  const manifest = join(root, 'redmine-mcp', 'service', 'active.json');
  let state = JSON.parse((await readFile(manifest, 'utf8')).replace(/^\uFEFF/, ''));
  pid = state.pid; assert.equal(state.engine, 'node');
  assert.equal(state.servicePaths.REDMINE_MCP_STORAGE_ROOT, undefined);
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
  await t.test('doctor verifies an owned listener with an authenticated MCP initialize', async () => {
    const doctor = (expected, extra) => run(process.execPath, [join(sourceNode, 'dist', 'cli.js'), 'doctor'], expected, extra);
    assert.match(await doctor(0), /^HEALTHY\b/);
    assert.match(await doctor(1, {INSTALL_FIXTURE_USER_TOKEN:'b'.repeat(32)}), /^AUTHENTICATED_PROBE_FAILED\b/);
    const {envHash, ...beforeMigration} = state;
    await writeFile(manifest, JSON.stringify(beforeMigration));
    assert.match(await doctor(0), /^HEALTHY\b/);
    await writeFile(manifest, JSON.stringify(state));
    assert.equal(JSON.parse((await readFile(manifest, 'utf8')).replace(/^\uFEFF/, '')).pid, pid);
  });

  await t.test('doctor repairs an identified supervisor that persistently returns 503', async () => {
    const deployment = join(dirname(manifest), state.deployment);
    await writeFile(join(deployment, '.fixture-unavailable-' + pid), '');
    assert.equal((await initialize()).status, 503);
    const doctor = (expected, extra) => run(process.execPath, [join(deployment, 'dist', 'cli.js'), 'doctor'], expected, extra);
    assert.match(await doctor(0, {HOME:join(root, 'other-shell'), PYTHONPATH:join(root, 'different-python-path')}), /^RECOVERED\b/);
    const restarted = JSON.parse((await readFile(manifest, 'utf8')).replace(/^\uFEFF/, ''));
    assert.notEqual(restarted.pid, pid);
    pid = restarted.pid;
    assert.equal(restarted.deployment, state.deployment);
    assert.equal(restarted.port, port);
    assert.equal((await initialize()).status, 200);
    state = restarted;
  });

  await t.test('doctor repairs an identified supervisor that lost its listener', async () => {
    const deployment = join(dirname(manifest), state.deployment);
    await writeFile(join(deployment, '.fixture-close-' + pid), '');
    for (let attempt = 0; attempt < 30; attempt++) {
      if (await initialize().then(() => false, () => true)) break;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    await assert.rejects(initialize());
    process.kill(pid, 0);
    assert.match(await run(process.execPath, [join(deployment, 'dist', 'cli.js'), 'doctor']), /^RECOVERED\b/);
    const restarted = JSON.parse((await readFile(manifest, 'utf8')).replace(/^\uFEFF/, ''));
    assert.notEqual(restarted.pid, pid);
    pid = restarted.pid;
    assert.equal(restarted.deployment, state.deployment);
    assert.equal((await initialize()).status, 200);
    state = restarted;
  });

  await t.test('doctor restarts a dead supervisor in place after verifying the installed context', async () => {
    const helper = join(dirname(manifest), 'doctor', 'doctor.ps1');
    const windowsPowerShell = join(process.env.SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
    const doctorEnv = {HOME:join(root, 'other-shell'), PATH:systemPath, REDMINE_CONFIG:undefined, REDMINE_MCP_STORAGE_ROOT:undefined, REDMINE_MCP_LOCAL_TOKEN:'b'.repeat(32), INSTALL_FIXTURE_NODE_MODULES:undefined};
    const doctor = (expected, extra) => run(windowsPowerShell, ['-NoProfile', '-File', helper], expected, {...doctorEnv, ...extra});

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
    assert.match(await doctor(1), /^PORT_OCCUPIED\b/);
    assert.equal(reached, false);
    await new Promise(resolve => occupiedPort.close(resolve));

    await writeFile(manifest, JSON.stringify({...state, pid:process.pid, startedAt:'1'}));
    assert.match(await doctor(1), /^PID_IDENTITY_MISMATCH\b/);
    const {envHash, ...beforeMigration} = state;
    await writeFile(manifest, JSON.stringify(beforeMigration));
    assert.match(await doctor(1), /^LEGACY_MANIFEST\b.*-Restart/);
    await writeFile(manifest, JSON.stringify(state));
    assert.match(await doctor(1, {INSTALL_FIXTURE_USER_TOKEN:undefined}), /^TOKEN_UNAVAILABLE\b/);
    const {environmentSnapshot, ...withoutSnapshot} = state;
    await writeFile(manifest, JSON.stringify(withoutSnapshot));
    assert.match(await doctor(1, {PYTHONPATH:join(root, 'different-python-path')}), /^ENVIRONMENT_CHANGED\b/);
    await writeFile(manifest, JSON.stringify({...state, environmentSnapshot:'invalid ciphertext'}));
    assert.match(await doctor(1), /^ENVIRONMENT_CHANGED\b/);
    await writeFile(manifest, JSON.stringify(state));
    await writeFile(config, updatedFixture + '# unverified configuration\n');
    assert.match(await doctor(1), /^CONFIGURATION_CHANGED\b/);
    await writeFile(config, updatedFixture);
    const installedCli = join(dirname(manifest), state.deployment, 'dist', 'cli.js');
    const output = await run(process.execPath, [installedCli, 'doctor'], 0, doctorEnv);
    assert.match(output, /^RECOVERED\b/);
    assert.doesNotMatch(output, /fixture-key|aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa|bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/);
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
  await invoke(['-Restart', '-AdoptEnvironment'], 1);
  assert.match(await readFile(join(root, 'installer.log'), 'utf8'), /requires the recorded service to be stopped/i);
  assert.equal((await initialize()).status, 200);
  const laterFixture = updatedFixture.replace('sites.changed', 'sites.later');
  await writeFile(config, laterFixture);
  await invoke(['-Restart'], 1, {INSTALL_FIXTURE_FAIL:'1'});
  assert.match(await readFile(join(root, 'installer.log'), 'utf8'), /previous service was not restored/i);
  assert.equal(await readFile(config, 'utf8'), laterFixture);
  await assert.rejects(fetch(`http://127.0.0.1:${port}/mcp`));
  await invoke(['-Restart', '-AdoptEnvironment'], 0, {HOME:join(root, 'adopted-shell')});
  state = JSON.parse((await readFile(manifest, 'utf8')).replace(/^\uFEFF/, ''));
  pid = state.pid;
  assert.ok(state.environmentSnapshot);
  assert.equal((await initialize()).status, 200);
});

for (const shell of ['pwsh.exe', 'powershell.exe']) {
  test(`${shell} restores encrypted launch context across shells and preserves guards`, {skip:process.platform !== 'win32'}, () => {
    execFileSync(shell, ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', resolve('tests/fixtures/installer-recovery.ps1'), sourceScript], {encoding:'utf8'});
  });
}

for (const [writer, reader] of [['pwsh.exe', 'powershell.exe'], ['powershell.exe', 'pwsh.exe']]) {
  test(`encrypted snapshot fingerprints survive ${writer} to ${reader} including empty values`, {skip:process.platform !== 'win32'}, async t => {
    const root = await mkdtemp(join(tmpdir(), 'redmine-snapshot-'));
    t.after(() => rm(root, {recursive:true, force:true}));
    const snapshot = join(root, 'snapshot.json');
    const fixture = resolve('tests/fixtures/installer-snapshot.ps1');
    for (const [shell, mode] of [[writer, 'write'], [reader, 'read']]) {
      execFileSync(shell, ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', fixture, sourceScript, mode, snapshot], {encoding:'utf8'});
    }
  });
}
