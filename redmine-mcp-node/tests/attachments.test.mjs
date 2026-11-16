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

async function directHandlers({ names = ['one'], json, upload, download, store, max = 100 } = {}) {
  const { registerAttachments } = await import('../dist/tools/attachments.js');
  const handlers = new Map();
  const settings = Object.fromEntries(names.map((name) => [name, { settings: { name, url: `https://${name}.invalid`, maxAttachmentBytes: max, attachmentUploadRoot: 'allowed' }, transport: { json: (...args) => json(name, ...args), upload, download } }]));
  const sites = { resolve: () => new Map(Object.entries(settings)), resolveForWrite: () => ['one', settings.one] };
  registerAttachments({ register(def, handler) { handlers.set(def.name, handler); } }, sites, store ?? {});
  return handlers;
}

test('base64 size check occurs before scanning excessive invalid tail', async () => {
  const handlers = await directHandlers({ max: 8 });
  await assert.rejects(handlers.get('upload_attachment')({ file_path: null, content_base64: 'A'.repeat(10000) + '!', site: null, filename: null }, new AbortController().signal), /超過/);
});

test('file upload recognizes legacy MIME and returns image references', async () => {
  let count = 0;
  const handlers = await directHandlers({ store: { async borrowSource(path, max, root) { assert.equal(root, 'allowed'); return Buffer.from('file'); } }, upload: async () => { count++; return { data: { upload: { token: 't' } } }; } });
  for (const [file_path, expected] of [['x.svg', 'image/svg+xml'], ['x.tiff', 'image/tiff'], ['x.csv', 'text/csv']]) {
    const result = await handlers.get('upload_attachment')({ file_path, content_base64: null, site: null, filename: null }, new AbortController().signal);
    assert.equal(result.content_type, expected); assert.equal(result.token, 't');
    assert.equal(result.markdown, expected.startsWith('image/') ? `![](${file_path})` : undefined);
  }
  assert.equal(count, 3);
});

test('no hits retain per-site error details', async () => {
  const { TransportError } = await import('../dist/transport.js');
  const handlers = await directHandlers({ names: ['one', 'two'], json: async (name) => { throw new TransportError(name === 'one' ? 'not_found' : 'network', name === 'one' ? 'missing' : 'connection failed'); } });
  await assert.rejects(handlers.get('download_attachment')({ attachment_id: 2, site: null }, new AbortController().signal), /two：connection failed/);
});

test('cancel after download discards without publishing', async () => {
  let discarded = 0, published = 0; const controller = new AbortController();
  const handlers = await directHandlers({ json: async () => ({ data: { attachment: { content_url: 'https://one.invalid/file', filename: 'file' } } }), download: async () => { controller.abort(); return 1; }, store: { async stageDownload() { return { writer: {} }; }, async publish() { published++; return 'path'; }, async discard() { discarded++; } } });
  await assert.rejects(handlers.get('download_attachment')({ attachment_id: 2, site: null }, controller.signal));
  assert.equal(discarded, 1); assert.equal(published, 0);
});

test('base64 accepts Python whitespace and data URI', async () => {
  const handlers = await directHandlers({ upload: async () => ({ data: { upload: { token: 't' } } }) });
  const result = await handlers.get('upload_attachment')({ file_path: null, content_base64: '\u0085data:image/png;base64, iVBO\u001cRw0K Ggo=\n', site: null, filename: 'x' }, new AbortController().signal);
  assert.equal(result.filename, 'x.png'); assert.equal(result.bytes, 8);
});

test('cancel during staging skips transport and discards', async () => {
  let discarded = 0, downloads = 0; const controller = new AbortController();
  const handlers = await directHandlers({ json: async () => ({ data: { attachment: { content_url: 'https://one.invalid/file' } } }), download: async () => { downloads++; return 1; }, store: { async stageDownload() { controller.abort(); return { writer: {} }; }, async discard() { discarded++; } } });
  await assert.rejects(handlers.get('download_attachment')({ attachment_id: 2, site: null }, controller.signal));
  assert.equal(discarded, 1); assert.equal(downloads, 0);
});
