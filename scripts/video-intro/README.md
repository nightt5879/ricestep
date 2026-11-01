# 肥鱼饭搭子：参赛展示视频

作品此前已经在 Mini3 成功运行，角色显示、按键、煮饭与米饭奖励有实机验证记录。本次录制时无法完成配网，用户于 2026-11-01 决定使用桌面模拟器展示已经实现的玩法。默认输出 40 秒、1080×1920、30 fps 的 H.264/AAC 视频，使用现有角色 MP3；本工程尚未换入新大肥鱼声线。

在仓库根目录执行 pwsh -File scripts/render-intro.ps1。只做静帧可加 -StillsOnly。依赖隔离在 work/filming/video/，输出在 work/filming/output/。需要已安装的官方 SDK（setup.ps1）、Node 20+、本机 Chrome；渲染脚本使用 C:/Program Files/Google/Chrome/Application/chrome.exe，其他安装位置需调整 render.cjs。

capture.cjs 将未修改的生产 Lua 与隔离模拟驱动拼接，经官方 API 4 桌面 SDK 生成截图。初始库存为 3，完美做饭得 2，喂一次扣 1，烧糊不扣已有库存；脚本对这些状态有断言。绘制与模拟输入不是 GUI 按键或物理设备验收。六态特写直接解码 character.lua 中已有高精度角色字符串；完整 UI 仍为官方 API 4 模拟器画面。

全片持续标注“模拟画面 · 非实机拍摄”；配音在后期合成。烧糊等待段明确说明剪辑压缩。未使用用户存档、内网地址、设备序列号、运动计数或摄像头数据。渲染没有 ADB 调用、部署或音频扬声器播放。

详细视觉、功能映射与时间线见 design-spec.md。当前没有 BGM，不需要单独的无 BGM 版本。正式发布/提交仍由用户处理。

标题笔画改编自 video-shotcraft 的 MarkerUnderlineTitle.tsx，按 Apache-2.0 保留许可于 LICENSE-video-shotcraft.txt；修改内容是适配中文正体字基线、项目配色与竖屏布局。来源：https://github.com/Vincentwei1021/video-shotcraft 。Remotion 和转码组件各自的许可仍随 npm 包保留。
