# Mini3 原生固件构建准备

核实日期：2026-10-29。本轮只做本地源码/脚本检查与构建尝试，没有操作设备、打开摄像头或刷机。

## 当前结果

- 已完整 checkout `.cache/native-mini3-sdk`，固定提交 `c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35`，detached HEAD，工作区干净。
- 来源：[Shinku-Chen/listenai_arcs_mini](https://github.com/Shinku-Chen/listenai_arcs_mini/tree/c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35)。这是个人维护的公开快照，尚未与官方仓库独立比对。
- 已阅读根目录及 SDK 的 `AGENTS.md`、`sdk-assistant-agent` 构建指南、Windows `env.ps1`、两层 `build.ps1`、工具链 CMake 配置和镜像同步脚本。
- 树中没有 Git submodule 项，依赖目录已经展开在镜像里；`git submodule status` 无输出。
- 原生 Windows 构建命令已实际执行，**在调用 CMake 之前失败**：预期的 ListenAI 工具包尚未安装，RISC-V GCC 也缺失。没有生成 `.config`、ELF 或固件。
- 没有修改系统 PATH、全局环境变量、防火墙或固件；所有设置仅用于当前 PowerShell 进程。

## 镜像的重要限制

镜像根 `AGENTS.md` 和 `.github/scripts/sync-upstream.ps1` 明确说明：同步时删除了 `arcs-sdk/samples/`，并将 `apps/arcs-mini/prj.conf` 改为 `CONFIG_MINIAPP=n`。小应用运行时依赖 `samples/subsys` 的源码。

因此下面命令复现的是**该镜像未修改的 Mini3 基线**，不是已验证等价于原厂 3.1.0 的完整固件。即使它编译成功，也不能直接替换当前运行肥鱼 Lua 的固件；必须先补齐匹配版本的小应用依赖，并重新构建验证。镜像 commit 自称同步官方 `23fca91e`，当前设备原厂包名为 `g0d199e4b`，二者没有完成源码/二进制对应验证。

SDK `LICENSE` 为 Apache-2.0，板级 C 文件标注 LISTENAI / Apache-2.0。顶层 README 指示按各模块许可证处理，并没有统一许可全部资源。本次仅放入忽略的研究缓存。

## 可复现的 Windows 命令

以下命令在 RiceStep 仓库根目录执行。现有 checkout 已完成，无需重复 clone：

```powershell
gh repo clone Shinku-Chen/listenai_arcs_mini .cache/native-mini3-sdk -- --filter=blob:none --no-checkout --depth 1 --branch master
git -C .cache/native-mini3-sdk fetch --depth 1 origin c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35
git -C .cache/native-mini3-sdk -c core.longpaths=true checkout --detach c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35
git -C .cache/native-mini3-sdk rev-parse HEAD
```

将工具包安装位置限制在项目缓存，并检查环境：

```powershell
$projectRoot = (Get-Location).Path
$env:LISTENAI_HOME = Join-Path $projectRoot '.cache/native-listenai-tools'
Set-Location (Join-Path $projectRoot '.cache/native-mini3-sdk')
.\arcs-sdk\env.ps1 check
```

本次检查报告：

```text
GCC toolchain not found: .../.cache/native-listenai-tools/gcc
listenai-tools not found: .../.cache/native-listenai-tools/listenai-tools
```

脚本要求的工具布局：

```text
.cache/native-listenai-tools/
  gcc/bin/riscv64-unknown-elf-gcc.exe
  listenai-tools/VERSION                         # v0.1.0
  listenai-tools/cmake/bin/cmake.exe
  listenai-tools/ninja/ninja.exe
  listenai-tools/kconfig/kconfig.py
  listenai-tools/menuconfig/menuconfig.py
```

网络恢复后，已审阅的标准安装入口为 `.\arcs-sdk\env.ps1`（无参数）；它会按需下载两个官方包并解压到 `LISTENAI_HOME`。它不需要管理员权限，但会临时使用系统 TEMP 保存 ZIP。当前工作区路径不含空格/中文，不触发脚本的 `subst R:` 路径映射。

安装成功并通过 `check` 后，CP 构建命令如下；这正是本次已尝试的命令：

```powershell
.\build.ps1 -S .\apps\arcs-mini -B build-mini3-baseline -DBOARD=arcs_mini3
```

不使用 `-C`，避免不必要清理。根构建脚本调用工具包内 CMake/Ninja，不使用系统已有 CMake。它会加入本仓 `boards/` 搜索路径并识别 SDK 内 `arcs_mini3`。本次在 `build.ps1:170` 因工具包内 `cmake.exe` 缺失退出，尚未进入编译器配置阶段。

当前 Mini3 板型选择 `SOC_LS2663`，应用 overlay 为 CP 分配 5 MiB PSRAM。后续成功构建必须检查生成 `.config` 中的板型、SoC、内存、camera 和小应用配置，不能只检查 `.bin` 是否存在。

## 本次工具下载阻塞

`arcs-sdk/env.ps1` 声明的 Windows 官方包：

- [ListenAI tools v0.1.0](https://listenai-firmware-delivery.oss-cn-beijing.aliyuncs.com/ARCS/tools/dev-tools/windows-amd64/v0.1.0/listenai-tools-windows-v0.1.0.zip)
- [Nuclei GCC 2025.10 slim](https://listenai-firmware-delivery.oss-cn-beijing.aliyuncs.com/ARCS/tools/toolchain/windows-amd64/nuclei_riscv_newlibc_prebuilt_win64_2025.10_slim.zip)

对工具包地址执行了一次有 20 秒上限的 HTTPS HEAD 请求，实际约 7 秒后失败：`schannel: failed to receive handshake, SSL/TLS connection failed`。没有反复下载，没有关闭证书校验。

已核查的官方替代发布入口：

- [Nuclei 2025.10 release](https://github.com/riscv-mcu/riscv-gnu-toolchain/releases/tag/nuclei-2025.10)：GitHub release 没有附件，正文把预编译下载指向 Nuclei 官网。
- [LISTENAI Nuclei 2025.10 r1](https://github.com/LISTENAI/riscv-gnu-toolchain/releases/tag/listenai/nuclei-2025.10/r1)：只有 macOS arm64/x86_64 附件，不能用作 Windows 工具链。

这轮没有启动新的长时间替代工具链下载或自行拼装版本不匹配的编译环境。系统已有 CMake、Python、PowerShell，默认 WSL 为 Ubuntu-24.04 / WSL2；未安装或修改 WSL 工具链，也未在 WSL 中开始构建。

## 恢复与后续验证

已保存并核验的 Mini3 3.1.0 恢复包详见 [摄像头调查](camera-research.md)。原生构建成功与可安全上板是两步：先补齐依赖并得到可重复产物，再比较 Mini3 AP/CP 资源、分区与现有功能，之后才进入设备验证。当前没有完成任何原生摄像头取帧或模型识别验证。
