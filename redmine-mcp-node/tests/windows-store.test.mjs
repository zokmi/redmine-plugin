import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, dirname, basename } from 'node:path';
import { once } from 'node:events';
import { openDirectory, createPrivateDirectory, openFile, writeHandle, closeHandle } from '../dist/storage/windows-handles.js';

// Crash/upload fixtures must have the same user ownership as files created by the store.
function writeOwnedFixture(file, data) {
  const parent = openDirectory(dirname(file));
  let handle;
  try { handle = openFile(parent, basename(file), 'create'); writeHandle(handle, Buffer.from(data)); }
  finally { if (handle) closeHandle(handle); closeHandle(parent); }
}

test('Windows store stages, publishes and borrows a Python-compatible item', { skip: process.platform !== 'win32' }, async () => {
  const { createWindowsAttachmentStore } = await import('../dist/storage/windows-store.js');
  const base = await mkdtemp(join(tmpdir(), 'rm-store-'));
  const parent = openDirectory(base); const root = createPrivateDirectory(parent, 'cache'); closeHandle(root); closeHandle(parent);
  try {
    const store = createWindowsAttachmentStore({root:join(base, 'cache'), clock:() => new Date('2026-01-01T00:00:00Z')});
    const staged = await store.stageDownload('https://example.com', 'sample.txt');
    staged.writer.end('hello'); await once(staged.writer, 'finish');
    const published = await store.publish(staged);
    assert.equal((await readFile(published)).toString(), 'hello');
    assert.equal(Buffer.from(await store.borrowSource(published, 10, null)).toString(), 'hello');
    const dir = dirname(published), item = basename(dir), site = dirname(dirname(dir));
    const record = JSON.parse(await readFile(join(site, 'records', `${item}.json`), 'utf8'));
    assert.equal(record.filename, 'sample.txt'); assert.equal(record.size, 5); assert.equal(record.schema_version, 1);
  } finally { await rm(base, {recursive:true, force:true}); }
});

test('discard removes an interrupted staging file and rejects unsafe roots', { skip: process.platform !== 'win32' }, async () => {
  const { createWindowsAttachmentStore } = await import('../dist/storage/windows-store.js');
  const { readdir } = await import('node:fs/promises');
  const base = await mkdtemp(join(tmpdir(), 'rm-discard-'));
  const parent = openDirectory(base); const root = createPrivateDirectory(parent, 'cache'); closeHandle(root); closeHandle(parent);
  try {
    const unsafe = createWindowsAttachmentStore({root:base});
    await assert.rejects(unsafe.stageDownload('https://example.com', 'x'));
    const store = createWindowsAttachmentStore({root:join(base,'cache')});
    const stage = await store.stageDownload('https://example.com', 'x');
    stage.writer.write('partial');
    await store.discard(stage);
    const site = (await readdir(join(base,'cache')))[0];
    const item = (await readdir(join(base,'cache',site,'active')))[0];
    assert.deepEqual(await readdir(join(base,'cache',site,'active',item)), []);
    await assert.rejects(store.publish(stage));
  } finally { await rm(base, {recursive:true, force:true}); }
});

test('failed publish removes the unrecorded file', { skip: process.platform !== 'win32' }, async () => {
  const { createWindowsAttachmentStore } = await import('../dist/storage/windows-store.js');
  const { readdir } = await import('node:fs/promises');
  const base = await mkdtemp(join(tmpdir(), 'rm-failed-publish-'));
  const parent = openDirectory(base); const root = createPrivateDirectory(parent, 'cache'); closeHandle(root); closeHandle(parent);
  let calls = 0;
  try {
    const store = createWindowsAttachmentStore({root:join(base,'cache'), clock:() => ++calls === 1 ? new Date() : new Date(NaN)});
    const stage = await store.stageDownload('https://example.com', 'x'); stage.writer.end('data'); await once(stage.writer, 'finish');
    await assert.rejects(store.publish(stage));
    const site = (await readdir(join(base,'cache')))[0], item = (await readdir(join(base,'cache',site,'active')))[0];
    assert.deepEqual(await readdir(join(base,'cache',site,'active',item)), []);
  } finally { await rm(base, {recursive:true, force:true}); }
});

test('Python and Node exchange on-disk cache entries', { skip: process.platform !== 'win32' || !process.env.STORAGE_TEST_PYTHON }, async () => {
  const { execFileSync } = await import('node:child_process');
  const { resolve } = await import('node:path');
  const { createWindowsAttachmentStore } = await import('../dist/storage/windows-store.js');
  const base = await mkdtemp(join(tmpdir(), 'rm-python-store-'));
  const parent = openDirectory(base); const root = createPrivateDirectory(parent, 'cache'); closeHandle(root); closeHandle(parent);
  const cache = join(base, 'cache');
  try {
    const store = createWindowsAttachmentStore({root:cache});
    const stage = await store.stageDownload('https://example.com', 'node.txt'); stage.writer.end('from node'); await once(stage.writer, 'finish');
    const nodePath = await store.publish(stage);
    const fixture = JSON.parse(execFileSync(process.env.STORAGE_TEST_PYTHON,
      ['-B', 'test-support/legacy-windows-storage.py', cache, nodePath], {
        encoding: 'utf8', env: { ...process.env, PYTHONPATH: resolve('../redmine-mcp/src') },
      }));
    assert.equal(fixture.borrowed, 'from node');
    assert.equal((await store.borrowSource(fixture.ordinary, 100, null)).toString(), 'from python');

    const item = dirname(fixture.native), site = dirname(dirname(item));
    const lockPath = join(site, 'locks', basename(item) + '.lock');
    const powershell = join(process.env.SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
    const identity = JSON.parse(execFileSync(powershell, ['-NoProfile', '-NonInteractive', '-Command', [
      '$ErrorActionPreference = "Stop"',
      '$sidType = [System.Security.Principal.SecurityIdentifier]',
      '$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()',
      '$acl = [System.IO.File]::GetAccessControl($env:LEGACY_FIXTURE_LOCK, [System.Security.AccessControl.AccessControlSections]::Owner)',
      '$lockOwner = $acl.GetOwner($sidType).Value',
      '@{user=$identity.User.Value; defaultOwner=$identity.Owner.Value; lockOwner=$lockOwner} | ConvertTo-Json -Compress',
    ].join('\n')], { encoding: 'utf8', env: { ...process.env, LEGACY_FIXTURE_LOCK: lockPath } }));
    assert.equal(identity.lockOwner, identity.defaultOwner);
    if (identity.lockOwner === identity.user) {
      assert.equal((await store.borrowSource(fixture.native, 100, null)).toString(), 'from python');
    } else {
      const error = identity.lockOwner === 'S-1-5-32-544'
        ? /owned by the Windows Administrators group/ : /Unsafe owner or DACL/;
      await assert.rejects(store.borrowSource(fixture.native, 100, null), error);
      assert.equal(await readFile(fixture.native, 'utf8'), 'from python');
    }
  } finally { await rm(base, {recursive:true, force:true}); }
});

test('configured upload root enforces containment and private ACL', { skip: process.platform !== 'win32' }, async () => {
  const { createWindowsAttachmentStore } = await import('../dist/storage/windows-store.js');
  const { writeFile } = await import('node:fs/promises');
  const base = await mkdtemp(join(tmpdir(), 'rm-upload-'));
  const parent = openDirectory(base); const cache = createPrivateDirectory(parent, 'cache'); const upload = createPrivateDirectory(parent, 'upload'); closeHandle(cache); closeHandle(upload); closeHandle(parent);
  try {
    writeOwnedFixture(join(base,'upload','ok.txt'), 'okay'); await writeFile(join(base,'outside.txt'), 'no');
    const store = createWindowsAttachmentStore({root:join(base,'cache')});
    assert.equal(Buffer.from(await store.borrowSource(join(base,'upload','ok.txt'), 4, join(base,'upload'))).toString(), 'okay');
    await assert.rejects(store.borrowSource(join(base,'outside.txt'), 4, null));
    await assert.rejects(store.borrowSource(join(base,'outside.txt'), 4, join(base,'upload')));
    await assert.rejects(store.borrowSource(join(base,'outside.txt'), 4, base));
    await assert.rejects(store.borrowSource(join(base,'upload','ok.txt'), 3, join(base,'upload')));
  } finally { await rm(base, {recursive:true, force:true}); }
});


test('borrow returns data and preserves record when metadata replacement is blocked', { skip: process.platform !== 'win32' }, async () => {
  const { createWindowsAttachmentStore } = await import('../dist/storage/windows-store.js');
  const { openFile } = await import('../dist/storage/windows-handles.js');
  const base = await mkdtemp(join(tmpdir(), 'rm-refresh-'));
  const parent = openDirectory(base); const root = createPrivateDirectory(parent, 'cache'); closeHandle(root); closeHandle(parent);
  try {
    const store = createWindowsAttachmentStore({root:join(base,'cache')});
    const stage = await store.stageDownload('https://example.com', 'x'); stage.writer.end('data'); await once(stage.writer, 'finish');
    const published = await store.publish(stage);
    const item = basename(dirname(published)), records = join(dirname(dirname(dirname(published))), 'records');
    const recordPath = join(records, `${item}.json`), before = await readFile(recordPath);
    const directory = openDirectory(records), blocking = openFile(directory, `${item}.json`, 'open', 'read');
    try { assert.equal(Buffer.from(await store.borrowSource(published, 100, null)).toString(), 'data'); }
    finally { closeHandle(blocking); closeHandle(directory); }
    assert.deepEqual(await readFile(recordPath), before);
  } finally { await rm(base, {recursive:true, force:true}); }
});

test('staged file is removed if its handle fails ACL verification', { skip: process.platform !== 'win32' }, async () => {
  const {spawnSync} = await import('node:child_process');
  const result = spawnSync(process.execPath, ['--experimental-test-module-mocks', 'test-support/stage-acl-fixture.mjs'], {encoding:'utf8'});
  assert.equal(result.status, 0, result.stderr);
});


test('record refresh ACL failure is propagated and old data remains', { skip: process.platform !== 'win32' }, async () => {
  const {spawnSync} = await import('node:child_process');
  const result = spawnSync(process.execPath, ['--experimental-test-module-mocks', 'test-support/record-acl-fixture.mjs'], {encoding:'utf8'});
  assert.equal(result.status, 0, result.stderr);
});

test('record refresh disk-full error preserves record and returns data', { skip: process.platform !== 'win32' }, async () => {
  const {spawnSync} = await import('node:child_process');
  const result = spawnSync(process.execPath, ['--experimental-test-module-mocks', 'test-support/record-acl-fixture.mjs', 'io'], {encoding:'utf8'});
  assert.equal(result.status, 0, result.stderr);
});

test('sweep archives an idle item into a Python-readable ZIP', { skip: process.platform !== 'win32' || !process.env.STORAGE_TEST_PYTHON }, async () => {
  const {spawnSync} = await import('node:child_process');
  const {createWindowsAttachmentStore} = await import('../dist/storage/windows-store.js');
  const base = await mkdtemp(join(tmpdir(), 'rm-sweep-'));
  const parent = openDirectory(base); const root = createPrivateDirectory(parent, 'cache'); closeHandle(root); closeHandle(parent);
  try {
    let now = new Date('2026-01-01T00:00:00Z');
    const store = createWindowsAttachmentStore({root:join(base,'cache'), clock:() => now});
    const stage = await store.stageDownload('https://example.com', 'sample.txt'); stage.writer.end('hello'); await once(stage.writer, 'finish');
    const original = await store.publish(stage), item = basename(dirname(original)), site = dirname(dirname(dirname(original)));
    now = new Date('2026-01-09T00:00:00Z'); await store.sweep();
    const record = JSON.parse(await readFile(join(site,'records',`${item}.json`),'utf8'));
    assert.equal(record.state, 'archived');
    await assert.rejects(readFile(original), {code:'ENOENT'});
    const result = spawnSync(process.env.STORAGE_TEST_PYTHON, ['-c', 'import sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); assert z.namelist()==["sample.txt"]; assert z.read("sample.txt")==b"hello"', join(site,'archives',`${item}.zip`)], {encoding:'utf8'});
    assert.equal(result.status,0,result.stderr);
  } finally { await rm(base,{recursive:true,force:true}); }
});

test('sweep repairs published state, retains mismatched archives and expires verified archives', {skip:process.platform !== 'win32'}, async () => {
  const {writeFile} = await import('node:fs/promises');
  const {createWindowsAttachmentStore} = await import('../dist/storage/windows-store.js');
  const base = await mkdtemp(join(tmpdir(), 'rm-recovery-'));
  const parent = openDirectory(base), root = createPrivateDirectory(parent,'cache'); closeHandle(root); closeHandle(parent);
  try {
    let now = new Date('2026-01-01T00:00:00Z'); const store = createWindowsAttachmentStore({root:join(base,'cache'),clock:() => now});
    const stage = await store.stageDownload('https://example.com','one.txt'); stage.writer.end('hello'); await once(stage.writer,'finish');
    const original = await store.publish(stage), id = basename(dirname(original)), site = dirname(dirname(dirname(original)));
    const recordPath = join(site,'records',`${id}.json`), zipPath = join(site,'archives',`${id}.zip`);
    now = new Date('2026-01-09T00:00:00Z'); await store.sweep();
    let record = JSON.parse(await readFile(recordPath)); const zip = await readFile(zipPath);
    // Simulate a crash after publishing the archive and before deleting the original.
    record.state = 'archive_published'; await writeFile(recordPath,JSON.stringify(record)); writeOwnedFixture(original,'hello');
    await writeFile(zipPath,'corrupt'); await store.sweep();
    assert.equal((await readFile(original)).toString(),'hello'); assert.equal(JSON.parse(await readFile(recordPath)).state,'archive_published');
    await writeFile(zipPath,zip); await writeFile(original,'changed'); await store.sweep();
    assert.equal((await readFile(original)).toString(),'changed'); assert.equal(JSON.parse(await readFile(recordPath)).state,'archive_published');
    await writeFile(original,'hello'); await store.sweep();
    await assert.rejects(readFile(original),{code:'ENOENT'}); assert.equal(JSON.parse(await readFile(recordPath)).state,'archived');
    now = new Date('2026-02-08T00:00:00Z'); await writeFile(zipPath,'corrupt'); await store.sweep();
    assert.equal(JSON.parse(await readFile(recordPath)).state,'archived');
    await writeFile(zipPath,zip); await store.sweep();
    await assert.rejects(readFile(zipPath),{code:'ENOENT'}); await assert.rejects(readFile(recordPath),{code:'ENOENT'});
  } finally { await rm(base,{recursive:true,force:true}); }
});

test('sweep respects locks and the seven-day idle threshold', {skip:process.platform !== 'win32'}, async () => {
  const {tryLock} = await import('../dist/storage/windows-handles.js');
  const {createWindowsAttachmentStore} = await import('../dist/storage/windows-store.js');
  const base = await mkdtemp(join(tmpdir(), 'rm-sweep-lock-'));
  const parent = openDirectory(base), root = createPrivateDirectory(parent,'cache'); closeHandle(root); closeHandle(parent);
  try {
    let now = new Date('2026-01-01T00:00:00Z'); const store = createWindowsAttachmentStore({root:join(base,'cache'),clock:() => now});
    const stage = await store.stageDownload('https://example.com','one.txt'); stage.writer.end('hello'); await once(stage.writer,'finish');
    const original = await store.publish(stage), id = basename(dirname(original)), site = dirname(dirname(dirname(original))), recordPath = join(site,'records',`${id}.json`);
    now = new Date('2026-01-07T23:59:59Z'); await store.sweep(); assert.equal(JSON.parse(await readFile(recordPath)).state,'active');
    now = new Date('2026-01-08T00:00:00Z');
    const locks = openDirectory(join(site,'locks')), itemLock = tryLock(locks,`${id}.lock`);
    try { await store.sweep(); assert.equal(JSON.parse(await readFile(recordPath)).state,'active'); } finally {closeHandle(itemLock); closeHandle(locks);}
    const cache = openDirectory(join(base,'cache')), global = tryLock(cache,'.sweep.lock');
    try { await store.sweep(); assert.equal(JSON.parse(await readFile(recordPath)).state,'active'); } finally {closeHandle(global); closeHandle(cache);}
    await store.sweep(); assert.equal(JSON.parse(await readFile(recordPath)).state,'archived');
  } finally { await rm(base,{recursive:true,force:true}); }
});

test('sweep keeps original when a post-rename barrier fails', {skip:process.platform !== 'win32'}, async () => {
  const {spawnSync} = await import('node:child_process');
  for (const mode of ['zip','record','recovery','directory','volume']) {
    const result = spawnSync(process.execPath, ['--experimental-test-module-mocks','test-support/sweep-barrier-fixture.mjs',mode], {encoding:'utf8'});
    assert.equal(result.status, 0, result.stderr);
  }
});

test('sweep uses microsecond timestamps without premature archive or expiry', {skip:process.platform !== 'win32'}, async () => {
  const {writeFile} = await import('node:fs/promises'); const {createWindowsAttachmentStore} = await import('../dist/storage/windows-store.js');
  const base = await mkdtemp(join(tmpdir(),'rm-micro-')); const p = openDirectory(base), r = createPrivateDirectory(p,'cache'); closeHandle(r); closeHandle(p);
  try {
    let now = new Date('2026-01-01T00:00:00Z'); const store = createWindowsAttachmentStore({root:join(base,'cache'),clock:() => now});
    const stage = await store.stageDownload('https://example.com','x'); stage.writer.end('data'); await once(stage.writer,'finish'); const original = await store.publish(stage);
    const id = basename(dirname(original)), site = dirname(dirname(dirname(original))), recordPath = join(site,'records',`${id}.json`), zipPath = join(site,'archives',`${id}.zip`);
    let rec = JSON.parse(await readFile(recordPath)); rec.last_used_at = '2026-01-01T00:00:00.000001+00:00'; await writeFile(recordPath,JSON.stringify(rec));
    now = new Date('2026-01-08T00:00:00Z'); await store.sweep(); assert.equal(JSON.parse(await readFile(recordPath)).state,'active');
    now = new Date('2026-01-08T00:00:00.001Z'); await store.sweep(); rec = JSON.parse(await readFile(recordPath)); assert.equal(rec.state,'archived');
    rec.state = 'archive_published'; rec.archived_at = '2026-01-08T00:00:00.002001+00:00'; await writeFile(recordPath,JSON.stringify(rec)); writeOwnedFixture(original,'data');
    now = new Date('2026-01-08T00:00:00.002Z'); await store.sweep(); assert.equal((await readFile(original)).toString(),'data');
    now = new Date('2026-01-08T00:00:00.003Z'); await store.sweep(); await assert.rejects(readFile(original),{code:'ENOENT'}); rec = JSON.parse(await readFile(recordPath));
    rec.archived_at = '2026-01-08T00:00:00.001001+00:00'; await writeFile(recordPath,JSON.stringify(rec));
    now = new Date('2026-02-07T00:00:00.001Z'); await store.sweep(); assert.equal(JSON.parse(await readFile(recordPath)).state,'archived'); assert.ok(await readFile(zipPath));
    now = new Date('2026-02-07T00:00:00.002Z'); await store.sweep(); await assert.rejects(readFile(recordPath),{code:'ENOENT'});
  } finally { await rm(base,{recursive:true,force:true}); }
});

test('sweep finishes expiry interrupted after removing archive', {skip:process.platform !== 'win32'}, async () => {
  const {createWindowsAttachmentStore} = await import('../dist/storage/windows-store.js'); const base = await mkdtemp(join(tmpdir(),'rm-expiry-')); const p = openDirectory(base), r = createPrivateDirectory(p,'cache'); closeHandle(r); closeHandle(p);
  try {
    let now = new Date('2026-01-01T00:00:00Z'); const store = createWindowsAttachmentStore({root:join(base,'cache'),clock:() => now});
    const stage = await store.stageDownload('https://example.com','x'); stage.writer.end('data'); await once(stage.writer,'finish'); const original = await store.publish(stage);
    const id = basename(dirname(original)), site = dirname(dirname(dirname(original))), recordPath = join(site,'records',`${id}.json`), zipPath = join(site,'archives',`${id}.zip`);
    now = new Date('2026-01-09T00:00:00Z'); await store.sweep(); await rm(zipPath);
    now = new Date('2026-02-08T00:00:00Z'); await store.sweep(); await assert.rejects(readFile(recordPath),{code:'ENOENT'});
  } finally { await rm(base,{recursive:true,force:true}); }
});

test('sweep resumes after injected interruption during expiry', {skip:process.platform !== 'win32'}, async () => {
  const {spawnSync} = await import('node:child_process');
  const result = spawnSync(process.execPath,['--experimental-test-module-mocks','test-support/sweep-expiry-fixture.mjs'],{encoding:'utf8'});
  assert.equal(result.status,0,result.stderr);
});
