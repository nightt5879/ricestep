"""Compile/test portable pose C against saved LiteRT oracles; never access hardware."""

import argparse
import ctypes as c
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


class Span(c.Structure):
    _fields_ = [("data", c.POINTER(c.c_int8)), ("bytes", c.c_size_t)]


class Heads(c.Structure):
    _fields_ = [(name, Span) for name in ("center", "heatmap", "offsets", "regressions")]


class Landmark(c.Structure):
    _fields_ = [(name, c.c_float) for name in ("y", "x", "score")]


class Trace(c.Structure):
    _fields_ = [("center_index", c.c_uint16), ("joint_index", c.c_uint16 * 17),
                ("weighted_score", c.c_int8 * 17)]


class Frame(c.Structure):
    _fields_ = [("data", c.POINTER(c.c_uint8)), ("data_size", c.c_size_t),
                ("stride_bytes", c.c_size_t), ("width", c.c_uint32),
                ("height", c.c_uint32), ("format", c.c_int)]


class Transform(c.Structure):
    _fields_ = [(name, c.c_uint32) for name in ("source_width", "source_height",
                "resized_width", "resized_height", "pad_left", "pad_top")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-path", type=Path, required=True, help="Isolated Linux NumPy directory")
    parser.add_argument("--oracle-dir", type=Path, default=Path(".cache/pose-oracle/results"))
    parser.add_argument("--output-dir", type=Path, default=Path(".cache/pose-native"))
    parser.add_argument("--prepare-rgb", type=Path, help="Original RGB888 file to letterbox; no camera")
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    args = parser.parse_args()
    sys.path.insert(0, str(args.python_path.resolve()))
    import numpy as np
    outdir = args.output_dir.resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    if args.prepare_rgb:
        if not args.width or not args.height:
            parser.error("--prepare-rgb requires --width and --height")
        source = ROOT / "firmware/components/pose_image/pose_image.c"
        library_path = outdir / "libpose_image.so"
        subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wpedantic",
                        "-O2", "-shared", "-fPIC", str(source), "-lm", "-o", str(library_path)], check=True)
        lib = c.CDLL(str(library_path))
        lib.pi_prepare.argtypes = [c.POINTER(Frame), c.POINTER(c.c_int8), c.c_size_t, c.POINTER(Transform)]
        lib.pi_prepare.restype = c.c_int
        raw = args.prepare_rgb.read_bytes()
        if len(raw) != args.width * args.height * 3:
            parser.error("RGB source length does not match its dimensions")
        pixels = (c.c_uint8 * len(raw)).from_buffer_copy(raw)
        frame = Frame(pixels, len(raw), args.width * 3, args.width, args.height, 2)
        tensor = (c.c_int8 * (192 * 192 * 3))()
        transform = Transform()
        status = lib.pi_prepare(c.byref(frame), tensor, len(tensor), c.byref(transform))
        if status:
            raise RuntimeError(f"Native image preprocessing failed: {status}")
        # Full-model oracle accepts UINT8; external Micro input is UINT8−128.
        rgb = (np.ctypeslib.as_array(tensor).astype(np.int16) + 128).astype(np.uint8).tobytes()
        (outdir / "input-192.rgb").write_bytes(rgb)
        (outdir / "image-transform.json").write_text(json.dumps({
            **{name: int(getattr(transform, name)) for name, _ in Transform._fields_},
            "input_sha256": hashlib.sha256(raw).hexdigest(),
            "output_sha256": hashlib.sha256(rgb).hexdigest(),
            "resampling": "half-pixel-bilinear-integer-final-floor",
            "edge_mode": "clamp", "antialias": False,
            "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in (source, source.with_suffix('.h'))},
            "board_validated": False}, indent=2) + "\n")
        print("Native RGB conversion/letterbox complete; no camera was opened.")
        return

    directory = args.oracle_dir.resolve()
    manifest = json.loads((directory / "oracle-report.json").read_text())
    expected_hash = "cd7cc22fa946e5d146a7b98d496853e1923e22828d3972d579973f27f91bb105"
    if manifest["model_sha256"] != expected_hash or manifest["resolver"] != "BUILTIN_REF":
        raise ValueError("Oracle model/resolver contract differs")
    component = ROOT / "firmware/components/pose_decode"
    library_path = outdir / "libpose_decode.so"
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wpedantic",
                    "-O2", "-shared", "-fPIC", str(component / "pose_movenet.c"),
                    "-lm", "-o", str(library_path)], check=True)
    lib = c.CDLL(str(library_path))
    lib.pose_movenet_decode.argtypes = [c.POINTER(Heads), c.POINTER(Landmark), c.POINTER(Trace)]
    lib.pose_movenet_decode.restype = c.c_int
    results = []
    for case in manifest["cases"]:
        file = directory / case["file"]
        if file.parent != directory or file.suffix != ".npz":
            raise ValueError("Oracle case path must be a local .npz filename")
        if hashlib.sha256(file.read_bytes()).hexdigest() != case["sha256"]:
            raise ValueError("Oracle fixture hash mismatch")
        with np.load(file, allow_pickle=False) as arrays:
            values = [np.ascontiguousarray(arrays[f"head_{i}"]) for i in (259, 276, 282, 285)]
            shapes = [(1, 48, 48, channels) for channels in (1, 17, 34, 34)]
            if any(value.dtype != np.int8 or value.shape != shape for value, shape in zip(values, shapes)):
                raise ValueError("Oracle head tensor contract differs")
            heads = Heads(*(Span(value.ctypes.data_as(c.POINTER(c.c_int8)), value.nbytes) for value in values))
            landmarks, trace = (Landmark * 17)(), Trace()
            status = lib.pose_movenet_decode(c.byref(heads), landmarks, c.byref(trace))
            if status:
                raise RuntimeError(f"Native decoder rejected oracle case: {status}")
            actual = np.asarray([[point.y, point.x, point.score] for point in landmarks], np.float32)
            expected = arrays["expected"].reshape(17, 3)
            center_ok = int(trace.center_index) == int(np.argmax(arrays["trace_261"].reshape(-1)))
            indices = np.argmax(arrays["trace_303"].reshape(-1, 17), axis=0)
            joints_ok = np.array_equal(np.asarray(trace.joint_index), indices)
            result = {"name": case["name"], "exact_float32": bool(np.array_equal(actual, expected)),
                      "max_absolute_error": float(np.max(np.abs(actual - expected))),
                      "center_matches": center_ok, "joint_indices_match": bool(joints_ok)}
            results.append(result)
    passed = bool(results) and all(r["exact_float32"] and r["center_matches"] and r["joint_indices_match"] for r in results)
    report = {"passed": passed, "model_sha256": expected_hash, "oracle_cases": len(results),
              "oracle_report_sha256": hashlib.sha256((directory / "oracle-report.json").read_bytes()).hexdigest(),
              "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in component.iterdir() if p.suffix in (".c", ".h", ".inc")},
              "results": results, "board_validated": False, "human_accuracy_validated": False}
    (outdir / "decode-parity.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"Native decoder exact reference parity: {sum(r['exact_float32'] for r in results)}/{len(results)} cases.")
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
