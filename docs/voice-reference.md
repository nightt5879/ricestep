# 大肥鱼配音参考

核对日期：2026-11-01。用户确认目标是 DeepSeek 蓝色大肥鱼，希望听感蠢萌、软糯、可爱；当前介绍视频的 Xiaoyi 女声不符合用户的新反馈。

## 已找到的具体来源

- 原作者：B 站 [星落落_oi](https://space.bilibili.com/30584746/)。
- 原作者发布：[deepseek大肥鱼可以唱歌和说话了，模型公布！](https://www.bilibili.com/video/BV1cbH56KER3/)，2026-10-25。简介说明使用大肥鱼 SSR 音色和「人是猫」音色作为高权重素材，并链接下方工程；原作者写明「自用随意，不可商用」。
- 配套工程：[Esmerubphire/astrbot_plugin_feiyu_voice](https://github.com/Esmerubphire/astrbot_plugin_feiyu_voice)。README 提供 `cute` 可爱、`gentle` 温柔两种说话声线，`cute` 是默认选项。按用户描述，可爱声线是后续应优先试听的候选，尚未完成用户听感确认。
- 来源声明：[COPYRIGHT.md](https://github.com/Esmerubphire/astrbot_plugin_feiyu_voice/blob/main/COPYRIGHT.md) 说明训练素材取得原作者授权；这不等同于本项目获得所有用途的授权。
- 说话模型：[FeiyuVoice-CosyVoice3](https://huggingface.co/Esmerubphire/FeiyuVoice-CosyVoice3)。已有模型推理不需要重新训练，但需另行搭建语音推理环境，或使用作者提供、仍标为尚未完成真实 GPU 验收的 Colab 入口。

## 本次实际完成范围

通过原作者视频说明和配套工程文档核对了声库来源与声线名称。没有主观试听参考片、没有下载或安装模型，没有训练、调用付费服务或替换音频。现有 `assets/audio/v1/` 的十四句 MP3 与 40 秒介绍片仍是 Xiaoyi 版本。

用户已暂停 ML/原生工具链安装与硬件排查；这次找配音参考不恢复上述工作。不能将调高普通 TTS 音高、找到仓库或原先两句试听通过，表述为已经得到符合新要求的大肥鱼声音。后续若明确继续换配音，应先用「啊呜，好吃。再一口，就一口。」核对可爱声线，再处理整片，并保持模拟画面与后期配音标注。
