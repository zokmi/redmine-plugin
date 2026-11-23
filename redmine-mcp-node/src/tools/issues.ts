import { isObject, neutralizeUntrustedMarkers, object, paginated, records, wrapUntrusted, type JsonObject } from '../formatting.js';
import { formatIssueDetail, formatIssueRow } from '../issue-formatting.js';
import { attachUploads, issuePayload, uncheckedFields, verifyPayload, warningFor } from '../issue-writes.js';
import { clampLimit, fanOut, redactSiteSecret, requestJson, requestObject, type SiteRegistry } from '../sites.js';
import { contracts } from '../tool-contracts.js';
import { ToolError, type ToolRegistry } from '../tool-registry.js';
import { TransportError } from '../transport.js';

const ALLOWED_INCLUDES = ['journals', 'attachments', 'relations', 'children', 'watchers'];
const MAX_TOTAL_ROWS = 200;
const createNextStep =
  '若使用者已授權發布診斷或評估，且有可查證的事實，' +
  '依後段模板（bug 用 stage=diagnose，change 用 stage=assess）撰寫，' +
  '內容填進 add_issue_note 的 notes，不要覆寫概述。' +
  '沒有查得到的事實就不要追加註記。' +
  '驗收標準與待確認在取得更新授權後以 update_issue ' +
  '補進概述對應的章節，不寫進註記。' +
  'kind=feature 沒有註記落點：概述是活文件，查到的事實與後續的需求修正一律以 ' +
  'update_issue 回頭改寫概述，資料表與關聯（stage=assess）寫進 DB 子單的概述。';

export function registerIssues(tools: ToolRegistry, sites: SiteRegistry, timeoutMs?: number): void {
  tools.register(contracts.list_issues, async ({
    site, limit, subject_keyword: subject, custom_field_filters: filters, ...queryFields
  }, signal) => {
    const targets = sites.resolve(site);
    const requested = clampLimit(limit);
    const effective = targets.size <= 1 ? requested : Math.min(requested, Math.ceil(MAX_TOTAL_ROWS / targets.size));
    const query = {
      ...Object.fromEntries(Object.entries(queryFields).filter(([, value]) => value !== null)),
      limit: effective,
      ...(subject && { subject: '~' + subject }),
      ...Object.fromEntries(Object.entries(filters ?? {}).map(([key, value]) => ['cf_' + key, value])),
    };
    const result = await fanOut(targets, async (target, signal) => {
      const raw = await requestObject(target, 'GET', '/issues.json', { signal, query });
      return paginated(raw, 'issues', records(raw.issues).map(formatIssueRow));
    }, { signal, timeoutMs });
    return {
      sites: result.payload,
      ...(effective < requested && {
        limit_per_site: effective,
        limit_reduced_for_multi_site: true,
        hint: `查詢了 ${targets.size} 個站台，為控制回傳量已把每站筆數調降為 ${effective}；需要更多結果請指定 site 或收斂篩選條件。`,
      }),
    };
  });

  tools.register(contracts.get_issue, async ({ issue_id, site, include, ...options }, signal) => {
    const invalid = (include ?? []).filter((name) => !ALLOWED_INCLUDES.includes(name)).map(neutralizeUntrustedMarkers);
    if (invalid.length) {
      throw new ToolError(`不支援的 include 值：${invalid.join(', ')}；可用值為 ${ALLOWED_INCLUDES.join(', ')}`);
    }
    const query = include?.length ? { include: include.join(',') } : {};
    const result = await fanOut(sites.resolve(site), async (target, signal) => {
      const raw = await requestObject(target, 'GET', `/issues/${issue_id}.json`, { signal, query });
      if (!isObject(raw.issue)) throw new Error('Invalid issue response');
      return formatIssueDetail(raw.issue, options);
    }, { signal, timeoutMs, noneOnNotFound: true });
    return { issue_id, found_in: result.foundIn, sites: result.payload };
  });

  tools.register(contracts.create_issue, async ({ site, uploads, ...fields }, signal) => {
    const [siteName, target] = sites.resolveForWrite(site);
    if (!fields.subject.trim()) throw new ToolError('subject 不可為空白');
    const issue = issuePayload(fields);
    const attached = attachUploads(issue, uploads);
    if (fields.assigned_to_id == null) {
      const { user } = await requestObject(target, 'GET', '/users/current.json', { signal });
      const id = object(user).id;
      if (typeof id !== 'number' || !Number.isSafeInteger(id) || id <= 0) {
        throw new ToolError('Redmine 未回傳有效的當前帳號 ID，無法指派負責人');
      }
      issue.assigned_to_id = id;
    }
    const raw = await requestObject(target, 'POST', '/issues.json', { signal, body: { issue } });
    const created = object(raw.issue);
    const verified = verifyPayload(issue, created);
    return {
      site: siteName, id: created.id ?? null, subject: wrapUntrusted(created.subject),
      uploads: attached, verified, next_step: createNextStep, ...warningFor(verified),
    };
  });

  tools.register(contracts.update_issue, async ({ issue_id, site, notes, uploads, ...fields }, signal) => {
    const [siteName, target] = sites.resolveForWrite(site);
    const issue = issuePayload(fields);
    if (notes?.trim()) issue.notes = notes;
    attachUploads(issue, uploads);
    const keys = Object.keys(issue).sort();
    if (!keys.length) throw new ToolError('至少要提供一個要更新的欄位');
    const path = `/issues/${issue_id}.json`;
    await requestJson(target, 'PUT', path, { signal, body: { issue } });
    const unchecked = uncheckedFields(issue);
    let verified: ReturnType<typeof verifyPayload>;
    if (keys.length === unchecked.length) {
      verified = { applied: [], ignored: [], unchecked };
    } else {
      try {
        const raw = await requestObject(target, 'GET', path, { signal });
        verified = verifyPayload(issue, object(raw.issue));
      } catch (error) {
        // The update succeeded. Keep the write result even when the later verification fails.
        const detail = error instanceof TransportError
          ? redactSiteSecret(error.message, target.settings)
          : '查詢此站台時發生非預期錯誤';
        verified = { error: `寫入已送出，但回查以確認結果時失敗：${detail}`, unchecked };
      }
    }
    return {
      site: siteName, issue_id, updated: true, fields: keys, verified, ...warningFor(verified),
    };
  });

  tools.register(contracts.add_issue_note, async ({ issue_id, site, notes, private_notes, uploads }, signal) => {
    const [siteName, target] = sites.resolveForWrite(site);
    if (!notes.trim()) throw new ToolError('notes 不可為空白');
    const issue: JsonObject = { notes, ...(private_notes && { private_notes }) };
    const attached = attachUploads(issue, uploads);
    await requestJson(target, 'PUT', `/issues/${issue_id}.json`, { signal, body: { issue } });
    return { site: siteName, issue_id, note_added: true, uploads: attached };
  });
}
