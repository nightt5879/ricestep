# 开发环境手动验收

此入口只验证 Lua 载入、绘图、按键回调与重载。屏幕显示的是 **按键点击次数**，没有计步、传感器、米饭或探索逻辑，也不执行网络请求。

## 前置条件

- 使用官方 LingClaw SDK 模拟器。
- 选择 `Mini` 设备预设，确认画布为 **240 × 240**，存在 ID 为 `function` 的按键。
- 模拟器当前配置须提供 **Lua API 2 或更高版本**。官方基础绘图、输入和生命周期文档的最低版本都是 API 2；这里不声称兼容 API 1。
- 入口为仓库中的 `app/main.lua`。

## 操作与预期结果

1. 启动模拟器，点击“打开 Lua”，选择 `app/main.lua`。
   - 画面出现 `RiceStep`、`SDK Ready`、`Button presses: 0`。
   - 下方出现 `Space / function: +1`、`Ctrl+R: reset`、`Not step tracking`。
   - 全部文字在 240 × 240 画布内，无 Lua 错误。
2. 点击预览画面取得键盘焦点，**短按并释放一次空格键**。
   - 计数从 `0` 变为 `1`，不会因后续静止等待而增长。
3. 再分别短按并释放两次空格键。
   - 计数依次为 `2`、`3`，每次点击只增加一次。
4. 点击模拟器预览下方的 `function` 按键。
   - 计数变为 `4`。若界面采用显示名称，选择设备配置中 ID 为 `function` 的对应按钮。
5. 按 **Ctrl+R**，或点击工具栏的“重载”。
   - 应用重新启动，计数回到 `0`，仍显示 `SDK Ready`。
   - 这是模拟器重载，不需要假设开发板另有可供应用使用的复位按键。
6. 保持窗口静止数秒，随后再次短按空格。
   - 静止期间计数保持 `0`，按键释放后变为 `1`。
7. 打开“事件”查看是否有 Lua 异常；可用相机按钮保存验收截图。
   - 不应出现异常。该应用使用 6 段文本和 1 个矩形，低于官方每帧 8 段文本和 128 个矩形的限制。

如果空格没有反应，先点击预览取得焦点，再检查设备的“按键映射”。不要把系统保留的长按行为当作应用短按事件测试。

## 验收记录

2026-10-27，本机 Windows x64、官方 SDK v0.1.0、默认 Mini / API 4 配置：

- [x] 官方无界面运行返回 0，生成并目视核查 240 × 240 PNG。
- [x] 实际窗口启动、六段文本及画布边界正常。
- [x] 聚焦画布后空格短按，计数由 0 变 1。
- [x] 鼠标点击模拟器功能键，计数由 0 变 1。
- [x] Ctrl+R 后计数恢复 0。
- [x] 修改入口文件的注释并保存，自动热重载后由 1 恢复 0。
- [ ] 物理开发板输入与运行：尚未执行。

本机构建预览位于 `dist/preview.png`；CI 也保存构建预览 artifact。桌面交互由实际窗口检查，尚未建立 GUI 自动回归测试。

无界面截图验证只能证明启动和绘图路径；GUI 按键及真机验证需要单独执行。本应用未保存计数，重载清零是预期行为。

## 应用格式与官方依据

官方上手流程直接打开或上传单个 `.lua` 文件，`examples/counter.lua` 也没有应用 manifest。因此这里仅提供 `app/main.lua`，不添加未经官方定义的 manifest。API 版本属于模拟器／设备 capabilities 配置中的 `runtime.lua_sdk.version`，不是 Lua 文件中的声明字段。

以下链接固定到此次查阅的官方提交 `9f43fc08c6caaa7d67c53a841a2e56209f39a7d0`：

- [官方计数器示例](https://github.com/LISTENAI/LingClaw-SDK/blob/9f43fc08c6caaa7d67c53a841a2e56209f39a7d0/examples/counter.lua)
- [app](https://github.com/LISTENAI/LingClaw-SDK/blob/9f43fc08c6caaa7d67c53a841a2e56209f39a7d0/docs/api/app.md)、[screen](https://github.com/LISTENAI/LingClaw-SDK/blob/9f43fc08c6caaa7d67c53a841a2e56209f39a7d0/docs/api/screen.md)、[input](https://github.com/LISTENAI/LingClaw-SDK/blob/9f43fc08c6caaa7d67c53a841a2e56209f39a7d0/docs/api/input.md)、[lifecycle](https://github.com/LISTENAI/LingClaw-SDK/blob/9f43fc08c6caaa7d67c53a841a2e56209f39a7d0/docs/api/lifecycle.md)
- [上手指南](https://github.com/LISTENAI/LingClaw-SDK/blob/9f43fc08c6caaa7d67c53a841a2e56209f39a7d0/docs/getting-started.md)
- [模拟器使用](https://github.com/LISTENAI/LingClaw-SDK/blob/9f43fc08c6caaa7d67c53a841a2e56209f39a7d0/docs/simulator.md)
