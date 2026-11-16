import {
  array, flattenRef, formatAttachment, neutralizeUntrustedMarkers, object, records, wrapUntrusted, type JsonObject,
} from './formatting.js';

export function formatIssueRow(raw: JsonObject): JsonObject {
  return {
    id: raw.id ?? null,
    subject: wrapUntrusted(raw.subject),
    status: flattenRef(raw.status),
    tracker: flattenRef(raw.tracker),
    assigned_to: flattenRef(raw.assigned_to),
    updated_on: raw.updated_on ?? null,
  };
}

function wrapFieldValue(value: unknown): unknown {
  if (typeof value === 'string') return wrapUntrusted(value);
  if (Array.isArray(value)) return value.map((entry) => typeof entry === 'string' ? wrapUntrusted(entry) : entry);
  return value ?? null;
}

function preview(value: string, limit: number, prefix: number) {
  // Count and slice Unicode code points, like Python's str.
  const chars = Array.from(value);
  if (chars.length <= limit) return wrapUntrusted(value);
  return { length: chars.length, preview: wrapUntrusted(chars.slice(0, prefix).join('')), truncated: true };
}

function formatJournalValue(value: unknown): unknown {
  return typeof value === 'string' ? preview(value, 500, 200) : wrapFieldValue(value);
}

function summaryText(value: unknown) {
  return typeof value === 'string' ? preview(value, 600, 600) : wrapUntrusted(value);
}

function formatJournal(raw: JsonObject, summary: boolean): JsonObject {
  const details = records(raw.details).map((item) => ({
    欄位: item.name ?? null,
    原值: formatJournalValue(item.old_value),
    新值: formatJournalValue(item.new_value),
  }));
  return {
    id: raw.id ?? null, user: flattenRef(raw.user), created_on: raw.created_on ?? null,
    ...(raw.notes ? { notes: summary ? summaryText(raw.notes) : wrapUntrusted(raw.notes) } : {}),
    ...(details.length ? { details } : {}),
  };
}

const relationKeys = ['id', 'issue_id', 'issue_to_id', 'relation_type', 'delay'];

export function formatIssueDetail(
  raw: JsonObject,
  { summary, journals_limit: limit, journals_offset: offset }: { summary: boolean; journals_limit: number; journals_offset: number },
): JsonObject {
  const detail: JsonObject = {
    id: raw.id ?? null,
    subject: wrapUntrusted(raw.subject),
    project: flattenRef(raw.project),
    status: flattenRef(raw.status),
    tracker: flattenRef(raw.tracker),
    updated_on: raw.updated_on ?? null,
    description: summary ? summaryText(raw.description) : wrapUntrusted(raw.description),
  };
  if (summary) {
    detail.summary = true;
  } else {
    Object.assign(detail, {
      priority: flattenRef(raw.priority),
      author: flattenRef(raw.author),
      assigned_to: flattenRef(raw.assigned_to),
      parent_id: object(raw.parent).id ?? null,
      done_ratio: raw.done_ratio ?? null,
      start_date: raw.start_date ?? null,
      due_date: raw.due_date ?? null,
      created_on: raw.created_on ?? null,
      custom_fields: Object.fromEntries(records(raw.custom_fields).map((item) => [
        neutralizeUntrustedMarkers(String(item.name || `#${item.id ?? 'None'}`)),
        { id: item.id ?? null, value: wrapFieldValue(item.value) },
      ])),
    });
  }
  const journals = records(raw.journals);
  if (journals.length) {
    const end = Math.max(0, journals.length - offset);
    const start = Math.max(0, end - limit);
    const kept = journals.slice(start, end);
    detail.journals = kept.map((journal) => formatJournal(journal, summary));
    if (start > 0) detail.journals_next_offset = offset + kept.length;
    if (kept.length < journals.length) {
      detail.journals_total = journals.length;
      detail.journals_truncated = true;
    }
  }
  const attachments = records(raw.attachments);
  if (attachments.length) detail.attachments = attachments.map(formatAttachment);
  const relations = records(raw.relations);
  if (relations.length) {
    detail.relations = relations.map((item) =>
      Object.fromEntries(relationKeys.filter((key) => Object.hasOwn(item, key)).map((key) => [key, item[key]])),
    );
  }
  const children = records(raw.children);
  if (children.length) detail.children = children.map(({ id, subject }) => ({ id: id ?? null, subject: wrapUntrusted(subject) }));
  const watchers = array(raw.watchers);
  if (watchers.length) detail.watchers = watchers.map(flattenRef);
  return detail;
}
