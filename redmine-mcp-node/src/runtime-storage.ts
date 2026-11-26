import { homedir } from 'node:os';
import { isAbsolute, join } from 'node:path';
import { Worker } from 'node:worker_threads';
import { scheduleStorage } from './storage-scheduler.js';

async function sweepWindowsInBackground(root: string, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return;
  const cancelled = new Int32Array(new SharedArrayBuffer(Int32Array.BYTES_PER_ELEMENT));
  const cancel = () => Atomics.store(cancelled, 0, 1);
  const worker = new Worker(new URL('./storage/windows-sweep-worker.js', import.meta.url), {
    workerData: { root, cancelled },
    execArgv: [],
  });
  // The sweep checks the shared flag between items, including while its own
  // event loop is busy. Let the current locked item finish before exiting.
  signal?.addEventListener('abort', cancel, { once: true });
  try {
    const exited = new Promise<void>((resolve, reject) => {
      let failure: Error | undefined;
      worker.once('error', error => { failure = error; });
      worker.once('exit', code => {
        if (failure) reject(failure);
        else if (code !== 0) reject(new Error(`Attachment cleanup worker exited with code ${code}`));
        else resolve();
      });
    });
    worker.unref();
    await exited;
  } finally {
    signal?.removeEventListener('abort', cancel);
  }
}

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
    const store = createWindowsAttachmentStore({ root });
    return scheduleStorage({ ...store, sweep: (_now, signal) => sweepWindowsInBackground(root, signal) });
  }
  const { createPosixAttachmentStore } = await import('./storage/posix-store.js');
  return scheduleStorage(createPosixAttachmentStore({ root }));
}
