import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { siteStorageKey } from '../dist/storage/site-key.js';
const hash = value => createHash('sha256').update(value).digest('hex');
test('site keys preserve Python normalization for supported URLs', () => {
  for (const [input, normalized] of [
    [' HTTPS://EXAMPLE.COM:443/redmine/// ', 'https://example.com/redmine'],
    ['http://example.com:80/a/../b/', 'http://example.com/a/../b'],
    ['https://[2001:DB8::1]:443/redmine/', 'https://[2001:db8::1]/redmine'],
    ['https://example.com:8443/a%2Fb/', 'https://example.com:8443/a%2Fb'],
  ]) assert.equal(siteStorageKey(input), hash(normalized));
});
test('site keys reject ambiguous inputs without disclosing credentials', () => {
  for (const input of ['https://user:secret@example.com/', 'https://example.com/?', 'https://example.com/#', 'https://example.com:99999/', 'ftp://example.com/', 'https:///x']) {
    assert.throws(() => siteStorageKey(input), error => !error.message.includes('secret'));
  }
});
