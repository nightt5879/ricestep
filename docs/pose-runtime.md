# Mini3 姿态推理构建

这条路径把固定的 MoveNet 四头 CNN、17 点解码、图像预处理和模型资源校验编译为 CPU 原生代码。本文记录最初的 dormant 链接诊断；后续已接入工作任务、运动页面和奖励的 `-PoseRuntime` 候选版见[摄像头运动说明](camera-exercise.md)。两者均未上板验证。

## 输入与依赖

`pose-runtime.lock.json` 固定官方 TensorFlow Lite Micro 提交 `fed64d19fb5af8d28ee108c07838d5c0ae3ddcbb`、FlatBuffers 25.9.23 及两个下载包的 SHA-256。FlatBuffers 使用该 TFLM 提交自带的补丁。源码下载到忽略的 `.cache/`，保留上游许可证；构建过程本身不下载依赖。

```powershell
node scripts/setup-pose-source.cjs
node scripts/setup-pose-source.cjs --check-only
pwsh -File scripts/build-standalone.ps1 -PoseLinkCheck
```

`-PoseLinkCheck` 将当前 Lua/角色语音与推理库链接成单独的诊断镜像，导出到 `dist/native/pose-link-check/`。关闭此选项仍构建普通桌搭镜像，导出到 `dist/native/standalone/`。两者均不访问设备。

推理解析器只注册 SUB、MUL、CONV_2D、DEPTHWISE_CONV_2D、ADD、RESIZE_BILINEAR 六个算子。未启用 Luna/NPU、压缩内核、KissFFT 或在线模型下载。代码按 Mini3 的 `rv32imac_zba_zbb_zbc_zbs / ilp32` 软浮点 ABI 构建；姿态源码最后显式覆盖 SDK 默认的 fast-math，使用 `-fno-fast-math -ffp-contract=off`。

最终 ELF 验证必须找到有定义且非空的推理 API、六个算子注册函数，并检查实际编译命令。只编出静态库或传入链接器 `-u` 不构成链接证明。原来的 Lua/MP3 字节、Flash 位置、4 MiB CP 分区检查继续执行。

## 2026-10-29 静默验收

- 完整 Mini3 SDK 链接成功：CP 为 2,683,196 字节，SHA-256 为 `bcf180789c58849e3d577ef298e300d4fcfd9df1b44a2b58771e79deb1ce74ab`，低于 4 MiB CP 分区。模型权重不在这个 CP 镜像中。
- 实际 ELF 中找到 12 个有定义的姿态入口和全部六个算子注册函数；42 个编译单元的实际命令通过软浮点 ABI 和精确浮点选项检查。Lua 为 58,579 字节，14 个语音片段与已批准文件保持一致。
- RISC-V 反汇编中 `pose_engine_context_bytes()` 返回 400 字节；主机 x86-64 为 760 字节。源码构建主机的 arena 使用量为 1,945,552 字节，不能沿用旧 wheel 探针的 1,945,792 字节当作新构建结果。
- 链接器 PSRAM 账面使用 5,161,968/5,242,880 字节，其中包括预留的堆和 LVGL 区域，不能解释成实际运行峰值。诊断镜像没有分配推理 arena；后续应从已有堆中按需申请并实测余量。
- WSL GCC 13 的同一 CMake 源码目标已运行真实 CNN，灰色、渐变、噪声三组输入的四头和最终 17 点均逐字节对齐 LiteRT 2.3.0 完整模型参考。106 项生命周期、错误输入、内存重叠等检查通过；整个运行时的 ASan/UBSan 构建重复通过，未报告内存或未定义行为问题。原生运行详情见组件 README。

这些是编译和主机执行证据。设备仍运行原来的固件，没有取得板上帧率、实际动作准确率、冷启动或离线播放的新证据。

## 生命周期与内存

`pose_engine` 没有全局推理实例。调用方显式提供互不重叠的上下文、不可变模型和 tensor arena；对象使用 placement construction，推理库只从 arena 分配。准备前须通过 `rp_model_validate` 和真实 SHA-256 实现验证整个模型窗口；引擎另外验证 FlatBuffer 边界、固定模型结构、输入输出量化参数。

调用顺序为 prepare → input → 填入 INT8 RGB → invoke → heads/decode → release。每次 invoke 前必须重新取得 input；失败不能继续使用旧结果。2 MiB 是封装器进入 TFLM 前的保守安全下限，实际 arena 用量由运行时查询，仍需在设备上验证峰值与剩余堆空间。没有把主机内存测量当作板上帧率或内存验收。

## 资源包与诊断镜像的区别

`scripts/prepare-pose-resources.cjs` 可离线生成独立的 5 MiB 资源候选包，包含保留的原厂 tone/fonts/emoji 以及有头部和哈希的模型窗口。资源布局见 [集成设计](native-pose-integration.md)。当前链接诊断镜像仍使用原有资源地址，**不是该候选资源包的配套运行固件**；不能把两个产物拼成可刷套件。

新增 `-PoseRuntime` 已接入显式资源布局、生产 SHA-256、相机租用、工作线程、取消机制、几何连续性及运动页面/奖励；它与本页诊断选项互斥。板上内存、时延和真实动作测量仍待完成。用户要求的安静边界保持有效：当前不刷写、不重启、不采集画面、不播放语音。
