import { createHash, randomUUID } from 'node:crypto';
import { closeSync, constants as F, fstatSync, fsyncSync, readSync } from 'node:fs';
import { readdir } from 'node:fs/promises';
import path from 'node:path/posix';
import { Writable } from 'node:stream';
import { fingerprint, signature, verifyArchive, writeAll, writeArchive } from './posix-archive.js';
import { assertPrivateAcl, mkdirAt, openAt, openDirectory, renameAt, renameNewAt, tryFlock, unlinkAt } from './posix-handles.js';
import { isSweepDue, parseItemRecord, type ItemRecord } from './record.js';
import { siteStorageKey } from './site-key.js';
import type { AttachmentStore, StagedDownload } from './store.js';

const idPattern = /^[a-f0-9]{32}$/, sitePattern = /^[a-f0-9]{64}$/;
const uid = () => process.getuid!();
const timestamp = (date: Date) => date.toISOString().replace('Z', '+00:00');
const newId = () => randomUUID().replaceAll('-', '');
const code = (e: unknown) => (e as NodeJS.ErrnoException)?.code;
const sameRecord = (a: ItemRecord, b: ItemRecord) => JSON.stringify(a) === JSON.stringify(b);

class Handles {
  private readonly fds: number[] = [];
  keep(fd: number) { this.fds.push(fd); return fd; }
  close() {
    const errors = [];
    while (this.fds.length) {
      try { closeSync(this.fds.pop()!); } catch (error) { errors.push(error); }
    }
    if (errors.length) throw new AggregateError(errors, 'Cannot close attachment files');
  }
}

async function withHandles<T>(run: (h: Handles) => T | Promise<T>): Promise<T> {
  const handles = new Handles();
  try { return await run(handles); } finally { handles.close(); }
}

function checked(fd: number, directory: boolean, uploadSource = false): number {
  const s = fstatSync(fd);
  // The caller explicitly selected this upload root. Its descendants may be readable
  // by others (e.g. 0644), while group/world writes and hard links remain disallowed.
  const forbiddenMode = uploadSource ? 0o022 : 0o077;
  if (s.uid !== uid() || (s.mode & forbiddenMode) !== 0 || (directory ? !s.isDirectory() : !s.isFile() || s.nlink !== 1)) {
    throw new Error('Attachment storage requires private, user-owned directories and regular files');
  }
  assertPrivateAcl(fd, uploadSource);
  return fd;
}

function directory(h: Handles, parent: number, name: string, create = false, uploadSource = false): number {
  let fd;
  try { fd = openAt(parent, name, F.O_RDONLY | F.O_DIRECTORY); }
  catch (e) {
    if (!create || code(e) !== 'ENOENT') throw e;
    try { mkdirAt(parent, name); } catch (mkdirError) { if (code(mkdirError) !== 'EEXIST') throw mkdirError; }
    fsyncSync(parent);
    fd = openAt(parent, name, F.O_RDONLY | F.O_DIRECTORY);
  }
  return checked(h.keep(fd), true, uploadSource);
}

function file(h: Handles, parent: number, name: string, flags = F.O_RDONLY): number {
  return checked(h.keep(openAt(parent, name, flags)), false);
}
function optionalFile(h: Handles, parent: number, name: string): number | undefined {
  try { return file(h, parent, name); }
  catch (error) { if (code(error) !== 'ENOENT') throw error; }
}

// Check the name relative to its pinned parent. A mismatched entry stays untouched.
function assertNamed(parent: number, name: string, fd: number): void {
  const other = openAt(parent, name, F.O_RDONLY);
  try {
    const observed = fstatSync(other, { bigint: true }), expected = fstatSync(fd, { bigint: true });
    if (!observed.isFile() || observed.dev !== expected.dev || observed.ino !== expected.ino) {
      throw new Error('Attachment entry changed');
    }
  } finally { closeSync(other); }
}
function removeOwned(parent: number, name: string, fd: number) {
  assertNamed(parent, name, fd);
  unlinkAt(parent, name);
  fsyncSync(parent);
}
function publishNew(parent: number, temporary: string, name: string, fd: number) {
  fsyncSync(fd);
  assertNamed(parent, temporary, fd);
  renameNewAt(parent, temporary, parent, name);
  assertNamed(parent, name, fd);
  fsyncSync(parent);
}

function lock(h: Handles, parent: number, name: string): boolean {
  const fd = file(h, parent, name, F.O_RDWR | F.O_CREAT);
  if (!tryFlock(fd)) return false;
  assertNamed(parent, name, fd);
  return true;
}

function bounded(fd: number, max: number): Buffer {
  const stamp = signature(fd), size = fstatSync(fd).size;
  if (!Number.isSafeInteger(max) || max < 0 || !Number.isSafeInteger(size) || size > max) {
    throw new Error('Attachment exceeds size limit');
  }
  const data = Buffer.allocUnsafe(size);
  let offset = 0;
  while (offset < size) {
    const n = readSync(fd, data, offset, Math.min(size - offset, 65536), offset);
    if (!n) throw new Error('Attachment changed during read');
    offset += n;
  }
  if (signature(fd) !== stamp) throw new Error('Attachment changed during read');
  return data;
}

function readRecord(fd: number, site: string, id: string) {
  const record = parseItemRecord(JSON.parse(bounded(fd, 65536).toString('utf8')));
  if (record.site_key !== site || record.item_id !== id) throw new Error('Record identity mismatch');
  return record;
}
function assertRecord(parent: number, fd: number, expected: ItemRecord) {
  assertNamed(parent, expected.item_id + '.json', fd);
  if (!sameRecord(readRecord(fd, expected.site_key, expected.item_id), expected)) throw new Error('Record changed');
}

function writeRecord(parent: number, record: ItemRecord, prior?: { fd: number; record: ItemRecord }) {
  const name = record.item_id + '.json', tmp = '.record-' + newId() + '.tmp', h = new Handles();
  try {
    const fd = file(h, parent, tmp, F.O_RDWR | F.O_CREAT | F.O_EXCL);
    try {
      writeAll(fd, Buffer.from(JSON.stringify(record)));
      fsyncSync(fd);
      if (prior) {
        assertRecord(parent, prior.fd, prior.record);
        assertNamed(parent, tmp, fd);
        renameAt(parent, tmp, parent, name);
        fsyncSync(parent);
      } else publishNew(parent, tmp, name, fd);
    } catch (error) {
      try { removeOwned(parent, tmp, fd); } catch { /* A failed barrier may follow a successful rename. */ }
      throw error;
    }
  } finally { h.close(); }
}

function below(root: string, target: string): string[] | undefined {
  const rel = path.relative(root, target);
  if (!rel || rel === '..' || rel.startsWith('../') || path.isAbsolute(rel)) return undefined;
  return rel.split('/');
}

interface Stage extends StagedDownload {
  handles: Handles; item: number; records: number; fd: number;
  record: ItemRecord; size: number; hash: ReturnType<typeof createHash>; done: boolean; published?: string;
}

async function sweepItem(site: number, records: number, locks: number, key: string, id: string, now: Date) {
  const name = id + '.json', zipName = id + '.zip', currentTime = now.getTime();
  const candidate = await withHandles(h => readRecord(file(h, records, name), key, id));
  if (!isSweepDue(candidate, currentTime)) return;
  await withHandles(async h => {
    if (!lock(h, locks, id + '.lock')) return;
    let metadata = file(h, records, name), record = readRecord(metadata, key, id);
    if (!isSweepDue(record, currentTime)) return;
    const archives = directory(h, site, 'archives', record.state === 'active');
    let zip = optionalFile(h, archives, zipName);
    if (record.state !== 'archived') {
      const item = directory(h, directory(h, site, 'active'), id), source = optionalFile(h, item, record.filename);
      if (record.state === 'active') {
        if (source === undefined) throw new Error('Original missing');
        const snapshot = { ...record, ...await fingerprint(source) };
        if (zip === undefined) {
          const tmp = '.' + id + '.' + newId() + '.tmp';
          zip = file(h, archives, tmp, F.O_RDWR | F.O_EXCL | F.O_CREAT);
          try {
            await writeArchive(source, zip, snapshot);
            publishNew(archives, tmp, zipName, zip);
          } catch (error) {
            try { removeOwned(archives, tmp, zip); } catch { /* Retain published or uncertain files. */ }
            throw error;
          }
        }
        await verifyArchive(zip, snapshot);
        const published: ItemRecord = { ...snapshot, state: 'archive_published', archived_at: timestamp(now) };
        writeRecord(records, published, { fd: metadata, record });
        metadata = file(h, records, name);
        record = published;
      }
      if (zip === undefined) throw new Error('Published archive missing');
      const zipStamp = await verifyArchive(zip, record);
      // Re-establish both durable publications when recovering from a previous failed barrier.
      fsyncSync(zip); fsyncSync(metadata); fsyncSync(archives); fsyncSync(records); fsyncSync(site);
      assertNamed(archives, zipName, zip);
      if (signature(zip) !== zipStamp) throw new Error('Archive changed');
      if (source !== undefined) {
        const sourceStamp = signature(source), actual = await fingerprint(source);
        if (actual.size !== record.size || actual.sha256 !== record.sha256 || signature(source) !== sourceStamp) {
          throw new Error('Original changed; retain original and archive');
        }
        assertRecord(records, metadata, record);
        assertNamed(archives, zipName, zip);
        if (signature(zip) !== zipStamp) throw new Error('Archive changed');
        removeOwned(item, record.filename, source);
      }
      fsyncSync(item);
      const archived: ItemRecord = { ...record, state: 'archived' };
      writeRecord(records, archived, { fd: metadata, record });
      if (!isSweepDue(archived, currentTime)) return;
      metadata = file(h, records, name);
      record = archived;
    }
    // A missing ZIP can follow an interrupted expiry. Persist its absence first.
    if (zip !== undefined) {
      const stamp = await verifyArchive(zip, record);
      assertRecord(records, metadata, record);
      if (signature(zip) !== stamp) throw new Error('Archive changed');
      removeOwned(archives, zipName, zip);
    }
    fsyncSync(archives);
    assertRecord(records, metadata, record);
    removeOwned(records, name, metadata);
  });
}

export function createPosixAttachmentStore(options: { root: string; clock?: () => Date }):
AttachmentStore & { sweep(now?: Date, signal?: AbortSignal): Promise<void> } {
  if (!['linux', 'darwin'].includes(process.platform) || !path.isAbsolute(options.root) || options.root.split('/').includes('..')) {
    throw new Error('POSIX attachment storage requires an absolute local root');
  }
  const rootPath = path.normalize(options.root), clock = options.clock ?? (() => new Date());
  const root = (h: Handles, create = false) => checked(h.keep(openDirectory(rootPath, create)), true);
  const stages = new WeakMap<StagedDownload, Stage>();
  return {
    async stageDownload(url, name) {
      const now = timestamp(clock()), key = siteStorageKey(url), id = newId();
      const record = parseItemRecord({ schema_version: 1, site_key: key, item_id: id, filename: name,
        created_at: now, last_used_at: now, archived_at: null, state: 'active', size: 0, sha256: '0'.repeat(64) });
      const h = new Handles();
      try {
        const site = directory(h, root(h, true), key, true), active = directory(h, site, 'active', true);
        const item = directory(h, active, id, true), records = directory(h, site, 'records', true);
        if (!lock(h, directory(h, site, 'locks', true), id + '.lock')) throw new Error('Attachment locked');
        const fd = file(h, item, '.download.part', F.O_RDWR | F.O_CREAT | F.O_EXCL);
        const writer = new Writable({ write(chunk: Buffer, _enc, done) {
          try {
            if (stage.done) throw new Error('Stage closed');
            writeAll(fd, chunk); stage.hash.update(chunk); stage.size += chunk.length; done();
          } catch (error) { done(error as Error); }
        } });
        const stage: Stage = { handles: h, writer, item, records, fd, record, size: 0, hash: createHash('sha256'), done: false };
        stages.set(stage, stage);
        return stage;
      } catch (e) { h.close(); throw e; }
    },
    async publish(value) {
      const s = stages.get(value);
      if (s?.published) return s.published;
      if (!s || s.done || !s.writer.writableFinished || s.writer.errored) throw new Error('Stage not ready');
      s.done = true;
      try {
        const now = timestamp(clock());
        const record = { ...s.record, size: s.size, sha256: s.hash.digest('hex'), created_at: now, last_used_at: now };
        // POSIX file locks are advisory. Confirm the staged bytes before adopting the file.
        const actual = await fingerprint(s.fd);
        if (actual.size !== record.size || actual.sha256 !== record.sha256) throw new Error('Staged attachment changed');
        publishNew(s.item, '.download.part', record.filename, s.fd);
        writeRecord(s.records, record);
        s.published = path.join(rootPath, record.site_key, 'active', record.item_id, record.filename);
        return s.published;
      } catch (error) {
        // A directory flush can fail after the record was renamed. Only discard our
        // own staged inode when we can confirm no record was actually published.
        try {
          if (optionalFile(s.handles, s.records, s.record.item_id + '.json') === undefined) {
            for (const name of ['.download.part', s.record.filename]) {
              try { removeOwned(s.item, name, s.fd); } catch { /* Missing or replaced. */ }
            }
          }
        } catch { /* Uncertain record state: retain the payload. */ }
        throw error;
      } finally { s.handles.close(); }
    },
    async discard(value) {
      const s = stages.get(value);
      if (!s) throw new Error('Unknown stage');
      if (s.done) return;
      s.done = true; s.writer.destroy();
      try { removeOwned(s.item, '.download.part', s.fd); } finally { s.handles.close(); }
    },
    async borrowSource(source, max, uploadRoot) {
      if (!path.isAbsolute(source) || source.split('/').includes('..')) throw new Error('Absolute source required');
      const target = path.resolve(source), managed = below(rootPath, target);
      return withHandles(h => {
        if (managed) {
          const [key, active, id, name] = managed;
          if (managed.length !== 4 || !sitePattern.test(key) || active !== 'active' || !idPattern.test(id)) throw new Error('Invalid managed path');
          const site = directory(h, root(h), key), locks = directory(h, site, 'locks');
          if (!lock(h, locks, id + '.lock')) throw new Error('Attachment locked');
          const records = directory(h, site, 'records'), metadata = file(h, records, id + '.json');
          const record = readRecord(metadata, key, id);
          if (record.filename !== name || record.state === 'archived') throw new Error('Invalid managed record');
          const item = directory(h, directory(h, site, 'active'), id), data = bounded(file(h, item, name), max);
          try { writeRecord(records, { ...record, last_used_at: timestamp(clock()) }, { fd: metadata, record }); }
          catch (error) { if (!['ENOSPC', 'EDQUOT'].includes(code(error) ?? '')) throw error; }
          return data;
        }
        if (target === rootPath || !uploadRoot || !path.isAbsolute(uploadRoot)) throw new Error('Source outside upload root');
        const parts = below(uploadRoot, target);
        if (!parts) throw new Error('Source outside upload root');
        let parent = checked(h.keep(openDirectory(uploadRoot)), true, true);
        for (const component of parts.slice(0, -1)) parent = directory(h, parent, component, false, true);
        const fd = checked(h.keep(openAt(parent, parts.at(-1)!, F.O_RDONLY)), false, true);
        return bounded(fd, max);
      });
    },
    async sweep(now = clock(), signal) {
      if (!Number.isFinite(now.getTime())) throw new Error('Invalid sweep time');
      await withHandles(async h => {
        let r;
        try { r = root(h); } catch (error) { if (code(error) === 'ENOENT') return; throw error; }
        if (signal?.aborted || !lock(h, r, '.sweep.lock')) return;
        for (const key of await readdir(rootPath)) {
          if (signal?.aborted) return;
          if (!sitePattern.test(key)) continue;
          try {
            await withHandles(async siteHandles => {
              const site = directory(siteHandles, r, key), records = directory(siteHandles, site, 'records');
              const locks = directory(siteHandles, site, 'locks');
              for (const name of await readdir(path.join(rootPath, key, 'records'))) {
                if (signal?.aborted) return;
                if (!/^[a-f0-9]{32}\.json$/.test(name)) continue;
                try { await sweepItem(site, records, locks, key, name.slice(0, -5), now); }
                catch { /* An uncertain item remains available for recovery. */ }
              }
            });
          } catch { /* Other sites can still be processed. */ }
        }
      });
    },
  };
}
