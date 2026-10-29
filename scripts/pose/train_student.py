"""Train an experimental small pose model on declared, image-disjoint COCO crops.

This is a development experiment, not a camera detector or a firmware release.
No data uploads, physical devices, audio, reward integration or automatic installs.
"""

import argparse
import hashlib
import json
import random
import shutil
import time
from pathlib import Path

import numpy as np
import torch
from torch.nn import functional as F

from student_data import decode, load_manifest, sample
from student_model import TinyPose, export_numpy_weights, profile_model

ROOT = Path(__file__).resolve().parents[2]


def batch(rows, *, augment, seed, device):
    samples = [sample(row, augment=augment, seed=seed + i) for i, row in enumerate(rows)]
    tensors = {key: torch.from_numpy(np.stack([s[key] for s in samples])).to(device)
               for key in ("input", "heatmaps", "offsets", "offset_mask", "valid")}
    return tensors, samples


def objective(outputs, data, heatmap_loss="spatial", amplitude_weight=5.0):
    logits, offsets = outputs
    mask = data["valid"][..., None, None]
    if heatmap_loss == "mse":
        heat = ((torch.sigmoid(logits) - data["heatmaps"]) ** 2 * mask).sum()
        heat = heat * 100 / (mask.sum().clamp_min(1) * 24 * 24)
    elif heatmap_loss == "spatial":
        # Every labeled joint must put probability mass at its location. A
        # uniformly dark heatmap cannot minimize this loss by exploiting the
        # large number of background cells. These logits are not calibrated
        # visibility probabilities; deployment confidence needs separate work.
        target = data["heatmaps"].flatten(2)
        target = target / target.sum(dim=-1, keepdim=True).clamp_min(1e-8)
        ce = -(target * F.log_softmax(logits.flatten(2), dim=-1)).sum(dim=-1)
        heat = (ce * data["valid"]).sum() / data["valid"].sum().clamp_min(1)
        # Spatial CE is invariant to adding an arbitrary constant to every
        # cell. Anchor the logit amplitude with an additional Gaussian target
        # loss, so the reported sigmoid peak is not entirely unconstrained.
        # This still does NOT statistically calibrate visibility/accuracy.
        amplitude = (F.binary_cross_entropy_with_logits(logits, data["heatmaps"], reduction="none") * mask).sum()
        heat = heat + amplitude_weight * amplitude / (mask.sum().clamp_min(1) * 24 * 24)
    else:
        raise ValueError("Unknown heatmap loss")
    residual = (F.smooth_l1_loss(offsets, data["offsets"], reduction="none") * data["offset_mask"]).sum()
    residual = residual / data["offset_mask"].sum().clamp_min(1)
    return heat + residual


def evaluate(model, rows, batch_size, device, heatmap_loss="spatial", amplitude_weight=5.0):
    model.eval()
    distances, peaks, losses = [], [], []
    with torch.no_grad():
        for start in range(0, len(rows), batch_size):
            data, samples = batch(rows[start:start + batch_size], augment=False, seed=0, device=device)
            outputs = model(data["input"])
            losses.append(float(objective(outputs, data, heatmap_loss, amplitude_weight)))
            points = decode(*(v.cpu().numpy() for v in outputs))
            for predicted, s in zip(points, samples):
                keep = s["valid"] > 0
                distances.extend((np.linalg.norm(predicted[keep, :2] - s["points"][keep], axis=1) /
                                  s["box_diagonal_grid"]).tolist())
                peaks.extend(predicted[keep, 2].tolist())
    d = np.asarray(distances)
    if not len(d) or not np.isfinite(d).all():
        raise RuntimeError("Validation has no finite labeled keypoint errors")
    return {"images": len(rows), "labeled_keypoints": len(d),
            "mean_loss": float(np.mean(losses)), "mean_error_bbox_diagonal": float(d.mean()),
            "pck_005_bbox_diagonal": float((d <= 0.05).mean()),
            "pck_010_bbox_diagonal": float((d <= 0.10).mean()),
            "mean_uncalibrated_peak": float(np.mean(peaks)),
            "scope": "GT-box crops; not COCO AP, full-camera accuracy or calibrated confidence"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--seed", type=int, default=26029)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    parser.add_argument("--learning-rate", type=float, default=0.001)
    parser.add_argument("--heatmap-loss", choices=("spatial", "mse"), default="spatial")
    parser.add_argument("--amplitude-weight", type=float, default=5.0,
                        help="Gaussian BCE logit anchor; zero reproduces the early spatial-only pilot")
    args = parser.parse_args()
    if (not 1 <= args.epochs <= 500 or not 1 <= args.batch_size <= 32 or
            not 0 < args.learning_rate <= 0.01 or not 0 <= args.amplitude_weight <= 20):
        parser.error("Invalid training bounds")
    out = (ROOT / args.output_dir).resolve()
    if not out.is_relative_to((ROOT / ".cache").resolve()) or out == (ROOT / ".cache").resolve():
        parser.error("Training artifacts must stay within a subdirectory of repository .cache")
    if out.exists() and any(out.iterdir()):
        parser.error("Use a fresh output directory; existing experiments are not overwritten")
    out.mkdir(parents=True, exist_ok=True)
    snapshots = out / "source-snapshot"
    snapshots.mkdir()
    for name in ("train_student.py", "student_model.py", "student_data.py"):
        shutil.copyfile(Path(__file__).parent / name, snapshots / name)
    dataset = load_manifest(args.manifest)
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.set_num_threads(4)
    if args.device == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA requested but unavailable; no silent CPU fallback")
        torch.cuda.set_per_process_memory_fraction(0.20)
        torch.backends.cudnn.benchmark = False
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
    model = TinyPose().to(args.device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=0.0001)
    report = {"schema": 1, "status": "running", "seed": args.seed,
              "configuration": {"epochs": args.epochs, "batch_size": args.batch_size,
                                "learning_rate": args.learning_rate, "weight_decay": 0.0001,
                                "heatmap_loss": args.heatmap_loss, "amplitude_weight": args.amplitude_weight},
              "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
              "versions": {"torch": torch.__version__, "numpy": np.__version__},
              "device": args.device, "parameter_count": sum(p.numel() for p in model.parameters()),
              "architecture_profile": profile_model(model),
              "source_sha256": {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                                for name in ("train_student.py", "student_model.py", "student_data.py")},
              "training_images": len(dataset["train"]), "validation_images": len(dataset["val"]),
              "background_candidates_used": False, "hardware_validated": False,
              "confidence_calibrated": False, "firmware_changed": False,
              "initial_validation": evaluate(model, dataset["val"], args.batch_size, args.device,
                                             args.heatmap_loss, args.amplitude_weight),
              "epochs": []}
    started = time.monotonic()
    path = out / "training-report.json"

    def save_report():
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    save_report()
    best = -1.0
    for epoch in range(args.epochs):
        order = np.random.default_rng(args.seed + epoch).permutation(len(dataset["train"]))
        model.train()
        losses = []
        for start in range(0, len(order), args.batch_size):
            rows = [dataset["train"][i] for i in order[start:start + args.batch_size]]
            data, _ = batch(rows, augment=True, seed=args.seed + epoch * 100000 + start, device=args.device)
            optimizer.zero_grad(set_to_none=True)
            loss = objective(model(data["input"]), data, args.heatmap_loss, args.amplitude_weight)
            if not torch.isfinite(loss):
                raise RuntimeError("Nonfinite training loss; experiment stopped")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0, error_if_nonfinite=True)
            optimizer.step()
            losses.append(float(loss.detach()))
        validation = evaluate(model, dataset["val"], args.batch_size, args.device,
                              args.heatmap_loss, args.amplitude_weight)
        row = {"epoch": epoch + 1, "mean_training_loss": float(np.mean(losses)),
               "validation": validation, "elapsed_seconds": time.monotonic() - started}
        report["epochs"].append(row)
        state = {"schema": 1, "epoch": epoch + 1,
                 "model": {name: value.detach().cpu().clone() for name, value in model.state_dict().items()},
                 "manifest_sha256": report["manifest_sha256"]}
        torch.save(state, out / "latest.pt")
        if validation["pck_010_bbox_diagonal"] > best:
            best = validation["pck_010_bbox_diagonal"]
            torch.save(state, out / "best.pt")
            report["best_epoch"] = epoch + 1
        save_report()
        print(json.dumps({"epoch": epoch + 1, "train_loss": row["mean_training_loss"],
                          "val_pck_010": validation["pck_010_bbox_diagonal"]}), flush=True)
    report["elapsed_seconds"] = time.monotonic() - started
    if args.device == "cuda":
        report["peak_cuda_tensor_bytes"] = torch.cuda.max_memory_allocated()
        report["peak_cuda_reserved_bytes"] = torch.cuda.max_memory_reserved()
    # This cache contains actual training pixels for explicit PTQ calibration,
    # not random noise. It is separate from held-out validation images.
    calibration = np.stack([sample(r)["input"].transpose(1, 2, 0) for r in dataset["train"][:32]])
    np.savez_compressed(out / "calibration.npz", images=calibration)
    calibration_metadata = {"source": "COCO 2017 official train subset; GT-box square crops",
                            "synthetic": False, "preprocessing": "rgb*(2/255)-1",
                            "geometry": "bbox-centered square, margin 1.5, black exterior, PIL bilinear, 96x96",
                            "sample_count": len(calibration), "manifest_sha256": report["manifest_sha256"],
                            "image_ids": [r["image_id"] for r in dataset["train"][:32]],
                            "npz_sha256": hashlib.sha256((out / "calibration.npz").read_bytes()).hexdigest()}
    (out / "calibration.json").write_text(json.dumps(calibration_metadata, indent=2) + "\n", encoding="utf-8")
    best_state = torch.load(out / "best.pt", map_location="cpu", weights_only=True)
    model.load_state_dict(best_state["model"])
    model.eval()
    export_numpy_weights(model, out / "weights.npz", metadata={
        "training_status": "trained_experiment", "epoch": best_state["epoch"],
        "manifest_sha256": report["manifest_sha256"], "seed": args.seed,
        "confidence_calibrated": False, "hardware_validated": False})
    parity_images = np.stack([sample(r)["input"] for r in dataset["val"][:16]])
    with torch.no_grad():
        logits, offsets = model(torch.from_numpy(parity_images).to(args.device))
    np.savez(out / "parity-reference.npz", images=parity_images.transpose(0, 2, 3, 1),
             logits=logits.cpu().numpy().transpose(0, 2, 3, 1),
             offsets=offsets.cpu().numpy().transpose(0, 2, 3, 1))
    report["status"] = "completed"
    report["best_checkpoint_sha256"] = hashlib.sha256((out / "best.pt").read_bytes()).hexdigest()
    save_report()
    print(f"Completed crop-training experiment: {path}", flush=True)


if __name__ == "__main__":
    main()
