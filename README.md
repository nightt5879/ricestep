# RiceStep · 肥鱼开饭

Arcs Mini（LS26）小应用开发仓库。项目名暂定；本轮先完成 Windows 开发环境，不展开玩法实现。

当前入口是环境验收程序：240×240 屏幕、启动状态、功能键计数。计数表示按键次数，不是步数。

## 本机开始开发

环境：Windows x64、PowerShell 7、Git、已登录的 GitHub CLI。官方 LingClaw SDK `v0.1.0`、Android Platform Tools `37.0.1` 的固定版本和校验和见 `toolchain.lock.json`。

在仓库根目录执行：

```powershell
pwsh -File scripts/setup.ps1
pwsh -File scripts/build.ps1
pwsh -File scripts/dev.ps1
```

`setup` 下载并校验官方 Windows 模拟器及 Google ADB，安装到仓库内 `.tools/`。再次执行会复用下载缓存，不修改系统 PATH。只需模拟器时加 `-SdkOnly`；CI 使用该选项，不连接硬件。

`build` 用官方模拟器加载 Lua，执行启动和 50 次更新，检查退出状态及 240×240 PNG，再生成 `dist/ricestep.lua`、`dist/preview.png` 与构建信息。Lua 是解释执行，构建产物仍是 Lua 源码。

`dev` 打开交互模拟器，编辑 `app/main.lua` 并保存即可热重载。点击画面后按空格增加按键计数，Ctrl+R 重载清零。VS Code 也可通过“运行任务”调用这三个命令，Ctrl+Shift+B 执行构建。

连接支持 ADB 的开发板后，检查连接：

```powershell
pwsh -File scripts/devices.ps1
```

设备列表为空时，模拟器开发仍可继续。请检查 USB 数据线、实际板型与设备调试状态。此命令只列出连接设备，不上传小应用。

后续实机上传命令及当前验收情况见 [设备联调](docs/device-setup.md)。

## 文件说明

| 路径 | 用途 |
| --- | --- |
| `app/main.lua` | 最小可运行入口 |
| `scripts/` | 安装、构建和启动命令 |
| `toolchain.lock.json` | 官方 SDK 版本、来源、校验和 |
| `docs/smoke-test.md` | 手动验收步骤与执行记录 |
| `.github/workflows/build.yml` | Windows 云端构建，保存 Lua 与预览产物 |
| `.tools/` / `.cache/` | 本机工具与下载缓存，不提交 |
| `dist/` | 构建输出，不提交 |

## 官方资料

- [固定版本源码](https://github.com/LISTENAI/LingClaw-SDK/tree/eff009e242cd748f1ba7a9cd3001b72b1de38298)
- [v0.1.0 官方发布包](https://github.com/LISTENAI/LingClaw-SDK/releases/tag/v0.1.0)
- [模拟器与无界面运行](https://github.com/LISTENAI/LingClaw-SDK/blob/eff009e242cd748f1ba7a9cd3001b72b1de38298/docs/simulator.md)
- [设备联调说明](https://github.com/LISTENAI/LingClaw-SDK/blob/eff009e242cd748f1ba7a9cd3001b72b1de38298/docs/getting-started.md)

发布包包含 Lua 运行时、示例和 API 文档，无需安装 Rust 或 WSL。只有修改模拟器本身时，才需要官方源码构建环境。SDK API 版本与设备固件版本是不同编号。

目前还没有真实硬件计步、设备上传或固件刷写结果。具体板型与传感器接口后续单独确认。
