"""Export only the pinned MoveNet postprocessing graph/quantization/constants."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

SOURCE_SHA256 = "cd7cc22fa946e5d146a7b98d496853e1923e22828d3972d579973f27f91bb105"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--parser-path", type=Path, help="Isolated tflite==2.18.0 extraction directory")
    args = parser.parse_args()
    if args.parser_path:
        sys.path.insert(0, str(args.parser_path.resolve()))
    import numpy as np
    import tflite as fb

    original = args.model.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    if digest != SOURCE_SHA256:
        parser.error("Model SHA differs; fixed tensor/operator indices cannot be reused")
    model = fb.Model.GetRootAsModel(original, 0)
    graph = model.Subgraphs(0)
    names = {v: k for k, v in vars(fb.BuiltinOperator).items() if isinstance(v, int)}
    types = {v: k for k, v in vars(fb.TensorType).items() if isinstance(v, int)}
    operators, needed = [], {259, 276, 282, 285}
    for index in range(86, 157):
        op = graph.Operators(index)
        code = model.OperatorCodes(op.OpcodeIndex())
        name = names[max(code.BuiltinCode(), code.DeprecatedBuiltinCode())]
        if name in ("CONV_2D", "DEPTHWISE_CONV_2D"):
            continue
        operators.append({"index": index, "op": name, "inputs": op.InputsAsNumpy().tolist(),
                          "outputs": op.OutputsAsNumpy().tolist()})
        needed.update(int(i) for i in op.InputsAsNumpy())
        needed.update(int(i) for i in op.OutputsAsNumpy())
    tensors = []
    for index in sorted(needed):
        tensor = graph.Tensors(index)
        item = {"index": index, "name": tensor.Name().decode(), "shape": tensor.ShapeAsNumpy().tolist(),
                "dtype": types[tensor.Type()]}
        quant = tensor.Quantization()
        if quant and quant.ScaleLength():
            item["scale"] = quant.ScaleAsNumpy().tolist()
            item["zero_point"] = quant.ZeroPointAsNumpy().tolist()
        data = model.Buffers(tensor.Buffer()).DataAsNumpy()
        if isinstance(data, np.ndarray) and data.size:
            # Only shape/index/coordinate/center-prior constants, never CNN weights.
            assert index in set(range(4, 18)) | {154, 170, 171, 172, 173}
            dtype = {fb.TensorType.INT8: np.int8, fb.TensorType.INT32: "<i4"}[tensor.Type()]
            item["constant"] = data.view(dtype).tolist()
        tensors.append(item)
    assert len(operators) == 62 and len(tensors) == 87
    contract = {"source_sha256": digest, "heads": [259, 276, 282, 285], "output": 332,
                "operators": operators, "tensors": tensors}
    destination = Path(__file__).resolve().with_name("movenet_v4_contract.json")
    destination.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
