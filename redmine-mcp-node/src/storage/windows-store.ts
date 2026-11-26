import { createHash, randomBytes, randomUUID } from 'node:crypto';
import path from 'node:path';
import { Writable } from 'node:stream';
import { setTimeout as delay } from 'node:timers/promises';
import type { AttachmentStore, StagedDownload } from './store.js';
import { siteStorageKey } from './site-key.js';
import { isSweepDue, parseItemRecord, type ItemRecord } from './record.js';
import { openDirectory, openChildDirectory, createDirectory, openFile, closeHandle, verifyPrivateHandle, tryLock, readHandle, writeHandle, flushHandle, renameHandle, deleteHandle } from './windows-handles.js';

type H = unknown;
type Stage = { writer: Writable; handles: H[]; item: H; records: H; file: H; lock: H; record: ItemRecord; hash: ReturnType<typeof createHash>; size: number; done: boolean; published?: string };
function filename(name: string): void {
  if (!name || name === '.' || name === '..' || name.toLowerCase() === '.download.part' || /[<>:"/\\|?*\x00-\x1f]/.test(name) || /[. ]$/.test(name) || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(name)) throw new Error('Invalid attachment filename');
}
function timestamp(clock: () => Date): string { return clock().toISOString().replace('Z', '+00:00'); }
function closeAll(handles: H[]): void { const errors: unknown[] = []; for (const h of handles.reverse()) { try { closeHandle(h); } catch (error) { errors.push(error); } } handles.length = 0; if (errors.length) throw new AggregateError(errors, 'Cannot close attachment handles'); }
function child(parent: H, name: string, create: boolean, handles: H[]): H {
  let h: H;
  try { h = openChildDirectory(parent, name); } catch (error) { if (!create) throw error; h = createDirectory(parent, name); }
  handles.push(h); verifyPrivateHandle(h); return h;
}
function file(parent: H, name: string, mode: 'open' | 'create', access: 'read' | 'modify', handles: H[]): H {
  const h = openFile(parent, name, mode, access); handles.push(h);
  try { verifyPrivateHandle(h); } catch (error) {
    if (mode === 'create') { try { deleteHandle(h); } catch (cleanup) { throw new AggregateError([error, cleanup], 'Cannot clean up unverified staged file'); } }
    throw error;
  }
  return h;
}
function bounded(h: H, max: number): Buffer {
  if (!Number.isSafeInteger(max) || max < 0) throw new Error('Invalid size limit');
  const chunks: Buffer[] = []; let size = 0;
  while (true) { const b = readHandle(h, Math.min(65536, max - size + 1)); size += b.length; if (size > max) throw new Error('File exceeds size limit'); if (!b.length) return Buffer.concat(chunks); chunks.push(b); }
}
function jsonWrite(parent: H, name: string, record: ItemRecord, mode: 'publish' | 'refresh' | 'transition'): void {
  const durable = mode === 'transition';
  const tempName = `.record-${randomBytes(16).toString('hex')}.tmp`;
  const h = durable ? openDurableFile(parent, tempName, 'create') : openFile(parent, tempName, 'create');
  let renamed = false;
  try {
    verifyPrivateHandle(h);
    writeHandle(h, Buffer.from(JSON.stringify(record), 'utf8'));
    flushHandle(h);
    renameHandle(h, parent, name, mode !== 'publish', mode === 'refresh');
    renamed = true;
    if (durable) flushHandle(h);
  }
  catch (e) { if (!renamed) { try { deleteHandle(h); } catch {} } throw e; } finally { closeHandle(h); }
}
export function createWindowsAttachmentStore(options: {root: string; clock?: () => Date}): AttachmentStore & { sweep(now?: Date, signal?: Pick<AbortSignal, 'aborted'>): Promise<void> } {
  if (process.platform !== 'win32' || !path.win32.isAbsolute(options.root)) throw new Error('Windows absolute root required');
  const rootPath = path.win32.resolve(options.root), clock = options.clock ?? (() => new Date());
  const stages = new WeakSet<object>();
  function root(handles: H[]): H { const h = openDirectory(rootPath); handles.push(h); verifyPrivateHandle(h, true); return h; }
  function lock(parent: H, id: string, handles: H[]): H { const h = tryLock(parent, `${id}.lock`); if (!h) throw new Error('Attachment is locked'); handles.push(h); verifyPrivateHandle(h); return h; }
  return {
    async sweep(now = clock(), signal) {
      await sweepWindows(rootPath, root, now, signal);
    },
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
        jsonWrite(s.records, `${record.item_id}.json`, record, 'publish');
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
          const updated = {...rec, last_used_at: timestamp(clock)};
          const refresh = () => jsonWrite(records, `${parts[2]}.json`, updated, 'refresh');
          try { refresh(); }
          catch (error) {
            // On older filesystems a speculative reader may have to close before classic rename works.
            for (const wait of [10, 40]) {
              if (!(error instanceof OpenRecordReaderError)) break;
              await delay(wait);
              try { refresh(); return data; } catch (retryError) { error = retryError; }
            }
            // Only known storage exhaustion or a confirmed sharing conflict is recoverable.
            if (error instanceof Error && /^Cannot (?:write|flush) file \((?:39|112)\)$/.test(error.message)) return data;
            if (!(error instanceof Error) || !/^Cannot rename file \(NTSTATUS /.test(error.message)) throw error;
            verifyPrivateHandle(records);
            try { const probe = openFile(records, `${parts[2]}.json`, 'open', 'modify'); try { verifyPrivateHandle(probe); } finally { closeHandle(probe); } }
            catch (probeError) { if (probeError instanceof Error && probeError.message === 'Cannot open entry (NTSTATUS -1073741757)') return data; throw probeError; }
            throw error;
          }
          return data;
        }
        if (target.toLowerCase() === rootPath.toLowerCase()) throw new Error('Invalid source');
        if (uploadRoot === null) throw new Error('Upload root required for unmanaged source');
        { const boundary = path.win32.resolve(uploadRoot), relative = path.win32.relative(boundary, target); if (!relative || relative === '..' || relative.startsWith('..\\') || path.win32.isAbsolute(relative)) throw new Error('Source outside upload root'); const b = openDirectory(boundary); handles.push(b); verifyPrivateHandle(b, true); const components = relative.split(path.win32.sep); const leaf = components.pop()!; let current = b; for (const component of components) current = child(current, component, false, handles); const h = file(current, leaf, 'open', 'read', handles); return bounded(h, max); }
      } finally { closeAll(handles); }
    },
  };
}
import { readdir } from 'node:fs/promises';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { ZipFile as ZipWriter } from 'yazl';
import { RandomAccessReader, fromRandomAccessReaderPromise } from 'yauzl';
import { createRequire } from 'node:module';
import { OpenRecordReaderError, openGuardedFile, openReplaceableReadFile, openDurableFile, openDurableDirectory, verifyNtfs, seekHandle } from './windows-handles.js';
const crc32 = createRequire(import.meta.url)('buffer-crc32') as { unsigned(data: Buffer, previous: number): number };

function guarded(parent: H, name: string, handles: H[]): H {
  const h = openGuardedFile(parent, name); handles.push(h); verifyPrivateHandle(h); return h;
}
function durable(parent: H, name: string, mode: 'open' | 'create', handles: H[]): H {
  const h = openDurableFile(parent, name, mode); handles.push(h); verifyPrivateHandle(h); return h;
}
function readDueRecord(handle: H, key: string, id: string, now: number): ItemRecord | undefined {
  try {
    verifyPrivateHandle(handle);
    const record = parseItemRecord(JSON.parse(bounded(handle, 65536).toString('utf8')));
    if (record.site_key !== key || record.item_id !== id) throw new Error('Record identity mismatch');
    filename(record.filename);
    return isSweepDue(record, now) ? record : undefined;
  } finally { closeHandle(handle); }
}
function streamRange(h: H, start: number, end: number): Readable {
  let position = start;
  return new Readable({read() { try { if (position >= end) { this.push(null); return; } seekHandle(h, position); const chunk = readHandle(h, Math.min(65536, end-position)); if (!chunk.length) throw new Error('Unexpected end of file'); position += chunk.length; this.push(chunk); } catch (error) { this.destroy(error as Error); } }});
}
function fingerprint(h: H): {size: number; sha256: string} {
  seekHandle(h, 0); const hash = createHash('sha256'); let size = 0, b: Buffer;
  while ((b = readHandle(h, 65536)).length) { size += b.length; if (!Number.isSafeInteger(size)) throw new Error('File too large'); hash.update(b); }
  return {size, sha256:hash.digest('hex')};
}
function verifySource(h: H, r: ItemRecord): void { const v = fingerprint(h); if (v.size !== r.size || v.sha256 !== r.sha256) throw new Error('Original mismatch'); }
class HandleReader extends RandomAccessReader {
  constructor(private readonly handle: H) { super(); }
  _readStreamForRange(start: number, end: number): Readable { return streamRange(this.handle, start, end); }
  close(callback: (error: Error | null) => void): void { callback(null); }
}
async function verifyZip(h: H, r: ItemRecord): Promise<void> {
  const size = fingerprint(h).size;
  const zip = await fromRandomAccessReaderPromise(new HandleReader(h), size, {lazyEntries:true, autoClose:false, strictFileNames:true});
  try {
    if (zip.entryCount !== 1) throw new Error('Archive entry count mismatch');
    let count = 0;
    for await (const entry of zip.eachEntry()) {
      count++;
      if (entry.fileName !== r.filename || entry.uncompressedSize !== r.size) throw new Error('Archive member mismatch');
      const stream = await zip.openReadStreamPromise(entry); const hash = createHash('sha256'); let size = 0, crc = 0;
      for await (const raw of stream) { const chunk = Buffer.from(raw); size += chunk.length; if (size > r.size) throw new Error('Archive size exceeded'); hash.update(chunk); crc = crc32.unsigned(chunk, crc); }
      if (size !== r.size || hash.digest('hex') !== r.sha256 || crc !== entry.crc32) throw new Error('Archive content mismatch');
    }
    if (count !== 1) throw new Error('Archive entry missing');
  } finally { zip.close(); }
}
async function writeZip(source: H, output: H, record: ItemRecord): Promise<void> {
  const zip = new ZipWriter(); zip.addReadStream(streamRange(source, 0, record.size), record.filename, {size:record.size, forceZip64Format:true}); zip.end();
  await pipeline(zip.outputStream, new Writable({write(chunk: Buffer, _encoding, cb) { try { writeHandle(output, Buffer.from(chunk)); cb(); } catch (e) { cb(e as Error); } }}));
  flushHandle(output); await verifyZip(output, record);
}
async function sweepWindows(rootPath: string, openRoot: (handles: H[]) => H, now: Date, signal?: Pick<AbortSignal, 'aborted'>): Promise<void> {
  const nowMs = now.getTime();
  if (!Number.isFinite(nowMs)) throw new Error('Invalid sweep time');
  if (signal?.aborted) return;
  const outer: H[] = [];
  try {
    const root = openRoot(outer); verifyNtfs(root); const global = tryLock(root, '.sweep.lock');
    if (!global) return; outer.push(global); verifyPrivateHandle(global);
    for (const key of await readdir(rootPath)) {
      if (signal?.aborted) return;
      if (!/^[0-9a-f]{64}$/.test(key)) continue;
      const siteHandles: H[] = [];
      try {
        const site = child(root, key, false, siteHandles), records = child(site, 'records', false, siteHandles), locks = child(site, 'locks', false, siteHandles);
        for (const name of await readdir(path.win32.join(rootPath, key, 'records'))) {
          if (signal?.aborted) return;
          if (!/^[0-9a-f]{32}\.json$/.test(name)) continue;
          const handles: H[] = [], id = name.slice(0, -5);
          try {
            // A foreground borrow can refresh the record between this hint and the item lock.
            if (!readDueRecord(openReplaceableReadFile(records, name), key, id, nowMs)) continue;
            const itemLock = tryLock(locks, `${id}.lock`); if (!itemLock) continue; handles.push(itemLock); verifyPrivateHandle(itemLock);
            let rec = readDueRecord(openFile(records, name, 'open', 'read'), key, id, nowMs);
            if (!rec) continue;
            const archives = child(site, 'archives', rec.state === 'active', handles), zipName = `${id}.zip`;
            if (rec.state !== 'archived') {
              let source: H | undefined;
              let archive: H | undefined;
              try { const active = child(site, 'active', false, handles), item = child(active, id, false, handles); source = guarded(item, rec.filename, handles); }
              catch (e) { if (rec.state !== 'archive_published' || !(e instanceof Error) || !e.message.includes('NTSTATUS -1073741772')) throw e; }
              if (rec.state === 'active') {
                if (!source) throw new Error('Original missing');
                rec = {...rec, ...fingerprint(source)};
                try { archive = durable(archives, zipName, 'open', handles); }
                catch (e) { if (!(e instanceof Error) || !e.message.includes('NTSTATUS -1073741772')) throw e; }
                if (!archive) {
                  const temp = durable(archives, `.${id}.${randomUUID().replaceAll('-', '')}.tmp`, 'create', handles);
                  try { await writeZip(source, temp, rec); verifySource(source, rec); renameHandle(temp, archives, zipName); flushHandle(temp); }
                  catch (e) { try { deleteHandle(temp); } catch {} throw e; }
                  closeHandle(temp); handles.splice(handles.indexOf(temp), 1);
                  archive = durable(archives, zipName, 'open', handles);
                }
                await verifyZip(archive, rec); verifySource(source, rec);
                rec = {...rec, state:'archive_published', archived_at:timestamp(() => now)};
                jsonWrite(records, name, rec, 'transition');
              }
              archive ??= durable(archives, zipName, 'open', handles); await verifyZip(archive, rec);
              const barrierRecord = durable(records, name, 'open', handles);
              const current = parseItemRecord(JSON.parse(bounded(barrierRecord, 65536).toString('utf8')));
              if (JSON.stringify(current) !== JSON.stringify(rec)) throw new Error('Record changed');
              flushHandle(archive); flushHandle(barrierRecord);
              const rootParentPath = path.win32.dirname(rootPath), rootName = path.win32.basename(rootPath);
              if (rootParentPath === rootPath) throw new Error('Cannot synchronize root directory');
              const rootParent = openDirectory(rootParentPath); handles.push(rootParent);
              const rootBarrier = openDurableDirectory(rootParent, rootName); handles.push(rootBarrier); verifyPrivateHandle(rootBarrier, true); flushHandle(rootBarrier);
              for (const [parent, directory] of [[root, key], [site, 'archives'], [site, 'records']] as [H, string][]) {
                const h = openDurableDirectory(parent, directory); handles.push(h); verifyPrivateHandle(h); flushHandle(h);
              }
              closeHandle(barrierRecord); handles.splice(handles.indexOf(barrierRecord), 1);
              if (source) { verifySource(source, rec); await verifyZip(archive, rec); deleteHandle(source); }
              rec = {...rec, state:'archived'}; jsonWrite(records, name, rec, 'transition');
              continue;
            }
            const archiveDirectory = openDurableDirectory(site, 'archives'); handles.push(archiveDirectory); verifyPrivateHandle(archiveDirectory);
            const recordsDirectory = openDurableDirectory(site, 'records'); handles.push(recordsDirectory); verifyPrivateHandle(recordsDirectory);
            flushHandle(archiveDirectory); flushHandle(recordsDirectory);
            let archive: H | undefined;
            try { archive = durable(archives, zipName, 'open', handles); }
            catch (e) { if (!(e instanceof Error) || e.message !== 'Cannot open entry (NTSTATUS -1073741772)') throw e; }
            if (archive) await verifyZip(archive, rec);
            const recordHandle = durable(records, name, 'open', handles);
            const current = parseItemRecord(JSON.parse(bounded(recordHandle, 65536).toString('utf8')));
            if (JSON.stringify(current) !== JSON.stringify(rec)) throw new Error('Record changed');
            if (archive) { deleteHandle(archive); closeHandle(archive); handles.splice(handles.indexOf(archive), 1); }
            flushHandle(archiveDirectory); deleteHandle(recordHandle);
            closeHandle(recordHandle); handles.splice(handles.indexOf(recordHandle), 1); flushHandle(recordsDirectory);
          } catch { /* Retain uncertain items. */ } finally { closeAll(handles); }
        }
      } catch { /* Ignore unsafe or unavailable sites. */ } finally { closeAll(siteHandles); }
    }
  } catch { /* An unsafe or unavailable root is a no-op. */ } finally { closeAll(outer); }
}
