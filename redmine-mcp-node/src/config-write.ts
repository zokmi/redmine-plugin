import { randomBytes } from 'node:crypto';
import { basename, dirname, resolve } from 'node:path';
import { lstatSync } from 'node:fs';
import { closeHandle, openDirectory, openGuardedFile, createPrivateDirectory, createPrivateFile, writeHandle, flushHandle, renameHandle, deleteHandle } from './storage/windows-handles.js';

export function writePrivate(path: string, text: string, exists = true): void {
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
  let parent = parents[0], temp: unknown;
  try {
    for (const component of missing) { parent = createPrivateDirectory(parent, component); parents.push(parent); }
    if (exists) { const original = openGuardedFile(parent, basename(path)); closeHandle(original); }
    temp = createPrivateFile(parent, `.redmine-config-${randomBytes(16).toString('hex')}.tmp`);
    writeHandle(temp, Buffer.from(text, 'utf8'));
    flushHandle(temp);
    renameHandle(temp, parent, basename(path), exists);
    closeHandle(temp); temp = undefined;
  } finally {
    try { if (temp) { try { deleteHandle(temp); } finally { closeHandle(temp); } } }
    finally { for (const item of parents.reverse()) closeHandle(item); }
  }
}
