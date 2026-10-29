'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const cp = require('node:child_process');
const { readLock, verifyArchive, extractArchive, patchPaths, treeInventory, compareTrees, removeOwnStage, setup } = require('../scripts/setup-pose-source.cjs');
const ROOT = path.resolve(__dirname, '..');
const hash = data => crypto.createHash('sha256').update(data).digest('hex');
const ARCHIVE_BUILDER = String.raw`
import io,json,sys,tarfile,zipfile
entries=json.load(sys.stdin)
if sys.argv[1].endswith('.zip'):
 with zipfile.ZipFile(sys.argv[1],'w') as z:
  for e in entries:
   info=zipfile.ZipInfo(); info.filename=e['name']
   z.writestr(info,e.get('data','').encode())
else:
 with tarfile.open(sys.argv[1],'w:gz') as t:
  for e in entries:
   m=tarfile.TarInfo(e['name']); data=e.get('data','').encode()
   if 'link' in e: m.type=tarfile.SYMTYPE; m.linkname=e['link']; t.addfile(m)
   elif e.get('directory'): m.type=tarfile.DIRTYPE; t.addfile(m)
   else: m.size=len(data); t.addfile(m,io.BytesIO(data))
`;
function fixture(t) {
  const parent = path.join(ROOT, '.cache/pose-source-tests');
  fs.mkdirSync(parent, { recursive: true });
  const root = fs.mkdtempSync(path.join(parent, 'case-'));
  t.after(() => { assert.equal(path.dirname(path.resolve(root)), parent); fs.rmSync(root, { recursive: true, force: true }); });
  return root;
}
function archive(file, entries) {
  const result = cp.spawnSync('python', ['-c', ARCHIVE_BUILDER, file], { input: JSON.stringify(entries), encoding: 'utf8', windowsHide: true });
  assert.equal(result.status, 0, result.stderr);
}

test('lock only accepts expected official URLs, pinned hashes and safe cache names', t => {
  const original = readLock().lock, root = fixture(t), file = path.join(root, 'lock.json');
  for (const change of [
    l => { l.tflm.url = 'http://example.com/source.zip'; },
    l => { l.flatbuffers.directory = '../outside'; },
    l => { l.flatbuffers.patch = '../../payload.patch'; },
    l => { l.tflm.sha256 = 'short'; },
    l => { l.cpuOnly = false; },
  ]) {
    const candidate = structuredClone(original); change(candidate);
    fs.writeFileSync(file, JSON.stringify(candidate));
    assert.throws(() => readLock(file));
  }
});

test('archive checksum failure is detected before extraction', t => {
  const root = fixture(t), file = path.join(root, 'source.zip'), data = Buffer.from('known archive bytes');
  fs.writeFileSync(file, data);
  verifyArchive({ sha256: hash(data) }, file);
  fs.appendFileSync(file, 'changed');
  assert.throws(() => verifyArchive({ sha256: hash(data) }, file), /SHA-256 mismatch/);
  assert.throws(() => verifyArchive({ sha256: hash(data) }, path.join(root, 'missing.zip')), /Missing/);
  const link = path.join(root, 'broken.zip');
  fs.symlinkSync(path.join(root, 'nonexistent-target'), link);
  assert.throws(() => verifyArchive({ sha256: hash(data) }, link), /filesystem link/);
});

test('safe extraction keeps regular bytes and contained relative symlink semantics', t => {
  const root = fixture(t), zip = path.join(root, 'source.zip'), tar = path.join(root, 'source.tar.gz');
  archive(zip, [{ name: 'pkg-1/include/a.h', data: 'header\n' }, { name: 'pkg-1/LICENSE', data: 'license' }]);
  const zipOut = path.join(root, 'zip-out'); extractArchive(zip, 'pkg-1', zipOut);
  assert.equal(fs.readFileSync(path.join(zipOut, 'include/a.h'), 'utf8'), 'header\n');
  archive(tar, [{ name: 'pkg-1/data', data: 'bytes' }, { name: 'pkg-1/sub/link', link: '../data' }]);
  const tarOut = path.join(root, 'tar-out'); extractArchive(tar, 'pkg-1', tarOut);
  assert.equal(fs.readFileSync(path.join(tarOut, 'sub/link'), 'utf8'), 'bytes');
  assert.deepEqual(treeInventory(tarOut).find(e => e.path === 'sub/link'), { path: 'sub/link', type: 'symlink', target: '../data' });
});

test('traversal, prefix mismatch, case aliases and link escapes fail before writes', t => {
  const root = fixture(t);
  const badZips = [
    [{ name: '../outside', data: 'x' }], [{ name: 'pkg-1/../outside', data: 'x' }],
    [{ name: '/pkg-1/absolute', data: 'x' }], [{ name: 'other/file', data: 'x' }],
    [{ name: 'pkg-1/C:stream', data: 'x' }], [{ name: 'pkg-1/back\\slash', data: 'x' }],
    [{ name: 'pkg-1/trailing.', data: 'x' }], [{ name: 'pkg-1/NUL.txt', data: 'x' }],
    [{ name: 'pkg-1/Header', data: 'x' }, { name: 'pkg-1/header', data: 'y' }],
  ];
  for (const [i, entries] of badZips.entries()) {
    const file = path.join(root, `bad-${i}.zip`), out = path.join(root, `bad-out-${i}`);
    archive(file, entries);
    assert.throws(() => extractArchive(file, 'pkg-1', out), /extraction failed/, `ZIP case ${i}: ${entries[0].name}`);
    assert.equal(fs.existsSync(out), false);
  }
  const badTars = [
    [{ name: 'pkg-1/link', link: '../outside' }],
    [{ name: 'pkg-1/link', link: '/absolute' }],
    [{ name: 'pkg-1/a', link: 'b' }, { name: 'pkg-1/b', link: 'a' }],
    [{ name: 'pkg-1/a', link: 'target' }, { name: 'pkg-1/a/file', data: 'x' }, { name: 'pkg-1/target', directory: true }],
  ];
  for (const [i, entries] of badTars.entries()) {
    const file = path.join(root, `link-${i}.tar.gz`), out = path.join(root, `link-out-${i}`);
    archive(file, entries);
    assert.throws(() => extractArchive(file, 'pkg-1', out), /extraction failed/);
    assert.equal(fs.existsSync(out), false);
  }
});

test('complete inventory comparison detects changed, missing and extra sources without rewriting', t => {
  const root = fixture(t), source = path.join(root, 'source');
  fs.mkdirSync(source); fs.writeFileSync(path.join(source, 'a.h'), 'original');
  const expected = treeInventory(source);
  compareTrees(expected, treeInventory(source), 'fixture');
  fs.writeFileSync(path.join(source, 'a.h'), 'changed');
  assert.throws(() => compareTrees(expected, treeInventory(source), 'fixture'), /not overwritten/);
  assert.equal(fs.readFileSync(path.join(source, 'a.h'), 'utf8'), 'changed');
  fs.unlinkSync(path.join(source, 'a.h'));
  assert.throws(() => compareTrees(expected, treeInventory(source), 'fixture'), /differs/);
  fs.writeFileSync(path.join(source, 'a.h'), 'original'); fs.writeFileSync(path.join(source, 'extra.cc'), 'extra');
  assert.throws(() => compareTrees(expected, treeInventory(source), 'fixture'), /extra.cc/);
});

test('check-only with missing archives performs no subprocess/network calls', t => {
  const cache = fixture(t), original = cp.spawnSync;
  let calls = 0;
  cp.spawnSync = () => { calls++; throw new Error('Unexpected subprocess/network access'); };
  try { assert.throws(() => setup({ checkOnly: true, cache }), /Missing archive in --check-only/); }
  finally { cp.spawnSync = original; }
  assert.equal(calls, 0);
  assert.deepEqual(fs.readdirSync(cache), []);
});

test('patch targets and recursive cleanup stay inside their declared directories', t => {
  const root = fixture(t);
  const paths = ['base', 'default_allocator', 'flatbuffer_builder', 'flexbuffers', 'util'];
  const patch = paths.map(p => `diff --git a/include/flatbuffers/${p}.h b/include/flatbuffers/${p}.h`).join('\n');
  assert.equal(patchPaths(patch).length, 5);
  assert.throws(() => patchPaths(patch.replace('a/include/flatbuffers/base.h', 'a/../../escape')));
  assert.throws(() => removeOwnStage(root, path.dirname(root)), /Refusing cleanup/);
  assert.ok(fs.existsSync(root));
});
