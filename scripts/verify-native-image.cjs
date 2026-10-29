'use strict';
// Static ELF/bin verification only. Never programs or communicates with a device.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { loadPack } = require('./generate-native-voice.cjs');

const FLASH_START = 0x30600000, FLASH_END = 0x30a00000;
const MAX_ELF_BYTES = 256 * 1024 * 1024;
const sha256 = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const hex = value => `0x${value.toString(16).padStart(8, '0')}`;
function ensure(condition, message) { if (!condition) throw new Error(message); }
function range(buffer, start, size, label) {
  ensure(Number.isSafeInteger(start) && Number.isSafeInteger(size) && start >= 0 && size >= 0 &&
    start <= buffer.length && size <= buffer.length - start, `${label}: outside file bounds`);
  return buffer.subarray(start, start + size);
}
function u32Range(start, size, label) {
  ensure(start + size <= 0x100000000, `${label}: address range overflows ELF32`);
}
function overlaps(a, an, b, bn) { return an > 0 && bn > 0 && a < b + bn && b < a + an; }
function powerOfTwo(n) { return n > 0 && (n & (n - 1)) === 0; }

function parseElf32(bytes) {
  ensure(Buffer.isBuffer(bytes) && bytes.length <= MAX_ELF_BYTES, 'ELF must be a bounded Buffer');
  range(bytes, 0, 52, 'ELF header');
  ensure(bytes.subarray(0, 4).equals(Buffer.from([0x7f, 0x45, 0x4c, 0x46])), 'Not an ELF file');
  ensure(bytes[4] === 1 && bytes[5] === 1 && bytes[6] === 1, 'Expected ELF32 little-endian version 1');
  const u16 = offset => bytes.readUInt16LE(offset), u32 = offset => bytes.readUInt32LE(offset);
  ensure(u16(16) === 2 && u16(18) === 243 && u32(20) === 1, 'Expected a RISC-V executable ELF');
  ensure(u16(40) === 52 && u16(42) === 32 && u16(46) === 40, 'Unexpected ELF32 header entry sizes');
  const phoff = u32(28), shoff = u32(32), phnum = u16(44), shnum = u16(48), shstr = u16(50);
  ensure(phnum > 0 && phnum < 4096 && shnum > 0 && shnum < 4096 && shstr < shnum, 'Missing/extended ELF tables are unsupported');
  range(bytes, phoff, phnum * 32, 'Program-header table');
  range(bytes, shoff, shnum * 40, 'Section-header table');
  const programs = Array.from({ length: phnum }, (_, index) => {
    const offset = phoff + index * 32;
    const p = { index, type: u32(offset), offset: u32(offset + 4), vma: u32(offset + 8), lma: u32(offset + 12),
      fileSize: u32(offset + 16), memorySize: u32(offset + 20), flags: u32(offset + 24), alignment: u32(offset + 28) };
    if (p.fileSize) range(bytes, p.offset, p.fileSize, `Program ${index}`);
    if (p.type === 1) {
      ensure(p.fileSize <= p.memorySize, `LOAD ${index} has file size larger than memory size`);
      u32Range(p.vma, p.memorySize, `LOAD ${index} VMA`); u32Range(p.lma, p.fileSize, `LOAD ${index} LMA`);
    }
    ensure(p.alignment <= 1 || powerOfTwo(p.alignment), `Program ${index} alignment is invalid`);
    return p;
  });
  const sections = Array.from({ length: shnum }, (_, index) => {
    const offset = shoff + index * 40;
    const s = { index, nameOffset: u32(offset), type: u32(offset + 4), flags: u32(offset + 8),
      address: u32(offset + 12), offset: u32(offset + 16), size: u32(offset + 20), link: u32(offset + 24),
      info: u32(offset + 28), alignment: u32(offset + 32), entrySize: u32(offset + 36) };
    if (s.type !== 8 && s.type !== 0) range(bytes, s.offset, s.size, `Section ${index}`);
    u32Range(s.address, s.size, `Section ${index}`);
    ensure(s.alignment <= 1 || powerOfTwo(s.alignment), `Section ${index} alignment is invalid`);
    return s;
  });
  function stringAt(table, start, label) {
    ensure(table && table.type === 3 && start < table.size, `${label}: invalid string table/offset`);
    const data = range(bytes, table.offset, table.size, label);
    const end = data.indexOf(0, start);
    ensure(end >= start && end - start <= 1024, `${label}: unterminated or oversized string`);
    return data.toString('utf8', start, end);
  }
  for (const section of sections) section.name = stringAt(sections[shstr], section.nameOffset, 'Section name');
  const symbols = [];
  for (const table of sections.filter(s => s.type === 2)) {
    ensure(table.entrySize === 16 && table.size % 16 === 0 && symbols.length + table.size / 16 <= 250000, 'Invalid/oversized symbol table');
    ensure(table.link < sections.length && sections[table.link].type === 3, 'Invalid symbol string table');
    for (let offset = table.offset; offset < table.offset + table.size; offset += 16) {
      const name = stringAt(sections[table.link], u32(offset), 'Symbol name');
      const sectionIndex = u16(offset + 14);
      ensure(sectionIndex < sections.length || (sectionIndex >= 0xff00 && sectionIndex !== 0xffff), 'Invalid/extended symbol section');
      symbols.push({ name, value: u32(offset + 4), size: u32(offset + 8), type: bytes[offset + 12] & 15,
        binding: bytes[offset + 12] >> 4, sectionIndex });
    }
  }
  ensure(symbols.length > 0, 'Unstripped ELF symbol table is required');
  return { bytes, programs, sections, symbols, entry: u32(24), flags: u32(36) };
}

function oneSymbol(elf, name) {
  const matches = elf.symbols.filter(s => s.name === name && s.sectionIndex !== 0);
  ensure(matches.length === 1, `Expected one defined symbol: ${name}`);
  return matches[0];
}
function sectionBytes(elf, symbol, expectedSection) {
  ensure(symbol.sectionIndex === expectedSection.index && symbol.type === 1 && symbol.size > 0,
    `Invalid resource symbol section/type/size: ${symbol.name}`);
  ensure(symbol.value >= expectedSection.address && symbol.size <= expectedSection.address + expectedSection.size - symbol.value,
    `Resource symbol is outside assets: ${symbol.name}`);
  return range(elf.bytes, expectedSection.offset + symbol.value - expectedSection.address, symbol.size, symbol.name);
}
function vmaBytes(elf, address, size, label) {
  u32Range(address, size, label);
  const matches = elf.programs.filter(p => p.type === 1 && p.fileSize && address >= p.vma && address + size <= p.vma + p.fileSize);
  ensure(matches.length === 1, `${label}: not in one file-backed LOAD segment`);
  ensure(matches[0].lma + address - matches[0].vma === address, `${label}: startup table must have identical Flash VMA/LMA`);
  return range(elf.bytes, matches[0].offset + address - matches[0].vma, size, label);
}
function verifyScatter(elf, assets, binaryBytes) {
  const result = [];
  for (const [base, last, words] of [
    ['__scat_copy_base', '__scat_copy_last', 3], ['__scat_zero_base', '__scat_zero_last', 2],
    ['__psram_scat_copy_base', '__psram_scat_copy_last', 3], ['__psram_scat_zero_base', '__psram_scat_zero_last', 2],
  ]) {
    const start = oneSymbol(elf, base).value, end = oneSymbol(elf, last).value;
    ensure(start % 4 === 0 && end >= start && (end - start) % (words * 4) === 0 && end - start <= 65536, `Invalid scatter table: ${base}`);
    const data = vmaBytes(elf, start, end - start, base), entries = [];
    ensure(start >= FLASH_START && end <= FLASH_END &&
      range(binaryBytes, start - FLASH_START, data.length, `Binary ${base}`).equals(data),
      `Binary startup scatter table differs from ELF: ${base}`);
    for (let offset = 0; offset < data.length; offset += words * 4) {
      const length = data.readUInt32LE(offset), destination = data.readUInt32LE(offset + 4);
      const source = words === 3 ? data.readUInt32LE(offset + 8) : null;
      u32Range(destination, length, base);
      ensure(!overlaps(destination, length, assets.address, assets.size), `Startup scatter destination overlaps assets: ${base}`);
      if (source !== null) {
        u32Range(source, length, base);
        ensure(!overlaps(source, length, assets.address, assets.size), `Startup scatter copy source overlaps assets: ${base}`);
      }
      entries.push({ bytes: length, destination: hex(destination), ...(source === null ? {} : { source: hex(source) }) });
    }
    result.push({ table: base, entries });
  }
  return result;
}

function verifyImage({ elfBytes, binaryBytes, manifest, voicePack = loadPack() }) {
  ensure(Buffer.isBuffer(binaryBytes) && binaryBytes.length > 0 && binaryBytes.length <= FLASH_END - FLASH_START,
    'Application binary must fit the 4 MiB CP Flash partition');
  ensure(manifest && manifest.schema === 1 && Number.isSafeInteger(manifest.app?.bytes) && manifest.app.bytes > 0 &&
    manifest.app.bytes <= 65536 && /^[a-f0-9]{64}$/.test(manifest.app.sha256), 'Invalid standalone overlay app manifest');
  const elf = parseElf32(elfBytes);
  ensure(elf.entry >= FLASH_START && elf.entry < FLASH_END, 'ELF entry is outside CP Flash');
  ensure(elf.programs.some(p => p.type === 1 && (p.flags & 1) && elf.entry >= p.vma && elf.entry < p.vma + p.fileSize),
    'ELF entry is not in a file-backed executable LOAD segment');
  const matches = elf.sections.filter(s => s.name === '.ricestep.assets');
  ensure(matches.length === 1, 'Expected one .ricestep.assets section');
  const assets = matches[0];
  ensure(assets.type === 1 && (assets.flags & 2) !== 0 && (assets.flags & 5) === 0, 'Assets must be allocated readonly non-code PROGBITS');
  ensure(assets.size > 0 && assets.alignment >= 4 && assets.address % 4 === 0 && assets.offset % 4 === 0,
    'Assets must have nonzero size and four-byte alignment');
  ensure(assets.address >= FLASH_START && assets.address + assets.size <= FLASH_END, 'Assets VMA is outside CP Flash');
  const load = elf.programs.filter(p => p.type === 1 && assets.address >= p.vma && assets.address + assets.size <= p.vma + p.memorySize &&
    assets.offset >= p.offset && assets.offset + assets.size <= p.offset + p.fileSize && assets.address - p.vma === assets.offset - p.offset);
  ensure(load.length === 1, 'Assets must belong to one consistent file-backed LOAD segment');
  const segment = load[0], lma = segment.lma + assets.address - segment.vma;
  ensure(lma === assets.address && lma >= FLASH_START && lma + assets.size <= FLASH_END, 'Assets LMA must equal its CP Flash VMA');
  ensure((segment.flags & 4) !== 0 && (segment.flags & 2) === 0, 'Assets LOAD segment must be readable and non-writable');
  for (const p of elf.programs.filter(p => p.type === 1 && p.index !== segment.index)) {
    ensure(!overlaps(p.lma, p.fileSize, lma, assets.size), 'Another LOAD segment aliases asset Flash bytes');
  }
  ensure(oneSymbol(elf, '__ricestep_assets_start').value === assets.address &&
    oneSymbol(elf, '__ricestep_assets_end').value === assets.address + assets.size, 'Asset linker boundary symbols disagree');
  const sectionData = range(elf.bytes, assets.offset, assets.size, 'Assets');
  ensure(range(binaryBytes, lma - FLASH_START, assets.size, 'Binary assets').equals(sectionData), 'Binary resource bytes differ from ELF');

  const expectedNames = new Set(voicePack.map(clip => `rice_voice_data_${clip.id}`));
  const actualNames = elf.symbols.filter(s => s.sectionIndex !== 0 && s.name.startsWith('rice_voice_data_'));
  ensure(actualNames.length === 14 && voicePack.length === 14 && actualNames.every(s => expectedNames.has(s.name)), 'Voice symbol inventory must contain exactly fourteen approved clips');
  const intervals = [];
  const voice = voicePack.map(clip => {
    const symbol = oneSymbol(elf, `rice_voice_data_${clip.id}`);
    ensure(symbol.binding === 0 && symbol.value % 4 === 0 && symbol.size === clip.bytes, `Invalid static voice size/alignment: ${clip.id}`);
    const data = sectionBytes(elf, symbol, assets);
    ensure(sha256(data) === clip.sha256 && data.equals(clip.data), `Voice byte/hash mismatch: ${clip.id}`);
    intervals.push({ start: symbol.value, size: symbol.size, name: symbol.name });
    return { key: clip.id, address: hex(symbol.value), bytes: symbol.size, sha256: sha256(data) };
  });
  const appSymbol = oneSymbol(elf, 'rice_miniapp_source');
  ensure(appSymbol.size === manifest.app.bytes + 1, 'Embedded Lua symbol size differs from overlay manifest');
  const app = sectionBytes(elf, appSymbol, assets);
  ensure(app[app.length - 1] === 0, 'Embedded Lua source is missing its final NUL');
  ensure(sha256(app.subarray(0, -1)) === manifest.app.sha256, 'Embedded Lua source hash differs from overlay manifest');
  intervals.push({ start: appSymbol.value, size: appSymbol.size, name: appSymbol.name });
  intervals.sort((a, b) => a.start - b.start);
  for (let i = 1; i < intervals.length; ++i) ensure(!overlaps(intervals[i - 1].start, intervals[i - 1].size, intervals[i].start, intervals[i].size), 'Native resources overlap');
  const scatter = verifyScatter(elf, assets, binaryBytes);
  return {
    schema: 1, result: 'static_checks_passed', hardware_validated: false,
    scope: 'ELF/bin resource integrity and Flash placement; not boot, audio, persistence or hardware validation',
    target: { format: 'ELF32 little-endian RISC-V EXEC', entry: hex(elf.entry), elf_flags: hex(elf.flags),
      flash_start: hex(FLASH_START), flash_end_exclusive: hex(FLASH_END) },
    elf: { bytes: elfBytes.length, sha256: sha256(elfBytes) },
    binary: { bytes: binaryBytes.length, sha256: sha256(binaryBytes), partition_bytes: FLASH_END - FLASH_START,
      free_partition_bytes: FLASH_END - FLASH_START - binaryBytes.length },
    assets: { vma: hex(assets.address), lma: hex(lma), bytes: assets.size, alignment: assets.alignment,
      load_segment: segment.index, segment_flags: segment.flags, sha256: sha256(sectionData) },
    voice: { count: voice.length, bytes: voice.reduce((sum, clip) => sum + clip.bytes, 0), clips: voice },
    app: { address: hex(appSymbol.value), bytes_excluding_nul: app.length - 1, sha256: manifest.app.sha256 },
    startup_scatter_tables: scatter,
  };
}

function readBounded(file, max, label) {
  const info = fs.statSync(file);
  ensure(info.isFile() && info.size > 0 && info.size <= max, `${label} has invalid size`);
  return fs.readFileSync(file);
}
function verifyNativeImage({ elfPath, binaryPath, manifestPath, assetRoot }) {
  const elfBytes = readBounded(elfPath, MAX_ELF_BYTES, 'ELF');
  const binaryBytes = readBounded(binaryPath, FLASH_END - FLASH_START, 'Binary');
  const manifestBytes = readBounded(manifestPath, 1024 * 1024, 'Overlay manifest');
  const result = verifyImage({ elfBytes, binaryBytes, manifest: JSON.parse(manifestBytes), voicePack: loadPack(assetRoot) });
  result.overlay_manifest_sha256 = sha256(manifestBytes);
  return result;
}
if (require.main === module) {
  const args = process.argv.slice(2), values = {};
  for (let index = 0; index < args.length; index += 2) {
    ensure(['--elf', '--bin', '--manifest', '--report', '--assets'].includes(args[index]) && args[index + 1] && !values[args[index]],
      'Usage: node scripts/verify-native-image.cjs --elf FILE --bin FILE --manifest FILE [--report FILE] [--assets DIRECTORY]');
    values[args[index]] = args[index + 1];
  }
  ensure(values['--elf'] && values['--bin'] && values['--manifest'], 'Explicit --elf, --bin and --manifest paths are required');
  const report = verifyNativeImage({ elfPath: values['--elf'], binaryPath: values['--bin'], manifestPath: values['--manifest'], assetRoot: values['--assets'] });
  const reportPath = values['--report'] || path.join(__dirname, '../dist/native/standalone/image-verification.json');
  fs.mkdirSync(path.dirname(reportPath), { recursive: true });
  fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n');
  console.log(`Static native checks passed: ${report.voice.count} exact voice clips, Lua ${report.app.bytes_excluding_nul} bytes, assets VMA=LMA ${report.assets.vma}. Hardware remains untested.`);
}

module.exports = { parseElf32, verifyImage, verifyNativeImage, FLASH_START, FLASH_END };
