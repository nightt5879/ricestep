'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { once } = require('node:events');
const { createServer, validDay, parseArgs, loadOrCreateToken } = require('../scripts/step-relay.cjs');

// Test-only constant. Production credentials are random and are never loaded here.
const TOKEN = 'a'.repeat(64);
const CLOCK = '2026-10-27T10:00:00.000Z';

function removeTestDirectory(directory) {
  const root = fs.realpathSync(os.tmpdir());
  const target = fs.realpathSync(directory);
  if (path.dirname(target) !== root || !path.basename(target).startsWith('ricestep-relay-')) {
    throw new Error('Refusing cleanup outside the test temporary directory.');
  }
  fs.rmSync(target, { recursive: true });
}

async function fixture(t, stateFile, now = () => new Date(CLOCK)) {
  const directory = stateFile ? null : fs.mkdtempSync(path.join(os.tmpdir(), 'ricestep-relay-test-'));
  const file = stateFile || path.join(directory, 'state.json');
  const server = createServer({ token: TOKEN, stateFile: file, now });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(async () => {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
    if (directory) removeTestDirectory(directory);
  });
  const port = server.address().port;
  function request({ method = 'GET', endpoint = `/api/steps?token=${TOKEN}`, payload, raw, headers = {} } = {}) {
    const body = raw !== undefined ? raw : payload === undefined ? undefined : JSON.stringify(payload);
    return new Promise((resolve, reject) => {
      const req = http.request({ hostname: '127.0.0.1', port, method, path: endpoint,
        headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...headers } }, (res) => {
        let text = ''; res.setEncoding('utf8'); res.on('data', (chunk) => { text += chunk; });
        res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, text, data: text ? JSON.parse(text) : null }));
      });
      req.on('error', reject); if (body !== undefined) req.write(body); req.end();
    });
  }
  return { file, request, server };
}

test('an authenticated fresh relay has no data, not a fabricated zero', async (t) => {
  const { request, file } = await fixture(t);
  const response = await request();
  assert.equal(response.status, 204);
  assert.equal(response.text, '');
  assert.equal(response.headers['cache-control'], 'no-store');
  assert.equal(fs.existsSync(file), false);
});

test('freshness checks require recent phone sync while preserving the last real count', async (t) => {
  let time = Date.parse(CLOCK);
  const { request } = await fixture(t, undefined, () => new Date(time));
  await request({method:'POST',payload:{total:500,day:'2026-10-27'}});
  const endpoint = `/api/steps?token=${TOKEN}&fresh=1`;
  assert.equal((await request({endpoint})).status, 200);
  time += 91000;
  assert.equal((await request({endpoint})).status, 204);
  assert.equal((await request()).data.total, 500);
  await request({method:'POST',payload:{total:1100,day:'2026-10-27'}});
  assert.equal((await request({endpoint})).data.total, 1100);
});

test('GET and POST both require the exact token; browser origins and extra routes are rejected', async (t) => {
  const { request, file } = await fixture(t);
  for (const method of ['GET', 'POST']) {
    for (const endpoint of ['/api/steps', '/api/steps?token=wrong', `/api/steps?token=${TOKEN}&token=${TOKEN}`]) {
      assert.equal((await request({ method, endpoint, payload: { total: 100, day: '2026-10-27' } })).status, 401);
    }
  }
  const origin = await request({ headers: { Origin: 'https://example.com' } });
  assert.equal(origin.status, 403);
  assert.equal(origin.headers['access-control-allow-origin'], undefined);
  assert.equal((await request({ endpoint: '/' })).status, 404);
  assert.equal((await request({ method: 'DELETE' })).status, 405);
  assert.equal(fs.existsSync(file), false);
});

test('valid data persists, same-day retries are idempotent, and GET exposes only the schema', async (t) => {
  const { request, file } = await fixture(t);
  const expected = { total: 1234, day: '2026-10-27', source: 'apple_health', updated_at: CLOCK };
  for (let i = 0; i < 2; i++) {
    const response = await request({ method: 'POST', payload: { total: 1234, day: '2026-10-27' } });
    assert.equal(response.status, 200); assert.deepEqual(response.data, expected);
  }
  assert.deepEqual((await request()).data, expected);
  assert.deepEqual(JSON.parse(fs.readFileSync(file)), expected);
  assert.equal(fs.readFileSync(file, 'utf8').includes(TOKEN), false);
  const restored = await fixture(t, file);
  assert.deepEqual((await restored.request()).data, expected);
});

test('same-day totals cannot decrease, next day can reset, and previous day cannot overwrite', async (t) => {
  const { request } = await fixture(t);
  const post = (total, day) => request({ method: 'POST', payload: { total, day } });
  assert.equal((await post(1000, '2026-10-27')).status, 200);
  assert.equal((await post(999, '2026-10-27')).status, 409);
  assert.equal((await post(0, '2026-10-28')).status, 200);
  assert.equal((await post(200000, '2026-10-27')).status, 409);
  assert.equal((await request()).data.total, 0);
  assert.equal((await request()).data.day, '2026-10-28');
});

test('payload validation rejects invalid dates, totals, JSON, content types and extra fields', async (t) => {
  const { request, file } = await fixture(t);
  const invalid = [
    { total: -1, day: '2026-10-27' }, { total: 200001, day: '2026-10-27' },
    { total: 1.5, day: '2026-10-27' }, { total: '20', day: '2026-10-27' },
    { total: null, day: '2026-10-27' }, { total: 2, day: '2026-02-29' },
    { total: 2, day: '2026-10-32' }, { total: 2, day: '2026-1-2' },
    { total: 2, day: '2026-10-27T00:00:00Z' }, { total: 2, day: '2026-10-27', source: 'sensor' },
    [], null, {},
  ];
  for (const payload of invalid) assert.equal((await request({ method: 'POST', payload })).status, 422);
  assert.equal((await request({ method: 'POST', raw: '{broken' })).status, 400);
  assert.equal((await request({ method: 'POST', raw: '{}', headers: { 'Content-Type': 'text/plain' } })).status, 415);
  assert.equal(fs.existsSync(file), false);
  assert.equal(validDay('2024-02-29'), true);
  assert.equal(validDay('2026-02-29'), false);
});

test('chunked and declared oversized requests are rejected without changing state', async (t) => {
  const { request, file } = await fixture(t);
  const raw = JSON.stringify({ total: 4, day: '2026-10-27', padding: 'x'.repeat(3000) });
  assert.equal((await request({ method: 'POST', raw })).status, 413);
  assert.equal((await request({ method: 'POST', raw, headers: { 'Content-Length': Buffer.byteLength(raw) } })).status, 413);
  assert.equal(fs.existsSync(file), false);
});

test('concurrent same-day posts cannot overwrite a larger accepted total', async (t) => {
  const { request } = await fixture(t);
  await Promise.all([10, 300, 25, 2000, 100].map((total) => request({ method: 'POST', payload: { total, day: '2026-10-27' } })));
  assert.equal((await request()).data.total, 2000);
});

test('CLI keeps default localhost, refuses public/wildcard listeners, and preserves random credentials', (t) => {
  assert.equal(parseArgs([]).host, '127.0.0.1');
  assert.equal(parseArgs(['--host', '10.10.0.50']).port, 8787);
  assert.throws(() => parseArgs(['--host', '0.0.0.0']));
  assert.throws(() => parseArgs(['--host', '8.8.8.8']));
  assert.throws(() => parseArgs(['--port', '0']));
  assert.throws(() => parseArgs(['--host']));
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ricestep-relay-token-test-'));
  t.after(() => removeTestDirectory(directory));
  const file = path.join(directory, 'config.json');
  const token = loadOrCreateToken(file);
  assert.match(token, /^[a-f0-9]{64}$/);
  assert.equal(loadOrCreateToken(file), token);
});

test('corrupt persisted state fails closed rather than creating a fake fresh total', (t) => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ricestep-relay-corrupt-test-'));
  t.after(() => removeTestDirectory(directory));
  const file = path.join(directory, 'state.json');
  fs.writeFileSync(file, JSON.stringify({ total: -3, day: '2026-10-27', source: 'apple_health', updated_at: CLOCK }));
  assert.throws(() => createServer({ token: TOKEN, stateFile: file }));
});
