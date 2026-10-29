# Mini3 姿态候选的静态速度预算

2026-10-29：本轮新增项目自己的整数 1×1 卷积内核，复用一次输入加载计算 4 个或 2 个输出通道。静态模型分区与实际目标 ELF 必须一起审核，不能继续把旧版每 MAC 12 条指令套到全部普通卷积。最终 ELF 已按四条实际路径审核：**仅普通卷积直接循环的条件下限为 2.2004 秒/帧**，仍超过 400 ms 门槛约 5.50 倍；旧版 5.0507 秒和 18.940 秒保留为历史证据。所有数值均是明确假设下的条件下限，不是板端测速、完整推理耗时预测或人体准确率。本轮没有访问开发板、摄像头或音频。

因此，目前的候选可用于后续静态图数值、取帧与资源验证，不能描述为已经可用的实时健身计数器。门槛继续保持 400 ms；不能靠延长门槛或给旧关键点换新时间戳来发米饭。

## 可复现输入与命令

先阅读 [已有模型探针](../scripts/pose/README.md)。原来的 `probe_movenet.py` 已计算名义卷积 MAC；新脚本补充精确 padding 剔除、实际 ELF 内循环和条件速度下限，不重复做模型推理。

```powershell
python scripts/pose/audit_pose_budget.py --output .cache/pose-native/performance-budget.json
```

默认读取以下已有文件，不下载、不安装全局依赖、不改源码、不执行目标 ELF：

- `.cache/pose-probe/movenet-heads-host-probe.tflite`，2,891,896 B，SHA-256 `9051a5ceebc1eb5f535c1b1afe58d33ce878bf9358a159cbfc689c1abebf9c68`。
- `.cache/research-pose/parser` 中的隔离 `tflite==2.18.0` schema 解析器，沿用早期模型检查环境；模型 SHA 不匹配直接拒绝。
- `dist/native/pose-runtime/arcs-mini3-ricestep.elf`；最新哈希由本次运行生成的 `.cache/pose-native/performance-budget.json` 记录，历史哈希仅适用于对应历史段落。
- `.cache/native-mini3-sdk/build-mini3-standalone/compile_commands.json`、SDK 源码及本地 RISC-V `nm/objdump/readelf`。

参数可显式指定路径；`--help`列出全部选项。JSON保存逐层shape/stride/dilation/padding、MAC、实际循环指令、ELF/源码哈希和编译命令。重编译后必须重跑，不能把本报告的地址套给新ELF。

脚本保留 `os-debug-checked-45`、`o3-release-inlined-12`，并增加 `o3-pointwise-dot4-dot2-tail-reference12`。新路径逐层重算 shape、stride、padding、quantization、bias 与全 int8 输入范围的安全界限；检查目标 ELF 内 Dot4、Dot2 和 Invoke 的精确操作序列与符号数据流，包括共享输入、独立累加器、指针增量和回跳。还检查原生 Invoke 的实际调用、Eligible 完整控制流的地址无关指纹、已审核源码哈希（统一 LF/CRLF）及有效 O3/NDEBUG/ISA/ABI/严格浮点配置。未知指令、源码或编译配置会报错。这个检查不能替代板端实际执行或证明硬件性能。

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

## 当前候选：Dot4 / Dot2 / 标量尾部 + 3×3 回退

`firmware/components/pose_pointwise` 保持模型、算术次序、重定量和参考 Init/Prepare 不变，不增加 tensor arena 或额外权重。只在严格 eligibility 检查通过后使用紧凑的 1×1 循环；其他情况仍走固定 TFLM 参考内核。每个 Dot4 输入通道完成 4 个标量 MAC，Dot2 完成 2 个，没有使用 SIMD、DSP 或 NPU。

固定模型的 49 个 1×1 节点全部满足形状、类型、stride/dilation、零 padding、零权重偏移与安全整数界限。脚本对每个 bias 和 per-channel shift 重算保守界限，覆盖任意 int8 输入；这一结论仍以原生 Prepare 成功、有效 tensor 指针及已审核编译配置为前提。主机统计 3 次推理有 147 次 fast / 3 次 fallback 是额外证据，不能单独证明 RV32 分派。

| 路径 | MAC / 帧 | 最终 ELF 直接指令 / 迭代 | MAC / 迭代 |
| --- | ---: | ---: | ---: |
| Dot4 | 243,302,400 | 20 | 4 |
| Dot2 | 884,736 | 12 | 2 |
| 单通道尾部 | 442,368 | 8 | 1 |
| 首层普通 3×3 参考内核 | 7,907,424 | 12 | 1 |

最终 ELF SHA-256 为 `e9d376d5734a268e9ea23b2a43a326aa6926bf7b735635a6e65a7c9621b01a05`。已审核实际循环：Dot4 `0x3071271c..0x30712754`，Dot2 `0x307126cc..0x307126ec`，Invoke 内标量尾部 `0x30712d76..0x30712d8a`。三条路径都只有有符号 int8 加载、标量乘法、寄存器累加和指针/回跳，没有内循环栈读写。原生 Invoke 确实调用两个 Dot 内核及 Eligible，并保留参考回退；746 B 的 Eligible 代码逐项保留类型、尺寸、参数、整数安全界限和成功/失败分支，其归一化控制流指纹纳入脚本拒绝变更检查。不是仅因链接到了函数或主机 fast 计数而认定板端路径。

逐路直接指令为：`1,216,512,000 + 5,308,416 + 3,538,944 + 94,889,088 = 1,320,248,448`。沿用单工作核、最高 300 MHz、最多双发射、零等待且无卸载假设，得到 `1,320,248,448 / 600,000,000 = 2.20041408 秒`；单发射则为 4.4008 秒。仍未计外层控制、eligibility、DotN 调用/返回、bias 与重定量、16,988,432 个 depthwise MAC、其他算子、采集预处理与调度。

原生 Dot4/Dot2/尾部的操作序列和数据流匹配检查通过，逐条改为其他指令、输入偏移破坏均被拒绝；旧 12/45 指令匹配与篡改拒绝也通过。旧 5.0507 秒 JSON 保存于 `.cache/pose-budget-audit/release12-budget.json`。这些静态数字不能充当板端 FPS、实测加速比或交付运动计数的证据。

## 历史发布构建：12指令循环

`firmware/pose_runtime/CMakeLists.txt` 对 `ricestep_tflm` 和 `ricestep_pose` 使用 **PRIVATE NDEBUG**。共享内联头文件在推理库内部保持一致，该宏不传播到SDK或 `app-miniapp`。仅 `conv.cc` 和 `depthwise_conv.cc` 在通用Os选项后追加O3；其他文件保持Os。最终 `-fno-fast-math -ffp-contract=off` 仍覆盖SDK全局fast-math。

资源SHA验证、模型profile、对齐、参数、生命周期、arena和范围检查均为显式检查，仍然保留；去掉的是上游反复执行的内部调试断言，不允许未经验证的任意模型进入运行时。

缓存实验比较了Os/debug、Os/NDEBUG、O3/debug、仅热文件O3/NDEBUG，以及一致PRIVATE NDEBUG的发布方案。每种源码主机构建均通过现有106项模型/生命周期检查，灰色、渐变、噪声输入的四头与17点逐字节匹配完整LiteRT参考；同一公开真人照片的RGB565输入也保持四头与51个float32值逐字节一致。记录位于 `.cache/pose-opt-audit/`。这些是数值回归证据，不是人体精度或板端测速。

这组历史发布 ELF 的 SHA-256 为 `48afe5e5f0834bfccf008dd8c60f29c83d2c1f4a5e984642613f753cc5684741`，其 `ConvEval` 位于 `0x3070a700`，大小5,618 B；有符号int8卷积已内联，两条过滤器路径的循环为：

- `0x3070b6fa..0x3070b71e`；
- `0x3070b922..0x3070b946`。

两条循环均为12条直接指令：两次 `lb`、栈保存/读取、输入偏移相加、两次指针递增、一个标量 `mul`、累加和回跳。循环内没有 `Offset` 调用，仍有标量加载和栈流量，没有SIMD/NPU加速。

普通卷积直接指令数为 `252,536,928 × 12 = 3,030,443,136`。采用下文相同的单工作核、最高300 MHz、最多双发射、零等待、无超频或自定义卸载假设：

`3,030,443,136 / (300,000,000 × 2) = 5.05073856秒`。

这仍是400 ms的约12.63倍，未计外层循环、depthwise、量化、其他算子、捕获、预处理、解码和任何等待。若实际核为单发射，同一计算为10.1015秒。不能将倒数写为实测FPS，也不能将旧18.94/新5.05当成实测加速倍数。

## 历史证据：旧版45指令循环

以下地址与18.940秒计算只属于更早的历史ELF，SHA-256 `96373becdd9f0b0e550d66c989aa3593ac49e395a4a272eb1c02c0826baa1e52`，不适用于新增 pointwise 内核。旧JSON、45条指令列表和编译命令保存在 `.cache/pose-budget-audit/historical-budget.json` 与 `historical-compile-commands.json`；兼容检查仍接受该指令记录。

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

后续O3/debug对象实验虽消除了调用，却保留内联边界检查，循环仍有55条指令；随后采用上文一致PRIVATE NDEBUG的12指令方案。因此只数函数调用不足以评估开销。重新分块、复用加载数据、并行累加或使用经确认的扩展指令会再次改变下限，必须重新审计。

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

预算仍很严格：即使采用新 Dot4 循环的理想 5 条指令/MAC，并独占双发射 300 MHz 的全部 400 ms，最多也只容纳约 **4,800 万个这样的 MAC**；单发射约 2,400 万。这不是可用整网预算，实际还要扣除 Dot2/尾部、普通 3×3、depthwise、重定量、调用和采集显示。历史 12 指令版本的对应普通卷积上限为 2,000 万，45 指令版本约 533 万。不能把不同内核的 MAC 混成同一个速度系数。

“偶尔慢速姿态初始化 + 每帧关节跟踪”值得独立研究，但**不推荐直接用本版大模型作在线纠偏器来交付计数**：

- 初次结果到达时，对应的图像已经远早于 400 ms。要关联到现在，必须从那个原始帧开始维护真实图像特征轨迹或历史，不能把旧坐标的时间戳改成现在。
- 跟踪器需要自己的前后向一致性、遮挡/出画/换人判定、关节可信度及周期性几何校验。不能永久继承旧 CNN confidence；背景或衣物纹理的位移不自动等于解剖关节的位置。
- 当前卷积工作量仍很大，初始化/纠偏间隔可能过长；跟踪器又与CNN共用CPU/内存带宽。补上线程不能消除总运算量。
- 若以后真测得一个更小的检测器能频繁纠偏，可再评估混合方案。只有新鲜的真实图像观测通过完整跟踪质量检查，才能送入动作状态机；丢失后仍重置半次动作，仍保留 400 ms 门槛。

允许实体操作的下一轮，最先做**不接相机的静态张量/内核板端周期测试**：确认实际 core clock/ID，分别记录 int8 pointwise、depthwise、整网与内存行为；采用静音、无自动计数/奖励的诊断路径。对当前镜像而言，这一步是测清真实瓶颈和验证工具链，不是期待它突然达到实时。之后把候选小网络放入同样的数值 oracle 与时间预算，再接连续摄像头和人工计数对照。
