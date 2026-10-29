"""Coordinate/decode and dataset-isolation contracts for training-only helpers."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/pose"))
from student_data import FLIP, decode, load_manifest, preload_images, sample, targets


class StudentDataTest(unittest.TestCase):
    def test_decoded_cache_is_identical_and_avoids_repeat_io(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.jpg"
            pixels = np.random.default_rng(82).integers(0, 256, (80, 120, 3), dtype=np.uint8)
            Image.fromarray(pixels).save(path)
            row = {"local_path": path, "width": 120, "height": 80, "image_id": 1,
                   "bbox_xywh": [20, 10, 60, 60], "keypoints_xyv": [50, 40, 2] * 17}
            expected = [sample(row, augment=True, seed=seed) for seed in range(8)]
            dataset = {"train": [row], "val": []}
            report = preload_images(dataset, 80 * 120 * 3)
            self.assertEqual(report["decoded_bytes"], 28800)
            self.assertFalse(row["_pixels"].flags.writeable)
            with patch("student_data.Image.open", side_effect=AssertionError("Repeated image decode")):
                for seed, reference in enumerate(expected):
                    actual = sample(row, augment=True, seed=seed)
                    for key in reference:
                        np.testing.assert_array_equal(actual[key], reference[key])
            with self.assertRaisesRegex(ValueError, "already contains"):
                preload_images(dataset, 28800)

    def test_cache_budget_and_decode_failure_leave_no_partial_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.png"
            Image.new("RGB", (10, 10)).save(path)
            row = {"local_path": path, "width": 10, "height": 10}
            missing = {**row, "local_path": Path(temporary) / "missing.png"}
            dataset = {"train": [row], "val": [missing]}
            with patch("student_data.Image.open", side_effect=AssertionError("Must preflight budget")):
                with self.assertRaisesRegex(ValueError, "above cache budget"):
                    preload_images(dataset, 599)
            with self.assertRaises(FileNotFoundError):
                preload_images(dataset, 600)
            self.assertNotIn("_pixels", row)
            self.assertNotIn("_pixels", missing)
            for bad in (0, -1, 1.5, True, 2 * 1024**3 + 1):
                with self.assertRaises(ValueError):
                    preload_images({"train": [row], "val": []}, bad)

    def test_local_residuals_reconstruct_subpixel_targets(self):
        rng = np.random.default_rng(26029)
        for _ in range(50):
            points = rng.uniform(0.1, 23.8, (17, 2)).astype(np.float32)
            heat, offsets, _ = targets(points, np.ones(17, dtype=np.float32))
            logits = np.log(np.clip(heat, 1e-7, 1 - 1e-7) / (1 - np.clip(heat, 1e-7, 1 - 1e-7)))
            result = decode(logits[None], offsets[None])[0]
            np.testing.assert_allclose(result[:, :2], points, atol=1e-6, rtol=0)
        self.assertEqual(decode(logits[None], offsets[None]).shape, (1, 17, 3))

    def test_invalid_heads_reject(self):
        good = np.zeros((1, 17, 24, 24), np.float32)
        offsets = np.zeros((1, 34, 24, 24), np.float32)
        with self.assertRaises(ValueError):
            decode(good[:, :16], offsets)
        good[0, 0, 0, 0] = np.nan
        with self.assertRaises(ValueError):
            decode(good, offsets)

    def test_masked_joints_have_no_supervision(self):
        heat, _, mask = targets(np.full((17, 2), 12, np.float32), np.zeros(17, np.float32))
        self.assertEqual(float(heat.sum()), 0)
        self.assertEqual(float(mask.sum()), 0)
        np.testing.assert_array_equal(FLIP[FLIP], np.arange(17))

    def test_crop_geometry_and_image_split_integrity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "test.png"
            Image.new("RGB", (200, 100), (255, 128, 0)).save(image)
            raw = image.read_bytes()
            points = [[100 + (k % 3), 50 + (k % 2), 2] for k in range(17)]
            row = {"image_id": 1, "annotation_id": 1, "split": "train", "width": 200, "height": 100,
                   "image_path": "test.png", "image_sha256": hashlib.sha256(raw).hexdigest(),
                   "image_bytes": len(raw), "image_downloaded": True, "iscrowd": 0,
                   "bbox_xywh": [50, 25, 100, 50], "keypoints_xyv": np.array(points).flatten().tolist()}
            validation_image = root / "validation.png"
            Image.new("RGB", (200, 100), (250, 120, 10)).save(validation_image)
            validation_raw = validation_image.read_bytes()
            validation = {**row, "image_id": 2, "annotation_id": 2, "split": "val",
                          "image_path": "validation.png", "image_bytes": len(validation_raw),
                          "image_sha256": hashlib.sha256(validation_raw).hexdigest()}
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"train": [row], "val": [validation]}))
            data = load_manifest(manifest)
            s = sample(data["train"][0])
            np.testing.assert_allclose(s["points"][0], [12, 12], atol=1e-6)
            self.assertEqual(s["input"].shape, (3, 96, 96))
            self.assertTrue((s["valid"] == 1).all())
            self.assertTrue(np.isfinite(s["input"]).all())
            self.assertGreaterEqual(float(s["input"].min()), -1)
            self.assertLessEqual(float(s["input"].max()), 1)
            np.testing.assert_array_equal(sample(data["train"][0], augment=True, seed=7)["input"],
                                          sample(data["train"][0], augment=True, seed=7)["input"])
            # Hash-correct but cross-split image identifiers are still forbidden.
            validation["image_id"] = 1
            manifest.write_text(json.dumps({"train": [row], "val": [validation]}))
            with self.assertRaisesRegex(ValueError, "Image repeated"):
                load_manifest(manifest)
            validation["image_id"] = 2
            # Renaming an identical file must not bypass split isolation.
            duplicate = {**row, "image_id": 2, "annotation_id": 2, "split": "val"}
            manifest.write_text(json.dumps({"train": [row], "val": [duplicate]}))
            with self.assertRaisesRegex(ValueError, "Identical image bytes"):
                load_manifest(manifest)
            row["image_sha256"] = "0" * 64
            manifest.write_text(json.dumps({"train": [row], "val": [validation]}))
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                load_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
