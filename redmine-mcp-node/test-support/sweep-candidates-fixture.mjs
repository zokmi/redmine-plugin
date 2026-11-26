import assert from 'node:assert/strict';
import { once } from 'node:events';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, dirname, join } from 'node:path';
import { mock } from 'node:test';
import koffi from 'koffi';

let rejectExtendedRename = false;
let extendedAttempts = 0;
mock.module('koffi', {defaultExport: {
  ...koffi,
  load(name) {
    const library = koffi.load(name);
    if (name !== 'ntdll.dll') return library;
    return {func(signature, ...types) {
      const call = library.func(signature, ...types);
      if (!signature.includes('NtSetInformationFile(')) return call;
      return (...args) => {
        if (args[4] === 65) {
          extendedAttempts++;
          if (rejectExtendedRename) return -1073741821; // STATUS_INVALID_INFO_CLASS
        }
        return call(...args);
      };
    }};
  },
}});
const handles = await import('../dist/storage/windows-handles.js');
const { provisionStorageRoot } = await import('../dist/storage-preflight.js');

let observe = false;
let refreshBeforeLock;
let duringRead;
const itemLocks = [];
mock.module(new URL('../dist/storage/windows-handles.js', import.meta.url), {
  namedExports: {
    ...handles,
    openReplaceableReadFile(...args) {
      const handle = handles.openReplaceableReadFile(...args);
      if (duringRead?.name === args[1]) {
        const refresh = duringRead;
        duringRead = undefined;
        refresh.run();
      }
      return handle;
    },
    tryLock(parent, name) {
      if (observe && name !== '.sweep.lock') {
        itemLocks.push(name);
        const refresh = refreshBeforeLock;
        refreshBeforeLock = undefined;
        refresh?.();
      }
      return handles.tryLock(parent, name);
    },
  },
});

const { createWindowsAttachmentStore } = await import('../dist/storage/windows-store.js');
const base = await mkdtemp(join(tmpdir(), 'rm-sweep-candidates-'));
try {
  const root = join(base, 'cache');
  provisionStorageRoot(root);
  let now = new Date('2026-01-01T00:00:00Z');
  const store = createWindowsAttachmentStore({root, clock: () => now});
  async function publish(filename) {
    const stage = await store.stageDownload('https://example.test', filename);
    const finished = once(stage.writer, 'finish');
    stage.writer.end('data');
    await finished;
    const path = await store.publish(stage);
    const itemDir = dirname(path), id = basename(itemDir);
    const records = join(dirname(dirname(itemDir)), 'records');
    return {path, id, records, metadata: join(records, id + '.json')};
  }
  const archived = await publish('old.txt');
  now = new Date('2026-01-09T00:00:00Z');
  await store.sweep();
  assert.equal(JSON.parse(await readFile(archived.metadata)).state, 'archived');
  const active = await publish('fresh.txt');
  observe = true;
  await store.sweep();
  assert.deepEqual(itemLocks, [], 'Neither fresh active nor fresh archived items need an exclusive item lock');

  now = new Date('2026-01-17T00:00:00Z');
  let upload;
  refreshBeforeLock = () => { upload = store.borrowSource(active.path, 10, null); };
  await store.sweep();
  assert.equal(Buffer.from(await upload).toString(), 'data');
  assert.equal(await readFile(active.path, 'utf8'), 'data');
  const current = JSON.parse(await readFile(active.metadata));
  assert.equal(current.state, 'active');
  assert.equal(Date.parse(current.last_used_at), now.getTime());
  assert.deepEqual(itemLocks, [active.id + '.lock', active.id + '.lock']);

  // A foreground upload can refresh yesterday's record while the background holds its old version.
  now = new Date('2026-01-25T00:00:00Z');
  itemLocks.length = 0;
  duringRead = {name: active.id + '.json', run() { upload = store.borrowSource(active.path, 10, null); }};
  await store.sweep();
  assert.equal(Buffer.from(await upload).toString(), 'data');
  assert.equal(Date.parse(JSON.parse(await readFile(active.metadata)).last_used_at), now.getTime());
  assert.ok(itemLocks.length > 0 && itemLocks.every(name => name === active.id + '.lock'));
  assert.ok(extendedAttempts > 0, 'foreground replacement ran while the sweep held the old record open');

  const nativeRecords = handles.openDirectory(active.records);
  try {
    // The source also needs DELETE rights for either rename information class.
    const readonlySource = handles.openReplaceableReadFile(nativeRecords, active.id + '.json');
    const originalRecord = await readFile(active.metadata);
    const originalDestination = await readFile(archived.metadata);
    try {
      assert.throws(() => handles.renameHandle(readonlySource, nativeRecords, archived.id + '.json', true, true));
      assert.deepEqual(await readFile(active.metadata), originalRecord);
      assert.deepEqual(await readFile(archived.metadata), originalDestination);
    } finally { handles.closeHandle(readonlySource); }
  } finally { handles.closeHandle(nativeRecords); }

  // A short reader on an older filesystem releases the target in time for the foreground retry.
  rejectExtendedRename = true;
  const beforeUnsupported = extendedAttempts;
  now = new Date('2026-01-27T00:00:00Z');
  duringRead = {name: active.id + '.json', run() { upload = store.borrowSource(active.path, 10, null); }};
  await store.sweep();
  assert.ok(extendedAttempts > beforeUnsupported);
  assert.equal(Buffer.from(await upload).toString(), 'data');
  assert.equal(Date.parse(JSON.parse(await readFile(active.metadata)).last_used_at), now.getTime());
  assert.equal(await readFile(active.path, 'utf8'), 'data');

  // A long-lived external reader still retains the exact record when classic replacement is unavailable.
  now = new Date('2026-01-28T00:00:00Z');
  const beforeBlocked = await readFile(active.metadata);
  const parent = handles.openDirectory(active.records);
  try {
    const read = handles.openReplaceableReadFile(parent, active.id + '.json');
    try {
      const deleting = handles.openGuardedFile(parent, active.id + '.json');
      handles.closeHandle(deleting);
      assert.equal(Buffer.from(await store.borrowSource(active.path, 10, null)).toString(), 'data');
      assert.deepEqual(await readFile(active.metadata), beforeBlocked);
    } finally { handles.closeHandle(read); }
  } finally { handles.closeHandle(parent); }
  rejectExtendedRename = false;
} finally {
  await rm(base, {recursive: true, force: true, maxRetries: 5});
}
