"""Export one explicitly supplied official MobileNetV1 .25/128 H5 backbone.

Runs in TensorFlow 2.15, without torch, downloads, or device access. The pinned
128px ImageNet weights are explicitly loaded at 96px; Keras's implicit 224px
fallback is never used. The resulting initialization has no pose-trained head.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import tensorflow as tf

SOURCE_URL = "https://storage.googleapis.com/tensorflow/keras-applications/mobilenet/mobilenet_2_5_128_tf_no_top.h5"
SOURCE_SHA256 = "7d8b7f3c9555cb4579f9659ee233a540e66fd7ad514c0a8b14493b667447c5d6"
SOURCE_BYTES = 2108140
BACKBONE_FORMAT = "ricestep-mobilenet-v1-025-backbone-v1"
TAPS = ("conv_pw_3_relu", "conv_pw_5_relu", "conv_pw_11_relu")


def build_reference(weights: str | Path):
    path = Path(weights)
    if not path.is_file() or path.stat().st_size != SOURCE_BYTES:
        raise ValueError("An explicit pinned local MobileNet .25/128 no-top H5 is required")
    if hashlib.sha256(path.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError("Official MobileNet H5 SHA256 mismatch")
    if tf.__version__ != "2.15.1":
        raise ValueError("Transfer reference is pinned to TensorFlow 2.15.1")
    # Local path, not 'imagenet': no hidden cache download or 96->224 selection.
    model = tf.keras.applications.MobileNet(input_shape=(96,96,3), alpha=.25,
                    depth_multiplier=1, include_top=False, weights=str(path))
    return model, tf.keras.Model(model.input, [model.get_layer(name).output for name in TAPS])


def export_backbone(weights: str | Path, output: str | Path):
    model, reference = build_reference(weights)
    arrays = {}
    mapping = [("stem", "conv1", "conv1_bn", "conv1_relu", 3, 8, 3, 2, False)]
    channels = [(8,16,1), (16,32,2), (32,32,1), (32,64,2),
                (64,64,1), (64,128,2)] + [(128,128,1)] * 5
    for index, (ci,co,stride) in enumerate(channels, 1):
        for suffix, source, ic, oc, kernel, step, depthwise in [
                ("dw",f"conv_dw_{index}",ci,ci,3,stride,True),
                ("pw",f"conv_pw_{index}",ci,co,1,1,False)]:
            mapping.append((f"blocks.{index-1}.{suffix}",source,source+"_bn",
                            source+"_relu",ic,oc,kernel,step,depthwise))
    for target, conv_name, bn_name, relu_name, ci, co, kernel, stride, depthwise in mapping:
        conv, bn, relu = (model.get_layer(name) for name in (conv_name,bn_name,relu_name))
        expected_cls = tf.keras.layers.DepthwiseConv2D if depthwise else tf.keras.layers.Conv2D
        if (not isinstance(conv, expected_cls) or conv.use_bias or
                conv.kernel_size != (kernel,kernel) or conv.strides != (stride,stride) or
                tuple(conv.dilation_rate) != (1,1) or
                not isinstance(bn,tf.keras.layers.BatchNormalization) or
                bn.epsilon != 1e-3 or bn.momentum != .99 or not bn.center or not bn.scale or
                not isinstance(relu,tf.keras.layers.ReLU) or relu.max_value != 6):
            raise ValueError(f"Unexpected layer configuration: {conv_name}")
        if depthwise and (conv.depth_multiplier != 1 or
                          conv.padding != ("valid" if stride == 2 else "same")):
            raise ValueError(f"Unexpected depthwise configuration: {conv_name}")
        if depthwise and stride == 2:
            pad = model.get_layer("conv_pad_" + conv_name.rsplit("_",1)[1])
            if pad.padding != ((0,1),(0,1)):
                raise ValueError("Unexpected Keras V1 stride-2 padding")
        if not depthwise and (conv.groups != 1 or conv.padding != "same"):
            raise ValueError(f"Unexpected Conv2D configuration: {conv_name}")
        source = conv.get_weights()
        shape = (kernel,kernel,ci,1 if depthwise else co)
        if len(source) != 1 or source[0].shape != shape:
            raise ValueError(f"Unexpected kernel shape: {conv_name}")
        value = source[0].transpose(2,3,0,1) if depthwise else source[0].transpose(3,2,0,1)
        arrays[target+".conv.weight"] = np.ascontiguousarray(value)
        stats = bn.get_weights()
        if len(stats) != 4 or any(value.shape != (co,) for value in stats) or np.any(stats[3] <= 0):
            raise ValueError(f"Unexpected BN state: {bn_name}")
        for suffix, value in zip(("weight","bias","running_mean","running_var"), stats):
            arrays[target+".bn."+suffix] = value.copy()
        arrays[target+".bn.num_batches_tracked"] = np.array(0,dtype=np.int64)
    if any(not np.isfinite(value).all() for value in arrays.values()):
        raise ValueError("Non-finite pretrained backbone")
    if any(value.dtype != np.float32 for name,value in arrays.items() if not name.endswith("num_batches_tracked")):
        raise ValueError("Expected float32 pretrained arrays")
    metadata = dict(format=BACKBONE_FORMAT, architecture="mobilenet_v1_025_stride16",
        input_shape=[1,3,96,96], input_range=[-1,1], bn_epsilon=1e-3,
        keras_bn_momentum=.99, source_url=SOURCE_URL, source_sha256=SOURCE_SHA256,
        source_bytes=SOURCE_BYTES, source_training="ImageNet image classification; not pose",
        source_input_resolution=128, tensorflow_version=tf.__version__,
        license="Apache-2.0",
        license_evidence={
            "original_model_family":"https://www.kaggle.com/models/google/mobilenet-v1",
            "keras_implementation":"https://github.com/keras-team/keras/blob/v2.15.0/LICENSE",
            "keras_weight_provenance":"https://github.com/keras-team/keras/blob/v2.15.0/keras/applications/mobilenet.py",
            "note":"Google MobileNet model family is Apache-2.0; Keras identifies these as translated TensorFlow checkpoints. H5 has no separate embedded license."},
        taps=list(TAPS), arrays={name:dict(shape=list(value.shape),dtype=str(value.dtype),
                sha256=hashlib.sha256(value.tobytes()).hexdigest()) for name,value in arrays.items()})
    payload = {**arrays, "__metadata__":np.frombuffer(json.dumps(metadata,sort_keys=True).encode(),dtype=np.uint8)}
    output = Path(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    np.savez(output, **payload)
    return metadata, reference


def export_parity(reference, image_archive: Path, output: Path):
    """Real caller-supplied RGB crops plus non-calibration boundary fixtures."""
    with np.load(image_archive,allow_pickle=False) as data:
        real = data["images"]
        if (real.dtype != np.float32 or real.ndim != 4 or real.shape[1:] != (96,96,3) or
                not 1 <= len(real) <= 2048 or not np.isfinite(real).all() or
                np.min(real) < -1 or np.max(real) > 1):
            raise ValueError("Parity images must be explicit float32 NHWC real crops in [-1,1]")
        real = real[:16].copy()
    boundary = np.zeros((4,96,96,3),dtype=np.float32)
    boundary[0].fill(-1); boundary[1].fill(1)
    yy,xx = np.indices((96,96))
    boundary[2,:,:,0]=(xx%2)*2-1
    boundary[2,:,:,1]=(yy%2)*2-1
    boundary[2,:,:,2]=xx.astype(np.float32)/95*2-1
    boundary[3,0,0]=[1,-1,1]; boundary[3,-1,-1]=[-1,1,-1]
    images = np.concatenate([real,boundary])
    outputs = [value.numpy() for value in reference(images,training=False)]
    info = dict(real_images=len(real),boundary_fixtures=4,
                input_archive_sha256=hashlib.sha256(image_archive.read_bytes()).hexdigest(),
                purpose="feature-transfer parity; not accuracy or quantization calibration")
    output.parent.mkdir(parents=True,exist_ok=True)
    np.savez(output,images=images,stride4=outputs[0],stride8=outputs[1],stride16=outputs[2],
             __metadata__=np.frombuffer(json.dumps(info).encode(),dtype=np.uint8))
    return info


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--parity-images",type=Path)
    parser.add_argument("--parity-output",type=Path)
    args = parser.parse_args()
    if bool(args.parity_images) != bool(args.parity_output):
        parser.error("--parity-images and --parity-output must be supplied together")
    metadata, reference = export_backbone(args.weights,args.output)
    result = {key:value for key,value in metadata.items() if key != "arrays"}
    result["array_count"] = len(metadata["arrays"])
    if args.parity_images:
        result["parity"] = export_parity(reference,args.parity_images,args.parity_output)
    print(json.dumps(result,indent=2))
