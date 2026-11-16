import { createHash } from 'node:crypto';
import { isIP } from 'node:net';
/** Python-compatible subset for ordinary ASCII site URLs. Unintegrated experimental helper. */
export function siteStorageKey(input: string): string {
  const invalid = () => { throw new Error('Invalid site URL'); };
  if (typeof input !== 'string' || /[^\x20-\x7e]/.test(input) || /[?#]/.test(input)) return invalid();
  const match = /^(https?):\/\/([^/]+)(\/.*)?$/i.exec(input.trim());
  if (!match || match[2].includes('@')) return invalid();
  const scheme = match[1].toLowerCase(), authority = match[2];
  let host: string, portText: string | undefined;
  if (authority.startsWith('[')) {
    const m = /^\[([^\]]+)\](?::([0-9]*))?$/.exec(authority);
    if (!m || isIP(m[1]) !== 6) return invalid();
    host = `[${m[1].toLowerCase()}]`; portText = m[2];
  } else {
    const m = /^([^:\[\]\\\s]+)(?::([0-9]*))?$/.exec(authority);
    if (!m) return invalid();
    host = m[1].toLowerCase(); portText = m[2];
  }
  const port = portText ? Number(portText) : undefined;
  if (port !== undefined && (!Number.isSafeInteger(port) || port > 65535)) return invalid();
  const suffix = port === undefined || port === (scheme === 'http' ? 80 : 443) ? '' : `:${port}`;
  const normalized = `${scheme}://${host}${suffix}${(match[3] ?? '').replace(/\/+$/, '')}`;
  return createHash('sha256').update(normalized, 'utf8').digest('hex');
}
