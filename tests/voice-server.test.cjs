'use strict';
const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const http = require('node:http');
const { createVoiceServer, localIPv4 } = require('../scripts/voice-server.cjs');

let root, server, port;
const logs = [];
const sample = Buffer.from('0123456789');
before(async () => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), 'ricestep-voice-test-'));
  fs.writeFileSync(path.join(root, 'feed.mp3'), sample);
  fs.writeFileSync(path.join(root, 'private.txt'), 'not part of the voice pack');
  fs.writeFileSync(path.join(root, 'manifest.json'), JSON.stringify({ version: 1, clips: [{
    id: 'feed', file: 'feed.mp3', bytes: sample.length,
    sha256: crypto.createHash('sha256').update(sample).digest('hex'),
  }] }));
  server = createVoiceServer({ assetRoot: root, logger: line => logs.push(line) });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  port = server.address().port;
});
after(async () => {
  if (server) await new Promise(resolve => server.close(resolve));
  if (root) fs.rmSync(root, { recursive: true, force: true });
});
function request(url, method = 'GET', headers = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port, path: url, method, headers }, res => {
      const chunks = [];
      res.on('data', chunk => chunks.push(chunk));
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks) }));
    });
    req.on('error', reject); req.end();
  });
}
test('only explicitly private/loopback IPv4 can bind', () => {
  for (const host of ['127.0.0.1', '10.1.2.3', '172.16.0.3', '172.31.255.254', '192.168.1.100']) assert.equal(localIPv4(host), true);
  for (const host of ['0.0.0.0', '8.8.8.8', '172.32.0.1', 'localhost', '::', '192.168.0.999']) assert.equal(localIPv4(host), false);
});
test('GET serves verified clip and HEAD has length but no body', async () => {
  const get = await request('/voice/v1/feed.mp3');
  assert.equal(get.status, 200); assert.deepEqual(get.body, sample);
  assert.equal(get.headers['content-type'], 'audio/mpeg');
  const head = await request('/voice/v1/feed.mp3', 'HEAD');
  assert.equal(head.status, 200); assert.equal(head.headers['content-length'], '10'); assert.equal(head.body.length, 0);
});
test('bounded and suffix byte ranges work, invalid or multi ranges fail', async () => {
  const range = await request('/voice/v1/feed.mp3', 'GET', { Range: 'bytes=2-5' });
  assert.equal(range.status, 206); assert.equal(range.body.toString(), '2345');
  assert.equal(range.headers['content-range'], 'bytes 2-5/10');
  const suffix = await request('/voice/v1/feed.mp3', 'GET', { Range: 'bytes=-3' });
  assert.equal(suffix.body.toString(), '789');
  for (const value of ['bytes=10-', 'bytes=5-2', 'bytes=-0', 'bytes=0-1,4-5', 'invalid']) {
    assert.equal((await request('/voice/v1/feed.mp3', 'GET', { Range: value })).status, 416);
  }
});
test('workspace paths, traversal, missing clips and query strings are never exposed', async () => {
  for (const url of ['/manifest.json', '/private.txt', '/../private.txt', '/voice/v1/%2e%2e/private.txt', '/voice/v1/missing.mp3', '/voice/v1/feed.mp3?secret=abc']) {
    assert.equal((await request(url)).status, 404);
  }
  assert.equal((await request('/voice/v1/feed.mp3', 'POST')).status, 405);
  assert.equal(logs.some(line => line.includes('secret')), false);
});
test('ETag permits conditional reads and modified assets fail startup validation', async () => {
  const initial = await request('/voice/v1/feed.mp3');
  assert.equal((await request('/voice/v1/feed.mp3', 'GET', { 'If-None-Match': initial.headers.etag })).status, 304);
  fs.writeFileSync(path.join(root, 'feed.mp3'), 'tampered!!');
  assert.throws(() => createVoiceServer({ assetRoot: root }), /hash mismatch/);
});
