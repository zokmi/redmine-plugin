import { readFile, stat } from 'node:fs/promises';
import { validateHeaderValue } from 'node:http';
import { homedir } from 'node:os';
import { isAbsolute, join, parse as parsePath, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse } from '@ltd/j-toml';

export class ConfigError extends Error {
  override name = 'ConfigError';
}

export interface SiteSettings {
  readonly name: string;
  readonly url: string;
  readonly apiKey: string;
  readonly description: string | null;
  readonly maxAttachmentBytes: number;
  readonly allowInsecureHttp: boolean;
  readonly attachmentUploadRoot: string | null;
}

export interface Settings {
  readonly sites: ReadonlyMap<string, SiteSettings>;
}

type Table = Record<string, unknown>;
type Fields = Map<string, string>;

const SITE_NAME = /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/;
const MB = 1024 * 1024;
const LEGACY_KEYS = ['download_dir', 'upload_dir'];
const GLOBAL_KEYS = new Set(['max_attachment_mb']);
const SITE_KEYS = new Set([
  ...GLOBAL_KEYS, 'url', 'api_key', 'description', 'allow_insecure_http', 'attachment_upload_root',
]);
const LEGACY_CONFIG = fileURLToPath(new URL('../../redmine-mcp/config.toml', import.meta.url));

function userHome(env: NodeJS.ProcessEnv): string {
  if (process.platform === 'win32') {
    if (env.USERPROFILE) return env.USERPROFILE;
    if (env.HOMEDRIVE && env.HOMEPATH) return `${env.HOMEDRIVE}${env.HOMEPATH}`;
  } else if (env.HOME) {
    return env.HOME;
  }
  return homedir();
}

function expandHome(path: string, env: NodeJS.ProcessEnv): string {
  if (path === '~') return userHome(env);
  if (path.startsWith('~/') || (process.platform === 'win32' && path.startsWith('~\\'))) {
    return join(userHome(env), path.slice(2));
  }
  if (path.startsWith('~')) throw new ConfigError('無法展開設定中的使用者目錄');
  return path;
}

async function isFile(path: string): Promise<boolean> {
  try {
    return (await stat(path)).isFile();
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code === 'ENOENT' || code === 'ENOTDIR') return false;
    throw new ConfigError('無法讀取參數檔，請確認 REDMINE_CONFIG 與檔案權限');
  }
}

export async function configPath(env: NodeJS.ProcessEnv): Promise<string> {
  const explicit = env.REDMINE_CONFIG?.trim();
  if (explicit) return expandHome(explicit, env);
  if (await isFile(LEGACY_CONFIG)) return LEGACY_CONFIG;
  const base = process.platform === 'win32'
    ? env.APPDATA || join(userHome(env), 'AppData', 'Roaming')
    : env.XDG_CONFIG_HOME || join(userHome(env), '.config');
  return join(base, 'redmine-mcp', 'config.toml');
}

async function readConfig(env: NodeJS.ProcessEnv): Promise<Table> {
  const path = await configPath(env);
  if (!await isFile(path)) return {};
  let contents: Buffer;
  try {
    contents = await readFile(path);
  } catch {
    throw new ConfigError('無法讀取參數檔，請確認 REDMINE_CONFIG 與檔案權限');
  }
  let source: string;
  try {
    source = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(contents);
  } catch {
    throw new ConfigError('參數檔必須以 UTF-8 儲存，請用編輯器另存為 UTF-8 後重試');
  }
  try {
    return parse(source, { joiner: '\n', bigint: true, x: { order: true } });
  } catch {
    // Parser diagnostics can include input lines containing credentials.
    throw new ConfigError('參數檔格式錯誤，無法解析 TOML 內容');
  }
}

function isTable(value: unknown): value is Table {
  return Object.prototype.toString.call(value) === '[object Object]';
}

function collect(table: Table, allowed: ReadonlySet<string>, obsolete: Set<string>, where: string): Fields {
  const fields: Fields = new Map();
  for (const [key, value] of Object.entries(table)) {
    if (LEGACY_KEYS.includes(key)) {
      obsolete.add(key);
      continue;
    }
    if (!allowed.has(key)) {
      console.warn(`${where}中有無法識別的設定鍵，已忽略（請確認是否拼錯）`);
      continue;
    }
    if (key === 'allow_insecure_http' && typeof value !== 'boolean') {
      throw new ConfigError(`${where}的 allow_insecure_http 必須是 TOML 布林值`);
    }
    if (key !== 'allow_insecure_http' && (typeof value === 'boolean' || isTable(value) || Array.isArray(value))) {
      throw new ConfigError(`${where}的 ${key} 只接受單一值，不支援表格、陣列或布林值`);
    }
    // Keep TOML floats distinguishable from integers even when their value is integral.
    const asString = key === 'max_attachment_mb' && typeof value === 'number' ? `${value}.0`
      : value && typeof value === 'object' ? (value as { toISOString(): string }).toISOString()
      : String(value);
    fields.set(key, asString);
  }
  return fields;
}

export function validateSiteUrl(raw: string, allowInsecureHttp: boolean, where = '站台'): URL {
  let url: URL;
  try {
    const authority = /^https?:\/\/([^/\\?#\s]+)/i.exec(raw)?.[1];
    if (!authority || authority.includes('@') || /[\\\s]/u.test(raw)) throw new Error();
    url = new URL(raw);
  } catch {
    throw new ConfigError(`${where}的 url 必須是完整網址，且不能包含帳密`);
  }
  if (url.protocol === 'http:' && allowInsecureHttp !== true) {
    throw new ConfigError(`${where}預設要求 HTTPS，舊站請在該站設定 allow_insecure_http = true`);
  }
  return url;
}

function maxBytes(text: string, where: string): number {
  if (!text) return 10 * MB;
  const value = Number(text.replaceAll('_', ''));
  if (!/^[+-]?\d(?:_?\d)*$/.test(text) || !Number.isSafeInteger(value * MB) || value <= 0) {
    throw new ConfigError(`${where}的 max_attachment_mb 必須是大於 0 的整數（單位 MB）`);
  }
  return value * MB;
}

function siteSettings(name: string, fields: Fields, defaults: Fields, env: NodeJS.ProcessEnv, where: string): SiteSettings {
  const url = fields.get('url')?.trim();
  const apiKey = fields.get('api_key')?.trim();
  const allowInsecureHttp = fields.get('allow_insecure_http') === 'true';
  if (!url) throw new ConfigError(`${where}缺少必填設定 url（單站亦可使用 REDMINE_URL）`);
  validateSiteUrl(url, allowInsecureHttp, where);
  if (!apiKey) throw new ConfigError(`${where}缺少必填設定 api_key（單站亦可使用 REDMINE_API_KEY）`);
  try {
    validateHeaderValue('X-Redmine-API-Key', apiKey);
  } catch {
    throw new ConfigError(`${where}的 api_key 含有無效的字元`);
  }

  let attachmentUploadRoot: string | null = null;
  const uploadRoot = fields.get('attachment_upload_root')?.trim();
  if (uploadRoot) {
    const expanded = expandHome(uploadRoot, env);
    const { root } = parsePath(expanded);
    if (!isAbsolute(expanded) || (process.platform === 'win32' && root.length === 1) || expanded.includes('\0')) {
      throw new ConfigError(`${where}的 attachment_upload_root 必須是絕對路徑`);
    }
    attachmentUploadRoot = resolve(expanded);
    if (attachmentUploadRoot === parsePath(attachmentUploadRoot).root) {
      throw new ConfigError(`${where}的 attachment_upload_root 不能是磁碟根目錄`);
    }
  }
  return {
    name, url: url.replace(/\/+$/, ''), apiKey, allowInsecureHttp, attachmentUploadRoot,
    description: fields.get('description')?.trim() || null,
    maxAttachmentBytes: maxBytes(fields.get('max_attachment_mb')?.trim() || defaults.get('max_attachment_mb')?.trim() || '', where),
  };
}

export async function loadSettings(env: NodeJS.ProcessEnv, source?: Table): Promise<Settings> {
  const { sites: table, ...top } = source ?? await readConfig(env);
  const legacy = Object.hasOwn(top, 'url') || Object.hasOwn(top, 'api_key');
  if (table !== undefined && legacy) {
    throw new ConfigError('參數檔同時有最外層的 url／api_key 與 [sites.*] 區塊，請擇一');
  }

  const obsolete = new Set<string>();
  const fields = new Map<string, Fields>();
  const defaults = collect(top, legacy ? SITE_KEYS : GLOBAL_KEYS, obsolete, '參數檔');
  if (legacy) fields.set('default', new Map(defaults));
  const envKey = env.REDMINE_API_KEY?.trim();
  const envUrl = env.REDMINE_URL?.trim();
  const siteLabel = (name: string, apiKey: unknown) => {
    if (name === 'default') return '參數檔';
    if (typeof apiKey === 'string' && apiKey.trim() && name.includes(apiKey.trim())) return '站台';
    if (envKey && name.includes(envKey)) return '站台';
    return `站台 ${name}`;
  };
  if (table !== undefined) {
    if (!isTable(table)) throw new ConfigError('參數檔的 sites 必須是 [sites.<名稱>] 形式的表格');
    for (const [name, values] of Object.entries(table)) {
      if (!SITE_NAME.test(name)) throw new ConfigError('站台名稱必須以英數開頭，僅可包含英數、減號與底線，長度 1–64');
      if (!isTable(values)) throw new ConfigError('參數檔的 [sites.<名稱>] 必須是表格');
      fields.set(name, collect(values, SITE_KEYS, obsolete, siteLabel(name, values.api_key)));
    }
  }
  const envMax = env.REDMINE_MAX_ATTACHMENT_MB?.trim();
  if (envMax) defaults.set('max_attachment_mb', envMax);
  for (const name of ['REDMINE_DOWNLOAD_DIR', 'REDMINE_UPLOAD_DIR']) {
    if (Object.hasOwn(env, name)) obsolete.add(name);
  }
  if (obsolete.size) {
    console.warn(`附件已改為自動管理，舊設定 ${[...obsolete].sort().join('、')} 已忽略`);
  }

  if (!fields.size) fields.set('default', new Map());
  if (fields.size === 1) {
    const only = fields.values().next().value!;
    if (envUrl) {
      if (envUrl.replace(/\/+$/, '') !== only.get('url')?.trim().replace(/\/+$/, '')) {
        only.delete('allow_insecure_http');
      }
      only.set('url', envUrl);
    }
    if (envKey) only.set('api_key', envKey);
  } else if (envUrl || envKey) {
    const variables = [envUrl && 'REDMINE_URL', envKey && 'REDMINE_API_KEY'].filter(Boolean);
    console.warn(`多站台設定已忽略環境變數 ${variables.join('、')}，請使用 [sites.<名稱>] 區塊`);
  }
  const sites = new Map<string, SiteSettings>();
  for (const [name, values] of fields) {
    sites.set(name, siteSettings(name, values, defaults, env, siteLabel(name, values.get('api_key'))));
  }
  return { sites };
}
