# Mini3 板端人体姿态识别：证据与验证路线

核查日期：2026-10-29。目标是 **Mini3 自带摄像头采集、板端识别动作、肥鱼反馈并结算米饭**。用户接受底层开发，优先不依赖手机或电脑摄像头。电脑目前只作为编译、模型分析和调试工具。

## 结论

板端摄像头路线值得继续做，但 **Mini3 候选源码指向 LS2663、8 MB PSRAM，与旧通用 Mini 的 LS2684/NPU 资料不同**。如果该型号属实，官方芯片表将其列为无 NPU，不能沿用旧 Mini 的 16 MB / 64 G ops 假设。现阶段应按无 NPU、AP 约 3 MB 的严格预算推进 CPU 模型验证，并继续核实实板。

已经在现有 WSL 中用 **官方 TFLite Micro 原生解释器实际执行了 MoveNet 的量化 CNN 四头子图**：2 MiB arena 可运行，实际分配 1,945,792 bytes。完整原模型遇到具体算子类型不支持；四头输出还需要 CPU 解码才能变成 17 个关键点。这是 CPU 参考兼容性/内存证据，**没有编译/烧录姿态固件，没有测得板端 FPS或验证真实健身动作**。

官方有摄像头、双核通信和视觉推理组合示例，但查到的主要是分类、人脸、掌静脉，没有在本次核查目录中找到可直接部署到 Mini3 的全身姿态/俯卧撑计数示例。

## 1. 芯片资源：要先确认完整型号

[LS26xx 官方芯片资料](https://docs2.listenai.com/arcs-sdk/latest/zh/html/hardware/soc/ls26xx.html)给出的资源是双核 RISC-V、最高 300 MHz、704 KB SRAM。系列内部区别如下：

| 芯片系列 | PSRAM 档位 | NPU |
| --- | --- | --- |
| LS2662 / LS2663 / LS2664 | 4 / 8 / 16 MB | 无 |
| LS2682 / LS2683 / LS2684 | 4 / 8 / 16 MB | 标称 64 G ops |

[通用 ARCS Mini 板文档](https://docs2.listenai.com/arcs-sdk/latest/zh/html/hardware/boards/arcs_mini.html)明确写了 `arcs_mini` 使用 **LS2684L0U，16 MB PSRAM + NPU**，并提供 DVP 摄像头接口。这支持板端视觉路线，但该文档没有单独证明用户这块 **Mini3** 的完整芯片型号。还需要 Mini3 BOM/芯片丝印、匹配的板级配置或可靠硬件识别结果；不能仅凭“LS26”推断有或没有 NPU。

随后读到 [Mini3 候选公开源码](https://github.com/justa-cai/listenai-arcs_mini/tree/66568d04375a933bf814b921c8f4442e8918f101)，提交 `66568d04375a933bf814b921c8f4442e8918f101`：

- `apps/remote-ap/arcs_mini3.conf` 明确注释 `LS2663, 8MB PSRAM`，AP 3 MB，不能放下 6 MB 权重镜像，改用 Flash XIP。
- `apps/arcs-mini/arcs_mini3.conf` 为 CP 配置 `0x28300000` 起、`0x500000` 大小，CP PSRAM heap 为 `0x370000`。这些是应用既有占用/预算，不能再全部算作姿态空闲区。
- `arcs-sdk/boards/arcs_mini3` 的相机 I2C1 PB0/PB1 等映射也不同于旧 Mini 文档，进一步说明不能混用板型。

这是社区镜像/改动的源码证据，不是经过独立认证的厂商 BOM，也不是本机硬件识别结果；与官方系列表结合后，应把 **无 NPU、8 MB** 作为保守开发前提，等待实板确认，而不是继续按 LS2684 的乐观资源预算。当前应用 AP 分区为 3 MB 不等于永远不可重分区，但重分配需同时审核 CP 的相机、声音、网络和显示占用。

双核不等于可以把 2 × 300 MHz 全部用于姿态计算。官方架构中 CP 承担通信，具体示例也会重新安排两核任务；要按实际固件的语音、网络、摄像头、显示分工计算余量。64 G ops 是芯片标称算力，不是本模型的实测吞吐。

## 2. 已找到的官方可运行起点

| 官方工程 | 已有链路 | 对本项目的作用与边界 |
| --- | --- | --- |
| [LNN ResNet18 实时识别](https://docs2.listenai.com/arcs-sdk/latest/zh/html/labs/lnn/thinker_resnet18_real/README.html) | CP 摄像头/LVGL/LCD，AP Thinker/Luna；共享内存传输入、mailbox 通知 | 最接近需要的底层结构。但实际流程是按 RUN 冻结一帧，输入 `int8[1,3,32,32]`，输出 CIFAR 分类；不是连续姿态识别。 |
| [TFLM MobileNet V2](https://docs2.listenai.com/arcs-sdk/latest/zh/html/labs/tflite_micro/mobilenet_v2/README.html) | ARCS CPU0 调用 Luna 的 Conv2D、DepthwiseConv2D、FullyConnected | 验证 TFLM + NPU 链路。使用常量输入，不做真实图像分类；文档没有给实际 FPS。 |
| [人脸算法示例](https://docs2.listenai.com/arcs-sdk/latest/zh/html/samples/algorithms/face_detect/README.html) | ACOMP 图像流、检测/对齐/活体/特征提取 | 有图像入算法、异步结果和内存布局参考；人脸关键点、头部姿态不能替代肩肘腕髋膝踝。 |
| [掌静脉算法示例](https://docs2.listenai.com/arcs-sdk/latest/zh/html/samples/algorithms/palm/README.html) | 原生视觉算法资源及缓冲分配 | 可参考资源管理，不能据此判断全身模型速度。 |

核查范围为[算法示例目录](https://docs2.listenai.com/arcs-sdk/latest/zh/html/samples/algorithms/index_zh.html)、[LNN 实验室](https://docs2.listenai.com/arcs-sdk/latest/zh/html/labs/lnn/index.html)、[TFLM 实验室](https://docs2.listenai.com/arcs-sdk/latest/zh/html/labs/tflite_micro/index.html)和下述官方 GitHub 仓库。这里的“未找到”只描述本次范围，不宣称厂商没有未公开的姿态算法。

TFLM MobileNet 示例的模型约 **3.84 MiB**，ARCS tensor arena 配置 **6 MiB**，其中最多 **4 MiB** 用于持久化预打包卷积权重；这 4 MiB 在 arena 内，不应再次相加。官方还要求推理任务栈在内部 SRAM，并为 Luna 配置 APRAM。它能说明内存问题需要认真规划，不能由“模型只有几 MB”推断能与现有全部功能同时运行。

文档中的 `arcs_evb` 构建命令只能先用于验证工具链；不能把 EVB 镜像直接当作 Mini3 镜像。要核对工程 `sample.yaml`、`boards/`、GC0328 引脚/时钟、LCD、按键、Flash 分区和恢复方式后再移植。完整原生 SDK 和当前 LingClaw Lua 小应用是不同部署层次。

## 3. 实际模型文件分析：MoveNet Lightning int8 v4

候选模型用于先检查迁移难点，并非已经选定最终产品模型。[TensorFlow 官方姿态介绍](https://android.googlesource.com/platform/external/tensorflow/+/refs/heads/master/tensorflow/lite/g3doc/examples/pose_estimation/overview.md)将 MoveNet 用于人体关键点估计；手机上的性能不能直接套用到 LS26。

下载入口：[Google MoveNet SinglePose Lightning TFLite int8 v4](https://tfhub.dev/google/lite-model/movenet/singlepose/lightning/tflite/int8/4?lite-format=tflite)。本次文件保存于忽略目录 `.cache/research-pose/movenet-lightning-int8-v4.download`，没有提交模型二进制。

| 项目 | 本次静态读取结果 |
| --- | --- |
| 文件大小 | 2,894,840 bytes，约 2.76 MiB |
| SHA-256 | `cd7cc22fa946e5d146a7b98d496853e1923e22828d3972d579973f27f91bb105` |
| 格式 | `TFL3`，schema 3，1 个 subgraph |
| 输入 | UINT8 `[1,192,192,3]` |
| 输出 | FLOAT32 `[1,1,17,3]` |
| 图规模 | 157 个算子节点、333 个 tensor |
| Tensor 类型 | 215 个 INT8、106 个 INT32、11 个 FLOAT32、1 个 UINT8 |

算子计数如下（名称是 TFLite BuiltinOperator）：

```text
CONV_2D 50          DEPTHWISE_CONV_2D 24    ADD 19
RESHAPE 12         QUANTIZE 8             MUL 7
SUB 6              CAST 5                 GATHER_ND 4
DEQUANTIZE 4       RESIZE_BILINEAR 3       PACK 3
ARG_MAX 2          CONCATENATION 2         FLOOR_DIV 2
LOGISTIC 2         UNPACK 2               DIV 1
SQRT 1
```

这直接说明 **“int8 模型”仍可能含浮点中间计算和非卷积后处理**。本文件的 CAST、DEQUANTIZE、DIV、QUANTIZE、SQRT 节点涉及 FLOAT32 tensor。不能把该文件直接当作 Thinker 资源，也不能只注册官方 MobileNet 示例的算子便宣称兼容。

本次只用 Python、已有 `flatbuffers` 和隔离安装的 `tflite==2.18.0` 读取结构，没有安装完整 TensorFlow。复核命令如下；依赖仅放在忽略目录，不修改项目运行依赖：

```powershell
python -m pip install --no-deps --target .cache/research-pose/parser tflite==2.18.0
Get-FileHash .cache/research-pose/movenet-lightning-int8-v4.download -Algorithm SHA256
@'
import sys, pathlib, collections
sys.path.insert(0, '.cache/research-pose/parser')
import tflite
b = pathlib.Path('.cache/research-pose/movenet-lightning-int8-v4.download').read_bytes()
assert b[4:8] == b'TFL3'
m = tflite.Model.GetRootAsModel(b, 0)
s = m.Subgraphs(0)
names = {v:k for k,v in vars(tflite.BuiltinOperator).items() if isinstance(v,int)}
counts = collections.Counter(names[m.OperatorCodes(s.Operators(i).OpcodeIndex()).BuiltinCode()]
                             for i in range(s.OperatorsLength()))
print(len(b), s.OperatorsLength(), dict(sorted(counts.items())))
'@ | python -
```

## 4. 框架兼容性与许可边界

### Thinker / Linger 路线

官方 [Thinker](https://github.com/LISTENAI/thinker) 和 [Linger](https://github.com/LISTENAI/linger) 仓库标注 Apache-2.0。Thinker 是 C 执行器和离线打包工具；Linger 处理量化训练/导出。此次读取 Thinker `main` 的提交为 `b9961deb1a49fdadf713cd10081ead5c82bff59a`。仓库实际包含 ARCS 的 `libluna.a`、`liblunaext.a` 及头文件，但这不替代匹配的固件/BSP、版本与二进制许可核查。

该提交的[算子支持文档](https://github.com/LISTENAI/thinker/blob/b9961deb1a49fdadf713cd10081ead5c82bff59a/docs/support_quant_ops.md)有几个直接影响姿态模型的限制：

- 普通计算图不支持 Float32 运算；Quant/Dequant 是模型边界转换例外。
- ARCS 有量化 Conv2d、ConvTranspose2d 等路径，但必须满足 shape、kernel、stride、量化 scale 和 workspace 限制。
- `Resize` 的正常离线入口仅开放 Venus，ARCS 未开放；有通用 C 源文件并不代表 ARCS 打包可达。
- `Gather` 已支持 ARCS，但不能等同于 TFLite `GATHER_ND`。要按实际图验证。

因此，若走 Thinker，需要量化/重构合适的主干与关键点头，必要时将缩放与解码放在 CPU，并对同一帧比较训练端、仿真端和板端结果。不能承诺将现成 TFLite 一键转换后保持精度。

[官方 tpacker 参数](https://github.com/LISTENAI/thinker/blob/b9961deb1a49fdadf713cd10081ead5c82bff59a/docs/thinker_packer.md)支持显式 `-p arcs`、`-r` 共享内存预算、`--psramsize` 预算和 `-d True` 内存报告。它默认采用平台最大值；产品必须改成实际分给模型的预算。该版本文档中 ARCS 最大共享预算为 393216 bytes，默认 PSRAM 上限为 8388608 bytes；不能与整颗芯片的 16 MB PSRAM 混为一谈。

### TFLM 路线及实际 CPU 参考验证

TFLM + Luna 是厂商明确列为实验室能力的路线。要逐个确认 MoveNet 的 19 类算子在目标构建中的注册、类型和参数支持，记录 CPU 回退部分及耗时。先 `AllocateTensors()` 验证实际 arena，再用已知图片 `Invoke()` 对齐输出；手机 TFLite 支持不等于 TFLM 支持，TFLM 能执行也不等于全部由 NPU 加速。

为避免只停留在文档推测，使用 [TensorFlow 官方 TFLM Python 解释器](https://github.com/tensorflow/tflite-micro/tree/fed64d19fb5af8d28ee108c07838d5c0ae3ddcbb/python/tflite_micro)做了三个实际实验。运行环境为 WSL Ubuntu 24.04 / Python 3.12.3，未用电脑摄像头，没有安装全局依赖。官方 wheel 版本 `tflite-micro==0.dev20261026171606`，文件 SHA-256：`4dd4fbcd3329221a4da23b349678763c778d4a079a81eff387aea59bff3f1466`。辅助依赖为 numpy 2.2.6、flatbuffers 25.12.19、PyYAML 6.0.3。

| 实验 | 真实结果 |
| --- | --- |
| 原始 MoveNet，2 MiB arena，常量 UINT8 128 输入 | 分配成功；执行第 0 个 CAST 时失败：`Input type UINT8 (3) not supported`。 |
| 将前两个输入 CAST/QUANTIZE 外移，输入改为 tensor 175 的 INT8 | 输入量化 scale=1、zero_point=-128，因此 `int8 = uint8 - 128`；继续执行到 INT32 `FLOOR_DIV` 失败。实际 arena 1,279,968 B（head 1,105,920，tail 174,048）。不能把分配成功当作整网成功。 |
| 从同一模型提取量化主干及四个原始预测头，2 MiB arena | **Invoke 成功**；93 节点，四个 int8 输出形状 `[1,48,48,1]`、`[1,48,48,17]`、`[1,48,48,34]`、`[1,48,48,34]`。实际 arena 1,945,792 B（head 1,777,568，tail 168,224）。 |

第三个实验只保留输出 tensor 259、276、282、285 的依赖图，并去掉可外移的两个输入节点；权重未重训、未改变数值。派生文件大小 2,891,896 B，SHA-256 `9051a5ceebc1eb5f535c1b1afe58d33ce878bf9358a159cbfc689c1abebf9c68`。可复现脚本已收敛为 [scripts/pose/probe_movenet.py](../scripts/pose/probe_movenet.py)，用法见[探针 README](../scripts/pose/README.md)。最终仓库脚本的 `inspect`、`heads` 已在隔离 WSL 依赖下成功执行，`full` 重现预期的 CAST 失败。派生模型与 JSON 输出在 `.cache/pose-probe/`，原始日志在 `.cache/research-pose/saved-*-probe.log`；均不提交 Git。

四头模型仍含量化 `RESIZE_BILINEAR`，它在本次 **TFLM CPU** 上能执行不代表 Thinker ARCS 能打包。完整关键点解码仍需实现中心选择、偏移/热图组合等，并和原模型在真实姿态图上的结果对齐。不能用常量输入的输出当作识别人形的证据。

该子图卷积计算量从模型张量 shape 复算为 **270,549,504 MAC/帧**。无 NPU 的 300 MHz 平台有明显速度风险；x86 测得的执行时间不用于预测 Mini3 FPS。Arena 不含模型文件的存储：若权重也复制到 AP PSRAM，总需求已超过 3 MB；必须考虑 Flash XIP 或其他分区方案，并用实板测 XIP 带宽和 CPU 实际耗时。x86 指针宽度、内核 scratch 分配和板端 ABI 不同，1,945,792 B 也不是板端 arena 的最终数值。

这给出了一个可验证的轻量内存方向：**Flash 权重 + 约 2 MiB CPU/TFLM 主干 arena + CPU 解码**。它目前只是内存/部分算子通过，尚无“完整、官方兼容、Mini3 实时姿态模型”的证据；不能以 TinyPose、NanoPose 等名称代替实际模型和实测。

### 官方检测参考仓库

下载检查了 [LISTENAI/ultralytics_lnn](https://github.com/LISTENAI/ultralytics_lnn)，提交 `4f33437a09722141422c51d139333788bd729ea9`。它提供检测、量化训练、打包流程，检查到的任务包括猫、狗、人脸、二维码、OCR、小车，没有人体关键点任务。训练报告中的 GPU 精度/耗时不是 Mini3 测量。

该仓库 GitHub license 字段为 null，本次没有认定其整仓代码可任意再分发。旧 `npu_limits.md` 的简化列表也不能覆盖当前 Thinker 平台矩阵。模型权重、训练数据、第三方 Ultralytics 代码及 Luna 二进制应分别记录许可；框架 Apache-2.0 不自动赋予其他资产相同许可。MoveNet 本次仅研究下载，正式打包分发前仍需保存对应权重版本的模型卡/许可记录。

## 5. 可复算的图像缓冲预算

以下只计算像素/张量载荷，不包含 stride 对齐、DMA 描述符、IPC、堆碎片、模型激活或语音/显示运行时。KiB = 1024 bytes。

| 数据 | 公式 | 大小 |
| --- | --- | --- |
| VGA RGB565 单帧 | 640 × 480 × 2 | 600 KiB |
| VGA RGB888 单帧 | 640 × 480 × 3 | 900 KiB |
| QVGA RGB565 单帧 | 320 × 240 × 2 | 150 KiB |
| QVGA RGB888 单帧 | 320 × 240 × 3 | 225 KiB |
| 240 × 240 RGB565 屏幕缓冲 | 240 × 240 × 2 | 112.5 KiB |
| MoveNet UINT8 输入 | 192 × 192 × 3 | 108 KiB |
| 17 个关键点的 float32 输出 | 17 × 3 × 4 | 204 bytes |

双缓冲将对应图像内存翻倍。即使关键点输出很小，主干激活、预打包权重及 NPU workspace 仍可能占数 MiB。必须从 linker map、实际 heap/arena 峰值和 `tGetMemoryPlan()` 记录完整账目；仅看文件大小不能判断能否装下。

## 6. 最短实证路线与通过条件

1. **确认硬件和恢复链路。** 拿到 Mini3 的确切 SoC、PSRAM/Flash、相机配置；固定 SDK/toolchain 提交，保留当前肥鱼小应用与官方固件恢复方法。只读分析不需要触碰设备。
2. **原生取帧基线。** 在 Mini3 引脚配置下让 GC0328 连续出真实帧，检查帧号、格式、尺寸、方向、曝光、丢帧；LCD 能预览。复用官方 DVP/摄像头工程，不把 Lua 中缺少相机 API 误判为硬件不可做。
3. **按真实芯片做计算基线。** LS2663 前提下先移植 CPU TFLM 和小型静态模型，记录内存与周期；不要调用未证实存在的 NPU。只有确认芯片确有 Luna NPU 后才测试官方 NPU 示例；此时除 `result=0` 外还应检查 `conv_luna`、`depthwise_luna`、`fc_luna` 计数均大于零。保存板上原始日志，不采用 x86 速度替代。
4. **姿态静态图片基线。** 为候选模型选择 TFLM 或 Thinker 路线，先通过算子/内存门槛，再对站立、侧身支撑、俯卧撑低点、遮挡等固定图片核对肩肘腕髋膝踝。记录浮点参考、量化后和板端误差；转换失败就记录具体算子和资源需求。
5. **连续姿态基线。** 串联相机→缩放/量化→推理→关键点→屏幕，分别测采集、预处理、推理、后处理、显示时间和内存峰值。至少连续运行 10 分钟，统计 P50/P95 延迟、实际有效 FPS、丢帧、断点和语音竞争。此时才决定能否运动计数。
6. **一个动作的完整验证。** 优先慢速俯卧撑，固定侧面机位且肩/肘/腕/髋可见，以准备→下降→低点→回升完整状态计数；低置信度/出画暂停，不凭画面变化发饭。用人工计数对照完整动作、半程、静止、遮挡和第二人入画，最后接一次性结算。波比跳在连续动作阶段识别稳定后再加入。

5 FPS 可作为首轮慢速动作的工程目标，不能视为已达到的性能或精度保证；波比跳需要另外测快速阶段是否漏检。若模型太慢，先缩小/改造网络、降低输入尺寸并复测精度，或只验证可辨识的慢动作；不把模拟关键点或计时器包装为摄像头测得的动作。

桌面放置适合存在感/陪伴，俯卧撑需要能看到身体的机位。应在进入运动会话时给出画面检查和站位反馈；开启、暂停、结束状态明确可见。久坐提醒可以先用可配置计时，只有经过人体观察验证后才能宣称“检测到持续久坐”。

## 当前尚未验证的事项

- 用户这块 Mini3 的确切 SoC/NPU/可用 RAM；通用 Mini 资料不足以替代硬件确认。
- 当前 SDK 版本中的 Mini3 BSP 与 GC0328 连续帧、显示和声音能否共存。
- MoveNet 或替代姿态网络的实际算子可达性、arena/共享内存峰值、量化精度与板端耗时。
- 运动计数、长期稳定性和奖励联动。

这些是下一轮实测的记录项，不是必须先改用电脑摄像头的理由。
