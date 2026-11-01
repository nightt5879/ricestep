# RiceStep · 肥鱼饭搭子

**2026-11-01 参赛展示：** 本作品此前已成功部署到 Mini3，角色显示、按键交互、煮饭和米饭奖励有[实机验证记录](docs/verification.md)。这次准备录制参赛视频时未能完成配网，改用官方桌面模拟器画面与后期角色配音展示已实现的交互。41.333 秒竖屏参赛视频已使用 FeiyuVoice 可爱声线，[视频工程](scripts/video-intro/README.md)与[配音生成记录](docs/feiyu-video-voice.json)均已公开；本片不是新的实机录像。硬件排查、动作识别研发与独立固件工作继续暂停。接手记录见 [HANDOFF_DORM.md](HANDOFF_DORM.md)。

源码仓库：https://github.com/nightt5879/ricestep 。本项目为社区二创，非 DeepSeek 官方产品。仓库公开不改变各素材与第三方组件原有的权利及许可。

“你负责努力，我负责吃饭。” 面向 **Arcs Mini3 / 固件 3.1.0** 的桌面陪伴与煮饭小游戏。肥鱼会犯困、闻到米饭就精神，还会记得上一锅是好饭还是锅巴。240×240像素界面，六种角色姿态，14句蠢萌、慢半拍、贪吃的短语音。

另有暂停验收的 **Mini3 独立固件**：将游戏和已选定的 14 段声音放入板内 Flash，开机进入肥鱼，存档不依赖网络校时。已通过原生编译、资源完整性和存档自动测试；按用户要求停在实机演示之前，**尚未刷写、尚未验证板上离线播放**。源码基线为公开的 3.0.3 快照，与现有原厂 3.1.0 的兼容性仍需实测。入口见[独立版说明](docs/standalone-device.md)、[原生环境](docs/native-build.md)和[恢复准备](docs/native-recovery.md)。下文的 HTTP 语音与 ADB 上传说明针对原厂固件上的 Lua 版本。

## 离线看效果

下载 [preview.html](docs/preview.html) 后用浏览器打开，可切换六种像素表情、预览喂饭动作、试听全部14句声音。素材和MP3均内嵌，不依赖开发板、电脑语音服务器或外部网站。它是外观与声音展览，不是完整游戏模拟器；[预览截图](docs/preview.png)可直接查看。

预览从当前 `app/character.lua` 的API5图元及音频清单生成，更新素材后运行 `node scripts/export-preview.cjs`。未来“摄像头识别俯卧撑／波比跳，运动赚饭”的想法已记录在[后续路线](docs/roadmap.md)，尚未实现可靠运动识别。[小模型训练实验](docs/pose-student-experiment.md)已打通本地训练、整数转换与主机推理；[新骨干实验](docs/pose-mobilenet-experiment.md)改善了手脚定位，但置信度和完整画面计数仍未达标，尚未接入固件。

## 玩法与操作

- **喂饭**：初始3碗，双击喂一碗，鲸鱼娘切换吃饭姿态；声音与语音在可用时反馈。饱腹时不消耗米饭，不会饿死或扣收藏。
- **专注**：25分钟番茄钟，可暂停、继续、回主页陪伴。完整完成获得1碗饭和一次随机收藏；提前结束需确认，不发奖励。
- **煮饭**：观察往返移动的火候指针，**单击起锅**。金色区域是完美白米饭，获得2碗饭和一次随机收藏；绿色区域获得1碗；夹生或锅巴不奖励，也不扣存粮。未起锅的回合在10秒游戏计时结束。
- **收藏**：六种小物件，已拥有的会出现在主页两侧收藏架。重复发现会显示“又遇见”，不会重复增加种类。
- **休息**：手动开启30分钟提醒，可暂停、稍后5分钟或确认已经休息。仅显示提示，不识别坐姿、不发运动奖励；重启后保留剩余时间并等待继续。见[休息提醒说明](docs/desk-reminder.md)。
- **运动（实验入口）**：「休息→运动」选择俯卧撑或深蹲，手动开始、停止。仅接受原生确认的完整动作，每5次兑换1碗饭；没有原生接口时明确显示不可用。界面与奖励账本已有自动测试，但当前大模型的[静态速度预算](docs/pose-performance.md)不满足实时计数要求，需要更换模型及优化内核；摄像头实机效果尚未验证。见[候选固件与验收边界](docs/camera-exercise.md)。
- **上一锅的记忆**：煮饭结算后记住完美、好饭、夹生或锅巴结果，下次做饭时给出对应的小互动。中断的回合不会覆盖记忆。

菜单仍是 **单击切换、双击确认**；收藏页单击翻页、双击回家。只有正在煮饭时改为单击起锅。开始后的350ms和结果出现后的500ms会忽略连按，避免开局即停锅或误开下一局；长按仍属于系统。

专注进行中先暂停，才能进入煮饭小游戏休息。主界面会显示剩余专注时间，肥鱼陪着你；完成后一起开饭。

## 计时与存档

独立固件的专注与饱腹消化优先使用板内单调计时器，不需要网络校时。原厂 Lua 版本在设备校时后使用实际秒差；回拨或超过5秒的跳变不补算，未校时则按运行器逻辑步长累计。煮饭指针与回合倒计时使用同一动画步长，设备繁忙时实际经过时间可能更长。

进度按SDK约束每隔至少10秒保存，最多保留30天。v2存档使用26个扁平字段，保留米饭、收藏、专注、做饭、休息提醒和旧散步权益；运动会话编号、已处理次数与米饭在同一次存档写入。重新载入后专注和未到期的提醒暂停，摄像头不会自动开启；未结算的煮饭回合取消且不发奖励。旧散步存档仅结算已经保存的进度，不增加新步数。刚操作就断电可能尚未保存。

当前版本没有散步入口，也不连接手机健康。步数方案保留为后续研发资料，见[步数接入状态](docs/phone-steps.md)。

## 构建、预览与测试

需要 Windows x64、PowerShell 7、Node.js 20+、Git和已登录的GitHub CLI。

```powershell
pwsh -File scripts/setup.ps1
pwsh -File scripts/build.ps1
pwsh -File scripts/test.ps1
pwsh -File scripts/dev.ps1
```

`setup`下载固定版本官方SDK和Google ADB至`.tools/`并校验哈希，不修改系统PATH。`dev`打开模拟器，监控`app/*.lua`并自动重新打包；Ctrl+C停止监控，`-NoWatch`只打开一次。

构建拼接`game.lua`、`character.lua`、`voice.lua`、`main.lua`，压紧为`dist/ricestep.lua`；另产出`dist/preview.png`和构建信息。语音地址读取本地 `.voice-config.json` 的 `base_url`，也可用 `build.ps1 -VoiceBaseUrl` 指定。不包含`step-sync.lua`，不读取`.local-config.json`。下载工具、构建结果、设备备份和私人配置不提交。

官方桌面模拟器为API4、128矩形，采用低细节角色；Mini3固件3.1.0已探明API5、256形状，角色使用32×40逻辑像素。桌面测试不替代实机显示、声音与按键验收，证据状态见[验证记录](docs/verification.md)。

## 角色语音

**2026-11-01 反馈：** 用户认为介绍视频的现有音色不够贴近大肥鱼，希望参考蠢萌、软糯的社区配音。已找到星落落_oi 发布的肥鱼声库及可爱声线，来源与未替换状态见[配音参考记录](docs/voice-reference.md)。以下参数描述当前实际保存的 MP3，不表示新声音已完成合成或验收。

固定台词采用轻亮女声 Xiaoyi，语速 −12%、音高 +4Hz，以慢半拍的停顿和小笨蛋式台词表现蠢萌贪吃；喂饭、锅巴两句试听已获用户认可。MP3 已随仓库保存。上板试听由同一局域网中的电脑提供音频；电脑和服务需要保持运行。

```powershell
# 先配置下面的 .voice-config.json，再启动隐藏的后台语音服务
pwsh -File scripts/start-voice-server.ps1
```

在仓库根目录创建不提交的 `.voice-config.json`，然后重新构建、部署：

```json
{"base_url":"http://192.168.1.100:8770/voice/v1"}
```

服务只提供语音清单内的 MP3。启动脚本和部署都会验证用户认可的喂饭音频 SHA-256 及路由白名单；已有健康服务直接复用。仅当配置是本机实际持有的私有 IPv4 时自动启动，日志与进程记录保存在 `.cache/voice-server/`，不修改网络配置或设置开机自启。

板卡已实际验证 `audio.play(URL)` 返回 `true`，播放状态经过 `loading → playing → idle`；两句代表性声音已试听认可，其余台词沿用同一音色参数。断网或播放失败时保留字幕和表情，**不回退到设备原声 TTS**。音频再生成与测试说明见[语音文档](docs/voice.md)。

## 上板

```powershell
pwsh -File scripts/devices.ps1
pwsh -File scripts/deploy.ps1 -Serial YOUR_BOARD_SERIAL
```

部署先构建、备份当前应用，再上传并回读比对源码哈希。上传立即替换运行中的小应用；备份只存于`.cache/device-backup/`。设备重启后需重新上传调试脚本。实机操作检查见[手动验收](docs/smoke-test.md)。

## 角色与源码

角色参考来自用户提供的鲸鱼娘形象；源图通过built-in imagegen生成，含待机、吃饭、发现、打盹、馋饭、心虚六态。[素材与提示词](assets/generated/README.md)保留来源说明。本项目是社区二创，不宣称官方DeepSeek产品。

已编译的`app/character.lua`可以直接构建。修改源图或编码后执行：

```powershell
npm ci --ignore-scripts
npm run assets
pwsh -File scripts/build.ps1
```

| 路径 | 内容 |
| --- | --- |
| `app/game.lua` | 喂饭、专注、煮饭、收藏与存档规则；保留旧散步规则 |
| `app/main.lua` | 桌面陪伴、单键交互与声音反馈 |
| `app/voice.lua` | 已审核角色短语音播放与状态；不可用时保留字幕 |
| `app/character.lua` | 编码后的角色六态 |
| `assets/audio/v1/` | 14句MP3、台词和音频校验清单 |
| `app/step-sync.lua` | 暂不打包的旧步数传输模块 |
| `scripts/` | 安装、构建、测试、部署与素材工具 |
| `tests/` | 规则、界面、存档和保留的中继协议测试 |

工具版本见`toolchain.lock.json`。官方依据：[LingClaw SDK](https://github.com/LISTENAI/LingClaw-SDK)、[Mini3固件](https://docs2.listenai.com/x/AL2O8BGTO)、[设备联调](https://github.com/LISTENAI/LingClaw-SDK/blob/eff009e242cd748f1ba7a9cd3001b72b1de38298/docs/getting-started.md)。
