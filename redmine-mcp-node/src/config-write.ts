import { createHash, randomBytes } from 'node:crypto';
import { basename, dirname, resolve } from 'node:path';
import { lstatSync } from 'node:fs';
import { closeHandle, openDirectory, openGuardedFile, snapshotHandle, tryLock, createPrivateDirectory, createPrivateFile, writeHandle, flushHandle, renameHandle, deleteHandle } from './storage/windows-handles.js';

export type ConfigSnapshot = {exists: false} | {exists: true, identity: string, bytes: Buffer};
export function captureSnapshot(path: string): ConfigSnapshot {
  if (process.platform !== 'win32') throw new Error('非 Windows 請使用既有 Python 設定命令');
  try { lstatSync(path); } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return {exists:false};
    throw error;
  }
  const parent = openDirectory(dirname(resolve(path)));
  try {
    const handle = openGuardedFile(parent, basename(path));
    try { return {exists:true, ...snapshotHandle(handle)}; } finally { closeHandle(handle); }
  } finally { closeHandle(parent); }
}
export function writePrivate(path: string, text: string, snapshot: ConfigSnapshot): void {
  if (process.platform !== 'win32') throw new Error('非 Windows 請使用既有 Python 設定命令');
  const missing: string[] = [];
  let directory = dirname(resolve(path));
  while (true) {
    try { lstatSync(directory); break; }
    catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
      const next = dirname(directory);
      if (next === directory) throw new Error('Cannot locate parent directory');
      missing.unshift(basename(directory)); directory = next;
    }
  }
  const parents: unknown[] = [openDirectory(directory)];
  let parent = parents[0], temp: unknown, lock: unknown, original: unknown;
  try {
    for (const component of missing) { parent = createPrivateDirectory(parent, component); parents.push(parent); }
    const lockName = `.redmine-config-${createHash('sha256').update(basename(path).toLowerCase()).digest('hex')}.lock`;
    lock = tryLock(parent, lockName);
    if (!lock) throw new Error('設定檔正由另一個程序修改，請稍後重試');
    if (snapshot.exists) {
      original = openGuardedFile(parent, basename(path));
      const current = snapshotHandle(original);
      if (current.identity !== snapshot.identity || !current.bytes.equals(snapshot.bytes)) throw new Error('設定檔已被其他程序修改，請重新執行');
    }
    temp = createPrivateFile(parent, `.redmine-config-${randomBytes(16).toString('hex')}.tmp`);
    writeHandle(temp, Buffer.from(text, 'utf8'));
    flushHandle(temp);
    // Windows replacement cannot proceed while our guarded original denies delete sharing.
    // The per-config lock excludes cooperating writers; an external editor can still race this close.
    if (original) { closeHandle(original); original = undefined; }
    renameHandle(temp, parent, basename(path), snapshot.exists);
    closeHandle(temp); temp = undefined;
  } finally {
    try { if (original) closeHandle(original); if (temp) { try { deleteHandle(temp); } finally { closeHandle(temp); } } }
    finally { try { if (lock) closeHandle(lock); } finally { for (const item of parents.reverse()) closeHandle(item); } }
  }
}
