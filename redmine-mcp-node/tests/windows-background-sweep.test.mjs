import assert from 'node:assert/strict';
import { once } from 'node:events';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, dirname, join } from 'node:path';
import test from 'node:test';
import { startRuntimeStorage } from '../dist/runtime-storage.js';

test('Windows runtime archives expired items on a separate thread and cancels a pending sweep on close', {
  skip: process.platform !== 'win32',
  timeout: 30000,
}, async t => {
  const base = await mkdtemp(join(tmpdir(), 'redmine-background-sweep-'));
  t.after(() => rm(base, {recursive: true, force: true, maxRetries: 5}));
  const root = join(base, 'cache');
  const { provisionStorageRoot } = await import('../dist/storage-preflight.js');
  const { createWindowsAttachmentStore } = await import('../dist/storage/windows-store.js');
  provisionStorageRoot(root);
  const oldStore = createWindowsAttachmentStore({root, clock: () => new Date('2020-01-01T00:00:00Z')});
  async function publish(name) {
    const staged = await oldStore.stageDownload('https://example.test', name);
    const finished = once(staged.writer, 'finish');
    staged.writer.end('test bytes');
    await finished;
    const path = await oldStore.publish(staged);
    const item = dirname(path);
    return join(dirname(dirname(item)), 'records', basename(item) + '.json');
  }
  const record = await publish('first.txt');
  const previous = process.env.REDMINE_MCP_STORAGE_ROOT;
  process.env.REDMINE_MCP_STORAGE_ROOT = root;
  t.after(() => {
    if (previous === undefined) delete process.env.REDMINE_MCP_STORAGE_ROOT;
    else process.env.REDMINE_MCP_STORAGE_ROOT = previous;
  });
  // Production unrefs maintenance. Keep this otherwise-idle test alive until its exit event.
  const keepAlive = setInterval(() => {}, 30000);
  t.after(() => clearInterval(keepAlive));
  const launched = once(process, 'worker');
  const runtime = await startRuntimeStorage();
  t.after(() => runtime.close());
  const [worker] = await launched;
  const [status] = await once(worker, 'exit');
  assert.equal(status, 0);
  assert.equal(JSON.parse(await readFile(record, 'utf8')).state, 'archived');
  runtime.close();

  const another = await publish('another.txt');
  const launchedAgain = once(process, 'worker');
  const closing = await startRuntimeStorage();
  closing.close();
  const [cancelledWorker] = await launchedAgain;
  const [exitCode] = await once(cancelledWorker, 'exit');
  assert.equal(exitCode, 0);
  assert.equal(JSON.parse(await readFile(another, 'utf8')).state, 'active');
});
