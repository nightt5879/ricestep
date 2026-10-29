# 肥鱼饭搭子语音

语音包位于 `assets/audio/v1/`。14 句原创短台词覆盖喂饭、吃饱、空碗、专注、四种饭的结果，以及对上一锅饭的回忆。音色采用轻亮女声 `zh-CN-XiaoyiNeural`，语速 `-12%`、音高 `+4Hz`，搭配慢半拍的停顿和贪吃小笨蛋式台词，表达用户希望的蠢萌感觉。喂饭“啊呜，好吃。再一口，就一口。”与锅巴“呃，饭好像，晒黑了。是锅巴！”两句已经试听并获用户确认，其余台词沿用相同参数。

音频为 24kHz、单声道 MP3，每句约 3.5–5.2 秒。喂饭和锅巴文件保留用户实际试听过的候选字节，其余12句按相同参数生成。`lines.json` 保存合成输入，`manifest.json` 保存文件名、台词、音色参数、时长、大小与 SHA-256。台词合成使用本机独立虚拟环境内的 [edge-tts 7.3.1](https://github.com/rany2/edge-tts)，通过 Microsoft Edge Read Aloud 服务生成。项目不需要保存语音 API 密钥。

音色与生成来源保留在清单中；生成器许可证不作为合成音频的独立授权声明。

## 重新生成

需要 `uv` 和可执行的 `ffprobe`。在仓库根目录运行：

```powershell
pwsh -File scripts/generate-voice.ps1
```

脚本将 `edge-tts==7.3.1` 安装在忽略提交的 `.tools/voice-venv/`；已有音频校验后复用。修改台词或声音参数后，用 `-Regenerate` 重做。生成先写临时文件，经过 MP3 校验后才替换正式文件。服务端合成可能随时间变化，输入参数可重复使用不等于输出字节永远相同；当前已审核的 MP3 与 SHA-256 一并保留。

## 开发板播放

官方 [3.1.0 更新说明](https://docs2.listenai.com/x/AL2O8BGTO) 新增在线音频。当前板卡已实测 `audio.play(stringURL)` 返回 `true`，`audio.get_state()` 从 `loading` 经过 `playing` 回到 `idle`。URL 必须为有长度限制的 HTTP(S) 地址；`/miniapp/voice.mp3` 这样的本地路径被拒绝。旧版 SDK 的 [tts.speak](https://github.com/LISTENAI/LingClaw-SDK/blob/master/docs/api/tts.md) 仍使用设备当前发音人，不能通过传入音色参数改成这套声音。

开发阶段由电脑提供只读语音包；开发板与电脑保持同一局域网，电脑和服务器保持运行。服务器只暴露清单内 MP3，不托管仓库、配置、清单或任何其他文件。应用只播放这套审核过的短语音；网络失败时保留字幕、表情和游戏交互，不调用设备原声 TTS。

```powershell
# 配置好下面的 .voice-config.json 后，启动后台服务并验证声音
pwsh -File scripts/start-voice-server.ps1
# 只检查现有服务，不启动进程
pwsh -File scripts/start-voice-server.ps1 -CheckOnly
```

把示例地址换成电脑实际局域网 IPv4，写入仓库根目录 `.voice-config.json` 后启动服务、部署：

```json
{"base_url":"http://192.168.1.100:8770/voice/v1"}
```

`scripts/build.ps1` 读取 `base_url`，`-VoiceBaseUrl` 参数可以覆盖本地配置。`app/voice.lua` 与其他模块一起打包，负责短语音播放；旧模拟器不提供真实在线音频播放。

`start-voice-server.ps1` 验证喂饭 MP3 与用户试听通过的字节完全一致，并检查非清单路由返回 404。健康服务会直接复用；配置地址是本机网卡实际持有的 RFC1918 私有 IPv4、HTTP、端口 1024–65535 时，才允许自动启动。其他地址只检查服务，无法连接或声音不符时明确报错，不改配置、不替换或终止其他进程。

启动脚本需要 Windows 和 PowerShell 7.2 以上。新启动的 Node 服务是隐藏的独立进程，关闭启动终端后继续运行；日志和进程记录在忽略提交的 `.cache/voice-server/`。这不是开机自启或系统服务，电脑重启后再次运行启动脚本即可。脚本不调整防火墙、计划任务或登录启动项。若本机检查通过而开发板连不上，仍需检查两者网络和已有防火墙设置。

`deploy.ps1` 会在备份、上传前执行同一启动与健康检查，然后用刚验证的地址构建，避免服务停掉后无声地切换音色。服务暂不可用时上传停止；运行中的应用遇到断网则静音保留字幕。

播放地址形式为 `http://<电脑局域网IPv4>:8770/voice/v1/feed.mp3`。监听地址只接受明确的回环或 RFC1918 私有 IPv4，默认 `127.0.0.1`。支持 GET、HEAD 和单段 Range；启动时校验所有音频的大小与 SHA-256，日志仅输出声音 ID 与 HTTP 状态。没有上传端点，不会把私有 GitHub 仓库改为公开。网络地址放在本地忽略配置中，不能写入提交。

本地路由检查：

```powershell
node --test tests/voice-server.test.cjs
pwsh -File tests/start-voice-server.test.ps1
```

## 验证边界

- 全部 14 个文件已用 ffprobe 验证格式和时长；清单哈希经过服务器加载校验。
- 服务器路由、请求方法、Range、私有 IP 限制和资源篡改检测已通过自动化检查。
- 板卡在线音频调用与 `loading → playing → idle` 状态已实测。用户对 Xiaoyi 喂饭、锅巴两句反馈“这次对味了，就用这个”；这两句的认可不等同于全部 14 句均已逐句试听。
- 后台启动、已有服务复用、带空格路径、错误音频拒绝和配置不改写有独立集成测试；测试只启动并清理自己的临时服务器，不访问开发板。
- 台词服务依赖电脑在线。离开这个局域网时，需要配置可访问且通过同样检查的授权音频托管；未连接时仅显示字幕，不切回其他发音人。
