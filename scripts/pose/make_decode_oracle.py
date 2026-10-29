"""Build reproducible MoveNet postprocess oracles; no camera, audio or board I/O."""

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys

SOURCE_SHA256 = "cd7cc22fa946e5d146a7b98d496853e1923e22828d3972d579973f27f91bb105"
HEADS = (259, 276, 282, 285)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--micro-path", type=Path, required=True)
    parser.add_argument("--litert-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path(".cache/pose-oracle/results"))
    parser.add_argument("--random-cases", type=int, default=16)
    parser.add_argument("--rgb-input", type=Path, action="append", default=[],
                        help="Optional preprocessed 192x192 RGB888 raw file, never a camera")
    parser.add_argument("--micro-heads", type=Path,
                        help="Optional pinned four-head model to exercise the real Micro CPU interpreter")
    args = parser.parse_args()
    if not 0 <= args.random_cases <= 128:
        parser.error("--random-cases must be between 0 and 128")
    source = args.model.read_bytes()
    if hashlib.sha256(source).hexdigest() != SOURCE_SHA256:
        parser.error("Unexpected model; fixed tensor indices only support the pinned v4 artifact")
    sys.path[:0] = [str(args.litert_path.resolve()), str(args.micro_path.resolve())]
    import flatbuffers
    import numpy as np
    from tflite_micro.tensorflow.lite.micro.python import schema_py_generated as fb
    from ai_edge_litert.interpreter import Interpreter, OpResolverType
    if importlib.metadata.version("ai-edge-litert") != "2.3.0":
        raise ValueError("This reference oracle requires pinned ai-edge-litert==2.3.0")

    def original_model():
        return fb.ModelT.InitFromObj(fb.Model.GetRootAsModel(source, 0))

    def slice_model(inputs, outputs):
        model = original_model()
        graph = model.subgraphs[0]
        boundary, needed, kept = set(inputs), set(outputs), []
        for op in reversed(graph.operators):
            if any(int(index) in needed - boundary for index in op.outputs):
                kept.append(op)
                needed.update(int(index) for index in op.inputs if index >= 0)
        graph.operators = list(reversed(kept))
        produced = {int(index) for op in kept for index in op.outputs}
        for index in needed - produced - boundary:
            data = model.buffers[graph.tensors[index].buffer].data
            if data is None or len(data) == 0:
                raise ValueError(f"Unbound nonconstant tensor {index}")
        graph.inputs = np.asarray(inputs, dtype=np.int32)
        graph.outputs = np.asarray(outputs, dtype=np.int32)
        model.signatureDefs = []
        builder = flatbuffers.Builder(0)
        offset = model.Pack(builder)
        builder.Finish(offset, file_identifier=b"TFL3")
        return bytes(builder.Output())

    def interpreter(artifact):
        # Reference builtins make the arithmetic contract explicit. No default
        # delegates may replace nodes or discard the intermediate tensors.
        result = Interpreter(model_content=artifact, num_threads=1,
                             experimental_op_resolver_type=OpResolverType.BUILTIN_REF,
                             experimental_preserve_all_tensors=True)
        result.allocate_tensors()
        return result

    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    unary = {}
    for input_id, output_id in ((259, 260), (276, 277), (296, 299), (299, 300)):
        engine = interpreter(slice_model([input_id], [output_id]))
        detail = engine.get_input_details()[0]
        values = np.resize(np.arange(-128, 128, dtype=np.int8), detail["shape"])
        engine.set_tensor(input_id, values)
        engine.invoke()
        actual = engine.get_tensor(output_id)
        unary[f"{input_id}_{output_id}"] = actual.reshape(-1)[:256].astype(int).tolist()
    (output / "unary-luts.json").write_text(json.dumps(unary, indent=2) + "\n")

    tail_artifact = slice_model(HEADS, [332])
    (output / "postprocess-reference.tflite").write_bytes(tail_artifact)
    tail = interpreter(tail_artifact)
    graph = original_model().subgraphs[0]
    head_shapes = {index: tuple(int(x) for x in graph.tensors[index].shape) for index in HEADS}
    cases = []

    def save_case(name, heads, expected=None, provenance="synthetic head tensors"):
        for index in HEADS:
            tail.set_tensor(index, heads[index])
        tail.invoke()
        actual = tail.get_tensor(332)
        if expected is not None and not np.array_equal(actual, expected):
            raise AssertionError(f"Cut tail disagrees with full reference model: {name}")
        trace = {}
        traced = {260, 261, 266, 271, 277, 283, 287, 288, 289, 290, 291, 292,
                  293, 294, 295, 296, 297, 298, 299, 300, 301, 302, 303, 331, 332}
        tail_graph = fb.Model.GetRootAsModel(tail_artifact, 0).Subgraphs(0)
        produced = {int(index) for op in range(tail_graph.OperatorsLength())
                    for index in tail_graph.Operators(op).OutputsAsNumpy()}
        for index in sorted(traced & produced):
            trace[str(index)] = tail.get_tensor(index)
        payload = {f"head_{index}": values for index, values in heads.items()}
        payload.update({"expected": actual, **{f"trace_{key}": value for key, value in trace.items()}})
        target = output / f"{name}.npz"
        np.savez(target, **payload)
        cases.append({"name": name, "file": target.name,
                      "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                      "source": provenance})
        return actual

    rng = np.random.default_rng(20261029)
    for value in (-128, -1, 0, 127):
        save_case(f"heads-constant-{value}", {i: np.full(s, value, np.int8) for i, s in head_shapes.items()})
    for case in range(args.random_cases):
        save_case(f"heads-random-{case:02d}", {i: rng.integers(-128, 128, size=s, dtype=np.int8)
                                               for i, s in head_shapes.items()})

    full = interpreter(source)
    pixels = np.arange(192 * 192 * 3, dtype=np.uint32).reshape(1, 192, 192, 3)
    samples = {"gray": np.full((1, 192, 192, 3), 128, np.uint8),
               "gradient": (pixels % 256).astype(np.uint8),
               "noise": rng.integers(0, 256, size=(1, 192, 192, 3), dtype=np.uint8)}
    extra_inputs = []
    for index, file in enumerate(args.rgb_input):
        raw = file.read_bytes()
        if len(raw) != 192 * 192 * 3:
            raise ValueError("--rgb-input must contain exactly 192x192 RGB888 bytes")
        name = f"rgb-{index:02d}"
        samples[name] = np.frombuffer(raw, np.uint8).reshape(1, 192, 192, 3)
        extra_inputs.append({"case": f"full-{name}", "sha256": hashlib.sha256(raw).hexdigest()})
    micro_engine = None
    micro_comparisons = []
    if args.micro_heads:
        micro_artifact = args.micro_heads.read_bytes()
        if hashlib.sha256(micro_artifact).hexdigest() != "9051a5ceebc1eb5f535c1b1afe58d33ce878bf9358a159cbfc689c1abebf9c68":
            raise ValueError("Unexpected Micro heads artifact; run the pinned heads probe first")
        from tflite_micro.python.tflite_micro import runtime
        micro_engine = runtime.Interpreter.from_bytes(micro_artifact, arena_size=2097152)
    for name, sample in samples.items():
        full.set_tensor(int(full.get_input_details()[0]["index"]), sample)
        full.invoke()
        full_heads = {i: full.get_tensor(i) for i in HEADS}
        full_pose = full.get_tensor(332)
        save_case(f"full-{name}", full_heads, full_pose,
                  "full model supplied RGB" if name.startswith("rgb-") else "full model synthetic RGB")
        if micro_engine is not None:
            micro_engine.set_input((sample.astype(np.int16) - 128).astype(np.int8), 0)
            micro_engine.invoke()
            micro_heads = {index: micro_engine.get_output(i).copy() for i, index in enumerate(HEADS)}
            micro_pose = save_case(f"micro-{name}", micro_heads, provenance="real TFLite Micro CPU head outputs")
            micro_comparisons.append({"sample": name,
                "head_max_int8_difference": {str(i): int(np.max(np.abs(micro_heads[i].astype(np.int16) - full_heads[i].astype(np.int16)))) for i in HEADS},
                "final_pose_exact": bool(np.array_equal(micro_pose, full_pose)),
                "pose_max_absolute_difference": float(np.max(np.abs(micro_pose - full_pose)))})
    if micro_engine is not None:
        micro_engine.print_allocations()
    report = {"model_sha256": SOURCE_SHA256, "runtime": "ai-edge-litert==2.3.0",
              "resolver": "BUILTIN_REF", "delegates": False, "seed": 20261029,
              "postprocess_sha256": hashlib.sha256(tail_artifact).hexdigest(),
              "cases": cases, "extra_rgb_inputs": extra_inputs, "full_model_tail_parity": True,
              "micro_cpu_comparisons": micro_comparisons,
              "board_validated": False, "human_pose_accuracy_validated": False}
    (output / "oracle-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"Saved {len(cases)} reference cases and four exact unary tables; no hardware/accuracy claim.")


if __name__ == "__main__":
    main()
