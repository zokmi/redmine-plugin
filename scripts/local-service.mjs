import { spawn } from 'node:child_process';
import { randomBytes, timingSafeEqual } from 'node:crypto';
import { createServer, request } from 'node:http';
import { createInterface } from 'node:readline';

const host = '127.0.0.1';
const [python, portArgument = '18765'] = process.argv.slice(2);
const port = Number(portArgument);
const localToken = process.env.REDMINE_MCP_LOCAL_TOKEN ?? '';
if (!python || !Number.isInteger(port) || port < 1 || port > 65535) {
  throw new Error('Usage: node local-service.mjs <python executable> [port]');
}
if (!/^[A-Za-z0-9_-]{32,}$/.test(localToken)) {
  throw new Error('REDMINE_MCP_LOCAL_TOKEN must contain at least 32 URL-safe characters.');
}

const expected = Buffer.from(`Bearer ${localToken}`);
const allowedHosts = new Set([`${host}:${port}`, `localhost:${port}`]);
const allowedOrigins = new Set([...allowedHosts].map(value => `http://${value}`));
const workerToken = randomBytes(32).toString('hex');
let worker;
let workerPort;
let startupTimeout;
let stopping = false;

function end(response, status, headers) {
  response.writeHead(status, headers);
  response.end();
}

function forwardedHeaders(headers) {
  const copy = { ...headers };
  const connectionHeaders = (copy.connection ?? '').toLowerCase().split(',');
  for (const name of [
    'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
    'te', 'trailer', 'transfer-encoding', 'upgrade', ...connectionHeaders,
  ]) {
    delete copy[name.trim()];
  }
  return copy;
}

const server = createServer((incoming, outgoing) => {
  if (incoming.url !== '/mcp') return end(outgoing, 404);
  const auth = incoming.headersDistinct.authorization ?? [];
  const supplied = Buffer.from(auth[0] ?? '');
  if (auth.length !== 1 || supplied.length !== expected.length ||
      !timingSafeEqual(supplied, expected)) {
    if (!workerPort) return end(outgoing, 503);
    return end(outgoing, 401, { 'www-authenticate': 'Bearer' });
  }

  const origins = incoming.headersDistinct.origin ?? [];
  if (incoming.headersDistinct.host?.length !== 1 || !allowedHosts.has(incoming.headers.host) ||
      origins.length > 1 || (origins.length === 1 && !allowedOrigins.has(origins[0]))) {
    return end(outgoing, 403);
  }
  if (!workerPort) return end(outgoing, 503);

  const headers = forwardedHeaders(incoming.headers);
  delete headers.host;
  delete headers.origin;
  headers.authorization = `Bearer ${workerToken}`;
  const upstream = request({
    host, port: workerPort, path: '/mcp', method: incoming.method, headers,
  }, response => {
    outgoing.writeHead(response.statusCode, forwardedHeaders(response.headers));
    response.on('error', error => outgoing.destroy(error));
    response.pipe(outgoing);
  });
  outgoing.on('close', () => upstream.destroy());
  upstream.on('error', () => {
    if (outgoing.headersSent) outgoing.destroy();
    else if (!outgoing.destroyed) end(outgoing, 502);
  });
  incoming.pipe(upstream);
});

function stop(message) {
  if (stopping) return;
  stopping = true;
  clearTimeout(startupTimeout);
  if (message) {
    console.error(message);
    process.exitCode = 1;
  }
  server.close();
  server.closeAllConnections();
  worker?.kill();
}

process.on('SIGINT', () => stop());
process.on('SIGTERM', () => stop());
server.on('error', error => stop(`無法監聽 ${host}:${port} (${error.code})`));
server.listen(port, host, () => {
  const env = { ...process.env, PYTHONUTF8: '1', REDMINE_MCP_WORKER_TOKEN: workerToken };
  delete env.REDMINE_MCP_LOCAL_TOKEN;
  worker = spawn(python, ['-u', '-m', 'redmine_mcp.local_worker'], {
    env, windowsHide: true, stdio: ['pipe', 'pipe', 'inherit'],
  });
  worker.on('error', error => stop(`Redmine worker 啟動失敗 (${error.code})`));
  worker.on('exit', (code, signal) => stop(`Redmine worker 已結束 (${code ?? signal})`));
  startupTimeout = setTimeout(() => stop('Redmine worker 啟動逾時'), 25_000);
  const lines = createInterface({ input: worker.stdout });
  lines.once('line', line => {
    const readyPort = Number(line);
    if (!/^\d+$/.test(line) || readyPort < 1 || readyPort > 65535) {
      stop('Redmine worker 回傳無效連接埠');
      return;
    }
    workerPort = readyPort;
    clearTimeout(startupTimeout);
    console.log(`Redmine MCP Node.js listening at http://${host}:${port}/mcp`);
  });
});
