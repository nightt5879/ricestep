# RiceStep · 肥鱼开饭

把专注和散步变成喂给鲸鱼娘的一碗饭。面向 **Arcs Mini3 / 固件 3.1.0** 的 Lua 小应用，240×240 像素界面。

## 基础版玩法

- **喂饭**：初始3碗，双击喂一碗；角色吃饭、短音效和语音反馈。饱腹时温和拒绝，不会饿死或扣除已有收藏。
- **专注**：25分钟番茄钟，支持暂停、继续和提前结束确认；完整完成获得1碗饭。这是专注时长奖励，不计入步数。
- **散步**：已准备“iPhone健康步数 → 本机中继 → 肥鱼”接入。500个新增真实步数换1碗，零头留到下次；有奖励的散步还会发现纪念品。需要先完成手机授权和同步，未接入时显示等待。
- **背包**：6种随机纪念品。出发前和结束前各同步一次手机步数；断网时可回家喂饭，散步仍保留。也可确认结束并只结算已经同步的步数，未同步部分不计入；重复同步不重复领奖。
- **存档**：按SDK约束节流保存，最多保留30天。专注重载后暂停；散步保留已保存的计步基线，收到真实累计步数后恢复差值。旧存档缺少基线时保持暂停，重新同步后从新基线继续。

只有一个功能键：**单击切换选项，双击确认**。长按仍属于系统操作。背包中单击翻页、双击回家。

角色参考来自用户提供的鲸鱼娘形象。三态原图通过 built-in imagegen 生成，提示词和来源说明见[角色素材](assets/generated/README.md)。这是社区二创项目，不宣称官方 DeepSeek 产品。

## 构建、预览与测试

需要 Windows x64、PowerShell 7、Node.js 20+、Git 和已登录的 GitHub CLI。

```powershell
pwsh -File scripts/setup.ps1
pwsh -File scripts/build.ps1
pwsh -File scripts/test.ps1
pwsh -File scripts/dev.ps1
```

`setup` 将固定版本官方SDK和Google ADB放在 `.tools/`，校验哈希，不修改系统PATH。`dev` 打开模拟器并监控 `app/*.lua`，保存后重新打包；Ctrl+C停止监控，`-NoWatch`只打开一次。VS Code构建任务也可调用这些命令。

源码分模块维护，构建时拼接、压紧为一个 `dist/ricestep.lua`，符合设备无 `require/package` 的环境。产物另含 `dist/preview.png` 和构建信息。输出、工具、设备备份、步数及配对token均不提交。

旧官方模拟器只提供API4和128矩形，预览使用低细节角色；**Mini3固件3.1.0的API5使用32×40逻辑像素角色和256形状额度**。角色图形由生成的位图编译，不依赖设备读取PNG。桌面预览不等同于真机验证。

## 上板

```powershell
pwsh -File scripts/devices.ps1
pwsh -File scripts/deploy.ps1 -Serial YOUR_BOARD_SERIAL
```

部署先构建和备份当前小应用，再上传、回读并比对源码哈希；上传立即替换运行中的小应用。备份仅在 `.cache/device-backup/`。调试源码在设备重启后需重新上传；存档遵循SDK有效期规则。

## 手机真实步数

[手机接入说明](docs/phone-steps.md)包含本机服务与iPhone快捷指令步骤。只有用户在手机明确授权后才读取健康步数。当前代码和合成数据测试已完成，实际手机链路还需授权和验证；这不是板载计步。

## 修改角色素材

已编译的 `app/character.lua` 可直接构建。修改源图或编码时：

```powershell
npm ci --ignore-scripts
npm run assets
pwsh -File scripts/build.ps1
```

源图、像素编码器、逐像素一致性测试和编译统计一同保留。每帧角色最多230形状，为界面留出额度。启动采用紧凑字串，避免大量Lua表导致真机启动超时。

## 目录

| 路径 | 内容 |
| --- | --- |
| `app/game.lua` | 喂食、专注、散步、奖励和存档规则 |
| `app/main.lua` | 界面、单键交互和声音反馈 |
| `app/character.lua` | 已编译角色三态 |
| `app/step-sync.lua` | 可选的局域网步数同步 |
| `scripts/` | 安装、构建、测试、上板、素材编译、中继 |
| `tests/` | Lua规则与交互、本机步数协议测试 |
| `assets/generated/` | imagegen源素材、提示词与编码统计 |
| `docs/verification.md` | 验证证据与待完成检查 |

依赖见 `toolchain.lock.json`。官方依据：[LingClaw SDK](https://github.com/LISTENAI/LingClaw-SDK)、[Mini3固件](https://docs2.listenai.com/x/AL2O8BGTO)、[设备联调](https://github.com/LISTENAI/LingClaw-SDK/blob/eff009e242cd748f1ba7a9cd3001b72b1de38298/docs/getting-started.md)。
