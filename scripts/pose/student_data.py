"""Declared single-person training crops; no camera, synthetic labels or uploads.

Training uses ground-truth person boxes. This is not a deployed person detector:
evaluation on full camera frames must be reported separately from crop metrics.
COCO coordinates are mapped into continuous output-grid units (0..24), with
offset channels ordered y0,x0,y1,x1,... . Unknown/out-of-crop labels are masked.
"""

import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance

FLIP = np.array([0, 2, 1, 4, 3, 6, 5, 8, 7, 10, 9, 12, 11, 14, 13, 16, 15])
INPUT = 96
GRID = 24


def load_manifest(path):
    path = Path(path).resolve()
    document = json.loads(path.read_text(encoding="utf-8"))
    seen = set()
    training_hashes = set()
    result = {}
    for split in ("train", "val"):
        rows = document[split]
        if not rows:
            raise ValueError(f"Empty {split} split")
        checked = []
        for row in rows:
            image_id = int(row["image_id"])
            if image_id in seen:
                raise ValueError("Image repeated within/across training and validation")
            seen.add(image_id)
            if row["split"] != split or not row["image_downloaded"] or row["iscrowd"]:
                raise ValueError("Only downloaded, noncrowd, split-matched targets are accepted")
            local = (path.parent / row["image_path"]).resolve()
            if not local.is_relative_to(path.parent):
                raise ValueError("Image path escapes dataset directory")
            raw = local.read_bytes()
            if len(raw) != row["image_bytes"] or hashlib.sha256(raw).hexdigest() != row["image_sha256"]:
                raise ValueError("Dataset image size/hash mismatch")
            if split == "train":
                training_hashes.add(row["image_sha256"])
            elif row["image_sha256"] in training_hashes:
                raise ValueError("Identical image bytes occur in training and validation")
            box = np.asarray(row["bbox_xywh"], dtype=np.float64)
            points = np.asarray(row["keypoints_xyv"], dtype=np.float64)
            if box.shape != (4,) or not np.isfinite(box).all() or np.any(box[2:] <= 0):
                raise ValueError("Invalid target box")
            if points.shape != (51,) or not np.isfinite(points).all():
                raise ValueError("Invalid COCO17 annotations")
            if not np.isin(points.reshape(17, 3)[:, 2], [0, 1, 2]).all():
                raise ValueError("Invalid COCO visibility values")
            checked.append({**row, "local_path": local})
        result[split] = checked
    # Background candidates are deliberately not consumed: absence of keypoint
    # records has not yet been independently established as a negative label.
    return result


def targets(points_grid, valid):
    """Gaussian location supervision and local y/x residuals, not confidence truth."""
    yy, xx = np.mgrid[:GRID, :GRID].astype(np.float32)
    dx = points_grid[:, 0, None, None] - xx
    dy = points_grid[:, 1, None, None] - yy
    distance2 = dx * dx + dy * dy
    heat = np.exp(-distance2 / 2.0) * valid[:, None, None]
    weights = (distance2 <= 2.25).astype(np.float32) * valid[:, None, None]
    offsets = np.stack((dy, dx), axis=1).reshape(34, GRID, GRID)
    return heat.astype(np.float32), offsets, np.repeat(weights, 2, axis=0)


def sample(row, *, augment=False, seed=0):
    rng = np.random.default_rng(seed)
    with Image.open(row["local_path"]) as original:
        if original.size != (row["width"], row["height"]):
            raise ValueError("Image dimensions differ from manifest")
        image = original.convert("RGB")
    x, y, w, h = map(float, row["bbox_xywh"])
    side = max(w, h) * (float(rng.uniform(1.25, 2.0)) if augment else 1.5)
    cx, cy = x + w / 2, y + h / 2
    if augment:
        cx += float(rng.uniform(-0.08, 0.08)) * side
        cy += float(rng.uniform(-0.08, 0.08)) * side
    x0, y0 = cx - side / 2, cy - side / 2
    # PIL's inverse affine samples the square ROI; outside-image pixels are
    # explicitly black. Original source images/annotations remain untouched.
    image = image.transform((INPUT, INPUT), Image.Transform.AFFINE,
                            (side / INPUT, 0, x0, 0, side / INPUT, y0),
                            Image.Resampling.BILINEAR, fillcolor=(0, 0, 0))
    points = np.asarray(row["keypoints_xyv"], dtype=np.float32).reshape(17, 3).copy()
    points[:, 0] = (points[:, 0] - x0) * GRID / side
    points[:, 1] = (points[:, 1] - y0) * GRID / side
    flipped = augment and rng.random() < 0.5
    if flipped:
        image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        points[:, 0] = GRID - points[:, 0]
        points = points[FLIP]
    if augment:
        image = ImageEnhance.Brightness(image).enhance(float(rng.uniform(0.85, 1.15)))
    valid = ((points[:, 2] > 0) & (points[:, 0] >= 0) & (points[:, 0] < GRID) &
             (points[:, 1] >= 0) & (points[:, 1] < GRID)).astype(np.float32)
    heat, offsets, offset_mask = targets(points[:, :2], valid)
    pixels = np.asarray(image, dtype=np.float32).transpose(2, 0, 1) / 127.5 - 1.0
    return {"input": pixels, "heatmaps": heat, "offsets": offsets,
            "offset_mask": offset_mask, "valid": valid,
            "points": points[:, :2], "box_diagonal_grid": np.float32(np.hypot(w, h) * GRID / side),
            "image_id": int(row["image_id"]), "flipped": bool(flipped)}


def decode(logits, offsets):
    """Host diagnostic decoder: peaks are uncalibrated, never reward confidence."""
    logits, offsets = np.asarray(logits), np.asarray(offsets)
    if logits.shape[-3:] != (17, GRID, GRID) or offsets.shape[-3:] != (34, GRID, GRID):
        raise ValueError("Student head shape mismatch")
    if not np.isfinite(logits).all() or not np.isfinite(offsets).all():
        raise ValueError("Nonfinite student outputs")
    flat = logits.reshape(-1, 17, GRID * GRID)
    residual = offsets.reshape(-1, 17, 2, GRID, GRID)
    peak = np.argmax(flat, axis=2)
    yy, xx = peak // GRID, peak % GRID
    b = np.arange(len(flat))[:, None]
    k = np.arange(17)[None, :]
    y = yy + residual[b, k, 0, yy, xx]
    x = xx + residual[b, k, 1, yy, xx]
    score = 1.0 / (1.0 + np.exp(-np.clip(flat[b, k, peak], -80, 80)))
    return np.stack((x, y, score), axis=-1)
