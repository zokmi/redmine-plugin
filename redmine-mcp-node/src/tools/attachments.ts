import { basename, extname } from 'node:path';
import { createRequire } from 'node:module';
import { finished } from 'node:stream/promises';
const lookupMime = (createRequire(import.meta.url)('mime-types') as { lookup(name: string): string | false }).lookup;
import { formatAttachment, isObject, object, records, neutralizeUntrustedMarkers } from '../formatting.js';
import { fanOut, requestObject, type SiteRegistry } from '../sites.js';
import { ToolError, type ToolRegistry } from '../tool-registry.js';
import { attachmentContracts } from './attachment-contracts.js';
import type { AttachmentStore } from '../storage/store.js';

function filename(value: string): string {
  const name = value.replaceAll('\\', '/').split('/').pop()!.replace(/[\x00-\x1f\x7f<>:"|?*]/g, '').trim().replace(/^\.+|\.+$/g, '');
  return name || 'attachment';
}
function image(payload: string, max: number): [Buffer, string, string] {
  // Stream over the input once. Never allocate a normalized copy larger than the limit.
  const whitespace = (c: number) => (c >= 9 && c <= 13) || (c >= 28 && c <= 32) || c === 0x85 || c === 0xa0 || c === 0x1680 || (c >= 0x2000 && c <= 0x200a) || c === 0x2028 || c === 0x2029 || c === 0x202f || c === 0x205f || c === 0x3000;
  let pos = 0;
  while (pos < payload.length && whitespace(payload.charCodeAt(pos))) pos++;
  // The data URI prefix is consumed without copying the potentially unbounded MIME label.
  if (payload.slice(pos, pos + 5).toLowerCase() === 'data:') {
    let cursor = pos + 5;
    while (cursor < payload.length && payload[cursor] !== ';' && payload[cursor] !== ',') cursor++;
    if (payload.slice(cursor, cursor + 8).toLowerCase() === ';base64,') pos = cursor + 8;
  }
  const chunks: string[] = []; let chunk = ''; let count = 0; let padding = 0;
  const cap = Math.ceil(max / 3) * 4 + 4;
  for (; pos < payload.length; pos++) {
    const code = payload.charCodeAt(pos);
    if (whitespace(code)) continue;
    if (++count > cap) throw new ToolError('圖片內容超過大小上限');
    const char = payload[pos];
    if (char === '=') { if (++padding > 2) throw new ToolError('content_base64 不是合法的 base64 內容'); }
    else if (padding || !((code >= 65 && code <= 90) || (code >= 97 && code <= 122) || (code >= 48 && code <= 57) || char === '+' || char === '/')) throw new ToolError('content_base64 不是合法的 base64 內容');
    chunk += char;
    if (chunk.length === 4096) { chunks.push(chunk); chunk = ''; }
  }
  if (!count || count % 4 !== 0 || (padding && count < 4)) throw new ToolError('content_base64 不是合法的 base64 內容');
  chunks.push(chunk);
  const data = Buffer.from(chunks.join(''), 'base64');
  if (!data.length || data.length > max) throw new ToolError('圖片內容為空或超過大小上限');
  if (data.subarray(0, 8).equals(Buffer.from('89504e470d0a1a0a', 'hex'))) return [data, 'image/png', '.png'];
  if (data.subarray(0, 3).equals(Buffer.from('ffd8ff', 'hex'))) return [data, 'image/jpeg', '.jpg'];
  if (['GIF87a', 'GIF89a'].includes(data.subarray(0, 6).toString())) return [data, 'image/gif', '.gif'];
  if (data.subarray(0, 4).toString() === 'RIFF' && data.subarray(8, 12).toString() === 'WEBP') return [data, 'image/webp', '.webp'];
  throw new ToolError('content_base64 只接受 PNG／JPEG／GIF／WebP 圖片');
}
function mime(name: string): string {
  return lookupMime(name) || 'application/octet-stream';
}
export function registerAttachments(tools: ToolRegistry, sites: SiteRegistry, store: AttachmentStore, timeoutMs?: number): void {
  tools.register(attachmentContracts.list_attachments, async ({ issue_id, site }, signal) => {
    const result = await fanOut(sites.resolve(site), async (target, signal) => {
      const raw = await requestObject(target, 'GET', `/issues/${issue_id}.json`, { signal, query: { include: 'attachments' } });
      return { attachments: records(object(raw.issue).attachments).map(formatAttachment) };
    }, { signal, timeoutMs, noneOnNotFound: true });
    return { issue_id, found_in: result.foundIn, sites: result.payload };
  });
  tools.register(attachmentContracts.download_attachment, async ({ attachment_id, site }, signal) => {
    const targets = sites.resolve(site);
    const result = await fanOut(targets, async (target, signal) => object((await requestObject(target, 'GET', `/attachments/${attachment_id}.json`, { signal })).attachment), { signal, timeoutMs, noneOnNotFound: true });
    const errors = Object.fromEntries(Object.entries(result.payload).filter(([, value]) => isObject(value) && typeof value.error === 'string').map(([key, value]) => [key, (value as { error: string }).error]));
    if (!result.foundIn.length) throw new ToolError(Object.keys(errors).length ? `查詢附件 ${attachment_id} 時沒有任何站台命中，且部分站台查詢失敗（${Object.entries(errors).map(([name, reason]) => `${neutralizeUntrustedMarkers(name)}：${reason}`).join('；')}）` : `在所有已設定的站台都找不到附件 ${attachment_id}`);
    if (result.foundIn.length > 1) throw new ToolError(`附件 ${attachment_id} 在多個站台都存在（${result.foundIn.join('、')}），請明寫 site 指定要下載哪一個`);
    const name = result.foundIn[0]; const target = targets.get(name)!; const meta = object(result.payload[name]);
    if (typeof meta.content_url !== 'string') throw new ToolError('Redmine 未提供該附件的下載網址');
    let source: URL;
    try { source = new URL(meta.content_url); if (source.origin !== new URL(target.settings.url).origin || source.username || source.password) throw new Error(); } catch { throw new ToolError('附件下載網址與 Redmine 站台不同來源，基於安全考量拒絕下載'); }
    let safe = filename(typeof meta.filename === 'string' ? meta.filename : '');
    if (/^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(safe)) safe = '_' + safe;
    const staged = await store.stageDownload(target.settings.url, `${attachment_id}_${safe}`);
    try {
      signal.throwIfAborted();
      const bytes = await target.transport.download(source.href, staged.writer, target.settings.maxAttachmentBytes, signal);
      signal.throwIfAborted();
      const completion = finished(staged.writer, { cleanup: true, signal });
      staged.writer.end();
      await completion;
      signal.throwIfAborted();
      const path = await store.publish(staged);
      return { site: name, attachment_id, filename: neutralizeUntrustedMarkers(basename(path)), path, bytes, ...(Object.keys(errors).length ? { site_errors: errors } : {}) };
    } catch (error) { await store.discard(staged); throw error; }
  });
  tools.register(attachmentContracts.upload_attachment, async ({ file_path, content_base64, site, filename: requested }, signal) => {
    const [name, target] = sites.resolveForWrite(site);
    if (file_path && content_base64) throw new ToolError('file_path 與 content_base64 只能給一個');
    let bytes: Uint8Array, contentType: string, display: string;
    if (content_base64) {
      let ext: string; [bytes, contentType, ext] = image(content_base64, target.settings.maxAttachmentBytes);
      const candidate = filename(requested || 'attachment'); const suffix = extname(candidate).toLowerCase();
      display = suffix === ext || (ext === '.jpg' && suffix === '.jpeg') ? candidate : (suffix ? candidate.slice(0, -suffix.length) : candidate) + ext;
    } else if (file_path) {
      bytes = await store.borrowSource(file_path, target.settings.maxAttachmentBytes, target.settings.attachmentUploadRoot);
      if (!bytes.length || bytes.length > target.settings.maxAttachmentBytes) throw new ToolError('檔案內容為空或超過大小上限');
      display = filename(requested || file_path); contentType = mime(display);
    } else throw new ToolError('請給 file_path 或 content_base64 其中一個');
    const response = await target.transport.upload(display, bytes, signal); const token = object(object(response.data).upload).token;
    if (!token) throw new ToolError('Redmine 未回傳 upload token，請確認站台是否允許附件上傳');
    const reference = /[\s()<>]/.test(display) ? `![](<${display}>)` : `![](${display})`;
    return { site: name, token, filename: display, content_type: contentType, bytes: bytes.length, ...(contentType.startsWith('image/') ? { markdown: reference } : {}) };
  });
}
