import { createHash, randomBytes } from 'node:crypto';
import { closeSync, constants, fchmodSync, fstatSync, fsyncSync, readFileSync, writeFileSync } from 'node:fs';
import { basename, dirname, resolve } from 'node:path';
import type { ConfigSnapshot } from './config-write.js';
import { linkAt, openAt, openDirectory, renameAt, tryFlock, unlinkAt } from './storage/posix-handles.js';

const changed = () => new Error('設定檔已被其他程序修改，請重新執行');
function code(error: unknown) { return (error as NodeJS.ErrnoException).code; }

function snapshotAt(parent: number, name: string): ConfigSnapshot {
  let fd: number;
  try { fd = openAt(parent, name, constants.O_RDONLY); }
  catch (error) { if (code(error) === 'ENOENT') return { exists: false }; throw error; }
  try {
    const before = fstatSync(fd, { bigint: true });
    if (!before.isFile() || before.nlink !== 1n || before.uid !== BigInt(process.geteuid!())) {
      throw new Error('設定檔必須是目前使用者擁有、只有一個連結的一般檔案');
    }
    const bytes = readFileSync(fd);
    const after = fstatSync(fd, { bigint: true });
    if (after.ctimeNs !== before.ctimeNs || after.size !== BigInt(bytes.length) || after.nlink !== 1n) throw changed();
    return { exists: true, identity: `${before.dev}:${before.ino}:${before.ctimeNs}`, bytes };
  } finally { closeSync(fd); }
}

export function capturePosixSnapshot(path: string): ConfigSnapshot {
  const target = resolve(path);
  let parent: number;
  try { parent = openDirectory(dirname(target)); }
  catch (error) { if (code(error) === 'ENOENT') return { exists: false }; throw error; }
  try { return snapshotAt(parent, basename(target)); }
  finally { closeSync(parent); }
}

function acquireLock(parent: number, name: string): number {
  // Keep the lock inode across calls so every process contends on the same flock.
  const lock = openAt(parent, name, constants.O_CREAT | constants.O_RDWR);
  try {
    const stat = fstatSync(lock);
    if (!stat.isFile() || stat.nlink !== 1 || stat.uid !== process.geteuid!() || (stat.mode & 0o077) !== 0) {
      throw new Error('設定檔鎖定檔的擁有者、權限或類型不符');
    }
    if (!tryFlock(lock)) throw new Error('設定檔正由另一個程序修改，請稍後重試');
    return lock;
  } catch (error) { closeSync(lock); throw error; }
}

function publish(parent: number, name: string, text: string, snapshot: ConfigSnapshot): void {
  const tmp = `.redmine-config-${randomBytes(16).toString('hex')}.tmp`;
  const fd = openAt(parent, tmp, constants.O_CREAT | constants.O_EXCL | constants.O_WRONLY);
  let tempExists = true;
  try {
    fchmodSync(fd, 0o600);
    writeFileSync(fd, text, 'utf8');
    fsyncSync(fd);
    if (snapshot.exists) {
      // An editor may have replaced or rewritten the file during interactive verification.
      const current = snapshotAt(parent, name);
      if (!current.exists || current.identity !== snapshot.identity || !current.bytes.equals(snapshot.bytes)) throw changed();
      renameAt(parent, tmp, parent, name);
      tempExists = false;
    } else {
      // linkat publishes the complete file only if the destination is still absent.
      try { linkAt(parent, tmp, parent, name); }
      catch (error) { if (code(error) === 'EEXIST') throw changed(); throw error; }
      unlinkAt(parent, tmp);
      tempExists = false;
    }
    fsyncSync(parent);
  } finally {
    try { if (tempExists) unlinkAt(parent, tmp); }
    finally { closeSync(fd); }
  }
}

export function writePosixPrivate(path: string, text: string, snapshot: ConfigSnapshot): void {
  const target = resolve(path);
  const name = basename(target);
  const parent = openDirectory(dirname(target), !snapshot.exists);
  try {
    const dir = fstatSync(parent);
    // A writable shared directory allows another account to substitute locks and temporary names.
    const trustedOwner = dir.uid === process.geteuid!() || dir.uid === 0;
    if (!trustedOwner || ((dir.mode & 0o022) !== 0 && (dir.mode & 0o1000) === 0)) {
      throw new Error('設定檔目錄必須由目前使用者或 root 擁有，並保護檔名免受其他使用者更改');
    }
    const key = createHash('sha256').update(name.toLowerCase()).digest('hex');
    const lock = acquireLock(parent, `.redmine-config-${key}.lock`);
    try { publish(parent, name, text, snapshot); }
    finally { closeSync(lock); }
  } finally { closeSync(parent); }
}
