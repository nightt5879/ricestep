# 小型姿态模型训练实验

后续进展见 [Pilot04 MobileNet 实验](pose-mobilenet-experiment.md)：在相同验证集上改善了肘腕、膝踝定位，仍未通过完整视频计数验收。

2026-10-29：已完成隔离训练环境、三个训练 pilot、浮点/整数转换和 TFLM 主机核验；**尚不能作为运动计数模型**。最新 Pilot03 使用 2,048 张训练图、256 张验证图。人物框裁剪下的定位指标仍不足以证明健身识别，完整视频的诊断计数为 0。以下结果不是 COCO AP、板载摄像头准确率或 Mini3 FPS。没有更改固件或操作设备。

## 原型与环境

[`TinyPose`](../scripts/pose/student_model.py) 是本项目从头训练的模型，没有使用受 ST 硬件限制的权重。输入为 96×96 RGB、范围 `[-1,1]`；输出为 24×24×17 heatmap logits 和 24×24×34 局部偏移。偏移通道是 `y0,x0,y1,x1,...`，单位为输出网格；两个头都没有 sigmoid 或 ReLU。Torch 使用 NCHW，导出的 Keras/TFLite 使用 NHWC。

结构为 16→24→48→96 的深度可分离卷积编码器，加两级 bilinear 上采样和 12/24 网格跳接。中间卷积使用 BN（epsilon `1e-3`）与 ReLU6。Torch 显式实现 TensorFlow SAME：偶数尺寸的 stride-2 3×3 卷积前侧补 0、后侧补 1；上采样 `align_corners=False`。

- 43,651 个参数，卷积名义计算量 **5,140,800 MAC/帧**。此数不计 BN、激活、偏置、跳接加法与插值。
- hook 记录的最大单个张量为 38,416 元素，包含 Torch 显式补边；两个保留跳接合计 20,736 元素。这些不是 TFLM arena 或峰值存活内存。
- [`pose-training.lock.json`](../pose-training.lock.json) 固定 Windows Python 3.11、PyTorch `2.7.1+cu128`、NumPy `2.2.6`、Pillow `11.3.0`、Requests 及完整依赖的 wheel 哈希；隔离环境为 `.cache/pose-train-venv`。16 个 wheel 合计 3,302,919,089 B，解包另占空间；安装不修改全局 Python/CUDA。数据准备器也已在此 venv 实测，不依赖全局 Requests。
- TensorFlow `2.15.1` 的已有 CPU 环境用于镜像与导出，没有为此安装新的全局包。

## 数据与训练范围

前两个 pilot 使用 **64 张训练图、16 张验证图**，来自 COCO2017 的图像不重叠子集；验证共 222 个有效标注关节。Pilot03 扩大到 2,048/256 张，结果单列于下文。原始文件、逐图 SHA256、许可和标注保留在本地 manifest；下载脚本见 [`prepare_pose_dataset.py`](../scripts/pose/prepare_pose_dataset.py)。原图和权重不入 Git。

样本依赖真实人物框：以框中心取 `1.5 × max(width,height)` 的正方形区域，PIL bilinear 缩到 96×96，图像外补黑。训练额外做尺度、中心、翻转与亮度增强。COCO visibility 为 1 或 2、且落在裁剪内的关节参与评价。未独立核验的背景候选未参与训练。

两个 pilot 的 manifest SHA256 均为 `57cedd7f5e6d09b06684be1143f214af95c8f084e06db340ee99e7bb6b51369f`。人物框本身已提供位置、尺度先验；目前没有能替代它的板端人物检测/裁剪方案。

Pilot01 使用 sigmoid heatmap MSE，20 epochs 后验证 PCK@0.10 约 3.6%，不能使用。Pilot02 改用每个有效关节的空间 softmax 交叉熵和局部偏移 Smooth L1；100 epochs，seed `26029`、batch 8、AdamW lr `0.001`、weight decay `0.0001`。训练用时 47.5 秒，仅代表本机 GPU；峰值 Torch CUDA 分配约 25.3 MB，保留约 46.1 MB，均不包含完整进程/驱动开销。

Pilot02 最佳 epoch 为 63，checkpoint SHA256 `c17bb1e38eb51b63ab1735814f2a4ceea2c7c0055aaa694eded32b23399229c7`。最佳 epoch 由同一组 16 张验证图选择，因此还需要独立测试集。

## 独立对照：模型是否利用图像

固定最佳权重，在 CPU 上重新计算验证输出，未继续训练。误差为关节欧氏距离除以该目标原人物框的对角线；PCK@0.05/0.10 是误差不超过相应比例的关节占比。没有按模型预测分数筛掉失败关节。

平均模板仅使用 64 张训练图：按关节平均经过同样无增强裁剪后的有效坐标。打乱输入使用固定 seed 的无不动点排列，目标验证标注保持原位。空白图分别为全黑和中灰，也与原验证标注比较；它们是输入干预对照，不是空场景关键点准确率。

| 输入/基线 | 平均归一化误差 | PCK@0.05 | PCK@0.10 |
| --- | ---: | ---: | ---: |
| 真实验证图 | 0.1295 | 18.47% | 50.00% |
| 训练集平均姿态模板 | 0.1411 | 15.77% | 43.69% |
| 固定打乱的验证图 | 0.1676 | 7.66% | 28.38% |
| 全黑图 | 0.1713 | 2.70% | 17.12% |
| 中灰图 | 0.2377 | 3.15% | 12.61% |

真实图在 10/16 张上比平均模板有更低平均误差，在 10/16 张上有更高 PCK@0.10。与空白/打乱图的差异支持模型已开始使用图像，但相对平均模板仅提高 **6.31 个百分点**，不能把 50% 当作强识别能力。

| 关节组 | 有效关节数 | 真实图误差 | 模板误差 | 真实图 PCK@0.05 / @0.10 | 模板 PCK@0.05 / @0.10 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 面部：鼻、眼、耳 | 54 | 0.1059 | 0.1216 | 20.37% / 62.96% | 20.37% / 50.00% |
| 上肢：肩、肘、腕 | 83 | 0.1519 | 0.1707 | 13.25% / 42.17% | 10.84% / 38.55% |
| 下肢：髋、膝、踝 | 85 | 0.1226 | 0.1245 | 22.35% / 49.41% | 17.65% / 44.71% |

下肢平均误差仅略好于模板，离健身角度判定仍有明显差距。审计报告、逐图预测、固定排列、冻结的原 manifest 和绿色标注/橙色预测对照图保存在 `.cache/pose-student-pilot-02-audit/`，未复制图像到仓库。

**Pilot02 的 sigmoid 峰值不是置信度。** 真实图的平均峰值约 0.9915，全黑图仍达 0.8584，中灰图为 0.6516。空间 softmax 损失对同一关节所有 logits 加常数不敏感，而 sigmoid 峰值会改变；训练尚无独立可见性/有人监督，不能把峰值直接接到现有 `0.60` 运动门限或奖励。后续训练器增加了高斯热图 BCE 幅值约束（`--amplitude-weight`，设为 0 可复现早期空间损失），以消除任意 logit 常数偏移；这不等于已经校准可见性或定位可靠性。

## Pilot03：扩大数据后的结果

官方 train/val 分别取 2,048/256 张，共 393,918,183 B 图像；manifest SHA256 为 `8fbf1a1dbabb31fc9eaa8a16077d70b3342c18f45fc049c70fe50f42dac17b33`。训练器既检查 image ID，也拒绝不同 ID 下训练/验证图像 SHA256 相同的情况。未使用背景候选。

40 epochs、batch 16、seed `26029`、空间 CE + 5 倍高斯热图 BCE + 局部偏移损失，其他优化器参数同 Pilot02。最佳 epoch 为 38，checkpoint SHA256 `d69c07d64982306b425c707a413dc38f6f09b63d08893dac7f0e757f04f2ca20`。本机 GPU 训练约 469 秒；Torch 峰值张量 50,110,976 B、保留 60,817,408 B，均不代表设备端资源。训练目录保存执行时的三份源码及哈希。

独立重算使用固定的最佳权重和已核验源码快照，256 张验证图共 3,655 个标注关节；不继续训练、不用验证集建立模板。这里的“独立”指独立计算流程，**验证集仍参与最佳 epoch 选择，不是新的测试集**。

| 输入/基线 | PCK@0.10 |
| --- | ---: |
| 真实验证图 | 58.74% |
| 2,048 张训练图平均姿态模板 | 49.58% |
| 固定打乱的验证图 | 35.16% |
| 全黑图 | 26.73% |
| 中灰图 | 34.23% |

真实图的上肢/下肢 PCK@0.10 为 48.63%/56.01%，肘腕只有 39.98%。下肢平均误差 0.11624，反而略差于模板的 0.11511；肘腕平均误差也略差于模板。真实图仅在 149/256 张上有更低平均误差。弯曲与遮挡关节仍明显失败，不能靠整体平均分放行。对照工具见 [`audit_student.py`](../scripts/pose/audit_student.py)，首次结果在 `.cache/pose-student-pilot-03-audit/`。

真实/打乱图的平均 sigmoid 峰值分别约 0.30969/0.30333，全黑/灰约 0.05646/0.01480。新增幅值约束降低了空白图峰值，却没有建立“位置正确”的概率标定。不能直接沿用或降低其他模型的 `0.60` 门槛。

## 转换与完整画面证据

[`student_export.py`](../scripts/pose/student_export.py) 校验权重名称、形状、BN 统计、坐标元数据和有限数值。全整数转换必须显式提供真实图像 NPZ 与来源 JSON，不生成随机校准数据；还会拒绝残留浮点张量、非 int8 公共输入/输出及旧整数产物混入纯浮点导出目录。

Pilot01 的 16 张验证输入中，Torch→Keras 最大绝对差约 `1.19e-6`，Torch→浮点 TFLite 约 `1.91e-6`。浮点文件 185,232 B；32 张真实训练裁剪校准后的全整数文件 88,200 B，输入/两个输出均 int8，所有 77 个 interpreter 张量均无浮点类型，主机 Invoke 成功。该尺寸和哈希只属于 Pilot01，不能代指后续 checkpoint。

随后对 Pilot01 整数模型（SHA256 `f5f9f992766f532b73f3e9b65e82d0dc54fe9bcb5342861c00bca4009014ace0`）做了隔离的源码 TFLM 核验：使用已校验的 `fed64d19fb5af8d28ee108c07838d5c0ae3ddcbb` 与项目 pointwise 内核，复用生产 CMake，但另建只有所需 4 类算子的 resolver，未放宽生产 MoveNet profile。27 个算子为 Conv2D v3×14、Depthwise v3×9、ADD v2×2、ResizeBilinear v3×2。名义卷积 5,140,800 MAC，排除补零乘加后为 5,081,224 MAC。

32 张真实校准图使用模型实际输入 `scale=0.007843137718737125, zero_point=-1` 量化，两个头合计 **940,032 字节与完整 LiteRT 2.3.0 BUILTIN_REF 逐字节一致**。x86_64 主机 arena 在绑定输入/输出后及 32 次 Invoke 后均为 **79,840 B**；解释器 240 B、resolver 296 B 在 arena 外。除最初提供 512 KiB 的测量外，又实际以 **128 KiB 容量**完成全部 32 个输入及逐字节比较。13 个 1×1 卷积均经过安全资格检查并走项目内核，3×3 stem 保留参考实现；计数为 416 次 fast、32 次 fallback。1 KiB arena 干净返回分配失败。主机指针宽度和实际 Mini3 不同，该数字不包含板端 camera、任务栈、模型存储或完整工作器 RAM。报告与独立 oracle 在 `.cache/pose-student-tflm-probe/`；这只证明该权重的整数算术兼容性，不证明识别质量。

Pilot03 的整数模型也单独验证，未沿用 Pilot01 的期望输出：文件 88,200 B，SHA256 `ba631a80b95f5570aac368bbaae5eccc454606993fba563084a7007f24339f59`。同一通用主机程序读取该新权重、以其实际输出量化参数生成新的 LiteRT oracle，32 张真实校准图的 940,032 字节仍全部一致；arena 79,840 B、128 KiB 容量通过、416/32 路由计数及 1 KiB 拒绝也重新核验。Pilot03 logits 的 scale/zero-point 为 `0.06456870585680008 / 111`，offsets 为 `0.013959778472781181 / 6`，与 Pilot01 不同。报告独立保存在 `.cache/pose-student-tflm-probe-03/`，记录复用的已编译通用程序路径/哈希。这是 Pilot03 权重的兼容性结果，不代替其数据集精度或完整画面验收。

Pilot03 在 16 张验证输入上的 Torch→Keras 最大绝对差为 `6.68e-6`，Torch→浮点 TFLite 为 `1.15e-5`。另对同样的 256 张验证裁剪比较浮点/整数模型：PCK@0.10 为 58.74%/58.82%，平均归一化误差 0.11498/0.11509，整体接近。但量化造成的关节位移最大为 6.37 个输出网格，95 分位为 0.87 个网格，不能忽略少量 argmax 跳变。报告在 `.cache/pose-student-pilot-03-quant-audit/report.json`；校准仅用训练图，与这 256 张验证图分离。

完整视频诊断保留 65 帧公开视频代理的 320×240 视野，缩为 96×72 后上下补边到 96×96，不使用真实人物框。Pilot02 接入未改阈值的 C session 诊断桥，结果为 **0 个完整循环**，膝角约 130.8–179.7°，没有下蹲底部。Pilot03 同样为 **0 个完整循环**，65 帧全部被 `low_confidence` 拒绝，最大 sigmoid 峰值为 0.55913；没有为得到计数而调整门槛。

源画面含先前将竖屏视频嵌入横屏时的黑边，既不是板载摄像头数据，也没有逐帧人工关键点真值。时间戳和 100ms 处理延迟为合成值，不能推算 FPS。报告分别在 `.cache/pose-student-pilot-02-exercise-final/report.json` 和 `.cache/pose-student-pilot-03-exercise/report.json`。

## 复现与检查

在仓库根目录运行。准备一个现有的 Windows x64 Python 3.11，首次安装前需要至少 20 GiB 可用磁盘；以下路径变量替换为实际位置。下载、图片、权重、日志全部保留在被 Git 忽略的 `.cache/`。训练需要兼容 CUDA 12.8 的 NVIDIA 驱动；`--device cpu` 可显式改为 CPU。

```powershell
$basePython = 'C:\path\to\python311\python.exe'
pwsh -File scripts/pose/setup-training.ps1 -Python311 $basePython
$trainPython = '.cache/pose-train-venv/Scripts/python.exe'
& $trainPython -B scripts/pose/prepare_pose_dataset.py --output .cache/pose-dataset --train-count 2048 --val-count 256 --background-train-count 0 --background-val-count 0 --max-image-bytes 629145600 --workers 4
& $trainPython -B scripts/pose/train_student.py --manifest .cache/pose-dataset/manifest.json --output-dir .cache/pose-repro-01 --epochs 40 --batch-size 16 --device cuda --heatmap-loss spatial --amplitude-weight 5
& $trainPython -B scripts/pose/audit_student.py --checkpoint .cache/pose-repro-01/best.pt --manifest .cache/pose-dataset/manifest.json --output .cache/pose-repro-01-audit
```

安装器的 `-Offline -SkipSmoke` 可复用已校验 wheel，不重新下载。数据准备器校验 HTTP 范围响应、归档 CRC、官方标注 SHA256、图片完整解码和缓存收据；重试耗费计入预算。流式中止时，实际收到的最后一块仍计入，传输量可能超出预算至多若干在途 worker 块，并非严格网络流量封顶。来源为 [COCO 官方数据](https://cocodataset.org/dataset/download.htm)；逐图来源和许可见本地 manifest，原图不重新分发。

训练输出目录必须为空的新 `.cache` 子目录。固定 seed 不承诺不同硬件/库版本的训练字节完全相同；每次记录实际 checkpoint、manifest、源码哈希和配置。审计使用该次训练保存的已核验源码快照，模板只从训练图生成。

转换工具需要**另一个已有的 TensorFlow 2.15.1 环境**；它不在上述 Torch 环境锁内，也不会由训练安装器自动安装。选择匹配的解释器后：

```powershell
$tfPython = 'C:\path\to\tensorflow215\python.exe'
& $tfPython -B scripts/pose/student_export.py --weights .cache/pose-repro-01/weights.npz --output-dir .cache/pose-repro-01/export --representative .cache/pose-repro-01/calibration.npz --representative-metadata .cache/pose-repro-01/calibration.json --parity-reference .cache/pose-repro-01/parity-reference.npz
& $trainPython -B tests/test_prepare_pose_dataset.py -v
& $trainPython -B tests/pose_student_data_test.py -v
& $trainPython -B tests/test_student_training.py -v
& $trainPython -B tests/test_student_audit.py -v
& $trainPython -B tests/test_student_model.py --write-fixture .cache/pose-student-smoke -v
& $tfPython -B tests/test_student_export.py --fixture .cache/pose-student-smoke --representative .cache/pose-repro-01/calibration.npz --representative-metadata .cache/pose-repro-01/calibration.json -v
```

数据下载预算/并发/缓存的 28 项、数据几何/隔离的 4 项、训练损失的 3 项、模型的 4 项、转换的 6 项和审计来源/对照的 4 项测试已通过，共 49 项。仓库审计工具重放 Pilot03 后，指标、全部逐图预测及诊断图哈希与原审计完全一致。全套训练实验当前在本地主机执行，现有 CI 仍检查游戏和原生代码，不宣称 CI 执行了 GPU 训练。

[`evaluate_student.py`](../scripts/pose/evaluate_student.py) 使用本地已校验的完整视频 RGB565 帧与 C session 桥；这些缓存不是仓库资源，新的克隆不会自动拥有该视频。可用时执行 `--checkpoint PATH --output NEW_CACHE_PATH --contact-sheet`。它不会自动打开摄像头或修改固件。

## 下一步

优先改善弯曲/遮挡的肘腕膝踝定位，检查模型容量与训练覆盖，再用独立测试集比较；同时处理真实完整视野与训练人物框裁剪之间的差异。可见性与定位置信度需要经核验的负样本和独立标定，不能简单降低门槛凑出次数。主机整数兼容性已通过，但 Mini3 实际时延、相机画面和计数验收仍待后续。当前模型未接入固件或游戏奖励。
