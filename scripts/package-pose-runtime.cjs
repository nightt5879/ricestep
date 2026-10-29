'use strict';
// Produce a reviewable two-image candidate, never flash or change a device.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { parseElf32, verifyNativeImage, FLASH_START } = require('./verify-native-image.cjs');
const { verify: verifyLink } = require('./verify-pose-link.cjs');
const { loadProfile, verifyResources } = require('./prepare-pose-resources.cjs');
const sha = b => crypto.createHash('sha256').update(b).digest('hex');
const check = (value, message) => { if (!value) throw new Error(message); };

function verifyRuntime(elfBytes, binary, config, manifest, commands) {
  check(manifest.poseRuntime === true && manifest.poseLinkCheck === false, 'Not a camera runtime overlay');
  for (const key of ['CONFIG_RICESTEP_STANDALONE', 'CONFIG_RICESTEP_POSE', 'CONFIG_BOARD_ARCS_MINI3']) {
    check(new RegExp(`^${key}=y$`, 'm').test(config), `Missing ${key}`);
  }
  check(!/^CONFIG_(?:OTA|MINIAPP_ADB_DEBUG)=y$/m.test(config), 'OTA/debug writers must be disabled');
  check(!/^CONFIG_ARCS_GPDMA_DATA_ONLY=y$/m.test(config), 'Camera requires the shared DMA lifecycle implementation');
  const elf = parseElf32(elfBytes), profile = loadProfile();
  const expected = Buffer.alloc(48);
  [0x45434952, 0x45534f50, 1, 1, ...profile.resources.flatMap(r => [profile.flash_base + r.offset, r.window_bytes])]
    .forEach((n, i) => expected.writeUInt32LE(n, i * 4));
  const matching = elf.symbols.filter(s => s.name === 'rice_pose_profile' && s.sectionIndex !== 0 && s.type === 1 && s.size === 48);
  check(matching.length === 1, 'Missing runtime resource profile');
  const symbol = matching[0];
  const loads = elf.programs.filter(p => p.type === 1 && symbol.value >= p.vma && symbol.value + 48 <= p.vma + p.fileSize);
  check(loads.length === 1 && loads[0].lma === loads[0].vma, 'Profile is not immutable Flash metadata');
  check(elfBytes.subarray(loads[0].offset + symbol.value - loads[0].vma, loads[0].offset + symbol.value - loads[0].vma + 48).equals(expected), 'Runtime layout does not match resources');
  // The SDK's reviewed ARCS mkhdr changes exactly the size and header checksum.
  // Every other file-backed LOAD byte must match; this catches stale CP binaries.
  check(binary.length > 384 && binary.length <= 0x400000, 'CP size invalid');
  check(binary.toString('ascii', 336, 340) === 'ARCS', 'Wrong CP boot header');
  check(binary.readUInt32LE(324) === binary.length, 'CP header size differs from binary');
  let sumv = 0, sumh = 0;
  for (let i = 0; i < 380; ++i) { if (i < 320) sumv += binary[i]; else sumh += binary[i]; }
  sumv += sumh + (sumh & 255) + (sumh >> 8);
  check(binary.readUInt16LE(380) === (sumh & 65535) && binary.readUInt16LE(382) === (sumv & 65535), 'CP header checksum differs');
  let imageEnd = 0;
  for (const p of elf.programs.filter(p => p.type === 1 && p.fileSize)) {
    const offset = p.lma - FLASH_START;
    check(offset >= 0 && offset + p.fileSize <= binary.length, 'CP LOAD outside image');
    imageEnd = Math.max(imageEnd, offset + p.fileSize);
    for (let i = 0; i < p.fileSize; ++i) {
      const at = offset + i;
      if ((at >= 324 && at < 328) || (at >= 380 && at < 384)) continue;
      check(binary[at] === elfBytes[p.offset + i], `CP differs from ELF at ${at}`);
    }
  }
  check(imageEnd === binary.length, 'Unexpected CP trailing data');
  const required = ['rice_pose_start', 'rice_pose_stop', 'rice_pose_set_active', 'rice_pose_snapshot', 'ps_start', 'ps_update',
    'service_camera_pose_claim', 'service_camera_pose_release', 'rice_pose_alloc', 'rice_pose_free',
    'rice_pose_camera_fb_alloc', 'rice_pose_camera_fb_free',
    'rice_pose_camera_queue_create', 'rice_pose_camera_queue_delete'];
  for (const name of required) check(elf.symbols.some(s => s.name === name && s.sectionIndex !== 0 && s.type === 2 && s.size > 0), `Missing camera runtime function ${name}`);
  const worker = commands.filter(c => /standalone[\\/]rice_pose\.c$/.test(c.file));
  check(worker.length === 1, 'Missing worker compile command');
  const flags = worker[0].arguments || worker[0].command.match(/"[^"\r\n]*"|[^\s]+/g);
  check(flags.filter(f => ['-ffast-math', '-fno-fast-math', '-Ofast'].includes(f)).at(-1) === '-fno-fast-math' &&
    flags.includes('-ffp-contract=off') && flags.includes('-mabi=ilp32') && flags.includes('-march=rv32imac_zba_zbb_zbc_zbs'),
    'Worker needs exact floating point semantics and Mini3 ABI');
  return { profile: profile.id, firmwareSha256: sha(binary), elfSha256: sha(elfBytes),
    linked: required, poseLink: verifyLink(elfBytes, commands), hardwareValidated: false };
}

function packageRuntime(directory, resources, compileCommands) {
  const elfPath = path.join(directory, 'arcs-mini3-ricestep.elf');
  const binaryPath = path.join(directory, 'arcs-mini3-ricestep.bin');
  const manifestPath = path.join(directory, 'overlay-manifest.json');
  const manifest = JSON.parse(fs.readFileSync(manifestPath));
  verifyNativeImage({ elfPath, binaryPath, manifestPath });
  const report = verifyRuntime(fs.readFileSync(elfPath), fs.readFileSync(binaryPath),
    fs.readFileSync(path.join(directory, '.config'), 'utf8'), manifest, JSON.parse(fs.readFileSync(compileCommands)));
  const resourceManifest = verifyResources(resources);
  const resourceBytes = fs.readFileSync(path.join(resources, 'resources.bin'));
  const result = { schema: 1, profile: report.profile, status: 'offline-candidate-not-deployed',
    hardwareValidated: false, flashed: false, inputsVerified: report,
    images: [resourceManifest.payloadWindow, { offset: 0x600000, bytes: fs.statSync(binaryPath).size,
      sha256: report.firmwareSha256, file: path.basename(binaryPath) }],
    reserved: resourceManifest.reserved.filter(r => r.name !== 'cp-app'),
    requirements: resourceManifest.requirements };
  // Write only after both images and their matching layout have passed.
  fs.writeFileSync(path.join(directory, 'resources.bin'), resourceBytes);
  fs.writeFileSync(path.join(directory, 'paired-candidate.json'), JSON.stringify(result, null, 2) + '\n');
  return result;
}
if (require.main === module) {
  const [directory, resources, commands] = process.argv.slice(2);
  if (!directory || !resources || !commands) throw new Error('Usage: package-pose-runtime.cjs FIRMWARE_DIR RESOURCES_DIR COMPILE_COMMANDS');
  const result = packageRuntime(directory, resources, commands);
  console.log(`Verified matching ${result.profile} CP + resources candidate; hardware untested, no flashing.`);
}
module.exports = { verifyRuntime, packageRuntime };
