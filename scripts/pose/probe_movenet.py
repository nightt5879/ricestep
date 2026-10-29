"""Inspect or CPU-probe the pinned MoveNet model. Never accesses a camera/device."""

import argparse
import collections
import hashlib
import json
from pathlib import Path
import sys


SOURCE_SHA256 = "cd7cc22fa946e5d146a7b98d496853e1923e22828d3972d579973f27f91bb105"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--python-path", type=Path, help="Isolated Linux wheel extraction directory")
    parser.add_argument("--mode", choices=("inspect", "full", "int8-input", "heads"), default="inspect")
    parser.add_argument("--arena-bytes", type=int, default=2 * 1024 * 1024)
    parser.add_argument("--output-dir", type=Path, default=Path(".cache/pose-probe"))
    args = parser.parse_args()
    if args.python_path:
        sys.path.insert(0, str(args.python_path.resolve()))
    import flatbuffers
    import numpy as np
    from tflite_micro.tensorflow.lite.micro.python import schema_py_generated as fb

    original = args.model.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    if digest != SOURCE_SHA256:
        parser.error("Model hash differs from the inspected v4 artifact; refusing fixed tensor-index transforms.")
    model = fb.ModelT.InitFromObj(fb.Model.GetRootAsModel(original, 0))
    graph = model.subgraphs[0]
    op_names = {v: k for k, v in vars(fb.BuiltinOperator).items() if isinstance(v, int)}
    type_names = {v: k for k, v in vars(fb.TensorType).items() if isinstance(v, int)}

    def code(op):
        return max(model.operatorCodes[op.opcodeIndex].builtinCode,
                   model.operatorCodes[op.opcodeIndex].deprecatedBuiltinCode)

    def describe(index):
        tensor = graph.tensors[int(index)]
        return {"index": int(index), "shape": tensor.shape.tolist(), "dtype": type_names[tensor.type]}

    report = {
        "source_sha256": digest, "source_bytes": len(original), "mode": args.mode,
        "board_measurement": False, "pose_accuracy_validated": False,
        "input": [describe(i) for i in graph.inputs],
        "output": [describe(i) for i in graph.outputs],
        "operator_count": len(graph.operators),
        "operators": dict(sorted(collections.Counter(op_names[code(op)] for op in graph.operators).items())),
        "tensor_dtypes": dict(collections.Counter(type_names[t.type] for t in graph.tensors)),
    }
    if args.mode == "inspect":
        print(json.dumps(report, indent=2))
        return 0
    if args.arena_bytes <= 0:
        parser.error("--arena-bytes must be positive")

    if args.mode in ("int8-input", "heads"):
        if not (code(graph.operators[0]) == fb.BuiltinOperator.CAST
                and code(graph.operators[1]) == fb.BuiltinOperator.QUANTIZE
                and graph.operators[1].outputs.tolist() == [175]
                and graph.tensors[175].quantization.scale.tolist() == [1.0]
                and graph.tensors[175].quantization.zeroPoint.tolist() == [-128]):
            raise ValueError("Unexpected input conversion; refusing transformation")
        if args.mode == "heads":
            needed = {259, 276, 282, 285}
            kept = []
            for op in reversed(graph.operators):
                if any(int(i) in needed for i in op.outputs):
                    kept.append(op)
                    needed.update(int(i) for i in op.inputs if i >= 0)
            graph.operators = list(reversed(kept))
            graph.outputs = np.array([259, 276, 282, 285], dtype=np.int32)
        graph.operators = graph.operators[2:]
        graph.inputs = np.array([175], dtype=np.int32)
        model.signatureDefs = []
        builder = flatbuffers.Builder(0)
        offset = model.Pack(builder)
        builder.Finish(offset, file_identifier=b"TFL3")
        artifact = bytes(builder.Output())
    else:
        artifact = original

    report.update({"artifact_sha256": hashlib.sha256(artifact).hexdigest(),
                   "artifact_bytes": len(artifact), "arena_capacity_bytes": args.arena_bytes,
                   "probe_operator_count": len(graph.operators),
                   "probe_output": [describe(i) for i in graph.outputs]})
    macs = 0
    for op in graph.operators:
        if code(op) in (fb.BuiltinOperator.CONV_2D, fb.BuiltinOperator.DEPTHWISE_CONV_2D):
            weights = graph.tensors[op.inputs[1]].shape
            output = graph.tensors[op.outputs[0]].shape
            channels = int(weights[3]) if code(op) == fb.BuiltinOperator.CONV_2D else 1
            macs += int(np.prod(output)) * int(weights[1]) * int(weights[2]) * channels
    report["conv_macs"] = macs
    args.output_dir.mkdir(parents=True, exist_ok=True)
    # Distinct name: the source file is never overwritten.
    if args.mode != "full":
        (args.output_dir / f"movenet-{args.mode}-host-probe.tflite").write_bytes(artifact)
    from tflite_micro.python.tflite_micro import runtime
    interpreter = None
    try:
        interpreter = runtime.Interpreter.from_bytes(artifact, arena_size=args.arena_bytes)
        report["allocation"] = "passed"
        sample = (np.full((1, 192, 192, 3), 128, dtype=np.uint8) if args.mode == "full"
                  else np.zeros((1, 192, 192, 3), dtype=np.int8))
        interpreter.set_input(sample, 0)
        interpreter.invoke()
        report["invoke"] = "passed"
        report["output_finite"] = all(bool(np.isfinite(interpreter.get_output(i)).all())
                                      for i in range(len(graph.outputs)))
        result = 0
    except RuntimeError as error:
        report["invoke"] = "failed"
        report["error"] = str(error)
        result = 1
    finally:
        if interpreter is not None:
            # TFLM writes its actual allocator report to stderr; capacity != usage.
            interpreter.print_allocations()
        report_path = args.output_dir / f"{args.mode}-report.json"
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
