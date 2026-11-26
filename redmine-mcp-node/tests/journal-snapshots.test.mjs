import assert from 'node:assert/strict';
import test from 'node:test';
import { journalSnapshots } from '../dist/journal-snapshots.js';

test('snapshot entry, byte and age limits keep the current page available', async (t) => {
  const cache = journalSnapshots({});
  const site = {};
  t.after(() => cache.invalidate(site));
  let now = 0;
  t.mock.method(performance, 'now', () => now);
  const key = { site, issueId: 42, includes: 'journals' };
  const signal = new AbortController().signal;
  const format = () => ({journals_next_offset: 1});
  const save = async (notes = '') => cache.page(key, null, signal,
    async () => ({journals: [{id: 1, notes}, {id: 2}]}), format);
  const read = async token => cache.page(key, token, signal, () => assert.fail('unexpected GET'), format);

  const tokens = [];
  for (let i = 0; i < 33; i++) tokens.push((await save()).journals_snapshot);
  await assert.rejects(read(tokens[0]), /快照/);
  assert.equal((await read(tokens[1])).journals_snapshot, tokens[1]);
  const large = '字'.repeat(9 * 1024 * 1024);
  const first = (await save(large)).journals_snapshot;
  const second = (await save(large)).journals_snapshot;
  assert.ok(first && second);
  await assert.rejects(read(first), /快照/);
  assert.deepEqual(await save(large + large), {journals_next_offset: 1});
  assert.equal((await read(second)).journals_snapshot, second);
  now = 60_001;
  await assert.rejects(read(second), /快照/);
});
