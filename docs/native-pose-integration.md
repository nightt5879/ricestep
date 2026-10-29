# Mini3 原生姿态模型：存储与 CPU 接入评估

2026-10-29 初始静态审查及主机离线打包记录。后续已实现显式运行候选 overlay 和配对校验，见[摄像头运动说明](camera-exercise.md)；下文按调研时点保留容量和接入依据。未刷写、访问摄像头或播放声音，不是已部署配置。

## 结论

现有独立版 CP 为 **2,587,152 B**，四头 MoveNet 为 **2,891,896 B**，合计 **5,479,048 B**，已经超过 CP 的 4 MiB，且尚未加入 TFLM 代码。因此不能把模型直接追加到当前 CP ELF。保留 boot、AP、CP、OTA staging、KV 边界的可行容量方案，是重排原有 **`0x100000..0x600000` 资源区**：删除独立版不运行的唤醒模型，保留原提示音、完整字体包和系统表情，将模型放入连续 Flash 窗口。

CPU 移植需要另外引入固定版本的 TFLM 源码。当前已编译的 SDK 镜像中没有 `modules/tflite-micro` 或 `labs/tflite_micro`，不能直接打开一个配置就得到推理。按 Mini3 的 LS2663/8 MiB 保守配置走 CPU 参考 kernel，不调用 Luna；当前只证明主机 TFLM 的内存/算子与数值链路，尚无板端速度证据。

## 当前 Flash 和读取证据

源码固定为第三方公开镜像 [c0a95bcf](https://github.com/Shinku-Chen/listenai_arcs_mini/tree/c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35)，未独立确认官方上游身份。以其中 [`res/arcs-mini3/partition_table.json`](https://github.com/Shinku-Chen/listenai_arcs_mini/blob/c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35/res/arcs-mini3/partition_table.json) 和 [`apps/arcs-mini/memap.h`](https://github.com/Shinku-Chen/listenai_arcs_mini/blob/c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35/apps/arcs-mini/memap.h) 为依据；地址为 Flash offset，CPU 映射基址为 `0x30000000`。

| 当前资源 | 范围（末端不含） | 容量 | 本地原厂 3.1.0 payload |
| --- | --- | ---: | ---: |
| boot / boot_control / AP | `0x000000..0x100000` | 1 MiB 合计 | 保持现状 |
| tone | `0x100000..0x200000` | 1 MiB | 351,232 B |
| wake_word | `0x200000..0x380000` | 1.5 MiB | 1,295,360 B |
| emoji | `0x380000..0x440000` | 768 KiB | 492,544 B |
| respak（字体） | `0x440000..0x600000` | 1,792 KiB | 983,040 B |
| CP app | `0x600000..0xA00000` | 4 MiB | 独立版 2,587,152 B |
| OTA staging | `0xA00000..0xF00000` | 5 MiB | 保留，不作为模型空间 |
| KV | `0xF00000..0x1000000` | 1 MiB | 保留 |

单独 wake_word 不足；wake_word + emoji 也只有 2,359,296 B，仍比模型少 532,600 B。CP 剩余 1,607,152 B 不能与另一区间直接拼成 TFLM 的连续 FlatBuffer 指针。把分散模型整份复制进 PSRAM 又会增加约 2.9 MB，不适合现有 CP 的 5 MiB 总额。

Flash 资源可直接只读访问，有现成路径：

- [`src/system/platform.c`](https://github.com/Shinku-Chen/listenai_arcs_mini/blob/c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35/src/system/platform.c) 的 `TONE_BIN_ADDR`、`WAKE_WORD_BIN_ADDR`、`EMOJI_BIN_ADDR` 均为 `CMN_FLASH_REGION + offset`；`romfs_init()` 从这一地址读取。
- [`src/middleware/romfs/romfs.c`](https://github.com/Shinku-Chen/listenai_arcs_mini/blob/c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35/src/middleware/romfs/romfs.c) 中节点位置为 `base_addr + relative_offset`，`romfs_info_get()` 返回原资源中的指针，不复制整个文件。初始化校验内部长度与 checksum。ROMFS 整包平移不需要重写其内部节点地址。
- [`apps-ui/assets/fonts/font_romfs.c`](https://github.com/Shinku-Chen/listenai_arcs_mini/blob/c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35/apps-ui/assets/fonts/font_romfs.c) 固定 respak 地址；`font_cbin.c` 用 `bin + offset` 解析字形。已编译的字体为 Chinese 16 和 Rubik Bold 64，路径分别为 `/font/lv_font_chinese_16.bin`、`/font/lv_font_rubik_bold_64.bin`；旧 Chinese 18 源文件未列入 CMake。

本次逐字节 SHA-256 比较确认镜像中的 tone、emoji、respak 与保存的原厂 3.1.0 包一致，可保留完整字节，无需裁字库或删除系统表情：

| 资源 | SHA-256 |
| --- | --- |
| tone | `382bc1ef35abbefed4da06d12e65d8aaaca688666e1e4996d32654534f96e67e` |
| emoji | `af4d0d9dbbd0ba7d33a001966160542fd645c529b1b7f795a68c2de7c36f7aa7` |
| respak | `31b05d15a242b8eb87a24ace43141c06e4ae6a54eff7ab17c3b9c01721b7c816` |

## 可测试的资源布局候选

所有边界按 64 KiB 对齐，不移动 CP 入口，也不借用 OTA/KV。

| 资源 | 新 offset 范围 | 容量 | 内容 |
| --- | --- | ---: | --- |
| tone | `0x100000..0x180000` | 512 KiB | 原 tone 字节，仍在原起点 |
| pose | `0x180000..0x480000` | 3 MiB | 前 4 KiB 有界描述头，后接原样四头模型 |
| respak | `0x480000..0x580000` | 1 MiB | 原字体 ROMFS 字节 |
| emoji | `0x580000..0x600000` | 512 KiB | 原 emoji ROMFS 字节 |

模型 payload 地址拟为 **`0x30181000`**，有 16 字节对齐；3 MiB 窗口扣去 4 KiB 头和模型后剩余 **249,736 B**。描述头应记录布局版本、长度、模型 SHA-256、派生模型/解码契约版本；运行时先验证范围、哈希和 TFLite schema，再把 Flash 指针传给 `tflite::GetModel()`。权重保持只读 Flash，arena 单独分配在 PSRAM。

实现必须同时改动、核验的入口：

1. 新的显式资源 profile/清单和打包校验器；不要悄悄覆盖原厂 partition table。独立构建的 CP 与资源布局版本需匹配。
2. `platform.c` 的 tone 容量、emoji 地址/容量；`font_romfs.c` 的 respak 地址/容量。保留当前 `app_wakeup_init(NULL)` 及不启动算法的分支，不再读取旧 wake_word 地址。
3. [`src/middleware/ota/ota_flash.c`](https://github.com/Shinku-Chen/listenai_arcs_mini/blob/c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35/src/middleware/ota/ota_flash.c) 仍写死原资源位置。此 profile 必须保持 `CONFIG_OTA=n` 且排除所有资源更新路径；未来恢复 OTA 时必须先实现 profile-aware 更新，不能只改 JSON。
4. 使用保存的原厂包制定恢复清单，恢复旧 CP **和**旧资源位置。新布局不能直接回刷旧 CP 后假定页面/字体仍正常；不能把镜像自产完整 LPK 当作原厂 3.1.0 恢复包。

已增加 [离线打包脚本](../scripts/prepare-pose-resources.cjs)、[版本化 profile](../firmware/resource-profiles/mini3-pose-v1.json) 和[只读 C 校验组件](../firmware/components/pose_resources/README.md)。输出位于忽略目录 `dist/native/pose-resources/`；Node 测试用真实 SHA-256 验证布局范围、完整保留资源、模型、头格式与损坏拒绝，C 测试验证有界解析及注入哈希回调的行为。C 组件尚未连接 Flash 或生产哈希库，不能据此声称板端 Flash 已校验。

仍需：在两个不同基址的主机缓冲中用真实 ROMFS 解析代码找到字体/表情并核对返回字节；集成真实 SHA-256 provider；最终 ELF 不含第二份模型副本且权重不进入 PSRAM 启动复制表。这不是可直接执行的烧写清单。

## CPU TFLM 编译入口

现有 SDK 的 `arcs-sdk/labs` 只有 lnn、zig；没有 TFLM 子模块。最新[厂商 TFLM Hello World](https://docs2.listenai.com/arcs-sdk/latest/zh/html/labs/tflite_micro/hello_world/README.html)依赖 `modules/tflite-micro` 和 Luna 注册函数，目标为带 NPU 的 LS2684/CSK7004；它的 CPU0/EVB 工程不能直接作为 Mini3 CP 镜像。

本仓库已补充独立源码准备入口；使用 Node、Python 3 和 Git，缺少缓存时才用 curl 下载锁定的 HTTPS 归档：

```powershell
node scripts/setup-pose-source.cjs
node scripts/setup-pose-source.cjs --check-only
node --test tests/setup-pose-source.test.cjs
```

`pose-runtime.lock.json` 固定两个官方归档的 SHA-256：TFLM commit 源码及 FlatBuffers 25.9.23。安装位置分别为 `.cache/tflm-source`、`.cache/tflm-flatbuffers`，没有下载 KissFFT 等当前 CPU 图不需要的依赖。两份归档校验后先在私有临时目录解包，再从仓库根目录用显式 `git apply --directory=<暂存目录>` 应用 TFLM 内的五文件 `flatbuffers.patch`，并确认五个文件确实改变，避免 Git 在嵌套目录中静默跳过补丁。

已有源码必须与重新解包并打补丁后的完整树匹配，任何修改、缺失或额外文件都会报错，不覆盖用户源码。当前核对 TFLM 1805 个文件、FlatBuffers 1881 个文件及其目录和 9 个内部相对链接；清单保存在 `.cache/pose-source-inventory.json`。`--check-only` 不联网、不更新清单或已安装源码，仍会在临时目录重建期望树并核对每个文件，结束后清理自己的暂存目录。归档路径穿越、大小写别名、绝对/逃逸/循环链接均拒绝；官方合法内部链接按目标系统路径格式保留。

可复用与主机参考实验同源的 [TensorFlow TFLM `fed64d19`](https://github.com/tensorflow/tflite-micro/tree/fed64d19fb5af8d28ee108c07838d5c0ae3ddcbb)，经 `gh api` 只读核实：

- [`tools/make/Makefile`](https://github.com/tensorflow/tflite-micro/blob/fed64d19fb5af8d28ee108c07838d5c0ae3ddcbb/tensorflow/lite/micro/tools/make/Makefile) 提供 `microlite` / `libtflm` 静态库目标、`list_library_sources`、`list_library_headers`、`list_third_party_sources`、`list_third_party_headers`，使用 C++17，禁 RTTI/异常/线程安全静态初始化。第三方依赖需固定校验，不能假定当前 wheel 等于完整可交叉编译源码。
- [`targets/riscv32_generic_makefile.inc`](https://github.com/tensorflow/tflite-micro/blob/fed64d19fb5af8d28ee108c07838d5c0ae3ddcbb/tensorflow/lite/micro/tools/make/targets/riscv32_generic_makefile.inc) 是可用的通用 RISC-V 起点，默认 `rv32imc`/`ilp32`、`riscv64-unknown-elf-`；实际 Mini3 CP 构建为 `rv32imac_zba_zbb_zbc_zbs`/`ilp32`、`-mtune=nuclei-300-series`，属于无 F 扩展的 soft-float ABI。后续应继承现有 SDK 工具链/ABI 构建一个独立 C++ library target，而不是使用默认下载工具链或独立 EVB 链接脚本。SDK 全局还打开了 `-ffast-math`；姿态/TFLM target 必须在其后显式覆盖 `-fno-fast-math -ffp-contract=off`，保留解码器所需的 IEEE float32/舍入行为，不能仅保留 ISA/ABI 就宣称数值兼容。
- 使用 [`MicroMutableOpResolver`](https://github.com/tensorflow/tflite-micro/blob/fed64d19fb5af8d28ee108c07838d5c0ae3ddcbb/tensorflow/lite/micro/micro_mutable_op_resolver.h) 及普通 CPU `kernels/conv.cc`、`depthwise_conv.cc`、`resize_bilinear.cc` 等；按实际四头 FlatBuffer 的算子集合生成 resolver。禁止 `tflite::luna::*`、Luna workspace 和权重预打包。原完整模型含 CAST 等已知不兼容路径，不能误装原始全图。
- [`system_setup.cc`](https://github.com/tensorflow/tflite-micro/blob/fed64d19fb5af8d28ee108c07838d5c0ae3ddcbb/tensorflow/lite/micro/system_setup.cc) 的参考 `InitializeTarget()` 为空；[`micro_time.cc`](https://github.com/tensorflow/tflite-micro/blob/fed64d19fb5af8d28ee108c07838d5c0ae3ddcbb/tensorflow/lite/micro/micro_time.cc) 默认计时函数返回 0。移植应接 SDK 日志和真实 cycle/tick 计时，否则性能报告没有意义。

接入顺序：先把官方 int8 hello_world 用普通 CPU resolver 链进现有 CP，验证 ABI/链接尺寸；再用 Flash 模型及固定图片执行 `AllocateTensors()`、`Invoke()`；最后对齐四头输出与仓库解码器。推理 worker 需与 Lua/LVGL 分开，受取消标志和看门狗约束；本次没有在板上新增推理任务或执行推理。

构建还存在一个必须覆盖的默认值：SDK 对 CP 源码加入 `-ffast-math`，而精确解码需要 IEEE float32 及固定舍入。姿态/TFLM 的独立库应显式使用 `-fno-fast-math -ffp-contract=off`。本次 `scripts/check-pose-target.ps1` 已用实际 Mini3 的软浮点 ISA/ABI 将预处理、解码和动作逻辑编译为 ELF32 RISC-V 对象；这不等于链接成功或板上执行。

当前 CP PSRAM 为 `0x28300000..0x28800000`，配置 heap `0x370000`；这些不是全部空闲内存。主机四头 arena 上限 2,097,152 B、实际账目 1,945,792 B，仅作为起始预算，目标 RV32 必须重新测量，另计摄像头 DMA、输入/输出、解码、UI、音频和任务栈。约 270.5M conv MAC/次也不能换算成已知 FPS。尚未发现有源码、权重及精度证据且明显更小的可直接替换模型；本次不以未验证的模型名称代替容量问题。

相关依据：[板端姿态可行性](board-pose-feasibility.md)、[主机 TFLM 探针](../scripts/pose/README.md)、[独立固件验证边界](standalone-device.md)。
