'use strict';
// Portable look-and-sound gallery. Decode the same API 5 geometry as the board.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const root = path.resolve(__dirname, '..');
const lua = fs.readFileSync(path.join(root, 'app/character.lua'), 'utf8');
const palette = lua.match(/local character_palette=\{([^}]+)\}/)[1].split(',').map(Number);
const block = lua.split('local character_high=')[1].split('local character_low=')[0];
const frames = [...block.matchAll(/\{(\d+),(\d+),\[=\[([\s\S]*?)\]=\]\}/g)].map(match => {
  const bytes = [...match[3]].map(c => c.charCodeAt(0) - 33);
  if (bytes.length % 5) throw new Error('Invalid packed frame.');
  const rects = [];
  for (let i = 0; i < bytes.length; i += 5) rects.push(bytes.slice(i, i + 5));
  return { w: Number(match[1]), h: Number(match[2]), rects };
});
const build = JSON.parse(fs.readFileSync(path.join(root, 'assets/generated/character-build.json'), 'utf8'));
if (frames.length !== 6 || frames.some((f, i) => f.rects.length !== build.high[i].shapes)) throw new Error('Frame count mismatch.');
const audioRoot = path.join(root, 'assets/audio/v1');
const manifest = JSON.parse(fs.readFileSync(path.join(audioRoot, 'manifest.json'), 'utf8'));
const clips = {};
for (const clip of manifest.clips) {
  if (clip.file !== `${clip.id}.mp3` || !/^[a-z_]+$/.test(clip.id)) throw new Error('Invalid clip name.');
  const bytes = fs.readFileSync(path.join(audioRoot, clip.file));
  if (crypto.createHash('sha256').update(bytes).digest('hex') !== clip.sha256) throw new Error(`Audio hash mismatch: ${clip.id}`);
  clips[clip.id] = { text: clip.text, duration: clip.duration_ms, src: `data:audio/mpeg;base64,${bytes.toString('base64')}` };
}
const data = JSON.stringify({ palette, frames, clips }).replace(/</g, '\\u003c');
const template = fs.readFileSync(path.join(root, 'assets/preview-template.html'), 'utf8');
const html = template.replace('/*__RICE_PREVIEW_DATA__*/null', data);
const destination = path.join(root, 'docs/preview.html');
fs.writeFileSync(destination, html);
console.log(`Portable preview: docs/preview.html (${Buffer.byteLength(html)} bytes; 6 poses, ${Object.keys(clips).length} clips)`);
