import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, symlink, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { openDirectory, openChildDirectory, closeHandle, tryLock, openFile, createDirectory, readHandle, writeHandle, flushHandle, renameHandle, deleteHandle } from '../dist/storage/windows-handles.js';

test('relative directory open rejects a junction to an outside directory', {skip: process.platform !== 'win32'}, async () => {
  const root = await mkdtemp(join(tmpdir(), 'rm-native-'));
  const parent = join(root, 'parent'), outside = join(root, 'outside');
  await mkdir(parent); await mkdir(outside);
  await symlink(outside, join(parent, 'link'), 'junction');
  let handle;
  try {
    handle = openDirectory(parent);
    assert.throws(() => openChildDirectory(handle, 'link'));
    const child = await mkdir(join(parent, 'child'));
    void child;
    const opened = openChildDirectory(handle, 'child'); closeHandle(opened);
  } finally { if (handle) closeHandle(handle); await rm(root, {recursive:true, force:true}); }
});

test('one-byte lock blocks Python msvcrt locking', {skip: process.platform !== 'win32' || !process.env.STORAGE_TEST_PYTHON}, async () => {
  const {spawnSync} = await import('node:child_process');
  const python = process.env.STORAGE_TEST_PYTHON;
  const root = await mkdtemp(join(tmpdir(), 'rm-lock-'));
  const handle = openDirectory(root);
  let lock;
  try {
    lock = tryLock(handle, 'item.lock');
    assert.ok(lock);
    const result = spawnSync(python, ['-c', 'import msvcrt,sys; f=open(sys.argv[1],"r+b");\ntry: msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1); print("acquired")\nexcept OSError: print("blocked")', join(root, 'item.lock')], {encoding:'utf8'});
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout.trim(), 'blocked');
  } finally { if (lock) closeHandle(lock); closeHandle(handle); await rm(root, {recursive:true, force:true}); }
});



test('held directory handle prevents replacement by junction', {skip: process.platform !== 'win32'}, async () => {
  const {rename} = await import('node:fs/promises');
  const root = await mkdtemp(join(tmpdir(), 'rm-pin-'));
  await mkdir(join(root, 'parent')); await mkdir(join(root, 'outside'));
  const handle = openDirectory(join(root, 'parent'));
  try {
    await assert.rejects(rename(join(root, 'parent'), join(root, 'moved')));
    await assert.rejects(symlink(join(root, 'outside'), join(root, 'parent'), 'junction'));
  } finally { closeHandle(handle); await rm(root, {recursive:true, force:true}); }
});

test('Python one-byte lock blocks Node until release', {skip: process.platform !== 'win32' || !process.env.STORAGE_TEST_PYTHON}, async () => {
  const {spawn} = await import('node:child_process');
  const {once} = await import('node:events');
  const root = await mkdtemp(join(tmpdir(), 'rm-lock-reverse-'));
  const handle = openDirectory(root);
  const seed = tryLock(handle, 'item.lock'); closeHandle(seed);
  const child = spawn(process.env.STORAGE_TEST_PYTHON, ['-u', '-c', 'import msvcrt,sys; f=open(sys.argv[1],"r+b"); msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1); print("ready",flush=True); sys.stdin.read(1)', join(root, 'item.lock')], {stdio:['pipe','pipe','pipe']});
  try {
    const [data] = await once(child.stdout, 'data'); assert.equal(data.toString().trim(), 'ready');
    assert.equal(tryLock(handle, 'item.lock'), null);
    child.stdin.end('x'); await once(child, 'exit');
    const acquired = tryLock(handle, 'item.lock'); assert.ok(acquired); closeHandle(acquired);
  } finally { child.kill(); closeHandle(handle); await rm(root, {recursive:true, force:true}); }
});

test('pins ancestor chain and performs relative file operations', {skip: process.platform !== 'win32'}, async () => {
  const {rename, readFile} = await import('node:fs/promises');
  const root = await mkdtemp(join(tmpdir(), 'rm-operations-'));
  await mkdir(join(root, 'ancestor')); await mkdir(join(root, 'ancestor', 'leaf'));
  const parent = openDirectory(join(root, 'ancestor', 'leaf'));
  try {
    await assert.rejects(rename(join(root, 'ancestor'), join(root, 'moved')));
    await symlink(root, join(root, 'ancestor', 'leaf', 'junction'), 'junction');
    assert.throws(() => openFile(parent, 'junction'));
    const sub = createDirectory(parent, 'sub'); closeHandle(sub);
    let file = openFile(parent, 'source', 'create');
    writeHandle(file, Buffer.from('hello')); flushHandle(file);
    renameHandle(file, parent, 'target'); closeHandle(file);
    assert.equal(await readFile(join(root, 'ancestor', 'leaf', 'target'), 'utf8'), 'hello');
    const readOnly = openFile(parent, 'target', 'open', 'read');
    assert.equal(readHandle(readOnly, 20).toString(), 'hello');
    assert.throws(() => writeHandle(readOnly, Buffer.from('x')));
    assert.throws(() => deleteHandle(readOnly)); closeHandle(readOnly);
    file = openFile(parent, 'target');
    assert.equal(readHandle(file, 20).toString(), 'hello');
    const other = openFile(parent, 'other', 'create'); closeHandle(other);
    assert.throws(() => renameHandle(file, parent, 'other'));
    renameHandle(file, parent, 'other', true);
    deleteHandle(file); closeHandle(file);
    await assert.rejects(readFile(join(root, 'ancestor', 'leaf', 'other')));
  } finally { closeHandle(parent); await rm(root, {recursive:true, force:true}); }
});

test('rejects hard-linked files and rechecks an already-open handle', {skip: process.platform !== 'win32'}, async () => {
  const {link, writeFile, readFile, unlink} = await import('node:fs/promises');
  const root = await mkdtemp(join(tmpdir(), 'rm-hardlink-'));
  await mkdir(join(root, 'managed')); await writeFile(join(root, 'outside'), 'untouched');
  await link(join(root, 'outside'), join(root, 'managed', 'alias'));
  const parent = openDirectory(join(root, 'managed'));
  try {
    assert.throws(() => openFile(parent, 'alias'));
    assert.throws(() => openFile(parent, 'alias', 'open', 'read'));
    assert.throws(() => tryLock(parent, 'alias'));
    assert.equal(await readFile(join(root, 'outside'), 'utf8'), 'untouched');
    await unlink(join(root, 'managed', 'alias'));
    await writeFile(join(root, 'managed', 'file'), 'safe');
    const file = openFile(parent, 'file');
    try {
      await link(join(root, 'managed', 'file'), join(root, 'second'));
      assert.throws(() => writeHandle(file, Buffer.from('changed')));
      assert.throws(() => renameHandle(file, parent, 'renamed'));
      assert.throws(() => deleteHandle(file));
    } finally { closeHandle(file); }
    assert.equal(await readFile(join(root, 'second'), 'utf8'), 'safe');
  } finally { closeHandle(parent); await rm(root, {recursive:true, force:true}); }
});
