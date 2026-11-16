import { mock } from 'node:test';
import * as real from '../dist/storage/windows-handles.js';
import { mkdtemp, readdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import assert from 'node:assert/strict';
let staged;
mock.module(new URL('../dist/storage/windows-handles.js', import.meta.url), { namedExports: {
  ...real,
  openFile(...args) { const h = real.openFile(...args); if (args[1] === '.download.part' && args[2] === 'create') staged = h; return h; },
  verifyPrivateHandle(handle, ...args) { if (handle === staged) throw new Error('Injected unsafe ACL'); return real.verifyPrivateHandle(handle, ...args); },
}});
const {createWindowsAttachmentStore} = await import('../dist/storage/windows-store.js');
const base = await mkdtemp(join(tmpdir(), 'rm-stage-acl-'));
const parent = real.openDirectory(base), root = real.createPrivateDirectory(parent, 'cache'); real.closeHandle(root); real.closeHandle(parent);
try {
  const store = createWindowsAttachmentStore({root:join(base,'cache')});
  await assert.rejects(store.stageDownload('https://example.com','x'), /Injected unsafe ACL/);
  const site = (await readdir(join(base,'cache')))[0], item = (await readdir(join(base,'cache',site,'active')))[0];
  assert.deepEqual(await readdir(join(base,'cache',site,'active',item)), []);
} finally { await rm(base,{recursive:true,force:true}); }
