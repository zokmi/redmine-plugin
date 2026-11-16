import { mock } from 'node:test';
import * as real from '../dist/storage/windows-handles.js';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, dirname, basename } from 'node:path';
import { once } from 'node:events';
import assert from 'node:assert/strict';
let inject = false, renamed = new Set(), recoveryHandle, directoryHandle, mode = process.argv[2];
mock.module(new URL('../dist/storage/windows-handles.js', import.meta.url), { namedExports: {
  ...real,
  verifyNtfs(handle) { if (inject && mode === 'volume') throw new Error('unsupported filesystem'); return real.verifyNtfs(handle); },
  openDurableDirectory(parent, name) { const h = real.openDurableDirectory(parent, name); if (inject && mode === 'directory') directoryHandle = h; return h; },
  openDurableFile(parent, name, operation) { const h = real.openDurableFile(parent, name, operation); if (inject && mode === 'recovery' && name.endsWith('.zip')) recoveryHandle = h; return h; },
  renameHandle(handle, ...args) { real.renameHandle(handle, ...args); if (inject && ((mode === 'record' && args[1].endsWith('.json')) || (mode === 'zip' && args[1].endsWith('.zip')))) renamed.add(handle); },
  flushHandle(handle) { if (inject && (renamed.has(handle) || handle === recoveryHandle || handle === directoryHandle)) throw new Error('injected barrier failure'); return real.flushHandle(handle); },
}});
const {createWindowsAttachmentStore} = await import('../dist/storage/windows-store.js');
const base = await mkdtemp(join(tmpdir(), 'rm-barrier-'));
const parent = real.openDirectory(base), root = real.createPrivateDirectory(parent,'cache'); real.closeHandle(root); real.closeHandle(parent);
try {
  let now = new Date('2026-01-01T00:00:00Z'); const store = createWindowsAttachmentStore({root:join(base,'cache'),clock:() => now});
  const stage = await store.stageDownload('https://example.com','x'); stage.writer.end('data'); await once(stage.writer,'finish'); const original = await store.publish(stage);
  const id = basename(dirname(original)), site = dirname(dirname(dirname(original))), record = join(site,'records',`${id}.json`);
  if (mode === 'recovery') mode = 'record';
  inject = true; now = new Date('2026-01-09T00:00:00Z'); await store.sweep();
  assert.equal((await readFile(original)).toString(),'data'); assert.notEqual(JSON.parse(await readFile(record)).state,'archived');
  if (process.argv[2] === 'recovery') {
    inject = false; renamed.clear(); mode = 'recovery'; inject = true; await store.sweep();
    assert.equal((await readFile(original)).toString(),'data'); assert.equal(JSON.parse(await readFile(record)).state,'archive_published');
  }
} finally { await rm(base,{recursive:true,force:true}); }
