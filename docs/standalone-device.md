# Mini3 独立运行版

目标是断开电脑、不配置 Wi-Fi，给 Mini3 供电即可进入肥鱼饭搭子；煮饭、喂饭、番茄钟、收藏、上次做饭记忆和已选角色语音均在板内运行。本目录提供可重复应用的原生固件 overlay。它不是原厂 Lua API 5 的隐藏功能，也不是把调试上传误当作安装。

## 当前实现与验证边界

- 保留现有 `app/game.lua`、`character.lua`、`voice.lua`、`main.lua`，构建时生成常驻 Flash 的 Lua 源码数组。
- 使用公开镜像中已有的小应用运行时，补入官方 Lua 5.4.8 核心源码；无需移植整个游戏到 C，也不需要缺失的 LVGL 示例工程。
- 运行时依旧报告 API 4、128 矩形，角色自动走已有的简化绘制分支。没有声称这是原厂 3.1.0/API 5 的完整源码。
- 独立版新增有界 `audio.play/stop/get_state`，只接受 `builtin://ricestep/<白名单 key>.mp3`。没有 HTTP、任意路径或任意内存地址入口，也没有云端 TTS 回退。
- 14 个 MP3 共 **375408 字节**，保持仓库内的原始字节；其中 feed/burnt 两句的试听得到用户认可，其他句子使用同一音色参数。
- 独立存档使用 `rice.pet.a/b` 两个 EasyFlash KV key，版本、递增序号和 CRC 校验；不依赖系统时间，不修改云小应用的 TTL 策略。
- 两个新 KV key 均不存在时，尝试只读导入明确匹配已知布局的 `local:ricestep` 存档；任何新记录、损坏记录或清除标记存在都会阻止导入。旧 KV 不改写、不删除；不猜测其他原厂 API 5 存储布局。
- 独立版提供 `clock.monotonic()`，番茄钟优先使用系统实际经过的时间；断网、未校时和屏幕绘制耗时都不再依赖固定回调间隔估算。

目标固件已经成功编译，但独立设备功能仍须实际断网重启验证。按用户“今晚不演示、不发声”的要求，目前停在代码、编译和静态检查；没有刷写或播放。

准备脚本当前管理固定 SDK 的 14 个补丁路径，包含可选摄像头运行配置；默认桌搭配置不启动相机。生成 C 数组的 UTF-8 逐字节回读测试通过；完整 resident.lua 在锁定的官方 API 4 headless simulator 启动并产生画面。后者没有本地音频 C 适配，不能据此声称声音已在硬件验证。单调时钟 UI 测试也在同一运行时通过，覆盖真实耗时优先于逻辑 tick、未校时，以及无效单调时钟值回退。摄像头候选固件的独立资源布局、构建方式及最新验证范围见 [camera-exercise.md](camera-exercise.md)。

存档实现还通过 GCC、Clang 各 385 项原生主机断言，覆盖单槽损坏回退、两阶段 clear 中断、重启、tick/序号回绕、1024 字节上限，以及有界旧存档导入。这是实际 `rice_storage.c` 加模拟 KV/时钟的验证，不是硬件 Flash 断电测试。

2026-10-29 已成功构建 stock 基线（CP 3112324 字节）和包含旧存档导入、单调计时、手动休息提醒的独立版（2593360 字节，4 MiB 分区剩余 1600944 字节）。ELF 验证器核实全部 14 个 MP3 和 58579 字节 Lua 的实际字节；资产段 VMA/LMA 均为 `0x30611354`、433988 字节，没有进入启动复制表。每次修改后必须重新构建验证，完整尺寸、地址、哈希以 `dist/native/standalone/build-info.json` 和 `image-verification.json` 为准。本工具链没有生成 `.map`，验证直接读取实际 ELF 与 bin；结果不包含音频解码峰值内存或真机启动验证。

## 输入锁定与来源

| 输入 | 固定值 |
| --- | --- |
| Mini3 原生源码 | [Shinku-Chen/listenai_arcs_mini](https://github.com/Shinku-Chen/listenai_arcs_mini/tree/c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35)，commit `c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35` |
| 来源限制 | 第三方公开镜像；上游身份尚未独立验证。不能称为已核实的官方镜像。 |
| Lua 核心 | [Lua 5.4.8 官方归档](https://www.lua.org/ftp/lua-5.4.8.tar.gz)，SHA-256 `4f18ddae154e793e46eeab727c59ef1c0c0c2b744e7b94219710d76f530629ae`；[官方校验和列表](https://www.lua.org/ftp/) |
| 原厂恢复包 | `arcs-mini3-v3.1.0-0-g0d199e4b.lpk`，SHA-256 `6092464a094def4bab3dae01ce40c7f44dca1eb72a91e7de75e80292a6efe07e`；恢复包及其分区核对见 `camera-research.md` |

Lua 5.4.8 是为恢复缺失依赖选择并固定的官方版本；尚无证据表明它与原厂 3.1.0 内部使用的 Lua 补丁版本完全相同。准备脚本检查版本并记录每个源文件的哈希；调用者先验证上表归档 SHA-256，再解压。标准 Lua 许可证文本保留在 `lua.h` 中。

## 构建链

原生 Windows 工具链和 stock 基线命令见 `native-build.md`。先完成 stock 基线构建，再在独立缓存 checkout 上应用 overlay，避免混淆 SDK 环境错误与产品改动。

```powershell
# 仓库根目录；依赖须已下载并通过固定校验和验证。
pwsh -File scripts/setup-native.ps1 -CheckOnly
node scripts/prepare-standalone.cjs --sdk .cache/native-mini3-sdk --lua-source .cache/lua-5.4.8/src

# 上一条只检查；wrapper 显式应用 overlay，然后编译、验证和导出。
pwsh -File scripts/build-standalone.ps1
```

`prepare-standalone.cjs` 不下载、不操作板卡、不刷写、不运行外部安装器。它要求固定 Git HEAD；写入前核对全部目标文件，拒绝覆盖未知改动。生成文件和每个补丁文件的哈希记在 `apps/arcs-mini/standalone/overlay-manifest.json`。可以幂等重跑来更新游戏/语音资产；手改其生成文件后必须先处理差异，不能静默覆盖。

Windows wrapper 自动建立或复用适合的短路径 SUBST 映射，避免长路径及 CMake 缓存路径不一致；默认构建目录为 `build-standalone-<路径哈希>`，可用 `-BuildName build-自定义名称` 指定。只有需要手工安排路径时，才同时传入 `-SdkRoot`、`-ToolsRoot`、`-LuaSource` 三个短路径。wrapper 恢复自己创建的映射和进程环境，不移除调用者已有的映射。它读取 SDK 的无扩展名 ELF `arcs-mini`，验证后导出为 `.elf`。

产物位置：

- SDK `apps/arcs-mini/standalone/`：原生适配、内嵌 Lua、精确 MP3 数组、清单。
- SDK `third_party/ricestep-lua/`：固定的 Lua C 核心文件。
- SDK `build-standalone-<路径哈希>/` 或指定 `-BuildName`：独立构建目录；继续保留 stock 构建供比较。
- 仓库 `dist/native/standalone/`：CP bin、ELF、配置、overlay 清单、构建信息和实际镜像验证报告。

SDK 默认还会生成 combined.hex、完整 LPK 和 OTA 包。这些包含镜像自己的 AP/唤醒资源，版本字段为 3.0.3：本次 AP 为 427868 字节、wake_word 为 1432576 字节，与保存的原厂 3.1.0 包不同。不能把这些完整包当作原厂恢复包或正式 3.1.0 升级包。未来真机步骤须单独核对与现有 AP/boot 的兼容性及 CP 写入范围；当前仅保留产物，没有发起任何刷写。

## 开机与离线路径

`src/system/platform.c` 仍初始化 KV、音频平台、USB、屏幕所需平台组件；独立模式跳过 Wi-Fi/BLE 连接初始化及网络管理器启动。`voice_platform_evt_init` 不订阅云连接/OTA 消息。应用 `main` 不订阅云错误页提示、不自动启动摄像头；屏幕和 `miniapp_init` 就绪后同步安装 Flash 中的固定 Lua bundle。

独立模式跳过唤醒算法运行、关闭 OTA 和网络/唤醒提示音策略。编译时仍保留 ACOMP/ACOMP_WAKEUP，因为应用有无条件引用它们头文件和函数的代码；不靠移除依赖或伪造头文件实现离线。启动改用现有 `app_wakeup_init(NULL)` 路径：先初始化共享音频设备、ADC/DAC，再因没有模型资源返回，不创建唤醒引擎或开始向模型送音频。保留 Mini3 原生 PA、扬声器音量、按键和电源保护。此配置保证应用运行不依赖网络；并未审计预编译 AP 固件的每一种射频状态，不能把它称为射频彻底关闭的验证结果。

原有按键服务在小应用运行时已将每次短按抬起转换成一次 click，Lua 继续处理单击/双击，不改已确认的交互。开机安装失败只有限重试三次；成功后不自动重新打开，保留“长按退出小应用，再次长按关机”的原生路径。重新开机才再次进入肥鱼；运行中异常退出也不会自动循环创建 VM。

### 只读启动审查结果

未发现新的确定性启动阻断。已有设置 `sys.usb.msc=true` 会使 `platform.c` 一直停在 USB 文件传输循环，任何应用都不会启动；本次对原厂设备只读查询得到 key missing，按现有代码默认进入正常模式。独立版没有改写这一用户设置；未来切换到 MSC 后须重新核对。

顺序为 KV/USB → `app_wakeup_init(NULL)` 的音频设备初始化 → `voice_player_platform_init()` 创建通道和恢复音量 → main 屏幕/电源服务 → `miniapp_init()` → 内置 Lua 安装。Wi-Fi 自动连接、网络管理器、云连接事件入口和网络提示音策略均不启动。

退出后，电源服务可能调用未初始化的 logger/Wi-Fi 节能函数：已核实 logger 对 NULL handle 返回错误，Wi-Fi 对未 ready/started 状态直接返回，未见空指针路径。小应用激活发送的旧云会话退出消息也有 cloud-init guard，会产生可解释的日志告警但不会发网络请求。长按退出后的再次长按仍能进入原生关机处理；USB 下关机是否有效还取决于原有 boot 的 POWER_GUARD 支持，行为与该底层代码一致。

## 本地音频

`rice_audio.c` 只有一个 worker 能调用 `app_player_play/stop/get_state`，Lua 回调只向长度为 1 的队列投递或覆盖命令。`audio.play` 的 true 表示接受请求；状态从 loading 到 playing，再到 idle；解码失败为一次可观察的 failed。停止、脚本替换及退出会递增 generation，使过期命令失效。

worker 将白名单 key 查到的静态数组转换成 SDK 原生 `mem://addr=<十进制地址>size=<长度>`。这个格式没有 `&`。播放前先取消等待焦点的 URL；IDLE/STOPPED 不再调用会拒绝无效状态的同步 stop。准备/播放超时后停止，数据在固件 ROM 内整个生命周期有效。

独立模式禁用旧 miniapp 蜂鸣器 worker，避免它与角色语音争用 `miniapp_player`；单独扩展 player 创建/音量/焦点的条件，使关闭蜂鸣器时仍有角色声音。系统提示音/低电量等仍拥有其原来的高优先级通道。

`rice_assets.ld` 放在 SDK 的 ROM linker slot（通用 `.rodata.*` 收集之前），将 `.rodata.ricestep_voice` 和 `.rodata.ricestep_app` 明确放入 `>ROM AT>ROM`，没有注册 SCATLOAD。`verify-native-image.cjs` 直接核对 ELF 的资产 section、load segment、VMA/LMA、启动复制表与 bin，逐个比对 MP3 和 Lua 字节，拒绝越过 4 MiB CP 分区；无需不存在的 `.map`。375408 字节不包括解码缓存，不能当作全部运行内存。

## 离线计时

`clock.now()` 保持原有 Unix epoch 语义，未校时仍返回 nil。独立绑定另加 `clock.monotonic()`：仅由 Lua 任务调用，以 64 位累加器累计 FreeRTOS tick 的无符号差值，转换为秒并加正偏移；持续运行期间可跨越底层 tick 计数器回绕。游戏优先使用有限正数的单调时间，遇到无效值才退回原有校准时钟，最后才使用逻辑 tick。原有暂停、异常时间跳变保护及奖励去重仍生效。

## 存档

原运行时有两层时钟依赖：Lua `storage.load/save` 的校时检查，以及 `miniapp_storage.c` 的有效期检查。overlay 仅对固定应用 ID `ricestep` 跳过这两处的墙上时钟依赖，路由到两个专属 KV key；其他应用仍使用原逻辑。

两个 slot 交替写入；读取选择校验有效且序号较新的记录，最新记录损坏时可回退上一条。clear 先提交更新的空记录（tombstone），再将另一槽也改为空；只有两步均成功才返回成功，防止单个槽损坏导致已清除存档复活。Lua 异常不会自动清掉本应用的有效存档。

首次导入只在两个新 key 都完全不存在时尝试，只读 `miniapp.save.0` 至 `.3`，要求精确 1176 字节已知布局、magic、ID `local:ricestep`、有界长度、正数有效期和正确字符串终止。导入成功写入新存档后才返回数据；离线时无法判断旧墙上时钟有效期是否已过，因此不伪称完成 TTL 验证。未知布局、读取或写入失败均不冒充迁移成功；旧 key 始终保留。

继续遵守 1 KiB、32 个扁平字段和 10 秒写入间隔。退出会尝试保存，但若仍在写入限流窗口，刚发生的操作最多可能损失约 10 秒；突然断电同样存在这个窗口。不能宣称“每次喂饭立即落盘”。第一版保留这种明确、有界的 Flash 写入策略；需要关机立即刷盘时，应增加 Lua task 内完成的 flush/关机握手，不在其他任务直接访问 Lua 状态。

## 上板前必须取得的证据

1. stock 和 standalone 均在固定工具链成功构建；生成清单可复现。
2. ELF/bin 验证确认内嵌资产在 Flash、app 分区小于 4 MiB；另外实测 Lua/解码器峰值仍留有足够 PSRAM。
3. 接纯电源并断网后冷启动，直接显示肥鱼；不出现配网提示覆盖页面。
4. 喂饭/完美饭/锅巴等事件直接播已选音频，完成、连续点击、取消和高优先级提示音无卡死/重叠。
5. 修改进度等待保存后断电重启，米饭、收藏和上次做饭结果恢复；未完成番茄钟仍恢复为暂停。
6. 需要时使用已保存的完整原厂 LPK 恢复；任何实际刷写由主任务单独执行，准备脚本不代办。

摄像头运动识别不属于这里已经完成的功能。本版只先建立它将来可复用的离线宿主。
