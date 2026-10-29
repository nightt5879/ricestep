# MoveNet Lightning int8 v4 后处理

这是 **四个量化预测头 → 17 个 COCO 关键点**的独立 C11 解码器，不包含相机驱动、CNN 推理、动作计数、模型权重或设备访问。只适用于模型 SHA-256 `cd7cc22fa946e5d146a7b98d496853e1923e22828d3972d579973f27f91bb105`。它没有接入开发板，也没有测板端速度或识别精度。

## 输入、输出和资源契约

`pose_movenet_decode()` 接受四个只读、连续的 INT8 NHWC tensor，输入范围是原始 logits/offset，不能预先 sigmoid。调用方必须先验证模型哈希；C 函数只接收张量，无法再次验证模型文件。

| Tensor | 内容 | Shape | scale | zero point | 字节数 |
| --- | --- | --- | --- | --- | ---: |
| 259 | 中心 logits | `[1,48,48,1]` | 0.2822169065475464 | 114 | 2304 |
| 276 | 关键点 logits | `[1,48,48,17]` | 0.28092747926712036 | 115 | 39168 |
| 282 | 局部偏移 | `[1,48,48,34]` | 0.19978104531764984 | -9 | 78336 |
| 285 | 中心到关键点的回归量 | `[1,48,48,34]` | 0.6884398460388184 | -21 | 78336 |

两个 34 通道张量都按 `y0,x0,y1,x1,...` 交错排列。17 点顺序为鼻、左右眼、左右耳、左右肩、左右肘、左右腕、左右髋、左右膝、左右踝。输出为 `float { y, x, score }[17]`，与原模型 tensor332 的 `[1,1,17,3]` 顺序相同。坐标相对完整的 192×192 输入帧，包括 letterbox padding；解码器不增加半像素偏移，也不裁剪坐标。原图映射属于预处理模块。

四个输入共 **198144 字节**，由推理调用方提供；可以直接引用推理输出。解码器不分配堆、不复制整张热图、没有可变全局状态。每个关节循环只使用两条 48 字节的平方距离数组，输出由调用方提供（204 字节），可选 trace 记录中心/关节索引及获胜的量化分数。源代码只用固定大小局部变量；实际函数栈大小仍由目标编译器决定。

只使用 C11 标准库和 float32；禁止 fast-math（实现会拒绝 `__FAST_MATH__`）。所有字节数必须完全匹配；空指针、短/长缓冲返回 `POSE_MOVENET_INVALID_ARGUMENT` 且不改输出。调用方不能让输入、输出和 trace 重叠。

## 保留的原模型语义

`movenet_v4_contract.json` 从固定模型导出 **62 个后处理节点、87 个相关 tensor**，没有 CNN 权重。不能把这里简化为普通热图 argmax：

1. tensor259 经量化 sigmoid，与 tensor154 的中心先验相乘并再次量化，再按行优先顺序取第一个最大值。
2. 在该中心位置采样 tensor285，按关节拆成 y/x；分别加上**量化后的**中心坐标。t266/t271 的校准范围会把边缘中心坐标饱和到约 26/27，必须保留。
3. 网格与预测位置依次经过量化 SUB、平方 MUL、ADD，反量化后 SQRT，再量化并加常量约 1.8。并未用浮点欧氏距离替换这些中间舍入/饱和。
4. t276 的量化 sigmoid 概率除以上述距离因子，**先量化到 t303 再取每个关节的第一个最大值**。在量化之后并列时，较早像素获胜。
5. 在每个关节获胜像素上，从 tensor282 中取相同关节的两个偏移通道，加上量化坐标，经量化乘法归一化。置信度使用该像素原始热图概率的 requantize，不是距离加权分数。
6. 坐标与置信度先拼接成 t331 INT8（scale `0.0040968540124595165`、zero point `-128`），再反量化成 float。输出因此不是无限精度连续坐标，极端合成输入可产生大于 1 的坐标；没有额外 clamp。

整数计算保留 TFLite 的两阶段定点舍入；相关参考来源为 TensorFlow 的 [int8 ADD](https://github.com/tensorflow/tensorflow/blob/v2.19.0/tensorflow/lite/kernels/internal/reference/integer_ops/add.h)、[int8 MUL](https://github.com/tensorflow/tensorflow/blob/v2.19.0/tensorflow/lite/kernels/internal/reference/integer_ops/mul.h) 和 [float QUANTIZE](https://github.com/tensorflow/tensorflow/blob/v2.19.0/tensorflow/lite/kernels/internal/reference/quantize.h)。本目录是独立的标量实现，没有引入 TensorFlow C++ 依赖。

两个 sigmoid 以及 `SQRT→QUANTIZE→ADD(1.8)` 使用经过官方 LiteRT `BUILTIN_REF` 单算子/子图穷举得到的小表，避免运行时 exp/sqrt 的数值差异。中心先验与网格表属于固定后处理常量，未包含学习权重。

## 复现与测试

```powershell
pwsh -File scripts/test-pose-decode.ps1
pwsh -File scripts/test-pose-decode.ps1 -Compiler clang
```

原生单元测试包含 443 项断言：无效输入不改输出、输入不可变、可选 trace、官方常量输入精确结果、中心先验改变中心选择、距离加权改变关节选择、并列取首、独立关节索引和交错偏移。官方常量输出以 float hex literal 固定，避免十进制转写损失。

另外，已用 WSL GCC 编译真实 C 实现，对 `.cache/pose-oracle/results/` 的 **44 组官方 LiteRT 2.3.0 `BUILTIN_REF`、无 delegate 参考结果**逐 float 对比，全部 `array_equal`，没有放宽容差。样本包含 4 组常量预测头、32 组随机预测头、4 组完整模型输入及其对应的 4 组真实 Micro CNN 输出。完整输入包括三组合成 RGB 和一张 TensorFlow.js 官方公开人体测试图；Micro 四头与完整模型四头逐字节一致。完整模型与裁剪后处理子图也经过独立对齐。这证明这些输入上的数值一致，**不证明人体标注误差、运动识别准确率或设备性能**。复现由 `scripts/pose/make_decode_oracle.py` 与 `verify_native_pose.py` 完成；模型、依赖、原图和 NPZ 留在忽略缓存中。

重新导出契约与表（使用已存在的隔离依赖和 oracle）：

```powershell
python firmware/components/pose_decode/export_contract.py --model .cache/research-pose/movenet-lightning-int8-v4.download --parser-path .cache/research-pose/parser
python firmware/components/pose_decode/generate_tables.py --unary-luts .cache/pose-oracle/results/unary-luts.json
```

源码导出工具会先核对原模型 SHA-256。更换模型、量化参数、参考内核或 LUT 后，必须重新生成并比较完整参考输出，不能继续套用固定 tensor 索引。表不提供独立的模型分发许可；正式分发模型仍需完成模型卡与权重许可记录。
