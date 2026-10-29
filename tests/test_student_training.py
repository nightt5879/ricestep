"""Training loss must favor located joints and reject arbitrary confidence shifts."""
from pathlib import Path
import sys
import unittest

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/pose"))
from student_data import targets
from train_student import objective


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


if __name__ == "__main__":
    unittest.main()
