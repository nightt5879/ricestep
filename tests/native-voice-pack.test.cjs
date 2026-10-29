'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');
const { APPROVED, loadPack, buildPack, generatePack } = require('../scripts/generate-native-voice.cjs');

const repo = path.resolve(__dirname, '..');
const original = path.join(repo, 'assets/audio/v1');
const cache = path.join(repo, '.cache/native-voice-tests');
fs.mkdirSync(cache, { recursive: true });
const sha256 = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
function fixture(t) {
  const root = fs.mkdtempSync(path.join(cache, 'voice pack '));
  t.after(() => {
    // Only this test's freshly created child directory can be removed.
    assert.equal(path.dirname(path.resolve(root)), cache);
    fs.rmSync(root, { recursive: true, force: true });
  });
  const assets = path.join(root, 'assets');
  fs.cpSync(original, assets, { recursive: true });
  return { root, assets, output: path.join(root, 'generated') };
}
function alterManifest(assets, change) {
  const file = path.join(assets, 'manifest.json');
  const manifest = JSON.parse(fs.readFileSync(file, 'utf8'));
  change(manifest);
  fs.writeFileSync(file, JSON.stringify(manifest));
}

test('exact pinned inventory is deterministic, including when manifest order changes', t => {
  const f = fixture(t);
  const dryRun = buildPack(f.assets);
  assert.equal(fs.existsSync(f.output), false);
  const first = generatePack({ assetRoot: f.assets, outputDir: f.output });
  assert.deepEqual(dryRun.inventory, first);
  assert.equal(dryRun.source, fs.readFileSync(path.join(f.output, 'voice_pack.c'), 'utf8'));
  assert.equal(first.clips.length, 14);
  assert.equal(first.total_bytes, 375408);
  assert.deepEqual(first.clips.map(clip => clip.key), Object.keys(APPROVED).sort());
  const before = ['voice_pack.c', 'voice_pack.h', 'voice_pack.inventory.json'].map(file => fs.readFileSync(path.join(f.output, file)));
  alterManifest(f.assets, manifest => manifest.clips.reverse());
  const second = generatePack({ assetRoot: f.assets, outputDir: f.output });
  assert.deepEqual(first, second);
  for (const [index, file] of ['voice_pack.c', 'voice_pack.h', 'voice_pack.inventory.json'].entries()) {
    assert.deepEqual(fs.readFileSync(path.join(f.output, file)), before[index]);
  }
  assert.doesNotMatch(before[0].toString(), /http|https|malloc|calloc/);
});

test('tampered MP3 fails before any existing build output changes', t => {
  const f = fixture(t);
  generatePack({ assetRoot: f.assets, outputDir: f.output });
  const source = fs.readFileSync(path.join(f.output, 'voice_pack.c'));
  const feed = path.join(f.assets, 'feed.mp3');
  const changed = fs.readFileSync(feed); changed[changed.length - 1] ^= 1;
  fs.writeFileSync(feed, changed);
  assert.throws(() => buildPack(f.assets), /MP3 bytes differ/);
  assert.throws(() => generatePack({ assetRoot: f.assets, outputDir: f.output }), /MP3 bytes differ/);
  assert.deepEqual(fs.readFileSync(path.join(f.output, 'voice_pack.c')), source);
  alterManifest(f.assets, manifest => { manifest.clips.find(clip => clip.id === 'feed').sha256 = sha256(changed); });
  assert.throws(() => loadPack(f.assets), /Manifest differs from reviewed/);
});

test('unknown, duplicate, missing, path-like IDs and invalid metadata are rejected', t => {
  const changes = [
    m => { m.clips[0].id = 'other'; m.clips[0].file = 'other.mp3'; },
    m => { m.clips[0] = m.clips[1]; },
    m => { m.clips.pop(); },
    m => { m.clips[0].file = '../feed.mp3'; },
    m => { m.clips[0].id = '../feed'; },
    m => { m.clips[0].duration_ms = -1; },
    m => { m.clips[0].sample_rate = 16000; },
    m => { m.clips[0].bytes += 1; },
  ];
  for (const change of changes) {
    const f = fixture(t);
    alterManifest(f.assets, change);
    assert.throws(() => generatePack({ assetRoot: f.assets, outputDir: f.output }));
    assert.equal(fs.existsSync(f.output), false);
  }
});

test('generated C lookup is bounded and compiled MP3 bytes survive unchanged', t => {
  let compiler;
  for (const candidate of [process.env.CC, 'gcc', 'clang', 'cc'].filter(Boolean)) {
    if (spawnSync(candidate, ['--version'], { encoding: 'utf8' }).status === 0) { compiler = candidate; break; }
  }
  if (!compiler) return t.skip('A host C11 compiler is not available; generation/integrity tests still ran.');
  const f = fixture(t);
  const inventory = generatePack({ assetRoot: f.assets, outputDir: f.output });
  const harness = path.join(f.root, 'lookup.c');
  fs.writeFileSync(harness, `#ifdef _MSC_VER
#define _CRT_SECURE_NO_WARNINGS
#endif
#include "voice_pack.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <stdint.h>
int main(int argc, char **argv) {
    assert(argc == 2);
    assert(rice_voice_get((rice_voice_id)-1) == NULL);
    assert(rice_voice_get(RICE_VOICE_COUNT) == NULL);
    assert(rice_voice_get((rice_voice_id)0x7fffffff) == NULL);
    assert(rice_voice_find(NULL, 4) == NULL);
    assert(rice_voice_find("feed", 0) == NULL);
    assert(rice_voice_find("feed", SIZE_MAX) == NULL);
    assert(rice_voice_find("Feed", 4) == NULL);
    assert(rice_voice_find("feed/../full", 12) == NULL);
    assert(rice_voice_find("feed\\0ignored", 12) == NULL);
    const char span[4] = {'f', 'e', 'e', 'd'};
    assert(rice_voice_find(span, sizeof(span)) == rice_voice_get(RICE_VOICE_FEED));
    uint32_t total = 0;
    for (unsigned i = 0; i < RICE_VOICE_COUNT; ++i) {
        const rice_voice_clip *clip = rice_voice_get((rice_voice_id)i);
        assert(clip && clip->id == (rice_voice_id)i && clip->size > 0);
        assert((uintptr_t)clip->data % 4 == 0);
        assert(clip->key_length == strlen(clip->key));
        assert(rice_voice_find(clip->key, clip->key_length) == clip);
        assert(strlen(clip->sha256) == 64);
        char file[2048];
        int n = snprintf(file, sizeof(file), "%s/%s.mp3", argv[1], clip->key);
        assert(n > 0 && (size_t)n < sizeof(file));
        FILE *out = fopen(file, "wb"); assert(out);
        assert(fwrite(clip->data, 1, clip->size, out) == clip->size);
        assert(fclose(out) == 0);
        total += clip->size;
    }
    assert(total == RICE_VOICE_TOTAL_BYTES);
    puts("Native voice C API: exact inventory, bounded lookup and alignment passed.");
    return 0;
}
`);
  const binary = path.join(f.root, process.platform === 'win32' ? 'voice-lookup.exe' : 'voice-lookup');
  const compile = spawnSync(compiler, ['-std=c11', '-Wall', '-Wextra', '-Werror', '-Wpedantic', '-O2', `-I${f.output}`,
    path.join(f.output, 'voice_pack.c'), harness, '-o', binary], { encoding: 'utf8' });
  assert.equal(compile.status, 0, `${compile.stdout}\n${compile.stderr}`);
  const decoded = path.join(f.root, 'compiled audio'); fs.mkdirSync(decoded);
  const run = spawnSync(binary, [decoded], { encoding: 'utf8' });
  assert.equal(run.status, 0, `${run.stdout}\n${run.stderr}`);
  for (const clip of inventory.clips) {
    const data = fs.readFileSync(path.join(decoded, `${clip.key}.mp3`));
    assert.equal(data.length, clip.bytes);
    assert.equal(sha256(data), clip.sha256);
    assert.deepEqual(data, fs.readFileSync(path.join(original, `${clip.key}.mp3`)));
  }
});
