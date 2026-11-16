import test from 'node:test';
import assert from 'node:assert/strict';
import { scheduleStorage } from '../dist/storage-scheduler.js';

test('storage sweep starts once, throttles triggers and aborts on close', async () => {
  let now = 0, calls = 0, signal;
  const store = { async sweep(_date, s) { calls++; signal = s; }, async stageDownload() {}, async publish() {}, async discard() {}, async borrowSource() {} };
  const scheduler = scheduleStorage(store, () => now);
  await new Promise(r => setImmediate(r)); assert.equal(calls, 1);
  await scheduler.store.borrowSource(); assert.equal(calls, 1);
  now = 3600001; await scheduler.store.borrowSource(); await new Promise(r => setImmediate(r)); assert.equal(calls, 2);
  scheduler.close(); assert.equal(signal.aborted, true);
  now += 3600001; await scheduler.store.borrowSource(); assert.equal(calls, 2);
});
