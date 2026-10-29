"""MobileNet transfer/profile tests; optional explicit cached official parity.

Default tests use synthetic states for format validation and do not download.
--backbone and --parity additionally validate real Keras feature transfer.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import torch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"/"pose"))
from mobilenet_student import (ARCHITECTURE, BACKBONE_FORMAT, SOURCE_BYTES,
    SOURCE_SHA256, SOURCE_URL, WEIGHTS_FORMAT, MobileNetPose,
    export_numpy_weights, load_pretrained_backbone, profile_model)


def synthetic_archive(model):
    arrays = {name:value.detach().cpu().numpy().copy()
              for name,value in model.backbone.state_dict().items()}
    info = dict(format=BACKBONE_FORMAT,architecture="mobilenet_v1_025_stride16",
        input_shape=[1,3,96,96],input_range=[-1,1],bn_epsilon=.001,
        keras_bn_momentum=.99,source_url=SOURCE_URL,source_sha256=SOURCE_SHA256,
        source_bytes=SOURCE_BYTES,source_training="ImageNet image classification; not pose",
        source_input_resolution=128,purpose="synthetic loader test, not official pretrained state")
    return arrays, info


def save_archive(path, arrays, metadata, refresh_inventory=True):
    if refresh_inventory:
        metadata = {**metadata,"arrays":{name:dict(shape=list(value.shape),dtype=str(value.dtype),
            sha256=hashlib.sha256(value.tobytes()).hexdigest()) for name,value in arrays.items()}}
    np.savez(path,**arrays,__metadata__=np.frombuffer(json.dumps(metadata).encode(),dtype=np.uint8))


class MobileNetTests(unittest.TestCase):
    def test_budget_shapes_and_training_state(self):
        model = MobileNetPose().train()
        model.backbone.blocks[0].eval()
        modes = {name:module.training for name,module in model.named_modules()}
        before = {name:value.clone() for name,value in model.state_dict().items()}
        budget = profile_model(model)
        self.assertEqual(budget["architecture"],ARCHITECTURE)
        self.assertEqual(budget["nominal_convolution_mac"],9531648)
        self.assertEqual(budget["backbone_nominal_mac"],6573312)
        self.assertEqual(budget["all_parameters"],128003)
        self.assertEqual(budget["backbone_parameters"],109520)
        self.assertEqual(budget["retained_skip_elements"],27648)
        self.assertEqual(budget["largest_single_activation_elements"],38416)
        self.assertEqual({name:module.training for name,module in model.named_modules()},modes)
        for name,value in before.items():
            self.assertTrue(torch.equal(value,model.state_dict()[name]))
        model.eval()
        with torch.no_grad():
            logits,offsets=model(torch.zeros(2,3,96,96))
        self.assertEqual(tuple(logits.shape),(2,17,24,24))
        self.assertEqual(tuple(offsets.shape),(2,34,24,24))
        self.assertTrue(all(module.momentum==.01 and module.eps==.001
            for module in model.backbone.modules() if isinstance(module,torch.nn.BatchNorm2d)))
        with self.assertRaises(ValueError): model(torch.zeros(1,3,128,128))
        with self.assertRaises(ValueError): model(torch.zeros(1,96,96,3))

    def test_backbone_loader_is_exact_and_does_not_touch_heads(self):
        torch.manual_seed(772)
        source,target=MobileNetPose(),MobileNetPose()
        arrays,meta=synthetic_archive(source)
        before={name:value.clone() for name,value in target.state_dict().items()
                if not name.startswith("backbone.")}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"backbone.npz"
            save_archive(path,arrays,meta)
            provenance=load_pretrained_backbone(target,path)
            self.assertEqual(provenance["source_sha256"],SOURCE_SHA256)
            self.assertEqual(provenance["interchange_sha256"],hashlib.sha256(path.read_bytes()).hexdigest())
        for name,value in source.backbone.state_dict().items():
            self.assertTrue(torch.equal(value,target.backbone.state_dict()[name]))
        for name,value in before.items():self.assertTrue(torch.equal(value,target.state_dict()[name]))

    def test_malformed_transfer_rejected_before_mutating_any_parameter(self):
        model=MobileNetPose()
        original={name:value.clone() for name,value in model.state_dict().items()}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"broken.npz"
            for kind in ("source_hash","resolution","epsilon","format","missing","extra",
                         "shape","dtype","nan","variance","batch_counter","digest"):
                with self.subTest(kind=kind):
                    arrays,meta=synthetic_archive(model)
                    if kind=="source_hash":meta["source_sha256"]="0"*64
                    elif kind=="resolution":meta["source_input_resolution"]=224
                    elif kind=="epsilon":meta["bn_epsilon"]=1e-5
                    elif kind=="format":meta["format"]="ricestep-tiny-pose-weights-v1"
                    elif kind=="missing":del arrays["stem.conv.weight"]
                    elif kind=="extra":arrays["conv_pw_13"]=np.array(1,dtype=np.float32)
                    elif kind=="shape":arrays["stem.conv.weight"]=arrays["stem.conv.weight"][:1]
                    elif kind=="dtype":arrays["stem.conv.weight"]=arrays["stem.conv.weight"].astype(np.float64)
                    elif kind=="nan":arrays["stem.conv.weight"][0,0,0,0]=np.nan
                    elif kind=="variance":arrays["stem.bn.running_var"][0]=0
                    elif kind=="batch_counter":arrays["stem.bn.num_batches_tracked"]=np.array(1,dtype=np.int64)
                    if kind=="digest":
                        save_archive(path,arrays,meta)
                        with np.load(path,allow_pickle=False) as saved:meta=json.loads(saved["__metadata__"].tobytes())
                        arrays["stem.conv.weight"][0,0,0,0]+=1
                        save_archive(path,arrays,meta,False)
                    else:save_archive(path,arrays,meta)
                    with self.assertRaises(ValueError):load_pretrained_backbone(model,path)
                    for name,value in original.items():
                        self.assertTrue(torch.equal(value,model.state_dict()[name]))
            with self.assertRaises(FileNotFoundError):load_pretrained_backbone(model,Path(directory)/"missing.npz")

    def test_linear_heads_backpropagate_and_have_separate_export_format(self):
        torch.manual_seed(451)
        model=MobileNetPose().train()
        logits,offsets=model(torch.randn(2,3,96,96))
        (logits.square().mean()+offsets.square().mean()).backward()
        for param in (model.backbone.stem.conv.weight,model.heatmap.weight,model.offset.weight):
            self.assertTrue(torch.isfinite(param.grad).all())
            self.assertGreater(float(param.grad.abs().sum()),0)
        model.eval()
        with torch.no_grad():
            model.heatmap.weight.zero_();model.heatmap.bias.fill_(-3)
            model.offset.weight.zero_();model.offset.bias.copy_(torch.arange(34,dtype=torch.float32)-17)
            logits,offsets=model(torch.zeros(1,3,96,96))
        self.assertTrue(torch.all(logits==-3))
        self.assertTrue(torch.equal(offsets[0,:,0,0],torch.arange(34,dtype=torch.float32)-17))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"whole.npz"
            export_numpy_weights(model,path)
            with np.load(path,allow_pickle=False) as saved:
                meta=json.loads(saved["__metadata__"].tobytes())
                self.assertEqual(meta["format"],WEIGHTS_FORMAT)
                self.assertEqual(meta["architecture"],ARCHITECTURE)
            with self.assertRaises(ValueError):load_pretrained_backbone(model,path)
            with torch.no_grad():model.offset.weight[0,0,0,0]=float("nan")
            with self.assertRaises(ValueError):export_numpy_weights(model,path)


def check_real_transfer(backbone,parity,report_path=None,fixture_dir=None):
    torch.manual_seed(2718)
    model=MobileNetPose().eval()
    provenance=load_pretrained_backbone(model,backbone)
    with np.load(parity,allow_pickle=False) as data:
        images=data["images"]
        if images.dtype!=np.float32 or images.shape[1:]!=(96,96,3):
            raise ValueError("Bad explicit parity images")
        inputs=torch.from_numpy(images.transpose(0,3,1,2).copy())
        with torch.no_grad():outputs=model.backbone(inputs)
        rows=[]
        for name,value in zip(("stride4","stride8","stride16"),outputs):
            actual=value.numpy().transpose(0,2,3,1)
            expected=data[name]
            # Accumulation order differs across frameworks; compare FP32 features.
            np.testing.assert_allclose(actual,expected,rtol=2e-4,atol=8e-5)
            rows.append(dict(tap=name,shape=list(actual.shape),max_abs_error=float(np.max(np.abs(actual-expected)))))
        fixture_info=json.loads(data["__metadata__"].tobytes())
    report=dict(source_sha256=SOURCE_SHA256,interchange_sha256=provenance["interchange_sha256"],
        parity_sha256=hashlib.sha256(Path(parity).read_bytes()).hexdigest(),
        fixtures=fixture_info,features=rows,rtol=2e-4,atol=8e-5,
        limitation="CPU FP32 feature transfer only; no pose accuracy, quantization or board latency claim")
    if report_path:
        report_path.parent.mkdir(parents=True,exist_ok=True)
        report_path.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    if fixture_dir:
        fixture_dir.mkdir(parents=True,exist_ok=True)
        with torch.no_grad():logits,offsets=model(inputs)
        np.savez(fixture_dir/"parity.npz",images=images,
                 logits=logits.numpy().transpose(0,2,3,1),offsets=offsets.numpy().transpose(0,2,3,1))
        export_numpy_weights(model,fixture_dir/"weights.npz",metadata={
            "training_status":"ImageNet backbone plus untrained pose head",
            "purpose":"conversion parity fixture, not pose accuracy or calibration",
            "seed":2718,"backbone":provenance})
        (fixture_dir/"budget.json").write_text(json.dumps(profile_model(model),indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,indent=2))


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backbone",type=Path)
    parser.add_argument("--parity",type=Path)
    parser.add_argument("--report",type=Path)
    parser.add_argument("--write-fixture",type=Path)
    args,rest=parser.parse_known_args()
    if bool(args.backbone)!=bool(args.parity) or ((args.report or args.write_fixture) and not args.backbone):
        parser.error("Explicit --backbone and --parity are required together, including for fixture/report output")
    torch.set_num_threads(2)
    result=unittest.main(argv=[sys.argv[0],*rest],exit=False)
    if not result.result.wasSuccessful():sys.exit(1)
    if args.backbone:check_real_transfer(args.backbone,args.parity,args.report,args.write_fixture)
