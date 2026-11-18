export async function dependencies() {
  const root = process.env.REDMINE_MCP_STORAGE_ROOT;
  if (!root || process.platform !== 'win32') return {};
  const { createWindowsAttachmentStore } = await import('./storage/windows-store.js');
  return { attachmentStore: createWindowsAttachmentStore({root}) };
}
async function main() {
  const args = process.argv.slice(2);
  if (!args.length) {
    // Config diagnostics must never corrupt stdio's JSON-RPC stream.
    console.warn = console.error;
    const { StdioServerTransport } = await import('@modelcontextprotocol/sdk/server/stdio.js');
    const { loadSettings } = await import('./config.js');
    const { createServer } = await import('./server.js');
    const settings = await loadSettings(process.env);
    const server = createServer(settings, await dependencies());
    await server.connect(new StdioServerTransport());
  } else if (args.length === 1 && args[0] === 'setup') {
    await (await import('./config-cli.js')).setupConfig(process.env);
  } else if (args.length === 1 && args[0] === 'doctor') {
    await (await import('./doctor.js')).runDoctor();
  } else if (args.length === 2 && args[0] === 'config' && args[1] === 'show') {
    await (await import('./config-cli.js')).showConfig(process.env);
  } else if (args[0] === 'config' && args.length >= 2) {
    await (await import('./config-cli.js')).editConfig(args.slice(1), process.env);
  } else if (args[0] === 'serve' && (args.length === 1 || (args.length === 3 && args[1] === '--port'))) {
    await import('./supervisor.js');
  } else throw new Error('Usage: redmine-mcp [doctor | serve [--port PORT]]');
}
main().catch(error => { console.error(error instanceof Error ? error.message : 'Startup failed'); process.exitCode = 1; });
