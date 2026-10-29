'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const { loadPack } = require('../scripts/generate-native-voice.cjs');
const { parseElf32, verifyImage, FLASH_START, FLASH_END } = require('../scripts/verify-native-image.cjs');
const pack = loadPack();
const align = number => (number + 3) & ~3;
const sha256 = bytes => crypto.createHash('sha256').update(bytes).digest('hex');

// A synthetic ELF fixture, never executable hardware evidence. Its resource
// payload deliberately uses the real pinned MP3 bytes to exercise full checks.
function fixture() {
  const application = Buffer.from('-- synthetic ELF verifier fixture\n');
  const manifest = { schema: 1, app: { bytes: application.length, sha256: sha256(application) } };
  const resourceSize = align(pack.reduce((sum, clip) => sum + clip.bytes, 0) + application.length + 1);
  const resourceOffset = 44, binaryBytes = Buffer.alloc(resourceOffset + resourceSize);
  binaryBytes.writeUInt32LE(0x00000013, 0); // RISC-V NOP, not executed by tests.
  const symbols = [{ name: '', value: 0, size: 0, info: 0, section: 0 }];
  let current = resourceOffset;
  for (const clip of pack) {
    clip.data.copy(binaryBytes, current);
    symbols.push({ name: `rice_voice_data_${clip.id}`, value: FLASH_START + current, size: clip.bytes, info: 1, section: 3 });
    current += clip.bytes;
  }
  const appOffset = current;
  application.copy(binaryBytes, appOffset);
  symbols.push({ name: 'rice_miniapp_source', value: FLASH_START + appOffset, size: application.length + 1, info: 17, section: 3 });
  symbols.push({ name: '__ricestep_assets_start', value: FLASH_START + resourceOffset, size: 0, info: 16, section: 3 });
  symbols.push({ name: '__ricestep_assets_end', value: FLASH_START + binaryBytes.length, size: 0, info: 16, section: 3 });
  for (const [name, offset] of Object.entries({
    __scat_copy_base: 4, __scat_copy_last: 16, __scat_zero_base: 16, __scat_zero_last: 24,
    __psram_scat_copy_base: 24, __psram_scat_copy_last: 36, __psram_scat_zero_base: 36, __psram_scat_zero_last: 44,
  })) symbols.push({ name, value: FLASH_START + offset, size: 0, info: 16, section: 2 });
  let names = '\0';
  for (const symbol of symbols) { symbol.nameOffset = names.length; names += symbol.name + '\0'; }
  const strings = Buffer.from(names);
  const sectionNames = ['', '.init', '.scatab', '.ricestep.assets', '.strtab', '.symtab', '.shstrtab'];
  let sectionStrings = '\0';
  const sectionNameOffsets = sectionNames.map(name => { const start = sectionStrings.length; sectionStrings += name + '\0'; return start; });
  const sectionNameBytes = Buffer.from(sectionStrings);
  const fileStart = 256;
  const strtabOffset = fileStart + binaryBytes.length;
  const symtabOffset = align(strtabOffset + strings.length);
  const shstrOffset = symtabOffset + symbols.length * 16;
  const shoff = align(shstrOffset + sectionNameBytes.length);
  const elfBytes = Buffer.alloc(shoff + sectionNames.length * 40);
  const w16 = (offset, value) => elfBytes.writeUInt16LE(value, offset);
  const w32 = (offset, value) => elfBytes.writeUInt32LE(value, offset);
  Buffer.from([0x7f, 0x45, 0x4c, 0x46, 1, 1, 1]).copy(elfBytes);
  w16(16, 2); w16(18, 243); w32(20, 1); w32(24, FLASH_START); w32(28, 52); w32(32, shoff); w32(36, 1);
  w16(40, 52); w16(42, 32); w16(44, 1); w16(46, 40); w16(48, sectionNames.length); w16(50, 6);
  for (const [index, value] of [1, fileStart, FLASH_START, FLASH_START, binaryBytes.length, binaryBytes.length, 5, 4].entries()) w32(52 + index * 4, value);
  binaryBytes.copy(elfBytes, fileStart); strings.copy(elfBytes, strtabOffset); sectionNameBytes.copy(elfBytes, shstrOffset);
  const symbolOffsets = {};
  symbols.forEach((symbol, index) => {
    const offset = symtabOffset + index * 16; symbolOffsets[symbol.name] = offset;
    w32(offset, symbol.nameOffset); w32(offset + 4, symbol.value); w32(offset + 8, symbol.size);
    elfBytes[offset + 12] = symbol.info; w16(offset + 14, symbol.section);
  });
  const sections = [
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [1, 6, FLASH_START, fileStart, 4, 0, 0, 4, 0],
    [1, 2, FLASH_START + 4, fileStart + 4, 40, 0, 0, 4, 0],
    [1, 2, FLASH_START + resourceOffset, fileStart + resourceOffset, resourceSize, 0, 0, 4, 0],
    [3, 0, 0, strtabOffset, strings.length, 0, 0, 1, 0],
    [2, 0, 0, symtabOffset, symbols.length * 16, 4, 15, 4, 16],
    [3, 0, 0, shstrOffset, sectionNameBytes.length, 0, 0, 1, 0],
  ];
  sections.forEach((fields, index) => {
    w32(shoff + index * 40, sectionNameOffsets[index]);
    fields.forEach((value, field) => w32(shoff + index * 40 + 4 + field * 4, value));
  });
  return { elfBytes, binaryBytes, manifest, voicePack: pack,
    meta: { fileStart, resourceOffset, appOffset, shoff, symbolOffsets } };
}
function mutateBoth(f, binaryOffset, value) {
  f.binaryBytes[binaryOffset] = value;
  f.elfBytes[f.meta.fileStart + binaryOffset] = value;
}

test('valid synthetic ELF proves exact resources, readonly Flash placement and no scatter copy', () => {
  const report = verifyImage(fixture());
  assert.equal(report.result, 'static_checks_passed');
  assert.equal(report.hardware_validated, false);
  assert.equal(report.voice.count, 14); assert.equal(report.voice.bytes, 375408);
  assert.equal(report.assets.vma, report.assets.lma);
  assert.equal(report.startup_scatter_tables.length, 4);
});

test('truncated/wrong-target/malformed table inputs fail before out-of-bounds reads', () => {
  assert.throws(() => parseElf32(Buffer.alloc(51)), /bounds/);
  for (const [offset, value, bytes] of [[4, 2, 1], [5, 2, 1], [18, 62, 2], [32, 0xfffffff0, 4], [50, 0xffff, 2]]) {
    const f = fixture();
    f.elfBytes[`writeUInt${bytes * 8}${bytes === 1 ? '' : 'LE'}`](value, offset);
    assert.throws(() => parseElf32(f.elfBytes));
  }
  const f = fixture();
  f.elfBytes.writeUInt32LE(0xfffffff0, f.meta.symbolOffsets.rice_voice_data_feed);
  assert.throws(() => parseElf32(f.elfBytes), /string table\/offset/);
});

test('RAM placement, writable LOAD, wrong LMA and weak alignment are rejected', () => {
  for (const change of [
    f => f.elfBytes.writeUInt32LE(0x28300000, f.meta.shoff + 3 * 40 + 12),
    f => f.elfBytes.writeUInt32LE(7, 52 + 24),
    f => f.elfBytes.writeUInt32LE(FLASH_START + 4, 52 + 12),
    f => f.elfBytes.writeUInt32LE(1, f.meta.shoff + 3 * 40 + 32),
    f => f.elfBytes.writeUInt32LE(3, f.meta.shoff + 3 * 40 + 8),
  ]) { const f = fixture(); change(f); assert.throws(() => verifyImage(f)); }
});

test('corrupt MP3, mismatched binary and changed Lua cannot produce a success report', () => {
  const clip = fixture();
  mutateBoth(clip, clip.meta.resourceOffset, clip.binaryBytes[clip.meta.resourceOffset] ^ 1);
  assert.throws(() => verifyImage(clip), /Voice byte\/hash mismatch/);
  const binary = fixture(); binary.binaryBytes[binary.meta.resourceOffset] ^= 1;
  assert.throws(() => verifyImage(binary), /Binary resource bytes differ/);
  const app = fixture(); mutateBoth(app, app.meta.appOffset, 0x78);
  assert.throws(() => verifyImage(app), /Lua source hash/);
  const terminator = fixture(); mutateBoth(terminator, terminator.meta.appOffset + terminator.manifest.app.bytes, 1);
  assert.throws(() => verifyImage(terminator), /final NUL/);
});

test('missing/duplicate/misaligned/wrong-sized resource symbols are rejected', () => {
  for (const change of [
    f => f.elfBytes.writeUInt32LE(0, f.meta.symbolOffsets.rice_voice_data_feed),
    f => f.elfBytes.writeUInt32LE(f.elfBytes.readUInt32LE(f.meta.symbolOffsets.rice_voice_data_burnt), f.meta.symbolOffsets.rice_voice_data_feed),
    f => f.elfBytes.writeUInt32LE(1, f.meta.symbolOffsets.rice_voice_data_feed + 8),
    f => f.elfBytes.writeUInt32LE(FLASH_START + 45, f.meta.symbolOffsets.rice_voice_data_feed + 4),
  ]) { const f = fixture(); change(f); assert.throws(() => verifyImage(f)); }
});

test('startup copies and clears of assets are rejected in ELF and in the binary', () => {
  const copy = fixture();
  for (const [relative, value] of [[4, 4], [8, 0x20000000], [12, FLASH_START + copy.meta.resourceOffset]]) {
    copy.elfBytes.writeUInt32LE(value, copy.meta.fileStart + relative); copy.binaryBytes.writeUInt32LE(value, relative);
  }
  assert.throws(() => verifyImage(copy), /scatter copy source overlaps/);
  const clear = fixture();
  for (const [relative, value] of [[16, 4], [20, FLASH_START + clear.meta.resourceOffset]]) {
    clear.elfBytes.writeUInt32LE(value, clear.meta.fileStart + relative); clear.binaryBytes.writeUInt32LE(value, relative);
  }
  assert.throws(() => verifyImage(clear), /scatter destination overlaps/);
  const mismatch = fixture(); mismatch.binaryBytes[4] = 4;
  assert.throws(() => verifyImage(mismatch), /Binary startup scatter table differs/);
});

test('oversize binaries, asset ranges and app manifest mismatch fail closed', () => {
  const big = fixture(); big.binaryBytes = Buffer.alloc(FLASH_END - FLASH_START + 1);
  assert.throws(() => verifyImage(big), /4 MiB/);
  const section = fixture(); section.elfBytes.writeUInt32LE(0xfffffff0, section.meta.shoff + 3 * 40 + 16);
  assert.throws(() => verifyImage(section), /bounds/);
  const manifest = fixture(); manifest.manifest.app.bytes++;
  assert.throws(() => verifyImage(manifest), /symbol size differs/);
});
