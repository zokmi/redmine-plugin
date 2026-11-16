import type { Settings, SiteSettings } from './config.js';
import { isObject, neutralizeUntrustedMarkers, pythonRepr, type JsonObject } from './formatting.js';
import { ToolError } from './tool-registry.js';
import { createTransport, TransportError, type JsonOptions, type JsonResponse, type RedmineTransport } from './transport.js';

const MAX_CONCURRENT_SITES = 8;
const PER_SITE_TIMEOUT_MS = 20_000;

export type TransportFactory = (site: SiteSettings) => RedmineTransport;

export interface Site {
  readonly settings: SiteSettings;
  readonly transport: RedmineTransport;
}

export interface SiteRegistry {
  readonly names: readonly string[];
  resolve(site?: string | null): ReadonlyMap<string, Site>;
  resolveForWrite(site?: string | null): readonly [string, Site];
  close(): void;
}

export function createSiteRegistry(settings: Settings, factory: TransportFactory = createTransport): SiteRegistry {
  const sites = new Map<string, Site>();
  let closed = false;
  function close() {
    if (closed) return;
    closed = true;
    for (const { transport } of sites.values()) {
      try {
        transport.close();
      } catch {
        // Continue closing the other sites.
      }
    }
  }
  try {
    for (const [name, site] of settings.sites) {
      sites.set(name, { settings: site, transport: factory(site) });
    }
  } catch (error) {
    close();
    throw error;
  }
  const names = Object.freeze([...sites.keys()]);
  const available = names.join('、');
  function lookup(name: string): Site {
    const site = sites.get(name);
    if (!site) throw new ToolError(`沒有名為 ${neutralizeUntrustedMarkers(pythonRepr(name))} 的站台（可用：${available}）`);
    return site;
  }
  return {
    names,
    resolve: (site) => site == null ? new Map(sites) : new Map([[site, lookup(site)]]),
    resolveForWrite(site) {
      if (site != null) return [site, lookup(site)];
      if (names.length !== 1) throw new ToolError(`已設定多個站台，寫入前請明寫 site（可用：${available}）`);
      return [names[0], sites.get(names[0])!];
    },
    close,
  };
}

export async function requestObject(
  site: Site, method: string, path: string, options: JsonOptions,
): Promise<JsonObject> {
  const { data } = await requestJson(site, method, path, options);
  return isObject(data) ? data : { data };
}

function httpErrorMessage(error: TransportError, path: string, site: SiteSettings): string {
  const { code, status, details } = error;
  if (code === 'validation' && status === 422) {
    const detail = details.map(neutralizeUntrustedMarkers).join('；') || '未提供詳細原因';
    return `Redmine 驗證失敗（HTTP 422）：${detail}`;
  }
  if (code === 'redirect' && status) {
    return `Redmine 回應非預期的轉址（HTTP ${status}），基於安全考量不跟隨`;
  }
  if (code === 'auth' && status) {
    return `API key 無效或權限不足（HTTP ${status}），請確認該站台參數檔 [sites.<代號>] 的 ` +
      'api_key 與該帳號權限（只設定單一站台時亦可用環境變數 REDMINE_API_KEY）';
  }
  if (code === 'not_found' && status === 404) {
    const basePath = new URL(site.url).pathname.replace(/\/+$/, '');
    return `找不到指定資源（HTTP 404）：${basePath}/${path.replace(/^\//, '')}`;
  }
  return error.message;
}

export async function requestJson(site: Site, method: string, path: string, options: JsonOptions): Promise<JsonResponse> {
  try {
    return await site.transport.json(method, path, options);
  } catch (error) {
    if (!(error instanceof TransportError)) throw error;
    const message = redactSiteSecret(httpErrorMessage(error, path, site.settings), site.settings);
    throw new TransportError(error.code, message, error.status);
  }
}

export function redactSiteSecret(text: string, site: SiteSettings): string {
  if (!site.apiKey) return text;
  return text.replaceAll(site.apiKey, '[REDACTED]')
    .replaceAll(neutralizeUntrustedMarkers(site.apiKey), '[REDACTED]');
}

type SiteResult<T> = { value: T | null } | { error: string };

export async function fanOut<T>(
  sites: ReadonlyMap<string, Site>,
  operation: (site: Site, signal: AbortSignal) => Promise<T>,
  options: { signal: AbortSignal; timeoutMs?: number; noneOnNotFound?: boolean },
): Promise<{ payload: Record<string, T | { error: string } | null>; foundIn: string[] }> {
  const entries = [...sites];
  const timeoutMs = options.timeoutMs ?? PER_SITE_TIMEOUT_MS;
  const results: SiteResult<T>[] = [];
  let next = 0;
  async function worker() {
    while (next < entries.length && !options.signal.aborted) {
      const index = next++;
      const [, site] = entries[index];
      const controller = new AbortController();
      const signal = AbortSignal.any([options.signal, controller.signal]);
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      let abort: () => void;
      try {
        const deadline = new Promise<never>((_, reject) => {
          abort = () => reject(signal.reason);
          signal.addEventListener('abort', abort, { once: true });
        });
        const request = Promise.resolve().then(() => {
          signal.throwIfAborted();
          return operation(site, signal);
        });
        results[index] = { value: await Promise.race([request, deadline]) };
      } catch (error) {
        if (controller.signal.aborted) {
          results[index] = { error: `此站台於 ${(timeoutMs / 1000).toFixed(0)} 秒內未回應（查詢逾時）` };
        } else if (error instanceof TransportError) {
          results[index] = options.noneOnNotFound && error.code === 'not_found'
            ? { value: null }
            : { error: redactSiteSecret(error.message, site.settings) };
        } else {
          results[index] = { error: '查詢此站台時發生非預期錯誤' };
        }
      } finally {
        clearTimeout(timer);
        signal.removeEventListener('abort', abort!);
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(entries.length, MAX_CONCURRENT_SITES) }, worker));
  options.signal.throwIfAborted();
  const foundIn: string[] = [];
  const payload = Object.fromEntries(entries.map(([name], index) => {
    const result = results[index];
    if ('error' in result) return [name, result];
    if (result.value !== null) foundIn.push(name);
    return [name, result.value];
  }));
  return { payload, foundIn };
}

export function clampLimit(limit: number): number {
  return Math.min(100, Math.max(1, limit));
}
