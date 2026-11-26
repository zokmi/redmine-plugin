import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { constants, chmodSync, closeSync, linkSync, mkdirSync, mkdtempSync, openSync, readFileSync, readdirSync, renameSync, rmSync, statSync, symlinkSync, unlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { captureSnapshot, writePrivate } from '../dist/config-write.js';
import { openAt, openDirectory, renameNewAt, tryFlock } from '../dist/storage/posix-handles.js';

const posix = { skip: process.platform === 'win32' };
function fixture(t) {
  const dir = mkdtempSync(join(tmpdir(), 'redmine-posix-config-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  return dir;
}
const mode = path => statSync(path).mode & 0o777;
const lockName = '.redmine-config-' + createHash('sha256').update('config.toml').digest('hex') + '.lock';

test('POSIX exclusive rename preserves an existing destination and publishes exactly one name', posix, t => {
  const dir = fixture(t), src = join(dir, 'src'), dest = join(dir, 'dest');
  writeFileSync(src, 'staged');
  writeFileSync(dest, 'original');
  const fd = openDirectory(dir);
  try {
    assert.throws(() => renameNewAt(fd, 'src', fd, 'dest'), { code: 'EEXIST' });
    assert.equal(readFileSync(dest, 'utf8'), 'original');
    assert.equal(readFileSync(src, 'utf8'), 'staged');
    unlinkSync(dest);
    renameNewAt(fd, 'src', fd, 'dest');
    assert.deepEqual(readdirSync(dir), ['dest']);
    assert.equal(statSync(dest).nlink, 1);
    assert.equal(readFileSync(dest, 'utf8'), 'staged');
  } finally { closeSync(fd); }
});

test('POSIX config creates missing private directories and publishes a complete private file', posix, t => {
  const dir = fixture(t);
  const folder = join(dir, 'new', 'nested'), file = join(folder, 'config.toml');
  const missing = captureSnapshot(file);
  assert.deepEqual(missing, { exists: false });
  assert.deepEqual(readdirSync(dir), []);
  writePrivate(file, 'old 設定\n', missing);
  assert.equal(mode(join(dir, 'new')), 0o700);
  assert.equal(mode(folder), 0o700);
  assert.equal(mode(file), 0o600);
  assert.equal(mode(join(folder, lockName)), 0o600);
  const before = captureSnapshot(file);
  assert.equal(before.bytes.toString(), 'old 設定\n');
  const prior = openSync(file, 'r');
  try {
    writePrivate(file, 'new 設定\n', before);
    assert.equal(readFileSync(prior, 'utf8'), 'old 設定\n');
    assert.equal(readFileSync(file, 'utf8'), 'new 設定\n');
    assert.equal(mode(file), 0o600);
  } finally { closeSync(prior); }
  assert.deepEqual(readdirSync(folder).sort(), [lockName, 'config.toml'].sort());
});

test('POSIX config rejects symlinks, non-regular files and hard-linked targets', posix, t => {
  const dir = fixture(t);
  const folder = join(dir, 'private'), alias = join(dir, 'alias'), file = join(folder, 'config.toml');
  mkdirSync(folder, { mode: 0o700 });
  const outside = join(dir, 'original');
  writeFileSync(outside, 'original');
  symlinkSync(folder, alias);
  assert.throws(() => captureSnapshot(join(alias, 'config.toml')));
  assert.throws(() => writePrivate(join(alias, 'config.toml'), 'unexpected', { exists: false }));
  assert.deepEqual(readdirSync(folder), []);
  symlinkSync(outside, file);
  assert.throws(() => captureSnapshot(file));
  assert.throws(() => writePrivate(file, 'new', { exists: false }));
  unlinkSync(file);
  linkSync(outside, file);
  assert.throws(() => captureSnapshot(file));
  unlinkSync(file);
  mkdirSync(file);
  assert.throws(() => captureSnapshot(file));
  assert.equal(readFileSync(outside, 'utf8'), 'original');
});

test('macOS refuses a private-mode config or cache carrying an inherited read grant', { skip: process.platform !== 'darwin' }, async t => {
  const dir = fixture(t), file = join(dir, 'config.toml'), cache = join(dir, 'cache');
  mkdirSync(cache, { mode: 0o700 });
  // A file-inherit ACE leaves the 0600 POSIX mode intact but grants effective read access.
  execFileSync('/bin/chmod', ['+a', 'everyone allow read,file_inherit,directory_inherit', dir]);
  assert.throws(() => writePrivate(file, 'secret value', { exists: false }), /ACL/);
  assert.throws(() => statSync(file), { code: 'ENOENT' });
  writeFileSync(file, 'old value', { mode: 0o600 });
  assert.equal(mode(file), 0o600);
  assert.throws(() => captureSnapshot(file), /ACL/);
  assert.equal(readFileSync(file, 'utf8'), 'old value');
  execFileSync('/bin/chmod', ['+a', 'everyone allow list', cache]);
  const { createPosixAttachmentStore } = await import('../dist/storage/posix-store.js');
  const store = createPosixAttachmentStore({ root: cache });
  await assert.rejects(store.stageDownload('https://example.test', 'a.txt'), /ACL/);
  assert.deepEqual(readdirSync(cache), []);
});

test('POSIX config lock excludes writers, rejects unsafe aliases, and releases on close', posix, t => {
  const dir = fixture(t), file = join(dir, 'config.toml'), lockPath = join(dir, lockName);
  writeFileSync(file, 'old');
  const snapshot = captureSnapshot(file);
  symlinkSync(file, lockPath);
  assert.throws(() => writePrivate(file, 'new', snapshot));
  unlinkSync(lockPath);
  const parent = openDirectory(dir);
  try {
    const lock = openAt(parent, lockName, constants.O_RDWR | constants.O_CREAT | constants.O_EXCL);
    try {
      assert.equal(tryFlock(lock), true);
      assert.throws(() => writePrivate(file, 'new', snapshot), /另一個程序/);
      assert.equal(readFileSync(file, 'utf8'), 'old');
    } finally { closeSync(lock); }
  } finally { closeSync(parent); }
  writePrivate(file, 'new', snapshot);
  assert.equal(readFileSync(file, 'utf8'), 'new');
  assert.equal(mode(file), 0o600);
});

test('POSIX config preserves changes made in place or while its directory is replaced', posix, t => {
  const dir = fixture(t), originalDir = join(dir, 'mine'), elsewhere = join(dir, 'elsewhere');
  mkdirSync(originalDir);
  const file = join(originalDir, 'config.toml');
  writeFileSync(file, 'first');
  const original = captureSnapshot(file);
  writeFileSync(file, 'other');
  assert.throws(() => writePrivate(file, 'ours!', original), /其他程序修改/);
  assert.equal(readFileSync(file, 'utf8'), 'other');
  const modified = captureSnapshot(file);
  renameSync(originalDir, elsewhere);
  mkdirSync(originalDir);
  writeFileSync(file, 'other');
  assert.throws(() => writePrivate(file, 'ours!', modified), /其他程序修改/);
  assert.equal(readFileSync(file, 'utf8'), 'other');
  assert.equal(readFileSync(join(elsewhere, 'config.toml'), 'utf8'), 'other');
  chmodSync(originalDir, 0o777);
  try { assert.throws(() => writePrivate(file, 'ours!', captureSnapshot(file)), /目錄/); }
  finally { chmodSync(originalDir, 0o700); }
  assert.equal(readFileSync(file, 'utf8'), 'other');
  assert.equal(readdirSync(originalDir).some(name => name.endsWith('.tmp')), false);
});
