'use strict';

// Local, opt-in bridge: iPhone Shortcuts -> this PC -> RiceStep.
// Importing this module neither creates credentials nor starts a listener.
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const net = require('node:net');

const MAX_BODY_BYTES = 2048;
const MAX_STEPS = 200000;
const TOKEN_PATTERN = /^[a-f0-9]{64}$/;

function validDay(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00.000Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

function validTotal(value) {
  return Number.isInteger(value) && value >= 0 && value <= MAX_STEPS;
}

function atomicJson(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temporary = `${file}.${crypto.randomUUID()}.tmp`;
  try {
    fs.writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
    fs.renameSync(temporary, file);
  } finally {
    if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
  }
}

function readState(file) {
  let text;
  try { text = fs.readFileSync(file, 'utf8'); }
  catch (error) { if (error.code === 'ENOENT') return null; throw error; }
  const value = JSON.parse(text);
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      Object.keys(value).sort().join(',') !== 'day,source,total,updated_at' ||
      !validTotal(value.total) || !validDay(value.day) || value.source !== 'apple_health' ||
      typeof value.updated_at !== 'string' || !Number.isFinite(Date.parse(value.updated_at))) {
    throw new Error('Invalid saved relay state; refusing to silently reset it.');
  }
  return value;
}

function tokenMatches(candidate, expected) {
  if (typeof candidate !== 'string' || !TOKEN_PATTERN.test(candidate)) return false;
  return crypto.timingSafeEqual(Buffer.from(candidate, 'hex'), Buffer.from(expected, 'hex'));
}

function reply(response, status, value) {
  response.writeHead(status, {
    'Cache-Control': 'no-store',
    'Content-Type': 'application/json; charset=utf-8',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
  });
  response.end(value === undefined ? undefined : JSON.stringify(value));
}

function createServer({ token, stateFile, now = () => new Date() } = {}) {
  if (!TOKEN_PATTERN.test(token || '')) throw new Error('A 32-byte hexadecimal token is required.');
  if (typeof stateFile !== 'string' || !stateFile) throw new Error('stateFile is required.');
  let state = readState(stateFile);

  const server = http.createServer((request, response) => {
    let url;
    try { url = new URL(request.url, 'http://relay.local'); }
    catch { request.resume(); reply(response, 400, { error: 'invalid_url' }); return; }
    if (url.pathname !== '/api/steps') {
      request.resume(); reply(response, 404, { error: 'not_found' }); return;
    }
    if (url.searchParams.getAll('token').length !== 1 ||
        !tokenMatches(url.searchParams.get('token'), token)) {
      request.resume(); reply(response, 401, { error: 'unauthorized' }); return;
    }
    // Shortcuts and the device's HTTP client send no Origin. No browser CORS is enabled.
    if (request.headers.origin) {
      request.resume(); reply(response, 403, { error: 'browser_origin_not_allowed' }); return;
    }
    if (request.method === 'GET') {
      const stale = state && url.searchParams.get('fresh') === '1' &&
        now().getTime() - Date.parse(state.updated_at) > 90000;
      request.resume(); reply(response, state && !stale ? 200 : 204, state && !stale ? state : undefined); return;
    }
    if (request.method !== 'POST') {
      request.resume(); response.setHeader('Allow', 'GET, POST');
      reply(response, 405, { error: 'method_not_allowed' }); return;
    }
    if ((request.headers['content-type'] || '').split(';')[0].trim().toLowerCase() !== 'application/json') {
      request.resume(); reply(response, 415, { error: 'application_json_required' }); return;
    }
    if (Number(request.headers['content-length']) > MAX_BODY_BYTES) {
      request.resume(); reply(response, 413, { error: 'body_too_large' }); return;
    }

    let chunks = [];
    let bytes = 0;
    let rejected = false;
    request.on('data', (chunk) => {
      if (rejected) return;
      bytes += chunk.length;
      if (bytes > MAX_BODY_BYTES) {
        rejected = true; chunks = [];
        reply(response, 413, { error: 'body_too_large' });
      } else chunks.push(chunk);
    });
    request.on('error', () => {
      if (!response.headersSent) reply(response, 400, { error: 'request_error' });
    });
    request.on('end', () => {
      if (rejected) return;
      let data;
      try { data = JSON.parse(Buffer.concat(chunks).toString('utf8')); }
      catch { reply(response, 400, { error: 'invalid_json' }); return; }
      if (!data || typeof data !== 'object' || Array.isArray(data) ||
          Object.keys(data).sort().join(',') !== 'day,total' ||
          !validTotal(data.total) || !validDay(data.day)) {
        reply(response, 422, { error: 'expected_total_and_day' }); return;
      }
      if (state && (data.day < state.day || (data.day === state.day && data.total < state.total))) {
        reply(response, 409, { error: 'older_day_or_decreasing_total' }); return;
      }
      // No awaits between validation and persistence: concurrent requests cannot lose an update.
      let next;
      try {
        next = { total: data.total, day: data.day, source: 'apple_health', updated_at: now().toISOString() };
        atomicJson(stateFile, next);
      } catch {
        reply(response, 500, { error: 'state_not_saved' }); return;
      }
      state = next;
      reply(response, 200, state);
    });
  });
  server.requestTimeout = 10000;
  server.headersTimeout = 6000;
  server.keepAliveTimeout = 1000;
  return server;
}

function privateHost(host) {
  if (host === '::1') return true;
  if (net.isIP(host) !== 4) return false;
  const [a, b] = host.split('.').map(Number);
  return a === 127 || a === 10 || (a === 192 && b === 168) || (a === 172 && b >= 16 && b <= 31);
}

function loadOrCreateToken(configFile) {
  try {
    const config = JSON.parse(fs.readFileSync(configFile, 'utf8'));
    if (!TOKEN_PATTERN.test(config.token || '')) throw new Error('Invalid saved relay token.');
    return config.token;
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  fs.mkdirSync(path.dirname(configFile), { recursive: true });
  const token = crypto.randomBytes(32).toString('hex');
  try {
    fs.writeFileSync(configFile, `${JSON.stringify({ token }, null, 2)}\n`, { flag: 'wx', mode: 0o600 });
    return token;
  } catch (error) {
    if (error.code === 'EEXIST') return loadOrCreateToken(configFile);
    throw error;
  }
}

function parseArgs(args) {
  const result = { host: '127.0.0.1', port: 8787, writeLocalConfig: false, quiet: false };
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--host') result.host = args[++i];
    else if (args[i] === '--port') result.port = Number(args[++i]);
    else if (args[i] === '--write-local-config') result.writeLocalConfig = true;
    else if (args[i] === '--quiet') result.quiet = true;
    else if (args[i] === '--help') result.help = true;
    else throw new Error('Unknown argument. Use --help.');
  }
  if (!privateHost(result.host)) throw new Error('--host must be a loopback or private LAN IP; wildcard/public listeners are not enabled.');
  if (!Number.isInteger(result.port) || result.port < 1 || result.port > 65535) throw new Error('Invalid --port.');
  return result;
}

function main(args = process.argv.slice(2)) {
  const options = parseArgs(args);
  if (options.help) {
    console.log('node scripts/step-relay.cjs [--host PRIVATE_LAN_IP] [--port 8787] [--write-local-config] [--quiet]');
    return;
  }
  const root = path.resolve(__dirname, '..');
  const token = loadOrCreateToken(path.join(root, '.cache', 'step-relay-config.json'));
  const authority = options.host.includes(':') ? `[${options.host}]` : options.host;
  const stepUrl = `http://${authority}:${options.port}/api/steps?token=${token}`;
  const server = createServer({ token, stateFile: path.join(root, '.cache', 'step-relay-state.json') });
  server.once('error', (error) => {
    // Never include request URLs or tokens in diagnostics.
    console.error(`Step relay could not listen (${error.code || 'error'}).`);
    process.exitCode = 1;
  });
  server.listen(options.port, options.host, () => {
    if (options.writeLocalConfig) {
      const file = path.join(root, '.local-config.json');
      try {
        let current = {};
        try { current = JSON.parse(fs.readFileSync(file, 'utf8')); }
        catch (error) { if (error.code !== 'ENOENT') throw error; }
        if (!current || typeof current !== 'object' || Array.isArray(current)) throw new Error('Invalid local config.');
        atomicJson(file, { ...current, step_url: stepUrl });
      } catch {
        console.error('Could not preserve/update .local-config.json; relay is stopping.');
        server.close(); process.exitCode = 1; return;
      }
    }
    console.log(`RiceStep relay ready at ${options.host}:${options.port}. No phone data is fabricated.`);
    if (!options.quiet) console.log(`Private phone POST / device GET URL (keep local):\n${stepUrl}`);
    if (options.writeLocalConfig) console.log('Local device step_url saved in ignored .local-config.json.');
  });
  for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => server.close());
  return server;
}

module.exports = { createServer, validDay, parseArgs, loadOrCreateToken };
if (require.main === module) {
  try { main(); }
  catch (error) { console.error(`Step relay: ${error.message}`); process.exitCode = 1; }
}
