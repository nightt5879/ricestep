# 大肥鱼配音参考

核对日期：2026-11-01。用户确认目标是 DeepSeek 蓝色大肥鱼，希望听感蠢萌、软糯、可爱；参赛视频已从原 Xiaoyi 版本换为 FeiyuVoice 的 `cute` / F6 声线。

## 已找到的具体来源

- 原作者：B 站 [星落落_oi](https://space.bilibili.com/30584746/)。
- 原作者发布：[deepseek大肥鱼可以唱歌和说话了，模型公布！](https://www.bilibili.com/video/BV1cbH56KER3/)，2026-10-25。简介说明使用大肥鱼 SSR 音色和「人是猫」音色作为高权重素材，并链接下方工程；原作者写明「自用随意，不可商用」。
- 配套工程：[Esmerubphire/astrbot_plugin_feiyu_voice](https://github.com/Esmerubphire/astrbot_plugin_feiyu_voice)。README 提供 `cute` 可爱、`gentle` 温柔两种说话声线，`cute` 是默认选项。按用户描述，可爱声线是后续应优先试听的候选，尚未完成用户听感确认。
- 来源声明：[COPYRIGHT.md](https://github.com/Esmerubphire/astrbot_plugin_feiyu_voice/blob/main/COPYRIGHT.md) 说明训练素材取得原作者授权；这不等同于本项目获得所有用途的授权。
- 说话模型：[FeiyuVoice-CosyVoice3](https://huggingface.co/Esmerubphire/FeiyuVoice-CosyVoice3)。已有模型推理不需要重新训练，但需另行搭建语音推理环境，或使用作者提供、仍标为尚未完成真实 GPU 验收的 Colab 入口。

## 本次授权与实际完成范围

用户先授权安装环境，后自行关闭游戏，并明确要求直接完成参赛作品。已据此进行完整语音推理与视频换声；摄像头／姿态研发、模型训练与硬件排查仍暂停。

已通过原作者视频说明和配套工程文档核对来源，并下载完整模型包：7,452,796,369 字节，Hugging Face 固定修订 `f37e9d1dda26e8b45b4e9f1b4c347c0a07b797e2`，使用作者同时提供的 ModelScope 镜像续传；所有文件均按作者清单校验大小与 SHA-256。默认候选为 `cute` / F6，参考 WAV 也已校验。模型与独立环境放在仓库外，不随 Git 提交。

本机推理依赖已安装，129 个包的兼容性检查通过。上游静音诊断通过后，已在 RTX 4070 SUPER 上实际加载 CosyVoice3 和可爱 F6 权重，完成 `hello`、`perfect`、`feed`、`burnt`、`remember_burnt`、`focus_start` 六句合成，逐句保存 WAV、哈希与生成记录。全部使用发布包的 FP32、seed 42、speed 1.0；没有训练、变声或调整音高。视频 MP3 只做响度处理，保留完整台词，并按其长度扩展时间轴。

新版视频为 1240 帧、41.333 秒，已使用六句新配音。文件解码、音峰值与成片逐句音轨相关性检查通过；本机 ASR 用于辅助检查台词，不能代替主观音色听审或用户认可。原有 `assets/audio/v1/` 十四句 MP3 未改变，未部署设备、播放扬声器或调用付费服务。此前 Mini3 实机运行和玩法验证仍然成立；此次后期配音不改变其证据范围。

运行边界：WeText 文本规范化资源的自动下载返回 HTTP 403，上游切换为原文输入。本次六句普通中文台词已成功生成；未验证数字、日期等复杂文本规范化，不宣称该可选前端已配置完成。输入脚本、配音清单和技术记录可用于复核本次确实完成的范围。

## 本机环境与后续入口

- 独立环境：WSL2 Ubuntu 24.04，Python 3.11.17，PyTorch / torchaudio 2.11.0+cu128，torchcodec 0.11.0+cpu。模型、环境主体与下载缓存位于仓库外的 `E:\ricestep-voice`；普通 Lua 构建不依赖这套环境。
- 仅进行 TTS 说话推理与本机 ASR 台词校验；没有恢复训练、安装唱歌／变声运行环境，也没有修改游戏进程、显卡驱动或系统网络配置。
- 本机单句入口为 `pwsh -File E:\ricestep-voice\speak-cute.ps1`，默认使用可爱声线和喂饭短句。`-CheckOnly` 只查看显存；实际合成设有 6 GiB 空闲显存门槛，低于门槛立即退出。批量视频配音入口为 `scripts/video-intro/generate-feiyu.py`；详见视频工程说明。
- 入口按上游校验模型和运行源码，输出独立 WAV 与记录；不会自动播放或部署。视频通过独立 `-VoiceDirectory` 输入换声，应用包保持原样。
- 固定来源、模型清单与工具归档 SHA-256 记录在 `toolchain.lock.json`；已安装包名和版本见 [依赖快照](feiyu-inference-requirements.txt)。下载内容、权重与含用户目录的本机运行配置不提交 Git。
