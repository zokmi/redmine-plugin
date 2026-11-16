import { mock } from 'node:test';
import * as real from '../dist/storage/windows-handles.js';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, dirname, basename } from 'node:path';
import { once } from 'node:events';
import assert from 'node:assert/strict';
let temp, inject = false;
mock.module(new URL('../dist/storage/windows-handles.js', import.meta.url), { namedExports: {
  ...real,
  openFile(...args) { const h = real.openFile(...args); if (args[1].startsWith('.record-')) temp = h; return h; },
  verifyPrivateHandle(handle, ...args) { if (inject && process.argv[2] !== 'io' && handle === temp) throw new Error('Injected unsafe ACL'); return real.verifyPrivateHandle(handle, ...args); },
  writeHandle(handle, data) { if (inject && process.argv[2] === 'io' && handle === temp) throw new Error('Cannot write file (112)'); return real.writeHandle(handle, data); },
}});
const {createWindowsAttachmentStore} = await import('../dist/storage/windows-store.js');
const base = await mkdtemp(join(tmpdir(), 'rm-record-acl-'));
const parent = real.openDirectory(base), root = real.createPrivateDirectory(parent, 'cache'); real.closeHandle(root); real.closeHandle(parent);
try {
  const store = createWindowsAttachmentStore({root:join(base,'cache')});
  const stage = await store.stageDownload('https://example.com','x'); stage.writer.end('data'); await once(stage.writer,'finish'); const published = await store.publish(stage);
  const item = basename(dirname(published)), records = join(dirname(dirname(dirname(published))),'records'), record = join(records,`${item}.json`), before = await readFile(record);
  inject = true;
  if (process.argv[2] === 'io') assert.equal(Buffer.from(await store.borrowSource(published,100,null)).toString(),'data');
  else await assert.rejects(store.borrowSource(published,100,null), /Injected unsafe ACL/);
  assert.deepEqual(await readFile(record),before);
  assert.equal((await readFile(published)).toString(),'data');
} finally { await rm(base,{recursive:true,force:true}); }
