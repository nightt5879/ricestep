# Mini3 原生固件构建准备

核实日期：2026-10-29。原生 Windows 工具链和未修改 Mini3 基线已编译通过；本页记录环境准备与构建，没有操作设备、打开摄像头或刷机。

## 当前结果

- 已完整 checkout `.cache/native-mini3-sdk`，固定提交 `c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35`，detached HEAD；下述基线在应用独立版 overlay 之前构建。
- 来源：[Shinku-Chen/listenai_arcs_mini](https://github.com/Shinku-Chen/listenai_arcs_mini/tree/c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35)。这是个人维护的公开快照，尚未与官方仓库独立比对。
- 已阅读根目录及 SDK 的 `AGENTS.md`、`sdk-assistant-agent` 构建指南、Windows `env.ps1`、两层 `build.ps1`、工具链 CMake 配置和镜像同步脚本。
- 树中没有 Git submodule 项，依赖目录已经展开在镜像里；`git submodule status` 无输出。
- 两个官方 Windows 工具归档已下载，大小、SHA-256、ZIP CRC 和路径检查通过；实际执行 GCC 为 **14.2.1**，CMake 为 **3.29.0**。固定输入见 `toolchain.native.lock.json`。
- 使用短路径 `R:/native-mini3-sdk` 和 `R:/native-listenai-tools` 后，**Mini3 基线 1458 个构建目标完成**，输出目录为 `.cache/native-mini3-sdk/build-mini3-baseline-short/`。
- CP `arcs-mini.bin` 为 **3,112,324 字节**，SHA-256 `7a5094e7df9145c7da20ed288a5a0c7c876d472b88b4050713e1e6b6add69405`；同时生成 extensionless ELF `arcs-mini`、`.config`、HEX、LPK 和 OTA 包。此基线目录没有 `.map` 文件，资源审计仍需显式生成链接 map。
- 基线 `.config` 确认 `CONFIG_SOC_LS2663=y`、`CONFIG_BOARD_ARCS_MINI3=y`、`CONFIG_PSRAM_HEAP_SIZE=0x370000`，且 `CONFIG_MINIAPP` 未启用。
- 没有修改系统 PATH、全局环境变量、防火墙或固件；所有设置仅用于当前 PowerShell 进程。

## 镜像的重要限制

镜像根 `AGENTS.md` 和 `.github/scripts/sync-upstream.ps1` 明确说明：同步时删除了 `arcs-sdk/samples/`，并将 `apps/arcs-mini/prj.conf` 改为 `CONFIG_MINIAPP=n`。小应用运行时依赖 `samples/subsys` 的源码。

因此下面命令复现的是**该镜像未修改的 Mini3 基线**，不是已验证等价于原厂 3.1.0 的完整固件。编译成功不能直接替换当前运行肥鱼 Lua 的固件；独立版需补齐小应用依赖并重新构建验证。镜像 commit 自称同步官方 `23fca91e`，当前设备原厂包名为 `g0d199e4b`，二者没有完成源码/二进制对应验证。基线版本是 **3.0.3**；其随附 AP 为 **427868 字节**，原厂 3.1.0 的 AP 为 **438380 字节**，不能把两个包的 AP/CP 混用视作已经验证。

SDK `LICENSE` 为 Apache-2.0，板级 C 文件标注 LISTENAI / Apache-2.0。顶层 README 指示按各模块许可证处理，并没有统一许可全部资源。本次仅放入忽略的研究缓存。

## 可复现的 Windows 命令

以下命令在 RiceStep 仓库根目录执行。现有 checkout 已完成，无需重复 clone：

```powershell
gh repo clone Shinku-Chen/listenai_arcs_mini .cache/native-mini3-sdk -- --filter=blob:none --no-checkout --depth 1 --branch master
git -C .cache/native-mini3-sdk fetch --depth 1 origin c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35
git -C .cache/native-mini3-sdk -c core.longpaths=true checkout --detach c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35
git -C .cache/native-mini3-sdk rev-parse HEAD
```

将工具包安装位置限制在项目缓存。PowerShell 7.4+ 下先验证或安装固定归档：

```powershell
pwsh -File scripts/setup-native.ps1
# 只验证归档和已安装的关键工具文件、全部 Lua 源文件，不下载、不执行工具：
pwsh -File scripts/setup-native.ps1 -CheckOnly
# 只验证三个归档的大小和 SHA-256：
pwsh -File scripts/setup-native.ps1 -VerifyArchivesOnly
```

安装脚本复用已核验归档，只下载缺失文件；下载有重试上限且保留 TLS 证书校验，解压前必须通过大小和 SHA-256。工具和 Lua 均留在 `.cache`；不写全局 PATH，不安装系统组件。`-CheckOnly` 已在本机通过。

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

### Windows 长路径与命令行限制

直接从完整仓库路径构建时，曾出现超过 32 KiB 的编译器命令行导致 `CreateProcess` 失败，且部分对象路径超过 250 字符。仅设置 Git `core.longpaths` 或 Windows 长路径开关无法解除进程命令行长度限制。该 SDK 自带 `env.ps1` 仅在空格、非 ASCII 或 OneDrive 路径条件下尝试映射，不能自动解决本仓路径过长的问题。

已通过的基线使用现有任务建立的 `R:` → 本仓 `.cache` 映射；以下命令仅说明这次构建的短路径参数。不要覆盖其他任务的 `R:` 映射：

```powershell
$env:LISTENAI_HOME = 'R:/native-listenai-tools'
Set-Location R:/native-mini3-sdk
.\arcs-sdk\env.ps1 check
.\build.ps1 -S .\apps\arcs-mini -B build-mini3-baseline-short -DBOARD=arcs_mini3
```

独立版入口为仓库的 `scripts/build-standalone.ps1`：不传路径时，它为本仓 `.cache` 分配一个当前未占用的盘符，并在退出时只清理本次创建且目标未变化的映射。既有映射不会被占用、改写或移除；进程环境变量会恢复。自动映射逻辑已做语法和只读映射解析检查，其实际构建结果以独立版记录为准。

```powershell
# 自动选择空闲短路径，应用独立版 overlay 并编译；不刷写：
pwsh -File scripts/build-standalone.ps1

# 或同时传入三个已有短路径；调用者自行管理映射生命周期：
pwsh -File scripts/build-standalone.ps1 -SdkRoot R:/native-mini3-sdk -ToolsRoot R:/native-listenai-tools -LuaSource R:/lua-5.4.8/src
```

自定义路径必须全部给出，且不超过 40 字符、不含空格/非 ASCII/OneDrive；否则给出明确错误。构建目录默认采用 `build-standalone-<路径摘要>`，摘要包含 SDK、工具与 Lua 的实际短路径。空闲盘符变化会产生独立缓存，避免让 CMake 重用另一盘符的绝对路径；重用相同路径仍使用相同缓存。脚本不删除旧缓存。已有相同路径的构建可用 `-BuildName build-mini3-standalone` 复用；此显式短路径方式已完成编译、验证与导出。

构建后脚本调用 `verify-native-image.cjs`，检查真实 ELF 的 ROM 段、符号、启动复制/清零表和最终 `.bin`。14 段音频须与仓库 MP3 逐字节一致，Lua 须匹配 overlay 清单。检查通过后导出 `dist/native/standalone/arcs-mini3-ricestep.bin`、`.elf`、`.config`、构建信息和静态验证报告。SDK 原始 ELF 文件名是无扩展名的 `arcs-mini`，当前默认构建不产出 `.map`。

不使用 `-C`，避免不必要清理。SDK 根构建脚本调用固定工具包内 CMake/Ninja，不使用系统已有 CMake，并加入本仓 `boards/` 搜索路径。

当前 Mini3 板型选择 `SOC_LS2663`，应用 overlay 为 CP 分配 5 MiB PSRAM。后续成功构建必须检查生成 `.config` 中的板型、SoC、内存、camera 和小应用配置，不能只检查 `.bin` 是否存在。

## 下载来源与已解决的网络阻塞

`arcs-sdk/env.ps1` 声明的 Windows 官方包：

- [ListenAI tools v0.1.0](https://listenai-firmware-delivery.oss-cn-beijing.aliyuncs.com/ARCS/tools/dev-tools/windows-amd64/v0.1.0/listenai-tools-windows-v0.1.0.zip)
- [Nuclei GCC 2025.10 slim](https://listenai-firmware-delivery.oss-cn-beijing.aliyuncs.com/ARCS/tools/toolchain/windows-amd64/nuclei_riscv_newlibc_prebuilt_win64_2025.10_slim.zip)

最初下载受间歇性 TLS EOF/握手失败阻塞。后来以 HTTP/1.1、有界重试和断点续传成功取回两个官方包，未关闭证书校验，也没有更改全局代理。归档长度与 SHA-256 已锁定；后续安装应走 `setup-native.ps1`，不使用未经校验的替代包。

已核查的官方替代发布入口：

- [Nuclei 2025.10 release](https://github.com/riscv-mcu/riscv-gnu-toolchain/releases/tag/nuclei-2025.10)：GitHub release 没有附件，正文把预编译下载指向 Nuclei 官网。
- [LISTENAI Nuclei 2025.10 r1](https://github.com/LISTENAI/riscv-gnu-toolchain/releases/tag/listenai/nuclei-2025.10/r1)：只有 macOS arm64/x86_64 附件，不能用作 Windows 工具链。

最终使用 SDK 声明的 Windows 官方包完成构建；没有拼装不同版本的交叉编译器，没有在 WSL 中安装或运行替代固件构建环境。

## 恢复与后续验证

已保存并核验的 Mini3 3.1.0 恢复包详见 [恢复准备](native-recovery.md)。基线和独立版均已编译成功，独立版的 ELF/二进制资源与 Flash 放置检查通过。AP/CP 跨版本兼容性、实际启动、离线声音和板上存档仍待设备验证；按用户要求今晚停在实机演示前，不刷写、不播放。独立版实现和验证边界见 [独立运行版](standalone-device.md)。当前没有完成任何原生摄像头取帧或模型识别验证。
