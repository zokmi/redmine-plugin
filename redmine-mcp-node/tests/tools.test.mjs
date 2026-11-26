import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { setImmediate } from 'node:timers/promises';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js';
import { createServer } from '../dist/server.js';
import { createSiteRegistry } from '../dist/sites.js';
import { TransportError } from '../dist/transport.js';

const fixture = JSON.parse(await readFile(new URL('./fixtures/v0.17.4-tools.json', import.meta.url), 'utf8'));
const { version } = JSON.parse(await readFile(new URL('../package.json', import.meta.url), 'utf8'));
const wrapped = (text) => '<redmine_content untrusted="true">' + text + '</redmine_content>';
const ok = (data = {}) => ({ status: 200, data });
const secret = 'fixture-api-key';
const baselineIssue = {
  project_id: 'demo', subject: '要查的項目', description: '此處是用於驗證工具的完整概述', due_date: '2028-02-29',
};

function settings(names) {
  return { sites: new Map(names.map((name) => [name, {
    name, description: null, url: 'https://' + name + '.invalid/redmine', apiKey: secret,
    maxAttachmentBytes: 2_000, allowInsecureHttp: false, attachmentUploadRoot: null,
  }])) };
}

async function connect(t, names, request, options = {}) {
  const server = createServer(settings(names), {
    registry: options.registry,
    transportFactory: ({ name }) => ({
      json: (method, path, opts = {}) => request(name, method, path, opts),
      close: () => options.closed?.push(name),
    }),
    fanoutTimeoutMs: options.fanoutTimeoutMs,
  });
  const client = new Client({ name: 'redmine-fixture', version: '1.0.0' });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  t.after(async () => {
    await client.close();
    await server.close();
  });
  await server.connect(serverTransport);
  await client.connect(clientTransport);
  return client;
}

async function call(client, name, args = {}) {
  const result = await client.callTool({ name, arguments: args });
  assert.equal(result.isError ?? false, false, JSON.stringify(result));
  assert.deepEqual(JSON.parse(result.content[0].text), result.structuredContent);
  return result.structuredContent;
}

async function error(client, name, args, pattern) {
  const result = await client.callTool({ name, arguments: args });
  assert.equal(result.isError, true, JSON.stringify(result));
  assert.match(result.content[0].text, pattern);
  assert.ok(!JSON.stringify(result).includes(secret));
}

// Only generated schema titles are ignored. The advertised contract includes defaults and nullable types.
function withoutSchemaTitles(value) {
  if (Array.isArray(value)) return value.map(withoutSchemaTitles);
  if (value === null || typeof value !== 'object') return value;
  return Object.fromEntries(Object.entries(value)
    .filter(([key]) => key !== 'title')
    .map(([key, field]) => [key, withoutSchemaTitles(field)]));
}

test('MCP tools/list matches all sixteen migrated v0.17.4 tool contracts', async (t) => {
  const closed = [];
  const client = await connect(t, ['north', 'south'], () => assert.fail('Unexpected network request'), { closed });
  const { tools } = await client.listTools();
  const expected = fixture.tools;
  assert.deepEqual(tools.map((tool) => tool.name), expected.map((tool) => tool.name));
  // v0.19 adds exactly one optional input. Preserve the captured fixture and compare every other field.
  const current = withoutSchemaTitles(tools);
  const schema = current.find(({ name }) => name === 'get_issue').inputSchema;
  const { journals_snapshot: snapshot } = schema.properties;
  assert.deepEqual(snapshot?.anyOf, [{ type: 'string' }, { type: 'null' }]);
  assert.equal(snapshot.default, null);
  assert.match(snapshot.description, /journals_next_offset/);
  delete schema.properties.journals_snapshot;
  assert.deepEqual(current, withoutSchemaTitles(expected));
  assert.deepEqual(client.getServerVersion(), { name: 'redmine', version });
  assert.match(client.getInstructions(), /已設定多個站台：north、south/);
  assert.deepEqual(await call(client, 'list_sites'), { configured_sites: [
    { name: 'north', url: 'https://north.invalid/redmine', description: null },
    { name: 'south', url: 'https://south.invalid/redmine', description: null },
  ] });
  await client.close();
  assert.deepEqual(closed, ['north', 'south']);
});

test('multi-site write requires site', async (t) => {
  const client = await connect(t, ['north', 'south'], () => assert.fail('Unrouted write'));
  for (const [name, args] of [
    ['create_issue', baselineIssue],
    ['update_issue', { issue_id: 7, subject: 'changed' }],
    ['add_issue_note', { issue_id: 7, notes: 'test' }],
    ['create_project', { name: 'Demo', identifier: 'demo' }],
  ]) {
    await error(client, name, args, /已設定多個站台，寫入前請明寫 site（可用：north、south）/);
    await error(client, name, { ...args, site: 'absent' }, /沒有名為 'absent' 的站台/);
  }
});

test('fanout preserves successful sites on timeout and runs at most eight at once', async (t) => {
  const names = Array.from({ length: 9 }, (_, index) => 'site' + (index + 1));
  const started = [];
  const pending = new Map();
  const client = await connect(t, names, (site, method, path, opts) => {
    assert.equal(method, 'GET');
    assert.equal(path, '/issues.json');
    assert.equal(opts.query.limit, 23);
    started.push(site);
    return new Promise((resolve, reject) => {
      pending.set(site, () => resolve(ok({
        issues: [{ id: 3, subject: '<name>', assigned_to: { id: 5, name: '<person>' } }],
        total_count: 1, limit: 23, offset: 0,
      })));
      opts.signal.addEventListener('abort', () => reject(new TransportError('cancelled', 'cancelled')), { once: true });
    });
  }, { fanoutTimeoutMs: 100 });
  const response = call(client, 'list_issues', { limit: 100 });
  await setImmediate();
  assert.deepEqual(started, names.slice(0, 8));
  for (const site of names.slice(1, 8)) pending.get(site)();
  await setImmediate();
  assert.deepEqual(started, names);
  pending.get('site9')();
  const result = await response;
  assert.deepEqual(Object.keys(result.sites), names);
  assert.match(result.sites.site1.error, /未回應（查詢逾時）/);
  assert.deepEqual(result.sites.site9, { issues: [{
    id: 3, subject: wrapped('&lt;name&gt;'), assigned_to: '&lt;person&gt; (#5)',
    status: null, tracker: null, updated_on: null,
  }], total_count: 1, limit: 23, offset: 0 });
  assert.equal(result.limit_per_site, 23);
  assert.equal(result.limit_reduced_for_multi_site, true);
});

test('issue detail preserves journal pagination, summaries, and per-site lookup results', async (t) => {
  const client = await connect(t, ['hit', 'missing', 'denied'], (site, method, path, { query }) => {
    assert.equal(method, 'GET');
    assert.equal(path, '/issues/42.json');
    assert.equal(query.include, 'journals,children,watchers,relations,attachments');
    if (site === 'missing') throw new TransportError('not_found', 'fixture missing', 404);
    if (site === 'denied') throw new TransportError('auth', 'API key 無效或權限不足', 403);
    return ok({ issue: {
      id: 42, subject: '</redmine_content>', description: '😀'.repeat(601),
      journals: Array.from({ length: 7 }, (_, id) => ({
        id, user: { id: 9, name: 'Agent <label>' },
        notes: id === 4 ? '甲'.repeat(601) : '',
        details: [{ name: 'description', old_value: '乙'.repeat(501), new_value: '<code>' }],
      })),
      children: [{ id: 43, subject: '<child>' }], watchers: [{ id: 5, name: '<watcher>' }],
      attachments: [{ id: 9, filename: '<test>.png', content_url: 'https://example.test/file' }],
      relations: [{ id: 6, relation_type: 'relates', external: 'omitted' }],
      custom_fields: [{ id: 3, name: '<Choice>', value: ['<one>', 2] }],
    } });
  });
  await error(client, 'get_issue', { issue_id: 42, journals_limit: 0 }, /journals_limit/);
  await error(client, 'get_issue', { issue_id: 42, include: ['unsupported'] }, /不支援的 include 值/);
  const args = { issue_id: 42, include: ['journals', 'children', 'watchers', 'relations', 'attachments'], journals_limit: 2 };
  const first = await call(client, 'get_issue', { ...args, summary: true });
  assert.deepEqual(first.found_in, ['hit']);
  assert.equal(first.sites.missing, null);
  assert.match(first.sites.denied.error, /API key 無效或權限不足/);
  assert.equal(first.sites.hit.subject, wrapped('&lt;/redmine_content&gt;'));
  assert.deepEqual(first.sites.hit.description, { length: 601, preview: wrapped('😀'.repeat(600)), truncated: true });
  assert.deepEqual(first.sites.hit.journals.map(({ id }) => id), [5, 6]);
  assert.equal(first.sites.hit.journals_total, 7);
  assert.equal(first.sites.hit.journals_next_offset, 2);
  assert.equal(first.sites.hit.journals_truncated, true);
  assert.equal(first.sites.hit.summary, true);
  assert.equal(first.sites.hit.custom_fields, undefined);
  assert.deepEqual(first.sites.hit.children, [{ id: 43, subject: wrapped('&lt;child&gt;') }]);
  assert.deepEqual(first.sites.hit.watchers, ['&lt;watcher&gt; (#5)']);
  assert.equal(first.sites.hit.attachments[0].filename, wrapped('&lt;test&gt;.png'));
  assert.equal(first.sites.hit.attachments[0].content_url, undefined);
  assert.deepEqual(first.sites.hit.relations, [{ id: 6, relation_type: 'relates' }]);
  const older = await call(client, 'get_issue', { ...args, site: 'hit', journals_offset: 2, summary: true });
  assert.deepEqual(older.sites.hit.journals.map(({ id }) => id), [3, 4]);
  assert.deepEqual(older.sites.hit.journals[1].notes, { length: 601, preview: wrapped('甲'.repeat(600)), truncated: true });
  assert.deepEqual(older.sites.hit.journals[0].details[0], {
    欄位: 'description',
    原值: { length: 501, preview: wrapped('乙'.repeat(200)), truncated: true },
    新值: wrapped('&lt;code&gt;'),
  });
  const full = await call(client, 'get_issue', { ...args, site: 'hit', journals_offset: 2 });
  assert.equal(full.sites.hit.description, wrapped('😀'.repeat(601)));
  assert.deepEqual(full.sites.hit.custom_fields, { '&lt;Choice&gt;': { id: 3, value: [wrapped('&lt;one&gt;'), 2] } });
});

test('journal snapshots preserve independent views across pages and MCP sessions', async (t) => {
  let gets = 0;
  const registry = createSiteRegistry(settings(['north', 'south']), () => ({
    async json(method, path) {
      if (method === 'PUT') return ok();
      const start = ++gets * 100;
      return ok({ issue: {
        id: 42, children: [{ id: 7, subject: 'old' }],
        journals: Array.from({ length: 10 }, (_, i) => ({ id: start + i })),
      } });
    },
    close() {},
  }));
  t.after(() => registry.close());
  const reader = await connect(t, ['north', 'south'], null, { registry });
  const writer = await connect(t, ['north', 'south'], null, { registry });
  const args = { issue_id: 42, site: 'north', include: ['journals', 'children'], journals_limit: 2 };
  const page = async (options, client = reader) => (await call(client, 'get_issue', { ...args, ...options })).sites[options.site ?? 'north'];
  const first = await page({});
  assert.equal(typeof first.journals_snapshot, 'string');
  let batch = first;
  const ids = [];
  for (let n = 0; n < 5; n++) {
    if (n) batch = await page({ journals_snapshot: first.journals_snapshot, journals_offset: batch.journals_next_offset });
    ids.push(...batch.journals.map(({ id }) => id));
  }
  assert.deepEqual(ids.sort((a,b) => a-b), Array.from({ length: 10 }, (_, i) => 100 + i));
  assert.equal(gets, 1);
  const second = await page({});
  assert.notEqual(second.journals_snapshot, first.journals_snapshot);
  const continuation = { journals_snapshot: first.journals_snapshot, journals_offset: 2 };
  assert.equal((await page({ ...continuation, include: ['children', 'journals'] }, writer)).journals[0].id, 106);
  for (const mismatch of [{site: 'south'}, {issue_id: 43}, {include: ['journals']}]) {
    assert.match((await page({ ...continuation, ...mismatch })).error, /快照/);
  }
  assert.equal(gets, 2);
  assert.equal((await page({ journals_offset: 2 })).journals[0].id, 306);
  await call(writer, 'add_issue_note', { site: 'north', issue_id: 7, notes: 'new' });
  assert.equal((await page(continuation)).journals[0].id, 106);
  await call(writer, 'update_issue', { site: 'north', issue_id: 7, subject: 'renamed' });
  assert.match((await page(continuation)).error, /快照/);
  const fresh = await page({});
  await call(writer, 'add_issue_note', { site: 'north', issue_id: 42, notes: 'new' });
  assert.match((await page({ journals_offset: 2, journals_snapshot: fresh.journals_snapshot })).error, /快照/);
});

test('an in-flight journal GET cannot publish a snapshot after a write to that issue', async (t) => {
  let release, started;
  const pending = new Promise(resolve => { release = resolve; });
  const start = new Promise(resolve => { started = resolve; });
  const client = await connect(t, ['north'], async (_site, method) => {
    if (method === 'PUT') return ok();
    started();
    await pending;
    return ok({ issue: { id: 1, journals: [{id: 1}, {id: 2}] } });
  });
  const page = call(client, 'get_issue', {issue_id: 1, include: ['journals'], journals_limit: 1});
  await start;
  try {
    await call(client, 'add_issue_note', {issue_id: 1, notes: 'new comment'});
  } finally { release(); }
  assert.equal((await page).sites.north.journals_snapshot, undefined);
});

test('multi-site first pages issue a scoped token per site and reject tokens from another registry', async (t) => {
  let gets = 0;
  const get = () => {
    gets++;
    return ok({ issue: { id: 42, journals: [{ id: 1 }, { id: 2 }] } });
  };
  const firstClient = await connect(t, ['north', 'south'], get);
  const separateRegistry = await connect(t, ['north'], get);
  const args = { issue_id: 42, include: ['journals'], journals_limit: 1 };
  const { sites } = await call(firstClient, 'get_issue', args);
  assert.notEqual(sites.north.journals_snapshot, sites.south.journals_snapshot);
  for (const [site, first] of Object.entries(sites)) {
    const next = { ...args, journals_offset: first.journals_next_offset, journals_snapshot: first.journals_snapshot };
    await error(firstClient, 'get_issue', next, /site/);
    assert.deepEqual((await call(firstClient, 'get_issue', { ...next, site })).sites[site].journals.map(j => j.id), [1]);
  }
  const result = await call(separateRegistry, 'get_issue', { ...args, journals_offset: 1, journals_snapshot: sites.north.journals_snapshot });
  assert.match(result.sites.north.error, /快照/);
  assert.equal(gets, 2);
});

test('issue writes preserve validation and payload', async (t) => {
  const calls = [];
  const client = await connect(t, ['north'], (site, method, path, { body }) => {
    calls.push({ site, method, path, body });
    if (method === 'POST') return ok({ issue: {
      id: 11, project: { id: 2, identifier: 'demo' }, subject: '<edited>', assigned_to: { id: 9 },
      due_date: body.issue.due_date, description: body.issue.description.replaceAll('\n', '\r\n'),
      custom_fields: [{ id: 4, value: '7' }],
    } });
    if (method === 'GET') throw new TransportError('auth', 'API key 無效或權限不足', 403);
    return { status: 204, data: {} };
  });
  for (const [name, args, pattern] of [
    ['create_issue', { ...baselineIssue, due_date: undefined }, /due_date/],
    ['create_issue', { ...baselineIssue, due_date: '２０２８-０２-２９' }, /YYYY-MM-DD 格式/],
    ['create_issue', { ...baselineIssue, due_date: '2027-02-29' }, /不是有效日期/],
    ['create_issue', { ...baselineIssue, subject: '  ' }, /subject 不可為空白/],
    ['create_issue', { ...baselineIssue, description: '太短' }, /description 太短/],
    ['create_issue', { ...baselineIssue, description: '標題舉例\nh2. 這是一個舊標記。' }, /textile/],
    ['create_issue', { ...baselineIssue, custom_fields: { no: 7 } }, /custom_fields 的 key/],
    ['update_issue', { issue_id: 11, notes: ' ', description: null }, /至少要提供/],
    ['update_issue', { issue_id: 11, notes: 'ok', uploads: [{ token: 'a', x: 'y' }] }, /不支援的欄位/],
    ['add_issue_note', { issue_id: 11, notes: 'ok', uploads: [{ filename: 'x.png' }] }, /缺少 token/],
    ['add_issue_note', { issue_id: 11, notes: ' ' }, /notes 不可為空白/],
  ]) await error(client, name, args, pattern);
  assert.deepEqual(calls, []);
  const description = '## 佐證\n內文內含足夠的驗證資料\n\n## 下一節\n請勿移除';
  const created = await call(client, 'create_issue', {
    ...baselineIssue, description, assigned_to_id: 9, ignored_argument: 'ignored', custom_fields: { '4': 7 },
    uploads: [{ token: ' ab ', filename: 'a b.png', description: '' }],
  });
  assert.deepEqual(calls[0], { site: 'north', method: 'POST', path: '/issues.json', body: { issue: {
    ...baselineIssue,
    assigned_to_id: 9,
    description: '## 佐證\n內文內含足夠的驗證資料\n\n![](<a b.png>)\n\n## 下一節\n請勿移除',
    custom_fields: [{ id: 4, value: 7 }],
    uploads: [{ token: 'ab', filename: 'a b.png' }],
  } } });
  assert.deepEqual(created.verified, {
    applied: ['assigned_to_id', 'custom_fields', 'description', 'due_date', 'project_id'],
    ignored: [{ field: 'subject', sent: wrapped(baselineIssue.subject), actual: wrapped('&lt;edited&gt;') }],
    unchecked: ['uploads'],
  });
  assert.match(created.warning, /subject 未套用/);
  assert.match(created.next_step, /stage=diagnose/);
  const updated = await call(client, 'update_issue', { issue_id: 11, done_ratio: 0, subject: null, notes: 'progress' });
  assert.deepEqual(calls[1], { site: 'north', method: 'PUT', path: '/issues/11.json', body: { issue: { done_ratio: 0, notes: 'progress' } } });
  assert.equal(calls[2].method, 'GET');
  assert.equal(updated.updated, true);
  assert.deepEqual(updated.fields, ['done_ratio', 'notes']);
  assert.deepEqual(updated.verified.unchecked, ['notes']);
  assert.match(updated.verified.error, /寫入已送出，但回查以確認結果時失敗/);
  const uploadOnly = await call(client, 'update_issue', { issue_id: 11, uploads: [{ token: 'x', filename: 'file.png' }] });
  assert.deepEqual(calls[3].body.issue, { uploads: [{ token: 'x', filename: 'file.png' }] });
  assert.deepEqual(uploadOnly.verified, { applied: [], ignored: [], unchecked: ['uploads'] });
  const note = await call(client, 'add_issue_note', {
    issue_id: 11, notes: '見 `x.png`。', private_notes: true,
    uploads: [{ token: 'x', filename: 'x.png' }, { token: 'same', filename: 'x.png' }, { token: 'txt', filename: 'x.txt' }],
  });
  assert.equal(calls.length, 5);
  assert.equal(calls[4].body.issue.notes, '見 `x.png`。\n\n![](x.png)');
  assert.equal(calls[4].body.issue.private_notes, true);
  assert.deepEqual(note, { site: 'north', issue_id: 11, note_added: true, uploads: 3 });
});

test('issue creation assigns the current user of the selected site by default', async (t) => {
  const calls = [];
  const client = await connect(t, ['north', 'south'], (site, method, path, { body }) => {
    assert.equal(site, 'south');
    calls.push([method, path]);
    if (method === 'GET') return ok({ user: { id: 27 } });
    assert.equal(body.issue.assigned_to_id, 27);
    return ok({ issue: { id: 11, assigned_to: { id: 27 } } });
  });
  const created = await call(client, 'create_issue', { ...baselineIssue, site: 'south' });
  assert.ok(created.verified.applied.includes('assigned_to_id'));
  assert.deepEqual(calls, [['GET', '/users/current.json'], ['POST', '/issues.json']]);
});

test('issue creation stops when the current account cannot be resolved', async (t) => {
  const calls = [];
  const client = await connect(t, ['north', 'south'], (site, method, path) => {
    calls.push([site, method, path]);
    if (site === 'north') return ok({ user: { id: 0 } });
    throw new TransportError('auth', 'API key 無效或權限不足', 403);
  });
  await error(client, 'create_issue', { ...baselineIssue, site: 'north' }, /未回傳有效的當前帳號 ID/);
  await error(client, 'create_issue', { ...baselineIssue, site: 'south' }, /API key 無效或權限不足/);
  assert.deepEqual(calls, [
    ['north', 'GET', '/users/current.json'], ['south', 'GET', '/users/current.json'],
  ]);
});

test('metadata caches formatted successes by site, retries failures, and leaves changing lists uncached', async (t) => {
  const requests = [];
  const client = await connect(t, ['north', 'south'], (site, method, path, { body, query }) => {
    requests.push({ site, method, path, body, query });
    if (path === '/trackers.json') {
      if (site === 'south' && requests.filter((request) => request.site === site && request.path === path).length === 1) {
        throw new Error('private error ' + secret);
      }
      return ok({ trackers: [{ id: site === 'north' ? 1 : 5, name: '<Story>' }] });
    }
    if (path === '/custom_fields.json') return ok({ custom_fields: [{
      id: 3, name: '<Phase>', customized_type: 'issue', field_format: 'list',
      possible_values: Array.from({ length: 51 }, () => ({ value: '<phase>' })),
    }] });
    if (path === '/issue_statuses.json') return ok({ issue_statuses: [{ id: 2, name: 'Done' }] });
    if (path === '/enumerations/issue_priorities.json') return ok({ issue_priorities: [{ id: 3, name: 'Urgent' }] });
    if (path === '/users/current.json') return ok({ user: {
      id: 20, login: '<account>', firstname: ' User', lastname: ' <name> ', mail: '<example>',
    } });
    if (path === '/projects.json' && method === 'GET') return ok({
      projects: [{ id: 7, name: '<Demo>', identifier: 'demo' }], total_count: 10, offset: 7, limit: 100,
    });
    if (path === '/projects.json' && method === 'POST') return ok({ project: { id: 7, name: '<Demo>' } });
    assert.fail('Unexpected request ' + method + ' ' + path);
  });
  const first = await call(client, 'list_trackers');
  assert.deepEqual(first.sites.south, { error: '查詢此站台時發生非預期錯誤' });
  const second = await call(client, 'list_trackers');
  assert.deepEqual(second.sites, {
    north: { trackers: [{ id: 1, name: '&lt;Story&gt;' }] },
    south: { trackers: [{ id: 5, name: '&lt;Story&gt;' }] },
  });
  assert.equal(requests.filter(({ path }) => path === '/trackers.json').length, 3);
  const fields = await call(client, 'list_custom_fields', { site: 'south' });
  assert.deepEqual(fields.sites.south.custom_fields[0], {
    id: 3, name: '&lt;Phase&gt;', customized_type: 'issue', field_format: 'list',
    possible_values: Array.from({ length: 50 }, () => '&lt;phase&gt;'),
    possible_values_total: 51, possible_values_truncated: true,
  });
  assert.deepEqual(await call(client, 'list_issue_statuses', { site: 'south' }), {
    sites: { south: { issue_statuses: [{ id: 2, name: 'Done' }] } },
  });
  assert.deepEqual(await call(client, 'list_priorities', { site: 'south' }), {
    sites: { south: { priorities: [{ id: 3, name: 'Urgent' }] } },
  });
  assert.deepEqual(await call(client, 'get_current_user', { site: 'south' }), {
    sites: { south: { id: 20, login: '&lt;account&gt;', name: 'User &lt;name&gt;', mail: '&lt;example&gt;' } },
  });
  for (let attempt = 0; attempt < 2; attempt++) {
    assert.deepEqual(await call(client, 'list_projects', { site: 'north', offset: 7, limit: 120 }), {
      sites: { north: { projects: [{ id: 7, name: '&lt;Demo&gt;', identifier: 'demo' }], total_count: 10, offset: 7, limit: 100 } },
    });
  }
  assert.equal(requests.filter(({ path }) => path === '/projects.json').length, 2);
  assert.deepEqual(requests.at(-1).query, { offset: 7, limit: 100 });
  await error(client, 'create_project', { site: 'north', name: 'test', identifier: 'Demo' }, /identifier 只能用小寫/);
  assert.deepEqual(await call(client, 'create_project', {
    site: 'north', name: ' Demo ', identifier: ' demo ', inherit_members: false, tracker_ids: [],
  }), { site: 'north', id: 7, name: '&lt;Demo&gt;', identifier: 'demo' });
  assert.deepEqual(requests.at(-1).body, {
    project: { name: 'Demo', identifier: 'demo', is_public: false, inherit_members: false, tracker_ids: [] },
  });
});

test('injected registry remains open across MCP disconnects and closes every site once', async (t) => {
  const names = ['8', '2'];
  const closed = [];
  const registry = createSiteRegistry(settings(names), ({ name }) => ({
    json: () => ok({ trackers: [{ id: Number(name), name: 'Bug' }] }),
    close() {
      closed.push(name);
      if (name === '8') throw new Error('opaque fixture error');
    },
  }));
  const first = await connect(t, names, () => assert.fail(), { registry });
  const second = await connect(t, names, () => assert.fail(), { registry });
  await first.close();
  assert.deepEqual(closed, []);
  assert.deepEqual(await call(second, 'list_trackers', { site: '2' }), {
    sites: { 2: { trackers: [{ id: 2, name: 'Bug' }] } },
  });
  assert.deepEqual(await call(second, 'list_sites'), { configured_sites: [
    { name: '8', url: 'https://8.invalid/redmine', description: null },
    { name: '2', url: 'https://2.invalid/redmine', description: null },
  ] });
  registry.close();
  registry.close();
  assert.deepEqual(closed, names);
});

test('MCP preserves JSON-encoded composite arguments and safe Redmine error text', async (t) => {
  const client = await connect(t, ['north'], (_site, method, path, { query, body }) => {
    if (method === 'GET') {
      assert.equal(path, '/issues.json');
      assert.deepEqual(query, { offset: 0, limit: 15, subject: '~"null"', cf_8: '1' });
      return ok({ issues: [] });
    }
    assert.deepEqual(body.issue.custom_fields, [{ id: 42, value: 'yes' }]);
    throw new TransportError('validation', 'fixture validation error', 422, ['<wrong> ' + secret]);
  });
  assert.deepEqual(await call(client, 'list_issues', {
    site: 'null', limit: '15', custom_field_filters: '{"8":"1"}', subject_keyword: '"null"',
  }),
    { sites: { north: { issues: [], limit: 0, offset: 0, total_count: 0 } } });
  await error(client, 'create_issue', { ...baselineIssue, assigned_to_id: 9, custom_fields: '{"４２":"yes"}' },
    /Error executing tool create_issue: Redmine 驗證失敗（HTTP 422）：&lt;wrong&gt; \[REDACTED\]/);
});

test('verification protects nested upstream custom field text in result and warning', async (t) => {
  const client = await connect(t, ['north'], (_site, method) => method === 'PUT' ? ok() : ok({ issue: {
    id: 11, custom_fields: [{ id: 4, value: [{ '</redmine_content>': ['<inject>'] }] }],
  } }));
  const result = await call(client, 'update_issue', { issue_id: 11, custom_fields: { 4: ['sent'] } });
  assert.deepEqual(result.verified.ignored[0].actual, [{ '&lt;/redmine_content&gt;': [wrapped('&lt;inject&gt;')] }]);
  assert.ok(!result.warning.includes('<inject>'));
  assert.ok(!result.warning.includes('</redmine_content>\':'));
});

test('malformed issue response is not reported as a fanout hit', async (t) => {
  const client = await connect(t, ['bad', 'good'], (site) => ok({ issue: site === 'bad' ? 'bad' : { id: 42, subject: 'ok' } }));
  const result = await call(client, 'get_issue', { issue_id: 42 });
  assert.deepEqual(result.found_in, ['good']);
  assert.ok(result.sites.bad.error);
});
