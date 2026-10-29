# Mini3 原厂恢复材料

状态：**仅完成离线准备，没有刷机、连接设备或播放声音**。用户要求今晚停在物理演示之前；本准备脚本只处理本地文件，不运行 ADB、串口工具或网络请求。

## 准备与复核

在仓库根目录执行：

```powershell
pwsh -File scripts/prepare-native-recovery.ps1
pwsh -File scripts/prepare-native-recovery.ps1 -CheckOnly
```

默认输入为工作区 `../../work/lisem/firmware/arcs-mini3-v3.1.0-0-g0d199e4b.lpk`。移动过该包时可用 `-PackagePath` 指向另一份文件，但仍要求相同大小与 SHA-256；脚本不会接受别的固件版本，也不下载缺失包。

固定包为 **4,965,689 字节**，SHA-256 `6092464a094def4bab3dae01ce40c7f44dca1eb72a91e7de75e80292a6efe07e`。格式来自已审阅的 SDK `tools/make_package.py:129–157`：ZIP 内 `manifest.json` 使用 `manifest: 2`、`chip: arcs`，镜像字段为 `name/addr/file/md5`，路径形如 `./ap.bin`。脚本先校验完整包，再校验七个镜像的 MD5、大小、固定地址及安全文件名，全部通过后才写文件；拒绝链接、重复 ZIP 名称及未知已有输出覆盖。

输出全部位于被 Git 忽略的 `.cache/stock-recovery-mini3/`：原始 manifest、七个原厂二进制、`partition_table.json` 和 `verification.json`。准备后会重新读取输出并校验 SHA-256；`-CheckOnly` 不写文件，验证包与所有输出。包内 `boot.bin` 被保留为恢复材料，**不列入刷写分区表**。

| 分区 | 地址 | 原厂字节数 |
| --- | --- | ---: |
| ap | `0x040000` | 438380 |
| tone | `0x100000` | 351232 |
| wake_word | `0x200000` | 1295360 |
| emoji | `0x380000` | 492544 |
| respak | `0x440000` | 983040 |
| app / CP | `0x600000` | 3346892 |

生成表只含以上六项，`file` 均相对表所在目录，`tags` 均为 `["lpk"]`；这与 SDK `adb_download.ps1:194–246` 的 default 模式解析一致。表中没有 boot、boot_control、OTA、KV 条目，也不含开发版 CP 路径或 `${BUILD_DIR}` 替换。它准备的是原厂恢复输入，不是可执行刷写程序。

## 开发固件的兼容性边界

原厂 3.1.0 的 AP 为 438380 字节，公开快照 3.0.3 的 AP 为 427868 字节，唤醒词镜像也不同。两套 boot、tone、emoji、respak 逐字节相同；boot 的 MD5 为 `a5f362e0c5de69ee8d9684681ad9df92`。

新旧 CP 在偏移 `0x180` 都记录 PSRAM 基址 `0x28300000`、大小 `0x00500000`，与 SDK `apps/firmware_compat.h:6`、`apps/arcs-mini/firmware_compat.c:6–18` 的契约一致。但 LSF IPC 依靠固定 endpoint/service ID，连接握手没有版本协商（`arcs-sdk/components/ipclsf/ic_message/ic_message.c:41–55,146–211`）；公开快照不含完整 `apps/remote-ap/` 源码。

所以 CP-only 是尚待物理验证的兼容性试验，不能因为编译通过就认定能与原厂 AP 混用。另一条试验路线是使用快照配套 AP、CP 和唤醒词资源，保留相同的 boot 和用户 KV：它避免跨版本 AP/CP 混用，但扩大了写入范围，也不能据此保证硬件功能正确。两种路线都还没有在板上验证，尚未选定实际部署方案；后续恢复使用本目录的六个原厂镜像。

## 之后实际恢复前需要核对

- 使用唯一目标开发板，核对正常 ADB 的芯片 ID、Mini3 板型和 USB 关联。上游脚本会枚举全部在线设备，没有单设备参数；`MAX_DEVICES=1` 可让多设备情况失败退出，仍需确认唯一设备身份。
- 上游脚本接受分区表目录，不直接接受 LPK。未来实际恢复使用该目录和 **default 模式**，不能误用 app 模式只恢复 CP；本页和准备脚本均不执行这一操作。
- 正常设备进入 recovery 后 serial 会变为 `BOOT-*`。上游脚本发送去掉 `BOOT-` 的后缀作为握手，等待 3500ms，然后逐镜像 push；最后执行 `recovery exit` 和 `reboot hard`。参考 `adb_download.ps1:570–599,682–727`。
- 上游板型检查（`adb_download.ps1:406–432`）依赖资源目录名，且没有排除 BOOT 设备。当前恢复目录名不会触发其内置板型匹配，因此不能把它当成目标身份保证；执行前必须独立核对唯一设备和恢复前后的 USB 关联。不要为了绕过检查而忽略不一致的设备身份。
- push 返回成功不等于 Flash 内容回读通过；上游脚本没有执行后者。真正恢复后还需检查恢复日志、正常启动、固件版本和原厂功能。

如果开发固件失效但仍能进入 BOOT recovery，可继续走原厂 boot 提供的 USB 恢复。若连 BOOT 都不能枚举，则需要现场按 Mini3 图示接串口、进入 ROM 下载模式并通过 cskburn desktop 使用完整原厂 LPK；这不是能承诺远程自动完成的步骤。入口见 [Mini3 串口恢复教程](https://docs2.listenai.com/x/uaiwdDObz) 与现有 [摄像头调查的恢复记录](camera-research.md)。不要按 Mini2 图片接线，也不要将镜像技能中旧板型的临时开发 boot 盲目写入 Mini3。
