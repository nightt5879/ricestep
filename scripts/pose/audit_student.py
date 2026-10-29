"""Replay crop-model controls using hash-verified training sources, CPU only.

Requires an exact completed best checkpoint, its original image manifest and a
new output directory below this repository's .cache. No downloads, training,
devices, firmware changes or calibrated confidence/accuracy claims.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import types

import numpy as np
from PIL import Image, ImageDraw, __version__ as PIL_VERSION
import torch

ROOT = Path(__file__).resolve().parents[2]
SOURCE_NAMES = ("student_model.py", "student_data.py", "train_student.py")
GROUPS = {"all": range(17), "face": range(5), "arms": range(5, 11),
          "legs": range(11, 17), "elbows_wrists": (7, 8, 9, 10),
          "knees_ankles": (13, 14, 15, 16)}
EDGES = [(5, 6), (5, 7), (7, 9), (6, 8), (8, 10), (5, 11), (6, 12),
         (11, 12), (11, 13), (13, 15), (12, 14), (14, 16)]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def verify_inputs(checkpoint, manifest):
    """Reject altered artifacts before executing any saved training source."""
    checkpoint, manifest = Path(checkpoint).resolve(), Path(manifest).resolve()
    report_bytes = (checkpoint.parent / "training-report.json").read_bytes()
    report = json.loads(report_bytes)
    if report.get("status") != "completed":
        raise ValueError("Training report is not completed")
    checkpoint_bytes, manifest_bytes = checkpoint.read_bytes(), manifest.read_bytes()
    if sha(checkpoint_bytes) != report.get("best_checkpoint_sha256"):
        raise ValueError("Checkpoint differs from recorded best checkpoint")
    if sha(manifest_bytes) != report.get("manifest_sha256"):
        raise ValueError("Manifest differs from training provenance")
    sources = {}
    for name in SOURCE_NAMES:
        raw = (checkpoint.parent / "source-snapshot" / name).read_bytes()
        if sha(raw) != report.get("source_sha256", {}).get(name):
            raise ValueError("Training source snapshot hash mismatch: " + name)
        sources[name] = raw
    return report, report_bytes, checkpoint_bytes, manifest_bytes, sources


def snapshot_module(name, raw, source_path):
    # Execute the already verified local source bytes, without import fallback
    # to current repository files or writing pycache beside the input snapshot.
    module = types.ModuleType("ricestep_audit_" + name.removesuffix(".py"))
    module.__file__ = str(source_path)
    exec(compile(raw, str(source_path), "exec"), module.__dict__)
    return module


def training_template(dataset, sample):
    """Only training labels contribute; validation rows are never sampled here."""
    total, count = np.zeros((17, 2), np.float64), np.zeros(17, np.int64)
    for row in dataset["train"]:
        item = sample(row, augment=False)
        valid = item["valid"] > 0
        total += item["points"] * valid[:, None]
        count += valid
    if np.any(count == 0) or not np.isfinite(total).all():
        raise ValueError("Training template requires finite labeled data for all 17 joints")
    return total / count[:, None], count


def derangement(size, seed=26029):
    if size < 2:
        raise ValueError("Shuffled control requires at least two validation images")
    rng = np.random.default_rng(seed)
    for _ in range(1000):
        permutation = rng.permutation(size)
        if np.all(permutation != np.arange(size)):
            return permutation
    raise RuntimeError("Failed to construct seeded derangement")


def summarize(values):
    values = np.asarray(values)
    if not len(values) or not np.isfinite(values).all():
        raise ValueError("Metrics require finite labeled keypoint errors")
    return dict(labeled_joints=int(len(values)), mean_normalized_error=float(np.mean(values)),
                median_normalized_error=float(np.median(values)),
                pck_005=float(np.mean(values <= .05)), pck_010=float(np.mean(values <= .10)))


def diagnostic_sheet(out, samples, truth, mask, predictions, per_image):
    # Evenly spaced samples, not chosen for model performance.
    chosen = np.linspace(0, len(samples) - 1, min(16, len(samples)), dtype=int)
    tile_w, tile_h = 244, 286
    sheet = Image.new("RGB", (4 * tile_w, ((len(chosen) + 3) // 4) * tile_h), (24, 24, 28))
    text = ImageDraw.Draw(sheet)
    for tile, index in enumerate(chosen):
        item = samples[index]
        origin = ((tile % 4) * tile_w, (tile // 4) * tile_h)
        rgb = np.clip(np.rint((item["input"].transpose(1, 2, 0) + 1) * 127.5), 0, 255).astype(np.uint8)
        picture = Image.fromarray(rgb).resize((240, 240), Image.Resampling.NEAREST)
        draw = ImageDraw.Draw(picture)
        for points, color in ((truth[index], (40, 245, 80)),
                              (predictions["real_images"][index], (255, 145, 20))):
            for a, b in EDGES:
                if mask[index, a] and mask[index, b]:
                    draw.line([tuple(points[a, :2] * 10), tuple(points[b, :2] * 10)], fill=color, width=2)
            for joint in np.flatnonzero(mask[index]):
                x, y = points[joint, :2] * 10
                draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=color)
        sheet.paste(picture, (origin[0] + 2, origin[1] + 20))
        metrics = per_image[index]["metrics"]
        text.text((origin[0] + 3, origin[1] + 2),
                  f"ID {item['image_id']} GT green / model orange", fill="white")
        text.text((origin[0] + 3, origin[1] + 264),
                  f"PCK.1 real {metrics['real_images']['pck_010']:.2f} mean {metrics['train_mean_template']['pck_010']:.2f}", fill="white")
    sheet.save(out / "contact-sheet.png")
    return chosen.tolist()


def run(checkpoint, manifest, output):
    checkpoint, manifest, out = map(lambda p: Path(p).resolve(), (checkpoint, manifest, output))
    cache = (ROOT / ".cache").resolve()
    if out == cache or not out.is_relative_to(cache) or out.exists():
        raise ValueError("Output must be a new subdirectory beneath repository .cache")
    training, training_bytes, checkpoint_bytes, manifest_bytes, sources = verify_inputs(checkpoint, manifest)
    saved_root = checkpoint.parent / "source-snapshot"
    model_source = snapshot_module("student_model.py", sources["student_model.py"], saved_root / "student_model.py")
    data_source = snapshot_module("student_data.py", sources["student_data.py"], saved_root / "student_data.py")
    dataset = data_source.load_manifest(manifest)  # Checks original image bytes/hashes and split isolation.
    if (len(dataset["train"]), len(dataset["val"])) != (training["training_images"], training["validation_images"]):
        raise ValueError("Dataset counts differ from training report")
    if {r["image_id"] for r in dataset["train"]} & {r["image_id"] for r in dataset["val"]}:
        raise ValueError("Training and validation image identifiers overlap")
    permutation = derangement(len(dataset["val"]))
    state = torch.load(io.BytesIO(checkpoint_bytes), map_location="cpu", weights_only=True)
    if (state.get("schema") != 1 or state.get("manifest_sha256") != training["manifest_sha256"]
            or state.get("epoch") != training["best_epoch"]):
        raise ValueError("Checkpoint provenance differs from completed training report")
    if any(not torch.isfinite(value).all() for value in state["model"].values()):
        raise ValueError("Checkpoint contains nonfinite model state")
    torch.set_num_threads(2)
    model = model_source.TinyPose().cpu().eval()
    model.load_state_dict(state["model"], strict=True)
    out.mkdir(parents=True, exist_ok=False)
    (out / "input-manifest.json").write_bytes(manifest_bytes)
    (out / "training-report.json").write_bytes(training_bytes)
    (out / "source-snapshot").mkdir()
    for name, raw in sources.items():
        (out / "source-snapshot" / name).write_bytes(raw)
    print(f"Verified {len(dataset['train'])} train / {len(dataset['val'])} val image hashes and source provenance", flush=True)
    template, count = training_template(dataset, data_source.sample)
    samples = [data_source.sample(row, augment=False) for row in dataset["val"]]
    truth = np.stack([item["points"] for item in samples])
    mask = np.stack([item["valid"] for item in samples]) > 0
    diagonal = np.array([item["box_diagonal_grid"] for item in samples])[:, None]
    if not np.isfinite(diagonal).all() or np.any(diagonal <= 0) or not np.all(mask.any(axis=1)):
        raise ValueError("Each validation target requires a positive box diagonal and labeled crop joints")
    images = np.stack([item["input"] for item in samples])

    def infer(inputs):
        batches = []
        with torch.inference_mode():
            for start in range(0, len(inputs), 16):
                logits, offsets = model(torch.from_numpy(inputs[start:start + 16]))
                batches.append(data_source.decode(logits.numpy(), offsets.numpy()))
        return np.concatenate(batches)

    n = len(images)
    predictions = {"real_images": infer(images), "blank_black": infer(np.full_like(images, -1)),
                   "blank_midgray": infer(np.zeros_like(images)), "shuffled_images": infer(images[permutation]),
                   "train_mean_template": np.broadcast_to(template, (n, 17, 2)).copy()}
    report = dict(schema=1, host_only=True, cpu_only=True, training_performed=False, device_used=False,
        checkpoint_sha256=sha(checkpoint_bytes), checkpoint_epoch=state["epoch"],
        training_report_sha256=sha(training_bytes), manifest_sha256=sha(manifest_bytes),
        evaluator_sha256=sha(Path(__file__).read_bytes()),
        versions={"torch": torch.__version__, "numpy": np.__version__, "pillow": PIL_VERSION},
        source_sha256={name: sha(raw) for name, raw in sources.items()},
        source_policy="Uses verified training source-snapshot bytes, not current repository model/data source",
        seed=26029, training_images=len(dataset["train"]), validation_images=n,
        labeled_validation_joints=int(mask.sum()),
        template={"construction": "Per-joint mean of valid training labels after deterministic GT-box crop; no augmentation",
                  "valid_train_counts": count.tolist(), "xy_grid": template.tolist()},
        shuffle={"kind": "fixed seeded derangement; original validation targets unchanged",
                 "permutation": permutation.tolist(), "target_image_ids": [s["image_id"] for s in samples],
                 "input_image_ids": [samples[i]["image_id"] for i in permutation]},
        normalization="Euclidean joint error/original target bbox diagonal expressed in crop-grid units",
        valid_labels="COCO visibility>0 and coordinate inside deterministic crop; includes labeled occluded joints",
        results={}, per_image=[], limitations=[
            "Best epoch selected using this same validation split; not an independent test set",
            "GT-box crop supplies target location/scale prior; not full-FOV camera evaluation",
            "No fitness-video anatomical truth, COCO AP, calibrated confidence or board/FPS claim",
            "Black/gray/shuffled controls diagnose image dependence, not generalization to all negatives",
            "No training/device/audio/network/firmware changes"])
    errors = {}
    for name, prediction in predictions.items():
        error = np.linalg.norm(prediction[:, :, :2] - truth, axis=-1) / diagonal
        errors[name] = error
        result = {group: summarize(error[:, list(ids)][mask[:, list(ids)]]) for group, ids in GROUPS.items()}
        if prediction.shape[-1] == 3:
            result["uncalibrated_peak_mean"] = float(prediction[:, :, 2][mask].mean())
            delta = np.linalg.norm(prediction[:, :, :2] - predictions["real_images"][:, :, :2], axis=-1) / diagonal
            result["prediction_delta_from_real_mean"] = float(delta[mask].mean())
        report["results"][name] = result
        print(name, json.dumps(result["all"]), flush=True)
    for i, item in enumerate(samples):
        report["per_image"].append(dict(image_id=item["image_id"], valid_joint_ids=np.flatnonzero(mask[i]).tolist(),
            truth_xy_grid=truth[i].tolist(), predictions={key: value[i].tolist() for key, value in predictions.items()},
            metrics={key: summarize(value[i][mask[i]]) for key, value in errors.items()}))
    report["paired_real_vs_template"] = dict(
        images_real_lower_mean_error=int(sum(errors["real_images"][i][mask[i]].mean() <
            errors["train_mean_template"][i][mask[i]].mean() for i in range(n))),
        images_real_higher_pck010=int(sum((errors["real_images"][i][mask[i]] <= .1).mean() >
            (errors["train_mean_template"][i][mask[i]] <= .1).mean() for i in range(n))))
    report["diagnostic_image_indices"] = diagnostic_sheet(out, samples, truth, mask, predictions, report["per_image"])
    (out / "audit.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print("Completed host controls:", out / "audit.json", flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.checkpoint, args.manifest, args.output)


if __name__ == "__main__":
    main()
