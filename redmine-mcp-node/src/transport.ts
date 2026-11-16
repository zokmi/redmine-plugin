import { readFileSync } from 'node:fs';
import http, { type ClientRequest, type IncomingMessage } from 'node:http';
import https from 'node:https';
import { Transform, Writable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { TLSSocket } from 'node:tls';
import { createBrotliDecompress, createGunzip, createInflate } from 'node:zlib';
import { validateSiteUrl, type SiteSettings } from './config.js';

const { version }: { version: string } = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
const USER_AGENT = `redmine-mcp/${version}`;
const CONNECT_TIMEOUT_MS = 10_000;
const POOL_WAIT_TIMEOUT_MS = 30_000;
const API_TIMEOUT_MS = 60_000;
const DOWNLOAD_TIMEOUT_MS = 300_000;
const MAX_RESPONSE_BYTES = 16 * 1024 * 1024;

type QueryValue = string | number | boolean | null | undefined;
export interface JsonOptions {
  readonly query?: Readonly<Record<string, QueryValue | readonly QueryValue[]>> | URLSearchParams;
  readonly body?: unknown;
  readonly signal?: AbortSignal;
  readonly timeoutMs?: number;
}

export interface JsonResponse {
  readonly status: number;
  readonly data: unknown;
}

export interface RedmineTransport {
  json(method: string, path: string, options?: JsonOptions): Promise<JsonResponse>;
  upload(filename: string, bytes: Uint8Array, signal?: AbortSignal): Promise<JsonResponse>;
  download(url: string, target: Writable, maxBytes: number, signal?: AbortSignal): Promise<number>;
  close(): void;
}

export type TransportErrorCode = 'invalid_url' | 'invalid_request' | 'closed' | 'cancelled' | 'timeout' |
  'network' | 'tls' | 'truncated' | 'too_large' | 'invalid_encoding' | 'invalid_json' | 'target_write' |
  'redirect' | 'challenge' | 'auth' | 'not_found' | 'validation' | 'server';

export class TransportError extends Error {
  override name = 'TransportError';

  constructor(
    readonly code: TransportErrorCode,
    message: string,
    readonly status?: number,
    readonly details: readonly string[] = [],
  ) {
    super(message);
  }
}

function validateLength(response: IncomingMessage, limit: number): void {
  const length = response.headers['content-length'];
  if (length !== undefined && Number(length) > limit) throw tooLarge(limit);
}

function tooLarge(limit: number): TransportError {
  return new TransportError('too_large', `Redmine 傳輸內容超過 ${limit} 位元組上限`);
}

function checkStatus(status: number, response: IncomingMessage): void {
  if (status >= 200 && status < 300) return;
  if (status >= 300 && status < 400) {
    throw new TransportError('redirect', `Redmine 回應非預期的轉址（HTTP ${status}）`, status);
  }
  if (status === 401 || status === 403) {
    if (!response.headers['content-type']?.toLowerCase().includes('json')) {
      throw new TransportError('challenge',
        `Redmine 回應不是 JSON（HTTP ${status}），請檢查 WAF、反向代理與 [sites.<代號>] 的 url（單站亦可使用 REDMINE_URL）`, status);
    }
    throw new TransportError('auth',
      `API key 無效或權限不足（HTTP ${status}），請確認 [sites.<代號>] 的 api_key 與帳號權限`, status);
  }
  if (status === 404) throw new TransportError('not_found', '找不到指定的 Redmine 資源（HTTP 404）', status);
  if (status === 422) throw new TransportError('validation', 'Redmine 驗證失敗（HTTP 422）', status);
  throw new TransportError('server', `Redmine 伺服器錯誤（HTTP ${status}）`, status);
}

function decoders(encoding: string): Transform[] {
  return encoding.split(',').reverse().map((item) => {
    switch (item.trim()) {
      case 'identity': return null;
      case 'gzip':
      case 'x-gzip': return createGunzip();
      case 'br': return createBrotliDecompress();
      case 'deflate': return createInflate();
      default: throw new TransportError('invalid_encoding', 'Redmine 回應使用不支援的壓縮格式');
    }
  }).filter((decoder) => decoder !== null);
}

async function readJson(response: IncomingMessage, method: string, signal: AbortSignal): Promise<unknown> {
  if (method === 'HEAD' || response.statusCode === 204) {
    await pipeline(response, new Writable({ write(_chunk, _encoding, done) { done(); } }), { signal });
    return {};
  }
  const encoding = response.headers['content-encoding']?.toLowerCase() || 'identity';
  if (encoding === 'identity') validateLength(response, MAX_RESPONSE_BYTES);
  const chunks: Buffer[] = [];
  let total = 0;
  const collector = new Writable({
    write(chunk: Buffer, _encoding, done) {
      total += chunk.length;
      if (total > MAX_RESPONSE_BYTES) return done(tooLarge(MAX_RESPONSE_BYTES));
      chunks.push(chunk);
      done();
    },
  });
  await pipeline([response, ...decoders(encoding), collector], { signal });
  if (!total) return {};
  return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks, total)));
}

function validationError(data: unknown, key: string): TransportError {
  const errors = (data as { errors?: unknown } | null)?.errors;
  const encodedKey = JSON.stringify(key).slice(1, -1);
  const details = Array.isArray(errors) ? errors.map((error) => {
    const string = typeof error === 'string' ? error : JSON.stringify(error);
    return string.replaceAll(encodedKey, '[REDACTED]').replaceAll(key, '[REDACTED]')
      .replaceAll('<', '&lt;').replaceAll('>', '&gt;');
  }) : [];
  const message = details.length ? `；${details.join('；')}` : '';
  return new TransportError('validation', `Redmine 驗證失敗（HTTP 422）${message}`, 422, details);
}

function requestFailure(error: unknown, response?: IncomingMessage): TransportError {
  if (error instanceof TransportError) return error;
  const code = (error as NodeJS.ErrnoException | undefined)?.code;
  if (code === 'ETIMEDOUT') return new TransportError('timeout', '連線 Redmine 逾時');
  if (code === 'SELF_SIGNED_CERT_IN_CHAIN' || code === 'DEPTH_ZERO_SELF_SIGNED_CERT' ||
      code === 'UNABLE_TO_VERIFY_LEAF_SIGNATURE' || code?.startsWith('UNABLE_TO_GET_ISSUER_CERT') ||
      code?.startsWith('CERT_') || code?.startsWith('ERR_TLS_') || code?.startsWith('ERR_SSL_')) {
    return new TransportError('tls', '無法驗證 Redmine 的 TLS 連線，請確認站台憑證與 TLS 設定');
  }
  if (code === 'Z_BUF_ERROR' || (response && !response.complete && (code === 'ECONNRESET' || code === 'ERR_STREAM_PREMATURE_CLOSE'))) {
    return new TransportError('truncated', 'Redmine 回應未完整傳輸');
  }
  if (code?.startsWith('Z_') || code?.startsWith('ERR_PADDING_') || code?.startsWith('ERR_CL_SPACE')) {
    return new TransportError('invalid_encoding', 'Redmine 回應解壓縮失敗');
  }
  return new TransportError('network', '連線 Redmine 失敗，請確認站台網址與網路狀態');
}

export function createTransport(site: SiteSettings): RedmineTransport {
  const siteUrl = validateSiteUrl(site.url, site.allowInsecureHttp);
  const key = site.apiKey;
  try {
    if (!key.trim()) throw new Error();
    http.validateHeaderValue('X-Redmine-API-Key', key);
  } catch {
    throw new TransportError('invalid_request', 'Redmine 的 API key 未設定或含有無效的字元');
  }
  const secure = siteUrl.protocol === 'https:';
  const request = secure ? https.request : http.request;
  const basePath = `${siteUrl.pathname.replace(/\/+$/, '')}/`;
  const agents = { keepAlive: true, maxSockets: 4 };
  const newAgent = () => secure ? new https.Agent({ ...agents, rejectUnauthorized: true }) : new http.Agent(agents);
  const apiAgent = newAgent();
  const downloadAgent = newAgent();
  const shutdown = new AbortController();

  function urlFor(path: string, absolute: boolean): URL {
    try {
      if (/[\\\r\n\t]/.test(path)) throw new Error();
      if (absolute) {
        const url = validateSiteUrl(path, site.allowInsecureHttp);
        if (url.origin !== siteUrl.origin) throw new Error();
        return url;
      }
      if (path.startsWith('//') || /^[a-z][a-z\d+.-]*:/i.test(path.trimStart())) throw new Error();
      const url = new URL(`${siteUrl.origin}${basePath}${path.replace(/^\//, '')}`);
      if (url.origin !== siteUrl.origin || !url.pathname.startsWith(basePath)) throw new Error();
      return url;
    } catch {
      throw new TransportError('invalid_url', 'Redmine 請求網址必須使用該站台的來源，API 路徑須位於站台根路徑下');
    }
  }

  async function send<T>(
    method: string, url: URL, body: Uint8Array | undefined, contentType: string | undefined,
    signal: AbortSignal | undefined, timeoutMs: number, agent: http.Agent,
    consume: (response: IncomingMessage, signal: AbortSignal) => Promise<T>,
  ): Promise<T> {
    const verb = method.toUpperCase();
    if (!/^[!#$%&'*+.^_`|~0-9A-Z-]+$/.test(verb) || verb === 'CONNECT' ||
        !Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 2_147_483_647) {
      throw new TransportError('invalid_request', 'Redmine 請求的方法或逾時設定無效');
    }
    if (shutdown.signal.aborted) throw new TransportError('closed', 'Redmine 連線已關閉');
    if (signal?.aborted) throw new TransportError('cancelled', 'Redmine 請求已取消');

    const timeout = new AbortController();
    const stop = AbortSignal.any([timeout.signal, shutdown.signal, ...(signal ? [signal] : [])]);
    const expire = () => timeout.abort();
    const totalTimer = setTimeout(expire, timeoutMs);
    const poolTimer = setTimeout(expire, POOL_WAIT_TIMEOUT_MS);
    let connectTimer: NodeJS.Timeout | undefined;
    let pending: ClientRequest | undefined;
    let response: IncomingMessage | undefined;
    let removeConnectListener: (() => void) | undefined;
    let removeAbortListener: (() => void) | undefined;
    try {
      response = await new Promise<IncomingMessage>((resolve, reject) => {
        const headers: http.OutgoingHttpHeaders = {
          'X-Redmine-API-Key': key,
          'User-Agent': USER_AGENT,
          Accept: 'application/json',
          'Accept-Encoding': agent === downloadAgent ? 'identity' : 'gzip, deflate, br',
        };
        if (body !== undefined) {
          headers['Content-Length'] = body.byteLength;
          headers['Content-Type'] = contentType;
        }
        const req = request(url, { method: verb, headers, agent, signal: stop }, resolve);
        pending = req;
        req.once('error', reject);
        // A queued ClientRequest may only emit its abort error when the agent revisits it.
        const aborted = () => reject(stop.reason);
        stop.addEventListener('abort', aborted, { once: true });
        removeAbortListener = () => stop.removeEventListener('abort', aborted);
        req.once('upgrade', (_response, socket) => {
          socket.destroy();
          reject(new TransportError('server', 'Redmine 回應非預期的協定升級（HTTP 101）', 101));
        });
        req.once('socket', (socket) => {
          clearTimeout(poolTimer);
          // A socket handed directly to a queued request may not set reusedSocket.
          const connecting = socket instanceof TLSSocket ? !socket.authorized : socket.connecting;
          if (stop.aborted || !connecting) return;
          connectTimer = setTimeout(expire, CONNECT_TIMEOUT_MS);
          const event = secure ? 'secureConnect' : 'connect';
          const connected = () => clearTimeout(connectTimer);
          socket.once(event, connected);
          removeConnectListener = () => socket.removeListener(event, connected);
        });
        // Each invocation creates one request. A failed write must never be replayed.
        req.end(body);
      });
      clearTimeout(connectTimer);
      return await consume(response, stop);
    } catch (error) {
      if (shutdown.signal.aborted) throw new TransportError('closed', 'Redmine 連線已關閉');
      if (signal?.aborted) throw new TransportError('cancelled', 'Redmine 請求已取消');
      if (timeout.signal.aborted) throw new TransportError('timeout', '連線 Redmine 逾時');
      throw requestFailure(error, response);
    } finally {
      clearTimeout(poolTimer);
      clearTimeout(connectTimer);
      clearTimeout(totalTimer);
      removeConnectListener?.();
      removeAbortListener?.();
      if (response) {
        if (!response.readableEnded) response.destroy();
      } else {
        pending?.destroy();
      }
    }
  }

  async function sendJson(
    method: string, url: URL, body: Uint8Array | undefined, type: string | undefined, options: JsonOptions,
  ): Promise<JsonResponse> {
    return send(method, url, body, type, options.signal, options.timeoutMs ?? API_TIMEOUT_MS, apiAgent,
      async (response, signal) => {
        const status = response.statusCode!;
        if (status !== 422) checkStatus(status, response);
        let data: unknown;
        try {
          data = await readJson(response, method.toUpperCase(), signal);
        } catch (error) {
          if (!(error instanceof SyntaxError || error instanceof TypeError)) throw error;
          if (status === 422) throw validationError(null, key);
          throw new TransportError('invalid_json', 'Redmine 回應不是合法的 JSON', status);
        }
        if (status === 422) throw validationError(data, key);
        return { status, data };
      });
  }

  return {
    async json(method, path, options = {}) {
      const url = urlFor(path, false);
      if (options.query instanceof URLSearchParams) {
        for (const [name, value] of options.query) url.searchParams.append(name, value);
      } else if (options.query) {
        for (const [name, value] of Object.entries(options.query)) {
          for (const item of Array.isArray(value) ? value : [value]) {
            if (item != null) url.searchParams.append(name, String(item));
          }
        }
      }
      let body: Buffer | undefined;
      if (options.body !== undefined) {
        try {
          body = Buffer.from(JSON.stringify(options.body));
        } catch {
          throw new TransportError('invalid_request', 'Redmine 請求內容無法轉換為 JSON');
        }
      }
      return sendJson(method, url, body, 'application/json', options);
    },

    async upload(filename, bytes, signal) {
      if (bytes.byteLength > site.maxAttachmentBytes) throw tooLarge(site.maxAttachmentBytes);
      const url = urlFor('uploads.json', false);
      url.searchParams.set('filename', filename);
      return sendJson('POST', url, bytes, 'application/octet-stream', { signal });
    },

    async download(rawUrl, target, maxBytes, signal) {
      if (!Number.isSafeInteger(maxBytes) || maxBytes < 0) {
        throw new TransportError('invalid_request', 'Redmine 附件大小上限必須是非負整數');
      }
      return send('GET', urlFor(rawUrl, true), undefined, undefined, signal, DOWNLOAD_TIMEOUT_MS, downloadAgent,
        async (response, stop) => {
          checkStatus(response.statusCode!, response);
          validateLength(response, maxBytes);
          let written = 0;
          let writePending = false;
          let pipelineSettled = false;
          const writeError = new TransportError('target_write', '無法寫入 Redmine 附件的下載目的地');
          // Pipeline owns the adapter, while the caller owns the destination's lifecycle.
          const writer = new Writable({
            write(chunk: Buffer, _encoding, done) {
              if (written + chunk.length > maxBytes) return done(tooLarge(maxBytes));
              writePending = true;
              try {
                target.write(chunk, (error) => {
                  // Writable emits its error after invoking the write callback.
                  if (error) return done(writeError);
                  writePending = false;
                  written += chunk.length;
                  done();
                  releaseTargetListeners();
                });
              } catch {
                writePending = false;
                done(writeError);
                releaseTargetListeners();
              }
            },
          });
          function releaseTargetListeners() {
            if (!pipelineSettled || writePending) return;
            target.removeListener('error', targetFailed);
            target.removeListener('close', targetFailed);
          }
          function targetFailed() {
            writePending = false;
            writer.destroy(writeError);
            releaseTargetListeners();
          }
          target.on('error', targetFailed);
          target.on('close', targetFailed);
          try {
            if (!target.writable) throw writeError;
            await pipeline(response, writer, { signal: stop });
          } finally {
            pipelineSettled = true;
            releaseTargetListeners();
          }
          return written;
        });
    },

    close() {
      shutdown.abort();
      apiAgent.destroy();
      downloadAgent.destroy();
    },
  };
}
