import assert from 'node:assert/strict';
import test from 'node:test';
import { setImmediate } from 'node:timers/promises';
import { fanOut } from '../dist/sites.js';
import { registerMetadata } from '../dist/tools/metadata.js';

function trackers() {
  const requests = [];
  const site = { settings: { name: 'main' }, transport: {
    json(_method, _path, { signal }) {
      const { promise, resolve, reject } = Promise.withResolvers();
      requests.push({ signal, resolve, reject });
      return promise;
    },
  } };
  const registry = { resolve: () => new Map([['main', site]]) };
  function session() {
    const handlers = new Map();
    registerMetadata({ register: ({ name }, handler) => handlers.set(name, handler) }, registry);
    return (signal = new AbortController().signal) => handlers.get('list_trackers')({ site: 'main' }, signal);
  }
  return { requests, read: session(), otherSession: session() };
}

const ok = (name) => ({ status: 200, data: { trackers: [{ id: 1, name }] } });

test('concurrent metadata readers share one GET; a cancelled reader cannot cancel its peers', async () => {
  const { read, otherSession, requests } = trackers();
  const first = new AbortController();
  const cancelled = read(first.signal);
  const others = Array.from({ length: 11 }, () => otherSession());
  await setImmediate();
  first.abort();
  await assert.rejects(cancelled, { name: 'AbortError' });
  for (const request of requests) request.resolve(ok('Bug'));
  const results = await Promise.all(others);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].signal.aborted, false);
  for (const result of results) assert.equal(result.sites.main.trackers[0].name, 'Bug');
  await read();
  assert.equal(requests.length, 1);
});

test('abandoning a metadata GET permits a retry even before the old transport finishes', async () => {
  const { read, requests } = trackers();
  const caller = new AbortController();
  const abandoned = read(caller.signal);
  await setImmediate();
  caller.abort();
  await assert.rejects(abandoned, { name: 'AbortError' });
  assert.ok(requests[0].signal.aborted);
  const next = read();
  await setImmediate();
  assert.equal(requests.length, 2);
  requests[1].resolve(ok('Current'));
  assert.equal((await next).sites.main.trackers[0].name, 'Current');
  requests[0].resolve(ok('Old'));
  await setImmediate();
  assert.equal((await read()).sites.main.trackers[0].name, 'Current');
  assert.equal(requests.length, 2);
});

test('simultaneous metadata failures share the upstream request and remain retryable', async () => {
  const { read, requests } = trackers();
  const group = Promise.all([read(), read()]);
  await setImmediate();
  for (const request of requests) request.reject(new Error('upstream failure'));
  for (const result of await group) assert.ok(result.sites.main.error);
  assert.equal(requests.length, 1);
  const retry = read();
  await setImmediate();
  requests[1].resolve(ok('Recovered'));
  assert.equal((await retry).sites.main.trackers[0].name, 'Recovered');
});

test('a batch deadline preserves completed sites, aborts active requests and leaves queued sites unstarted', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const names = Array.from({ length: 12 }, (_, i) => 'site' + i);
  const started = new Map();
  const result = fanOut(new Map(names.map((name) => [name, { name }])), async (site, signal) => {
    started.set(site.name, signal);
    if (site.name === 'site0') return 7;
    return new Promise(() => {});
  }, { signal: new AbortController().signal, timeoutMs: 60_000 });
  await setImmediate();
  assert.equal(started.size, 9);
  t.mock.timers.tick(25_000);
  await setImmediate();
  assert.equal(started.get('site1').aborted, true);
  const { payload, foundIn } = await result;
  assert.deepEqual(foundIn, ['site0']);
  assert.deepEqual(Object.keys(payload), names);
  assert.equal(payload.site0, 7);
  assert.match(payload.site1.error, /已開始/);
  assert.match(payload.site11.error, /尚未開始/);
  assert.equal(started.size, 9);
});

test('cancelling a batch stops active requests and never starts queued sites', async () => {
  const controller = new AbortController();
  const started = [];
  const sites = new Map(Array.from({ length: 12 }, (_, index) => [String(index), { index }]));
  const query = fanOut(sites, (_site, signal) => {
    started.push(signal);
    return new Promise(() => {});
  }, { signal: controller.signal });
  await setImmediate();
  controller.abort();
  await assert.rejects(query, { name: 'AbortError' });
  assert.equal(started.length, 8);
  assert.ok(started.every((signal) => signal.aborted));
});
