# Mini3 姿态候选的静态速度预算

2026-10-29：当前 MoveNet + CPU 参考内核固件不能合理期待通过 400 ms 门槛。对下面明确列出的时钟与发射宽度假设，**仅普通卷积内循环的条件下限已经是 18.940 秒/帧**。这不是板端测速，也不是完整推理耗时预测；实际延迟、摄像头帧率与准确率仍未测量。本轮没有访问开发板、摄像头或音频。

因此，目前的候选可用于后续静态图数值、取帧与资源验证，不能描述为已经可用的实时健身计数器。门槛继续保持 400 ms；不能靠延长门槛或给旧关键点换新时间戳来发米饭。

## 可复现输入与命令

先阅读 [已有模型探针](../scripts/pose/README.md)。原来的 `probe_movenet.py` 已计算名义卷积 MAC；新脚本补充精确 padding 剔除、实际 ELF 内循环和条件速度下限，不重复做模型推理。

```powershell
python scripts/pose/audit_pose_budget.py --output .cache/pose-native/performance-budget.json
```

默认读取以下已有文件，不下载、不安装全局依赖、不改源码、不执行目标 ELF：

- `.cache/pose-probe/movenet-heads-host-probe.tflite`，2,891,896 B，SHA-256 `9051a5ceebc1eb5f535c1b1afe58d33ce878bf9358a159cbfc689c1abebf9c68`。
- `.cache/research-pose/parser` 中的隔离 `tflite==2.18.0` schema 解析器，沿用早期模型检查环境；模型 SHA 不匹配直接拒绝。
- `dist/native/pose-runtime/arcs-mini3-ricestep.elf`；本次 SHA-256 `96373becdd9f0b0e550d66c989aa3593ac49e395a4a272eb1c02c0826baa1e52`。
- `.cache/native-mini3-sdk/build-mini3-standalone/compile_commands.json`、SDK 源码及本地 RISC-V `nm/objdump/readelf`。

参数可显式指定路径；`--help` 列出全部选项。JSON 保存逐层 shape/stride/dilation/padding、MAC、45 条循环指令、ELF/源码 hash 和实际编译命令。重编译后必须重跑，不能把本报告的地址套给新 ELF。若编译器改变内循环结构，脚本报错并要求重新审计，不沿用 45 这个数字。

## 运算量来自实际模型

普通卷积名义量为 `N × Hout × Wout × Cout × Kh × Kw × Cin/group`；depthwise 每个输出通道只乘自己的 kernel。参考内核在乘法之前跳过 padding，因此脚本按每个输出位置、stride、dilation、前侧 padding，分别枚举实际在图像内的 kernel 行与列后相乘。

| 四头子图项目 | MAC / 帧 |
| --- | ---: |
| 名义卷积总量，74 层 | 270,549,504 |
| 剔除 padding 后总量 | 269,525,360 |
| 50 层普通卷积 | 252,536,928 |
| 24 层 depthwise 卷积 | 16,988,432 |
| 其中 1×1 普通卷积 | 244,629,504 |

1×1 卷积占实际卷积 MAC 的约 90.8%。这里不是因为 padding 高估才得到大数；即使完全优化掉边界判断，绝大部分乘加仍然存在。现有内核也不会跳过数值为零的权重或输入。

仅作尺度检查：即使假设一整个 MAC 每周期完成、300 MHz、其他工作免费，269,525,360 / 300,000,000 仍为 **898.4 ms**。这是假设性的理想实现，不能把它写成实际内核的耗时下限而不说明吞吐假设。

## 实际固件与更强的条件下限

编译命令确认：`-mtune=nuclei-300-series -march=rv32imac_zba_zbb_zbc_zbs -mabi=ilp32 -Os -fno-fast-math -ffp-contract=off`。没有 `-DNDEBUG`；本次 `Offset()` 检查没有被内联消除。最终 ELF 的 ISA attributes 为 RV32 I/M/A/C/B 等，无 F/D/V/P；不能把 SDK 随附 DSP 或 Luna 头文件当作本次使用了向量/NPU 内核的证据。

TFLM 实际注册的 `conv.cc` 对 int8 输入调用 `reference_integer_ops::ConvPerChannel()`。最终 ELF 中该函数在 `0x3070ac9a`，大小 `0x2e8`。每次有效的 input-channel 循环为 `0x3070aebe..0x3070af52`：

- 两次 `lb` 读取输入和权重，单个标量 `mul`，以及累加到 `s8` 的标量 `add`。
- 一次 channel 增量和回跳；还有指针计算、寄存器保存/恢复及两次 `Offset()` 调用。
- **循环自身 45 条指令/有效普通卷积 MAC**。两次被调函数内部的指令完全没有计入，外层循环、量化、depthwise 和其他算子也都不计入。

因此，本次二进制至少执行 `252,536,928 × 45 = 11,364,161,760` 条上述直接指令。芯来 [N300 厂商产品表](https://www.nucleisys.com/product/300.php) 给出 N305/N307/N308 单发射，N310/N320 双发射；本审计故意采用更宽松的 **最多 2 条指令/周期**，没有猜测 LS2663 的具体 IP 子型号。

在以下假设全部成立时，可得保守的条件下限：

1. 单个 pose worker 在一个 N300 系列核执行当前 ELF，没有未声明的多核分拆、自定义 MAC 协处理器或超频。
2. 核心频率不超过 SDK/芯片资料的 300 MHz，发射宽度不超过 2。
3. 指令、Flash 权重和 PSRAM 访问全都零等待，乘除法/分支零额外罚时，其他任务和中断不占时间。

`11,364,161,760 / (300,000,000 × 2) = 18.9402696 秒`。

这仅计算普通卷积的直接循环指令，已是 400 ms 的约 47.35 倍。若实际核为单发射，相同计算变为 37.88 秒；本结论不依赖确认它是单发射。被排除的工作只能增加时间。不能将这些数的倒数当成实测 FPS。

编译优化能显著消除这里的 `Offset()` 调用和栈操作，值得用于静态 benchmark；但不能因而承诺这个 2.70 亿 MAC 网络会达到 400 ms。对**当前 ELF**，即使只计算每 MAC 的两次 load、一次 scalar multiply 和一次 add，在双发射/300 MHz 下，普通卷积也需要约 1.684 秒，且这还没包括 depthwise 或任何其他工作。重新分块、复用加载数据、并行累加或使用经确认的扩展指令，会改变这项指令下限，必须对新内核重新审计，不能套用当前数字。

## 时钟证据与未知量

SDK 快照基于 `c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35`。下列路径均相对于 `.cache/native-mini3-sdk/`：

| 证据 | 含义 |
| --- | --- |
| `arcs-sdk/docs/zh/hardware/soc/ls26xx.rst:54` | LS2663L0U 表项：双核 300 MHz、8 MB PSRAM、无 NPU。 |
| `arcs-sdk/soc/arcs/hal/chip/arcs/include/clock_config.h:45` | SYSPLL core 默认 300 MHz，line 47 的该路径上限也是 300 MHz。 |
| `apps/arcs-mini/services/service_power_policy.c:53` | 活跃档请求 300 MHz；line 54 休眠档 150 MHz。 |
| `arcs-sdk/soc/arcs/hal/chip/arcs/driver/cmu/ClockManager.c:179` | Core 300 MHz 档来自 1.2 GHz SYSPLL / 4；其他列出的档位更低。 |
| `arcs-sdk/soc/arcs/common/startup/system.c:113` | `SystemCoreClockUpdate()` 读取 CRM 当前配置；变量初始化的 24 MHz 不等同于活跃频率。 |
| `arcs-sdk/soc/arcs/common/startup/soc.c:88` 与候选 `.config:396` | `CONFIG_CLOCK_INIT` 关闭，CP 不在此重配 BootClock；启动时继承的实际寄存器值需要后续板测。 |
| 候选 `.config:145,400,838` | `SOC_LS2663=y`、无 `SOC_HAS_NPU`、无 RISC-V FPU。 |

没有读过这块用户设备的实时 `SystemCoreClock`、CPU ID、cycle counter、cache miss、Flash/PSRAM 带宽。这限制我们声称精确耗时，不会使当前二进制在上述已明确的正常规格内突然获得所需吞吐。

## 下一步架构选择

**优先选择明显更小、重新训练/导出的任务姿态模型，并同时优化整数内核。** 保留当前 MoveNet 作为静态参考与数值对齐工具。先确定 CPU 运算与 SRAM/PSRAM 预算，再选择只包含所需身体关节的网络，验证俯卧撑/深蹲低点、遮挡与站位变化的实际精度。仅把输入改为 96×96 不等于得到可用模型，也不能绕过重新导出、校准、精度和解码契约验证。

预算的尺度很严格：在当前 45 指令/MAC 的循环下，即便全给普通卷积、双发射且 300 MHz，400 ms 也最多容纳约 **533 万 MAC**；单发射约 267 万。真实预算还要让给捕获、显示、解码和其他算子。改成高效内核后可以重新计算，但不能预先把数十/数百 MMAC 当作已可承受。

“偶尔慢速姿态初始化 + 每帧关节跟踪”值得独立研究，但**不推荐直接用本版大模型作在线纠偏器来交付计数**：

- 初次结果到达时，对应的图像已经远早于 400 ms。要关联到现在，必须从那个原始帧开始维护真实图像特征轨迹或历史，不能把旧坐标的时间戳改成现在。
- 跟踪器需要自己的前后向一致性、遮挡/出画/换人判定、关节可信度及周期性几何校验。不能永久继承旧 CNN confidence；背景或衣物纹理的位移不自动等于解剖关节的位置。
- 本版条件下限约 19 秒，意味着初始化/纠偏间隔可能过长；跟踪器又与 CNN 共用 CPU/内存带宽。补上线程并不能消除同一硬件上的总运算量。
- 若以后真测得一个更小的检测器能频繁纠偏，可再评估混合方案。只有新鲜的真实图像观测通过完整跟踪质量检查，才能送入动作状态机；丢失后仍重置半次动作，仍保留 400 ms 门槛。

允许实体操作的下一轮，最先做**不接相机的静态张量/内核板端周期测试**：确认实际 core clock/ID，分别记录 int8 pointwise、depthwise、整网与内存行为；采用静音、无自动计数/奖励的诊断路径。对当前镜像而言，这一步是测清真实瓶颈和验证工具链，不是期待它突然达到实时。之后把候选小网络放入同样的数值 oracle 与时间预算，再接连续摄像头和人工计数对照。
