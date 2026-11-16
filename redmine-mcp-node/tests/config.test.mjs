import assert from 'node:assert/strict';
import { copyFile, mkdtemp, mkdir, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { ConfigError, loadSettings } from '../dist/config.js';

async function configFile(t, contents) {
  const directory = await mkdtemp(join(tmpdir(), 'redmine-config-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const filename = join(directory, 'config.toml');
  await writeFile(filename, contents);
  return filename;
}

test('legacy config priority', async (t) => {
  const filename = await configFile(t, `
url = 'https://file.example/redmine/'
api_key = 'file-key'
max_attachment_mb = 7
description = 'legacy'
`);
  const { sites } = await loadSettings({
    REDMINE_CONFIG: filename,
    REDMINE_URL: ' https://env.example/redmine// ',
    REDMINE_API_KEY: ' env-key ',
    REDMINE_MAX_ATTACHMENT_MB: '20',
  });
  assert.deepEqual([...sites], [['default', {
    name: 'default', url: 'https://env.example/redmine', apiKey: 'env-key', description: 'legacy',
    allowInsecureHttp: false, attachmentUploadRoot: null, maxAttachmentBytes: 7 * 1024 * 1024,
  }]]);
});

test('multi-site config keeps file credentials and site-specific limit', async (t) => {
  const filename = await configFile(t, `
max_attachment_mb = 7
[sites.20]
url = 'https://one.example'
api_key = 'one-key'
max_attachment_mb = 2
[sites.3]
url = 'https://two.example/'
api_key = 'two-key'
`);
  const warn = t.mock.method(console, 'warn', () => {});
  const { sites } = await loadSettings({
    REDMINE_CONFIG: filename, REDMINE_URL: 'https://ignored.example', REDMINE_API_KEY: 'ignored-key',
    REDMINE_MAX_ATTACHMENT_MB: '8', REDMINE_DOWNLOAD_DIR: '~/ignored-path',
  });
  assert.deepEqual([...sites.keys()], ['20', '3']);
  assert.equal(sites.get('20').apiKey, 'one-key');
  assert.equal(sites.get('20').maxAttachmentBytes, 2 * 1024 * 1024);
  assert.equal(sites.get('3').url, 'https://two.example');
  assert.equal(sites.get('3').maxAttachmentBytes, 8 * 1024 * 1024);
  const messages = warn.mock.calls.flatMap((call) => call.arguments).join('\n');
  assert.match(messages, /REDMINE_(?:URL|API_KEY)/);
  assert.match(messages, /REDMINE_DOWNLOAD_DIR/);
  assert.doesNotMatch(messages, /ignored|one-key|two-key/);
});

test('an explicit HTTP opt-in applies only to the configured URL', async (t) => {
  const filename = await configFile(t, `
[sites.legacy]
url = 'http://legacy.example/redmine/'
api_key = 'one-key'
allow_insecure_http = true
`);
  const env = { REDMINE_CONFIG: filename, REDMINE_URL: 'http://legacy.example/redmine' };
  assert.equal((await loadSettings(env)).sites.get('legacy').allowInsecureHttp, true);
  await assert.rejects(loadSettings({ ...env, REDMINE_URL: 'http://other.example' }), /HTTPS/);
});

test('malformed config, mixed layouts and invalid types report safe errors', async (t) => {
  const filename = await configFile(t, '');
  for (const contents of [
    'api_key = "secret-key',
    `url = 'https://a.example'\napi_key = 'secret-key'\n[sites.a]`,
    `url = 'http://a.example'\napi_key = 'secret-key'\nallow_insecure_http = 'true'`,
    `url = 'https://secret-key:password@a.example'\napi_key = 'secret-key'`,
    `url = 'https://a.example'\napi_key = 'secret-key'\nmax_attachment_mb = -1`,
    `url = 'https://a.example'\napi_key = 'secret-key'\nmax_attachment_mb = 1.0`,
    `url = 'https://a.example'\napi_key = 'secret-key'\nattachment_upload_root = '.'`,
    Buffer.from([0xff, 0xff]),
  ]) {
    await writeFile(filename, contents);
    await assert.rejects(loadSettings({ REDMINE_CONFIG: filename }), (error) => {
      assert.ok(error instanceof ConfigError);
      assert.doesNotMatch(String(error), /secret-key|password/);
      assert.equal(error.cause, undefined);
      return true;
    });
  }
});

test('a missing explicit config uses environment settings and defaults', async (t) => {
  const filename = await configFile(t, '');
  await rm(filename);
  const { sites } = await loadSettings({
    REDMINE_CONFIG: filename, REDMINE_URL: 'https://env.example', REDMINE_API_KEY: 'env-key',
  });
  assert.equal(sites.get('default').maxAttachmentBytes, 10 * 1024 * 1024);
});

test('config path precedence is explicit path, source checkout, then OS user settings', async (t) => {
  const directory = await mkdtemp(join(tmpdir(), 'redmine-config-location-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  // Run from a copy of the built module so no real checkout config can take precedence.
  const installed = join(directory, 'redmine-mcp-node');
  const dist = join(installed, 'dist');
  const userConfig = join(directory, 'user', 'redmine-mcp');
  const checkout = join(directory, 'redmine-mcp');
  await Promise.all([dist, userConfig, checkout].map((path) => mkdir(path, { recursive: true })));
  await copyFile(new URL('../dist/config.js', import.meta.url), join(dist, 'config.js'));
  await writeFile(join(installed, 'package.json'), '{"type":"module"}');
  await symlink(new URL('../node_modules', import.meta.url), join(installed, 'node_modules'), 'junction');
  await writeFile(join(userConfig, 'config.toml'), `url = 'https://user.example'\napi_key = 'test'`);
  const { loadSettings: loadInstalledSettings } = await import(pathToFileURL(join(dist, 'config.js')));
  const env = { APPDATA: join(directory, 'user'), XDG_CONFIG_HOME: join(directory, 'user') };
  assert.equal((await loadInstalledSettings(env)).sites.get('default').url, 'https://user.example');
  await writeFile(join(checkout, 'config.toml'), `url = 'https://checkout.example'\napi_key = 'test'`);
  assert.equal((await loadInstalledSettings(env)).sites.get('default').url, 'https://checkout.example');
  const explicit = await configFile(t, `url = 'https://explicit.example'\napi_key = 'test'`);
  const settings = await loadInstalledSettings({ ...env, REDMINE_CONFIG: explicit });
  assert.equal(settings.sites.get('default').url, 'https://explicit.example');
});
