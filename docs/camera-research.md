# Mini3 板载摄像头取帧调查

核实日期：2026-10-29。目标是使用 Mini3 自带 GC0328，给肥鱼运动互动提供真实图像。本调查只读取文档、公开代码和本地恢复包；没有打开摄像头、操作设备、刷写固件或验证实际帧率。

后续本地构建准备：官方工具包下载阻塞已解决，固定源码的 Mini3 基线和肥鱼独立版均已编译成功。独立版 overlay 补入固定 Lua 核心并启用小应用；公开镜像仍不能视为原厂 3.1.0 的完整等价源码。按用户要求尚未刷机、启用摄像头或验证实际画面。详见 [原生构建记录](native-build.md)与[姿态模型集成边界](native-pose-integration.md)。

## 结论与证据等级

- **官方文档已证实**：GC0328 通过 DVP 接入；原生 SDK 有 `lisa_camera_capture()`，存在连续取帧、串口输出和视觉模型示例。
- **公开源码中已读到 Mini3 实现**：`service_camera_init()` / `service_camera_capture()` 支持 Mini3 的 I2C1、扩展 MCU 的 PWDN 和硬件降采样。代码来自个人公开仓库，不能称为已核验的官方镜像，也不能等同当前 3.1.0 二进制源码。
- **尚未证实**：现用 3.1.0 的 Lua 小应用直接取帧、ADB 的现成取帧命令、原厂固件的 UVC 输出、板上全身姿态模型、连续识别速度和稳定性。
- 首选工程路线：在 **Mini3 专用固件**中复用现有 camera service，先得到带时间戳的连续真实帧，再接动作判定。现有肥鱼 Lua 应用接收计数结果；此桥接仍须开发。

## 官方入口

| 资料 | 地址 | 用途 |
| --- | --- | --- |
| Mini3 硬件 | <https://docs2.listenai.com/x/1KVekpl1e> | GC0328、DVP、I2C1、`arcs_mini3` 板型 |
| Mini 固件开发仓库说明 | <https://docs2.listenai.com/x/yffNVbsmK> | 官方指向 `https://cloud.listenai.com/CSKG836746/arcs-sdk/public/arcs_mini` |
| Camera 驱动 | <https://docs2.listenai.com/arcs-sdk/latest/zh/html/drivers/lisa_camera/README.html> | C API、缓冲区和启停约束 |
| Camera 取帧示例 | <https://docs2.listenai.com/arcs-sdk/latest/zh/html/samples/drivers/devices/lisa_camera/README.html> | 连续取帧后通过 UART DMA 输出 |
| 人脸演示 | <https://docs2.listenai.com/arcs-sdk/latest/zh/html/demos/arcs/face_detect/README.html> | GC0328 → LCD/IPC → AP 模型示例 |
| LS26 型号表 | <https://docs2.listenai.com/arcs-sdk/latest/zh/html/hardware/soc/ls26xx.html> | 区分 LS266x 无 NPU 与 LS268x 有 NPU |
| Mini3 原厂固件 | <https://docs2.listenai.com/x/AL2O8BGTO> | 恢复固件入口 |
| 串口恢复教程 | <https://docs2.listenai.com/x/uaiwdDObz> | ROM 下载模式和 `.lpk` 烧录 |

官方 SDK 源码链接为 `https://cloud.listenai.com/CSKG836746/arcs-sdk/public/arcs-sdk`。本次访问遇到 TLS 错误，Schannel 访问还出现要求登录，未取得可信官方 commit；没有关闭 TLS 验证或绕过访问控制。

## 原生 SDK 的最小取帧链

使用 `lisa_device_get("camera")` 获取设备，确认 ready 后按顺序调用：

```text
lisa_camera_setup()
→ 设置格式、裁剪、镜像（在 start 之前）
→ lisa_camera_attach_bus()
→ lisa_camera_start()
→ lisa_camera_capture(dev, &fb)
→ 消费/复制 fb->buf；记录 width/height/format/len/timestamp
→ lisa_camera_release_fb(dev, fb)
→ 持续取帧，结束时 lisa_camera_stop()
```

驱动配置项包括 `CONFIG_LISA_CAMERA_DEVICE`、`CONFIG_LISA_CAMERA_BUS_DVP`、`CONFIG_LISA_CAMERA_SENSOR_GC0328`。准确 Kconfig 和结构体字段以锁定的源码版本为准：示例 README 中仍有旧字段，不能把不同版本的片段直接拼接。

帧缓冲归驱动管理；释放后不能继续访问。帧完成回调在中断上下文中执行，推理应交给工作任务。像素格式列表描述的是驱动框架能力，不能据此认为 GC0328 原生输出 JPEG 或任意分辨率。

## 已读取的 Mini3 源码

本次通过 `gh api` 读取 [Shinku-Chen/listenai_arcs_mini](https://github.com/Shinku-Chen/listenai_arcs_mini)，固定提交：

`c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35`

提交说明自称同步 upstream `23fca91e`，日期 2026-10-19；这只是仓库作者的声明，未与官方仓库独立比对。板级 C 文件标注 LISTENAI / Apache-2.0，内嵌 SDK 有 Apache-2.0 LICENSE；顶层 README 按各模块说明许可证，不能把全部资源统称为同一种许可。缓存文件位于 `.cache/research-camera/shinku_*`，未执行该仓库脚本。

关键文件（均基于上述固定提交）：

- [`apps/arcs-mini/services/service_camera.c`](https://github.com/Shinku-Chen/listenai_arcs_mini/blob/c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35/apps/arcs-mini/services/service_camera.c)：现有取帧服务。
- [`service_camera.h`](https://github.com/Shinku-Chen/listenai_arcs_mini/blob/c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35/apps/arcs-mini/services/service_camera.h)：`init/capture/get_framesize/stop/suspend/resume/is_inited`。
- [`boards/arcs_mini3/board.h`](https://github.com/Shinku-Chen/listenai_arcs_mini/blob/c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35/arcs-sdk/boards/arcs_mini3/board.h)：扩展 MCU 与 PWDN。
- [`boards/arcs_mini3/pinmux.h`](https://github.com/Shinku-Chen/listenai_arcs_mini/blob/c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35/arcs-sdk/boards/arcs_mini3/pinmux.h)：摄像头和屏幕引脚。
- [`boards/arcs_mini3/Kconfig`](https://github.com/Shinku-Chen/listenai_arcs_mini/blob/c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35/arcs-sdk/boards/arcs_mini3/Kconfig) 与 [`apps/arcs-mini/arcs_mini3.conf`](https://github.com/Shinku-Chen/listenai_arcs_mini/blob/c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35/apps/arcs-mini/arcs_mini3.conf)：SoC 与内存布局。

### 实现中与 Mini2 不同的关键参数

| 项目 | Mini3 实现 |
| --- | --- |
| 控制总线 | `i2c1`，PB00/PB01；与 CH32V003 和 USB-C 控制器共享 |
| 图像总线 | `dvp0`，DMA 通道 4 |
| HSYNC / VSYNC / PCLK | PA10 / PA11 / PA12 |
| D0–D7 | PA13–PA20 |
| XCLK | PA26，18 MHz |
| PWDN | CH32V003 的 `exgpiod` pin 6；高有效；初始化先拉低 100 ms |
| 缓冲 | 2 个，DVP DMA 乒乓缓冲 |
| 输出 | 320×240 RGB565，每帧 153,600 字节 |
| 8 MiB SoC 路线 | GC0328 `lisa_camera_set_subsample(..., 2, 2)`，减少帧缓存 |
| 安装方向 | Mini3 不做 Mini2 的 90° 旋转；镜像/翻转可受 KV 覆盖 |

`service_camera_capture(buffer, buffer_len)` 会启动或复用 stream，丢掉设定数量的旧帧，再复制当前图像；用调用方缓冲区接收，不能直接把缓冲区塞进受限 Lua 堆。启停/休眠由 service 统一管理，后续需要保证拍照、预览和运动采集不会竞争摄像头。

这份 Mini3 Kconfig 选择 `SOC_LS2663`，CP 使用 5 MiB PSRAM，整个 SoC 为 8 MiB 路线。官方硬件页同时出现 LS2684、LS2663、LS2662 三种矛盾文字；必须结合实物或固件构建信息确认。**在确认前按无 NPU 的资源预算设计，不能直接承诺移植 NPU 姿态模型。**

另查到两份公开源码：`qfhuang/arcs_mini` 的默认 HEAD `c8547fb160f3f3e3d71d2200c911c2e8c6ea56a8` 无 Mini3 板型且 camera 用 I2C0，不适合作为 Mini3 基线；`justa-cai/listenai-arcs_mini` 的 `66568d04375a933bf814b921c8f4442e8918f101` 有 Mini3，但属于自建服务端配套改版，应单独审查改动。

## USB、串口、ADB 与视觉示例的边界

- 官方 `samples/drivers/devices/lisa_camera` 已展示 UART1 3 Mbps DMA 发送帧。它需要原生固件和匹配板型/引脚，并非当前 USB 线上的即插即用摄像头。按 8N1 理论带宽计算，320×240 RGB565 的原始帧上限不足 2 fps，尚未扣除协议开销，适合第一帧调试而非动作识别主链。
- 官方 [USB Host Video 示例](https://docs2.listenai.com/arcs-sdk/v0.1.4/zh/html/samples/subsys/usb/host/cherryusb_video/README.html) 引用 `samples/subsys/usb/device/uvc`，并说明 EVB 可作为 YUY2 640×480、15 fps、Bulk UVC 设备。此处只能证明 SDK 存在 Device 路线；不能据此认定 Mini3 原厂固件已提供 UVC。直接访问 Device README 未成功，也未在当前 Mini3 公开树核到该示例目录。
- ADB 是目前应用部署/调试通道。未找到官方 `adb shell camera ...` 或固定图像文件路径；不能凭 Android ADB 的习惯假设可 `screencap` 或读取 `/dev/video0`。
- 官方 `demos/arcs/face_detect` 在 ARCS EVB 上用 GC0328 连续采集、LCD 预览和 AP IPC 模型。其模型是人脸相关，不是全身关节点，也不是俯卧撑计数模型。该 demo 的 AP/CP 分区和 Mini3 原厂分区不同，整包不可直接套用。

## 恢复包静态核验

已读取工作区现有 `../../work/lisem/firmware/arcs-mini3-v3.1.0-0-g0d199e4b.lpk`（本调查没有重新下载）。

- 大小：4,965,689 字节。
- SHA-256：`6092464A094DEF4BAB3DAE01CE40C7F44DCA1EB72A91E7DE75E80292A6EFE07E`。
- ZIP 内 `manifest.json` 的 7 个镜像 MD5 全部匹配。此校验证明本地包内部一致，不能单独证明下载来源或实际恢复成功。

| 分区 | 地址 | 文件大小 |
| --- | --- | ---: |
| boot | `0x000000` | 222,604 |
| ap | `0x040000` | 438,380 |
| tone | `0x100000` | 351,232 |
| wake_word | `0x200000` | 1,295,360 |
| emoji | `0x380000` | 492,544 |
| respak | `0x440000` | 983,040 |
| app | `0x600000` | 3,346,892 |

恢复优先保留原厂 boot：ADB 正常时使用对应 Mini3 包的官方恢复流程；若原生实验导致 ADB 无法启动，官方串口教程提供 ROM 下载方式，需拆机接串口、按照 Mini3 图示连线，再用 cskburn desktop 选择 LS26 并载入完整 `.lpk`。自动进入要求串口板具备 DTR/RTS；手动方式涉及 BOOT/TX 复用脚与复位，不能按 Mini2 图片接线。本文仅记录恢复路径，不是已执行恢复。

## 下一步验收顺序

1. 锁定含 Mini3 的源码、构建脚本和依赖；先复现未改动构建，核对 Mini3 board、SoC、AP/CP 内存和分区表。
2. 只增加受控的一帧采集入口：先观察尺寸/格式/长度/计数，再导出一帧核实颜色、方向、曝光和真实画面。
3. 连续采集计时，测真实 FPS、丢帧、堆余量、CPU 负载与音频/UI 共存；不要用示例宣传帧率代替实测。
4. 保留原生 frame service 所有权，设计工作队列和暂停恢复，再决定在板上运行轻量算法或进一步模型移植。
5. 将可靠动作事件与奖励序号交给肥鱼；没有真实帧或置信度不足时不发米饭。
