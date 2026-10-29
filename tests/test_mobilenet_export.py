"""Full-pose MobileNet export parity and architecture isolation, CPU only."""
import argparse
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/pose"))
from student_export import (ARCHITECTURE, MOBILE_ARCHITECTURE, build_keras_model,
                            check_parity, export_models, load_weights, tf)


class MobileExportTests(unittest.TestCase):
    def test_whole_model_matches_torch_reference(self):
        model, mapping = build_keras_model(MOBILE_ARCHITECTURE)
        metadata = load_weights(model, mapping, OPTIONS.fixture / "weights.npz", MOBILE_ARCHITECTURE)
        self.assertEqual(metadata["architecture"], MOBILE_ARCHITECTURE)
        report, _ = check_parity(model, OPTIONS.fixture / "parity.npz")
        self.assertGreater(report["count"], 0)

    def test_architecture_selection_never_silently_accepts_another_model(self):
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            build_keras_model("unknown-model")
        for selected in (ARCHITECTURE, MOBILE_ARCHITECTURE):
            model, mapping = build_keras_model(selected)
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                # Tiny is the explicit default contract, even for a Mobile mapping.
                load_weights(model, mapping, OPTIONS.fixture / "weights.npz")

    def test_float_and_real_calibrated_int8_export(self):
        with tempfile.TemporaryDirectory() as directory:
            report = export_models(OPTIONS.fixture / "weights.npz", directory,
                architecture=MOBILE_ARCHITECTURE,
                representative=OPTIONS.representative,
                representative_metadata=OPTIONS.representative_metadata,
                parity_reference=OPTIONS.fixture / "parity.npz")
            self.assertEqual(report["architecture"], MOBILE_ARCHITECTURE)
            self.assertIsNotNone(report["torch_parity"])
            self.assertEqual(report["float"]["input"]["dtype"], "float32")
            self.assertEqual(report["int8"]["input"]["dtype"], "int8")
            self.assertEqual({o["role"] for o in report["int8"]["outputs"]}, {"logits", "offsets"})
            self.assertTrue(all(o["dtype"] == "int8" for o in report["int8"]["outputs"]))
            self.assertFalse(report["calibration"]["synthetic"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--representative", type=Path, required=True)
    parser.add_argument("--representative-metadata", type=Path, required=True)
    OPTIONS, rest = parser.parse_known_args()
    tf.config.threading.set_intra_op_parallelism_threads(2)
    tf.config.threading.set_inter_op_parallelism_threads(1)
    unittest.main(argv=[sys.argv[0], *rest])
