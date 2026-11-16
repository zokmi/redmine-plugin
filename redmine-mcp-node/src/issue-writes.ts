import { neutralizeUntrustedMarkers, object, pythonRepr, pythonString, records, wrapUntrusted, type JsonObject } from './formatting.js';
import { ToolError } from './tool-registry.js';

const uploadKeys = ['token', 'filename', 'content_type', 'description'];
const imageExtensions = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp']);
const fencedBlock = /^(```+|~~~+)[\s\S]*?^\1/gm;
const inlineCode = /(`+)[^`\n]*\1/g;
const evidenceHeading = /(?:^|\n)##[ \t]*佐證[^\n]*/;
const nextHeading = /\n##[ \t]/;

type Upload = Record<string, string>;
interface CustomField {
  id: number;
  value: unknown;
}

function embedImages(text: string, uploads: Upload[]): string {
  if (!text.trim() || !uploads.length) return text;
  const prose = text.replace(fencedBlock, '').replace(inlineCode, '');
  const names = new Set<string>();
  for (const { filename, content_type: mime } of uploads) {
    if (!filename || prose.includes(filename)) continue;
    const contentType = mime?.trim().toLowerCase();
    const extension = filename.includes('.') ? filename.split('.').at(-1)?.toLowerCase() : undefined;
    if (contentType ? contentType.startsWith('image/') : extension && imageExtensions.has(extension)) names.add(filename);
  }
  if (!names.size) return text;
  const heading = evidenceHeading.exec(text);
  const headingEnd = heading ? heading.index + heading[0].length : text.length;
  const nextSection = nextHeading.exec(text.slice(headingEnd));
  const offset = nextSection ? headingEnd + nextSection.index : text.length;
  const head = text.slice(0, offset).trimEnd();
  const tail = text.slice(offset).trimStart();
  const references = Array.from(names, (name) => /[\s()<>]/.test(name) ? `![](<${name}>)` : `![](${name})`);
  return `${head}\n\n${references.join('\n')}` + (tail ? `\n\n${tail}` : '');
}

export function attachUploads(payload: JsonObject, input: Upload[] | null): number {
  if (!input?.length) return 0;
  const uploads = input.map((item, index) => {
    const invalid = Object.keys(item).filter((key) => !uploadKeys.includes(key)).map(neutralizeUntrustedMarkers);
    if (invalid.length) {
      throw new ToolError(`uploads 第 ${index + 1} 筆含不支援的欄位：${invalid.join(', ')}；可用欄位為 ${uploadKeys.join(', ')}`);
    }
    const token = (Object.hasOwn(item, 'token') ? item.token : '').trim();
    if (!token) throw new ToolError(`uploads 第 ${index + 1} 筆缺少 token，請先用 upload_attachment 取得`);
    const entry: Upload = { token };
    for (const key of ['filename', 'content_type', 'description']) {
      if (item[key]) entry[key] = item[key];
    }
    return entry;
  });
  payload.uploads = uploads;
  // A note owns its attachments when both the note and description were supplied.
  for (const field of ['notes', 'description']) {
    const text = payload[field];
    if (typeof text === 'string' && text) {
      payload[field] = embedImages(text, uploads);
      break;
    }
  }
  return uploads.length;
}

function validateDate(value: string | null | undefined, field: string): void {
  if (value == null) return;
  const quoted = neutralizeUntrustedMarkers(pythonRepr(value));
  if (value.length !== 10 || !/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(value)) {
    throw new ToolError(`${field} 必須是 YYYY-MM-DD 格式（收到：${quoted}）`);
  }
  const date = new Date(value + 'T00:00:00.000Z');
  if (value.startsWith('0000') || Number.isNaN(date.valueOf()) || date.toISOString().slice(0, 10) !== value) {
    throw new ToolError(`${field} 不是有效日期（收到：${quoted}）`);
  }
}

function validateDescription(value: string | null): void {
  if (value === null) return;
  const trimmed = value.trim();
  if (!trimmed) throw new ToolError('description 不可為空白；請說明哪個功能、什麼情況下、發生什麼事');
  const length = Array.from(trimmed).length;
  if (length < 10) {
    throw new ToolError(`description 太短（去除空白後 ${length} 字，至少需 10 字）；請先取得該單別的骨架` +
      '（redmine-issue-writing skill，或 ~/.redmine-issue-guides/SKILL.md）並逐個章節補齊');
  }
  if (/(?:^|\n)h[1-6]\.[ \t]/.test(value)) {
    throw new ToolError('description 使用了 textile 語法（行首的 h1.～h6. 標題），' +
      '站台的內文格式是 Markdown，這類標題會原樣顯示成純文字；請改用 Markdown 的 ## 標題');
  }
}

// Python int() accepts Unicode decimal digits as well as ASCII digits.
function decimalDigit(char: string): string {
  let point = char.codePointAt(0)!;
  let offset = 0;
  while (/\p{Decimal_Number}/u.test(String.fromCodePoint(point - 1))) {
    point--;
    offset++;
  }
  return String(offset % 10);
}

function customFieldId(key: string): number {
  const candidate = key.trim().replace(/\p{Decimal_Number}/gu, decimalDigit);
  const number = Number(candidate.replaceAll('_', ''));
  if (!/^[+-]?\d+(?:_\d+)*$/.test(candidate) || !Number.isSafeInteger(number)) {
    throw new ToolError(`custom_fields 的 key 必須是自訂欄位的數字 id（收到：${neutralizeUntrustedMarkers(pythonRepr(key))}），` +
      '可用 list_custom_fields 查詢欄位 id');
  }
  return number;
}

export function issuePayload({
  project_id, subject, description, tracker_id, status_id, priority_id, assigned_to_id, parent_issue_id,
  done_ratio, start_date, due_date, custom_fields,
}: {
  project_id?: string; subject: string | null; description: string | null; tracker_id: number | null;
  status_id: number | null; priority_id: number | null; assigned_to_id: number | null; parent_issue_id: number | null;
  done_ratio?: number | null; start_date: string | null; due_date: string | null; custom_fields: JsonObject | null;
}): JsonObject {
  validateDate(start_date, 'start_date');
  validateDate(due_date, 'due_date');
  validateDescription(description);
  const fields = {
    project_id, subject, description, tracker_id, status_id, priority_id, assigned_to_id, parent_issue_id,
    done_ratio, start_date, due_date,
  };
  const payload = Object.fromEntries(Object.entries(fields).filter(([, value]) => value != null));
  const customFields = Object.entries(custom_fields ?? {}).map(([id, value]) => ({ id: customFieldId(id), value }));
  return customFields.length ? { ...payload, custom_fields: customFields } : payload;
}

const nestedIds = new Map([
  ['tracker_id', 'tracker'], ['status_id', 'status'], ['priority_id', 'priority'],
  ['assigned_to_id', 'assigned_to'], ['parent_issue_id', 'parent'],
]);
const comparable = new Set([
  ...nestedIds.keys(), 'subject', 'start_date', 'due_date', 'done_ratio', 'project_id', 'description', 'custom_fields',
]);

type Ignored = { field: string; sent: unknown; actual: unknown };
type Verification = { unchecked: string[] } & ({ applied: string[]; ignored: Ignored[] } | { error: string });

function sameValue(sent: unknown, actual: unknown): boolean {
  const comparableText = (value: unknown) => value == null ? '' : pythonString(value).trim();
  return comparableText(sent) === comparableText(actual);
}

function normalizedText(value: unknown): string {
  return pythonString(value ?? '').replace(/\r\n?/g, '\n').trim();
}

function ignoredCustomFields(sent: CustomField[], actual: unknown): Ignored[] {
  const byId = new Map(records(actual).map(({ id, value }) => [id, value]));
  return sent.flatMap(({ id, value }) => {
    const current = byId.get(id);
    return sameValue(value, current) ? [] : [{ field: `custom_field_${id}`, sent: value, actual: current }];
  });
}

function checkedValue(field: string, sent: unknown, issue: JsonObject): Ignored[] {
  if (field === 'custom_fields') return ignoredCustomFields(sent as CustomField[], issue.custom_fields);
  if (field === 'project_id') {
    const project = object(issue.project);
    if ([project.id, project.identifier, project.name].some((value) => sameValue(sent, value))) return [];
    return [{ field, sent, actual: project.id }];
  }
  const nested = nestedIds.get(field);
  const actual = nested ? object(issue[nested]).id : issue[field];
  const same = field === 'description' ? normalizedText(sent) === normalizedText(actual) : sameValue(sent, actual);
  return same ? [] : [{ field, sent, actual }];
}

export function uncheckedFields(payload: JsonObject): string[] {
  return Object.keys(payload).filter((field) => !comparable.has(field)).sort();
}

function formattedIgnoredValue(value: unknown): unknown {
  return typeof value === 'string' ? wrapUntrusted(value) : value ?? null;
}

export function verifyPayload(payload: JsonObject, issue: JsonObject): Verification {
  const unchecked = uncheckedFields(payload);
  if (!Object.keys(issue).length) {
    return { error: 'Redmine 未回傳 issue 內容，本次無法確認欄位是否實際套用', unchecked };
  }
  const applied: string[] = [];
  const ignored: Ignored[] = [];
  for (const [field, sent] of Object.entries(payload)) {
    if (!comparable.has(field)) continue;
    const mismatches = checkedValue(field, sent, issue);
    if (!mismatches.length) applied.push(field);
    for (const item of mismatches) {
      ignored.push({ field: item.field, sent: formattedIgnoredValue(item.sent), actual: formattedIgnoredValue(item.actual) });
    }
  }
  return { applied: applied.sort(), ignored, unchecked };
}

export function warningFor(verified: Verification): { warning: string } | Record<string, never> {
  if (!('ignored' in verified) || !verified.ignored.length) return {};
  const detail = verified.ignored.map(({ field, sent, actual }) =>
    `${field} 未套用（送出 ${pythonRepr(sent)}，實際 ${pythonRepr(actual)}）`,
  ).join('；');
  return { warning: detail + '。Redmine 會靜默丟棄專案未啟用或無權限的值' +
    '（例如該專案未啟用此 tracker、workflow 不允許此狀態轉換、' +
    '指定的負責人不是專案成員），HTTP 仍會回報成功。請確認後重送，不要當成已完成。' };
}
