import { readFile } from 'node:fs/promises';
import { parse } from '@ltd/j-toml';
import { configPath, ConfigError } from './config.js';

export async function showConfig(env: NodeJS.ProcessEnv): Promise<void> {
  const path = await configPath(env);
  let raw: Record<string, unknown>;
  try {
    const source = new TextDecoder('utf-8', {fatal: true}).decode(await readFile(path));
    raw = parse(source, {joiner: '\n', bigint: true}) as Record<string, unknown>;
  } catch { throw new ConfigError('無法讀取或解析參數檔，請確認檔案存在且為 UTF-8 TOML'); }
  const table = raw.sites;
  if (table !== undefined && (table === null || typeof table !== 'object' || Array.isArray(table))) throw new ConfigError('參數檔的 sites 必須是表格');
  if (table !== undefined && ('url' in raw || 'api_key' in raw)) throw new ConfigError('參數檔不可混用舊格式與站台表格');
  const sites = table === undefined ? (('url' in raw || 'api_key' in raw) ? {default: raw} : {}) : table as Record<string, unknown>;
  const keys = Object.values(sites).flatMap(value => value && typeof value === 'object' && typeof (value as Record<string, unknown>).api_key === 'string' ? [(value as Record<string, string>).api_key] : []).filter(Boolean);
  const safe = (value: unknown) => {
    let output = typeof value === 'string' ? value : '（未設定）';
    for (const key of keys) output = output.replaceAll(key, '****');
    return output.replace(/[\r\n\x00-\x1f\x7f]/g, ' ');
  };
  console.log('全域設定：');
  console.log(`  max_attachment_mb = ${typeof raw.max_attachment_mb === 'bigint' || typeof raw.max_attachment_mb === 'number' ? raw.max_attachment_mb : '（未設定）'}`);
  console.log('站台：');
  for (const [name, value] of Object.entries(sites).sort(([a], [b]) => a.localeCompare(b))) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new ConfigError('參數檔的站台必須是表格');
    const site = value as Record<string, unknown>;
    console.log(`  ${safe(name)}  ${safe(site.url)}  ****  ${safe(site.description)}`);
  }
}

import { isDeepStrictEqual } from 'node:util';
import { loadSettings } from './config.js';
import { writePrivate } from './config-write.js';
import { createTransport } from './transport.js';
import { createInterface } from 'node:readline/promises';

function comparable(value: any): any {
  if (Array.isArray(value)) return value.map(comparable);
  if (value instanceof Date) return value.toISOString();
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, comparable(item)]));
  return value;
}
function same(a: unknown, b: unknown): boolean { return isDeepStrictEqual(comparable(a), comparable(b)); }
function parsed(text: string): Record<string, any> {
  try { return parse(text, {joiner:'\n', bigint:true}) as Record<string, any>; }
  catch { throw new ConfigError('參數檔格式錯誤，未修改'); }
}
function editLines(text: string, section: string | null, changes: Record<string, string | number | null>, remove = false): string {
  const lines = text.match(/[^\n]*\n|[^\n]+$/g) ?? [];
  function statements(): {start: number, end: number, header: boolean}[] {
    const result: {start: number, end: number, header: boolean}[] = [];
    for (let i = 0; i < lines.length;) {
      if (!lines[i].trim() || lines[i].trimStart().startsWith('#')) { i++; continue; }
      const start = i; let body = ''; let complete = false;
      while (i < lines.length) {
        body += lines[i++];
        try { parsed(body); complete = true; break; } catch { /* more lines may complete the statement */ }
      }
      if (!complete) throw new ConfigError('無法安全辨識 TOML 敘述，請手動修改');
      result.push({start, end:i, header:body.trimStart().startsWith('[')});
    }
    return result;
  }
  const spans = statements();
  let found = section === null, start = -1, end = lines.length;
  for (const item of spans) {
    if (!item.header) continue;
    if (section === null) { end = item.start; break; }
    if (start >= 0) { end = item.start; break; }
    const match = /^\[sites\.([A-Za-z0-9_-]+)\]\s*(?:#.*)?$/.exec(lines[item.start].trim());
    if (match?.[1] === section) { found = true; start = item.start; }
  }
  if (!found) throw new ConfigError('找不到可安全編輯的站台區塊');
  if (remove) {
    while (end > start + 1 && (!lines[end - 1].trim() || lines[end - 1].trimStart().startsWith('#'))) end--;
    lines.splice(start, end - start); return lines.join('');
  }
  for (const [key, value] of Object.entries(changes)) {
    let index = -1;
    for (const item of statements()) {
      if (item.header || item.start <= start || item.start >= end) continue;
      const match = /^\s*([A-Za-z0-9_-]+)\s*=/.exec(lines[item.start]);
      if (match?.[1] === key) {
        if (index >= 0 || item.end !== item.start + 1) throw new ConfigError('設定鍵不明確');
        index = item.start;
      }
    }
    if (index >= 0) {
      if (value === null) { lines.splice(index, 1); end--; }
      else {
        const original = lines[index];
        const assignment = /^(\s*[A-Za-z0-9_-]+\s*=)\s*/.exec(original)!;
        const rest = original.slice(assignment[0].length).replace(/\r?\n$/, '');
        let comment = '';
        for (let i = 0; i < rest.length; i++) if (rest[i] === '#') {
          try { parsed(`x = ${rest.slice(0, i)}`); comment = rest.slice(i).replace(/^/, ' '); break; } catch { /* inside value */ }
        }
        lines[index] = `${assignment[1]} ${typeof value === 'number' ? value : JSON.stringify(value)}${comment}\n`;
      }
    } else if (value !== null) { lines.splice(end, 0, `${key} = ${typeof value === 'number' ? value : JSON.stringify(value)}\n`); end++; }
  }
  return lines.join('');
}
async function askQuestion(label: string): Promise<string> {
  const prompt = createInterface({input:process.stdin, output:process.stdout});
  try { return (await prompt.question(label)).trim(); } finally { prompt.close(); }
}
export async function readSecret(input: NodeJS.ReadStream, output: NodeJS.WriteStream): Promise<string> {
  if (!input.isTTY || !output.isTTY) throw new ConfigError('輸入金鑰需要互動式終端');
  output.write('新的 API 金鑰：');
  return await new Promise((resolve, reject) => {
    let value = '';
    const previous = input.isRaw;
    const finish = (error?: Error) => {
      input.off('data', onData); input.setRawMode(previous); input.pause(); output.write('\n');
      if (error) reject(error); else if (!value.trim()) reject(new ConfigError('金鑰不能留白')); else resolve(value.trim());
    };
    const onData = (bytes: Buffer) => {
      for (const char of bytes.toString('utf8')) {
        if (char === '\u0003' || char === '\u0004') { finish(new ConfigError('已取消')); return; }
        if (char === '\r' || char === '\n') { finish(); return; }
        if (char === '\u007f' || char === '\b') value = value.slice(0, -1);
        else if (char >= ' ') value += char;
      }
    };
    input.setRawMode(true); input.resume(); input.on('data', onData);
  });
}
export async function editConfig(args: string[], env: NodeJS.ProcessEnv): Promise<void> {
  const path = await configPath(env);
  let source: string;
  try { source = new TextDecoder('utf-8', {fatal:true}).decode(await readFile(path)); }
  catch { throw new ConfigError('無法讀取參數檔'); }
  const raw = parsed(source);
  if (raw.url !== undefined || raw.api_key !== undefined) throw new ConfigError('請先使用 setup 轉換舊格式');
  const [command, ...options] = args;
  const yes = options.includes('--yes');
  const flags = options.filter(x => x !== '--yes');
  const expected = parsed(source);
  let result: string;
  if (command === 'remove-site') {
    if (flags.length !== 1 || !expected.sites?.[flags[0]]) throw new ConfigError('找不到站台');
    result = editLines(source, flags[0], {}, true); delete expected.sites[flags[0]];
    if (!Object.keys(expected.sites).length) delete expected.sites;
  } else if (command === 'set-site') {
    const name = flags[0];
    if (!name || !expected.sites?.[name] || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(name)) throw new ConfigError('找不到站台');
    const changes: Record<string, string | null> = {};
    if (flags.length === 1) {
      const url = await askQuestion('站台網址（Enter 保留）：');
      if (url) changes.url = url;
      const description = await askQuestion('站台說明（Enter 保留，輸入 - 清空）：');
      if (description) changes.description = description === '-' ? null : description;
      if (/^y(?:es)?$/i.test(await askQuestion('要換 API 金鑰嗎？ [y/N] '))) changes.api_key = await readSecret(process.stdin, process.stdout);
      if (!Object.keys(changes).length) { console.log('沒有任何變更，參數檔未修改。'); return; }
    }
    for (let i = 1; i < flags.length; i++) {
      const flag = flags[i];
      if (flag === '--key' && changes.api_key === undefined) changes.api_key = await readSecret(process.stdin, process.stdout);
      else if (flag === '--clear-description' && changes.description === undefined) changes.description = null;
      else if ((flag === '--description' || flag === '--url') && flags[i + 1] && changes[flag.slice(2)] === undefined) changes[flag.slice(2)] = flags[++i];
      else throw new ConfigError('無效的站台選項');
    }
    if (!Object.keys(changes).length) throw new ConfigError('請指定要修改的欄位');
    if (changes.url && changes.url.replace(/\/+$/, '') !== String(expected.sites[name].url).replace(/\/+$/, '') && expected.sites[name].allow_insecure_http === true) throw new ConfigError('變更網址時不可沿用 HTTP 例外');
    result = editLines(source, name, changes);
    for (const [key, value] of Object.entries(changes)) {
      if (value === null) delete expected.sites[name][key]; else expected.sites[name][key] = value;
    }
    if (changes.url || changes.api_key) {
      const site = (await loadSettings({}, expected)).sites.get(name)!;
      const client = createTransport(site);
      try {
        const response = await client.json('GET', '/users/current.json');
        const user = (response.data as {user?: {login?: unknown}})?.user;
        if (!user || typeof user.login !== 'string' || !user.login) throw new Error();
      } catch { throw new ConfigError('站台金鑰驗證失敗，參數檔未修改'); }
      finally { client.close(); }
    }
  } else if (command === 'set-global') {
    let change: Record<string, number | null> = {};
    if (!flags.length) {
      const input = await askQuestion('max_attachment_mb（Enter 保留，輸入 - 清空）：');
      if (input === '-') change = {max_attachment_mb:null};
      else if (input) {
        if (!/^[1-9]\d*$/.test(input) || !Number.isSafeInteger(Number(input))) throw new ConfigError('max_attachment_mb 必須是大於 0 的整數');
        change = {max_attachment_mb:Number(input)};
      } else { console.log('沒有任何變更，參數檔未修改。'); return; }
    } else {
      let specified = false, clear = false, value = 0;
      for (let i = 0; i < flags.length; i++) {
        if (flags[i] === '--clear' && flags[i + 1] === 'max_attachment_mb') { clear = true; i++; }
        else if (flags[i] === '--max-attachment-mb' && !specified && /^[1-9]\d*$/.test(flags[i + 1] ?? '') && Number.isSafeInteger(Number(flags[i + 1]))) { specified = true; value = Number(flags[++i]); }
        else throw new ConfigError('無效的全域設定選項');
      }
      if (specified && clear) throw new ConfigError('不可同時設定與清空');
      change = {max_attachment_mb:clear ? null : value};
    }
    result = editLines(source, null, change);
    if (change.max_attachment_mb === null) delete expected.max_attachment_mb; else expected.max_attachment_mb = BigInt(change.max_attachment_mb);
  } else throw new ConfigError('不支援的設定命令');
  const actual = parsed(result);
  if (!same(actual, expected)) throw new ConfigError('設定編輯結果不符，未修改');
  if (actual.sites && Object.keys(actual.sites).length) await loadSettings({}, actual);
  else if (command !== 'remove-site') throw new ConfigError('參數檔沒有站台');
  if (!yes) {
    const prompt = createInterface({input: process.stdin, output: process.stdout});
    let answer: string;
    try { answer = await prompt.question('確認寫入嗎？ [y/N] '); } finally { prompt.close(); }
    if (!/^y(?:es)?$/i.test(answer.trim())) throw new ConfigError('已取消，參數檔未修改');
  }
  writePrivate(path, result);
  console.log('已寫入參數檔，請重新連線。');
}







export function convertLegacy(text: string): string {
  const raw = parsed(text);
  if (raw.sites !== undefined || (raw.url === undefined && raw.api_key === undefined)) throw new ConfigError('不是可轉換的舊格式');
  const keys = new Set(['url','api_key','description','allow_insecure_http','attachment_upload_root']);
  const lines = text.match(/[^\n]*\n|[^\n]+$/g) ?? [];
  const keep: string[] = [], moved: string[] = [];
  let root = true;
  for (let i = 0; i < lines.length;) {
    if (!lines[i].trim() || lines[i].trimStart().startsWith('#')) { keep.push(lines[i++]); continue; }
    let body = '', complete = false;
    while (i < lines.length) { body += lines[i++]; try { parsed(body); complete = true; break; } catch { /* wait for complete statement */ } }
    if (!complete) throw new ConfigError('無法安全辨識 TOML 敘述');
    if (body.trimStart().startsWith('[')) root = false;
    const statement = parsed(body);
    if (root && Object.keys(statement).some(key => keys.has(key))) {
      if (Object.keys(statement).length !== 1) throw new ConfigError('舊設定欄位不明確');
      moved.push(body.endsWith('\n') ? body : body + '\n');
    } else keep.push(body);
  }
  const prefix = keep.join('');
  const result = `${prefix}${prefix && !prefix.endsWith('\n') ? '\n' : ''}${prefix ? '\n' : ''}[sites.default]\n${moved.join('')}`;
  const expected = parsed(text), site: Record<string, unknown> = {};
  for (const key of keys) if (Object.hasOwn(expected, key)) { site[key] = expected[key]; delete expected[key]; }
  expected.sites = {default:site};
  if (!same(parsed(result), expected)) throw new ConfigError('舊設定轉換結果不符，未修改');
  return result;
}
export async function setupConfig(env: NodeJS.ProcessEnv): Promise<void> {
  if (!process.stdin.isTTY || !process.stdout.isTTY) throw new ConfigError('設定精靈需要互動式終端');
  const path = await configPath(env);
  let source = '', exists = true;
  try { source = new TextDecoder('utf-8',{fatal:true}).decode(await readFile(path)); }
  catch (error) { if ((error as NodeJS.ErrnoException).code === 'ENOENT') exists = false; else throw new ConfigError('無法讀取參數檔'); }
  let raw = parsed(source);
  const ask = async (label: string) => {
    const prompt = createInterface({input:process.stdin, output:process.stdout});
    try { return (await prompt.question(label)).trim(); } finally { prompt.close(); }
  };
  if (raw.url !== undefined || raw.api_key !== undefined) {
    if (!/^y(?:es)?$/i.test(await ask('偵測到舊格式，轉換成 sites.default？ [y/N] '))) throw new ConfigError('已取消，參數檔未修改');
    source = convertLegacy(source); raw = parsed(source);
  }
  const name = (await ask('站台代號（預設 main）：')) || 'main';
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(name)) throw new ConfigError('站台代號無效');
  const old = raw.sites?.[name];
  const url = (await ask('Redmine HTTPS 網址：')) || old?.url;
  if (typeof url !== 'string' || !url) throw new ConfigError('網址不能留白');
  let key: string;
  if (old?.api_key && /^y(?:es)?$/i.test(await ask('沿用現有金鑰？ [y/N] '))) key = old.api_key;
  else key = await readSecret(process.stdin, process.stdout);
  const expected = parsed(source);
  if (!expected.sites) expected.sites = {};
  if (old) {
    expected.sites[name].url = url; expected.sites[name].api_key = key;
  } else expected.sites[name] = {url, api_key:key};
  if (old?.allow_insecure_http === true && url.replace(/\/+$/, '') !== String(old.url).replace(/\/+$/, '')) throw new ConfigError('變更網址時不可沿用 HTTP 例外');
  const site = (await loadSettings({}, expected)).sites.get(name)!;
  const client = createTransport(site);
  try {
    const response = await client.json('GET','/users/current.json');
    const user = (response.data as {user?:{login?:unknown}})?.user;
    if (!user || typeof user.login !== 'string' || !user.login) throw new Error();
  } catch { throw new ConfigError('站台金鑰驗證失敗，參數檔未修改'); } finally { client.close(); }
  const result = old ? editLines(source, name, {url, api_key:key}) : `${source}${source && !source.endsWith('\n') ? '\n' : ''}${source ? '\n' : ''}[sites.${name}]\nurl = ${JSON.stringify(url)}\napi_key = ${JSON.stringify(key)}\n`;
  if (!same(parsed(result), expected)) throw new ConfigError('設定編輯結果不符，未修改');
  if (!/^y(?:es)?$/i.test(await ask('驗證成功，確認寫入嗎？ [y/N] '))) throw new ConfigError('已取消，參數檔未修改');
  writePrivate(path, result, exists);
  console.log('已寫入參數檔；MCP 連線由 plugin 管理。');
}
