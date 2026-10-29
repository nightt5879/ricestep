'use strict';

// Pinned, CPU-only source preparation. No SDK build, board access or firmware write.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const cp = require('node:child_process');
const ROOT = path.resolve(__dirname, '..');
const CACHE = path.join(ROOT, '.cache');
const LOCK_PATH = path.join(ROOT, 'pose-runtime.lock.json');
const INVENTORY_PATH = path.join(CACHE, 'pose-source-inventory.json');
const HELPER = path.join(__dirname, 'extract-pose-source.py');
const sha256 = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const error = message => { throw new Error(message); };
const safeName = name => typeof name === 'string' && /^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(name) && !name.includes('..');
function inside(root, candidate) {
  const r = path.relative(root, candidate);
  return r !== '' && r !== '..' && !r.startsWith('..' + path.sep) && !path.isAbsolute(r);
}
function assertNoAncestorLinks(target) {
  let current = path.resolve(target);
  while (true) {
    let info;
    try { info = fs.lstatSync(current); } catch (failure) { if (failure.code !== 'ENOENT') throw failure; }
    if (info?.isSymbolicLink()) error(`Refusing filesystem link: ${current}`);
    const parent = path.dirname(current);
    if (parent === current) break;
    current = parent;
  }
}
function readLock(lockPath = LOCK_PATH) {
  const bytes = fs.readFileSync(lockPath), lock = JSON.parse(bytes);
  if (lock.schemaVersion !== 1 || lock.cpuOnly !== true || !lock.tflm || !lock.flatbuffers) error('Unsupported CPU pose source lock.');
  const expected = {
    tflm: ['tflm-source', 'tflm-fed64.zip', 'tflite-micro-' + lock.tflm.commit,
      'https://codeload.github.com/tensorflow/tflite-micro/zip/' + lock.tflm.commit],
    flatbuffers: ['tflm-flatbuffers', 'flatbuffers-' + lock.flatbuffers.version + '.tar.gz', 'flatbuffers-' + lock.flatbuffers.version,
      'https://codeload.github.com/google/flatbuffers/tar.gz/refs/tags/v' + lock.flatbuffers.version],
  };
  if (!/^[a-f0-9]{40}$/.test(lock.tflm.commit) || !/^\d+\.\d+\.\d+$/.test(lock.flatbuffers.version) ||
      lock.flatbuffers.patch !== 'tensorflow/lite/micro/tools/make/flatbuffers.patch') error('Invalid pinned source version/patch.');
  for (const id of ['tflm', 'flatbuffers']) {
    const pkg = lock[id], values = expected[id];
    if (![pkg.directory, pkg.archive, pkg.prefix].every(safeName) || !/^[a-f0-9]{64}$/.test(pkg.sha256) ||
        pkg.directory !== values[0] || pkg.archive !== values[1] || pkg.prefix !== values[2] || pkg.url !== values[3]) {
      error(`Invalid official archive metadata: ${id}`);
    }
  }
  return { lock, lockSha256: sha256(bytes) };
}
function verifyArchive(pkg, file) {
  assertNoAncestorLinks(file);
  if (!fs.existsSync(file) || !fs.lstatSync(file).isFile()) error(`Missing source archive: ${file}`);
  const info = fs.statSync(file);
  if (info.size <= 0 || info.size > 128 * 1024 * 1024 || sha256(fs.readFileSync(file)) !== pkg.sha256) {
    error(`Archive SHA-256 mismatch: ${file}. Nothing was extracted.`);
  }
}
function obtainArchive(pkg, file, checkOnly) {
  assertNoAncestorLinks(file);
  if (fs.existsSync(file)) { verifyArchive(pkg, file); return; }
  if (checkOnly) error(`Missing archive in --check-only mode: ${file}`);
  const partial = file + '.partial';
  assertNoAncestorLinks(partial);
  if (fs.existsSync(partial)) error(`Partial source download exists; preserve/remove it explicitly before retry: ${partial}`);
  process.stdout.write(`Downloading pinned official ${pkg.archive}\n`);
  const curl = process.platform === 'win32' ? 'curl.exe' : 'curl';
  const run = cp.spawnSync(curl, ['--fail', '--location', '--http1.1', '--proto', '=https', '--proto-redir', '=https',
    '--max-redirs', '5', '--connect-timeout', '15', '--max-time', '60', '--output', partial, pkg.url], { stdio: 'inherit', windowsHide: true });
  if (run.error || run.status !== 0) error(`Source download failed; partial retained: ${partial}`);
  verifyArchive(pkg, partial);
  fs.renameSync(partial, file);
}
function extractArchive(archive, prefix, destination, python = 'python') {
  const run = cp.spawnSync(python, [HELPER, archive, prefix, destination], { encoding: 'utf8', windowsHide: true, maxBuffer: 2 * 1024 * 1024 });
  if (run.error || run.status !== 0) error(`Safe source extraction failed: ${run.error?.message || run.stderr || run.stdout}`);
}
function patchPaths(patchText) {
  const result = [];
  for (const line of patchText.replace(/\r\n/g, '\n').split('\n')) {
    if (!line.startsWith('diff --git ')) continue;
    const match = /^diff --git a\/([^\s]+) b\/([^\s]+)$/.exec(line);
    if (!match || match[1] !== match[2] || match[1].includes('\\') || match[1].includes(':') ||
        match[1].split('/').some(p => !p || p === '.' || p === '..') || !match[1].startsWith('include/flatbuffers/')) {
      error('Unsupported path in pinned FlatBuffers patch.');
    }
    if (result.includes(match[1])) error('Duplicate file in FlatBuffers patch.');
    result.push(match[1]);
  }
  if (result.length !== 5) error('Expected five upstream FlatBuffers header patches.');
  return result;
}
function applyPinnedPatch(flatDirectory, patchFile, repository = ROOT) {
  const relative = path.relative(repository, flatDirectory).replace(/\\/g, '/');
  if (!inside(repository, flatDirectory)) error('Patch target must remain inside the repository staging directory.');
  assertNoAncestorLinks(flatDirectory);
  const patch = fs.readFileSync(patchFile);
  const targets = patchPaths(patch.toString('utf8'));
  const before = new Map(targets.map(name => [name, sha256(fs.readFileSync(path.join(flatDirectory, name)))]));
  const common = ['-C', repository, '-c', 'core.autocrlf=false', 'apply', '--whitespace=nowarn', `--directory=${relative}`];
  for (const check of [true, false]) {
    const run = cp.spawnSync('git', [...common, ...(check ? ['--check'] : []), path.resolve(patchFile)], { encoding: 'utf8', windowsHide: true });
    if (run.error || run.status !== 0) error(`Pinned FlatBuffers patch failed: ${run.error?.message || run.stderr || run.stdout}`);
  }
  // git apply from an inherited repository subdirectory can silently skip paths;
  // require each pinned target to have actually changed, using explicit --directory.
  for (const name of targets) if (before.get(name) === sha256(fs.readFileSync(path.join(flatDirectory, name)))) {
    error(`Patch was silently skipped: ${name}`);
  }
  return { sha256: sha256(patch), files: targets.sort() };
}
function treeInventory(root) {
  assertNoAncestorLinks(root);
  if (!fs.existsSync(root) || !fs.lstatSync(root).isDirectory()) error(`Missing source directory: ${root}`);
  const records = [];
  function walk(directory, prefix) {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true }).sort((a, b) => a.name < b.name ? -1 : 1)) {
      const full = path.join(directory, entry.name), relative = prefix + entry.name;
      const stat = fs.lstatSync(full);
      if (stat.isSymbolicLink()) {
        const target = fs.readlinkSync(full).replace(/\\/g, '/');
        if (path.isAbsolute(target) || target.includes(':') || !inside(root, path.resolve(directory, target))) error(`Source symlink escapes root: ${relative}`);
        records.push({ path: relative, type: 'symlink', target });
      } else if (stat.isDirectory()) {
        records.push({ path: relative, type: 'directory' });
        walk(full, relative + '/');
      } else if (stat.isFile()) records.push({ path: relative, type: 'file', bytes: stat.size, sha256: sha256(fs.readFileSync(full)) });
      else error(`Unsupported installed source entry: ${relative}`);
    }
  }
  walk(root, '');
  return records;
}
function compareTrees(expected, actual, label) {
  const want = new Map(expected.map(e => [e.path, e])), got = new Map(actual.map(e => [e.path, e]));
  const differences = [];
  for (const [name, record] of want) if (JSON.stringify(record) !== JSON.stringify(got.get(name))) differences.push(name);
  for (const name of got.keys()) if (!want.has(name)) differences.push(name);
  if (differences.length) error(`${label} differs from pinned extracted/patched sources (${differences.length} entries): ${differences.slice(0, 8).join(', ')}. Existing files were not overwritten.`);
}
function removeOwnStage(stage, cache) {
  const resolved = path.resolve(stage), root = path.resolve(cache);
  if (path.dirname(resolved) !== root || !path.basename(resolved).startsWith('.pose-source-stage-')) error('Refusing cleanup outside the private source staging directory.');
  if (fs.lstatSync(resolved).isSymbolicLink()) error('Refusing to remove a replaced staging link.');
  fs.rmSync(resolved, { recursive: true, force: true });
}
function setup({ checkOnly = false, cache = CACHE, lockPath = LOCK_PATH, python = 'python' } = {}) {
  cache = path.resolve(cache);
  if (!inside(ROOT, cache)) error('Pose source cache must stay in the repository.');
  assertNoAncestorLinks(cache);
  if (!fs.existsSync(cache)) {
    if (checkOnly) error('Missing source cache in --check-only mode.');
    fs.mkdirSync(cache, { recursive: true });
  }
  const { lock, lockSha256 } = readLock(lockPath);
  const ids = ['tflm', 'flatbuffers'];
  // Both archives are verified before any source extraction.
  for (const id of ids) obtainArchive(lock[id], path.join(cache, lock[id].archive), checkOnly);
  const stage = fs.mkdtempSync(path.join(cache, '.pose-source-stage-'));
  try {
    for (const id of ids) extractArchive(path.join(cache, lock[id].archive), lock[id].prefix, path.join(stage, id), python);
    const patch = applyPinnedPatch(path.join(stage, 'flatbuffers'), path.join(stage, 'tflm', lock.flatbuffers.patch));
    const inventory = { schemaVersion: 1, lockSha256, cpuOnly: true, packages: {} };
    for (const id of ids) {
      const expected = treeInventory(path.join(stage, id));
      inventory.packages[id] = { archiveSha256: lock[id].sha256, directory: lock[id].directory,
        ...(id === 'flatbuffers' ? { patch } : {}), entries: expected };
      const installed = path.join(cache, lock[id].directory);
      assertNoAncestorLinks(installed);
      if (fs.existsSync(installed)) compareTrees(expected, treeInventory(installed), id);
      else if (checkOnly) error(`Missing installed source directory: ${installed}`);
    }
    const inventoryPath = path.join(cache, path.basename(INVENTORY_PATH));
    assertNoAncestorLinks(inventoryPath);
    if (fs.existsSync(inventoryPath)) {
      if (JSON.stringify(JSON.parse(fs.readFileSync(inventoryPath))) !== JSON.stringify(inventory)) {
        error('Existing pose source inventory differs from fresh archive/patch reconstruction; not overwritten.');
      }
    } else if (checkOnly) error('Source inventory missing; run setup-pose-source.cjs once to record the verified source inventory.');
    // Only now install missing trees; never overwrite a differing existing tree.
    if (!checkOnly) {
      for (const id of ids) {
        const installed = path.join(cache, lock[id].directory);
        if (!fs.existsSync(installed)) fs.renameSync(path.join(stage, id), installed);
      }
      if (!fs.existsSync(inventoryPath)) fs.writeFileSync(inventoryPath, JSON.stringify(inventory, null, 2) + '\n', { flag: 'wx' });
    }
    return inventory;
  } finally { removeOwnStage(stage, cache); }
}

if (require.main === module) {
  try {
    const args = process.argv.slice(2);
    if (args.length > 1 || (args.length === 1 && args[0] !== '--check-only')) error('Usage: node scripts/setup-pose-source.cjs [--check-only]');
    const result = setup({ checkOnly: args[0] === '--check-only' });
    const count = Object.values(result.packages).reduce((total, pkg) => total + pkg.entries.filter(e => e.type === 'file').length, 0);
    process.stdout.write(`Verified CPU pose sources: ${count} regular files plus directories/contained links; no build or hardware access.\n`);
  } catch (failure) { process.stderr.write(`${failure.message}\n`); process.exitCode = 1; }
}
module.exports = { readLock, verifyArchive, extractArchive, patchPaths, applyPinnedPatch, treeInventory, compareTrees, removeOwnStage, setup };
