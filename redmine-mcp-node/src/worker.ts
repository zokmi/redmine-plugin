import { loadSettings } from './config.js';
import { createWindowsAttachmentStore } from './storage/windows-store.js';
import { createWorkerHttp } from './worker-http.js';

async function main() {
  const token = process.env.REDMINE_MCP_WORKER_TOKEN ?? '';
  delete process.env.REDMINE_MCP_WORKER_TOKEN;
  if (!/^[A-Za-z0-9_-]{32,}$/.test(token)) throw new Error('Invalid worker token');
  const settings = await loadSettings(process.env);
  const root = process.env.REDMINE_MCP_STORAGE_ROOT;
  const attachmentStore = root && process.platform === 'win32' ? createWindowsAttachmentStore({root}) : undefined;
  const service = createWorkerHttp(settings, token, {attachmentStore});
  process.stdin.on('end', () => { void service.close().finally(() => process.exit(0)); }); process.stdin.resume();
  console.log(await service.listen());
}
main().catch(error => { console.error(error instanceof Error ? error.message : 'Worker startup failed'); process.exitCode = 1; });
