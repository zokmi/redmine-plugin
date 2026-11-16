import { createHash, randomBytes, randomUUID } from 'node:crypto';
import path from 'node:path';
import { Writable } from 'node:stream';
import type { AttachmentStore, StagedDownload } from './store.js';
import { siteStorageKey } from './site-key.js';
import { parseItemRecord, type ItemRecord } from './record.js';
import { openDirectory, openChildDirectory, createDirectory, openFile, closeHandle, verifyPrivateHandle, tryLock, readHandle, writeHandle, flushHandle, renameHandle, deleteHandle } from './windows-handles.js';

type H = unknown;
type Stage = { writer: Writable; handles: H[]; item: H; records: H; file: H; lock: H; record: ItemRecord; hash: ReturnType<typeof createHash>; size: number; done: boolean; published?: string };
function filename(name: string): void {
  if (!name || name === '.' || name === '..' || name.toLowerCase() === '.download.part' || /[<>:"/\\|?*\x00-\x1f]/.test(name) || /[. ]$/.test(name) || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(name)) throw new Error('Invalid attachment filename');
}
function timestamp(clock: () => Date): string { return clock().toISOString().replace('Z', '+00:00'); }
function closeAll(handles: H[]): void { for (const h of handles.reverse()) { try { closeHandle(h); } catch {} } handles.length = 0; }
function child(parent: H, name: string, create: boolean, handles: H[]): H {
  let h: H;
  try { h = openChildDirectory(parent, name); } catch (error) { if (!create) throw error; h = createDirectory(parent, name); }
  handles.push(h); verifyPrivateHandle(h); return h;
}
function file(parent: H, name: string, mode: 'open' | 'create', access: 'read' | 'modify', handles: H[]): H {
  const h = openFile(parent, name, mode, access); handles.push(h); verifyPrivateHandle(h); return h;
}
function bounded(h: H, max: number): Buffer {
  if (!Number.isSafeInteger(max) || max < 0) throw new Error('Invalid size limit');
  const chunks: Buffer[] = []; let size = 0;
  while (true) { const b = readHandle(h, Math.min(65536, max - size + 1)); size += b.length; if (size > max) throw new Error('File exceeds size limit'); if (!b.length) return Buffer.concat(chunks); chunks.push(b); }
}
function jsonWrite(parent: H, name: string, record: ItemRecord, replace: boolean): void {
  const h = openFile(parent, `.record-${randomBytes(16).toString('hex')}.tmp`, 'create');
  try { verifyPrivateHandle(h); writeHandle(h, Buffer.from(JSON.stringify(record), 'utf8')); flushHandle(h); renameHandle(h, parent, name, replace); }
  catch (e) { try { deleteHandle(h); } catch {} throw e; } finally { closeHandle(h); }
}
export function createWindowsAttachmentStore(options: {root: string; clock?: () => Date}): AttachmentStore {
  if (process.platform !== 'win32' || !path.win32.isAbsolute(options.root)) throw new Error('Windows absolute root required');
  const rootPath = path.win32.resolve(options.root), clock = options.clock ?? (() => new Date());
  const stages = new WeakSet<object>();
  function root(handles: H[]): H { const h = openDirectory(rootPath); handles.push(h); verifyPrivateHandle(h, true); return h; }
  function lock(parent: H, id: string, handles: H[]): H { const h = tryLock(parent, `${id}.lock`); if (!h) throw new Error('Attachment is locked'); handles.push(h); verifyPrivateHandle(h); return h; }
  return {
    async stageDownload(siteUrl, name) {
      filename(name); const key = siteStorageKey(siteUrl), id = randomUUID().replaceAll('-', ''), now = timestamp(clock), handles: H[] = [];
      try {
        const r = root(handles), site = child(r, key, true, handles), active = child(site, 'active', true, handles), item = child(active, id, true, handles), records = child(site, 'records', true, handles), locks = child(site, 'locks', true, handles);
        const l = lock(locks, id, handles), f = file(item, '.download.part', 'create', 'modify', handles);
        const stage = {handles, item, records, file:f, lock:l, size:0, done:false, hash:createHash('sha256'), record:{site_key:key,item_id:id,filename:name,created_at:now,last_used_at:now,state:'active',size:0,sha256:'',archived_at:null,schema_version:1}} as Stage;
        stage.writer = new Writable({write(chunk: Buffer, _encoding, cb) { try { if (stage.done) throw new Error('Stage closed'); const data = Buffer.from(chunk); writeHandle(f, data); stage.hash.update(data); stage.size += data.length; cb(); } catch (e) { cb(e as Error); } }});
        stages.add(stage); return stage;
      } catch (e) { closeAll(handles); throw e; }
    },
    async publish(value) {
      if (!stages.has(value)) throw new Error('Unknown stage'); const s = value as Stage;
      if (s.published) return s.published;
      if (s.done || !s.writer.writableFinished || s.writer.errored) throw new Error('Stage not ready');
      s.done = true;
      try {
        flushHandle(s.file); renameHandle(s.file, s.item, s.record.filename);
        const now = timestamp(clock); const record = parseItemRecord({...s.record, size:s.size, sha256:s.hash.digest('hex'), created_at:now, last_used_at:now});
        jsonWrite(s.records, `${record.item_id}.json`, record, false);
        s.published = path.win32.join(rootPath, record.site_key, 'active', record.item_id, record.filename); return s.published;
      } catch (e) { try { deleteHandle(s.file); } catch {} throw e; } finally { closeAll(s.handles); }
    },
    async discard(value) {
      if (!stages.has(value)) throw new Error('Unknown stage'); const s = value as Stage; if (s.done) return; s.done = true; s.writer.destroy(); try { deleteHandle(s.file); } finally { closeAll(s.handles); }
    },
    async borrowSource(source, max, uploadRoot) {
      if (!path.win32.isAbsolute(source)) throw new Error('Absolute source required');
      const target = path.win32.resolve(source), rel = path.win32.relative(rootPath, target), inside = rel !== '' && !rel.startsWith('..' + path.win32.sep) && rel !== '..' && !path.win32.isAbsolute(rel), handles: H[] = [];
      try {
        if (inside) {
          const parts = rel.split(path.win32.sep); if (parts.length !== 4 || !/^[0-9a-f]{64}$/.test(parts[0]) || parts[1] !== 'active' || !/^[0-9a-f]{32}$/.test(parts[2])) throw new Error('Invalid managed path');
          filename(parts[3]); const r = root(handles), site = child(r, parts[0], false, handles), locks = child(site, 'locks', false, handles); lock(locks, parts[2], handles);
          const records = child(site, 'records', false, handles), rh = file(records, `${parts[2]}.json`, 'open', 'read', handles), rec = parseItemRecord(JSON.parse(bounded(rh, 65536).toString('utf8')));
          if (rec.site_key !== parts[0] || rec.item_id !== parts[2] || rec.filename !== parts[3] || !['active','archive_published'].includes(rec.state)) throw new Error('Invalid managed record');
          const active = child(site, 'active', false, handles), item = child(active, parts[2], false, handles), fh = file(item, parts[3], 'open', 'read', handles), data = bounded(fh, max);
          closeHandle(rh); handles.splice(handles.indexOf(rh), 1);
          jsonWrite(records, `${parts[2]}.json`, {...rec,last_used_at:timestamp(clock)}, true); return data;
        }
        if (target.toLowerCase() === rootPath.toLowerCase()) throw new Error('Invalid source');
        if (uploadRoot !== null) { const boundary = path.win32.resolve(uploadRoot), relative = path.win32.relative(boundary, target); if (!relative || relative === '..' || relative.startsWith('..\\') || path.win32.isAbsolute(relative)) throw new Error('Source outside upload root'); const b = openDirectory(boundary); handles.push(b); verifyPrivateHandle(b, true); const components = relative.split(path.win32.sep); const leaf = components.pop()!; let current = b; for (const component of components) current = child(current, component, false, handles); const h = file(current, leaf, 'open', 'read', handles); return bounded(h, max); }
        const dir = openDirectory(path.win32.dirname(target)); handles.push(dir); const h = openFile(dir, path.win32.basename(target), 'open', 'read'); handles.push(h); return bounded(h, max);
      } finally { closeAll(handles); }
    },
  };
}
