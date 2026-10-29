# Mini3 3.1.0 的本地语音边界

结论：现有原厂固件的小应用接口尚没有已证实的 MP3 本地播放入口；已验证的 `audio.play` 是 HTTP(S) URL。不能仅因原生 C 播放器支持文件或内存，就宣称 Lua 可直接播放这些资源。完全离线且重启自启动的路线采用独立原生固件 overlay，见 `standalone-device.md`。

## 已确认入口与限制

| 层级 | 证据 | 对本项目的意义 |
| --- | --- | --- |
| 原厂 3.1.0 Lua API 5 | 已有物理探针确认 HTTP(S) URL 可以 loading → playing → idle；本地文件路径被拒绝 | 没有证据支持直接把 `/SD:/feed.mp3` 交给 Lua `audio.play` |
| 公开 native 小应用代码 | 固定镜像 `apps/arcs-mini/miniapp/miniapp_runtime.c` 只有 tts/buzzer 注册；API 4 | 缺少原厂 API 5 audio 绑定源码，不能据此猜测 file://、mem:// 支持 |
| ADB debug sync | `miniapp/Kconfig` 依赖 `!ADB_SYNC`；`miniapp_adb.c::parse_path` 只接 `/miniapp/<id>.lua`，源存在 PSRAM | 不接受 MP3；断电不保留调试脚本，不能当安装介质 |
| 原生 app_player | `arcs-sdk/components/app_player/README.rst` 描述 `/SD:/music.mp3` 与 `mem://addr=...size=...` | 可以写受控 native 适配，不能直接提升为 Lua 权限 |
| 系统 tone ROMFS | `src/middleware/tone/app_tone.c::app_tone_get_url` 给现有音频资源生成 mem URL | 证明 Flash 中压缩音频可作为原生播放器输入 |
| SD 音乐功能 | `voice_player_music.c` 在 miniapp active 时拒绝 music URL；USB-MSC 模式在 `platform.c` 阻止应用启动 | 系统 SD 播放/USB 文件传输不是桌宠事件语音的直接接入点 |

来源固定为 [public SDK mirror commit c0a95bcf](https://github.com/Shinku-Chen/listenai_arcs_mini/tree/c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35)。这是第三方公开镜像，官方上游 commit 对应关系尚未独立验证。

## 不选用的快捷改法

官方[更换唤醒提示音说明](https://docs2.listenai.com/x/cpmSpoptA)描述替换 `000_geeting.mp3`、用 ROMFS 打包 tone.bin、写到 `0x100000`，以及防止云端覆盖 tone 的配置。它只改变系统唤醒提示音，不会将 14 个做饭/喂饭事件接到游戏里；覆盖整个 tone 资源还要保留其他系统提示音。当前方案把音频随应用一起编译到 CP Flash，不单独改 tone 分区。

Flash/SD 的容量足以容纳 375408 字节声音并不代表可任意写入。OTA 和 KV 分区保留原用途；不把它们当 MP3 文件系统。不使用系统 TTS 替代用户已选的角色音色。

## 选择的实现

复用已有 Lua 引擎；补入固定官方 Lua 5.4.8；构建时嵌入游戏源码和 MP3；增加只接受 `builtin://ricestep/<白名单>.mp3` 的原生 audio 绑定；开机直接启动内置 bundle，存档走独立有界 KV。依赖下载并通过固定校验和验证后，构建可离线完成；设计中的板卡运行不要求电脑或网站。

2026-10-29 stock 基线和独立版均已在目标工具链成功编译。实际 ELF/bin 检查核实 14 个 MP3 的精确字节、内嵌 Lua、Flash VMA/LMA、启动复制表及 4 MiB CP 分区边界；本工具链没有生成 `.map`，使用实际 ELF 验证器。后续源代码变更必须重新构建并生成验证报告，最新结果在 `dist/native/standalone/`。

这项路线需要新固件。当前硬件功能仍未验证，用户要求今晚安静停在演示之前，因此没有刷入、没有播放。准备、编译与主机测试不能替代断网冷启动、真实音频播放和断电恢复测试；具体实现、构建命令和验收边界在 `standalone-device.md`。
