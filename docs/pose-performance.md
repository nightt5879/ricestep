# Mini3 姿态候选的静态速度预算

2026-10-29：当前发布构建已优化两个卷积热文件，并统一关闭推理库内部调试断言。最终 ELF 的普通卷积循环从旧版45条直接指令变为12条；对下述时钟与发射宽度假设，**仅普通卷积部分的条件下限仍为5.0507秒/帧**，超过400 ms门槛约12.63倍。这不是板端测速或完整推理耗时预测；实际延迟、摄像头帧率与准确率仍未测量。本轮没有访问开发板、摄像头或音频。旧版18.940秒保留在历史证据一节，不再用于描述当前构建。

因此，目前的候选可用于后续静态图数值、取帧与资源验证，不能描述为已经可用的实时健身计数器。门槛继续保持 400 ms；不能靠延长门槛或给旧关键点换新时间戳来发米饭。

## 可复现输入与命令

先阅读 [已有模型探针](../scripts/pose/README.md)。原来的 `probe_movenet.py` 已计算名义卷积 MAC；新脚本补充精确 padding 剔除、实际 ELF 内循环和条件速度下限，不重复做模型推理。

```powershell
python scripts/pose/audit_pose_budget.py --output .cache/pose-native/performance-budget.json
```

默认读取以下已有文件，不下载、不安装全局依赖、不改源码、不执行目标 ELF：

- `.cache/pose-probe/movenet-heads-host-probe.tflite`，2,891,896 B，SHA-256 `9051a5ceebc1eb5f535c1b1afe58d33ce878bf9358a159cbfc689c1abebf9c68`。
- `.cache/research-pose/parser` 中的隔离 `tflite==2.18.0` schema 解析器，沿用早期模型检查环境；模型 SHA 不匹配直接拒绝。
- `dist/native/pose-runtime/arcs-mini3-ricestep.elf`；本次发布版 SHA-256 `48afe5e5f0834bfccf008dd8c60f29c83d2c1f4a5e984642613f753cc5684741`。
- `.cache/native-mini3-sdk/build-mini3-standalone/compile_commands.json`、SDK 源码及本地 RISC-V `nm/objdump/readelf`。

参数可显式指定路径；`--help`列出全部选项。JSON保存逐层shape/stride/dilation/padding、MAC、实际循环指令、ELF/源码哈希和编译命令。重编译后必须重跑，不能把本报告的地址套给新ELF。

脚本只接受历史 `os-debug-checked-45` 和当前 `o3-release-inlined-12` 两个已审查配置。发布版必须在唯一的 `ConvEval` 中找到两条完整12指令回边，操作顺序、寄存器与栈保存数据流、指针递增和累加均匹配；同时检查有效的O3/NDEBUG、ISA/ABI和严格浮点选项。旧版要求准确的45条操作序列及两次 `Offset` 调用。未知循环或编译配置会报错，不能自动套用次数。

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

## 当前发布构建：12指令循环

`firmware/pose_runtime/CMakeLists.txt` 对 `ricestep_tflm` 和 `ricestep_pose` 使用 **PRIVATE NDEBUG**。共享内联头文件在推理库内部保持一致，该宏不传播到SDK或 `app-miniapp`。仅 `conv.cc` 和 `depthwise_conv.cc` 在通用Os选项后追加O3；其他文件保持Os。最终 `-fno-fast-math -ffp-contract=off` 仍覆盖SDK全局fast-math。

资源SHA验证、模型profile、对齐、参数、生命周期、arena和范围检查均为显式检查，仍然保留；去掉的是上游反复执行的内部调试断言，不允许未经验证的任意模型进入运行时。

缓存实验比较了Os/debug、Os/NDEBUG、O3/debug、仅热文件O3/NDEBUG，以及一致PRIVATE NDEBUG的发布方案。每种源码主机构建均通过现有106项模型/生命周期检查，灰色、渐变、噪声输入的四头与17点逐字节匹配完整LiteRT参考；同一公开真人照片的RGB565输入也保持四头与51个float32值逐字节一致。记录位于 `.cache/pose-opt-audit/`。这些是数值回归证据，不是人体精度或板端测速。

最终发布ELF的 `ConvEval` 位于 `0x3070a700`，大小5,618 B；有符号int8卷积已内联，两条过滤器路径的循环为：

- `0x3070b6fa..0x3070b71e`；
- `0x3070b922..0x3070b946`。

两条循环均为12条直接指令：两次 `lb`、栈保存/读取、输入偏移相加、两次指针递增、一个标量 `mul`、累加和回跳。循环内没有 `Offset` 调用，仍有标量加载和栈流量，没有SIMD/NPU加速。

普通卷积直接指令数为 `252,536,928 × 12 = 3,030,443,136`。采用下文相同的单工作核、最高300 MHz、最多双发射、零等待、无超频或自定义卸载假设：

`3,030,443,136 / (300,000,000 × 2) = 5.05073856秒`。

这仍是400 ms的约12.63倍，未计外层循环、depthwise、量化、其他算子、捕获、预处理、解码和任何等待。若实际核为单发射，同一计算为10.1015秒。不能将倒数写为实测FPS，也不能将旧18.94/新5.05当成实测加速倍数。

## 历史证据：旧版45指令循环

以下地址与18.940秒计算只属于历史ELF，SHA-256 `96373becdd9f0b0e550d66c989aa3593ac49e395a4a272eb1c02c0826baa1e52`，不适用于当前发布版。旧JSON、45条指令列表和编译命令保存在 `.cache/pose-budget-audit/historical-budget.json` 与 `historical-compile-commands.json`；兼容检查仍接受该指令记录。

旧版编译命令为：`-mtune=nuclei-300-series -march=rv32imac_zba_zbb_zbc_zbs -mabi=ilp32 -Os -fno-fast-math -ffp-contract=off`。没有 `-DNDEBUG`；旧版 `Offset()` 检查没有被内联消除。ISA为RV32标量整数扩展，无F/D/V/P；不能把SDK随附DSP或Luna头文件当作使用了向量/NPU内核的证据。

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

后续O3/debug对象实验虽消除了调用，却保留内联边界检查，循环仍有55条指令；最终采用上文一致PRIVATE NDEBUG的12指令方案。因此只数函数调用不足以评估开销。重新分块、复用加载数据、并行累加或使用经确认的扩展指令会再次改变下限，必须重新审计。

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

预算仍很严格：在当前12指令/MAC循环下，即使独占双发射300 MHz的全部400 ms预算，普通卷积也最多约 **2,000万MAC**；单发射约1,000万。真实预算还要给捕获、显示、解码和其他算子留余量。历史45指令版本的对应预算仅约533万MAC。不能预先把数十/数百MMAC当作已可承受。

“偶尔慢速姿态初始化 + 每帧关节跟踪”值得独立研究，但**不推荐直接用本版大模型作在线纠偏器来交付计数**：

- 初次结果到达时，对应的图像已经远早于 400 ms。要关联到现在，必须从那个原始帧开始维护真实图像特征轨迹或历史，不能把旧坐标的时间戳改成现在。
- 跟踪器需要自己的前后向一致性、遮挡/出画/换人判定、关节可信度及周期性几何校验。不能永久继承旧 CNN confidence；背景或衣物纹理的位移不自动等于解剖关节的位置。
- 当前发布版条件下限仍约5.05秒，初始化/纠偏间隔可能过长；跟踪器又与CNN共用CPU/内存带宽。补上线程不能消除总运算量。
- 若以后真测得一个更小的检测器能频繁纠偏，可再评估混合方案。只有新鲜的真实图像观测通过完整跟踪质量检查，才能送入动作状态机；丢失后仍重置半次动作，仍保留 400 ms 门槛。

允许实体操作的下一轮，最先做**不接相机的静态张量/内核板端周期测试**：确认实际 core clock/ID，分别记录 int8 pointwise、depthwise、整网与内存行为；采用静音、无自动计数/奖励的诊断路径。对当前镜像而言，这一步是测清真实瓶颈和验证工具链，不是期待它突然达到实时。之后把候选小网络放入同样的数值 oracle 与时间预算，再接连续摄像头和人工计数对照。
