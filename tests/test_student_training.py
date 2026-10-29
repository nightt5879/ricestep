"""Training loss must favor located joints and reject arbitrary confidence shifts."""
from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import sys
import tempfile
import types
import unittest
from unittest import mock

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/pose"))
from student_data import targets
from train_student import objective
import student_data
import train_student as training


class TrainingLossTest(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(1)
        heat, offsets, mask = targets(np.full((17, 2), 12, np.float32), np.ones(17, np.float32))
        self.data = {"heatmaps": torch.from_numpy(heat[None]), "offsets": torch.from_numpy(offsets[None]),
                     "offset_mask": torch.from_numpy(mask[None]), "valid": torch.ones(1, 17)}
        self.located = torch.log(self.data["heatmaps"].clamp_min(1e-8))

    def test_location_beats_dark_or_displaced_map(self):
        offsets = self.data["offsets"]
        good = objective((self.located, offsets), self.data)
        dark = objective((torch.full_like(self.located, -30), offsets), self.data)
        displaced = objective((self.located.roll(8, dims=3), offsets), self.data)
        self.assertLess(good, dark)
        self.assertLess(good, displaced)

    def test_absolute_logit_shift_is_not_free(self):
        offsets = self.data["offsets"]
        centered = objective((self.located, offsets), self.data)
        inflated = objective((self.located + 30, offsets), self.data)
        self.assertGreater(inflated, centered + 10)

    def test_joint_and_offset_gradients_are_finite(self):
        logits = torch.zeros_like(self.located, requires_grad=True)
        offsets = torch.zeros_like(self.data["offsets"], requires_grad=True)
        loss = objective((logits, offsets), self.data)
        loss.backward()
        for value in (logits, offsets):
            self.assertTrue(torch.isfinite(value.grad).all())
            self.assertGreater(float(value.grad.abs().sum()), 0)


class TrainingContractTest(unittest.TestCase):
    def setUp(self):
        self.cache = training.ROOT / '.cache'
        self.cache.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=self.cache)
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.weights = self.directory / 'mock-backbone.npz'
        self.weights.write_bytes(b'Only a mocked loader consumes this test file')

    def args(self, *extra):
        return training.parse_args(['--manifest','unused-manifest.json','--output-dir','.cache/unused',*extra])

    def test_default_cli_keeps_tiny_and_cache_disabled(self):
        args = self.args()
        self.assertEqual(args.model, 'tiny')
        self.assertIsNone(args.backbone_weights)
        self.assertEqual(args.image_cache_mib, 0)
        self.assertEqual(training.model_source_files('tiny'),
                         ('train_student.py','student_model.py','student_data.py'))
        self.assertEqual(training.model_source_files('mobilenet025'),
                         ('train_student.py','student_model.py','student_data.py','mobilenet_student.py'))

    def test_cli_requires_explicit_existing_mobile_weights_and_rejects_tiny_weights(self):
        for extra in (['--model','mobilenet025'],
                      ['--model','mobilenet025','--backbone-weights',str(self.directory/'missing.npz')],
                      ['--model','mobilenet025','--backbone-weights',str(self.directory)],
                      ['--backbone-weights',str(self.weights)]):
            with self.subTest(extra=extra), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                self.args(*extra)
            self.assertEqual(error.exception.code, 2)
        args = self.args('--model','mobilenet025','--backbone-weights',str(self.weights))
        self.assertEqual(args.backbone_weights, self.weights)

    def test_cache_cli_integer_boundaries(self):
        self.assertEqual(self.args('--image-cache-mib','2048').image_cache_mib, 2048)
        for value in ('-1','2049','1.5','nan'):
            with self.subTest(value=value), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.args('--image-cache-mib',value)

    def test_tiny_factory_does_not_require_mobile_module(self):
        with mock.patch.dict(sys.modules, {'mobilenet_student': None}):
            model, profile, export, provenance = training.create_model('tiny')
        self.assertIsInstance(model, training.TinyPose)
        self.assertIs(profile, training.profile_model)
        self.assertIs(export, training.export_numpy_weights)
        self.assertIsNone(provenance)
        with self.assertRaises(ValueError): training.create_model('tiny',self.weights)
        with self.assertRaises(ValueError): training.create_model('unknown')
        with self.assertRaises(ValueError): training.create_model('mobilenet025')

    def mobile_module(self):
        # Factory routing is isolated from the real loader's separate NPZ tests.
        module = types.ModuleType('mobilenet_student')
        module.MobileNetPose = mock.Mock(return_value=training.TinyPose())
        module.load_pretrained_backbone = mock.Mock(return_value={'source':'test fixture, never pretrained proof'})
        module.profile_model = mock.Mock()
        module.export_numpy_weights = mock.Mock()
        return module

    def test_mobile_factory_calls_explicit_loader_and_records_exact_file_hash(self):
        module = self.mobile_module()
        with mock.patch.dict(sys.modules,{'mobilenet_student':module}):
            model, profile, export, provenance = training.create_model('mobilenet025',self.weights)
        module.MobileNetPose.assert_called_once_with()
        module.load_pretrained_backbone.assert_called_once_with(model,self.weights.resolve())
        self.assertIs(profile,module.profile_model)
        self.assertIs(export,module.export_numpy_weights)
        self.assertEqual(provenance['weights_sha256'],hashlib.sha256(self.weights.read_bytes()).hexdigest())
        self.assertEqual(provenance['provenance'],module.load_pretrained_backbone.return_value)

    def test_mobile_loader_failure_never_returns_random_fallback(self):
        module = self.mobile_module()
        module.load_pretrained_backbone.side_effect = ValueError('Invalid backbone contract')
        with mock.patch.dict(sys.modules,{'mobilenet_student':module}), self.assertRaisesRegex(ValueError,'backbone contract'):
            training.create_model('mobilenet025',self.weights)
        module.load_pretrained_backbone.assert_called_once()
        module.profile_model.assert_not_called()

    def test_mobile_provenance_missing_or_changed_source_is_rejected(self):
        for metadata in (None,{},[]):
            module = self.mobile_module(); module.load_pretrained_backbone.return_value = metadata
            with self.subTest(metadata=metadata), mock.patch.dict(sys.modules,{'mobilenet_student':module}), self.assertRaises(ValueError):
                training.create_model('mobilenet025',self.weights)
        module = self.mobile_module()
        def changed_source(model,path):
            path.write_bytes(b'Changed during load')
            return {'source':'test'}
        module.load_pretrained_backbone.side_effect = changed_source
        with mock.patch.dict(sys.modules,{'mobilenet_student':module}), self.assertRaises(ValueError):
            training.create_model('mobilenet025',self.weights)

    def test_cache_dispatch_only_calls_helper_when_enabled(self):
        dataset = {'train':[], 'val':[]}
        metadata = {'images':2,'decoded_bytes':300,'max_bytes':1024*1024,'scope':'train and val'}
        with mock.patch.object(student_data,'preload_images',return_value=metadata) as preload:
            self.assertEqual(training.cache_images(dataset,0),{'images':0,'decoded_bytes':0,'max_bytes':0})
            preload.assert_not_called()
            self.assertEqual(training.cache_images(dataset,1),metadata)
            preload.assert_called_once_with(dataset,1024*1024)
        for value in (-1,2049,1.5,True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                training.cache_images(dataset,value)

    def test_cache_budget_failure_propagates_without_training(self):
        with mock.patch.object(student_data,'preload_images',side_effect=ValueError('Cache budget exceeded')):
            with self.assertRaisesRegex(ValueError,'Cache budget'):
                training.cache_images({'train':[],'val':[]},1)

    def test_tiny_one_batch_cpu_report_checkpoint_snapshot_and_export(self):
        """Two synthetic fixture images exercise persistence, not pose accuracy."""
        from PIL import Image
        manifest = {}
        for image_id,(split,color) in enumerate((('train','blue'),('val','green')),1):
            path = self.directory/(split+'.png')
            Image.new('RGB',(80,64),color).save(path)
            manifest[split] = [{'image_id':image_id,'split':split,'iscrowd':0,'image_downloaded':True,
                'image_path':path.name,'image_bytes':path.stat().st_size,
                'image_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'width':80,'height':64,
                'bbox_xywh':[10,5,60,55],'keypoints_xyv':[40,30,2]*17}]
        manifest_path = self.directory/'manifest.json'
        manifest_path.write_text(json.dumps(manifest),encoding='utf-8')
        output = self.directory/'tiny-run'
        with redirect_stdout(io.StringIO()):
            training.main(['--manifest',str(manifest_path),'--output-dir',str(output),
                           '--epochs','1','--batch-size','1','--device','cpu','--image-cache-mib','1'])
        report = json.loads((output/'training-report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['status'],'completed')
        self.assertEqual(report['architecture'],'ricestep-tiny-pose-v1')
        self.assertIsNone(report['backbone_pretraining'])
        self.assertEqual(report['configuration']['model'],'tiny')
        self.assertEqual(report['image_cache']['images'],2)
        self.assertEqual(report['image_cache']['decoded_bytes'],2*80*64*3)
        self.assertEqual(report['image_cache']['max_bytes'],1024*1024)
        self.assertEqual(set(report['source_sha256']),set(training.model_source_files('tiny')))
        for name,digest in report['source_sha256'].items():
            self.assertEqual(hashlib.sha256((output/'source-snapshot'/name).read_bytes()).hexdigest(),digest)
        checkpoint = torch.load(output/'best.pt',map_location='cpu',weights_only=True)
        self.assertEqual(checkpoint['architecture'],report['architecture'])
        self.assertEqual(checkpoint['model_choice'],'tiny')
        self.assertEqual(checkpoint['manifest_sha256'],report['manifest_sha256'])
        with np.load(output/'weights.npz',allow_pickle=False) as weights:
            metadata = json.loads(weights['__metadata__'].tobytes())
        self.assertEqual(metadata['format'],'ricestep-tiny-pose-weights-v1')
        self.assertEqual(metadata['provenance']['architecture'],report['architecture'])
        self.assertEqual(report['best_checkpoint_sha256'],hashlib.sha256((output/'best.pt').read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
