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
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
MODEL_SHA256 = "9051a5ceebc1eb5f535c1b1afe58d33ce878bf9358a159cbfc689c1abebf9c68"
CPU_HZ_CEILING = 300_000_000
GENEROUS_ISSUE_WIDTH = 2
POINTWISE_SOURCE_SHA256 = "d97dc279ab31603b90beed80021b24efacb692c71c6ba047cdea5814f94f2c40"
POINTWISE_ELIGIBLE_CODE_SHA256 = "c9c39ce983c92918621c11d29c035cfbfe8fd4c32b4087f6b66a7dcda50e7c1b"


def pointwise_eligibility(model, graph, op, row, schema):
    """Static equivalent of the reviewed Eligible gate for this fixed model.

    Non-null arena pointers still depend on successful native Prepare. This is
    not a board dispatch measurement; overflow bounds cover every int8 input.
    """
    if row["kind"] != "CONV_2D" or row["filter"][1:3] != [1, 1]:
        return {"eligible": False, "reason": "not_pointwise"}
    if op.InputsLength() != 3:
        raise ValueError("Pointwise bias input changed")
    tensors = [graph.Tensors(op.Inputs(i)) for i in range(3)] + [graph.Tensors(op.Outputs(0))]
    source, weights, bias, output = tensors
    ins, filt, outs = row["input"], row["filter"], row["output"]
    pixels, depth, channels = math.prod(outs[:3]), ins[3], outs[3]
    if ([t.Type() for t in tensors] != [schema.TensorType.INT8, schema.TensorType.INT8,
                                      schema.TensorType.INT32, schema.TensorType.INT8] or
            row["stride"] != [1, 1] or row["dilation"] != [1, 1] or
            row["padding_before"] != [0, 0] or ins[:3] != outs[:3] or
            filt[3] != depth or filt[0] != channels or not 0 < depth <= 65535 or
            not 0 < pixels or not 0 < channels or
            max(pixels * depth, pixels * channels, depth * channels) > 2**31 - 1 or
            bias.ShapeLength() != 1 or bias.Shape(0) != channels):
        raise ValueError("Pinned pointwise dimensions/types no longer satisfy the native gate")
    iq, wq, oq = [t.Quantization() for t in (source, weights, output)]
    if (iq.ZeroPointLength() != 1 or oq.ZeroPointLength() != 1 or
            not -128 <= iq.ZeroPoint(0) <= 127 or not -128 <= oq.ZeroPoint(0) <= 127 or
            wq.ZeroPointLength() not in (1, channels) or
            any(wq.ZeroPoint(i) != 0 for i in range(wq.ZeroPointLength())) or
            iq.ScaleLength() != 1 or oq.ScaleLength() != 1 or
            wq.ScaleLength() not in (1, channels)):
        raise ValueError("Pinned pointwise quantization changed")
    blob = model.Buffers(bias.Buffer())
    raw = bytes(blob.Data(i) for i in range(blob.DataLength()))
    if len(raw) != channels * 4:
        raise ValueError("Unexpected constant bias storage")
    biases = struct.unpack("<" + "i" * channels, raw)
    shifts, bounds = [], []
    max_x = max(abs(-128 - iq.ZeroPoint(0)), abs(127 - iq.ZeroPoint(0)))
    for c, b in enumerate(biases):
        real = iq.Scale(0) * wq.Scale(c if wq.ScaleLength() > 1 else 0) / oq.Scale(0)
        if not math.isfinite(real) or real <= 0:
            raise ValueError("Non-positive per-channel scale")
        q, shift = math.frexp(real)
        multiplier = math.floor(q * (1 << 31) + 0.5)
        if multiplier == 1 << 31:
            multiplier //= 2
            shift += 1
        if shift < -31:
            shift, multiplier = 0, 0
        bound = max_x * 128 * depth + abs(b)
        if (not -31 <= shift <= 30 or multiplier < 0 or
                bound > ((2**31 - 1) - 255) // (1 << max(0, shift))):
            raise ValueError("Pinned pointwise accumulator/shift gate fails")
        shifts.append(shift)
        bounds.append(bound)
    return {"eligible": True, "reason": "reviewed_shape_quantization_and_all_int8_input_bound",
            "input_zero_point": iq.ZeroPoint(0), "output_zero_point": oq.ZeroPoint(0),
            "max_conservative_accumulator_bound": max(bounds),
            "per_channel_shift_range": [min(shifts), max(shifts)],
            "dot4_macs": pixels * (channels // 4 * 4) * depth,
            "dot2_macs": pixels * ((channels % 4) // 2 * 2) * depth,
            "scalar_tail_macs": pixels * (channels % 2) * depth}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_sha256(path):
    # Source semantics do not change when Git converts LF/CRLF on Windows.
    return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


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
        row = {"op": index, "kind": kind, "input": source, "filter": weights,
                     "output": output, "stride": [sh, sw], "dilation": [dh, dw],
                     "padding_before": [ph, pw],
                     "nominal_macs": math.prod(output) * kh * kw * channels,
                     "nonpadding_macs": output[0] * output[3] * vh * vw * channels}
        row["pointwise_gate"] = pointwise_eligibility(model, graph, op, row, tflite)
        rows.append(row)
    totals = {kind: {"nodes": sum(r["kind"] == kind for r in rows),
                     "nonpadding_macs": sum(r["nonpadding_macs"] for r in rows if r["kind"] == kind)}
              for kind in ("CONV_2D", "DEPTHWISE_CONV_2D")}
    split = {key: sum(r["pointwise_gate"].get(key, 0) for r in rows)
             for key in ("dot4_macs", "dot2_macs", "scalar_tail_macs")}
    split["eligible_nodes"] = sum(r["pointwise_gate"]["eligible"] for r in rows)
    split["reference_conv_macs"] = sum(r["nonpadding_macs"] for r in rows
                                      if r["kind"] == "CONV_2D" and not r["pointwise_gate"]["eligible"])
    if (split["eligible_nodes"] != 49 or sum(split[k] for k in
            ("dot4_macs", "dot2_macs", "scalar_tail_macs", "reference_conv_macs")) !=
            totals["CONV_2D"]["nonpadding_macs"]):
        raise ValueError("Pointwise dispatch partition changed")
    return {"sha256": sha256(path), "bytes": path.stat().st_size,
            "operator_count": graph.OperatorsLength(), "by_kind": totals,
            "nominal_macs": sum(r["nominal_macs"] for r in rows),
            "nonpadding_macs": sum(r["nonpadding_macs"] for r in rows),
            "pointwise_macs": sum(r["nonpadding_macs"] for r in rows if r["filter"][1:3] == [1, 1]),
            "pointwise_partition": split, "layers": rows}


def parse_instructions(disassembly):
    instructions = []
    for line in disassembly.splitlines():
        match = re.match(r"\s*([0-9a-f]+):\s+([0-9a-f]+)\s+(\S+)\s*(.*)", line)
        if match:
            instructions.append({"address": int(match[1], 16), "machine_code": match[2],
                                 "mnemonic": match[3], "operands": match[4]})
    return instructions


def backedge_block(instructions, end):
    jump = re.search(r",([0-9a-f]+)\s+<", end["operands"])
    if not jump or int(jump[1], 16) >= end["address"]:
        return []
    target = int(jump[1], 16)
    block = [i for i in instructions if target <= i["address"] <= end["address"]]
    # Reject a target in the middle of an instruction or disassembly gaps.
    if not block or block[0]["address"] != target:
        return []
    if any(a["address"] + len(a["machine_code"]) // 2 != b["address"] for a, b in zip(block, block[1:])):
        return []
    return block


def checked_loop(instructions):
    candidates = []
    audited_sequence = (
        "add mv mv mv mv sw sw sw sw sw sw sw jal lw lw lw mv mv sw mv mv mv sw sw jal "
        "lw lw lw add lb lw lw add lw lw add lb lw mul lw lw lw add addi blt").split()
    for end in instructions:
        if end["mnemonic"] != "blt":
            continue
        block = backedge_block(instructions, end)
        counts = collections.Counter(i["mnemonic"] for i in block)
        # Recognize the audited scalar channel loop, not any arbitrary backedge.
        # Exclude Offset callees from the lower bound. A different optimized
        # loop must be reviewed explicitly instead of reusing this count.
        if ([i["mnemonic"] for i in block] == audited_sequence and counts["mul"] == 1 and counts["lb"] == 2
                and counts["jal"] == 2 and counts["blt"] == 1
                and all(i["mnemonic"] != "jal" or "::Offset(" in i["operands"] for i in block)
                and not any(i["mnemonic"].startswith("b") for i in block[:-1])
                and not any(i["mnemonic"] in ("j", "jr", "ret", "jalr") for i in block)):
            candidates.append(block)
    if len(candidates) != 1:
        raise ValueError("Checked scalar MAC loop changed; inspect ELF, never assume 45 instructions")
    return candidates


def release_loop_shape(block):
    # Audited O3/NDEBUG signed-int8 loop. Match the exact operation sequence AND
    # register/spill data flow, not merely 'two loads plus multiply'. Both int8
    # filter routes are inlined into ConvEval; a changed compiler must fail shut.
    if [i["mnemonic"] for i in block] != [
            "lb", "lb", "sw", "lw", "addi", "add", "lw", "addi", "mul", "lw", "add", "bne"]:
        return False
    operands = [i["operands"].split(" <", 1)[0].replace(" ", "").split(",") for i in block]
    if [len(o) for o in operands] != [2,2,2,2,3,3,2,3,3,2,3,3]:
        return False
    weight, pixels = operands[0][0], operands[1][0]
    wp = re.fullmatch(r"0\(([a-z][a-z0-9]*)\)", operands[0][1])
    ip = re.fullmatch(r"0\(([a-z][a-z0-9]*)\)", operands[1][1])
    if not wp or not ip or wp[1] == ip[1] or weight == pixels:
        return False
    temp, spill = operands[3][0], operands[2][1]
    accumulator = operands[10][0]
    return (operands[2][0] == weight and
            re.fullmatch(r"-?\d+\(s0\)", spill) is not None and
            all(re.fullmatch(r"-?\d+\(s0\)", operands[n][1]) for n in (3,6,9)) and
            operands[4] == [wp[1], wp[1], "1"] and
            operands[5] == [pixels, pixels, temp] and
            operands[6] == [temp, spill] and
            operands[7] == [ip[1], ip[1], "1"] and
            operands[8] == [pixels, pixels, temp] and operands[9][0] == temp and
            operands[10] == [accumulator, accumulator, pixels] and
            set(operands[11][:2]) == {wp[1], temp} and
            len({wp[1], ip[1], pixels, temp, accumulator}) == 5)


def release_loops(instructions):
    candidates = [block for end in instructions if end["mnemonic"] == "bne"
                  if release_loop_shape(block := backedge_block(instructions, end))]
    if len(candidates) != 2:
        raise ValueError("Release ConvEval MAC loops changed; expected two audited 12-instruction int8 routes")
    return candidates


def dot_loop_shape(block, lanes):
    """Recognize reviewed register-only loops, including full symbolic data flow.

    Each loaded input is shared by exactly lanes independent MACs. No spills,
    calls or hidden branches are permitted, and every source pointer advances
    once before an input-end backedge. Register allocation may vary.
    """
    sequences = {
        4: "lb lb lb lb lb add mul mul mul addi mul addi addi addi addi add add add add bne",
        2: "lb lb lb add mul addi mul addi addi add add bne",
        1: "lb lb add mul addi add addi bne",
    }
    if [i["mnemonic"] for i in block] != sequences[lanes].split():
        return False
    state, loads, products, adds = {}, [], [], []

    def reg(name):
        return state.get(name, ("reg", name))

    def expr(op, a, b):
        return (op, *sorted((a, b), key=repr))

    try:
        for ins in block[:-1]:
            o = ins["operands"].replace(" ", "").split(",")
            if ins["mnemonic"] == "lb":
                match = re.fullmatch(r"0\(([a-z][a-z0-9]*)\)", o[1])
                if len(o) != 2 or not match or reg(match[1]) != ("reg", match[1]):
                    return False
                value = ("load_i8", reg(match[1]))
                state[o[0]] = value
                loads.append((match[1], value))
            elif ins["mnemonic"] == "addi":
                if len(o) != 3 or o[0] != o[1] or o[2] != "1":
                    return False
                state[o[0]] = expr("add", reg(o[1]), ("const", 1))
            else:
                if len(o) != 3:
                    return False
                value = expr(ins["mnemonic"], reg(o[1]), reg(o[2]))
                state[o[0]] = value
                (products if ins["mnemonic"] == "mul" else adds).append(value)
        if len(loads) != lanes + 1 or len({p for p, _ in loads}) != lanes + 1:
            return False
        adjusted = adds[0]
        input_value = loads[0][1]
        if adjusted[0] != "add" or input_value not in adjusted[1:]:
            return False
        offset = next(x for x in adjusted[1:] if x != input_value)
        if offset[0] != "reg" or reg(offset[1]) != offset:
            return False
        expected = [expr("mul", adjusted, value) for _, value in loads[1:]]
        if sorted(products, key=repr) != sorted(expected, key=repr):
            return False
        accumulators = [name for name, value in state.items()
                        if any(value == expr("add", ("reg", name), p) for p in expected)]
        if len(accumulators) != lanes:
            return False
        if any(reg(pointer) != expr("add", ("reg", pointer), ("const", 1))
               for pointer, _ in loads):
            return False
        branch = block[-1]["operands"].split(" <", 1)[0].replace(" ", "").split(",")
        input_pointer = loads[0][0]
        if len(branch) != 3 or input_pointer not in branch[:2]:
            return False
        end = next(x for x in branch[:2] if x != input_pointer)
        return reg(end) == ("reg", end) and end not in {p for p, _ in loads}
    except (IndexError, StopIteration, ValueError):
        return False


def symbol_table(elf, nm):
    result = []
    for line in run(nm, "-S", "-C", elf).splitlines():
        fields = line.split(None, 3)
        if len(fields) == 4 and all(re.fullmatch(r"[0-9a-fA-F]+", x) for x in fields[:2]):
            result.append((int(fields[0], 16), int(fields[1], 16), fields[3]))
    return result


def normalized_function_hash(instructions, start):
    """Address-independent fingerprint of reviewed native guard control flow.

    Local branches retain relative targets; external calls retain symbol names.
    No register/operation/constant is discarded. This deliberately fails when
    compiler output changes, requiring a new review instead of a weak presence
    check such as finding one 'return true' instruction.
    """
    rows = []
    for ins in instructions:
        operands = ins["operands"].split(" #", 1)[0]
        if " <" in operands:
            match = re.search(r"([0-9a-f]+) <([^>]+)>$", operands)
            if not match:
                raise ValueError("Unrecognized native branch/call operand")
            target = ("symbol:" + match[2] if ins["mnemonic"] in ("jal", "jalr") else
                      "relative:" + str(int(match[1], 16) - start))
            operands = operands[:match.start()] + target
        rows.append([ins["address"] - start, ins["mnemonic"], operands])
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


def pointwise_loops(elf, objdump):
    symbols = symbol_table(elf, objdump.with_name(objdump.name.replace("objdump", "nm")))
    prefix = "ricestep::pointwise::"
    if not any(s[2].startswith(prefix) for s in symbols):
        return None

    def one(name):
        hits = [s for s in symbols if s[2].startswith(prefix + name + "(")]
        if len(hits) != 1:
            raise ValueError(f"Expected exactly one native pointwise {name} symbol")
        start, size, full_name = hits[0]
        dis = run(objdump, "-d", "-C", f"--start-address={start}", f"--stop-address={start+size}", elf)
        return hits[0], parse_instructions(dis), dis

    reports = {}
    for name, lanes in (("Dot4", 4), ("Dot2", 2), ("Invoke", 1)):
        symbol, instructions, dis = one(name)
        blocks = [b for end in instructions if end["mnemonic"] == "bne"
                  if dot_loop_shape(b := backedge_block(instructions, end), lanes)]
        if len(blocks) != 1:
            raise ValueError(f"Pointwise {name} loop changed; inspect exact native data flow")
        b = blocks[0]
        reports[name] = {"function": symbol[2], "function_address": hex(symbol[0]),
                         "function_bytes": symbol[1], "loop_start": hex(b[0]["address"]),
                         "loop_end_inclusive": hex(b[-1]["address"]),
                         "direct_instructions_per_iteration": len(b), "macs_per_iteration": lanes,
                         "instructions": b}
        if name == "Invoke":
            # Register-only DotN definitions alone are insufficient: the native
            # wrapper must call both and retain the fallback/eligibility paths.
            for required in ("Dot4(", "Dot2(", "Eligible("):
                if prefix + required not in dis:
                    raise ValueError("Pointwise Invoke no longer calls the audited native path")
            if "Register_CONV_2D(" not in dis:
                raise ValueError("Pointwise reference fallback registration missing")
    eligible, eligibility_code, _ = one("Eligible")
    if not any(i["mnemonic"] == "li" and i["operands"].replace(" ", "") == "a0,1"
               for i in eligibility_code):
        raise ValueError("Native Eligible has no success path; compression/stub build is not audited")
    eligibility_hash = normalized_function_hash(eligibility_code, eligible[0])
    if eligibility_hash != POINTWISE_ELIGIBLE_CODE_SHA256:
        raise ValueError("Native Eligible control flow changed; review before updating its fingerprint: " + eligibility_hash)
    reports["eligibility"] = {"function": eligible[2], "function_address": hex(eligible[0]),
                              "function_bytes": eligible[1], "native_success_path_present": True,
                              "source_sha256": sha256(ROOT / "firmware/components/pose_pointwise/pose_pointwise.cc"),
                              "normalized_source_sha256": source_sha256(ROOT / "firmware/components/pose_pointwise/pose_pointwise.cc"),
                              "normalized_native_guard_sha256": eligibility_hash,
                              "board_dispatch_measured": False}
    return reports


def scalar_loop(elf, objdump):
    header = elf.read_bytes()[:20]
    if len(header) != 20 or header[:6] != b"\x7fELF\x01\x01" or int.from_bytes(header[18:20], "little") != 243:
        raise ValueError("Expected ELF32 little-endian RISC-V image")
    nm = objdump.with_name(objdump.name.replace("objdump", "nm"))
    readelf = objdump.with_name(objdump.name.replace("objdump", "readelf"))
    symbols = []
    for line in run(nm, "-S", "-C", elf).splitlines():
        fields = line.split(None, 3)
        if len(fields) == 4 and re.fullmatch(r"[0-9a-fA-F]+", fields[0]) and re.fullmatch(r"[0-9a-fA-F]+", fields[1]):
            symbols.append((int(fields[0], 16), int(fields[1], 16), fields[3]))
    checked = [s for s in symbols if "reference_integer_ops::ConvPerChannel(" in s[2] and "signed char const*" in s[2]]
    if len(checked) == 1:
        selected, profile, finder = checked[0], "os-debug-checked-45", checked_loop
    elif not checked:
        inlined = [s for s in symbols if "micro::(anonymous namespace)::ConvEval(" in s[2]]
        if len(inlined) != 1:
            raise ValueError("Expected one linked ConvEval; re-audit this compiler/runtime")
        selected, profile, finder = inlined[0], "o3-release-inlined-12", release_loops
    else:
        raise ValueError("Ambiguous int8 convolution implementation; re-audit this compiler/runtime")
    start, size, name = selected
    disassembly = run(objdump, "-d", "-C", f"--start-address={start}", f"--stop-address={start + size}", elf)
    blocks = finder(parse_instructions(disassembly))
    loop_reports = [{"loop_start": hex(b[0]["address"]), "loop_end_inclusive": hex(b[-1]["address"]),
                     "direct_instructions_per_conv_mac": len(b), "instructions": b} for b in blocks]
    return {"profile": profile, "function": name, "function_address": hex(start), "function_bytes": size,
            **loop_reports[0], "audited_routes": loop_reports,
            "offset_callees_included": False, "offset_calls_per_mac": 2 if checked else 0,
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


def check_compile_profile(command, profile):
    # Last compiler option wins. These are evidence gates for the two audited
    # configurations, not an attempt to infer arbitrary optimizer behavior.
    words = command.split()
    if any(flag not in words for flag in (
            "-mtune=nuclei-300-series", "-march=rv32imac_zba_zbb_zbc_zbs", "-mabi=ilp32")):
        raise ValueError("Target CPU/ISA/ABI changed; re-audit the issue-width assumption")
    optimization = [w for w in words if re.fullmatch(r"-O(?:[0-3sgz]|fast)", w)]
    debug = [w for w in words if w == "-UNDEBUG" or re.fullmatch(r"-DNDEBUG(?:=\S+)?", w)]
    fast_math = [w for w in words if w in ("-ffast-math", "-fno-fast-math")]
    contraction = [w for w in words if w.startswith("-ffp-contract=")]
    expected_opt = "-O3" if profile == "o3-release-inlined-12" else "-Os"
    has_ndebug = bool(debug) and debug[-1].startswith("-D")
    if (not optimization or optimization[-1] != expected_opt or
            has_ndebug != (profile == "o3-release-inlined-12") or
            not fast_math or fast_math[-1] != "-fno-fast-math" or
            not contraction or contraction[-1] != "-ffp-contract=off"):
        raise ValueError("Compile options do not match the recognized loop profile; re-audit this build")


def dispatch_instruction_budget(model, reference, pointwise):
    reference_rate = reference["direct_instructions_per_conv_mac"]
    if not pointwise:
        count = reference_rate * model["by_kind"]["CONV_2D"]["nonpadding_macs"]
        return count, {"reference_all_conv": {"macs": model["by_kind"]["CONV_2D"]["nonpadding_macs"],
                                               "instructions": count}}
    split = model["pointwise_partition"]
    costs = {}
    for route, key in (("Dot4", "dot4_macs"), ("Dot2", "dot2_macs"), ("Invoke", "scalar_tail_macs")):
        loop = pointwise[route]
        macs = split[key]
        lanes = loop["macs_per_iteration"]
        if macs % lanes:
            raise ValueError("MAC partition does not fit the audited dot loop")
        costs[key] = {"macs": macs, "iterations": macs // lanes,
                      "instructions_per_iteration": loop["direct_instructions_per_iteration"],
                      "instructions": macs // lanes * loop["direct_instructions_per_iteration"]}
    costs["reference_nonpointwise"] = {"macs": split["reference_conv_macs"],
                                       "instructions": split["reference_conv_macs"] * reference_rate}
    return sum(v["instructions"] for v in costs.values()), costs


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
    pointwise = pointwise_loops(args.elf, args.objdump)
    commands = json.loads(args.compile_commands.read_text(encoding="utf-8"))
    conv_commands = [c for c in commands if c["file"].replace("\\", "/").endswith("/kernels/conv.cc")]
    if len(conv_commands) != 1:
        raise ValueError("Expected one reference conv.cc compile command")
    command = conv_commands[0]["command"]
    check_compile_profile(command, loop["profile"])
    pointwise_commands = [c for c in commands if c["file"].replace("\\", "/").endswith("/pose_pointwise/pose_pointwise.cc")]
    if pointwise:
        source = ROOT / "firmware/components/pose_pointwise/pose_pointwise.cc"
        if source_sha256(source) != POINTWISE_SOURCE_SHA256:
            raise ValueError("Pointwise source/eligibility changed; re-audit before updating the source pin")
        if len(pointwise_commands) != 1 or loop["profile"] != "o3-release-inlined-12":
            raise ValueError("Expected one O3 pointwise translation unit and audited release fallback")
        check_compile_profile(pointwise_commands[0]["command"], "o3-release-inlined-12")
        forbidden = ("USE_TFLM_COMPRESSION", "RICESTEP_POINTWISE_TEST_STATS", "TFLITE_EMULATE_FLOAT",
                     "TFLITE_SINGLE_ROUNDING")
        if any(re.search(r"(?:^|\s)-D" + macro + r"(?:=|\s|$)", c["command"])
               for c in commands for macro in forbidden):
            raise ValueError("Unaudited compression/stats/quantization macro in native build")
    elif pointwise_commands:
        raise ValueError("Pointwise compiled but not recognized in final ELF")
    direct_count, instruction_split = dispatch_instruction_budget(model, loop, pointwise)
    profile = "o3-pointwise-dot4-dot2-tail-reference12" if pointwise else loop["profile"]
    report = {"scope": "Static conditional lower bounds only; no board measurement, FPS or pose accuracy claim",
              "model": model, "elf_sha256": sha256(args.elf), "scalar_conv_loop": loop,
              "dispatch_profile": profile, "pointwise_loops": pointwise,
              "sdk_evidence": source_evidence(args.sdk),
              "compile_commands_sha256": sha256(args.compile_commands), "conv_compile_command": conv_commands[0],
              "pointwise_compile_command": pointwise_commands[0] if pointwise else None,
              "assumptions": {"clock_at_most_hz": CPU_HZ_CEILING, "issue_at_most_instructions_per_cycle": GENEROUS_ISSUE_WIDTH,
                              "one_worker_core": True, "zero_stalls": True, "no_overclock_or_custom_offload": True,
                              "cpu_family_source": "https://www.nucleisys.com/product/300.php",
                              "clock_is_measured": False, "precise_ls2663_core_variant_confirmed": False},
              "budget": {"gate_ms": 400, "ideal_one_mac_per_cycle_ms": model["nonpadding_macs"] / CPU_HZ_CEILING * 1000,
                         "ordinary_conv_direct_instructions": direct_count,
                         "instruction_split": instruction_split,
                         "ordinary_conv_only_lower_ms_at_dual_issue": direct_count / (CPU_HZ_CEILING * GENEROUS_ISSUE_WIDTH) * 1000,
                         "excluded": (["Offset callee instructions"] if loop["offset_calls_per_mac"] else []) + ["outer convolution loops", "depthwise convolution",
                                      "pointwise eligibility checks and DotN call/prologue/epilogue", "requantization", "other operators", "camera capture", "image preparation", "decode",
                                      "instruction/data stalls", "interrupts", "other tasks"]}}
    encoded = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
        print(json.dumps({"report": str(args.output), "model_sha256": model["sha256"],
                          "elf_sha256": report["elf_sha256"], "nonpadding_macs": model["nonpadding_macs"],
                          "loop_profile": profile,
                          "budget": report["budget"]}, indent=2))
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
