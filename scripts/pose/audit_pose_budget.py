"""Offline static budget audit of the pinned four-head model and built Mini3 ELF.

No inference, camera, board, network or global dependency installation. Requires
the same isolated tflite schema parser used in board-pose-feasibility.md.
Instruction/cycle assumptions are explicit; results are NOT measured latency.
"""

import argparse
import collections
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
MODEL_SHA256 = "9051a5ceebc1eb5f535c1b1afe58d33ce878bf9358a159cbfc689c1abebf9c68"
CPU_HZ_CEILING = 300_000_000
GENEROUS_ISSUE_WIDTH = 2


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(*args):
    return subprocess.check_output([str(a) for a in args], text=True, encoding="utf-8")


def model_counts(path, schema_path):
    if sha256(path) != MODEL_SHA256:
        raise ValueError("Unexpected model SHA-256; this audit only accepts the pinned four-head model")
    sys.path.insert(0, str(schema_path.resolve()))
    import tflite
    model = tflite.Model.GetRootAsModel(path.read_bytes(), 0)
    graph = model.Subgraphs(0)
    names = {v: k for k, v in vars(tflite.BuiltinOperator).items() if isinstance(v, int)}
    rows = []
    for index in range(graph.OperatorsLength()):
        op = graph.Operators(index)
        kind = names[model.OperatorCodes(op.OpcodeIndex()).BuiltinCode()]
        if kind not in ("CONV_2D", "DEPTHWISE_CONV_2D"):
            continue

        def shape(tensor_id):
            tensor = graph.Tensors(tensor_id)
            return [tensor.Shape(i) for i in range(tensor.ShapeLength())]

        source, weights, output = shape(op.Inputs(0)), shape(op.Inputs(1)), shape(op.Outputs(0))
        options = tflite.Conv2DOptions() if kind == "CONV_2D" else tflite.DepthwiseConv2DOptions()
        options.Init(op.BuiltinOptions().Bytes, op.BuiltinOptions().Pos)
        kh, kw = weights[1:3]
        dh, dw = options.DilationHFactor(), options.DilationWFactor()
        sh, sw = options.StrideH(), options.StrideW()
        if options.Padding() == tflite.Padding.SAME:
            ph = max(0, (output[1] - 1) * sh + (kh - 1) * dh + 1 - source[1]) // 2
            pw = max(0, (output[2] - 1) * sw + (kw - 1) * dw + 1 - source[2]) // 2
        elif options.Padding() == tflite.Padding.VALID:
            ph = pw = 0
        else:
            raise ValueError("Unsupported padding")
        # Reference kernels skip padded samples before the multiply. Count only
        # in-bounds kernel positions, including exact stride/dilation/asymmetry.
        vh = sum(0 <= y * sh - ph + k * dh < source[1] for y in range(output[1]) for k in range(kh))
        vw = sum(0 <= x * sw - pw + k * dw < source[2] for x in range(output[2]) for k in range(kw))
        channels = weights[3] if kind == "CONV_2D" else 1
        rows.append({"op": index, "kind": kind, "input": source, "filter": weights,
                     "output": output, "stride": [sh, sw], "dilation": [dh, dw],
                     "padding_before": [ph, pw],
                     "nominal_macs": math.prod(output) * kh * kw * channels,
                     "nonpadding_macs": output[0] * output[3] * vh * vw * channels})
    totals = {kind: {"nodes": sum(r["kind"] == kind for r in rows),
                     "nonpadding_macs": sum(r["nonpadding_macs"] for r in rows if r["kind"] == kind)}
              for kind in ("CONV_2D", "DEPTHWISE_CONV_2D")}
    return {"sha256": sha256(path), "bytes": path.stat().st_size,
            "operator_count": graph.OperatorsLength(), "by_kind": totals,
            "nominal_macs": sum(r["nominal_macs"] for r in rows),
            "nonpadding_macs": sum(r["nonpadding_macs"] for r in rows),
            "pointwise_macs": sum(r["nonpadding_macs"] for r in rows if r["filter"][1:3] == [1, 1]),
            "layers": rows}


def scalar_loop(elf, objdump):
    nm = objdump.with_name(objdump.name.replace("objdump", "nm"))
    readelf = objdump.with_name(objdump.name.replace("objdump", "readelf"))
    symbols = run(nm, "-S", "-C", elf)
    matches = []
    for line in symbols.splitlines():
        if "reference_integer_ops::ConvPerChannel(" in line and "signed char const*" in line:
            fields = line.split(None, 3)
            matches.append((int(fields[0], 16), int(fields[1], 16), fields[3]))
    if len(matches) != 1:
        raise ValueError("Expected one linked int8 reference ConvPerChannel; re-audit this compiler/runtime")
    start, size, name = matches[0]
    disassembly = run(objdump, "-d", "-C", f"--start-address={start}", f"--stop-address={start + size}", elf)
    instructions = []
    for line in disassembly.splitlines():
        match = re.match(r"\s*([0-9a-f]+):\s+([0-9a-f]+)\s+(\S+)\s*(.*)", line)
        if match:
            instructions.append({"address": int(match[1], 16), "machine_code": match[2],
                                 "mnemonic": match[3], "operands": match[4]})
    candidates = []
    for end in instructions:
        if end["mnemonic"] != "blt":
            continue
        jump = re.search(r",([0-9a-f]+)\s+<", end["operands"])
        if not jump:
            continue
        target = int(jump[1], 16)
        block = [i for i in instructions if target <= i["address"] <= end["address"]]
        counts = collections.Counter(i["mnemonic"] for i in block)
        # Recognize the audited scalar channel loop, not any arbitrary backedge.
        # Exclude Offset callees from the lower bound. A different optimized
        # loop must be reviewed explicitly instead of reusing this count.
        if (target < end["address"] and counts["mul"] == 1 and counts["lb"] == 2
                and counts["jal"] == 2 and counts["blt"] == 1
                and all(i["mnemonic"] != "jal" or "::Offset(" in i["operands"] for i in block)
                and not any(i["mnemonic"].startswith("b") for i in block[:-1])
                and not any(i["mnemonic"] in ("j", "jr", "ret", "jalr") for i in block)):
            candidates.append(block)
    if len(candidates) != 1:
        raise ValueError("Scalar MAC loop changed; inspect ELF and update the audit, never assume 45 instructions")
    block = candidates[0]
    return {"function": name, "function_address": hex(start), "function_bytes": size,
            "loop_start": hex(block[0]["address"]), "loop_end_inclusive": hex(block[-1]["address"]),
            "direct_instructions_per_conv_mac": len(block),
            "offset_callees_included": False, "instructions": block,
            "riscv_attributes": run(readelf, "-A", elf).strip()}


def source_evidence(sdk):
    clock_header = (sdk / "arcs-sdk/soc/arcs/hal/chip/arcs/include/clock_config.h").read_text(encoding="utf-8")
    for macro in ("BOARD_BOOTCLOCKRUN_SYSPLL_CORE_CLK", "BOARD_BOOTCLOCKRUN_SYSPLL_CORE_CLK_MAX"):
        match = re.search(r"^#define\s+" + macro + r"\s+(\d+)UL\s*$", clock_header, re.MULTILINE)
        if not match or int(match[1]) != CPU_HZ_CEILING:
            raise ValueError("SDK core-clock configuration changed; re-audit the frequency assumption")
    anchors = {
        "arcs-sdk/soc/arcs/hal/chip/arcs/include/clock_config.h":
            ("#define BOARD_BOOTCLOCKRUN_SYSPLL_CORE_CLK ", "#define BOARD_BOOTCLOCKRUN_SYSPLL_CORE_CLK_MAX "),
        "apps/arcs-mini/services/service_power_policy.c":
            ("#define POWER_POLICY_ACTIVE_CORE_CLOCK", "#define POWER_POLICY_HIBERNATE_CORE_CLOCK"),
        "arcs-sdk/soc/arcs/common/startup/system.c":
            ("SystemCoreClock = CRM_GetSrcFreq",),
        "arcs-sdk/soc/arcs/common/startup/soc.c": ("#if CONFIG_CLOCK_INIT", "SystemCoreClockUpdate();"),
        "arcs-sdk/docs/zh/hardware/soc/ls26xx.rst": ("LS2663L0U", "Dual RISC-V 300MHz"),
    }
    result = []
    for relative, needles in anchors.items():
        file = sdk / relative
        lines = file.read_text(encoding="utf-8").splitlines()
        evidence = [{"line": n, "text": text.strip()} for n, text in enumerate(lines, 1)
                    if any(needle in text for needle in needles)]
        if not evidence:
            raise ValueError(f"Missing SDK clock evidence in {relative}")
        result.append({"file": relative, "sha256": sha256(file), "matches": evidence})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=ROOT / ".cache/pose-probe/movenet-heads-host-probe.tflite")
    parser.add_argument("--schema-path", type=Path, default=ROOT / ".cache/research-pose/parser")
    parser.add_argument("--elf", type=Path, default=ROOT / "dist/native/pose-runtime/arcs-mini3-ricestep.elf")
    parser.add_argument("--objdump", type=Path, default=ROOT / ".cache/native-listenai-tools/gcc/bin/riscv64-unknown-elf-objdump.exe")
    parser.add_argument("--sdk", type=Path, default=ROOT / ".cache/native-mini3-sdk")
    parser.add_argument("--compile-commands", type=Path, default=ROOT / ".cache/native-mini3-sdk/build-mini3-standalone/compile_commands.json")
    parser.add_argument("--output", type=Path, help="Optional JSON report; generated reports belong in .cache")
    args = parser.parse_args()
    model = model_counts(args.model, args.schema_path)
    loop = scalar_loop(args.elf, args.objdump)
    commands = json.loads(args.compile_commands.read_text(encoding="utf-8"))
    conv_commands = [c for c in commands if c["file"].replace("\\", "/").endswith("/kernels/conv.cc")]
    if len(conv_commands) != 1:
        raise ValueError("Expected one reference conv.cc compile command")
    command = conv_commands[0]["command"]
    if "-mtune=nuclei-300-series" not in command or "-march=rv32imac_zba_zbb_zbc_zbs" not in command:
        raise ValueError("Target CPU/ISA changed; re-audit the issue-width assumption")
    direct_count = loop["direct_instructions_per_conv_mac"] * model["by_kind"]["CONV_2D"]["nonpadding_macs"]
    report = {"scope": "Static conditional lower bounds only; no board measurement, FPS or pose accuracy claim",
              "model": model, "elf_sha256": sha256(args.elf), "scalar_conv_loop": loop,
              "sdk_evidence": source_evidence(args.sdk),
              "compile_commands_sha256": sha256(args.compile_commands), "conv_compile_command": conv_commands[0],
              "assumptions": {"clock_at_most_hz": CPU_HZ_CEILING, "issue_at_most_instructions_per_cycle": GENEROUS_ISSUE_WIDTH,
                              "one_worker_core": True, "zero_stalls": True, "no_overclock_or_custom_offload": True,
                              "cpu_family_source": "https://www.nucleisys.com/product/300.php",
                              "clock_is_measured": False, "precise_ls2663_core_variant_confirmed": False},
              "budget": {"gate_ms": 400, "ideal_one_mac_per_cycle_ms": model["nonpadding_macs"] / CPU_HZ_CEILING * 1000,
                         "ordinary_conv_direct_instructions": direct_count,
                         "ordinary_conv_only_lower_ms_at_dual_issue": direct_count / (CPU_HZ_CEILING * GENEROUS_ISSUE_WIDTH) * 1000,
                         "excluded": ["Offset callee instructions", "outer convolution loops", "depthwise convolution",
                                      "requantization", "other operators", "camera capture", "image preparation", "decode",
                                      "instruction/data stalls", "interrupts", "other tasks"]}}
    encoded = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
        print(json.dumps({"report": str(args.output), "model_sha256": model["sha256"],
                          "elf_sha256": report["elf_sha256"], "nonpadding_macs": model["nonpadding_macs"],
                          "direct_instructions_per_mac": loop["direct_instructions_per_conv_mac"],
                          "budget": report["budget"]}, indent=2))
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
