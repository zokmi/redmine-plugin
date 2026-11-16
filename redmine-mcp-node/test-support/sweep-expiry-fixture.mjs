import { mock } from 'node:test';
import * as real from '../dist/storage/windows-handles.js';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, dirname, basename } from 'node:path';
import { once } from 'node:events';
import assert from 'node:assert/strict';
let inject = false, archiveHandle;
mock.module(new URL('../dist/storage/windows-handles.js', import.meta.url), {namedExports:{
  ...real,
  openDurableFile(parent,name,mode) { const h = real.openDurableFile(parent,name,mode); if (inject && name.endsWith('.zip')) archiveHandle = h; return h; },
  deleteHandle(h) { real.deleteHandle(h); if (inject && h === archiveHandle) throw new Error('interrupted after archive deletion'); },
}});
const {createWindowsAttachmentStore} = await import('../dist/storage/windows-store.js');
const base = await mkdtemp(join(tmpdir(),'rm-expiry-inject-')); const parent = real.openDirectory(base), root = real.createPrivateDirectory(parent,'cache'); real.closeHandle(root); real.closeHandle(parent);
try {
  let now = new Date('2026-01-01T00:00:00Z'); const store = createWindowsAttachmentStore({root:join(base,'cache'),clock:() => now});
  const stage = await store.stageDownload('https://example.com','x'); stage.writer.end('data'); await once(stage.writer,'finish'); const original = await store.publish(stage);
  const id = basename(dirname(original)), site = dirname(dirname(dirname(original))), record = join(site,'records',`${id}.json`), zip = join(site,'archives',`${id}.zip`);
  now = new Date('2026-01-09T00:00:00Z'); await store.sweep();
  now = new Date('2026-02-08T00:00:00Z'); inject = true; await store.sweep(); inject = false;
  await assert.rejects(readFile(zip),{code:'ENOENT'}); assert.equal(JSON.parse(await readFile(record)).state,'archived');
  await store.sweep(); await assert.rejects(readFile(record),{code:'ENOENT'});
} finally { await rm(base,{recursive:true,force:true}); }
