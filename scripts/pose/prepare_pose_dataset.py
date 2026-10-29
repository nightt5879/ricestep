"""Bounded COCO2017 keypoint data; cache only, no training or uploads.

Python3.10+, requests and Pillow (already available in the development Anaconda).
Downloads only the two keypoint ZIP members via verified HTTPS byte ranges,
Defaults:64 train/16 val person images,16/4 unverified background candidates,
100MiB image limit. Explicit limits allow2048/256 positives and600MiB, at most
four HTTPS workers. Completed images have verified SHA256 receipts for resume.
Use --annotation-cache to reuse only pinned annotation JSON without downloading
or copying the original archive. No global install.
The S3 HTTPS endpoint is the same public images.cocodataset.org bucket advertised
by https://cocodataset.org/dataset/download.htm. Individual image licenses remain
those recorded by COCO/Flickr; annotation terms are separate (CC BY4.0).
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import struct
import threading
import time
import zlib

import requests

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = 'https://s3.amazonaws.com/images.cocodataset.org/annotations/annotations_trainval2017.zip'
ARCHIVE_BYTES = 252907541
ARCHIVE_ETAG = 'f4bbac642086de4f52a3fdda2de5fa2c'
ANNOTATION_SHA256 = {
    'train': '7fc1549d934547c470384d8a207c38707ca33fc016d00e33b795e408603af83e',
    'val': '788e2dae83c86bd547be7fab269d6399df5671063d29a61360cdb2cc370d2b14',
}
MIB = 1024 * 1024
LICENSES = {4, 5, 7, 8}
MEMBERS = {f'annotations/person_keypoints_{split}2017.json' for split in ('train', 'val')}
JOINTS = ['nose', 'left_eye', 'right_eye', 'left_ear', 'right_ear', 'left_shoulder',
          'right_shoulder', 'left_elbow', 'right_elbow', 'left_wrist', 'right_wrist',
          'left_hip', 'right_hip', 'left_knee', 'right_knee', 'left_ankle', 'right_ankle']


def sha_file(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(MIB), b''):
            h.update(block)
    return h.hexdigest()


def save_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    temporary.replace(path)


def validate_range(status, headers, start, end, total, etag):
    if status != 206 or headers.get('Content-Range') != f'bytes {start}-{end}/{total}':
        raise ValueError('Server did not honor the exact requested byte range')
    if int(headers.get('Content-Length', '-1')) != end - start + 1:
        raise ValueError('Range Content-Length mismatch')
    if headers.get('Content-Encoding', 'identity') != 'identity':
        raise ValueError('Encoded range responses are unsupported')
    if headers.get('ETag', '').strip('"') != etag:
        raise ValueError('Archive object changed')


class RangeArchive:
    def __init__(self, session):
        self.session = session
        with session.head(ARCHIVE, timeout=(10, 45), allow_redirects=False) as response:
            response.raise_for_status()
            if (response.status_code != 200 or
                    int(response.headers.get('Content-Length', '-1')) != ARCHIVE_BYTES or
                    response.headers.get('ETag', '').strip('"') != ARCHIVE_ETAG):
                raise ValueError('Pinned COCO archive size/ETag changed')

    def stream(self, start, end):
        if not 0 <= start <= end < ARCHIVE_BYTES:
            raise ValueError('Invalid archive range')
        with self.session.get(ARCHIVE, headers={'Range': f'bytes={start}-{end}',
                              'If-Match': '"' + ARCHIVE_ETAG + '"', 'Accept-Encoding': 'identity'},
                              timeout=(10, 60), stream=True, allow_redirects=False) as response:
            validate_range(response.status_code, response.headers, start, end, ARCHIVE_BYTES, ARCHIVE_ETAG)
            total = 0
            for block in response.iter_content(MIB):
                total += len(block)
                if total > end - start + 1:
                    raise ValueError('Oversized range body')
                yield block
            if total != end - start + 1:
                raise ValueError('Truncated range body')

    def read(self, start, size):
        if not 0 < size <= MIB:
            raise ValueError('Metadata range exceeds1MiB')
        for attempt in range(3):
            try:
                return b''.join(self.stream(start, start + size - 1))
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(attempt + 1)

    def download(self, start, size, target):
        if not 0 < size <= 80 * MIB:
            raise ValueError('Compressed member size exceeds80MiB')
        partial = target.with_suffix(target.suffix + '.part')
        if target.exists():
            if target.stat().st_size != size:
                raise ValueError('Cached compressed member size mismatch')
            return
        for attempt in range(4):
            have = partial.stat().st_size if partial.exists() else 0
            if have > size:
                raise ValueError('Oversized partial member')
            if have == size:
                partial.replace(target)
                return
            print(f'Range download {target.name}: {have}/{size} bytes', flush=True)
            try:
                with partial.open('ab') as stream:
                    for block in self.stream(start + have, start + size - 1):
                        stream.write(block)
                partial.replace(target)
                return
            except requests.RequestException:
                if attempt == 3:
                    raise
                time.sleep(attempt + 1)
        raise RuntimeError('Member download exhausted retries')


def parse_directory(data, count, archive_bytes):
    if not 0 < count <= 64 or len(data) > MIB:
        raise ValueError('Unbounded central directory')
    members = {}
    offset = 0
    for _ in range(count):
        if offset + 46 > len(data):
            raise ValueError('Truncated central directory')
        t = struct.unpack_from('<4s6H3L5H2L', data, offset)
        name_size, extra_size, comment_size = t[10:13]
        end = offset + 46 + name_size + extra_size + comment_size
        if t[0] != b'PK\x01\x02' or end > len(data) or t[13] != 0:
            raise ValueError('Invalid or multi-disk ZIP directory')
        name = data[offset+46:offset+46+name_size].decode('utf-8' if t[3] & 2048 else 'ascii')
        if (not name or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts or
                '\\' in name or ':' in name or name in members):
            raise ValueError('Unsafe or duplicate archive member name')
        if t[3] & ~2048 or t[4] not in (0, 8) or max(t[8], t[9], t[16]) == 0xffffffff:
            raise ValueError('Unsupported ZIP flags, compression or ZIP64')
        if t[16] + 30 + name_size + t[8] > archive_bytes:
            raise ValueError('ZIP member exceeds archive')
        members[name] = dict(name=name, flags=t[3], compression=t[4], crc32=t[7],
                             compressed_bytes=t[8], bytes=t[9], local_offset=t[16])
        offset = end
    if offset != len(data):
        raise ValueError('Central directory trailing bytes')
    return members


def directory(archive):
    tail = archive.read(ARCHIVE_BYTES - 65557, 65557)
    offset = tail.rfind(b'PK\x05\x06')
    if offset < 0 or offset + 22 > len(tail):
        raise ValueError('ZIP end record missing')
    end = struct.unpack_from('<4s4H2LH', tail, offset)
    if end[1] or end[2] or end[3] != end[4] or offset + 22 + end[7] != len(tail):
        raise ValueError('Invalid ZIP end record')
    if end[6] + end[5] > ARCHIVE_BYTES - 22:
        raise ValueError('Central directory outside archive')
    return parse_directory(archive.read(end[6], end[5]), end[4], ARCHIVE_BYTES)


def local_data_offset(archive, member):
    header = archive.read(member['local_offset'], 30)
    t = struct.unpack('<4s5H3L2H', header)
    if (t[0] != b'PK\x03\x04' or t[2] != member['flags'] or t[3] != member['compression'] or
            t[6] != member['crc32'] or t[7] != member['compressed_bytes'] or t[8] != member['bytes']):
        raise ValueError('Local ZIP header disagrees with directory')
    name = archive.read(member['local_offset'] + 30, t[9]).decode('utf-8' if t[2] & 2048 else 'ascii')
    start = member['local_offset'] + 30 + t[9] + t[10]
    if name != member['name'] or start + member['compressed_bytes'] > ARCHIVE_BYTES:
        raise ValueError('Invalid local ZIP member bounds/name')
    return start


def verify_annotation(path, member):
    if not path.exists() or path.stat().st_size != member['bytes']:
        return False
    crc = 0
    with path.open('rb') as source:
        for block in iter(lambda: source.read(MIB), b''):
            crc = zlib.crc32(block, crc)
    return crc == member['crc32']


def inflate(compressed, target, member):
    if not 0 < member['bytes'] <= 256 * MIB:
        raise ValueError('Annotation expansion exceeds256MiB')
    temporary = target.with_suffix('.json.part')
    decoder = zlib.decompressobj(-15) if member['compression'] == 8 else None
    count = crc = 0
    try:
        with compressed.open('rb') as source, temporary.open('wb') as output:
            for block in iter(lambda: source.read(MIB), b''):
                data = decoder.decompress(block, member['bytes'] - count + 1) if decoder else block
                count += len(data)
                if count > member['bytes'] or (decoder and decoder.unconsumed_tail):
                    raise ValueError('Annotation expansion exceeded declared size')
                crc = zlib.crc32(data, crc)
                output.write(data)
        if (count != member['bytes'] or crc != member['crc32'] or
                (decoder and (not decoder.eof or decoder.unused_data))):
            raise ValueError('Annotation CRC, length or deflate stream mismatch')
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()


def valid_person(person, width, height):
    if person.get('category_id') != 1 or person.get('iscrowd') != 0:
        return False
    bbox, points = person.get('bbox', []), person.get('keypoints', [])
    if len(bbox) != 4 or len(points) != 51:
        return False
    if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in bbox + points):
        return False
    x, y, w, h = bbox
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width + .001 or y + h > height + .001:
        return False
    labeled = 0
    for i in range(17):
        px, py, visibility = points[3*i:3*i+3]
        if visibility not in (0, 1, 2):
            return False
        if visibility:
            if not 0 <= px <= width or not 0 <= py <= height:
                return False
            labeled += 1
    if labeled < 10 or labeled != person.get('num_keypoints'):
        return False
    return any(all(points[3*i+2] for i in side) for side in ((5, 11, 13, 15), (6, 12, 14, 16)))


def select_images(data, split, count):
    category = next((c for c in data['categories'] if c['id'] == 1), None)
    if not category or category['keypoints'] != JOINTS:
        raise ValueError('COCO17 annotation contract changed')
    licenses = {entry['id']: entry for entry in data['licenses']}
    if not LICENSES <= licenses.keys():
        raise ValueError('Requested license IDs missing from source registry')
    by_image = defaultdict(list)
    for person in data['annotations']:
        if person.get('category_id') == 1:
            by_image[person['image_id']].append(person)
    eligible = []; rejected = Counter(); all_ids = set()
    for image in data['images']:
        ident = image['id']; all_ids.add(ident)
        if image.get('license') not in LICENSES:
            rejected['license'] += 1; continue
        if (not isinstance(ident, int) or image.get('file_name') != f'{ident:012d}.jpg' or
                not 1 <= image.get('width', 0) <= 4096 or not 1 <= image.get('height', 0) <= 4096):
            rejected['image_metadata'] += 1; continue
        people = by_image[ident]
        qualifying = [p for p in people if valid_person(p, image['width'], image['height'])]
        if not qualifying:
            rejected['person_labels'] += 1; continue
        # Explicit single target per image; preserve all other source people.
        person = min(qualifying, key=lambda p: (-p['bbox'][2] * p['bbox'][3], p['id']))
        eligible.append({'image_id': ident, 'annotation_id': person['id'], 'split': split,
                         'width': image['width'], 'height': image['height'],
                         'file_name': image['file_name'], 'coco_url': image.get('coco_url'),
                         'flickr_url': image.get('flickr_url'), 'license': licenses[image['license']],
                         'bbox_xywh': person['bbox'], 'keypoints_xyv': person['keypoints'],
                         'num_keypoints': person['num_keypoints'], 'iscrowd': 0,
                         'all_person_annotations': sorted(people, key=lambda p: p['id']),
                         'qualifying_annotation_ids': sorted(p['id'] for p in qualifying)})
    eligible.sort(key=lambda row: hashlib.sha256(f'ricestep-coco-smoke-v1:{split}:{row["image_id"]}'.encode()).digest())
    if len(eligible) < count:
        raise ValueError(f'Only{len(eligible)} eligible {split} images, requested{count}')
    return eligible[:count], dict(eligible_images=len(eligible), rejected=dict(rejected)), all_ids


def select_background_candidates(data, split, count):
    # Zero records of any kind, NOT merely zero visible/labeled keypoints.
    # This is a candidate selector, never an assertion that no unlabeled human
    # is visible or that keypoint annotations cover every instance annotation.
    with_person = {p['image_id'] for p in data['annotations'] if p.get('category_id') == 1}
    licenses = {entry['id']: entry for entry in data['licenses']}
    rows = []
    for image in data['images']:
        ident = image['id']
        if (ident in with_person or image.get('license') not in LICENSES or
                not isinstance(ident, int) or image.get('file_name') != f'{ident:012d}.jpg' or
                not 1 <= image.get('width', 0) <= 4096 or not 1 <= image.get('height', 0) <= 4096):
            continue
        rows.append({'image_id': ident, 'split': split, 'width': image['width'], 'height': image['height'],
                     'file_name': image['file_name'], 'coco_url': image.get('coco_url'),
                     'flickr_url': image.get('flickr_url'), 'license': licenses[image['license']],
                     'all_person_annotations': [], 'negative_verified': False,
                     'label_kind': 'unverified_background_candidate',
                     'negative_caveat': 'Zero person records in keypoint JSON; not independently verified against instance annotations or visible unlabeled people. Exclude from training until reviewed.'})
    rows.sort(key=lambda row: hashlib.sha256(f'ricestep-coco-background-v1:{split}:{row["image_id"]}'.encode()).digest())
    if len(rows) < count:
        raise ValueError('Not enough licensed background candidates')
    return rows[:count]


def ensure_disjoint_splits(train_ids, val_ids):
    if set(train_ids) & set(val_ids):
        raise ValueError('Official train/val image IDs overlap')


def verify_image(path, row):
    from PIL import Image
    with Image.open(path) as image:
        if image.format != 'JPEG' or image.size != (row['width'], row['height']):
            raise ValueError('Image format/dimensions disagree with annotation')
        image.verify()
    # JPEG verify() alone does not decode its entire scan. Reopen and fully
    # decode only after the bounded, annotation-matched dimensions are checked.
    with Image.open(path) as image:
        image.load()


class ImageBudget:
    """Bound committed/reserved payload; abort when delivered body bytes exceed limit.

    Bytes already yielded by a streaming response must still be counted on
    failure. Concurrent requests may each deliver one final chunk before the
    abort propagates; this is not a hard transport-byte cap (HTTP/TLS buffering
    is outside this accounting). Retry bytes never disappear from the total.
    """
    def __init__(self, limit):
        if not 0 < limit <= 600 * MIB:
            raise ValueError('Image budget must be positive and at most600MiB')
        self.limit = limit
        self.used = self.reserved = self.network = 0
        self.lock = threading.Lock()

    @contextmanager
    def reservation(self, size):
        with self.lock:
            # Already in-flight streams may deliver one more chunk, but a
            # queued/retried download must not start another body afterwards.
            if self.network > self.limit:
                raise ValueError('Image network-byte budget exceeded, including retries')
            if not 0 < size <= 8 * MIB or self.used + self.reserved + size > self.limit:
                raise ValueError('Image payload budget exceeded')
            self.reserved += size
        try:
            yield
        except BaseException:
            with self.lock:
                self.reserved -= size
            raise
        else:
            with self.lock:
                self.reserved -= size
                self.used += size

    def received(self, size):
        with self.lock:
            if size < 0:
                raise ValueError('Received byte count cannot be negative')
            self.network += size
            if self.network > self.limit:
                raise ValueError('Image network-byte budget exceeded, including retries')


def fetch_image(session, row, path, budget, expected_sha=None):
    url = f'https://s3.amazonaws.com/images.cocodataset.org/{row["split"]}2017/{row["file_name"]}'
    receipt_path = path.with_suffix('.jpg.source.json')
    identity = {'image_id': row['image_id'], 'width': row['width'], 'height': row['height'], 'download_url': url}
    expected_bytes = None
    if expected_sha is not None and (not isinstance(expected_sha, str) or
                                     not re.fullmatch('[0-9a-f]{64}', expected_sha)):
        raise ValueError('Previous manifest image SHA256 must be64 lowercase hex digits')
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        if not isinstance(receipt, dict):
            raise ValueError('Cached image receipt must be an object')
        if any(receipt.get(k) != v for k, v in identity.items()):
            raise ValueError('Cached image receipt identity mismatch')
        receipt_sha = receipt.get('image_sha256')
        if not isinstance(receipt_sha, str) or not re.fullmatch('[0-9a-f]{64}', receipt_sha):
            raise ValueError('Cached image receipt SHA256 must be64 lowercase hex digits')
        if 'image_bytes' in receipt:
            expected_bytes = receipt['image_bytes']
            if type(expected_bytes) is not int or not 0 < expected_bytes <= 8*MIB:
                raise ValueError('Cached image receipt byte count is invalid')
        if expected_sha and receipt['image_sha256'] != expected_sha:
            raise ValueError('Cached image receipt disagrees with previous manifest')
        expected_sha = receipt_sha
    if path.exists() and expected_sha:
        if expected_bytes is not None and path.stat().st_size != expected_bytes:
            raise ValueError('Cached image receipt byte count mismatch')
        if sha_file(path) != expected_sha:
            raise ValueError('Cached image SHA256 mismatch; refusing unverified reuse')
        verify_image(path, row)
        with budget.reservation(path.stat().st_size):
            result = identity | {'image_sha256': expected_sha, 'image_bytes': path.stat().st_size, 'image_downloaded': True}
            if not receipt_path.exists():
                save_json(receipt_path, result)
        return result
    temporary = path.with_suffix('.jpg.part')
    try:
        for attempt in range(3):
            try:
                with session.get(url, stream=True, timeout=(10, 45), allow_redirects=False,
                                 headers={'Accept-Encoding': 'identity'}) as response:
                    response.raise_for_status()
                    declared = int(response.headers.get('Content-Length', '-1'))
                    if (response.status_code != 200 or not 0 < declared <= 8 * MIB or
                            response.headers.get('Content-Encoding', 'identity') != 'identity'):
                        raise ValueError('Image response exceeds per-file/total budget')
                    if expected_bytes is not None and declared != expected_bytes:
                        raise ValueError('Downloaded image byte count differs from pinned receipt')
                    with budget.reservation(declared):
                        received = 0
                        with temporary.open('wb') as output:
                            for block in response.iter_content(65536):
                                budget.received(len(block))
                                received += len(block)
                                if received > declared:
                                    raise ValueError('Image body exceeded declared size')
                                output.write(block)
                        if received != declared:
                            raise ValueError('Truncated image')
                        verify_image(temporary, row)
                        digest = sha_file(temporary)
                        if expected_sha and digest != expected_sha:
                            raise ValueError('Downloaded image changed from its pinned receipt')
                        result = identity | {'image_sha256': digest, 'image_bytes': received, 'image_downloaded': True}
                        temporary.replace(path)
                        save_json(receipt_path, result)
                    return result
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(attempt + 1)
    finally:
        if temporary.exists():
            temporary.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'.cache/pose-dataset-coco2017')
    parser.add_argument('--metadata-only', action='store_true')
    parser.add_argument('--annotation-cache', type=Path, help='Existing metadata directory; verify pinned JSON SHA256 before reusing read-only')
    parser.add_argument('--train-count', type=int, default=64)
    parser.add_argument('--val-count', type=int, default=16)
    parser.add_argument('--background-train-count', type=int, default=16)
    parser.add_argument('--background-val-count', type=int, default=4)
    parser.add_argument('--max-image-bytes', type=int, default=100 * MIB)
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    out = args.output.resolve(); cache = (ROOT/'.cache').resolve()
    if out == cache or not out.is_relative_to(cache):
        parser.error('Output must be a child of this repository .cache')
    if not 0 < args.train_count <= 2048 or not 0 < args.val_count <= 256:
        parser.error('Permitted positive counts:1..2048 train and1..256 val')
    if not 0 <= args.background_train_count <= 16 or not 0 <= args.background_val_count <= 4:
        parser.error('At most16 train/4 val unverified background candidates')
    if not 1 <= args.workers <= 4 or not 0 < args.max_image_bytes <= 600 * MIB:
        parser.error('Use1..4 workers and an image budget up to600MiB')
    annotation_cache = args.annotation_cache.resolve() if args.annotation_cache else None
    if annotation_cache and (not annotation_cache.is_dir() or not annotation_cache.is_relative_to(cache)):
        parser.error('--annotation-cache must be an existing directory inside repository .cache')
    out.mkdir(parents=True, exist_ok=True); metadata = out/'metadata'; metadata.mkdir(exist_ok=True)
    archive = None; members = {}
    if not annotation_cache:
        archive = RangeArchive(requests.Session()); members = directory(archive)
        if not MEMBERS <= members.keys():
            raise ValueError('Keypoint members missing')
    previous_hashes = {}
    if (out/'manifest.json').exists():
        previous = json.loads((out/'manifest.json').read_text(encoding='utf-8'))
        for key in ('train', 'val', 'background_train_candidates', 'background_val_candidates'):
            for row in previous.get(key, []):
                if row.get('image_sha256'):
                    previous_hashes[row['image_path']] = row['image_sha256']
    source = {'official_download_page': 'https://cocodataset.org/dataset/download.htm',
              'archive_url': ARCHIVE, 'archive_bytes': ARCHIVE_BYTES, 'etag': ARCHIVE_ETAG,
              'archive_sha256': None, 'archive_sha256_note': 'Whole ZIP intentionally not downloaded; members verified by size/CRC and SHA256.',
              'annotation_terms': 'https://cocodataset.org/dataset/termsofuse.htm',
              'annotation_license': 'CC BY4.0', 'image_license_ids': sorted(LICENSES), 'members': {}}
    selected = {}; backgrounds = {}; summary = {}; ids = {}
    for split, count in (('train', args.train_count), ('val', args.val_count)):
        target = (annotation_cache or metadata)/f'person_keypoints_{split}2017.json'
        compressed = None
        if annotation_cache:
            member = {'name': f'annotations/person_keypoints_{split}2017.json',
                      'bytes': target.stat().st_size, 'reused_json_path': target.relative_to(ROOT).as_posix(),
                      'verification': 'Pinned decoded JSON SHA256; no archive/network access for metadata reuse'}
        else:
            member = members[f'annotations/person_keypoints_{split}2017.json']
            compressed = metadata/f'person_keypoints_{split}2017.deflate'
            if not verify_annotation(target, member):
                start = local_data_offset(archive, member)
                archive.download(start, member['compressed_bytes'], compressed)
                inflate(compressed, target, member)
        annotation_sha256 = sha_file(target)
        if annotation_sha256 != ANNOTATION_SHA256[split]:
            raise ValueError('Pinned keypoint annotation SHA256 changed')
        source['members'][split] = member | {'sha256': annotation_sha256,
            'compressed_sha256': sha_file(compressed) if compressed and compressed.exists() else None}
        print(f'Parse verified {split} annotations: {target.stat().st_size} bytes', flush=True)
        with target.open(encoding='utf-8') as stream:
            data = json.load(stream)
        selected[split], summary[split], ids[split] = select_images(data, split, count)
        backgrounds[split] = select_background_candidates(data, split,
            args.background_train_count if split == 'train' else args.background_val_count)
        summary[split]['unverified_background_candidates'] = len(backgrounds[split])
        summary[split]['person_annotation_records'] = len(data['annotations'])
        summary[split]['zero_keypoint_person_records'] = sum(p.get('num_keypoints') == 0 for p in data['annotations'])
        del data
        print(f'{split}: selected{len(selected[split])} from{summary[split]["eligible_images"]}', flush=True)
    ensure_disjoint_splits(ids['train'], ids['val'])
    source['selection'] = summary
    save_json(out/'source.json', source)
    budget = ImageBudget(args.max_image_bytes)
    jobs = []
    for split in ('train', 'val'):
        image_dir = out/'images'/f'{split}2017'; image_dir.mkdir(parents=True, exist_ok=True)
        rows = selected[split] + backgrounds[split]
        for i, row in enumerate(rows):
            path = image_dir/row['file_name']
            row['image_path'] = path.relative_to(out).as_posix()
            row['image_downloaded'] = False
            if not args.metadata_only:
                jobs.append((row, path))
    if len({str(path) for _, path in jobs}) != len(jobs):
        raise ValueError('Duplicate destination image path')
    state = {'status': 'metadata_only' if args.metadata_only else 'downloading', 'pid': os.getpid(),
             'images_requested': len(jobs), 'images_completed': 0, 'image_bytes': 0,
             'image_byte_limit': args.max_image_bytes, 'network_image_bytes': 0, 'workers': args.workers}
    save_json(out/'progress.json', state)
    local = threading.local(); stopped = threading.Event()
    def worker(row, path):
        if stopped.is_set():
            raise RuntimeError('Preparation stopped after another worker failed')
        if not hasattr(local, 'session'):
            local.session = requests.Session()
        return fetch_image(local.session, row, path, budget, previous_hashes.get(row['image_path']))
    executor = ThreadPoolExecutor(max_workers=args.workers)
    futures = {}
    try:
        futures = {executor.submit(worker, row, path): row for row, path in jobs}
        for future in as_completed(futures):
            futures[future].update(future.result())
            state.update(images_completed=state['images_completed'] + 1, image_bytes=budget.used, network_image_bytes=budget.network)
            if state['images_completed'] % 32 == 0 or state['images_completed'] == len(jobs):
                save_json(out/'progress.json', state)
                print(f'Images{state["images_completed"]}/{len(jobs)}; verified payload{budget.used}B, network{budget.network}B', flush=True)
    except BaseException as error:
        stopped.set()
        for future in futures:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)
        state.update(status='failed', error=str(error), image_bytes=budget.used, network_image_bytes=budget.network)
        save_json(out/'progress.json', state)
        raise
    else:
        executor.shutdown(wait=True)
    manifest = {'schema': 'ricestep-coco-smoke-v1', 'source_manifest': 'source.json',
      'source_sha256': sha_file(out/'source.json'), 'metadata_only': args.metadata_only,
      'image_bytes': budget.used, 'image_byte_limit': args.max_image_bytes,
      'network_image_bytes_this_run': budget.network, 'download_workers': args.workers,
      'image_disjoint_official_splits': True, 'keypoint_order': JOINTS,
      'contract': {'coordinates': 'Original-image pixel x/y; visibility0 absent,1 labeled but occluded,2 visible. Never fill absent joints.',
        'target_selection': 'Largest bbox area then annotation_id among noncrowd people with >=10 source-labeled joints and one complete shoulder/hip/knee/ankle chain. Keep all other source annotations as context; no strict standing filter.',
        'training_contract': 'RGB96 input,17 heatmaps24x24 plus34 offsets. Trainer uses declared GT-bbox-centered square crop with1.25..2.0 margin and configured small translation, Pillow bilinear resize, exact COCO17 coordinate mapping and left/right swaps for flips.',
        'crop_policy': 'No crop/augmentation is performed by this preparer. Trainer must record crop/inverse transform and mask out-of-crop joints. GT crop evaluation is not product proof; product evaluation remains full camera FOV without GT boxes.',
        'background_policy': 'Background candidate arrays are unverified and must be ignored by training until explicit annotation-coverage or manual visual validation.',
        'attribution': 'Preserve per-image license URL and Flickr source. Author names are not provided by COCO metadata and are not fabricated.'},
      'train': selected['train'], 'val': selected['val'],
      'background_train_candidates': backgrounds['train'], 'background_val_candidates': backgrounds['val']}
    save_json(out/'manifest.json', manifest)
    state['status'] = 'complete'; save_json(out/'progress.json', state)
    print(json.dumps({'manifest': str(out/'manifest.json'), 'train': len(selected['train']),
                     'val': len(selected['val']), 'image_bytes': manifest['image_bytes'],
                     'annotation_sha256': {k: v['sha256'] for k, v in source['members'].items()}}, indent=2), flush=True)


if __name__ == '__main__':
    main()
