/** Validated subset of the Python attachment storage schema v1. No filesystem access. */
export interface ItemRecord {
  site_key: string; item_id: string; filename: string;
  created_at: string; last_used_at: string;
  state: 'active' | 'archive_published' | 'archived';
  size: number; sha256: string; archived_at: string | null; schema_version: 1;
}

function utcTimestamp(value: unknown): value is string {
  if (typeof value !== 'string') return false;
  // Python's isoformat() emits this form. Keep the original microseconds intact.
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?\+00:00$/.exec(value);
  if (!m) return false;
  const [, yy, mm, dd, hh, mi, ss] = m;
  const y = Number(yy), month = Number(mm), day = Number(dd);
  const leap = y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return y >= 1 && month >= 1 && month <= 12 && day >= 1 && day <= days[month - 1]
    && Number(hh) <= 23 && Number(mi) <= 59 && Number(ss) <= 59;
}

export function parseItemRecord(input: unknown): ItemRecord {
  if (input === null || typeof input !== 'object' || Array.isArray(input)) throw new Error('Invalid attachment record');
  const r = input as Record<string, unknown>;
  const bad = () => { throw new Error('Invalid attachment record'); };
  if (typeof r.site_key !== 'string' || !/^[0-9a-f]{64}$/.test(r.site_key)
      || typeof r.item_id !== 'string' || !/^[0-9a-f]{32}$/.test(r.item_id)
      || typeof r.filename !== 'string' || !r.filename || ['.', '..', '.download.part'].includes(r.filename)
      || /[/\\\0:]/.test(r.filename)
      || !utcTimestamp(r.created_at) || !utcTimestamp(r.last_used_at)
      || (r.state !== 'active' && r.state !== 'archive_published' && r.state !== 'archived')
      || !Number.isSafeInteger(r.size) || (r.size as number) < 0
      || typeof r.sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(r.sha256)
      || r.schema_version !== 1
      || (r.archived_at != null && !utcTimestamp(r.archived_at))
      || (r.state !== 'active' && r.archived_at == null)) bad();
  return {
    site_key: r.site_key as string, item_id: r.item_id as string, filename: r.filename as string,
    created_at: r.created_at as string, last_used_at: r.last_used_at as string,
    state: r.state as ItemRecord['state'], size: r.size as number, sha256: r.sha256 as string,
    archived_at: (r.archived_at ?? null) as string | null, schema_version: 1,
  };
}
