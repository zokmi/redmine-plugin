import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseItemRecord } from '../dist/storage/record.js';

// Produced by the baseline Python ItemRecord.to_json().
const fixture = JSON.parse(readFileSync(new URL('./fixtures/storage-v1-python.json', import.meta.url), 'utf8'));
test('Python schema v1 record round-trips without losing microseconds', () => {
  assert.deepEqual(parseItemRecord(fixture), fixture);
});
test('rejects records unsafe to derive managed paths from', () => {
  for (const patch of [{filename:'../x'}, {filename:'x:y'}, {item_id:'../x'}, {site_key:'A'.repeat(64)}, {size:-1}, {state:'archived'}, {created_at:'2026-02-30T00:00:00+00:00'}, {last_used_at:'2026-11-16T12:34:56+01:00'}]) {
    assert.throws(() => parseItemRecord({...fixture, ...patch}));
  }
});
