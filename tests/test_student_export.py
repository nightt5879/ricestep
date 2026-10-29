"""Optional TF research tests; input fixture must come from test_student_model.py."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
import numpy as np
import tensorflow as tf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'/'pose'))
from student_export import build_keras_model, check_parity, export_models, load_representative, load_weights

OPTIONS = None


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.model, self.mapping = build_keras_model()
        self.weights = OPTIONS.fixture/'weights.npz'
        self.reference = OPTIONS.fixture/'parity.npz'

    def test_torch_keras_parity_and_contract(self):
        info = load_weights(self.model, self.mapping, self.weights)
        self.assertEqual(info['provenance']['training_status'], 'untrained')
        result, _ = check_parity(self.model, self.reference)
        self.assertLess(result['logits_max_abs'], 1e-5)
        self.assertLess(result['offsets_max_abs'], 1e-5)

    def test_malformed_weights_are_rejected(self):
        with np.load(self.weights, allow_pickle=False) as source:
            original = {k:source[k] for k in source.files}
        cases = []
        changed = dict(original); changed.pop('stem.conv.weight'); cases.append(changed)
        changed = dict(original); changed['unexpected'] = np.zeros(1,np.float32); cases.append(changed)
        changed = dict(original); changed['stem.bn.running_var'] = np.full(16,-1,np.float32); cases.append(changed)
        changed = dict(original); changed['stem.conv.weight'] = np.full((16,3,3,3),np.nan,np.float32); cases.append(changed)
        changed = dict(original)
        meta = json.loads(changed['__metadata__'].tobytes()); meta['offset_layout'] = 'wrong_xy'
        changed['__metadata__'] = np.frombuffer(json.dumps(meta).encode(),np.uint8); cases.append(changed)
        with tempfile.TemporaryDirectory() as directory:
            for index, arrays in enumerate(cases):
                path = Path(directory)/f'bad{index}.npz'; np.savez(path, **arrays)
                with self.subTest(index=index), self.assertRaises(ValueError):
                    load_weights(self.model, self.mapping, path)

    def test_no_implicit_random_calibration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            np.savez(path/'images.npz', images=np.zeros((16,96,96,3),np.float32))
            metadata = dict(source='synthetic unit-test rejection fixture', synthetic=True,
                preprocessing='rgb*(2/255)-1', sample_count=16)
            (path/'images.json').write_text(json.dumps(metadata))
            with self.assertRaises(ValueError): load_representative(path/'images.npz',path/'images.json')
            with self.assertRaises(ValueError): export_models(self.weights,path/'out',representative=path/'images.npz')
            self.assertFalse((path/'out').exists())

    def test_representative_metadata_and_numeric_bounds(self):
        # Synthetic arrays are used ONLY for rejection tests, never conversion.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            baseline = dict(source='unit test declaration; no conversion performed', synthetic=False,
                preprocessing='rgb*(2/255)-1', sample_count=16)
            cases = []
            bad = dict(baseline, sample_count=17); cases.append((np.zeros((16,96,96,3),np.float32),bad))
            bad = dict(baseline, npz_sha256='0'*64); cases.append((np.zeros((16,96,96,3),np.float32),bad))
            cases.append((np.zeros((16,3,96,96),np.float32),baseline))
            cases.append((np.zeros((16,96,96,3),np.float64),baseline))
            cases.append((np.full((16,96,96,3),np.nan,np.float32),baseline))
            cases.append((np.full((16,96,96,3),1.1,np.float32),baseline))
            for index, (images, metadata) in enumerate(cases):
                np.savez(path/'images.npz', images=images)
                (path/'images.json').write_text(json.dumps(metadata))
                with self.subTest(index=index), self.assertRaises(ValueError):
                    load_representative(path/'images.npz',path/'images.json')

    def test_float_export_refuses_stale_integer_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path/'student-int8.tflite').write_bytes(b'old artifact must not silently survive')
            with self.assertRaises(ValueError): export_models(self.weights,path)
            self.assertEqual((path/'student-int8.tflite').read_bytes(),b'old artifact must not silently survive')

    def test_export_float_and_explicit_int8_when_supplied(self):
        target = OPTIONS.fixture/('export-smoke-int8' if OPTIONS.representative else 'export-smoke-float')
        report = export_models(self.weights, target, parity_reference=self.reference,
            representative=OPTIONS.representative, representative_metadata=OPTIONS.representative_metadata)
        self.assertTrue(report['research_only'])
        self.assertIsNotNone(report['torch_parity'])
        self.assertEqual(report['float']['input']['dtype'], 'float32')
        if OPTIONS.representative:
            self.assertEqual(report['int8']['input']['dtype'], 'int8')
            self.assertTrue(all(o['dtype']=='int8' for o in report['int8']['outputs']))
            self.assertFalse(report['calibration']['synthetic'])
        else:
            self.assertIsNone(report['int8'])
        print('EXPORT_SMOKE', json.dumps({k:report[k] for k in ('torch_parity','float','int8')}),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--representative', type=Path)
    parser.add_argument('--representative-metadata', type=Path)
    OPTIONS, rest = parser.parse_known_args()
    tf.config.threading.set_intra_op_parallelism_threads(2)
    tf.config.threading.set_inter_op_parallelism_threads(1)
    unittest.main(argv=[sys.argv[0], *rest])
