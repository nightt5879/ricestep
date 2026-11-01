# 肥鱼饭搭子：参赛展示视频

作品此前已经在 Mini3 成功运行，角色显示、按键、煮饭与米饭奖励有实机验证记录。本次录制时无法完成配网，用户于 2026-11-01 决定使用桌面模拟器展示已经实现的玩法。参赛版已使用 FeiyuVoice 可爱 F6 声线，输出 41.333 秒、1080×1920、30 fps 的 H.264/AAC 视频；应用原有十四句 Xiaoyi 声音未改变。

在仓库根目录执行 pwsh -File scripts/render-intro.ps1。只做静帧可加 -StillsOnly。依赖隔离在 work/filming/video/，输出在 work/filming/output/。需要已安装的官方 SDK（setup.ps1）、Node 20+、本机 Chrome；渲染脚本使用 C:/Program Files/Google/Chrome/Application/chrome.exe，其他安装位置需调整 render.cjs。

不传音频目录时使用仓库原语音；参赛版使用 `pwsh -File scripts/render-intro.ps1 -VoiceDirectory <视频音频目录>`。该目录需包含六个 MP3 与 manifest.json；捕获脚本校验台词、SHA-256 与时长，再生成 timeline.json，确保完整台词结束后仍有停留。生成的台词、模型与视频不提交 Git。

复现新配音：在已安装的独立 WSL TTS 环境运行 `generate-feiyu.py --server <上游服务源码> --package <模型目录> --profile <本机运行配置> --output <新输出目录>`，并按运行配置设置 `LD_LIBRARY_PATH`。它校验固定源码和模型后只加载一次模型，按原六句台词逐句合成。然后在 Windows 仓库根目录运行 `node scripts/video-intro/prepare-voice.cjs <生成目录> <视频音频目录>`，再用上述 `-VoiceDirectory` 渲染。模型来源与实际验证边界见 [配音记录](../../docs/voice-reference.md)。

成片检查入口为 `node scripts/video-intro/check-video.cjs`，检查最终 MP4 的格式、完整解码、音峰值、逐句音轨位置与完整长度，并抽出切点和各镜头关键帧。

capture.cjs 将未修改的生产 Lua 与隔离模拟驱动拼接，经官方 API 4 桌面 SDK 生成截图。初始库存为 3，完美做饭得 2，喂一次扣 1，烧糊不扣已有库存；脚本对这些状态有断言。绘制与模拟输入不是 GUI 按键或物理设备验收。六态特写直接解码 character.lua 中已有高精度角色字符串；完整 UI 仍为官方 API 4 模拟器画面。

全片持续标注“模拟画面 · 非实机拍摄”；配音在后期合成。烧糊等待段明确说明剪辑压缩。未使用用户存档、内网地址、设备序列号、运动计数或摄像头数据。渲染没有 ADB 调用、部署或音频扬声器播放。

详细视觉、功能映射与时间线见 design-spec.md。当前没有 BGM，不需要单独的无 BGM 版本。正式发布/提交仍由用户处理。

标题笔画改编自 video-shotcraft 的 MarkerUnderlineTitle.tsx，按 Apache-2.0 保留许可于 LICENSE-video-shotcraft.txt；修改内容是适配中文正体字基线、项目配色与竖屏布局。来源：https://github.com/Vincentwei1021/video-shotcraft 。Remotion 和转码组件各自的许可仍随 npm 包保留。
