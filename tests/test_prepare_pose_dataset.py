"""Offline bounded COCO-preparer regression; no network or real dataset needed."""
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest import mock
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('prepare_pose_dataset', ROOT/'scripts/pose/prepare_pose_dataset.py')
prep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prep)


def person(image_id=1, annotation_id=11):
    return {'id': annotation_id, 'image_id': image_id, 'category_id': 1, 'iscrowd': 0,
            'bbox': [5, 5, 50, 70], 'keypoints': [30, 40, 2] * 17, 'num_keypoints': 17}


def dataset():
    licenses = [{'id': n, 'name': f'license{n}', 'url': f'https://license.test/{n}'} for n in (4, 5, 7, 8)]
    images = [{'id': n, 'license': (4, 5, 7, 8)[(n-1) % 4], 'width': 100, 'height': 100,
               'file_name': f'{n:012d}.jpg', 'flickr_url': f'https://flickr.test/{n}'} for n in range(1, 9)]
    absent = person(3, 31); absent.update(keypoints=[0, 0, 0] * 17, num_keypoints=0)
    crowd = person(4, 41); crowd['iscrowd'] = 1
    return {'images': images, 'licenses': licenses, 'categories': [{'id': 1, 'keypoints': prep.JOINTS}],
            'annotations': [person(), person(2, 21), absent, crowd]}


def zip_directory(name='annotations/person_keypoints_val2017.json', contents=b'{"test":true}'):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, contents)
    data = stream.getvalue()
    end = struct.unpack_from('<4s4H2LH', data, data.rfind(b'PK\x05\x06'))
    return data, data[end[6]:end[6]+end[5]], end[4]


class RangeTests(unittest.TestCase):
    def headers(self):
        return {'Content-Range': 'bytes 10-13/100', 'Content-Length': '4', 'ETag': '"pinned"'}

    def test_exact206_and_headers_required(self):
        prep.validate_range(206, self.headers(), 10, 13, 100, 'pinned')
        for status, patch in ((200, {}), (206, {'Content-Range': 'bytes 11-14/100'}),
                              (206, {'Content-Length': '100'}), (206, {'ETag': '"changed"'}),
                              (206, {'Content-Encoding': 'gzip'})):
            with self.subTest(status=status, patch=patch), self.assertRaises(ValueError):
                prep.validate_range(status, self.headers() | patch, 10, 13, 100, 'pinned')

    def test_range_body_truncation_and_overflow(self):
        class Response:
            status_code = 206
            headers = {'Content-Range': f'bytes 10-13/{prep.ARCHIVE_BYTES}', 'Content-Length': '4', 'ETag': prep.ARCHIVE_ETAG}
            def __enter__(self): return self
            def __exit__(self, *unused): pass
            def iter_content(self, unused): return iter(self.blocks)
        class Session:
            def get(self, *unused, **kwargs):
                self.request = kwargs
                return self.response
        session = Session(); session.response = Response()
        archive = prep.RangeArchive.__new__(prep.RangeArchive); archive.session = session
        session.response.blocks = [b'ab', b'cd']
        self.assertEqual(b''.join(archive.stream(10, 13)), b'abcd')
        self.assertEqual(session.request['headers']['If-Match'], '"' + prep.ARCHIVE_ETAG + '"')
        self.assertFalse(session.request['allow_redirects'])
        for blocks in ([b'abc'], [b'abcde']):
            session.response.blocks = blocks
            with self.assertRaises(ValueError): list(archive.stream(10, 13))


class ArchiveTests(unittest.TestCase):
    def test_standard_directory_and_path_rejection(self):
        data, central, count = zip_directory()
        entries = prep.parse_directory(central, count, len(data))
        self.assertEqual(len(entries), 1)
        self.assertEqual(next(iter(entries.values()))['bytes'], 13)
        for name in ('../escape.json', '/absolute.json', 'C:/escape.json'):
            data, central, count = zip_directory(name)
            with self.subTest(name=name), self.assertRaises(ValueError):
                prep.parse_directory(central, count, len(data))
        # zipfile normalizes backslashes on Windows, so mutate the encoded name
        # after creation to exercise a genuinely unsafe incoming ZIP directory.
        data, central, count = zip_directory()
        central = central[:46] + central[46:].replace(b'/', b'\\', 1)
        with self.assertRaises(ValueError): prep.parse_directory(central, count, len(data))
        with self.assertRaises(ValueError): prep.parse_directory(b'', 1, 100)
        with self.assertRaises(ValueError): prep.parse_directory(b'', 65, 100)

    def test_local_header_disagreement(self):
        data, central, count = zip_directory()
        member = next(iter(prep.parse_directory(central, count, len(data)).values()))
        class Source:
            def read(self, start, length): return data[start:start+length]
        self.assertEqual(prep.local_data_offset(Source(), member), 30 + len(member['name']))
        bad = member | {'crc32': member['crc32'] ^ 1}
        with self.assertRaises(ValueError): prep.local_data_offset(Source(), bad)

    def test_crc_expansion_and_stream_end(self):
        (ROOT/'.cache').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'.cache') as folder:
            folder = Path(folder); source = folder/'source.deflate'; target = folder/'result.json'
            payload = b'{"real": "annotation"}' * 3000
            compressor = zlib.compressobj(wbits=-15)
            compressed = compressor.compress(payload) + compressor.flush()
            source.write_bytes(compressed)
            member = {'compression': 8, 'bytes': len(payload), 'crc32': zlib.crc32(payload)}
            prep.inflate(source, target, member)
            self.assertEqual(target.read_bytes(), payload)
            self.assertTrue(prep.verify_annotation(target, member))
            for bad in (member | {'crc32': 0}, member | {'bytes': len(payload)-1}, member | {'bytes': 257*prep.MIB}):
                with self.assertRaises(ValueError): prep.inflate(source, target, bad)
                self.assertEqual(target.read_bytes(), payload)  # no partial replacement
            for bad_stream in (compressed[:-2], compressed + b'junk'):
                source.write_bytes(bad_stream)
                with self.assertRaises(ValueError): prep.inflate(source, target, member)


class AnnotationTests(unittest.TestCase):
    def test_valid_visibility_and_real_limb_chain(self):
        row = person(); self.assertTrue(prep.valid_person(row, 100, 100))
        visible = {0, 1, 2, 3, 5, 6, 11, 13, 15, 16}
        row['keypoints'] = [value for i in range(17) for value in ((30, 40, 1) if i in visible else (0, 0, 0))]
        row['num_keypoints'] = 10
        before = copy.deepcopy(row)
        self.assertTrue(prep.valid_person(row, 100, 100))
        self.assertEqual(row, before)  # no invented invisible/occluded coordinates
        row['keypoints'][3*15+2] = 0; row['num_keypoints'] = 9
        self.assertFalse(prep.valid_person(row, 100, 100))

    def test_invalid_person_rejection(self):
        for patch in ({'iscrowd': 1}, {'category_id': 2}, {'bbox': [-1, 0, 30, 40]},
                      {'bbox': [0, 0, 0, 40]}, {'bbox': [90, 0, 30, 40]},
                      {'num_keypoints': 10}, {'keypoints': [0]*50}):
            with self.subTest(patch=patch): self.assertFalse(prep.valid_person(person() | patch, 100, 100))
        for value in (float('nan'), float('inf'), -1, 101):
            row = person(); row['keypoints'][0] = value
            self.assertFalse(prep.valid_person(row, 100, 100))
        row = person(); row['keypoints'][2] = 3
        self.assertFalse(prep.valid_person(row, 100, 100))

    def test_selection_order_and_other_people_preserved(self):
        data = dataset(); small = person(1, 12); small['bbox'] = [5, 5, 20, 20]
        data['annotations'].append(small)
        selected, _, _ = prep.select_images(data, 'train', 2)
        reverse = copy.deepcopy(data); reverse['images'].reverse(); reverse['annotations'].reverse()
        again, _, _ = prep.select_images(reverse, 'train', 2)
        self.assertEqual(selected, again)
        first = next(r for r in selected if r['image_id'] == 1)
        self.assertEqual(first['annotation_id'], 11)
        self.assertEqual(first['qualifying_annotation_ids'], [11, 12])
        self.assertEqual(len(first['all_person_annotations']), 2)
        data['images'][0]['license'] = 2
        with self.assertRaises(ValueError): prep.select_images(data, 'train', 2)

    def test_unverified_background_never_uses_zero_keypoint_people(self):
        rows = prep.select_background_candidates(dataset(), 'train', 4)
        self.assertEqual({r['image_id'] for r in rows}, {5, 6, 7, 8})
        self.assertTrue(all(r['negative_verified'] is False and not r['all_person_annotations'] for r in rows))
        self.assertTrue(all('keypoints_xyv' not in r for r in rows))

    def test_official_split_overlap_fails_closed(self):
        prep.ensure_disjoint_splits({1, 2}, {3, 4})
        with self.assertRaises(ValueError): prep.ensure_disjoint_splits({1, 2}, {2, 3})


class ImageTests(unittest.TestCase):
    def test_image_bytes_must_match_annotation_dimensions(self):
        from PIL import Image
        (ROOT/'.cache').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'.cache') as folder:
            path = Path(folder)/'image.jpg'
            Image.new('RGB', (8, 6), 'blue').save(path)
            prep.verify_image(path, {'width': 8, 'height': 6})
            with self.assertRaises(ValueError): prep.verify_image(path, {'width': 6, 'height': 8})
            path.write_bytes(b'not an image')
            with self.assertRaises(OSError): prep.verify_image(path, {'width': 8, 'height': 6})


class ImageBudgetTests(unittest.TestCase):
    def test_success_commits_and_exception_releases_only_its_reservation(self):
        budget = prep.ImageBudget(100)
        with budget.reservation(20):
            self.assertEqual((budget.used, budget.reserved), (0, 20))
        for error in (RuntimeError('failed stream'), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(type(error)), budget.reservation(50):
                    budget.received(10)
                    raise error
                self.assertEqual((budget.used, budget.reserved), (20, 0))
        self.assertEqual(budget.network, 20)  # failed attempts still consumed bytes
        with budget.reservation(80):
            pass
        self.assertEqual((budget.used, budget.reserved), (100, 0))
        with self.assertRaises(ValueError), budget.reservation(1):
            self.fail('Over-budget reservation body must never run')

    def test_invalid_limits_and_reservations_do_not_mutate_budget(self):
        for limit in (0, -1, 600*prep.MIB+1):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                prep.ImageBudget(limit)
        budget = prep.ImageBudget(9*prep.MIB)
        for size in (0, -1, 8*prep.MIB+1):
            with self.subTest(size=size), self.assertRaises(ValueError), budget.reservation(size):
                self.fail('Invalid reservation must never enter')
            self.assertEqual((budget.used, budget.reserved), (0, 0))

    def test_concurrent_reservations_cannot_overbook(self):
        budget = prep.ImageBudget(30)
        start = threading.Barrier(13)
        release = threading.Event()
        attempted = threading.Condition()
        observations = []
        def worker():
            start.wait(timeout=5)
            try:
                with budget.reservation(10):
                    with attempted:
                        observations.append(True); attempted.notify_all()
                    if not release.wait(timeout=5):
                        raise AssertionError('Test release was not signaled')
                return True
            except ValueError:
                with attempted:
                    observations.append(False); attempted.notify_all()
                return False
        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = [pool.submit(worker) for _ in range(12)]
            try:
                start.wait(timeout=5)
                with attempted:
                    self.assertTrue(attempted.wait_for(lambda: len(observations)==12, timeout=5))
                self.assertEqual(sum(observations), 3)
                self.assertEqual((budget.used, budget.reserved), (0, 30))
            finally:
                release.set()
            self.assertEqual(sum(f.result(timeout=5) for f in futures), 3)
        self.assertEqual((budget.used, budget.reserved), (30, 0))

    def test_received_budget_is_shared_and_does_not_reset_on_retry(self):
        budget = prep.ImageBudget(100)
        start = threading.Barrier(17)
        def worker():
            start.wait(timeout=5)
            try:
                budget.received(10)
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = [pool.submit(worker) for _ in range(16)]
            start.wait(timeout=5)
            self.assertEqual(sum(f.result(timeout=5) for f in futures), 10)
        # All16 chunks were delivered to received(), including rejected chunks.
        # Report the real160 bytes rather than pretending the transport stopped at100.
        self.assertEqual(budget.network, 160)
        self.assertEqual((budget.used, budget.reserved), (0, 0))
        with self.assertRaises(ValueError):
            budget.received(-1)
        self.assertEqual(budget.network, 160)
        with self.assertRaises(ValueError):
            budget.received(1)
        self.assertEqual(budget.network, 161)
        with self.assertRaisesRegex(ValueError,'network-byte budget'), budget.reservation(1):
            self.fail('Queued/retried body must not start after network abort')
        self.assertEqual((budget.used,budget.reserved), (0,0))
        exact = prep.ImageBudget(100)
        exact.received(100); exact.received(0)
        self.assertEqual(exact.network, 100)


class FakeImageResponse:
    def __init__(self, blocks, length, status=200, headers=None):
        self.blocks = blocks
        self.status_code = status
        self.headers = {'Content-Length': str(length)} | (headers or {})
        self.closed = False
    def __enter__(self): return self
    def __exit__(self, *unused): self.closed = True
    def raise_for_status(self):
        if self.status_code >= 400:
            raise prep.requests.HTTPError('Synthetic HTTP failure')
    def iter_content(self, unused):
        for item in self.blocks:
            if isinstance(item, BaseException):
                raise item
            yield item


class FakeImageSession:
    def __init__(self, responses=()):
        self.responses = iter(responses)
        self.requests = []
    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        try:
            return next(self.responses)
        except StopIteration:
            raise AssertionError('Unexpected GET; this test must remain offline') from None


class ImageFetchTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        (ROOT/'.cache').mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT/'.cache')
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)/'000000000017.jpg'
        self.receipt = self.path.with_suffix('.jpg.source.json')
        self.row = {'image_id':17, 'split':'val', 'file_name':self.path.name, 'width':8, 'height':6}
        stream = io.BytesIO(); Image.new('RGB', (8,6), 'blue').save(stream, format='JPEG')
        self.payload = stream.getvalue()
        self.digest = hashlib.sha256(self.payload).hexdigest()
        self.identity = {'image_id':17, 'width':8, 'height':6,
            'download_url':'https://s3.amazonaws.com/images.cocodataset.org/val2017/000000000017.jpg'}

    def seed_cache(self):
        self.path.write_bytes(self.payload)
        receipt = self.identity | {'image_sha256':self.digest, 'image_bytes':len(self.payload),
                                   'image_downloaded':True}
        prep.save_json(self.receipt, receipt)
        return receipt

    def test_download_records_receipt_and_resume_performs_no_get(self):
        response = FakeImageResponse([self.payload[:11], self.payload[11:]], len(self.payload))
        session = FakeImageSession([response]); budget = prep.ImageBudget(2*len(self.payload))
        downloaded = prep.fetch_image(session, self.row, self.path, budget)
        self.assertEqual((budget.used,budget.reserved,budget.network), (len(self.payload),0,len(self.payload)))
        self.assertTrue(response.closed)
        self.assertEqual(self.path.read_bytes(), self.payload)
        self.assertEqual(json.loads(self.receipt.read_text()), downloaded)
        self.assertEqual(downloaded['image_sha256'], self.digest)
        self.assertFalse(self.path.with_suffix('.jpg.part').exists())
        url, kwargs = session.requests[0]
        self.assertEqual(url, self.identity['download_url'])
        self.assertFalse(kwargs['allow_redirects'])
        self.assertEqual(kwargs['headers']['Accept-Encoding'], 'identity')
        original_receipt = self.receipt.read_bytes()
        resumed = FakeImageSession(); resumed_budget = prep.ImageBudget(len(self.payload))
        self.assertEqual(prep.fetch_image(resumed,self.row,self.path,resumed_budget),downloaded)
        self.assertEqual(resumed.requests, [])
        self.assertEqual(self.receipt.read_bytes(), original_receipt)
        self.assertEqual((resumed_budget.used,resumed_budget.reserved,resumed_budget.network), (len(self.payload),0,0))

    def test_manifest_pin_without_receipt_reuses_and_creates_receipt(self):
        self.path.write_bytes(self.payload)
        session = FakeImageSession(); budget = prep.ImageBudget(len(self.payload))
        result = prep.fetch_image(session,self.row,self.path,budget,expected_sha=self.digest)
        self.assertEqual(session.requests, [])
        self.assertEqual(json.loads(self.receipt.read_text()), result)
        self.assertEqual(budget.network, 0)

    def test_cache_and_receipt_identity_or_hash_mismatch_fail_without_get(self):
        good = self.seed_cache()
        mutations = [{'image_id':18}, {'width':9}, {'height':7},
                     {'download_url':self.identity['download_url'].replace('val2017','train2017')},
                     {'image_sha256':'0'*64}]
        for patch in mutations:
            with self.subTest(patch=patch):
                prep.save_json(self.receipt, good | patch)
                session = FakeImageSession(); budget = prep.ImageBudget(2*len(self.payload))
                with self.assertRaises(ValueError):
                    prep.fetch_image(session,self.row,self.path,budget)
                self.assertEqual(session.requests, [])
                self.assertEqual((budget.used,budget.reserved,budget.network), (0,0,0))
                self.assertEqual(self.path.read_bytes(), self.payload)
        prep.save_json(self.receipt, good)
        session = FakeImageSession()
        with self.assertRaisesRegex(ValueError,'previous manifest'):
            prep.fetch_image(session,self.row,self.path,prep.ImageBudget(2*len(self.payload)),expected_sha='1'*64)
        self.assertEqual(session.requests, [])
        self.path.write_bytes(self.payload[:-1]+bytes([self.payload[-1]^1]))
        with self.assertRaisesRegex(ValueError,'SHA256 mismatch'):
            prep.fetch_image(session,self.row,self.path,prep.ImageBudget(2*len(self.payload)))
        self.assertEqual(session.requests, [])

    def test_cached_sha_does_not_bypass_real_jpeg_dimensions(self):
        self.seed_cache()
        changed_row = self.row | {'width':9}
        receipt = json.loads(self.receipt.read_text()); receipt['width'] = 9
        prep.save_json(self.receipt, receipt)
        session = FakeImageSession()
        with self.assertRaises(ValueError):
            prep.fetch_image(session,changed_row,self.path,prep.ImageBudget(len(self.payload)))
        self.assertEqual(session.requests, [])

    def test_malformed_receipt_sha_fails_before_network_or_cache_change(self):
        good = self.seed_cache()
        for value in ('',None,False,0,{},'a'*63,'a'*65,'g'*64,'A'*64):
            with self.subTest(value=value):
                prep.save_json(self.receipt,good | {'image_sha256':value})
                session = FakeImageSession(); budget = prep.ImageBudget(2*len(self.payload))
                with self.assertRaisesRegex(ValueError,'receipt SHA256'):
                    prep.fetch_image(session,self.row,self.path,budget)
                self.assertEqual(session.requests, [])
                self.assertEqual((budget.used,budget.reserved,budget.network), (0,0,0))
                self.assertEqual(self.path.read_bytes(), self.payload)
        del good['image_sha256']; prep.save_json(self.receipt,good)
        with self.assertRaisesRegex(ValueError,'receipt SHA256'):
            prep.fetch_image(FakeImageSession(),self.row,self.path,prep.ImageBudget(len(self.payload)))
        prep.save_json(self.receipt,[])
        with self.assertRaisesRegex(ValueError,'receipt must be an object'):
            prep.fetch_image(FakeImageSession(),self.row,self.path,prep.ImageBudget(len(self.payload)))

    def test_bad_receipt_byte_count_is_rejected_and_legacy_missing_size_allowed(self):
        good = self.seed_cache()
        for value in (0,-1,True,1.5,'123',None,8*prep.MIB+1,len(self.payload)+1):
            with self.subTest(value=value):
                prep.save_json(self.receipt,good | {'image_bytes':value})
                session = FakeImageSession()
                with self.assertRaisesRegex(ValueError,'receipt byte count'):
                    prep.fetch_image(session,self.row,self.path,prep.ImageBudget(len(self.payload)))
                self.assertEqual(session.requests, [])
        del good['image_bytes']; prep.save_json(self.receipt,good)
        session = FakeImageSession()
        self.assertEqual(prep.fetch_image(session,self.row,self.path,prep.ImageBudget(len(self.payload)))['image_sha256'],self.digest)
        self.assertEqual(session.requests, [])

    def test_invalid_previous_manifest_pin_is_rejected_before_get(self):
        self.path.write_bytes(self.payload)
        for digest in ('',False,'x'*64,'a'*63):
            with self.subTest(digest=digest), self.assertRaisesRegex(ValueError,'Previous manifest'):
                prep.fetch_image(FakeImageSession(),self.row,self.path,prep.ImageBudget(len(self.payload)),expected_sha=digest)

    def test_retry_rolls_back_payload_reservation_but_counts_prior_network(self):
        first = FakeImageResponse([self.payload[:19],prep.requests.ConnectionError('Synthetic interruption')],len(self.payload))
        second = FakeImageResponse([self.payload],len(self.payload))
        session = FakeImageSession([first,second]); budget = prep.ImageBudget(len(self.payload)+19)
        with mock.patch.object(prep.time,'sleep') as sleep:
            result = prep.fetch_image(session,self.row,self.path,budget)
        self.assertEqual(result['image_sha256'], self.digest)
        self.assertEqual((budget.used,budget.reserved,budget.network), (len(self.payload),0,len(self.payload)+19))
        self.assertEqual(len(session.requests), 2)
        sleep.assert_called_once_with(1)
        self.assertTrue(first.closed and second.closed)
        self.assertFalse(self.path.with_suffix('.jpg.part').exists())

    def test_retry_network_ceiling_aborts_without_committing_or_third_get(self):
        first = FakeImageResponse([self.payload[:19],prep.requests.ConnectionError('Synthetic interruption')],len(self.payload))
        second = FakeImageResponse([self.payload],len(self.payload))
        session = FakeImageSession([first,second]); budget = prep.ImageBudget(len(self.payload))
        with mock.patch.object(prep.time,'sleep'), self.assertRaisesRegex(ValueError,'network-byte budget'):
            prep.fetch_image(session,self.row,self.path,budget)
        self.assertEqual((budget.used,budget.reserved), (0,0))
        self.assertEqual(budget.network, len(self.payload)+19)  # offending chunk is already delivered
        self.assertEqual(len(session.requests), 2)
        self.assertFalse(self.path.exists())
        self.assertFalse(self.receipt.exists())
        self.assertFalse(self.path.with_suffix('.jpg.part').exists())

    def test_retry_exhaustion_is_bounded_and_cleans_partial(self):
        responses = [FakeImageResponse([self.payload[:7],prep.requests.ConnectionError('Interrupted')],len(self.payload)) for _ in range(3)]
        budget = prep.ImageBudget(2*len(self.payload)); session = FakeImageSession(responses)
        with mock.patch.object(prep.time,'sleep') as sleep, self.assertRaises(prep.requests.ConnectionError):
            prep.fetch_image(session,self.row,self.path,budget)
        self.assertEqual(len(session.requests), 3)
        self.assertEqual(sleep.call_args_list, [mock.call(1),mock.call(2)])
        self.assertEqual((budget.used,budget.reserved,budget.network), (0,0,21))
        self.assertFalse(self.path.exists())
        self.assertFalse(self.receipt.exists())
        self.assertFalse(self.path.with_suffix('.jpg.part').exists())
        self.assertTrue(all(response.closed for response in responses))

    def test_bad_length_or_image_never_commits_receipt(self):
        scenarios = [(self.payload[:-1],len(self.payload)),(self.payload+b'x',len(self.payload)),
                     (b'not a jpeg',10)]
        for payload, declared in scenarios:
            with self.subTest(declared=declared,actual=len(payload)):
                response = FakeImageResponse([payload],declared)
                session = FakeImageSession([response]); budget = prep.ImageBudget(2*len(self.payload))
                with self.assertRaises((ValueError,OSError)):
                    prep.fetch_image(session,self.row,self.path,budget)
                self.assertEqual((budget.used,budget.reserved), (0,0))
                self.assertEqual(budget.network, len(payload))
                self.assertFalse(self.path.exists())
                self.assertFalse(self.receipt.exists())
                self.assertFalse(self.path.with_suffix('.jpg.part').exists())

    def test_missing_cached_file_keeps_receipt_pin_during_redownload(self):
        good = self.seed_cache(); self.path.unlink()
        changed = self.payload[:-1]+bytes([self.payload[-1]^1])
        response = FakeImageResponse([changed],len(changed))
        session = FakeImageSession([response]); budget = prep.ImageBudget(len(changed))
        # Isolate hash pin enforcement from JPEG decoder tolerance for trailing bytes.
        with mock.patch.object(prep,'verify_image'), self.assertRaisesRegex(ValueError,'pinned receipt'):
            prep.fetch_image(session,self.row,self.path,budget)
        self.assertEqual(json.loads(self.receipt.read_text()), good)
        self.assertFalse(self.path.exists())
        self.assertEqual((budget.used,budget.reserved,budget.network), (0,0,len(changed)))

    def test_missing_cache_receipt_size_mismatch_rejects_before_body_read(self):
        good = self.seed_cache(); self.path.unlink()
        response = FakeImageResponse([AssertionError('Body must not be consumed')],len(self.payload)+1)
        session = FakeImageSession([response]); budget = prep.ImageBudget(2*len(self.payload))
        with self.assertRaisesRegex(ValueError,'byte count differs'):
            prep.fetch_image(session,self.row,self.path,budget)
        self.assertEqual((budget.used,budget.reserved,budget.network), (0,0,0))
        self.assertEqual(json.loads(self.receipt.read_text()), good)
        self.assertFalse(self.path.exists())


if __name__ == '__main__':
    unittest.main()
