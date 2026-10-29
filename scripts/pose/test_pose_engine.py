"""Run the compiled native wrapper against verified full-model oracle fixtures.

Only reads files and runs the supplied host executable; no camera/device I/O.
NumPy can be supplied from the existing isolated oracle dependency directory.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HEADS_SHA = "9051a5ceebc1eb5f535c1b1afe58d33ce878bf9358a159cbfc689c1abebf9c68"
SOURCE_SHA = "cd7cc22fa946e5d146a7b98d496853e1923e22828d3972d579973f27f91bb105"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--oracle-dir", type=Path, required=True)
    parser.add_argument("--python-path", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path(".cache/pose-engine-fixtures"))
    args = parser.parse_args()
    if args.python_path:
        sys.path.insert(0, str(args.python_path.resolve()))
    import numpy as np
    if hashlib.sha256(args.model.read_bytes()).hexdigest() != HEADS_SHA:
        parser.error("Pinned four-head model SHA mismatch")
    report = json.loads((args.oracle_dir / "oracle-report.json").read_text())
    if report["model_sha256"] != SOURCE_SHA or report["runtime"] != "ai-edge-litert==2.3.0" or not report["full_model_tail_parity"]:
        parser.error("Expected verified pinned LiteRT full-model oracle report")
    # Reproduce the original RNG stream, including preceding random-head draws.
    rng = np.random.default_rng(report["seed"])
    shapes = ((1, 48, 48, 1), (1, 48, 48, 17), (1, 48, 48, 34), (1, 48, 48, 34))
    for case in report["cases"]:
        if case["name"].startswith("heads-random-"):
            for shape in shapes:
                rng.integers(-128, 128, size=shape, dtype=np.int8)
    samples = {
        "gray": np.full((1, 192, 192, 3), 128, np.uint8),
        "gradient": (np.arange(192 * 192 * 3, dtype=np.uint32) % 256).astype(np.uint8),
        "noise": rng.integers(0, 256, size=(1, 192, 192, 3), dtype=np.uint8),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    fixtures = []
    for name, sample in samples.items():
        case = next(c for c in report["cases"] if c["name"] == f"full-{name}")
        source = args.oracle_dir / case["file"]
        if hashlib.sha256(source.read_bytes()).hexdigest() != case["sha256"]:
            raise ValueError(f"Oracle fixture hash mismatch: {name}")
        with np.load(source, allow_pickle=False) as oracle:
            data = (sample.astype(np.int16) - 128).astype(np.int8).tobytes()
            for index, shape in zip((259, 276, 282, 285), shapes):
                array = oracle[f"head_{index}"]
                if array.shape != shape or array.dtype != np.int8:
                    raise ValueError(f"Unexpected head tensor: {name}/{index}")
                data += array.tobytes()
            expected = oracle["expected"]
            if expected.shape != (1, 1, 17, 3) or expected.dtype != np.float32:
                raise ValueError("Unexpected landmark tensor")
            data += expected.astype("<f4").tobytes()
        target = args.output_dir / f"{name}.bin"
        target.write_bytes(data)
        fixtures.append(str(target.resolve()))
    subprocess.run([str(args.executable.resolve()), str(args.model.resolve()), *fixtures], check=True)


if __name__ == "__main__":
    main()
