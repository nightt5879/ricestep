'use strict';

// Serve only the checked-in character voice pack, never the workspace itself.
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const net = require('node:net');

function localIPv4(host) {
  if (net.isIP(host) !== 4) return false;
  const [a, b] = host.split('.').map(Number);
  return a === 127 || a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168);
}

function loadClips(assetRoot) {
  const root = fs.realpathSync(assetRoot);
  const manifest = JSON.parse(fs.readFileSync(path.join(root, 'manifest.json'), 'utf8'));
  if (manifest.version !== 1 || !Array.isArray(manifest.clips) || manifest.clips.length > 64) {
    throw new Error('Invalid voice manifest.');
  }
  const clips = new Map();
  for (const clip of manifest.clips) {
    if (!/^[a-z][a-z0-9_]*$/.test(clip.id) || clip.file !== `${clip.id}.mp3` || clips.has(clip.id)) {
      throw new Error('Invalid or duplicate voice clip.');
    }
    const file = fs.realpathSync(path.join(root, clip.file));
    if (path.dirname(file) !== root) throw new Error('Voice clip must be inside the voice pack.');
    const size = fs.statSync(file).size;
    if (size < 1 || size > 1024 * 1024 || size !== clip.bytes) throw new Error(`Invalid size: ${clip.id}`);
    const data = fs.readFileSync(file);
    const hash = crypto.createHash('sha256').update(data).digest('hex');
    if (hash !== clip.sha256) throw new Error(`Audio hash mismatch: ${clip.id}`);
    clips.set(clip.id, { data, etag: `"${hash}"` });
  }
  return clips;
}

function byteRange(value, length) {
  const match = /^bytes=(\d*)-(\d*)$/.exec(value);
  if (!match || (!match[1] && !match[2])) return null;
  let start, end;
  if (!match[1]) {
    const suffix = Number(match[2]);
    if (!Number.isSafeInteger(suffix) || suffix <= 0) return null;
    start = Math.max(0, length - suffix);
    end = length - 1;
  } else {
    start = Number(match[1]);
    end = match[2] ? Number(match[2]) : length - 1;
    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start >= length || end < start) return null;
    end = Math.min(end, length - 1);
  }
  return { start, end };
}

function createVoiceServer({ assetRoot = path.join(__dirname, '../assets/audio/v1'), logger = console.log } = {}) {
  const clips = loadClips(assetRoot);
  return http.createServer((req, res) => {
    // Do not log arbitrary URLs, headers, addresses, or query parameters.
    const match = /^\/voice\/v1\/([a-z][a-z0-9_]*)\.mp3$/.exec(req.url || '');
    const id = match && clips.has(match[1]) ? match[1] : '-';
    res.on('finish', () => logger(`${id} ${res.statusCode}`));
    const finish = (status, headers = {}) => {
      res.writeHead(status, { 'Content-Length': 0, 'X-Content-Type-Options': 'nosniff', ...headers });
      res.end();
    };
    if (req.method !== 'GET' && req.method !== 'HEAD') return finish(405, { Allow: 'GET, HEAD' });
    if (id === '-') return finish(404);
    const { data, etag } = clips.get(id);
    const headers = {
      'Content-Type': 'audio/mpeg', 'Accept-Ranges': 'bytes',
      'Cache-Control': 'private, max-age=3600', 'X-Content-Type-Options': 'nosniff', ETag: etag,
    };
    if (req.headers['if-none-match'] === etag) return finish(304, headers);
    let start = 0, end = data.length - 1, status = 200;
    if (req.headers.range) {
      const range = byteRange(req.headers.range, data.length);
      if (!range) return finish(416, { ...headers, 'Content-Range': `bytes */${data.length}` });
      ({ start, end } = range);
      status = 206;
      headers['Content-Range'] = `bytes ${start}-${end}/${data.length}`;
    }
    headers['Content-Length'] = end - start + 1;
    res.writeHead(status, headers);
    res.end(req.method === 'HEAD' ? undefined : data.subarray(start, end + 1));
  });
}

if (require.main === module) {
  let host = '127.0.0.1', port = 8770;
  const args = process.argv.slice(2);
  for (let i = 0; i < args.length; i += 2) {
    if (args[i] === '--host' && args[i + 1]) host = args[i + 1];
    else if (args[i] === '--port' && args[i + 1]) port = Number(args[i + 1]);
    else throw new Error('Usage: node scripts/voice-server.cjs [--host PRIVATE_IPV4] [--port PORT]');
  }
  if (!localIPv4(host)) throw new Error('Bind to an explicit loopback or private IPv4 address.');
  if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('Port must be 1024..65535.');
  const server = createVoiceServer();
  server.on('error', error => { console.error(`Voice server error: ${error.code || 'UNKNOWN'}`); process.exitCode = 1; });
  server.listen(port, host, () => console.log(`Voice pack ready on ${host}:${port}`));
}

module.exports = { createVoiceServer, localIPv4, byteRange };
