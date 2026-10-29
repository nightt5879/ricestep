"""Audit controls must not leak held-out labels or accept changed provenance."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/pose"))
from audit_student import SOURCE_NAMES, derangement, training_template, verify_inputs


class StudentAuditTests(unittest.TestCase):
    def test_template_never_reads_validation_rows_or_invalid_labels(self):
        first = {"split": "train", "points": np.full((17, 2), 2, np.float32),
                 "valid": np.ones(17, np.float32)}
        second = {"split": "train", "points": np.full((17, 2), 6, np.float32),
                  "valid": np.ones(17, np.float32)}
        second["valid"][0] = 0
        second["points"][0] = 10000  # An unlabelled coordinate must not enter the mean.
        def only_training(row, *, augment):
            self.assertFalse(augment)
            self.assertEqual(row["split"], "train", "Template accessed validation data")
            return row
        dataset = {"train": [first, second], "val": [{"split": "val"}]}
        template, count = training_template(dataset, only_training)
        np.testing.assert_array_equal(template[0], [2, 2])
        np.testing.assert_array_equal(template[1:], np.full((16, 2), 4))
        np.testing.assert_array_equal(count, [1] + [2] * 16)
        dataset["val"] = [object()]  # Even unreadable held-out rows cannot affect the template.
        np.testing.assert_array_equal(training_template(dataset, only_training)[0], template)

    def test_template_rejects_unobserved_training_joint(self):
        data = {"train": [{"points": np.zeros((17, 2)), "valid": np.zeros(17)}]}
        with self.assertRaisesRegex(ValueError, "all 17 joints"):
            training_template(data, lambda row, **kwargs: row)

    def test_shuffle_is_seeded_bijection_without_fixed_points(self):
        for size in (2, 3, 16, 256):
            for seed in (26029, 17):
                permutation = derangement(size, seed)
                np.testing.assert_array_equal(np.sort(permutation), np.arange(size))
                self.assertTrue(np.all(permutation != np.arange(size)))
                np.testing.assert_array_equal(permutation, derangement(size, seed))
        with self.assertRaises(ValueError):
            derangement(1)

    def test_changed_sources_checkpoint_or_manifest_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            saved = root / "source-snapshot"
            saved.mkdir()
            checkpoint, manifest = root / "best.pt", root / "manifest.json"
            checkpoint.write_bytes(b"not executed; provenance test only")
            manifest.write_bytes(b'{"train":[],"val":[]}')
            hashes = {}
            for name in SOURCE_NAMES:
                raw = b"raise RuntimeError('snapshot must not execute during provenance check')\n"
                (saved / name).write_bytes(raw)
                hashes[name] = hashlib.sha256(raw).hexdigest()
            report = {"status": "completed", "source_sha256": hashes,
                      "best_checkpoint_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
                      "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest()}
            (root / "training-report.json").write_text(json.dumps(report))
            self.assertEqual(verify_inputs(checkpoint, manifest)[0], report)
            for path, message in ((saved / "student_data.py", "snapshot hash mismatch"),
                                  (checkpoint, "best checkpoint"), (manifest, "Manifest differs")):
                original = path.read_bytes()
                path.write_bytes(original + b"changed")
                with self.subTest(path=path.name), self.assertRaisesRegex(ValueError, message):
                    verify_inputs(checkpoint, manifest)
                path.write_bytes(original)
            report["status"] = "running"
            (root / "training-report.json").write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError, "not completed"):
                verify_inputs(checkpoint, manifest)


if __name__ == "__main__":
    unittest.main()
