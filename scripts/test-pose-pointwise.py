"""Host-only int8 pointwise regression with the actual production CMake graph.

Linux/WSL prerequisites: Python 3.10+, Node 20+, CMake 3.19+, GCC/G++,
Make, Git and curl. No Python packages, model files, camera or board are used.
The existing setup-pose-source helper verifies/downloads only the locked TFLM
and FlatBuffers archives. Use --check-only to require already cached sources.

    python3 scripts/test-pose-pointwise.py
    python3 scripts/test-pose-pointwise.py --check-only --jobs 3

ASan, UBSan and leak detection are mandatory. Logs/report stay under .cache.
Host sources use a separate cache so Windows-patched header line endings cannot
silently replace a Linux source inventory (or vice versa).
This validates arithmetic and memory safety on the host, not Mini3 throughput.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FLAGS = (
    "-g -fsanitize=address,undefined -fno-sanitize-recover=all "
    "-fno-omit-frame-pointer -fno-fast-math -ffp-contract=off"
)


def executable(name):
    result = shutil.which(name)
    if not result:
        raise ValueError(f"Missing prerequisite executable: {name}")
    return str(Path(result).resolve())


def run(command, log, env=None):
    print(f"{Path(command[0]).name}: {log.name}", flush=True)
    with log.open("a", encoding="utf-8") as output:
        output.write("$ " + shlex.join(command) + "\n")
        output.flush()
        result = subprocess.run(command, cwd=ROOT, env=env, stdout=output,
                                stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        tail = log.read_text(encoding="utf-8", errors="replace")[-6000:]
        raise RuntimeError(f"Command failed ({result.returncode}); {log}\n{tail}")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check-only", action="store_true", help="verify cached source dependencies without downloading")
    parser.add_argument("--cmake", default="cmake")
    parser.add_argument("--node", default="node")
    parser.add_argument("--cc", default="gcc")
    parser.add_argument("--cxx", default="g++")
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--build-dir", type=Path, default=Path(".cache/pose-pointwise-ci"))
    parser.add_argument("--source-cache", type=Path, default=Path(".cache/pose-pointwise-host-sources"),
                        help="separate host source tree (Windows/Linux patched-header line endings can differ)")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Run this sanitizer regression on Linux (or Windows via WSL).")
    if not 1 <= args.jobs <= 16:
        parser.error("--jobs must be 1..16")
    build = (ROOT / args.build_dir).resolve()
    cache = (ROOT / ".cache").resolve()
    if build == cache or not build.is_relative_to(cache):
        parser.error("--build-dir must be a subdirectory of this repository's .cache")
    sources = (ROOT / args.source_cache).resolve()
    if sources == cache or not sources.is_relative_to(cache):
        parser.error("--source-cache must be a subdirectory of this repository's .cache")
    node, cmake, cc, cxx = map(executable, (args.node, args.cmake, args.cc, args.cxx))
    for prerequisite in ("git", "curl", "make"):
        executable(prerequisite)
    versions = {}
    for name, tool in (("node", node), ("cmake", cmake), ("cc", cc), ("cxx", cxx)):
        versions[name] = subprocess.check_output([tool, "--version"], text=True).splitlines()[0]
    match = re.fullmatch(r"v(\d+)\.\d+\.\d+", versions["node"])
    if not match or int(match[1]) < 20:
        parser.error("Node 20+ is required by the locked source preparation helper")
    build.mkdir(parents=True, exist_ok=True)
    report = {"passed": False, "host_only": True, "hardware_used": False,
              "model_required": False, "sanitizers": ["address", "undefined", "leaks"],
              "versions": versions, "source_cache": str(sources.relative_to(ROOT))}
    report_path = build / "pointwise-report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    for name in ("setup.log", "build.log", "pointwise-test.log"):
        (build / name).write_text("", encoding="utf-8")
    # Call the existing locked helper API so it uses this exact Python, rather
    # than assuming an unversioned `python` alias exists on the host.
    setup_code = (
        "const s=require(process.argv[1]);"
        "s.setup({python:process.argv[2],checkOnly:process.argv[3]==='true',cache:process.argv[4]});"
        "console.log('Pinned CPU source archives, patch and installed trees verified.');"
    )
    run([node, "-e", setup_code, str(ROOT / "scripts/setup-pose-source.cjs"),
         sys.executable, "true" if args.check_only else "false", str(sources)], build / "setup.log")
    locked = json.loads((ROOT / "pose-runtime.lock.json").read_text())
    run([cmake, "-S", str(ROOT / "tests/native/pose_engine"), "-B", str(build),
         "-G", "Unix Makefiles", "-DCMAKE_BUILD_TYPE=", "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
         f"-DCMAKE_C_COMPILER={cc}", f"-DCMAKE_CXX_COMPILER={cxx}",
         f"-DRICESTEP_TFLM_ROOT={sources / locked['tflm']['directory']}",
         f"-DRICESTEP_FLATBUFFERS_ROOT={sources / locked['flatbuffers']['directory']}",
         f"-DCMAKE_C_FLAGS={FLAGS}", f"-DCMAKE_CXX_FLAGS={FLAGS}",
         f"-DCMAKE_EXE_LINKER_FLAGS={FLAGS}"], build / "build.log")
    # Only the standalone kernel target is built; full-model oracle harnesses
    # remain separate tests and this job neither downloads nor invents a model.
    run([cmake, "--build", str(build), "--target", "pose_pointwise_test",
         "--parallel", str(args.jobs)], build / "build.log")
    entries = json.loads((build / "compile_commands.json").read_text())
    candidate = next(e for e in entries if e["file"].endswith("/pose_pointwise/pose_pointwise.cc"))
    harness = next(e for e in entries if e["file"].endswith("/pose_pointwise_test.cc"))
    options = shlex.split(candidate["command"])
    optimization = [o for o in options if re.fullmatch(r"-O(?:[0-3sgz]|fast)", o)]
    if (not optimization or optimization[-1] != "-O3" or "-DNDEBUG" not in options or
            "-DNDEBUG" in shlex.split(harness["command"])):
        raise RuntimeError("Expected production O3/private NDEBUG kernel and active test checks")
    for entry in (candidate, harness):
        actual = shlex.split(entry["command"])
        if not all(flag in actual for flag in FLAGS.split()):
            raise RuntimeError("Missing required sanitizer/strict-float compile options")
        float_options = [o for o in actual if o in ("-ffast-math", "-fno-fast-math")]
        contract_options = [o for o in actual if o.startswith("-ffp-contract=")]
        if float_options[-1] != "-fno-fast-math" or contract_options[-1] != "-ffp-contract=off":
            raise RuntimeError("Strict floating-point options were overridden")
    env = os.environ.copy()
    env["ASAN_OPTIONS"] = "detect_leaks=1:halt_on_error=1"
    env["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
    run([str(build / "pose_pointwise_test")], build / "pointwise-test.log", env)
    output = (build / "pointwise-test.log").read_text()
    match = re.search(r"^PASS (\d+) checks; 1600 seeded random/extreme cases; reference exact, no devices\.$", output, re.MULTILINE)
    if not match or int(match[1]) < 10065:
        raise RuntimeError("Missing complete kernel regression marker")
    paths = ("pose-runtime.lock.json", "firmware/pose_runtime/CMakeLists.txt",
             "firmware/components/pose_pointwise/pose_pointwise.cc",
             "tests/native/pose_pointwise_test.cc")
    report.update(passed=True, checks=int(match[1]), seeded_cases=1600,
                  production_cmake_used_directly=True,
                  hashes={p: sha(ROOT / p) for p in paths},
                  private_ndebug=True, effective_optimization="O3")
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"PASS {report['checks']} checks under ASan/UBSan/leak detection; host only.\nReport: {report_path}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        sys.exit(str(error))
