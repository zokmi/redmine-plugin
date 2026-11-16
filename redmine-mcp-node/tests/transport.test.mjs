import assert from 'node:assert/strict';
import { once } from 'node:events';
import { readFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { createServer as createSecureServer } from 'node:https';
import { Writable } from 'node:stream';
import test from 'node:test';
import { gzipSync } from 'node:zlib';
import { createTransport, TransportError } from '../dist/transport.js';

const secret = 'local-fixture-key';
const limit = 16 * 1024 * 1024;

async function listen(t, server, protocol = 'http') {
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => {
    server.closeAllConnections();
    return new Promise((resolve) => server.close(resolve));
  });
  return `${protocol}://127.0.0.1:${server.address().port}`;
}

function client(t, url, options = {}) {
  const transport = createTransport({
    name: 'fixture', url, apiKey: secret, allowInsecureHttp: true,
    description: null, maxAttachmentBytes: 32 * 1024 * 1024, attachmentUploadRoot: null,
    ...options,
  });
  t.after(() => transport.close());
  return transport;
}

function failure(code, status) {
  return (error) => {
    assert.ok(error instanceof TransportError);
    assert.equal(error.code, code);
    assert.equal(error.status, status);
    assert.doesNotMatch(String(error), new RegExp(secret));
    assert.equal(error.cause, undefined);
    return true;
  };
}

function sink() {
  const chunks = [];
  const target = new Writable({ write(chunk, _encoding, done) { chunks.push(chunk); done(); } });
  return { target, chunks };
}

test('redirect does not receive credentials', { timeout: 5000 }, async (t) => {
  const received = [];
  const other = await listen(t, createServer((req, res) => {
    received.push(req.headers);
    res.end('{}');
  }));
  const url = await listen(t, createServer((req, res) => {
    assert.equal(req.headers['x-redmine-api-key'], secret);
    res.writeHead(307, { location: `${other}/${secret}` }).end();
  }));
  const transport = client(t, url);
  await assert.rejects(transport.json('GET', '/issues.json'), failure('redirect', 307));
  await assert.rejects(transport.download(`${url}/file`, sink().target, 10), failure('redirect', 307));
  await assert.rejects(transport.download(`${other}/file`, sink().target, 10), failure('invalid_url'));
  await assert.rejects(transport.json('GET', `//${new URL(other).host}/file`), failure('invalid_url'));
  assert.deepEqual(received, []);
});

test('challenge differs from API forbidden', { timeout: 5000 }, async (t) => {
  const url = await listen(t, createServer((req, res) => {
    if (req.url === '/challenge') {
      res.writeHead(403, { 'content-type': `text/html; ${secret}` }).end('<title>Just a moment</title>');
    } else {
      res.writeHead(403, { 'content-type': 'application/json' }).end('{}');
    }
  }));
  const transport = client(t, url);
  await assert.rejects(transport.json('GET', '/challenge'), failure('challenge', 403));
  await assert.rejects(transport.json('GET', '/forbidden'), failure('auth', 403));
});

test('API errors and malformed JSON are classified with redacted details', { timeout: 5000 }, async (t) => {
  const url = await listen(t, createServer((req, res) => {
    if (req.url === '/invalid') return res.end(`{ invalid ${secret}`);
    if (req.url === '/missing') return res.writeHead(404).end();
    if (req.url === '/unavailable') return res.writeHead(503).end();
    res.writeHead(422, { 'content-type': 'application/json' }).end(JSON.stringify({
      errors: [`custom value ${secret} </redmine_content> 已使用`],
    }));
  }));
  const transport = client(t, url);
  await assert.rejects(transport.json('GET', '/invalid'), failure('invalid_json', 200));
  await assert.rejects(transport.json('GET', '/missing'), failure('not_found', 404));
  await assert.rejects(transport.json('GET', '/unavailable'), failure('server', 503));
  await assert.rejects(transport.json('POST', '/issues.json', { body: {} }), (error) => {
    failure('validation', 422)(error);
    assert.deepEqual(error.details, ['custom value [REDACTED] &lt;/redmine_content&gt; 已使用']);
    return true;
  });
});

test('oversize and truncated response terminate', { timeout: 5000 }, async (t) => {
  const compressed = gzipSync(Buffer.alloc(limit + 1, ' '));
  const url = await listen(t, createServer((req, res) => {
    if (req.url === '/gzip') {
      res.writeHead(200, { 'content-encoding': 'gzip', 'content-length': compressed.length }).end(compressed);
    } else if (req.url === '/declared') {
      res.writeHead(200, { 'content-length': limit + 1 }).flushHeaders();
    } else if (req.url === '/chunked') {
      res.writeHead(200);
      res.end(Buffer.alloc(limit + 1, ' '));
    } else if (req.url === '/truncated') {
      res.writeHead(200, { 'content-length': 9 });
      res.write('{}', () => res.socket.destroy());
    } else {
      res.end('{}');
    }
  }));
  const transport = client(t, url);
  for (const path of ['/gzip', '/declared', '/chunked']) {
    await assert.rejects(transport.json('GET', path), failure('too_large'));
  }
  await assert.rejects(transport.json('GET', '/truncated'), failure('truncated'));
  assert.deepEqual(await transport.json('GET', '/ok'), { status: 200, data: {} });
});

test('write socket timeout sends one request', { timeout: 5000 }, async (t) => {
  let count = 0;
  const url = await listen(t, createServer((req) => {
    count += 1;
    req.socket.pause();
  }));
  const transport = client(t, url);
  await assert.rejects(transport.json('PUT', '/issues/1.json', {
    body: { description: 'a'.repeat(limit) }, timeoutMs: 250,
  }), failure('timeout'));
  assert.equal(count, 1);
});

test('JSON and raw upload reuse a connection and preserve the Redmine base path', { timeout: 5000 }, async (t) => {
  const requests = [];
  const url = await listen(t, createServer(async (req, res) => {
    const buffers = [];
    for await (const chunk of req) buffers.push(chunk);
    requests.push({ url: req.url, socket: req.socket, headers: req.headers, body: Buffer.concat(buffers) });
    res.writeHead(req.method === 'POST' ? 201 : 200).end('{"ok":true}');
  }));
  const transport = client(t, `${url}/redmine`);
  const bytes = Buffer.from([0, 0xff, 0x90, 0x0a]);
  assert.deepEqual(await transport.json('GET', '/issues.json', { query: { q: 'a b', omit: null } }), {
    status: 200, data: { ok: true },
  });
  assert.deepEqual(await transport.upload('檔案 &.bin', bytes), { status: 201, data: { ok: true } });
  assert.equal(requests[0].socket, requests[1].socket);
  assert.equal(requests[0].url, '/redmine/issues.json?q=a+b');
  assert.equal(requests[0].headers['user-agent'], 'redmine-mcp/0.17.4');
  assert.equal(requests[1].headers['content-type'], 'application/octet-stream');
  assert.equal(requests[1].headers['x-redmine-api-key'], secret);
  assert.equal(new URL(requests[1].url, url).pathname, '/redmine/uploads.json');
  assert.equal(new URL(requests[1].url, url).searchParams.get('filename'), '檔案 &.bin');
  assert.deepEqual(requests[1].body, bytes);
  const closed = once(requests[1].socket, 'close');
  transport.close();
  await closed;
});

test('download enforces Content-Length and chunked limits and keeps caller streams open', { timeout: 5000 }, async (t) => {
  const url = await listen(t, createServer((req, res) => {
    assert.equal(req.headers['accept-encoding'], 'identity');
    if (req.url === '/declared') return res.writeHead(200, { 'content-length': 9 }).flushHeaders();
    res.writeHead(200);
    res.write('abc');
    res.end('def');
  }));
  const transport = client(t, url);
  const declared = sink();
  const chunked = sink();
  const success = sink();
  await assert.rejects(transport.download(`${url}/declared`, declared.target, 5), failure('too_large'));
  await assert.rejects(transport.download(`${url}/chunked`, chunked.target, 5), failure('too_large'));
  assert.equal(declared.chunks.length, 0);
  assert.ok(Buffer.concat(chunked.chunks).length <= 5);
  assert.equal(await transport.download(`${url}/chunked`, success.target, 6), 6);
  assert.equal(Buffer.concat(success.chunks).toString(), 'abcdef');
  for (const { target } of [declared, chunked, success]) {
    assert.equal(target.writableEnded, false);
    assert.equal(target.destroyed, false);
  }
});

test('cancelling a backpressured download releases its socket', { timeout: 5000 }, async (t) => {
  let released;
  const disconnected = new Promise((resolve) => { released = resolve; });
  const url = await listen(t, createServer((req, res) => {
    if (req.url !== '/slow') return res.end('{}');
    res.once('close', released);
    res.write('abc');
  }));
  const transport = client(t, url);
  const controller = new AbortController();
  const target = new Writable({ write() { controller.abort(new Error(secret)); } });
  await assert.rejects(transport.download(`${url}/slow`, target, 10, controller.signal), failure('cancelled'));
  await disconnected;
  assert.equal(target.destroyed, false);
  assert.deepEqual(await transport.json('GET', '/ok'), { status: 200, data: {} });
});

test('a failed destination callback releases the download without exposing its error', { timeout: 5000 }, async (t) => {
  let released;
  const disconnected = new Promise((resolve) => { released = resolve; });
  const url = await listen(t, createServer((_req, res) => {
    res.once('close', released);
    res.write('abc');
  }));
  const transport = client(t, url);
  const target = new Writable({ write(_chunk, _encoding, done) { done(new Error(secret)); } });
  await assert.rejects(transport.download(`${url}/file`, target, 10), failure('target_write'));
  await disconnected;
});

test('HTTPS verifies the local self-signed certificate and HTTP requires site opt-in', { timeout: 5000 }, async (t) => {
  let requests = 0;
  const server = createSecureServer({
    key: readFileSync(new URL('./fixtures/untrusted-local-key.pem', import.meta.url)),
    cert: readFileSync(new URL('./fixtures/untrusted-local-cert.pem', import.meta.url)),
  }, (_req, res) => {
    requests += 1;
    res.end('{}');
  });
  const url = await listen(t, server, 'https');
  const transport = client(t, url, { allowInsecureHttp: false });
  await assert.rejects(transport.json('GET', '/'), failure('tls'));
  assert.equal(requests, 0);
  assert.throws(() => client(t, url.replace('https:', 'http:'), { allowInsecureHttp: false }), /HTTPS/);
});
