import { test } from 'node:test';
import assert from 'node:assert/strict';
import { formatIssueDetail } from '../dist/issue-formatting.js';
import { wrapUntrusted } from '../dist/formatting.js';

test('long previews retain Python code-point lengths and prefixes without a full character array', (t) => {
  const prefix = 'x'.repeat(198) + '<😀';
  const description = prefix + '𠮷'.repeat(400) + '\ud800' + '尾'.repeat(100_000);
  const from = Array.from;
  t.mock.method(Array, 'from', function (input, ...args) {
    assert.notEqual(input === description, true, 'allocated an array for the whole description');
    return Reflect.apply(from, this, [input, ...args]);
  });
  const detail = formatIssueDetail({
    description,
    journals: [{ details: [{ old_value: description, new_value: '😀'.repeat(500) }] }],
  }, { summary: true, journals_limit: 1, journals_offset: 0 });
  assert.deepEqual(detail.description, { length: 100_601, preview: wrapUntrusted(prefix + '𠮷'.repeat(400)), truncated: true });
  assert.deepEqual(detail.journals[0].details[0].原值, { length: 100_601, preview: wrapUntrusted(prefix), truncated: true });
  assert.equal(detail.journals[0].details[0].新值, wrapUntrusted('😀'.repeat(500)));
});
