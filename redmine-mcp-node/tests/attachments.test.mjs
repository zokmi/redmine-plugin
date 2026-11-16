import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js';
import { createServer } from '../dist/server.js';

test('base64 image upload yields token and image reference', async (t) => {
  let uploads = 0;
  const settings = { sites: new Map([['one', { name: 'one', url: 'https://one.invalid', apiKey: 'key', maxAttachmentBytes: 100, allowInsecureHttp: false, attachmentUploadRoot: null }]]) };
  const server = createServer(settings, { transportFactory: () => ({ close() {}, async upload(name, bytes) { uploads++; assert.equal(name, 'image.png'); assert.equal(bytes.length, 8); return { status: 201, data: { upload: { token: 'token' } } }; } }) });
  const client = new Client({ name: 'test', version: '1' });
  const [a, b] = InMemoryTransport.createLinkedPair();
  t.after(async () => { await client.close(); await server.close(); });
  await server.connect(b); await client.connect(a);
  const result = await client.callTool({ name: 'upload_attachment', arguments: { content_base64: 'iVBORw0KGgo=', filename: 'image.jpg' } });
  assert.equal(result.isError ?? false, false, JSON.stringify(result));
  assert.equal(result.structuredContent.token, 'token');
  assert.equal(result.structuredContent.filename, 'image.png');
  assert.equal(result.structuredContent.markdown, '![](image.png)');
  assert.equal(uploads, 1);
});

test('failed download discards staged output', async (t) => {
  const { Writable } = await import('node:stream');
  let discarded = 0, published = 0;
  const settings = { sites: new Map([['one', { name: 'one', url: 'https://one.invalid', apiKey: 'key', maxAttachmentBytes: 100, allowInsecureHttp: false, attachmentUploadRoot: null }]]) };
  const store = { async stageDownload() { return { writer: new Writable({ write(chunk, enc, cb) { cb(); } }) }; }, async discard() { discarded++; }, async publish() { published++; return 'bad'; } };
  const server = createServer(settings, { attachmentStore: store, transportFactory: () => ({ close() {}, async json() { return { status: 200, data: { attachment: { filename: 'x.png', content_url: 'https://one.invalid/x' } } }; }, async download() { throw new Error('interrupted'); } }) });
  const client = new Client({ name: 'test', version: '1' }); const [a, b] = InMemoryTransport.createLinkedPair();
  t.after(async () => { await client.close(); await server.close(); }); await server.connect(b); await client.connect(a);
  const result = await client.callTool({ name: 'download_attachment', arguments: { attachment_id: 1 } });
  assert.equal(result.isError, true); assert.equal(discarded, 1); assert.equal(published, 0);
});

test('upload rejects non-image base64 and over-limit images before transport', async (t) => {
  let uploads = 0;
  const settings = { sites: new Map([['one', { name: 'one', url: 'https://one.invalid', apiKey: 'key', maxAttachmentBytes: 7, allowInsecureHttp: false, attachmentUploadRoot: null }]]) };
  const server = createServer(settings, { transportFactory: () => ({ close() {}, async upload() { uploads++; } }) });
  const client = new Client({ name: 'test', version: '1' }); const [a, b] = InMemoryTransport.createLinkedPair();
  t.after(async () => { await client.close(); await server.close(); }); await server.connect(b); await client.connect(a);
  for (const content_base64 of ['dGVzdA==', 'iVBORw0KGgo=', 'not-base64']) {
    assert.equal((await client.callTool({ name: 'upload_attachment', arguments: { content_base64 } })).isError, true);
  }
  assert.equal(uploads, 0);
});
