"""Host-only pose experiment with a transferred MobileNetV1 .25 backbone.

The constructor is deliberately random until load_pretrained_backbone is called.
ImageNet initialization supplies visual features, not trained pose predictions.
No downloads, torchvision, firmware integration, or device access occur here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import torch
from torch import nn

from student_model import (BN_EPSILON, OFFSET_LAYOUT, ConvBNAct,
                           DepthwiseSeparable, profile_model as tiny_profile)

ARCHITECTURE = "ricestep-mobilenet-v1-025-pose-v1"
BACKBONE_FORMAT = "ricestep-mobilenet-v1-025-backbone-v1"
WEIGHTS_FORMAT = "ricestep-mobilenet-pose-weights-v1"
SOURCE_URL = "https://storage.googleapis.com/tensorflow/keras-applications/mobilenet/mobilenet_2_5_128_tf_no_top.h5"
SOURCE_SHA256 = "7d8b7f3c9555cb4579f9659ee233a540e66fd7ad514c0a8b14493b667447c5d6"
SOURCE_BYTES = 2108140
INPUT_SIZE, GRID_SIZE = 96, 24


class MobileNetBackbone(nn.Module):
    """Keras MobileNet(alpha=.25) through conv_pw_11_relu at fixed 96px.

    All stride-2 input sizes are even, so ConvBNAct's TensorFlow SAME agrees
    with Keras V1's explicit bottom/right-only padding before those DW layers.
    Keras BN momentum=.99 is PyTorch momentum=.01 (opposite conventions).
    """
    def __init__(self):
        super().__init__()
        self.stem = ConvBNAct(3, 8, 3, 2)
        channels = [(8,16,1), (16,32,2), (32,32,1), (32,64,2),
                    (64,64,1), (64,128,2)] + [(128,128,1)] * 5
        self.blocks = nn.ModuleList(DepthwiseSeparable(ci, co, stride)
                                    for ci, co, stride in channels)
        for module in self.modules():
            if isinstance(module, nn.BatchNorm2d):
                module.momentum = .01

    def forward(self, x):
        if x.ndim != 4 or tuple(x.shape[1:]) != (3, INPUT_SIZE, INPUT_SIZE):
            raise ValueError("MobileNet backbone input must be NCHW [N,3,96,96], RGB in [-1,1]")
        x = self.stem(x)
        skips = []
        for index, block in enumerate(self.blocks, 1):
            x = block(x)
            if index in (3, 5):
                skips.append(x)
        return skips[0], skips[1], x


class MobileNetPose(nn.Module):
    def __init__(self):
        super().__init__()
        self.backbone = MobileNetBackbone()
        self.neck64 = ConvBNAct(128, 64)
        self.up12 = nn.Upsample(scale_factor=2, mode="bilinear", align_corners=False)
        self.dec64 = DepthwiseSeparable(64, 64)
        self.neck32 = ConvBNAct(64, 32)
        self.up24 = nn.Upsample(scale_factor=2, mode="bilinear", align_corners=False)
        self.dec32 = DepthwiseSeparable(32, 32)
        self.heatmap = nn.Conv2d(32, 17, 1, bias=True)
        self.offset = nn.Conv2d(32, 34, 1, bias=True)

    def forward(self, x):
        skip24, skip12, x = self.backbone(x)
        x = self.dec64(self.up12(self.neck64(x)) + skip12)
        x = self.dec32(self.up24(self.neck32(x)) + skip24)
        return self.heatmap(x), self.offset(x)


def load_pretrained_backbone(model: MobileNetPose, path: str | Path) -> dict:
    """Validate the explicit TF-exported interchange file before any mutation.

    Source digest identifies the pinned H5; per-array digests detect interchange
    corruption. These are integrity checks, not a signature of an untrusted NPZ.
    Pose head weights are deliberately untouched. Loading never fetches weights.
    """
    path = Path(path)
    expected = model.backbone.state_dict()
    with np.load(path, allow_pickle=False) as archive:
        if set(archive.files) != set(expected) | {"__metadata__"}:
            raise ValueError("Backbone NPZ names differ from the exact V1 .25 stride16 profile")
        raw = archive["__metadata__"]
        if raw.dtype != np.uint8 or raw.ndim != 1 or raw.size > 131072:
            raise ValueError("Invalid backbone metadata encoding")
        metadata = json.loads(raw.tobytes().decode("utf-8"))
        required = dict(format=BACKBONE_FORMAT, architecture="mobilenet_v1_025_stride16",
                        input_shape=[1,3,96,96], input_range=[-1,1],
                        bn_epsilon=BN_EPSILON, keras_bn_momentum=.99,
                        source_url=SOURCE_URL, source_sha256=SOURCE_SHA256,
                        source_bytes=SOURCE_BYTES, source_training="ImageNet image classification; not pose",
                        source_input_resolution=128)
        if any(metadata.get(k) != v for k, v in required.items()):
            raise ValueError("Backbone provenance or numerical contract does not match")
        inventory = metadata.get("arrays", {})
        if set(inventory) != set(expected):
            raise ValueError("Backbone array inventory differs from expected names")
        state = {}
        for name, target in expected.items():
            value = archive[name]
            dtype = np.dtype("int64") if name.endswith("num_batches_tracked") else np.dtype("float32")
            record = dict(shape=list(value.shape), dtype=str(value.dtype),
                          sha256=hashlib.sha256(value.tobytes()).hexdigest())
            if (value.shape != tuple(target.shape) or value.dtype != dtype or
                    not np.isfinite(value).all() or inventory[name] != record):
                raise ValueError(f"Invalid backbone tensor {name}")
            if name.endswith("running_var") and np.any(value <= 0):
                raise ValueError(f"Nonpositive BN variance: {name}")
            if name.endswith("num_batches_tracked") and int(value) != 0:
                raise ValueError(f"Unexpected imported batch counter: {name}")
            state[name] = torch.from_numpy(value.copy())
    model.backbone.load_state_dict(state, strict=True)
    return {**metadata, "interchange_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def profile_model(model: MobileNetPose) -> dict:
    budget = tiny_profile(model)
    budget.update(architecture=ARCHITECTURE,
                  retained_skip_elements=24*24*32 + 12*12*64,
                  backbone_nominal_mac=sum(row["nominal_mac"] for row in budget["convolution_layers"]
                                           if row["name"].startswith("backbone.")),
                  backbone_parameters=sum(p.numel() for p in model.backbone.parameters()))
    return budget


def export_numpy_weights(model: MobileNetPose, path: str | Path, *, metadata: dict | None = None) -> dict:
    """Export the whole research model with its own format, never TinyPose's."""
    info = dict(format=WEIGHTS_FORMAT, architecture=ARCHITECTURE,
                input_layout="NCHW", input_shape=[1,3,96,96], input_range=[-1,1],
                offset_layout=OFFSET_LAYOUT, bn_epsilon=BN_EPSILON,
                provenance=metadata if metadata is not None else {"training_status":"untrained"})
    arrays = {name: value.detach().cpu().numpy() for name, value in model.state_dict().items()}
    if any(not np.isfinite(value).all() for value in arrays.values()):
        raise ValueError("Cannot export non-finite model state")
    arrays["__metadata__"] = np.frombuffer(json.dumps(info, sort_keys=True).encode(), dtype=np.uint8)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, **arrays)
    return info


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path)
    args = parser.parse_args()
    torch.set_num_threads(2)
    text = json.dumps(profile_model(MobileNetPose()), indent=2)
    if args.profile:
        args.profile.parent.mkdir(parents=True, exist_ok=True)
        args.profile.write_text(text + "\n", encoding="utf-8")
    print(text)
