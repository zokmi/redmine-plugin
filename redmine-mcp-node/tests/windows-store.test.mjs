import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, dirname, basename } from 'node:path';
import { once } from 'node:events';
import { openDirectory, createPrivateDirectory, closeHandle } from '../dist/storage/windows-handles.js';

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
  const { spawnSync } = await import('node:child_process');
  const { resolve } = await import('node:path');
  const { createWindowsAttachmentStore } = await import('../dist/storage/windows-store.js');
  const base = await mkdtemp(join(tmpdir(), 'rm-python-store-'));
  const parent = openDirectory(base); const root = createPrivateDirectory(parent, 'cache'); closeHandle(root); closeHandle(parent);
  const cache = join(base, 'cache');
  const run = (code, ...args) => { const p = spawnSync(process.env.STORAGE_TEST_PYTHON, ['-c', code, cache, ...args], {encoding:'utf8', env:{...process.env, PYTHONPATH:resolve('../redmine-mcp/src')}}); assert.equal(p.status, 0, p.stderr); return p.stdout.trim(); };
  try {
    const store = createWindowsAttachmentStore({root:cache});
    const stage = await store.stageDownload('https://example.com', 'node.txt'); stage.writer.end('from node'); await once(stage.writer, 'finish');
    const nodePath = await store.publish(stage);
    assert.equal(run('import sys; from pathlib import Path; from redmine_mcp.attachment_storage.store import AttachmentStore; s=AttachmentStore(Path(sys.argv[1]));\nwith s.borrow_source(Path(sys.argv[2]),100) as data: print(data.decode())', nodePath), 'from node');
    const pyPath = run('import sys; from pathlib import Path; from redmine_mcp.attachment_storage.store import AttachmentStore; s=AttachmentStore(Path(sys.argv[1]));\nwith s.begin_download("https://example.com", "python.txt") as p:\n with p.open_writer() as f: f.write(b"from python")\n print(p.publish())');
    assert.equal(Buffer.from(await store.borrowSource(pyPath,100,null)).toString(), 'from python');
  } finally { await rm(base, {recursive:true, force:true}); }
});

test('configured upload root enforces containment and private ACL', { skip: process.platform !== 'win32' }, async () => {
  const { createWindowsAttachmentStore } = await import('../dist/storage/windows-store.js');
  const { writeFile } = await import('node:fs/promises');
  const base = await mkdtemp(join(tmpdir(), 'rm-upload-'));
  const parent = openDirectory(base); const cache = createPrivateDirectory(parent, 'cache'); const upload = createPrivateDirectory(parent, 'upload'); closeHandle(cache); closeHandle(upload); closeHandle(parent);
  try {
    await writeFile(join(base,'upload','ok.txt'), 'okay'); await writeFile(join(base,'outside.txt'), 'no');
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
