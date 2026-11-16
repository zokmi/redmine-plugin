import { createServer as httpServer } from 'node:http';
import { randomUUID, timingSafeEqual } from 'node:crypto';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import { isInitializeRequest } from '@modelcontextprotocol/sdk/types.js';
import type { Settings } from './config.js';
import { createServer } from './server.js';
import { createSiteRegistry } from './sites.js';
import type { AttachmentStore } from './storage/store.js';

type Session = {transport: StreamableHTTPServerTransport; active: number; lastActive: number; closing: boolean};
export function createWorkerHttp(settings: Settings, token: string, options: {attachmentStore?: AttachmentStore; maxSessions?: number; idleMs?: number; sweepMs?: number} = {}) {
  const maxSessions = options.maxSessions ?? 128;
  const idleMs = options.idleMs ?? 30 * 60_000;
  const sweepMs = options.sweepMs ?? 60_000;
  const registry = createSiteRegistry(settings);
  const limit = Math.max(4 * 1024 * 1024, 4 * Math.ceil(Math.max(...Array.from(settings.sites.values(), site => site.maxAttachmentBytes)) / 3) + 1024 * 1024);
  const expected = Buffer.from(`Bearer ${token}`);
  const sessions = new Map<string, Session>();
  const all = new Set<Session>();
  let shuttingDown = false;
  function release(entry: Session) {
    all.delete(entry);
    const id = entry.transport.sessionId;
    if (id && sessions.get(id) === entry) sessions.delete(id);
  }
  async function closeEntry(entry: Session) {
    if (entry.closing) return;
    entry.closing = true;
    release(entry);
    await entry.transport.close();
  }
  const timer = setInterval(() => {
    const cutoff = Date.now() - idleMs;
    for (const entry of all) if (entry.active === 0 && entry.lastActive <= cutoff) void closeEntry(entry).catch(() => {});
  }, sweepMs);
  timer.unref();
  const http = httpServer(async (req, res) => {
    let created: Session | undefined;
    try {
      if (req.url !== '/mcp') { res.writeHead(404).end(); return; }
      const auth = req.headersDistinct.authorization ?? [], supplied = Buffer.from(auth[0] ?? '');
      if (auth.length !== 1 || supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) { res.writeHead(401, {'www-authenticate':'Bearer'}).end(); return; }
      const id = req.headers['mcp-session-id'];
      let entry = typeof id === 'string' ? sessions.get(id) : undefined;
      if (id && !entry) { res.writeHead(404).end(); return; }
      function track(current: Session, initializing: boolean) {
        current.active++;
        res.once('close', () => {
          current.active--; current.lastActive = Date.now();
          if (initializing && (!res.writableFinished || !current.transport.sessionId)) void closeEntry(current).catch(() => {});
        });
      }
      if (entry) track(entry, false);
      let body: unknown;
      if (req.method === 'POST') {
        const chunks: Buffer[] = []; let size = 0;
        for await (const chunk of req) { const b = Buffer.from(chunk); size += b.length; if (size > limit) { res.writeHead(413).end(); req.resume(); return; } chunks.push(b); }
        try { body = JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch { res.writeHead(400).end(); return; }
      }
      if (shuttingDown) { if (!res.destroyed) res.writeHead(503).end(); return; }
      if (!entry && !id && req.method === 'POST' && isInitializeRequest(body)) {
        if (all.size >= maxSessions) { res.writeHead(503).end(); return; }
        const transport = new StreamableHTTPServerTransport({sessionIdGenerator: randomUUID, onsessioninitialized: sid => { if (!entry!.closing) sessions.set(sid, entry!); }});
        entry = {transport, active: 0, lastActive: Date.now(), closing: false};
        created = entry; all.add(entry);
        track(entry, true);
        const mcp = createServer(settings, {registry, attachmentStore: options.attachmentStore});
        await mcp.connect(transport);
        const priorClose = transport.onclose;
        transport.onclose = () => { release(entry!); priorClose?.(); };
      }
      if (!entry) { res.writeHead(id ? 404 : 400).end(); return; }
      const current = entry;
      if (res.destroyed || current.closing) { if (created) await closeEntry(current); return; }
      await current.transport.handleRequest(req, res, body);
      if (created && !current.transport.sessionId) await closeEntry(current);
    } catch {
      if (created) await closeEntry(created).catch(() => {});
      if (!res.headersSent && !res.destroyed) res.writeHead(500).end(); else res.destroy();
    }
  });
  return {
    async listen(): Promise<number> {
      await new Promise<void>((resolve, reject) => { http.once('error', reject); http.listen(0, '127.0.0.1', () => { http.off('error', reject); resolve(); }); });
      return (http.address() as {port:number}).port;
    },
    async close(): Promise<void> {
      shuttingDown = true;
      clearInterval(timer);
      const stopped = new Promise<void>(resolve => http.close(() => resolve()));
      http.closeAllConnections();
      await Promise.allSettled([...all].map(closeEntry));
      await stopped;
      registry.close();
    },
  };
}
