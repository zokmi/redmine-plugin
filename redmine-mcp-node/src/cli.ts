import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { loadSettings } from './config.js';
import { createServer } from './server.js';
import { createWindowsAttachmentStore } from './storage/windows-store.js';

export async function dependencies() {
  const root = process.env.REDMINE_MCP_STORAGE_ROOT;
  return root && process.platform === 'win32' ? { attachmentStore: createWindowsAttachmentStore({root}) } : {};
}
async function main() {
  const args = process.argv.slice(2);
  if (!args.length) {
    // Config diagnostics must never corrupt stdio's JSON-RPC stream.
    console.warn = console.error;
    const settings = await loadSettings(process.env);
    const server = createServer(settings, await dependencies());
    await server.connect(new StdioServerTransport());
  } else if (args[0] === 'serve' && (args.length === 1 || (args.length === 3 && args[1] === '--port'))) {
    await import('./supervisor.js');
  } else throw new Error('Usage: redmine-mcp [serve [--port PORT]]');
}
main().catch(error => { console.error(error instanceof Error ? error.message : 'Startup failed'); process.exitCode = 1; });
