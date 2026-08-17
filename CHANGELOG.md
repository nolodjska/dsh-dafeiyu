# Changelog

## Unreleased

- DSH 设置页新增「桌宠」导航页：立绘、启用拨纽（switch）、动作循环照片墙
- 照片墙由 `pet-manifest.json` 的 `photoWall` 契约驱动，支持热重载与二次开发
- 每个动作循环一行缩略图，带「打开文件夹」按钮（`workspaces.openPath`）
- 「启用大肥鱼」复选框改为拨纽
- 工作坐姿帧补偿性放大（clip `scale` 契约，默认 1.0）
- 提问工具（ask_user_question）触发 question/answer 表情（各帧 0.8s）

## 0.1.0-alpha.6

First public Windows Alpha of DSH BigFish / DSH 大肥鱼.

### Highlights

- Native transparent, frameless, always-on-top Windows companion owned by DSH
- Real DSH session states: idle, thinking, working, waiting, success, and error
- Project status card with project directory, current phase, active todo, and real todo progress
- Friendly Simplified Chinese status copy and 49-frame character runtime
- DSH WebUI settings for enable/disable, scale, activity, reduced motion, and subagents
- Helper heartbeat, crash restart, snapshot replay, and automatic exit with the DSH Host
- Bilingual Chinese/English GitHub documentation

### Install the Alpha

```powershell
dsh plugin --profile web add dsh-dafeiyu@alpha
```

If DSH is installed locally rather than globally:

```powershell
pnpm exec dsh plugin --profile web add dsh-dafeiyu@alpha
```

### Current limitations

- Windows 10/11 x64 only
- Settings and desktop status copy are currently Simplified Chinese
- Numeric progress requires a structured todo list from DSH
- Community Electron clients are not part of the supported compatibility scope

Code is MIT-licensed. Bundled character artwork has separate terms documented in
[ASSET_LICENSE.md](ASSET_LICENSE.md). This is an unofficial fan-made project and is not
affiliated with or endorsed by DeepSeek.
