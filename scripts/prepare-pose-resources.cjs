'use strict';

// Local, offline packaging only. Never downloads, edits the SDK or invokes a flasher.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const ROOT = path.resolve(__dirname, '..');
const PROFILE_PATH = path.join(ROOT, 'firmware/resource-profiles/mini3-pose-v1.json');
const DEFAULT_STOCK = path.join(ROOT, '.cache/stock-recovery-mini3');
const DEFAULT_MODEL = path.join(ROOT, '.cache/pose-probe/movenet-heads-host-probe.tflite');
const DEFAULT_OUTPUT = path.join(ROOT, 'dist/native/pose-resources');
const MAGIC = Buffer.from('RICEPOSE', 'ascii');
const SOURCE_SHA = 'cd7cc22fa946e5d146a7b98d496853e1923e22828d3972d579973f27f91bb105';
const PINS = Object.freeze({
  tone: [0x100000, 0x80000, 351232, '382bc1ef35abbefed4da06d12e65d8aaaca688666e1e4996d32654534f96e67e'],
  pose: [0x180000, 0x300000, 2891896, '9051a5ceebc1eb5f535c1b1afe58d33ce878bf9358a159cbfc689c1abebf9c68'],
  respak: [0x480000, 0x100000, 983040, '31b05d15a242b8eb87a24ace43141c06e4ae6a54eff7ab17c3b9c01721b7c816'],
  emoji: [0x580000, 0x80000, 492544, 'af4d0d9dbbd0ba7d33a001966160542fd645c529b1b7f795a68c2de7c36f7aa7'],
});
for (const value of Object.values(PINS)) Object.freeze(value);
const RESERVED = Object.freeze([[0, 0x100000], [0x600000, 0x400000], [0xa00000, 0x500000], [0xf00000, 0x100000]]);
const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const fail = message => { throw new Error(message); };
const overlaps = (start, bytes, otherStart, otherBytes) => start < otherStart + otherBytes && otherStart < start + bytes;

function validateProfile(profile) {
  if (!profile || profile.schema !== 1 || profile.id !== 'ricestep-mini3-pose-v1' || profile.board !== 'arcs_mini3' ||
      profile.flash_base !== 0x30000000 || profile.flash_bytes !== 0x1000000 || profile.resource_start !== 0x100000 ||
      profile.resource_end !== 0x600000 || profile.alignment !== 0x10000) fail('Unsupported resource profile identity or bounds.');
  if (!Array.isArray(profile.reserved) || profile.reserved.length !== RESERVED.length ||
      profile.reserved.some((r, i) => !r || r.offset !== RESERVED[i][0] || r.bytes !== RESERVED[i][1])) {
    fail('Reserved boot/AP/CP/OTA/KV windows must remain unchanged.');
  }
  if (!Array.isArray(profile.resources) || profile.resources.length !== 4) fail('Expected four pinned resources.');
  const seen = new Set();
  for (const r of profile.resources) {
    if (!r || !Object.hasOwn(PINS, r.id) || seen.has(r.id)) fail('Unknown or duplicate resource.');
    seen.add(r.id);
    if (![r.offset, r.window_bytes, r.bytes].every(Number.isSafeInteger) || r.window_bytes <= 0 || r.bytes <= 0 ||
        r.offset < profile.resource_start || r.offset + r.window_bytes > profile.resource_end ||
        r.offset % profile.alignment || r.window_bytes % profile.alignment) fail('Resource window is out of bounds or misaligned.');
    if (RESERVED.some(([start, bytes]) => overlaps(r.offset, r.window_bytes, start, bytes))) fail('Resource overlaps a reserved partition.');
    const headerBytes = r.id === 'pose' ? 4096 : 0;
    if (r.bytes + headerBytes > r.window_bytes) fail('Payload exceeds its resource window.');
    const expected = PINS[r.id];
    if (r.offset !== expected[0] || r.window_bytes !== expected[1] || r.bytes !== expected[2] || r.sha256 !== expected[3] ||
        r.file !== `${r.id}.bin` || (r.id === 'pose' && r.header_bytes !== 4096) ||
        (r.id !== 'pose' && r.header_bytes !== undefined)) fail(`Resource differs from pinned v1 layout: ${r.id}`);
  }
  const sorted = [...profile.resources].sort((a, b) => a.offset - b.offset);
  for (let i = 1; i < sorted.length; ++i) {
    if (overlaps(sorted[i - 1].offset, sorted[i - 1].window_bytes, sorted[i].offset, sorted[i].window_bytes)) fail('Resource windows overlap.');
  }
  const c = profile.model_contract;
  if (!c || c.header_version !== 1 || c.profile_version !== 1 || c.format !== 1 || c.decoder_version !== 1 ||
      c.input_version !== 1 || c.tflite_schema !== 3 || c.source_sha256 !== SOURCE_SHA) fail('Unsupported model contract.');
  const requirements = profile.requirements;
  if (!requirements || ['matching_cp_resource_profile', 'wakeup_engine_disabled', 'ota_resource_writers_disabled',
      'restore_cp_and_stock_resources_together'].some(key => requirements[key] !== true)) fail('Required deployment restrictions are missing.');
  return profile;
}

function loadProfile() { return validateProfile(JSON.parse(fs.readFileSync(PROFILE_PATH, 'utf8'))); }

function modelHeader() {
  const header = Buffer.alloc(4096);
  MAGIC.copy(header);
  [1, 4096, 1, 1, 4096, PINS.pose[2], PINS.pose[1], 1, 3, 1].forEach((value, i) => header.writeUInt32LE(value, 8 + i * 4));
  Buffer.from(PINS.pose[3], 'hex').copy(header, 64);
  Buffer.from(SOURCE_SHA, 'hex').copy(header, 96);
  return header;
}

function validateModelWindow(window) {
  if (!Buffer.isBuffer(window) || window.length !== PINS.pose[1]) fail('Model window must be exactly 3 MiB.');
  if (!window.subarray(0, 4096).equals(modelHeader())) fail('Model header is not the pinned v1 wire contract.');
  const model = window.subarray(4096, 4096 + PINS.pose[2]);
  if (model.subarray(4, 8).toString('ascii') !== 'TFL3') fail('Model is not a TFLite FlatBuffer.');
  if (hash(model) !== PINS.pose[3]) fail('Model SHA-256 differs from the pinned heads model.');
  if (window.subarray(4096 + model.length).some(byte => byte !== 0xff)) fail('Model window padding must remain erased (FF).');
  return { offset: 4096, bytes: model.length, sha256: PINS.pose[3] };
}

function readPinned(file, id) {
  const pin = PINS[id];
  const info = fs.statSync(file);
  if (!info.isFile() || info.size !== pin[2]) fail(`Invalid input size: ${id}`);
  const bytes = fs.readFileSync(file);
  if (bytes.length !== pin[2] || hash(bytes) !== pin[3]) fail(`Input SHA-256 differs from pinned ${id}.`);
  return bytes;
}

function buildResources({ stockRoot = DEFAULT_STOCK, modelFile = DEFAULT_MODEL } = {}) {
  const profile = loadProfile();
  const inputs = {};
  for (const id of ['tone', 'respak', 'emoji']) inputs[id] = readPinned(path.join(stockRoot, `${id}.bin`), id);
  inputs.pose = readPinned(modelFile, 'pose');
  const poseWindow = Buffer.alloc(PINS.pose[1], 0xff);
  modelHeader().copy(poseWindow);
  inputs.pose.copy(poseWindow, 4096);
  validateModelWindow(poseWindow);
  const files = new Map([['tone.bin', inputs.tone], ['pose.bin', poseWindow], ['respak.bin', inputs.respak], ['emoji.bin', inputs.emoji]]);
  const image = Buffer.alloc(profile.resource_end - profile.resource_start, 0xff);
  for (const r of profile.resources) files.get(r.file).copy(image, r.offset - profile.resource_start);
  const manifest = {
    schema: 1, profile: profile.id, board: profile.board, status: 'offline-candidate-not-deployed',
    deviceAccessed: false, flashed: false, compatibleWithUnmodifiedStockFirmware: false,
    payloadWindow: { offset: profile.resource_start, bytes: image.length, sha256: hash(image), file: 'resources.bin' },
    reserved: profile.reserved, requirements: profile.requirements,
    modelContract: profile.model_contract,
    resources: profile.resources.map(r => ({ id: r.id, offset: r.offset, windowBytes: r.window_bytes,
      file: r.file, fileBytes: files.get(r.file).length, fileSha256: hash(files.get(r.file)),
      payloadOffset: r.id === 'pose' ? 4096 : 0, payloadBytes: r.bytes, payloadSha256: r.sha256 })),
  };
  files.set('resources.bin', image);
  files.set('manifest.json', Buffer.from(JSON.stringify(manifest, null, 2) + '\n'));
  return { profile, manifest, files };
}

function verifyResources(directory) {
  const profile = loadProfile();
  const manifest = JSON.parse(fs.readFileSync(path.join(directory, 'manifest.json'), 'utf8'));
  if (manifest.schema !== 1 || manifest.profile !== profile.id || manifest.board !== profile.board ||
      manifest.status !== 'offline-candidate-not-deployed' || manifest.deviceAccessed !== false || manifest.flashed !== false ||
      manifest.compatibleWithUnmodifiedStockFirmware !== false || !Array.isArray(manifest.resources) ||
      manifest.resources.length !== profile.resources.length || JSON.stringify(manifest.reserved) !== JSON.stringify(profile.reserved) ||
      JSON.stringify(manifest.requirements) !== JSON.stringify(profile.requirements) ||
      JSON.stringify(manifest.modelContract) !== JSON.stringify(profile.model_contract)) fail('Invalid resource manifest.');
  const imageInfo = manifest.payloadWindow;
  if (!imageInfo || imageInfo.offset !== profile.resource_start || imageInfo.bytes !== profile.resource_end - profile.resource_start ||
      imageInfo.file !== 'resources.bin') fail('Invalid aggregate image bounds.');
  const image = fs.readFileSync(path.join(directory, imageInfo.file));
  if (image.length !== imageInfo.bytes || hash(image) !== imageInfo.sha256) fail('Aggregate resource image checksum/size mismatch.');
  const rebuilt = Buffer.alloc(image.length, 0xff);
  for (const [i, resource] of profile.resources.entries()) {
    const r = manifest.resources[i];
    if (!r || r.id !== resource.id || r.file !== resource.file || r.offset !== resource.offset ||
        r.windowBytes !== resource.window_bytes || r.payloadOffset !== (r.id === 'pose' ? 4096 : 0) ||
        r.payloadBytes !== resource.bytes || r.payloadSha256 !== resource.sha256 ||
        r.fileBytes !== (r.id === 'pose' ? resource.window_bytes : resource.bytes)) fail('Resource manifest differs from pinned layout.');
    const bytes = fs.readFileSync(path.join(directory, r.file));
    if (bytes.length !== r.fileBytes || hash(bytes) !== r.fileSha256) fail(`Output file checksum/size mismatch: ${r.id}`);
    if (r.id === 'pose') validateModelWindow(bytes);
    else if (hash(bytes) !== resource.sha256) fail(`Output resource differs from stock: ${r.id}`);
    bytes.copy(rebuilt, resource.offset - profile.resource_start);
  }
  if (!image.equals(rebuilt)) fail('Aggregate image differs from validated resource windows.');
  return manifest;
}

function generateResources(options = {}) {
  const output = path.resolve(options.outputDir || DEFAULT_OUTPUT);
  const stock = fs.realpathSync(options.stockRoot || DEFAULT_STOCK);
  const model = fs.realpathSync(options.modelFile || DEFAULT_MODEL);
  const existingOutput = fs.existsSync(output) ? fs.realpathSync(output) : output;
  const isWithin = (base, candidate) => { const relative = path.relative(base, candidate); return relative === '' || (!relative.startsWith('..' + path.sep) && relative !== '..' && !path.isAbsolute(relative)); };
  if (isWithin(stock, existingOutput) || isWithin(existingOutput, stock) || isWithin(existingOutput, model) ||
      isWithin(path.join(ROOT, '.cache/native-mini3-sdk'), existingOutput)) fail('Output cannot overwrite inputs or the SDK checkout.');
  // Validate all bytes before touching existing output files; manifest is committed last.
  const result = buildResources({ stockRoot: stock, modelFile: model });
  fs.mkdirSync(output, { recursive: true });
  for (const [name, data] of result.files) fs.writeFileSync(path.join(output, name), data);
  verifyResources(output);
  return result.manifest;
}

if (require.main === module) {
  try {
    const args = process.argv.slice(2), options = {};
    let verify = null;
    for (let i = 0; i < args.length; ++i) {
      const name = args[i], value = args[++i];
      if (!value) fail(`Missing value: ${name}`);
      if (name === '--stock-root') options.stockRoot = value;
      else if (name === '--model') options.modelFile = value;
      else if (name === '--output') options.outputDir = value;
      else if (name === '--verify') verify = value;
      else fail(`Unknown option: ${name}`);
    }
    if (verify && Object.keys(options).length) fail('--verify cannot be combined with generation options.');
    const result = verify ? verifyResources(path.resolve(verify)) : generateResources(options);
    process.stdout.write(`${verify ? 'Verified' : 'Prepared'} ${result.profile}: ${result.payloadWindow.bytes} bytes; no device access or flashing.\n`);
  } catch (error) { process.stderr.write(`${error.message}\n`); process.exitCode = 1; }
}

module.exports = { PINS, PROFILE_PATH, loadProfile, validateProfile, modelHeader, validateModelWindow,
  buildResources, generateResources, verifyResources };
