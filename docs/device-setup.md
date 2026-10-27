# ADB 与设备联调

本机工具已按官方来源准备，具体板型、固件和传感器仍待实物确认。环境安装、模拟器运行不依赖开发板在线。

## 工具来源

- Android Platform Tools `37.0.1`，Google 官方稳定版本。
- [官方发布说明](https://developer.android.com/tools/releases/platform-tools)。
- [官方仓库元数据](https://dl-ssl.google.com/android/repository/repository2-1.xml)给出 Windows 包 SHA-1；已与下载文件核对。
- 锁文件另记录本次验证文件的 SHA-256，后续安装同时核验两者。
- `dl.google.com` 在本机 TLS 连接失败，采用可访问的 Google 官方 `dl-ssl.google.com` 下载域名，全程保留 TLS 校验。

运行 `pwsh -File scripts/setup.ps1` 后，工具位于 `.tools/platform-tools/`。无需修改系统 PATH。

## 连接检查

```powershell
pwsh -File scripts/devices.ps1
```

仅当目标设备实际出现在列表且状态为 `device`，才继续硬件联调。多个设备连接时应明确指定目标序列号。不要把个人设备序列号或拉取的用户数据提交到 Git。

## 以后上传小应用

以下命令是未来手动操作说明，本轮环境安装不执行上传。官方文档说明，`adb push` 会立即运行上传的 Lua 并替换当前应用；调试源码在设备重启后需要重新上传。

```powershell
pwsh -File scripts/build.ps1
if ($LASTEXITCODE -ne 0) { throw 'Build failed; stop before device upload.' }
# 把 YOUR_BOARD_SERIAL 替换为已核对的开发板序列号。
& ./.tools/platform-tools/adb.exe -s YOUR_BOARD_SERIAL push ./dist/ricestep.lua /miniapp/ricestep.lua
```

如需保留正在运行的小应用，先按官方文档拉取到一个尚不存在的本地文件名。这个仓库忽略 `device-app*.lua`，避免把设备上的既有用户内容意外提交。

不使用 fastboot、不刷写底层固件、不改变设备权限。硬件计步要单独确认传感器及固件到 Lua 的数据通路。

## 本轮验证记录

2026-10-27：官方包完整性校验通过；`adb version` 返回 `37.0.1-15733141`；`adb devices -l` 正常执行，列表为空。本轮没有检测到已连接设备，未执行实机上传或刷写。
