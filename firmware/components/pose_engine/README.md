# 固定 MoveNet 四头 CPU 推理接口

这个模块只封装同步 TFLite Micro 推理及已有的 17 点解码器。模型、对象存储和 tensor arena 都由调用方提供；它不包含相机、任务调度、屏幕、声音、动作奖励或固件刷写。只有显式调用 `pose_engine_prepare()` 才能构造运行时和分配 arena 内部结构。

## 固定模型

使用 `scripts/pose/probe_movenet.py --mode heads` 生成的确切四头子图：

- 大小：2,891,896 字节；SHA-256：`9051a5ceebc1eb5f535c1b1afe58d33ce878bf9358a159cbfc689c1abebf9c68`。
- 原始模型 SHA-256：`cd7cc22fa946e5d146a7b98d496853e1923e22828d3972d579973f27f91bb105`。两者不同；这里不能直接装入原始完整图。
- Schema 3，一个 subgraph，93 个节点。只注册六种 CPU 算子：SUB v2 × 2、MUL v2 × 1、CONV_2D v3 × 50、DEPTHWISE_CONV_2D v3 × 24、ADD v2 × 13、RESIZE_BILINEAR v3 × 3。
- 输入 tensor175：INT8 NHWC `[1,192,192,3]`，scale 1、zero point −128，共 110592 字节；转换方式为 RGB UINT8 减 128。
- 输出顺序固定为 tensor259、276、282、285，shape、scale、zero point 和字节数必须与 [解码器契约](../pose_decode/README.md) 完全一致。

先使用 `rp_model_validate()` 与真实 SHA-256 回调（或等价的完整文件哈希校验）验证资源，再将得到的有界只读 payload 交给引擎。引擎独立执行 FlatBuffer/schema/算子与输入输出契约检查，**不声称自己重新计算了 SHA-256**。校验之后到 release 之前，模型必须保持不可修改；Flash/XIP 场景也不能在引擎使用模型时更新其分区。

## 内存和生命周期

先通过 `pose_engine_context_bytes()` / `pose_engine_context_alignment()` 查询对象存储要求，这两个函数不启动运行时。提供互不重叠的 context、模型、arena；模型和 arena 基址至少 16 字节对齐，context 按查询的要求对齐。调用方负责这些区域的生命周期，release 不释放它们。

1. `prepare`：验证输入和模型，注册固定算子，placement-construct 运行时，调用 `AllocateTensors`。输出句柄参数本身必须与三个区域不重叠；发现重叠会在任何写入之前拒绝。其他失败返回错误且输出句柄为 NULL；context/arena 可重新使用。
2. `input`：取得内部输入 tensor 的可写 span，并使上一次输出失效。调用方写完全部 110592 字节。
3. `invoke`：每次调用前必须重新取得并填写 `input`。同步 CPU 推理，只有成功才允许访问四头或解码。运行时失败后输出失效，使用 release→prepare 恢复。忘记取得新输入时返回 `PE_NOT_READY`，不会运行未初始化或旧帧。
4. `heads`：借用四个只读输出 span，不复制完整热图；或 `decode`：调用已验证的 17 点后处理。二者都不隐式重新推理。
5. `release`：析构 placement 对象，不调用 free/delete 释放调用方内存。之后所有借用指针失效，句柄不可再次使用。

一个实例及其借用 tensor 只能由一个执行上下文访问；没有内部互斥锁、工作线程或异步队列。每次新输入都必须经过 `input`，不能把上一次结果误认为新帧。

此前 x86 TFLM 四头探针的 arena 账目为 1,945,792 字节，传入容量为 2 MiB；**这不是 RV32 构建的占用保证**。固定版 TFLM 的 `MicroAllocator::Create` / `SingleArenaBufferAllocator::Create` 在极小 arena 上有未检查的 placement 构造。本封装在进入 TFLM 前拒绝小于 2 MiB 的容量，返回 `PE_ARENA_TOO_SMALL`。这是一条保守的入口限制，不代表板上分配必然成功；`AllocateTensors` 仍必须成功才返回有效引擎。模型 Flash 大小、context、arena、解码器、相机缓冲和其他任务栈需要分别预算。

## 构建与验证边界

目标使用原生 CPU 参考内核，RV32 ILP32 soft-float，不注册 Luna/NPU 自定义算子，不使用 fast-math。TFLM 固定在 `fed64d19fb5af8d28ee108c07838d5c0ae3ddcbb`；依赖与构建目标由上层构建脚本固定。本模块不会自行下载或配置工具链。

构建目标统一位于 `firmware/pose_runtime/CMakeLists.txt`：`ricestep_tflm` 为固定 CPU 内核，`ricestep_pose` 包含此封装及其他便携组件。本目录不另行注册重复库。

主机完整模型回归的独立入口为 `tests/native/pose_engine/CMakeLists.txt`，可在 Linux/WSL、仓库根目录运行（需已有 CMake 与 C++17 编译器）：

```sh
cmake -S tests/native/pose_engine -B .cache/pose-engine-host \
  "-DRICESTEP_TFLM_ROOT=$PWD/.cache/tflm-source" \
  "-DRICESTEP_FLATBUFFERS_ROOT=$PWD/.cache/tflm-flatbuffers" \
  -DCMAKE_BUILD_TYPE=Release
cmake --build .cache/pose-engine-host --parallel 3
python3 scripts/pose/test_pose_engine.py \
  --executable .cache/pose-engine-host/pose_engine_test \
  --model .cache/pose-probe/movenet-heads-host-probe.tflite \
  --oracle-dir .cache/pose-oracle/results \
  --python-path .cache/research-pose/tflm-host
```

测试驱动先检查模型及每个 oracle NPZ 的 SHA-256，再复现 oracle 的灰色、渐变与噪声 RGB 输入。C++ 测试执行真实 CNN，逐字节比较四个头及 17 点的 51 个 float 值，还覆盖未取得输入、旧结果失效、release 后重建、错误模型 profile、损坏 FlatBuffer、对齐、重叠输出指针及过小 arena。测试生成的模型、二进制 fixture 和编译产物均只放在忽略的 `.cache` 中。

2026-10-29 的源码构建验证：WSL x86-64 GCC 13.3 / CMake 3.29.0、上述固定 TFLM 与 FlatBuffers 25.9.23，106 项检查通过；灰色、渐变、噪声三个完整 CNN 输入均与 LiteRT 2.3.0 `BUILTIN_REF` 的四头及最终 17 点逐字节一致。此源码版本实测 context 为 760 字节，arena 账目为 **1,945,552 字节 / 2,097,152 字节容量**；它与此前 Python wheel 的 1,945,792 字节属于不同构建，不能混为同一次测量。

同一 CMake 入口也以 AddressSanitizer + UndefinedBehaviorSanitizer（`-fsanitize=address,undefined -fno-sanitize-recover=all -fno-omit-frame-pointer`，同时设置 C/C++/可执行链接 flags）重新构建了完整运行时，并通过相同 106 项检查及三个整网样本；无 sanitizer 报错。它覆盖生命周期和真实 CPU 推理内存访问，不替代硬件上的 PSRAM/cache/栈验证。

这些结果只证明主机 CPU 的静态/完整推理验证。没有连接开发板，没有测 Mini3 推理速度、摄像头帧率或真实人体精度。交叉编译结果以对应构建日志为准；后续接入工作线程与摄像头必须另行完成，不能把这个同步接口当成已经运行的设备功能。
