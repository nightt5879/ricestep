# MobileNet 小型姿态实验（Pilot04）

2026-10-29：新模型改善了肘腕、膝踝定位，但**尚不能交付运动计数**。65 帧完整视频在沿用旧模型门槛的诊断中全部被拒绝，新模型的分数仍待单独标定；未接入固件或奖励，未操作开发板。前序实验见 [TinyPose 记录](pose-student-experiment.md)。

## 为什么更换骨干

Pilot03 固定权重在 2,048 张训练图上的 PCK@0.10 为 59.94%，256 张验证图为 58.74%，肘腕分别为 41.04%/39.98%，训练数据本身也没有拟合充分。进一步用原结构、原损失拟合固定 16 张训练图，不增强、最多 1,000 步；250 步时 PCK@0.10 达 100%，最终 PCK@0.05 也达 100%。这只证明训练链路可以记住小样本，不能证明产品准确率。

独立数值检查没有发现超过一个输入像素的裁剪/翻转错位：48 个实际裁剪样例的最大标签/像素质心差为 0.01111 输入像素；局部偏移监督和 5 例组合损失最优解保持目标峰位置。这些证据支持继续检验模型能力和训练覆盖，而非通过移动标签或降低门槛制造次数。缓存证据在 `.cache/pose-overfit16-v1/`、`.cache/pose-student-geometry-audit/` 和 `.cache/pose-student-pilot-03-fit-audit/`。

## 模型与权重来源

选用 MobileNetV1 alpha 0.25，截到 `conv_pw_11_relu`，保留 stride 4/8 跳接。96×96 RGB 输入；主干输出 6×6×128，经两级 bilinear 上采样和 64/32 通道卷积头，输出与旧模型一致的 24×24×17 logits、24×24×34 局部偏移（y/x 交错）。完整结构有 **128,003 个可训练参数、9,531,648 名义卷积 MAC**；不计 BN、激活、插值、加法和内存开销，不是板端时延。

初始权重来自 [Keras 官方 MobileNet 实现](https://github.com/keras-team/keras/blob/601488fd4c1468ae7872e132e0f1c9843df54182/keras/applications/mobilenet.py) 指定的 Google Storage 文件 `mobilenet_2_5_128_tf_no_top.h5`，2,108,140 B，下载后固定 SHA256：

`7d8b7f3c9555cb4579f9659ee233a540e66fd7ad514c0a8b14493b667447c5d6`

这是 **ImageNet 图像分类骨干，不是预训练姿态模型**。Google 的 [MobileNetV1 模型系列卡](https://www.kaggle.com/models/google/mobilenet-v1) 标注 Apache-2.0，Keras 官方实现说明了从原始 TensorFlow 权重转换的来源链；H5 内没有另附许可证，不能把任意同名文件视为同一来源。下载权重不提交到仓库。

V1 没有专门的 96px ImageNet 检查点，因此显式加载上述本地 128px 文件，再在 96px 上微调；不调用会为 96px 自动选择 224px 权重的 `weights='imagenet'` 路径。TensorFlow 2.15.1→Torch 的三个特征层在 16 张真实裁剪和 4 组边界图上对齐，最大绝对差 6.28e-5。完整新结构与旧 TinyPose 使用不同的架构和 NPZ 格式标识，转换器必须显式选择新架构。

## 训练与对照结果

沿用 Pilot03 的同一份 2,048/256 图 manifest、增强、seed 26029、40 epochs、batch 16、AdamW lr 0.001、空间 CE + BCE 幅值约束 5 + 局部偏移损失。模型结构、初始化和 BN 训练动量均有变化，**不是单独隔离预训练作用的消融实验**。

最佳 epoch 39，checkpoint SHA256 `7933956d31455308a61b6369a250897906139f2d68acc4de6d06992f252f3b84`。验证共 3,655 个标注关节；指标按真实人物框对角线归一化，不按预测分数筛掉失败关节。验证集也用于挑选最佳 epoch，因此不是独立测试集，更不是完整画面/设备准确率。

| 验证 PCK@0.10 | Pilot03 | Pilot04 | 训练平均姿态模板 |
| --- | ---: | ---: | ---: |
| 全部关节 | 58.74% | 64.60% | 49.58% |
| 肘腕 | 39.98% | 51.08% | 30.24% |
| 膝踝 | 53.91% | 58.56% | 47.67% |

肘腕平均归一化误差从 0.16484 降至 0.14967，膝踝从 0.12280 降至 0.11314，均好于模板。打乱/纯黑/纯灰输入的整体 PCK@0.10 为 32.07%/3.69%/4.68%；172/256 张图的平均误差好于模板。新模型在训练集的整体 PCK@0.10 为 70.87%，与验证差距扩大到 6.27 个百分点；这不能单独证明容量或数据哪一项造成差距。

本机训练约 219 秒，Torch GPU 张量峰值 75,805,696 B，保留 96,468,992 B，不含驱动/完整进程。可选 RGB 缓存保留 1,922,804,061 B 数组，上限 2 GiB，减少重复 JPEG 解码；这不是总进程内存上限。固定 128 图三次增强采样中，未缓存约 0.38–0.40 秒/遍，缓存约 0.12–0.14 秒/遍，所有输入与监督张量哈希完全相同。训练与缓存时间都是主机证据，不能用于推算 Mini3 FPS。

## 转换与完整视频

完整浮点模型通过 Torch/Keras/TFLite 的 16 张验证输入对齐；最大绝对差分别为 4.10e-5 和 6.35e-5。32 张真实训练裁剪用于整数校准，均与验证图分离。整数文件 **210,808 B**，SHA256 `e13e207965953fdea4811a1649feb4a751587abe451f534935a1dd49791a2a9f`；输入与两个输出均为 int8，实际输入 scale 为 0.007843137718737125、zero-point 为 -1。

256 张验证图的浮点/整数 PCK@0.10 为 64.60%/64.73%，平均误差为 0.10548/0.10524；肘腕 PCK 为 51.08%/50.06%。整体接近，但浮点/整数关节位移的均值/95 分位/最大值为 1.05/3.98/55.41 输入像素，26/3,655 个关节超过 16 像素。少量跳点仍影响连续计数；并非每次位移都是变差，也不能以平均分掩盖尾部不稳定。报告在 `.cache/pose-student-pilot-04-quant-audit/`，同一批输入已核验缓存前后完全相同。

使用实际固定版本 TFLM 和项目 pointwise 内核，单独编译新结构的主机核验程序：35 个算子（18 Conv2D、13 Depthwise、2 Add、2 ResizeBilinear），32 张真实校准图的两头共 **940,032 字节与 LiteRT 2.3 BUILTIN_REF 逐字节一致**。实际 x86_64 arena 为 **99,904 B**，提供 128 KiB 时全部输入也通过；1 KiB 干净拒绝分配且保护字节完整。每帧 17 个 pointwise 使用优化实现，3×3 stem 使用参考实现。该数字不含相机、任务栈、解释器对象、Flash 或完整进程，不代替 Mini3 内存/速度测量。证据在 `.cache/pose-student-tflm-probe-04/`。

65 帧完整 320×240 视频保留原黑边，缩放到 96×72 再补边，未使用人物真值框。新模型仍有 65 帧 `low_confidence`、0 次完整循环，最大未标定 sigmoid 峰值 0.59004。没有为了获得次数而修改 `.60` 门槛。时间戳与处理延迟是诊断合成值；视频没有逐帧人工关节真值，不能据此给准确率或帧率。报告位于 `.cache/pose-student-pilot-04-exercise/`。

**旧 `.60` 门槛不是这个新模型的可靠性证明。** 对原训练损失直接求自由 logits 最优解，即使定位和偏移完全正确，整数网格中心的最优 sigmoid 峰值为 0.6389，双轴半格位置只有 0.5779，接近右下边界时约 0.3321。12 例数值残差均小于 9.4e-17；证据在 `.cache/pose-student-score-objective-audit/`。因此不能把 65 帧拒绝解释为“65 帧姿态全错”，也不能简单降阈值放行。需要用独立数据验证与实际定位误差、可见性相关的评分，再验证计数。

## 复现

先按 [训练环境说明](pose-student-experiment.md#复现与检查) 准备隔离 Torch 环境、COCO manifest 和单独的 TensorFlow 2.15.1 解释器。以下只操作主机缓存，不启动设备；路径变量需指向实际文件。官方下载的 H5 在转换前必须通过内置大小及 SHA 检查。

首次将 [官方 H5](https://storage.googleapis.com/tensorflow/keras-applications/mobilenet/mobilenet_2_5_128_tf_no_top.h5) 保存到下方 `$h5` 指定的缓存位置；转换脚本不会隐式下载其他权重。

```powershell
$trainPython = '.cache/pose-train-venv/Scripts/python.exe'
$tfPython = 'C:\path\to\tensorflow215\python.exe'
$h5 = '.cache/research-pose/imagenet-backbone/mobilenet_2_5_128_tf_no_top.h5'
& $tfPython -B scripts/pose/mobilenet_transfer.py --weights $h5 --output .cache/backbone-repro.npz
& $trainPython -B scripts/pose/train_student.py --manifest .cache/pose-dataset-coco2017-2k/manifest.json --output-dir .cache/mobile-repro --model mobilenet025 --backbone-weights .cache/backbone-repro.npz --image-cache-mib 2048 --epochs 40 --batch-size 16 --device cuda --heatmap-loss spatial --amplitude-weight 5
& $trainPython -B scripts/pose/audit_student.py --checkpoint .cache/mobile-repro/best.pt --manifest .cache/pose-dataset-coco2017-2k/manifest.json --output .cache/mobile-repro-audit
& $tfPython -B scripts/pose/student_export.py --architecture ricestep-mobilenet-v1-025-pose-v1 --weights .cache/mobile-repro/weights.npz --output-dir .cache/mobile-repro/export --representative .cache/mobile-repro/calibration.npz --representative-metadata .cache/mobile-repro/calibration.json --parity-reference .cache/mobile-repro/parity-reference.npz
```

`--image-cache-mib 0` 保持原来的逐图解码；开缓存时先检查整批数组预算，全部解码成功后才挂接，只读像素不改变增强。Mobile 模型没有有效权重就报错，不退回随机骨干。审计从每次训练保存并核验的源码快照重建，包括 Mobile 对旧基础模块的依赖；历史 Tiny 65 帧重放已逐值、逐状态及图像哈希核对一致。

研究管线现有 72 项测试：数据准备 28、数据几何/缓存 6、训练入口/损失 13、Tiny 模型 4、架构审计 8、Mobile 骨干 4、Tiny 转换 6、Mobile 转换 3。另有真实骨干三层特征及整数主机核验。专项检查没有播放声音或访问设备。

下一步优先建立新模型自己的定位/可见性评分和独立测试，同时改善弯曲/遮挡时的定位；允许实机操作后再测 Mini3 时延与相机连续计数。现有桌面陪伴、做饭、声音与存档逻辑未改动。
