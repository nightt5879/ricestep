"""Offline full-frame student diagnostics; no camera, training, or rewards.

Uses the cached RGB565_BE exercise frames, with their existing video letterbox
preserved. The student's additional 96-square letterbox is explicitly inverted.
Sigmoid peaks are uncalibrated. C session counts are diagnostic outputs, not
accuracy measurements or production authorization to award rice.
"""
from __future__ import annotations

import argparse
from collections import Counter
import ctypes as C
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, __version__ as PIL_VERSION
import torch

from audit_student import architecture_id, load_snapshot_model, verify_checkpoint

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INDEX = ROOT / ".cache/research-pose/tfjs-exercise/squats-rgb565be/index.json"
SOURCES = ["firmware/components/pose_session/pose_session.c",
           "firmware/components/pose_image/pose_image.c",
           "firmware/components/motion_logic/motion_logic.c"]
HEADERS = ["firmware/components/pose_session/pose_session.h",
           "firmware/components/pose_image/pose_image.h",
           "firmware/components/motion_logic/motion_logic.h",
           "firmware/components/pose_decode/pose_movenet.h"]
PHASES = ["wait_top", "ready", "need_bottom", "need_top"]
MOTION_REASONS = ["ok", "not_started", "paused", "low_confidence", "bad_geometry",
                  "frame_gap", "duplicate_time", "reversed_time", "person_changed",
                  "side_changed", "too_fast", "too_slow", "refractory"]
EDGES = [(5, 6), (5, 7), (7, 9), (6, 8), (8, 10), (5, 11), (6, 12),
         (11, 12), (11, 13), (13, 15), (12, 14), (14, 16)]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def rgb565(raw, width=320, height=240):
    if len(raw) != width * height * 2:
        raise ValueError("RGB565_BE frame byte count mismatch")
    words = np.frombuffer(raw, dtype=">u2").reshape(height, width)
    r, g, b = words >> 11, (words >> 5) & 63, words & 31
    return np.stack(((r << 3) | (r >> 2), (g << 2) | (g >> 4),
                     (b << 3) | (b >> 2)), axis=-1).astype(np.uint8)


def prepare(rgb):
    """Full 320x240 FOV -> 96x72 content + 12px top/bottom black, no AA."""
    if rgb.shape != (240, 320, 3) or rgb.dtype != np.uint8:
        raise ValueError("This exercise audit requires 320x240 uint8 RGB")
    image = Image.fromarray(rgb).transform((96, 96), Image.Transform.AFFINE,
        (320 / 96, 0, 0, 0, 320 / 96, -40),
        Image.Resampling.BILINEAR, fillcolor=(0, 0, 0))
    return np.asarray(image, dtype=np.float32).transpose(2, 0, 1) / 127.5 - 1


def unletterbox(decoded):
    """Grid x/y units -> original normalized x/y; never clamp padded outputs."""
    points = np.asarray(decoded, dtype=np.float32).copy()
    if points.shape != (17, 3) or not np.isfinite(points).all():
        raise ValueError("Expected finite student [17,x/y/score] coordinates")
    points[:, 0] /= 24
    points[:, 1] = (points[:, 1] / 24 - 12 / 96) / (72 / 96)
    valid = ((points[:, :2] >= 0).all(1) & (points[:, :2] <= 1).all(1)
             & (points[:, 2] >= 0) & (points[:, 2] <= 1))
    points[~valid, 2] = 0
    return points, valid


def angle(points, a, b, c):
    # Correct the original frame's 4:3 aspect before computing any angle.
    xy = points[:, :2] * np.array([4 / 3, 1], dtype=np.float32)
    u, v = xy[a] - xy[b], xy[c] - xy[b]
    length = float(np.linalg.norm(u) * np.linalg.norm(v))
    return None if length < .0004 else float(np.degrees(np.arccos(
        np.clip(float(np.dot(u, v)) / length, -1, 1))))


def side_metrics(points):
    result = {}
    for side, name in enumerate(("left", "right")):
        ids = np.array([5, 11, 13, 15]) + side
        qualified = bool(np.all(points[ids, 2] >= .60))
        inside = bool(np.all((points[ids, :2] >= 0) & (points[ids, :2] <= 1)))
        result[name] = {"minimum_uncalibrated_score": float(points[ids, 2].min()),
                        "required_points_qualified_at_060": qualified and inside,
                        "raw_knee_angle_degrees": angle(points, 11+side, 13+side, 15+side)
                        if inside else None}
    return result


BRIDGE = r'''
#include "pose_session.h"
#include <stddef.h>
#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif
static ps_session session;
struct audit_result {
    int reason, motion_reason, phase, side;
    uint32_t reps, completed, valid_mask, token;
    float angle;
};
EXPORT int audit_start(void) { return ps_start(&session, ML_SQUAT, 1); }
EXPORT const char *audit_reason(int reason) { return ps_reason_string((ps_reason)reason); }
EXPORT int audit_step(const float *original, uint32_t capture_ms, struct audit_result *out) {
    if (!original || !out) return 0;
    pose_movenet_landmark points[17];
    /* Student96 was already explicitly unletterboxed in Python. Re-encode
       original normalized coordinates solely for the unchanged session API's
       canonical192 transform. This is NOT a192 image/model/preprocessing run.
       96x72+pad12 and192x144+pad24 have identical normalized content bounds. */
    const pi_transform transform = {320,240,192,144,0,24};
    for (unsigned i=0;i<17;++i) {
        points[i].x=original[3*i];
        points[i].y=.125f+.75f*original[3*i+1];
        points[i].score=original[3*i+2];
        if (points[i].score > 0) {
            pi_source_point p;
            if (pi_unletterbox(&transform,points[i].x,points[i].y,&p)!=PI_OK) return 0;
            /* Fail rather than silently use incompatible C geometry. */
            float dx=p.x-original[3*i],dy=p.y-original[3*i+1];
            if (dx>1e-6f || dx< -1e-6f || dy>1e-6f || dy< -1e-6f) return 0;
        }
    }
    ps_result r=ps_update(&session,points,&transform,capture_ms,capture_ms+100);
    *out=(struct audit_result){r.reason,r.exercise.reason,r.exercise.phase,session.side,
        r.exercise.total_reps,r.exercise.rep_completed,r.valid_points,
        r.continuity_token,r.exercise.angle_degrees};
    return 1;
}
'''


class Result(C.Structure):
    _fields_ = [(name, C.c_int) for name in ("reason", "motion_reason", "phase", "side")]
    _fields_ += [(name, C.c_uint32) for name in ("reps", "completed", "valid_mask", "token")]
    _fields_ += [("angle", C.c_float)]


def build_bridge(out, compiler):
    executable = shutil.which(compiler)
    if not executable:
        raise ValueError("A local C11 compiler (clang/gcc) is required; nothing is installed")
    bridge = out / "session_bridge.c"
    bridge.write_text(BRIDGE, encoding="utf-8")
    library = out / ("session_bridge.dll" if sys.platform == "win32" else "session_bridge.so")
    target = subprocess.check_output([executable, "-dumpmachine"], text=True).strip()
    arguments = [executable, "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wpedantic",
                 "-O2", "-fno-fast-math", "-ffp-contract=off", "-shared"]
    if sys.platform != "win32":
        arguments += ["-fPIC"]
    for component in ("pose_session", "pose_image", "motion_logic", "pose_decode"):
        arguments += ["-I", str(ROOT / "firmware/components" / component)]
    arguments += [str(ROOT / source) for source in SOURCES] + [str(bridge)]
    if "windows-msvc" not in target:
        arguments += ["-lm"]
    arguments += ["-o", str(library)]
    with (out / "compile.log").open("w", encoding="utf-8") as log:
        subprocess.run(arguments, cwd=out, stdout=log, stderr=subprocess.STDOUT, check=True)
    lib = C.CDLL(str(library))
    lib.audit_start.restype = C.c_int
    lib.audit_step.argtypes = [C.POINTER(C.c_float), C.c_uint32, C.POINTER(Result)]
    lib.audit_step.restype = C.c_int
    lib.audit_reason.argtypes = [C.c_int]
    lib.audit_reason.restype = C.c_char_p
    if lib.audit_start() != 1:
        raise RuntimeError("Actual C session refused explicit diagnostic start")
    evidence = {"command": arguments, "target": target,
                "compiler_version": subprocess.check_output([executable, "--version"], text=True).splitlines()[0],
                "library_sha256": sha(library.read_bytes()),
                "bridge_sha256": sha(bridge.read_bytes()),
                "source_sha256": {name: sha((ROOT/name).read_bytes()) for name in SOURCES + HEADERS}}
    return lib, evidence


def geometry_checks():
    np.testing.assert_array_equal(rgb565(bytes.fromhex("f80007e0001fffff0000"), 5, 1)[0],
        [[255,0,0],[0,255,0],[0,0,255],[255,255,255],[0,0,0]])
    white = prepare(np.full((240,320,3),255,np.uint8))
    if not (np.all(white[:, :12] == -1) and np.all(white[:, 84:] == -1)
            and np.all(white[:, 12:84] == 1)):
        raise AssertionError("96px full-FOV padding does not match documented transform")
    points = np.tile([12,12,.9], (17,1)).astype(np.float32)
    points[:4] = [[0,3,.9],[24,21,.9],[12,2.999,.9],[24.001,12,.9]]
    mapped, valid = unletterbox(points)
    np.testing.assert_allclose(mapped[:2,:2], [[0,0],[1,1]], atol=1e-6)
    np.testing.assert_array_equal(valid[:4], [True,True,False,False])
    np.testing.assert_array_equal(mapped[2:4,2], [0,0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--output", type=Path, required=True, help="Fresh directory under repo .cache")
    parser.add_argument("--compiler", default="clang")
    parser.add_argument("--contact-sheet", action="store_true")
    args = parser.parse_args()
    out = args.output.resolve()
    if not out.is_relative_to((ROOT/".cache").resolve()) or out.exists():
        raise ValueError("Output must be a new directory beneath repository .cache")
    geometry_checks()
    training, training_bytes, checkpoint_bytes, snapshots = verify_checkpoint(args.checkpoint)
    torch.set_num_threads(2)
    model, data_source, state = load_snapshot_model(training, checkpoint_bytes, snapshots,
                                                  args.checkpoint.parent / "source-snapshot")
    index = json.loads(args.index.read_text(encoding="utf-8"))
    if (index["width"],index["height"],index["stride_bytes"],index["pixel_format"]) != (320,240,640,"RGB565_BE"):
        raise ValueError("Unexpected exercise frame geometry/format")
    source = (args.index.parent/index["source"]).resolve()
    if not source.is_relative_to(args.index.parent.parent.resolve()) or sha(source.read_bytes()) != index["source_sha256"]:
        raise ValueError("Original video provenance mismatch")
    frames, pixels, previous = [], [], -1
    for number, frame in enumerate(index["frames"]):
        path = (args.index.parent/frame["file"]).resolve()
        if not path.is_relative_to(args.index.parent.resolve()):
            raise ValueError("Frame path escapes manifest directory")
        raw = path.read_bytes()
        if (len(raw) != frame["bytes"] or sha(raw) != frame["sha256"] or frame["index"] != number
                or not isinstance(frame["capture_ms"],int) or not previous < frame["capture_ms"] < 2**32-1100):
            raise ValueError("Frame hash/size/order/timestamp mismatch")
        pixels.append(rgb565(raw)); frames.append(frame); previous = frame["capture_ms"]
    if len(frames) != 65:
        raise ValueError("Expected the pinned 65-frame exercise sequence")
    out.mkdir(parents=True)
    (out/"training-report.json").write_bytes(training_bytes)
    (out/"source-snapshot").mkdir()
    for name, raw in snapshots.items():
        (out/"source-snapshot"/name).write_bytes(raw)
    lib, native = build_bridge(out, args.compiler)
    records = []
    with torch.inference_mode():
        for frame, rgb in zip(frames, pixels):
            logits, offsets = model(torch.from_numpy(prepare(rgb)[None]))
            decoded = data_source.decode(logits.numpy(), offsets.numpy())[0]
            points, inside = unletterbox(decoded)
            native_result = Result()
            capture = 1000 + frame["capture_ms"]
            if lib.audit_step(points.ctypes.data_as(C.POINTER(C.c_float)), capture, C.byref(native_result)) != 1:
                raise RuntimeError("Native normalized coordinate adapter failed")
            records.append({"frame":frame["index"], "video_pts_ms":frame["capture_ms"],
                "capture_ms_synthetic":capture,"processed_ms_synthetic":capture+100,
                "decoded_grid_xy_uncalibrated_score":decoded.tolist(),
                "original_xy_uncalibrated_score":points.tolist(),"inside_original_mask":inside.tolist(),
                "side_metrics":side_metrics(points),"session_reason":lib.audit_reason(native_result.reason).decode(),
                "motion_reason":MOTION_REASONS[native_result.motion_reason],
                "phase":PHASES[native_result.phase],"selected_side":native_result.side,
                "diagnostic_cumulative_cycles":native_result.reps,"diagnostic_cycle_completed":bool(native_result.completed),
                "session_valid_mask":native_result.valid_mask,"continuity_token":native_result.token,
                "session_angle_degrees":native_result.angle})
    angles = [row["session_angle_degrees"] for row in records if row["session_angle_degrees"] >= 0]
    summary = {"frames":len(records), "session_reasons":dict(Counter(row["session_reason"] for row in records)),
        "session_phases":dict(Counter(row["phase"] for row in records)),
        "session_knee_angle_range_degrees":[min(angles),max(angles)] if angles else None,
        "any_required_side_qualified_frames":sum(any(side["required_points_qualified_at_060"]
            for side in row["side_metrics"].values()) for row in records),
        "diagnostic_complete_cycles":records[-1]["diagnostic_cumulative_cycles"],
        "cycle_frames":[row["frame"] for row in records if row["diagnostic_cycle_completed"]],
        "maximum_uncalibrated_sigmoid_peak":max(point[2] for row in records for point in row["decoded_grid_xy_uncalibrated_score"])}
    report = {"schema":1,"host_only":True,"physical_camera_used":False,"device_used":False,
        "architecture":architecture_id(training),
        "firmware_changed":False,"training_performed":False,"reward_source":False,
        "confidence_calibrated":False,"accuracy_or_fps_claim":False,
        "checkpoint_sha256":sha(checkpoint_bytes),"training_manifest_sha256":state["manifest_sha256"],
        "evaluator_sha256":sha(Path(__file__).read_bytes()),
        "model_data_source_sha256":{name:sha(raw) for name,raw in snapshots.items()},
        "training_report_sha256":sha(training_bytes),
        "source_policy":"Uses verified training source-snapshot bytes, not current repository model/data source",
        "snapshot_loader_sha256":sha((Path(__file__).parent/"audit_student.py").read_bytes()),
        "checkpoint_epoch":state["epoch"],"source_video_sha256":index["source_sha256"],
        "frame_manifest_sha256":sha(args.index.read_bytes()),"frame_sha256":[f["sha256"] for f in frames],
        "preprocessing":{"source":[320,240],"input":[96,96],"content":[96,72],"pad_left_top":[0,12],
            "crop":False,"rgb565_expansion":"bit replication","sampling":"PIL inverse affine bilinear, no antialias",
            "normalization":"RGB / 127.5 - 1","preserved_input_letterbox":index["transform"]},
        "session_adapter":"Explicit grid24 to original xy via 96x72+pad12. Original xy re-encoded into canonical192 solely for unchanged ps_update; normalized bounds identical. No192 image or CNN.",
        "timing":"Capture=1000+video PTS; processed=capture+100ms synthetic. Does not measure runtime or board FPS.",
        "limitations":["No human-labelled frame truth; this is not an accuracy score.",
            "Uncalibrated sigmoid peaks fed to unchanged0.60 gate only for diagnostics; no threshold tuning.",
            "Crop-trained student evaluated on complete cached FOV without GT boxes or tracking crops.",
            "C cycles are hypothetical diagnostic gate outputs, never game rewards or proof of exercise recognition."],
        "versions":{"python":sys.version,"torch":torch.__version__,"numpy":np.__version__,"pillow":PIL_VERSION},
        "native_bridge":native,"summary":summary,"frames":records}
    (out/"report.json").write_text(json.dumps(report,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    if args.contact_sheet:
        chosen = np.linspace(0,len(frames)-1,8,dtype=int)
        sheet = Image.new("RGB",(4*320,2*280),(24,24,24))
        for tile, i in enumerate(chosen):
            image = Image.fromarray(pixels[i]); draw = ImageDraw.Draw(image)
            points = np.asarray(records[i]["original_xy_uncalibrated_score"])
            for a,b in EDGES:
                if points[a,2]>=.60 and points[b,2]>=.60:
                    draw.line([tuple(points[a,:2]*[320,240]),tuple(points[b,:2]*[320,240])],fill=(0,255,120),width=2)
            for j,point in enumerate(points):
                if records[i]["inside_original_mask"][j]:
                    x,y = point[:2]*[320,240]
                    color = (0,255,120) if point[2]>=.60 else (255,170,0)
                    draw.ellipse((x-2,y-2,x+2,y+2),outline=color)
            x,y = (tile%4)*320,(tile//4)*280
            sheet.paste(image,(x,y))
            ImageDraw.Draw(sheet).text((x+4,y+242),f"HOST ONLY frame {i}: {records[i]['session_reason']}\nuncalibrated; green >=0.60, orange <0.60",fill="white")
        sheet.save(out/"contact-sheet.png")
    print(json.dumps(summary,indent=2))
    print("Host-only diagnostic report:",out/"report.json")


if __name__ == "__main__":
    main()
