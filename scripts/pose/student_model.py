"""Untrained, project-owned 96px pose student; host research only.

Torch uses NCHW. Both heads are linear: 17 heatmap logits and 34 offsets,
interleaved [y0,x0,y1,x1,...] in 24x24 output-grid units. Coordinates and losses
belong to the training/data pipeline. This module does not fetch data or devices.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

ARCHITECTURE = "ricestep-tiny-pose-v1"
INPUT_SIZE = 96
GRID_SIZE = 24
BN_EPSILON = 1e-3
OFFSET_LAYOUT = "interleaved_yx_grid_units"


def same_padding(length: int, kernel: int, stride: int) -> tuple[int, int]:
    """TensorFlow SAME, including asymmetric stride-2 padding on even inputs."""
    total = max((math.ceil(length / stride) - 1) * stride + kernel - length, 0)
    return total // 2, total - total // 2


class ConvBNAct(nn.Module):
    def __init__(self, ci: int, co: int, kernel: int = 1, stride: int = 1,
                 groups: int = 1):
        super().__init__()
        self.kernel, self.stride = kernel, stride
        self.conv = nn.Conv2d(ci, co, kernel, stride, padding=0,
                              groups=groups, bias=False)
        self.bn = nn.BatchNorm2d(co, eps=BN_EPSILON, momentum=0.1)
        self.relu = nn.ReLU6(inplace=False)

    def forward(self, x):
        top, bottom = same_padding(x.shape[-2], self.kernel, self.stride)
        left, right = same_padding(x.shape[-1], self.kernel, self.stride)
        x = F.pad(x, (left, right, top, bottom))
        return self.relu(self.bn(self.conv(x)))


class DepthwiseSeparable(nn.Module):
    def __init__(self, ci: int, co: int, stride: int = 1):
        super().__init__()
        self.dw = ConvBNAct(ci, ci, 3, stride, groups=ci)
        self.pw = ConvBNAct(ci, co)

    def forward(self, x):
        return self.pw(self.dw(x))


class TinyPose(nn.Module):
    def __init__(self):
        super().__init__()
        self.stem = ConvBNAct(3, 16, 3, 2)
        self.enc24a = DepthwiseSeparable(16, 24, 2)
        self.enc24b = DepthwiseSeparable(24, 24)
        self.enc48a = DepthwiseSeparable(24, 48, 2)
        self.enc48b = DepthwiseSeparable(48, 48)
        self.enc96a = DepthwiseSeparable(48, 96, 2)
        self.enc96b = DepthwiseSeparable(96, 96)
        self.enc96c = DepthwiseSeparable(96, 96)
        self.neck48 = ConvBNAct(96, 48)
        self.up12 = nn.Upsample(scale_factor=2, mode="bilinear", align_corners=False)
        self.dec48 = DepthwiseSeparable(48, 48)
        self.neck24 = ConvBNAct(48, 24)
        self.up24 = nn.Upsample(scale_factor=2, mode="bilinear", align_corners=False)
        self.dec24 = DepthwiseSeparable(24, 24)
        self.heatmap = nn.Conv2d(24, 17, 1, bias=True)
        self.offset = nn.Conv2d(24, 34, 1, bias=True)

    def forward(self, x):
        if x.ndim != 4 or tuple(x.shape[1:]) != (3, INPUT_SIZE, INPUT_SIZE):
            raise ValueError("TinyPose input must be NCHW [N,3,96,96], RGB in [-1,1]")
        x = self.stem(x)
        skip24 = self.enc24b(self.enc24a(x))
        skip12 = self.enc48b(self.enc48a(skip24))
        x = self.enc96c(self.enc96b(self.enc96a(skip12)))
        x = self.dec48(self.up12(self.neck48(x)) + skip12)
        x = self.dec24(self.up24(self.neck24(x)) + skip24)
        return self.heatmap(x), self.offset(x)


def profile_model(model: TinyPose) -> dict:
    """Measure nominal convolution MAC and tensor sizes with forward hooks.

    Activation records are single tensor sizes, NOT a TFLM arena/liveness claim.
    MAC exclude BN, bias, ReLU6, residual additions and bilinear interpolation.
    """
    rows, activations, hooks = [], [], []
    original_training = {module: module.training for module in model.modules()}
    def hook(name):
        def record(module, inputs, output):
            elements = output.numel()
            activations.append(dict(name=name + ":input", shape=list(inputs[0].shape), elements=inputs[0].numel()))
            activations.append(dict(name=name + ":output", shape=list(output.shape), elements=elements))
            if isinstance(module, nn.Conv2d):
                mac = elements * (module.in_channels // module.groups) * math.prod(module.kernel_size)
                rows.append(dict(name=name, input=list(inputs[0].shape), output=list(output.shape),
                    kernel=list(module.kernel_size), groups=module.groups, nominal_mac=mac))
        return record
    try:
        for name, module in model.named_modules():
            if isinstance(module, (nn.Conv2d, nn.Upsample)):
                hooks.append(module.register_forward_hook(hook(name)))
        parameter = next(model.parameters())
        model.eval()
        with torch.no_grad():
            model(torch.zeros(1, 3, INPUT_SIZE, INPUT_SIZE,
                              device=parameter.device, dtype=parameter.dtype))
    finally:
        for handle in hooks:
            handle.remove()
        for module, training in original_training.items():
            module.training = training
    largest = max(row["elements"] for row in activations)
    return dict(architecture=ARCHITECTURE, input_nchw=[1, 3, 96, 96],
        logits_nchw=[1, 17, 24, 24], offsets_nchw=[1, 34, 24, 24],
        offset_layout=OFFSET_LAYOUT, nominal_convolution_mac=sum(r["nominal_mac"] for r in rows),
        trainable_parameters=sum(p.numel() for p in model.parameters() if p.requires_grad),
        all_parameters=sum(p.numel() for p in model.parameters()),
        largest_single_activation_elements=largest,
        largest_single_activation_fp32_bytes=largest * 4,
        largest_single_activation_int8_bytes_if_quantized=largest,
        retained_skip_elements=24*24*24 + 12*12*48,
        activation_budget_limitation="Not TFLM arena or peak live memory; excludes allocator/scratch/input/output coexistence.",
        convolution_layers=rows, activation_tensors=activations)


def export_numpy_weights(model: TinyPose, path: str | Path, *, metadata: dict | None = None) -> dict:
    """Export state_dict to a pickle-free interchange file for student_export.py.

    Caller metadata must record training provenance. Default is explicitly
    untrained; passing weights through this function does not certify accuracy.
    BN running_mean/running_var and num_batches_tracked are included.
    """
    info = dict(format="ricestep-tiny-pose-weights-v1", architecture=ARCHITECTURE,
        input_layout="NCHW", input_shape=[1, 3, 96, 96], input_range=[-1, 1],
        offset_layout=OFFSET_LAYOUT, bn_epsilon=BN_EPSILON,
        provenance=metadata if metadata is not None else {"training_status": "untrained"})
    arrays = {name: tensor.detach().cpu().numpy() for name, tensor in model.state_dict().items()}
    if any(not np.isfinite(value).all() for value in arrays.values()):
        raise ValueError("Cannot export non-finite model state")
    arrays["__metadata__"] = np.frombuffer(json.dumps(info, sort_keys=True).encode(), dtype=np.uint8)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, **arrays)
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, help="Write architecture budget JSON; no trained model is produced")
    args = parser.parse_args()
    budget = profile_model(TinyPose())
    text = json.dumps(budget, indent=2)
    if args.profile:
        args.profile.parent.mkdir(parents=True, exist_ok=True)
        args.profile.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
