'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { PINS, loadProfile, validateProfile, modelHeader, validateModelWindow,
  buildResources, generateResources, verifyResources } = require('../scripts/prepare-pose-resources.cjs');
const ROOT = path.resolve(__dirname, '..');
const stockRoot = path.join(ROOT, '.cache/stock-recovery-mini3');
const modelFile = path.join(ROOT, '.cache/pose-probe/movenet-heads-host-probe.tflite');
const inputsAvailable = fs.existsSync(modelFile) && ['tone', 'respak', 'emoji'].every(id => fs.existsSync(path.join(stockRoot, `${id}.bin`)));
const realInputs = { skip: inputsAvailable ? false : 'Requires pinned stock resources and derived heads model in .cache.' };
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
const cloneProfile = () => structuredClone(loadProfile());
function fixture(t) {
  const parent = path.join(ROOT, '.cache/resource-profile-tests');
  fs.mkdirSync(parent, { recursive: true });
  const root = fs.mkdtempSync(path.join(parent, 'case-'));
  t.after(() => {
    assert.equal(path.dirname(path.resolve(root)), parent);
    fs.rmSync(root, { recursive: true, force: true });
  });
  return root;
}

test('v1 profile stays inside resource region and preserves all reserved partitions', () => {
  const p = loadProfile();
  assert.equal(p.resources.reduce((sum, r) => sum + r.window_bytes, 0), 5 * 1024 * 1024);
  for (const mutation of [
    p => { p.resources[0].offset = 0; },
    p => { p.resources[3].offset = 0x600000; },
    p => { p.resources[1].window_bytes = 0xf00000; },
    p => { p.resources[2].offset = p.resources[1].offset; },
    p => { p.resources[0].offset++; },
    p => { p.resources[0].offset = NaN; },
    p => { p.resources[1].bytes = Number.MAX_SAFE_INTEGER; },
    p => { p.reserved.pop(); },
    p => { p.reserved[2].bytes = 0; },
    p => { p.resources[0].file = '../tone.bin'; },
    p => { p.resources[0].sha256 = '0'.repeat(64); },
    p => { p.resources[1] = p.resources[0]; },
    p => { p.model_contract.decoder_version++; },
    p => { p.requirements.ota_resource_writers_disabled = false; },
  ]) {
    const changed = cloneProfile(); mutation(changed);
    assert.throws(() => validateProfile(changed));
  }
});

test('header wire format is explicit, little endian and deterministic', () => {
  const bytes = modelHeader();
  assert.equal(bytes.length, 4096);
  assert.equal(bytes.subarray(0, 8).toString(), 'RICEPOSE');
  assert.deepEqual(Array.from({ length: 10 }, (_, i) => bytes.readUInt32LE(8 + i * 4)),
    [1, 4096, 1, 1, 4096, 2891896, 3145728, 1, 3, 1]);
  assert.equal(bytes.subarray(64, 96).toString('hex'), PINS.pose[3]);
  assert.ok(bytes.subarray(48, 64).every(v => v === 0));
  assert.ok(bytes.subarray(128).every(v => v === 0));
  assert.deepEqual(bytes, modelHeader());
});

test('real resources package deterministically and keep every stock byte', realInputs, t => {
  const outputDir = path.join(fixture(t), 'output');
  const dry = buildResources();
  assert.equal(fs.existsSync(outputDir), false);
  const manifest = generateResources({ outputDir });
  assert.deepEqual(manifest, dry.manifest);
  assert.deepEqual(verifyResources(outputDir), manifest);
  assert.equal(manifest.payloadWindow.offset, 0x100000);
  assert.equal(manifest.payloadWindow.bytes, 0x500000);
  assert.equal(manifest.flashed, false);
  const combined = fs.readFileSync(path.join(outputDir, 'resources.bin'));
  for (const id of ['tone', 'respak', 'emoji']) {
    const [offset, window, size, digest] = PINS[id];
    assert.deepEqual(fs.readFileSync(path.join(outputDir, `${id}.bin`)), fs.readFileSync(path.join(stockRoot, `${id}.bin`)));
    assert.equal(hash(combined.subarray(offset - 0x100000, offset - 0x100000 + size)), digest);
    assert.ok(combined.subarray(offset - 0x100000 + size, offset - 0x100000 + window).every(v => v === 0xff));
  }
  const pose = fs.readFileSync(path.join(outputDir, 'pose.bin'));
  assert.deepEqual(pose.subarray(4096, 4096 + PINS.pose[2]), fs.readFileSync(modelFile));
  assert.deepEqual(validateModelWindow(pose), { offset: 4096, bytes: PINS.pose[2], sha256: PINS.pose[3] });
  generateResources({ outputDir });
  for (const [file, bytes] of dry.files) assert.deepEqual(fs.readFileSync(path.join(outputDir, file)), bytes);
});

test('all header fields, model corruption, truncation and tail changes are rejected', realInputs, () => {
  const original = buildResources().files.get('pose.bin');
  for (const offset of [0, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48, 63, 64, 95, 96, 127, 128, 4095]) {
    const changed = Buffer.from(original); changed[offset] ^= 1;
    assert.throws(() => validateModelWindow(changed), /header/);
  }
  for (const length of [0, 4096, original.length - 1, original.length + 1]) {
    assert.throws(() => validateModelWindow(Buffer.alloc(length)), /exactly 3 MiB/);
  }
  const badModel = Buffer.from(original); badModel[4096 + 1000] ^= 1;
  assert.throws(() => validateModelWindow(badModel), /SHA-256/);
  const badMagic = Buffer.from(original); badMagic[4100] ^= 1;
  assert.throws(() => validateModelWindow(badMagic), /TFLite/);
  const badTail = Buffer.from(original); badTail[badTail.length - 1] = 0;
  assert.throws(() => validateModelWindow(badTail), /padding/);
});

test('bad inputs fail before existing outputs change and cannot overwrite inputs', realInputs, t => {
  const root = fixture(t), copiedStock = path.join(root, 'stock'), copiedModel = path.join(root, 'model.tflite');
  fs.mkdirSync(copiedStock);
  for (const id of ['tone', 'respak', 'emoji']) fs.copyFileSync(path.join(stockRoot, `${id}.bin`), path.join(copiedStock, `${id}.bin`));
  fs.copyFileSync(modelFile, copiedModel);
  const options = { stockRoot: copiedStock, modelFile: copiedModel, outputDir: path.join(root, 'output') };
  generateResources(options);
  const oldManifest = fs.readFileSync(path.join(options.outputDir, 'manifest.json'));
  const wrong = fs.readFileSync(copiedModel); wrong[1000] ^= 1; fs.writeFileSync(copiedModel, wrong);
  assert.throws(() => generateResources(options), /SHA-256/);
  assert.deepEqual(fs.readFileSync(path.join(options.outputDir, 'manifest.json')), oldManifest);
  fs.copyFileSync(modelFile, copiedModel);
  const tone = path.join(copiedStock, 'tone.bin');
  const wrongTone = fs.readFileSync(tone); wrongTone[1000] ^= 1; fs.writeFileSync(tone, wrongTone);
  assert.throws(() => generateResources(options), /SHA-256/);
  assert.throws(() => generateResources({ ...options, outputDir: copiedStock }), /overwrite inputs/);
});

test('output verification rejects coordinated manifest edits and mixed resource sets', realInputs, t => {
  const outputDir = path.join(fixture(t), 'output');
  generateResources({ outputDir });
  const file = path.join(outputDir, 'manifest.json');
  const manifest = JSON.parse(fs.readFileSync(file));
  manifest.resources[2].offset = 0xf00000;
  fs.writeFileSync(file, JSON.stringify(manifest));
  assert.throws(() => verifyResources(outputDir), /pinned layout/);
  generateResources({ outputDir });
  const tone = fs.readFileSync(path.join(outputDir, 'tone.bin')); tone[3] ^= 1;
  fs.writeFileSync(path.join(outputDir, 'tone.bin'), tone);
  const edited = JSON.parse(fs.readFileSync(file));
  edited.resources[0].fileSha256 = hash(tone);
  fs.writeFileSync(file, JSON.stringify(edited));
  assert.throws(() => verifyResources(outputDir), /differs from stock/);
});
