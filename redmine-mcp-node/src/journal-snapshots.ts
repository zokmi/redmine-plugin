import { randomBytes } from 'node:crypto';
import { setImmediate } from 'node:timers/promises';
import { isObject, type JsonObject } from './formatting.js';
import type { Site, SiteRegistry } from './sites.js';
import { TransportError } from './transport.js';

const TTL_MS = 60_000;
const MAX_ENTRIES = 32;
const MAX_BYTES = 32 * 1024 * 1024;

interface Query {
  site: Site;
  issueId: number;
  includes: string;
}
interface Read {
  query: Query;
  issue?: JsonObject;
}
interface Entry extends Read {
  issue: JsonObject;
  size: number;
  expires: number;
  timer: NodeJS.Timeout;
}

function* properties(object: JsonObject) {
  for (const key in object) {
    if (Object.hasOwn(object, key)) {
      yield key;
      yield object[key];
    }
  }
}

// Charge container and reference overhead as well as UTF-16 text, once per new
// snapshot. Yield during large histories so unrelated requests can still run.
async function retainedSize(issue: JsonObject, signal: AbortSignal): Promise<number> {
  const stack: Iterator<unknown>[] = [[issue].values()];
  let size = 0;
  let steps = 0;
  while (stack.length && size <= MAX_BYTES) {
    if (++steps % 4096 === 0) {
      await setImmediate();
      signal.throwIfAborted();
    }
    const next = stack[stack.length - 1].next();
    if (next.done) {
      stack.pop();
      continue;
    }
    const value = next.value;
    size += 32;
    if (typeof value === 'string') size += value.length * 2;
    else if (Array.isArray(value)) stack.push(value.values());
    else if (isObject(value)) stack.push(properties(value));
  }
  return size;
}

class JournalSnapshots {
  private readonly entries = new Map<string, Entry>();
  private readonly pending = new Set<Read>();
  private bytes = 0;

  private remove(token: string, entry: Entry): void {
    clearTimeout(entry.timer);
    this.entries.delete(token);
    this.bytes -= entry.size;
  }

  invalidate(site: Site, issueId?: number, subjectChanged = false): void {
    const affected = ({ query, issue }: Read) => {
      if (query.site !== site) return false;
      if (issueId === undefined || query.issueId === issueId) return true;
      if (!subjectChanged || !query.includes.includes('children')) return false;
      // Until the GET finishes we cannot tell which children it includes.
      return !issue || (Array.isArray(issue.children) && issue.children.some(
        child => isObject(child) && child.id === issueId,
      ));
    };
    for (const read of this.pending) if (affected(read)) this.pending.delete(read);
    for (const [token, entry] of this.entries) if (affected(entry)) this.remove(token, entry);
  }

  async page(
    query: Query, token: string | null, signal: AbortSignal,
    load: () => Promise<JsonObject>, format: (raw: JsonObject) => JsonObject,
  ): Promise<JsonObject> {
    if (token !== null) {
      const entry = this.entries.get(token);
      if (!entry || entry.expires <= performance.now() || entry.query.site !== query.site ||
          entry.query.issueId !== query.issueId || entry.query.includes !== query.includes) {
        throw new TransportError('invalid_request', '歷程快照已失效或查詢條件不同，請不帶快照從首頁重新查詢');
      }
      const detail = format(entry.issue);
      if ('journals_next_offset' in detail) detail.journals_snapshot = token;
      return detail;
    }
    const read: Read = { query };
    this.pending.add(read);
    const release = () => { this.pending.delete(read); };
    signal.addEventListener('abort', release, { once: true });
    try {
      const issue = await load();
      read.issue = issue;
      const detail = format(issue);
      if (!('journals_next_offset' in detail) || !this.pending.has(read)) return detail;
      const size = await retainedSize(issue, signal);
      if (size > MAX_BYTES || !this.pending.has(read)) return detail;
      while (this.entries.size >= MAX_ENTRIES || this.bytes + size > MAX_BYTES) {
        const [oldToken, oldest] = this.entries.entries().next().value!;
        this.remove(oldToken, oldest);
      }
      const nextToken = randomBytes(18).toString('base64url');
      const timer = setTimeout(() => this.remove(nextToken, entry), TTL_MS).unref();
      const entry: Entry = { query, issue, size, timer, expires: performance.now() + TTL_MS };
      this.entries.set(nextToken, entry);
      this.bytes += size;
      detail.journals_snapshot = nextToken;
      return detail;
    } finally {
      release();
      signal.removeEventListener('abort', release);
    }
  }
}

// HTTP sessions share the worker's registry. A write in another session must
// invalidate the same snapshots; standalone servers have separate registries.
const caches = new WeakMap<SiteRegistry, JournalSnapshots>();
export function journalSnapshots(sites: SiteRegistry): JournalSnapshots {
  let cache = caches.get(sites);
  if (!cache) {
    cache = new JournalSnapshots();
    caches.set(sites, cache);
  }
  return cache;
}
