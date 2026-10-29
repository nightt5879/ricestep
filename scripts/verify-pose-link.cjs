'use strict';
// Read-only executable/compile-command inspection. No device access.
const fs = require('node:fs');
const crypto = require('node:crypto');
const { parseElf32 } = require('./verify-native-image.cjs');
const REQUIRED = ['pose_engine_prepare', 'pose_engine_input', 'pose_engine_invoke',
  'pose_engine_heads', 'pose_engine_decode', 'pose_engine_release',
  'pose_engine_context_bytes', 'pose_engine_context_alignment',
  'pose_engine_arena_used_bytes', 'pose_movenet_decode', 'pi_prepare', 'rp_model_validate'];
function verify(elfBytes, commands) {
  const elf = parseElf32(elfBytes);
  const symbols = REQUIRED.map(name => {
    const matches = elf.symbols.filter(s => s.name === name && s.sectionIndex !== 0 && s.type === 2 && s.size > 0);
    if (matches.length !== 1) throw new Error(`Missing linked pose function: ${name}`);
    return { name, address: matches[0].value, bytes: matches[0].size };
  });
  const operators = ['ADD', 'SUB', 'MUL', 'CONV_2D', 'DEPTHWISE_CONV_2D', 'RESIZE_BILINEAR'].map(op => {
    const name = `Register_${op}`;
    const matches = elf.symbols.filter(s => s.name.includes(`${name.length}${name}Ev`) && s.sectionIndex !== 0 && s.type === 2 && s.size > 0);
    if (matches.length !== 1) throw new Error(`Missing linked CPU operator: ${op}`);
    return { operator: op, symbol: matches[0].name, bytes: matches[0].size };
  });
  const selected = commands.filter(c => /(?:tflm-source[\\/]|standalone[\\/]pose_components[\\/])/.test(c.file));
  if (selected.length < 42) throw new Error('Incomplete pose/TFLM compile-command evidence');
  for (const entry of selected) {
    const flags = entry.arguments || entry.command.match(/"[^"\r\n]*"|[^\s]+/g);
    const relevant = flags.filter(f => ['-ffast-math', '-fno-fast-math', '-Ofast'].includes(f));
    if (relevant.at(-1) !== '-fno-fast-math' || !flags.includes('-ffp-contract=off') ||
        !flags.includes('-mabi=ilp32') || !flags.includes('-march=rv32imac_zba_zbb_zbc_zbs')) {
      throw new Error(`Wrong Mini3 math/ABI flags: ${entry.file}`);
    }
  }
  return { scope: 'Mini3 ELF static linkage only; no runtime execution or hardware validation',
    elfSha256: crypto.createHash('sha256').update(elfBytes).digest('hex'),
    checkedCompilationUnits: selected.length, symbols, operators, cameraStarted: false, flashed: false };
}
if (require.main === module) {
  const [elf, commands, output] = process.argv.slice(2);
  if (!elf || !commands || !output) throw new Error('Usage: verify-pose-link.cjs ELF compile_commands.json report.json');
  const report = verify(fs.readFileSync(elf), JSON.parse(fs.readFileSync(commands, 'utf8')));
  fs.writeFileSync(output, JSON.stringify(report, null, 2) + '\n');
  console.log(`Linked ${report.symbols.length} pose entry points; verified ${report.checkedCompilationUnits} compilation units. Hardware untested.`);
}
module.exports = { verify };
