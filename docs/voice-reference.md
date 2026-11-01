# 大肥鱼配音参考

核对日期：2026-11-01。用户确认目标是 DeepSeek 蓝色大肥鱼，希望听感蠢萌、软糯、可爱；当前介绍视频的 Xiaoyi 女声不符合用户的新反馈。

## 已找到的具体来源

- 原作者：B 站 [星落落_oi](https://space.bilibili.com/30584746/)。
- 原作者发布：[deepseek大肥鱼可以唱歌和说话了，模型公布！](https://www.bilibili.com/video/BV1cbH56KER3/)，2026-10-25。简介说明使用大肥鱼 SSR 音色和「人是猫」音色作为高权重素材，并链接下方工程；原作者写明「自用随意，不可商用」。
- 配套工程：[Esmerubphire/astrbot_plugin_feiyu_voice](https://github.com/Esmerubphire/astrbot_plugin_feiyu_voice)。README 提供 `cute` 可爱、`gentle` 温柔两种说话声线，`cute` 是默认选项。按用户描述，可爱声线是后续应优先试听的候选，尚未完成用户听感确认。
- 来源声明：[COPYRIGHT.md](https://github.com/Esmerubphire/astrbot_plugin_feiyu_voice/blob/main/COPYRIGHT.md) 说明训练素材取得原作者授权；这不等同于本项目获得所有用途的授权。
- 说话模型：[FeiyuVoice-CosyVoice3](https://huggingface.co/Esmerubphire/FeiyuVoice-CosyVoice3)。已有模型推理不需要重新训练，但需另行搭建语音推理环境，或使用作者提供、仍标为尚未完成真实 GPU 验收的 Colab 入口。

## 本次授权与实际完成范围

用户后续明确授权安装现成语音模型进行推理，随后选择「暂时不退出游戏，先把环境装好」。此授权只涉及视频配音；摄像头／姿态研发、模型训练与硬件排查仍暂停，也不能关闭用户正在运行的游戏。

已通过原作者视频说明和配套工程文档核对来源，并下载完整模型包：7,452,796,369 字节，Hugging Face 固定修订 `f37e9d1dda26e8b45b4e9f1b4c347c0a07b797e2`，使用作者同时提供的 ModelScope 镜像续传；所有文件均按作者清单校验大小与 SHA-256。默认候选为 `cute` / F6，参考 WAV 也已校验。模型与独立环境放在仓库外，不随 Git 提交。

本机推理依赖已安装，129 个包的兼容性检查通过。上游静音诊断在 RTX 4070 SUPER 上通过源码校验、版本检查、GPU 矩阵乘法／卷积／FFT 和推理模块加载；未加载完整语音模型、生成台词或进行主观听审。现有 `assets/audio/v1/` 的十四句 MP3 与 40 秒介绍片仍是 Xiaoyi 版本，没有替换音轨、部署设备或调用付费服务。不能将调高普通 TTS 音高、找到仓库、安装成功或原先两句试听通过，表述为已经得到符合新要求的大肥鱼声音。

显存可用后，应先合成「啊呜，好吃。再一口，就一口。」核对可爱声线，再处理整片，并保持模拟画面与后期配音标注。此前 Mini3 实机运行和玩法验证仍然成立；此次视频配音更新不改变其证据范围。

## 本机环境与后续入口

- 独立环境：WSL2 Ubuntu 24.04，Python 3.11.17，PyTorch / torchaudio 2.11.0+cu128，torchcodec 0.11.0+cpu。模型、环境主体与下载缓存位于仓库外的 `E:\ricestep-voice`；普通 Lua 构建不依赖这套环境。
- 仅安装 TTS 说话推理；没有恢复训练、安装唱歌／变声运行环境，也没有修改游戏进程、显卡驱动或系统网络配置。
- 本机后续入口为 `pwsh -File E:\ricestep-voice\speak-cute.ps1`，默认使用可爱声线和上述短句。`-CheckOnly` 只查看显存；实际合成设有 6 GiB 空闲显存门槛，低于门槛立即退出。完整推理所需显存仍待实际生成验证。
- 入口按上游校验模型和运行源码，输出独立 WAV 与记录；不会自动播放、部署或替换成片。完整推理和听感验收仍待显存空闲后进行。
- 固定来源、模型清单与工具归档 SHA-256 记录在 `toolchain.lock.json`；已安装包名和版本见 [依赖快照](feiyu-inference-requirements.txt)。下载内容、权重与含用户目录的本机运行配置不提交 Git。
