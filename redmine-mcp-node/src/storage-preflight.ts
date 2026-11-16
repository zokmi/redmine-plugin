import { fileURLToPath } from 'node:url';
import { lstatSync } from 'node:fs';
import { basename, dirname, isAbsolute, resolve } from 'node:path';
import { closeHandle, createPrivateDirectory, openDirectory, verifyPrivateHandle } from './storage/windows-handles.js';

// Create only missing components; existing roots are inspected and never repaired in place.
export function provisionStorageRoot(root: string): void {
  if (process.platform !== 'win32' || !isAbsolute(root)) throw new Error('Windows absolute storage root required');
  const target = resolve(root), missing: string[] = [];
  let current = target;
  while (true) {
    try { lstatSync(current); break; } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
      const next = dirname(current); if (next === current) throw new Error('Storage parent unavailable');
      missing.unshift(basename(current)); current = next;
    }
  }
  const handles: unknown[] = [openDirectory(current)];
  try {
    let parent = handles[0];
    if (!missing.length) verifyPrivateHandle(parent, true);
    for (const component of missing) { parent = createPrivateDirectory(parent, component); handles.push(parent); verifyPrivateHandle(parent, true); }
  } finally { for (const handle of handles.reverse()) closeHandle(handle); }
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) provisionStorageRoot(process.argv[2]);
