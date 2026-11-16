import { createServer as httpServer } from 'node:http';
import { randomUUID, timingSafeEqual } from 'node:crypto';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import { isInitializeRequest } from '@modelcontextprotocol/sdk/types.js';
import { loadSettings } from './config.js';
import { createServer } from './server.js';
import { createSiteRegistry } from './sites.js';
import { createWindowsAttachmentStore } from './storage/windows-store.js';

async function main() {
  const token = process.env.REDMINE_MCP_WORKER_TOKEN ?? '';
  delete process.env.REDMINE_MCP_WORKER_TOKEN;
  if (!/^[A-Za-z0-9_-]{32,}$/.test(token)) throw new Error('Invalid worker token');
  const settings = await loadSettings(process.env);
  const registry = createSiteRegistry(settings);
  const root = process.env.REDMINE_MCP_STORAGE_ROOT;
  const attachmentStore = root && process.platform === 'win32' ? createWindowsAttachmentStore({root}) : undefined;
  const limit = Math.max(4 * 1024 * 1024, 4 * Math.ceil(Math.max(...Array.from(settings.sites.values(), site => site.maxAttachmentBytes)) / 3) + 1024 * 1024);
  const expected = Buffer.from(`Bearer ${token}`);
  const sessions = new Map<string, StreamableHTTPServerTransport>();
  const http = httpServer(async (req, res) => {
    try {
      if (req.url !== '/mcp') { res.writeHead(404).end(); return; }
      const auth = req.headersDistinct.authorization ?? [], supplied = Buffer.from(auth[0] ?? '');
      if (auth.length !== 1 || supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) { res.writeHead(401, {'www-authenticate':'Bearer'}).end(); return; }
      let body: unknown;
      if (req.method === 'POST') {
        const chunks: Buffer[] = []; let size = 0;
        for await (const chunk of req) { const b = Buffer.from(chunk); size += b.length; if (size > limit) { res.writeHead(413).end(); req.resume(); return; } chunks.push(b); }
        try { body = JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch { res.writeHead(400).end(); return; }
      }
      const id = req.headers['mcp-session-id'];
      let transport = typeof id === 'string' ? sessions.get(id) : undefined;
      if (!transport && !id && req.method === 'POST' && isInitializeRequest(body)) {
        transport = new StreamableHTTPServerTransport({sessionIdGenerator: randomUUID, onsessioninitialized: sid => { sessions.set(sid, transport!); }});
        const server = createServer(settings, {registry, attachmentStore});
        await server.connect(transport);
        const close = transport.onclose;
        transport.onclose = () => { if (transport?.sessionId) sessions.delete(transport.sessionId); close?.(); };
      }
      if (!transport) { res.writeHead(id ? 404 : 400).end(); return; }
      await transport.handleRequest(req, res, body);
    } catch { if (!res.headersSent) res.writeHead(500).end(); else res.destroy(); }
  });
  process.stdin.on('end', () => process.exit(0)); process.stdin.resume();
  http.listen(0, '127.0.0.1', () => console.log((http.address() as {port:number}).port));
}
main().catch(error => { console.error(error instanceof Error ? error.message : 'Worker startup failed'); process.exitCode = 1; });

