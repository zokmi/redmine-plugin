import { homedir } from 'node:os';
import { isAbsolute, join } from 'node:path';
import { scheduleStorage } from './storage-scheduler.js';

function storageRoot(env: NodeJS.ProcessEnv): string {
  if (env.REDMINE_MCP_STORAGE_ROOT) {
    if (!isAbsolute(env.REDMINE_MCP_STORAGE_ROOT)) throw new Error('REDMINE_MCP_STORAGE_ROOT must be absolute');
    return env.REDMINE_MCP_STORAGE_ROOT;
  }
  const home = homedir();
  let data: string;
  if (process.platform === 'win32') {
    data = env.LOCALAPPDATA && isAbsolute(env.LOCALAPPDATA) ? env.LOCALAPPDATA : join(home, 'AppData', 'Local');
  } else if (process.platform === 'darwin') {
    data = join(home, 'Library', 'Application Support');
  } else {
    data = env.XDG_DATA_HOME && isAbsolute(env.XDG_DATA_HOME) ? env.XDG_DATA_HOME : join(home, '.local', 'share');
  }
  return join(data, 'redmine-mcp', 'attachments');
}

export async function startRuntimeStorage() {
  const root = storageRoot(process.env);
  if (process.platform === 'win32') {
    const { createWindowsAttachmentStore } = await import('./storage/windows-store.js');
    if (!process.env.REDMINE_MCP_STORAGE_ROOT) {
      const { provisionStorageRoot } = await import('./storage-preflight.js');
      provisionStorageRoot(root);
    }
    return scheduleStorage(createWindowsAttachmentStore({ root }));
  }
  const { createPosixAttachmentStore } = await import('./storage/posix-store.js');
  return scheduleStorage(createPosixAttachmentStore({ root }));
}
