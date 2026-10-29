# MoveNet CPU 参考兼容性探针

此脚本不访问摄像头或开发板，只检查真实模型并在 **x86 Linux TFLite Micro** 上运行常量输入。它不证明 Mini3 速度、人体识别精度或完整运动计数。研究结论见 [板端可行性记录](../../docs/board-pose-feasibility.md)。

## 固定输入与环境

- [Google MoveNet Lightning int8 v4](https://tfhub.dev/google/lite-model/movenet/singlepose/lightning/tflite/int8/4?lite-format=tflite)
- 模型 SHA-256：`cd7cc22fa946e5d146a7b98d496853e1923e22828d3972d579973f27f91bb105`
- 实测环境：Ubuntu 24.04 / Python 3.12.3，`tflite-micro==0.dev20261026171606`、numpy 2.2.6、flatbuffers 25.12.19、PyYAML 6.0.3。
- TFLM cp312 manylinux_2_28_x86_64 wheel SHA-256：`4dd4fbcd3329221a4da23b349678763c778d4a079a81eff387aea59bff3f1466`。

只在隔离环境安装依赖；不把模型、wheel、venv、输出文件或训练数据提交到 Git。Linux 示例（从仓库根目录运行）：

```bash
python3 -m venv .cache/pose-venv
.cache/pose-venv/bin/python -m pip install \
  tflite-micro==0.dev20261026171606 numpy==2.2.6 flatbuffers==25.12.19 pyyaml==6.0.3
mkdir -p .cache/research-pose
curl --fail --location \
  'https://tfhub.dev/google/lite-model/movenet/singlepose/lightning/tflite/int8/4?lite-format=tflite' \
  --output .cache/research-pose/movenet-lightning-int8-v4.download
```

当前工作区已将上述 Linux wheels 解包至 `.cache/research-pose/tflm-host`，因此也可以在 WSL 使用系统 `python3` 加 `--python-path .cache/research-pose/tflm-host`，无需全局安装。

## 运行

```bash
.cache/pose-venv/bin/python scripts/pose/probe_movenet.py \
  --model .cache/research-pose/movenet-lightning-int8-v4.download --mode inspect

.cache/pose-venv/bin/python scripts/pose/probe_movenet.py \
  --model .cache/research-pose/movenet-lightning-int8-v4.download \
  --mode heads --arena-bytes 2097152 \
  > .cache/research-pose/heads-report.log 2>&1
```

模式及本次结果：

| 模式 | 行为 | 结果 |
| --- | --- | --- |
| `inspect` | 只读取原始模型 | 157 算子，UINT8 输入，FLOAT32 的 17 点输出 |
| `full` | 原样执行 | 第 0 个 CAST 不支持 UINT8 输入，退出码 1 |
| `int8-input` | 外移输入 CAST/QUANTIZE，用 `uint8 - 128` 喂同等 INT8 tensor | 后续 INT32 FLOOR_DIV 不支持，退出码 1 |
| `heads` | 保留量化 CNN 及原始四头，外移输入转换 | Invoke 成功，退出码 0；**没有完成 17 点解码** |

`heads` 输出 tensor 259、276、282、285，shape 分别为 `[1,48,48,1/17/34/34]`。源模型未被覆盖；派生模型和 JSON 报告默认写入 `.cache/pose-probe/`。模型 hash 不匹配时拒绝套用固定 tensor 索引。

**2 MiB（2,097,152 B）是传入的容量上限；本次 heads 的 allocator 实际账目是 1,945,792 B**，其中 head 1,777,568 B，tail 168,224 B。实际占用由 TFLM `print_allocations()` 写入 stderr，需要和 JSON 一起保留。模型文件约 2.76 MiB 不包括在 arena 数字里，Mini3 的 Flash XIP、运行栈和其他任务要单独预算。

该模型卷积量为 270,549,504 MAC/帧。成功执行于桌面 CPU 不代表 300 MHz 芯片能实时执行，尤其在候选 LS2663 无 NPU 配置下。后续需要 CPU 解码与原图精度验证，再移植板端计时；不要把此探针输出接成真实运动奖励。
