# MoveNet CPU 参考兼容性探针

此目录不访问摄像头或开发板。`probe_movenet.py` 检查模型并在 **x86 Linux TFLite Micro** 上运行常量输入；新增的参考与 C 核验脚本还支持事先准备的 RGB 文件。它们不证明 Mini3 速度、人体识别精度或完整运动计数。研究结论见 [板端可行性记录](../../docs/board-pose-feasibility.md)。

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
| `heads` | 保留量化 CNN 及原始四头，外移输入转换 | Invoke 成功，退出码 0；此探针仅输出四头，17 点解码见下节 |

`heads` 输出 tensor 259、276、282、285，shape 分别为 `[1,48,48,1/17/34/34]`。源模型未被覆盖；派生模型和 JSON 报告默认写入 `.cache/pose-probe/`。模型 hash 不匹配时拒绝套用固定 tensor 索引。

**2 MiB（2,097,152 B）是传入的容量上限；本次 heads 的 allocator 实际账目是 1,945,792 B**，其中 head 1,777,568 B，tail 168,224 B。实际占用由 TFLM `print_allocations()` 写入 stderr，需要和 JSON 一起保留。模型文件约 2.76 MiB 不包括在 arena 数字里，Mini3 的 Flash XIP、运行栈和其他任务要单独预算。

该模型卷积量为 270,549,504 MAC/帧。成功执行于桌面 CPU 不代表 300 MHz 芯片能实时执行，尤其在候选 LS2663 无 NPU 配置下。CPU 解码现已完成下述数值验证，仍需原图精度与板端计时；不要把此探针输出接成真实运动奖励。

## 17 点 C 解码的完整参考

参考运行器采用 [Google LiteRT](https://developers.google.com/edge/litert/overview) 的兼容 `Interpreter` API，以 `BUILTIN_REF`、单线程、无 delegate、保留中间张量的方式核对原模型算术，不用于产品部署。固定包 `ai_edge_litert-2.3.0-cp312-cp312-manylinux_2_27_x86_64.whl` 为 21781762 字节，SHA-256 `dc9f56c23e0dcf182b6c35f23fbb102ed048296588882b806d5eff7654c04c88`；它已解包到忽略的 `.cache/pose-oracle/litert`，NumPy/schema 继续使用上面的隔离目录。

下面命令在 WSL 仓库目录运行，需要系统 C 编译器；没有相机、ADB、音频或刷写操作：

```bash
python3 scripts/pose/make_decode_oracle.py \
  --model .cache/research-pose/movenet-lightning-int8-v4.download \
  --micro-path .cache/research-pose/tflm-host --litert-path .cache/pose-oracle/litert \
  --random-cases 32 \
  --micro-heads .cache/research-pose/movenet-int8-heads-host-probe.tflite
python3 scripts/pose/verify_native_pose.py --python-path .cache/research-pose/tflm-host
```

四头模型 SHA 必须为 `9051a5ceebc1eb5f535c1b1afe58d33ce878bf9358a159cbfc689c1abebf9c68`；`probe_movenet.py --mode heads` 会生成相同内容，默认文件名在 `.cache/pose-probe/`，可据实际路径传入。oracle 脚本按原图切出后处理，不改输入模型；同时生成四个精确一元查表、参考 NPZ 和报告。C 核验脚本实际编译 `pose_movenet.c`，比较 17×3 个 float32、中心位置和全部关节索引，任何不同都会失败，不使用宽松容差。

可选的真实图片验证使用 [TensorFlow.js 测试图 pose.jpg](https://github.com/tensorflow/tfjs-models/blob/c731b9ebbd6f4c9e8bf99b0df76bbdbf9c25b07f/pose-detection/test_data/pose.jpg)，1000×667，SHA-256 `c8a830ed683c0276d713dd5aeda28f415f10cd6291972084a40d0d8b934ed62b`。原图与解码 RGB 只保存在 `.cache/pose-oracle/`，不随仓库重新分发。将 JPEG 用 Pillow 转为 RGB888 后：

```bash
python3 scripts/pose/verify_native_pose.py --python-path .cache/research-pose/tflm-host \
  --prepare-rgb .cache/pose-oracle/pose.rgb --width 1000 --height 667
# 在前面的 make_decode_oracle.py 命令后添加：
# --rgb-input .cache/pose-native/input-192.rgb
```

上述历史报告使用当时 C 的 center-nearest 等比例补边和量化：44 个参考用例全部逐 float 一致；4 个完整输入的 Micro 四头与 LiteRT 四头均逐字节一致，Micro arena 为 1945792 字节。报告分别位于 `.cache/pose-oracle/results/oracle-report.json` 与 `.cache/pose-native/decode-parity.json`。当前生产预处理已改为完整视野的整数双线性缩放，不能直接复用旧图像输入结果；新像素与源码 TFLM 的完整验证见[真实动作验证](../../docs/pose-exercise-validation.md)。这些是数值与主机链路证据，不是人体标注误差、运动计数准确率或板端 FPS。

无 Python/模型依赖的 C 契约测试，以及目标对象编译检查：

```powershell
pwsh -File scripts/test-pose-image.ps1 -Compiler clang
pwsh -File scripts/test-pose-decode.ps1 -Compiler clang
pwsh -File scripts/check-pose-target.ps1
```

最后一条只产出 ELF32 对象，未链接 TFLM，也不生成可刷写固件。Mini3 原生构建默认有 `-ffast-math`；未来集成必须对这些组件显式覆盖为 `-fno-fast-math -ffp-contract=off`。原始图和模型不会上传到服务端，脚本也不会开启任何摄像头。
