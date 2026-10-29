"""Offline MoveNet shape experiment; never a production model or device command.

Requires the existing isolated Linux LiteRT/Micro packages and cached public-photo
fixtures. Does not download/install anything. Generated models stay under .cache.
See docs/pose-resolution-experiment.md for limits and the reproduction command.
"""

import argparse
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SOURCE_SHA = "cd7cc22fa946e5d146a7b98d496853e1923e22828d3972d579973f27f91bb105"
HEADS = (259, 276, 282, 285)
SIZES = (192, 128, 96, 64, 48, 32)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def code(model, op):
    entry = model.operatorCodes[op.opcodeIndex]
    return max(entry.builtinCode, entry.deprecatedBuiltinCode)


def pack(model):
    builder = flatbuffers.Builder(0)
    offset = model.Pack(builder)
    builder.Finish(offset, file_identifier=b"TFL3")
    return bytes(builder.Output())


def engine(data):
    result = Interpreter(model_content=data, num_threads=1,
                         experimental_op_resolver_type=OpResolverType.BUILTIN_REF,
                         experimental_preserve_all_tensors=True)
    result.allocate_tensors()
    return result


def modify(source, size):
    model = fb.ModelT.InitFromObj(fb.Model.GetRootAsModel(source, 0))
    original = fb.ModelT.InitFromObj(fb.Model.GetRootAsModel(source, 0))
    graph, grid = model.subgraphs[0], size // 4
    conv_types = (fb.BuiltinOperator.CONV_2D, fb.BuiltinOperator.DEPTHWISE_CONV_2D)
    weights = {int(i) for op in graph.operators if code(model, op) in conv_types
               for i in op.inputs[1:]}
    for index, tensor in enumerate(graph.tensors):
        data = model.buffers[tensor.buffer].data
        if data is not None and len(data):
            continue
        shape = tensor.shape.copy()
        if len(shape) == 4 and shape[1] > 1 and shape[1] == shape[2] and index <= 285:
            shape[1] = shape[2] = math.ceil(int(shape[1]) * size / 192)
        elif len(shape) == 4 and shape[1] == shape[2] == 48:
            shape[1] = shape[2] = grid
        elif len(shape) == 3 and shape[0] == shape[1] == 48:
            shape[0] = shape[1] = grid
        elif len(shape) == 3 and shape[0] == 1 and shape[1] == 2304:
            shape[1] = grid * grid
        tensor.shape = shape
        if tensor.shapeSignature is not None:
            tensor.shapeSignature = shape.copy()

    def constant(index, values, dtype):
        buffer = model.buffers[graph.tensors[index].buffer]
        buffer.data = np.frombuffer(np.asarray(values, dtype).tobytes(), np.uint8).copy()

    def quantized_constant(index, values):
        q = graph.tensors[index].quantization
        values = np.clip(np.round(np.asarray(values) / q.scale[0] + q.zeroPoint[0]), -128, 127)
        constant(index, values, np.int8)

    for index, old in ((1, 12), (2, 24), (3, 48)):
        constant(index, [size // (192 // old)] * 2, np.int32)
    constant(6, grid, np.int32)
    y, x = np.indices((grid, grid))
    for index in (154, 170, 171):
        graph.tensors[index].shape = np.array([grid, grid, 1], np.int32)
    # Inferred geometry rule: reconstructs all 2304 original bytes at grid48.
    quantized_constant(154, 1 / (np.sqrt((y - grid / 2) ** 2 + (x - grid / 2) ** 2) + 1.8))
    quantized_constant(170, y)
    quantized_constant(171, x)
    q = graph.tensors[173].quantization
    q.scale = q.scale * np.float32(48 / grid)  # Represent 1/grid instead of 1/48.
    model.signatureDefs = []
    for index in weights:
        before = original.buffers[original.subgraphs[0].tensors[index].buffer].data
        after = model.buffers[graph.tensors[index].buffer].data
        require(bytes(before) == bytes(after), f"Changed learned weight/bias {index}")
    for index, tensor in enumerate(graph.tensors):
        if index == 173 or tensor.quantization is None:
            continue
        before = original.subgraphs[0].tensors[index].quantization
        for name, value in vars(tensor.quantization).items():
            other = getattr(before, name)
            same = np.array_equal(value, other) if isinstance(value, np.ndarray) else value == other
            require(same, f"Changed quantization tensor {index} field {name}")
    if size == 192:
        for index in (1, 2, 3, 6, 154, 170, 171, 173):
            before = original.buffers[original.subgraphs[0].tensors[index].buffer].data
            after = model.buffers[graph.tensors[index].buffer].data
            require(bytes(before) == bytes(after), f"192 geometry constant mismatch {index}")
    return model, pack(model)


def macs(model, interpreter):
    details = {t["index"]: t for t in interpreter.get_tensor_details()}
    rows = []
    for op in model.subgraphs[0].operators:
        kind = code(model, op)
        if kind not in (fb.BuiltinOperator.CONV_2D, fb.BuiltinOperator.DEPTHWISE_CONV_2D):
            continue
        source = list(map(int, details[int(op.inputs[0])]["shape"]))
        output = list(map(int, details[int(op.outputs[0])]["shape"]))
        weights = model.subgraphs[0].tensors[op.inputs[1]].shape
        options = op.builtinOptions
        kh, kw = map(int, weights[1:3])
        dh, dw, sh, sw = options.dilationHFactor, options.dilationWFactor, options.strideH, options.strideW
        ph = max(0, (output[1] - 1) * sh + (kh - 1) * dh + 1 - source[1]) // 2
        pw = max(0, (output[2] - 1) * sw + (kw - 1) * dw + 1 - source[2]) // 2
        if options.padding == fb.Padding.VALID:
            ph = pw = 0
        else:
            require(options.padding == fb.Padding.SAME, "Unexpected padding")
        vh = sum(0 <= y * sh - ph + k * dh < source[1] for y in range(output[1]) for k in range(kh))
        vw = sum(0 <= x * sw - pw + k * dw < source[2] for x in range(output[2]) for k in range(kw))
        ordinary = kind == fb.BuiltinOperator.CONV_2D
        channels = int(weights[3]) if ordinary else 1
        rows.append((ordinary, math.prod(output) * kh * kw * channels,
                     output[0] * output[3] * vh * vw * channels))
    return {"nominal_macs": sum(r[1] for r in rows), "nonpadding_macs": sum(r[2] for r in rows),
            "ordinary_macs": sum(r[2] for r in rows if r[0])}


def letterbox(rgb, size):
    height, width = rgb.shape[:2]
    if width >= height:
        rw, rh = size, max(1, (height * size + width // 2) // width)
    else:
        rh, rw = size, max(1, (width * size + height // 2) // height)
    x = ((2 * np.arange(rw) + 1) * width) // (2 * rw)
    y = ((2 * np.arange(rh) + 1) * height) // (2 * rh)
    output = np.zeros((size, size, 3), np.uint8)
    output[(size - rh) // 2:(size - rh) // 2 + rh,
           (size - rw) // 2:(size - rw) // 2 + rw] = rgb[y[:, None], x[None, :]]
    return output[None]


def micro_compare(model, full, sample, output, size):
    from tflite_micro.python.tflite_micro import runtime
    graph, needed, kept = model.subgraphs[0], set(HEADS), []
    for op in reversed(graph.operators):
        if any(int(i) in needed for i in op.outputs):
            kept.append(op)
            needed.update(int(i) for i in op.inputs if i >= 0)
    kept = list(reversed(kept))
    require(code(model, kept[0]) == fb.BuiltinOperator.CAST and
            code(model, kept[1]) == fb.BuiltinOperator.QUANTIZE, "Unexpected input conversion")
    graph.operators = kept[2:]
    graph.inputs, graph.outputs = np.array([175], np.int32), np.array(HEADS, np.int32)
    data = pack(model)
    (output / f"experimental-heads-{size}.tflite").write_bytes(data)
    micro = runtime.Interpreter.from_bytes(data, arena_size=2097152)
    micro.set_input((sample.astype(np.int16) - 128).astype(np.int8), 0)
    micro.invoke()
    require(all(np.array_equal(micro.get_output(i), full.get_tensor(t)) for i, t in enumerate(HEADS)),
            "TFLite Micro / full LiteRT head mismatch")
    print(f"TFLM allocator for {size} input (capacity 2097152; actual usage below)", flush=True)
    micro.print_allocations()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=ROOT / ".cache/research-pose/movenet-lightning-int8-v4.download")
    parser.add_argument("--micro-path", type=Path, required=True, help="Isolated Linux Micro wheel directory")
    parser.add_argument("--litert-path", type=Path, required=True, help="Isolated Linux LiteRT2.3.0 wheel directory")
    parser.add_argument("--rgb", type=Path, default=ROOT / ".cache/pose-oracle/pose.rgb")
    parser.add_argument("--fixture-dir", type=Path, default=ROOT / ".cache/pose-real-audit")
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".cache/pose-resolution")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    require(output.is_relative_to((ROOT / ".cache").resolve()), "Output must stay under repository .cache")
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").unlink(missing_ok=True)  # Failed reruns must not retain a success manifest.
    sys.path[:0] = [str(args.litert_path.resolve()), str(args.micro_path.resolve())]
    global np, fb, flatbuffers, Interpreter, OpResolverType
    import numpy as np
    import flatbuffers
    from tflite_micro.tensorflow.lite.micro.python import schema_py_generated as fb
    from ai_edge_litert.interpreter import Interpreter, OpResolverType
    require(importlib.metadata.version("ai-edge-litert") == "2.3.0", "Expected pinned LiteRT2.3.0")
    require(importlib.metadata.version("tflite-micro") == "0.dev20261026171606", "Expected pinned Micro wheel")
    source = args.model.read_bytes()
    require(digest(source) == SOURCE_SHA, "Wrong source model; fixed tensor indices are unsafe")
    rgb_data = args.rgb.read_bytes()
    require(digest(rgb_data) == "9a2ec8b63d69e155163c83b5ea3bf7ce015bdfd4c115f23d294b4f11885755aa", "Wrong public RGB fixture")
    rgb = np.frombuffer(rgb_data, np.uint8).reshape(667, 1000, 3)
    fixture = (args.fixture_dir / "person-320x240.rgb565be").read_bytes()
    require(digest(fixture) == "80b5a1218ebbb2074a318dfdc557f21b16e397a0bbaea07e7300a36df2944558", "Wrong RGB565 fixture")
    value = np.frombuffer(fixture, dtype=">u2").reshape(240, 320)
    rgb565 = np.stack([((value >> 11) << 3) | ((value >> 11) >> 2),
                       (((value >> 5) & 63) << 2) | (((value >> 5) & 63) >> 4),
                       ((value & 31) << 3) | ((value & 31) >> 2)], axis=-1).astype(np.uint8)
    native_input = np.fromfile(args.fixture_dir / "native-input-int8.bin", np.int8).reshape(1, 192, 192, 3)
    require(np.array_equal(letterbox(rgb565, 192).astype(np.int16) - 128, native_input), "C preprocessing mismatch")
    base, references = engine(source), {}
    pictures = {"original": rgb, "rgb565_320x240": rgb565}
    for name, picture in pictures.items():
        base.set_tensor(0, letterbox(picture, 192))
        base.invoke()
        references[name] = base.get_tensor(332).copy()
    native_pose = np.fromfile(args.fixture_dir / "native-landmarks-f32.bin", np.float32)
    require(np.array_equal(references["rgb565_320x240"].reshape(-1), native_pose), "Source C++ baseline mismatch")
    report = {"production_changed": False, "training": False, "accuracy_validated": False,
              "sample_count_independent_photos": 1, "model_source_sha256": SOURCE_SHA,
              "baseline_source_C_TFLM_exact": True, "input_only": {}, "experiments": []}
    for size in (128, 96):
        try:
            resized = engine(source)
            resized.resize_tensor_input(0, [1, size, size, 3], strict=False)
            resized.allocate_tensors()
        except RuntimeError as error:
            report["input_only"][str(size)] = str(error)
            require("72 (ADD)" in str(error), "Input-only failure changed; re-inspect graph")
        else:
            raise ValueError("Input-only resize unexpectedly passed; re-inspect graph")
    for size in SIZES:
        model, data = modify(source, size)
        full = engine(data)
        item = {"input": size, "grid": size // 4, "sha256": digest(data), "bytes": len(data),
                "heads": {str(t): list(full.get_tensor(t).shape) for t in HEADS}, "cases": {}}
        item.update(macs(model, full))
        (output / f"experimental-{size}.tflite").write_bytes(data)
        for name, picture in pictures.items():
            sample = letterbox(picture, size)
            full.set_tensor(0, sample)
            full.invoke()
            pose = full.get_tensor(332).copy()
            require(pose.shape == (1, 1, 17, 3) and np.isfinite(pose).all(), "Invalid 17-point result")
            if size == 192:
                require(np.array_equal(pose, references[name]), "Identity transform output mismatch")
            np.savez(output / f"{name}-{size}.npz", pose=pose,
                     **{f"head_{t}": full.get_tensor(t).copy() for t in HEADS})
            distance = np.linalg.norm((pose - references[name]).reshape(17, 3)[:, :2], axis=1)
            item["cases"][name] = {"median_score": float(np.median(pose[..., 2])),
                "joints_ge_06": int(np.sum(pose[..., 2] >= .6)),
                "mean_coord_distance_vs_192": float(distance.mean()),
                "max_coord_distance_vs_192": float(distance.max()), "points": pose.reshape(17, 3).tolist()}
        if size in (96, 48, 32):
            micro_compare(model, full, letterbox(rgb565, size), output, size)
            item["tflm_four_heads_exact"] = True
        item["invoke"] = "passed"
        report["experiments"].append(item)
        print(f"Input {size}: {item['nonpadding_macs']} nonpadding MAC; full invoke passed", flush=True)
    # Emit success manifest only when every requested experiment completed.
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
