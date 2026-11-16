import { array, isObject, nameText, object, paginated, pythonString, records, type JsonObject } from '../formatting.js';
import { clampLimit, fanOut, requestObject, type Site, type SiteRegistry } from '../sites.js';
import { contracts } from '../tool-contracts.js';
import type { ToolRegistry } from '../tool-registry.js';

function namedRows(raw: JsonObject, key: string): JsonObject[] {
  return records(raw[key]).map(({ id, name }) => ({ id: id ?? null, name: nameText(name) }));
}

function customFields(raw: JsonObject): JsonObject {
  return {
    custom_fields: records(raw.custom_fields).map((item) => {
      const values = array(item.possible_values);
      const possible = values.slice(0, 50).map((value) => nameText(pythonString(isObject(value) ? value.value : value)));
      return {
        id: item.id ?? null,
        name: nameText(item.name),
        customized_type: item.customized_type ?? null,
        field_format: item.field_format ?? null,
        possible_values: possible,
        ...(values.length > possible.length && {
          possible_values_total: values.length,
          possible_values_truncated: true,
        }),
      };
    }),
  };
}

export function registerMetadata(tools: ToolRegistry, sites: SiteRegistry, timeoutMs?: number): void {
  const caches = new Map<Site, Map<string, JsonObject>>();
  async function cached(
    site: Site, path: string, signal: AbortSignal, format: (raw: JsonObject) => JsonObject,
  ): Promise<JsonObject> {
    let cache = caches.get(site);
    if (!cache) {
      cache = new Map();
      caches.set(site, cache);
    }
    const hit = cache.get(path);
    if (hit) return hit;
    const value = format(await requestObject(site, 'GET', path, { signal }));
    signal.throwIfAborted();
    cache.set(path, value);
    return value;
  }
  async function across(
    name: string | null, signal: AbortSignal, fetch: (site: Site, signal: AbortSignal) => Promise<JsonObject>,
  ): Promise<JsonObject> {
    const { payload } = await fanOut(sites.resolve(name), fetch, { signal, timeoutMs });
    return { sites: payload };
  }

  tools.register(contracts.list_projects, ({ site, offset, limit }, signal) =>
    across(site, signal, async (target, signal) => {
      const raw = await requestObject(target, 'GET', '/projects.json', { signal, query: { offset, limit: clampLimit(limit) } });
      const rows = records(raw.projects).map(({ id, name, identifier }) => ({
        id: id ?? null, name: nameText(name), identifier: identifier ?? null,
      }));
      return paginated(raw, 'projects', rows);
    }),
  );

  const named = [
    { contract: contracts.list_trackers, path: '/trackers.json', key: 'trackers', outputKey: 'trackers' },
    { contract: contracts.list_issue_statuses, path: '/issue_statuses.json', key: 'issue_statuses', outputKey: 'issue_statuses' },
    { contract: contracts.list_priorities, path: '/enumerations/issue_priorities.json', key: 'issue_priorities', outputKey: 'priorities' },
  ];
  for (const { contract, path, key, outputKey } of named) {
    tools.register(contract, ({ site }, signal) =>
      across(site, signal, (target, signal) => cached(target, path, signal, (raw) => ({ [outputKey]: namedRows(raw, key) }))),
    );
  }
  tools.register(contracts.list_custom_fields, ({ site }, signal) =>
    across(site, signal, (target, signal) => cached(target, '/custom_fields.json', signal, customFields)),
  );
  tools.register(contracts.get_current_user, ({ site }, signal) =>
    across(site, signal, async (target, signal) => {
      const { user } = await requestObject(target, 'GET', '/users/current.json', { signal });
      const raw = object(user);
      const first = Object.hasOwn(raw, 'firstname') ? pythonString(raw.firstname) : '';
      const last = Object.hasOwn(raw, 'lastname') ? pythonString(raw.lastname) : '';
      return { id: raw.id ?? null, login: nameText(raw.login), name: nameText((first + last).trim()), mail: nameText(raw.mail) };
    }),
  );
  tools.register(contracts.list_sites, () => ({
    configured_sites: Array.from(sites.resolve().values(), ({ settings: { name, url, description } }) => ({
      name, url, description,
    })),
  }));
}
