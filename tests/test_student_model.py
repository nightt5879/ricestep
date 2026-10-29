"""Focused optional PyTorch research tests; no hardware/data downloads."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "pose"))
from student_model import ConvBNAct, TinyPose, export_numpy_weights, profile_model, same_padding


class ModelTests(unittest.TestCase):
    def test_same_even_padding_has_known_boundary_values(self):
        self.assertEqual(same_padding(96, 3, 2), (0, 1))
        self.assertEqual(same_padding(95, 3, 2), (1, 1))
        block = ConvBNAct(1, 1, 3, 2).eval()
        with torch.no_grad():
            block.conv.weight.fill_(0.01)
            block.bn.weight.fill_((1 + block.bn.eps)**0.5)
            block.bn.bias.zero_()
        image = torch.arange(1,17,dtype=torch.float32).reshape(1,1,4,4)
        np.testing.assert_allclose(block(image).detach().numpy()[0,0],
            [[.54,.45],[.72,.54]], rtol=1e-6, atol=1e-7)

    def test_profile_and_shapes_preserve_training_state(self):
        model = TinyPose().train()
        before = {k:v.clone() for k,v in model.state_dict().items()}
        budget = profile_model(model)
        self.assertTrue(model.training)
        self.assertTrue(all(m.training for m in model.modules()))
        self.assertEqual(budget['nominal_convolution_mac'], 5140800)
        self.assertEqual(budget['all_parameters'], 43651)
        # Includes explicit SAME padding input: 49*49*16 before the first DS.
        self.assertEqual(budget['largest_single_activation_elements'], 38416)
        self.assertEqual(budget['retained_skip_elements'], 20736)
        self.assertEqual(len(budget['convolution_layers']), 23)
        for key, value in before.items(): self.assertTrue(torch.equal(value, model.state_dict()[key]))
        logits, offsets = model(torch.zeros(2,3,96,96))
        self.assertEqual(tuple(logits.shape), (2,17,24,24))
        self.assertEqual(tuple(offsets.shape), (2,34,24,24))
        with self.assertRaises(ValueError): model(torch.zeros(1,96,96,3))
        with self.assertRaises(ValueError): model(torch.zeros(1,3,95,95))

    def test_heads_are_linear_and_both_backpropagate(self):
        torch.manual_seed(16)
        model = TinyPose().train()
        logits, offsets = model(torch.randn(2,3,96,96))
        (logits.square().mean() + offsets.square().mean()).backward()
        for parameter in (model.stem.conv.weight, model.heatmap.weight, model.offset.weight):
            self.assertTrue(torch.isfinite(parameter.grad).all())
            self.assertGreater(float(parameter.grad.abs().sum()), 0)
        model.eval()
        with torch.no_grad():
            model.heatmap.weight.zero_(); model.heatmap.bias.fill_(-2)
            model.offset.weight.zero_(); model.offset.bias.copy_(torch.arange(34,dtype=torch.float32)-17)
            logits, offsets = model(torch.zeros(1,3,96,96))
        self.assertTrue(torch.all(logits == -2))
        self.assertTrue(torch.equal(offsets[0,:,0,0], torch.arange(34,dtype=torch.float32)-17))

    def test_export_is_pickle_free_and_rejects_nonfinite(self):
        with tempfile.TemporaryDirectory() as directory:
            model = TinyPose()
            path = Path(directory)/'weights.npz'
            export_numpy_weights(model, path)
            with np.load(path, allow_pickle=False) as saved:
                metadata = json.loads(saved['__metadata__'].tobytes())
                self.assertEqual(metadata['provenance']['training_status'], 'untrained')
                self.assertEqual(metadata['offset_layout'], 'interleaved_yx_grid_units')
                self.assertEqual(saved['stem.conv.weight'].shape, (16,3,3,3))
            with torch.no_grad(): model.stem.conv.weight[0,0,0,0] = float('nan')
            with self.assertRaises(ValueError): export_numpy_weights(model, path)


def write_fixture(path):
    """Nontrained deterministic weights for conversion parity, never calibration."""
    path.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(2718)
    model = TinyPose().eval()
    with torch.no_grad():
        for module in model.modules():
            if isinstance(module, torch.nn.BatchNorm2d):
                n = module.num_features
                module.running_mean.copy_(torch.linspace(-.3,.3,n))
                module.running_var.copy_(torch.linspace(.5,1.5,n))
                module.weight.copy_(torch.linspace(.5,1.25,n))
                module.bias.copy_(torch.linspace(-.1,.1,n))
    images = np.zeros((4,96,96,3), dtype=np.float32)
    images[0].fill(-1); images[1].fill(1)
    yy, xx = np.indices((96,96))
    images[2,:,:,0] = (xx%2)*2-1
    images[2,:,:,1] = (yy%2)*2-1
    images[2,:,:,2] = xx.astype(np.float32)/95*2-1
    images[3,0,0] = [1,-1,1]
    images[3,95,95] = [-1,1,-1]
    with torch.no_grad():
        logits, offsets = model(torch.from_numpy(images.transpose(0,3,1,2).copy()))
    np.savez(path/'parity.npz', images=images,
        logits=logits.numpy().transpose(0,2,3,1), offsets=offsets.numpy().transpose(0,2,3,1))
    export_numpy_weights(model, path/'weights.npz', metadata={
        'training_status':'untrained', 'purpose':'deterministic nontrained conversion fixture, not calibration', 'seed':2718})
    (path/'budget.json').write_text(json.dumps(profile_model(model),indent=2)+'\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--write-fixture', type=Path)
    args, rest = parser.parse_known_args()
    torch.set_num_threads(2)
    result = unittest.main(argv=[sys.argv[0], *rest], exit=False)
    if not result.result.wasSuccessful(): sys.exit(1)
    if args.write_fixture: write_fixture(args.write_fixture)
