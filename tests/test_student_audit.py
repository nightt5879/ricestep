"""Audit controls must not leak held-out labels or accept changed provenance."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/pose"))
from audit_student import (MOBILE_ARCHITECTURE, SOURCE_NAMES, TINY_ARCHITECTURE,
                           architecture_id, derangement, load_snapshot_model,
                           training_template, verify_inputs)


class StudentAuditTests(unittest.TestCase):
    def fixture_sources(self, architecture=MOBILE_ARCHITECTURE, *, mobile_failure=False):
        base = b'''import torch
ARCHITECTURE = "ricestep-tiny-pose-v1"
INPUT_SIZE, GRID_SIZE = 96, 24
OFFSET_LAYOUT = "interleaved_yx_grid_units"
MARKER = "verified historical base"
class TinyPose(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.origin = MARKER
'''
        mobile = (b'from student_model import TinyPose, MARKER\n'
                  b'ARCHITECTURE = "ricestep-mobilenet-v1-025-pose-v1"\n'
                  b'INPUT_SIZE, GRID_SIZE = 96, 24\n'
                  b'OFFSET_LAYOUT = "interleaved_yx_grid_units"\n'
                  b'class MobileNetPose(TinyPose): pass\n')
        if mobile_failure:
            mobile += b'raise RuntimeError("mobile snapshot failure")\n'
        sources = {"student_model.py": base, "student_data.py": b"INPUT, GRID = 96, 24\n",
                   "train_student.py": b"# provenance-only fixture, never executed\n"}
        if architecture == MOBILE_ARCHITECTURE:
            sources["mobilenet_student.py"] = mobile
        state = {"schema": 1, "epoch": 3, "manifest_sha256": "manifest", "model": {}}
        training = {"best_epoch": 3, "manifest_sha256": "manifest",
                    "source_sha256": {name: hashlib.sha256(raw).hexdigest() for name, raw in sources.items()}}
        if architecture != TINY_ARCHITECTURE:
            training["architecture"] = state["architecture"] = architecture
        buffer = io.BytesIO()
        torch.save(state, buffer)
        raw = buffer.getvalue()
        training["best_checkpoint_sha256"] = hashlib.sha256(raw).hexdigest()
        return training, raw, sources

    def test_historical_tiny_architecture_default_and_unknown_rejection(self):
        self.assertEqual(architecture_id({}), TINY_ARCHITECTURE)
        training, raw, sources = self.fixture_sources(TINY_ARCHITECTURE)
        model, _, state = load_snapshot_model(training, raw, sources, Path("unused"))
        self.assertEqual(model.origin, "verified historical base")
        self.assertNotIn("architecture", state)
        for unknown in (None, "mobilenet", "unknown", [TINY_ARCHITECTURE]):
            with self.subTest(unknown=unknown), self.assertRaisesRegex(ValueError, "Unknown student architecture"):
                architecture_id({"architecture": unknown})

    def test_mobile_imports_verified_base_and_restores_existing_module(self):
        training, raw, sources = self.fixture_sources()
        current = types.ModuleType("student_model")
        current.MARKER = "wrong current working tree"
        with patch.dict(sys.modules, {"student_model": current}):
            model, _, state = load_snapshot_model(training, raw, sources, Path("unused"))
            self.assertEqual(model.origin, "verified historical base")
            self.assertEqual(state["architecture"], MOBILE_ARCHITECTURE)
            self.assertIs(sys.modules["student_model"], current)

    def test_mobile_base_binding_removed_when_previously_absent_and_on_failure(self):
        # Both success and exception must restore the exact prior import state.
        previous = sys.modules.pop("student_model", None)
        try:
            for fail in (False, True):
                training, raw, sources = self.fixture_sources(mobile_failure=fail)
                if fail:
                    with self.assertRaisesRegex(RuntimeError, "mobile snapshot failure"):
                        load_snapshot_model(training, raw, sources, Path("unused"))
                else:
                    load_snapshot_model(training, raw, sources, Path("unused"))
                self.assertNotIn("student_model", sys.modules)
        finally:
            if previous is not None:
                sys.modules["student_model"] = previous

    def test_missing_mobile_source_and_checkpoint_architecture_mismatch_rejected(self):
        training, raw, sources = self.fixture_sources()
        removed = sources.pop("mobilenet_student.py")
        with self.assertRaisesRegex(ValueError, "verified training source missing"):
            load_snapshot_model(training, raw, sources, Path("unused"))
        sources["mobilenet_student.py"] = removed
        state = torch.load(io.BytesIO(raw), weights_only=True)
        state.pop("architecture")  # Missing means Tiny, never silently Mobile.
        buffer = io.BytesIO(); torch.save(state, buffer); raw = buffer.getvalue()
        training["best_checkpoint_sha256"] = hashlib.sha256(raw).hexdigest()
        with self.assertRaisesRegex(ValueError, "provenance or architecture"):
            load_snapshot_model(training, raw, sources, Path("unused"))

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
            report["status"] = "completed"
            report["architecture"] = "unknown"
            (root / "training-report.json").write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError, "Unknown student architecture"):
                verify_inputs(checkpoint, manifest)
            report["architecture"] = MOBILE_ARCHITECTURE
            (root / "training-report.json").write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError, "snapshot missing: mobilenet_student.py"):
                verify_inputs(checkpoint, manifest)
            (saved / "mobilenet_student.py").write_bytes(b"# exists but lacks a recorded hash")
            with self.assertRaisesRegex(ValueError, "snapshot hash mismatch: mobilenet_student.py"):
                verify_inputs(checkpoint, manifest)


if __name__ == "__main__":
    unittest.main()
