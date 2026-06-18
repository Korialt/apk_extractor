# APK Split Extractor

跨平台桌面工具，用于从已连接并授权的 Android 设备中扫描第三方用户安装应用，展示应用图标、应用名、包名和 base APK 路径，选择目标应用后提取全部 split APK，并打包为 `.apks` 或 `.xapk`。

界面使用 Tauri 2 + React + TypeScript + Rust。

## 功能

- 一键扫描连接设备里的第三方用户安装包，排除系统、预装和系统分区应用。
- 列表展示应用图标、应用名、包名和 base APK 路径。
- 支持输入关键词过滤应用名、包名或 APK 路径。
- 选择目标应用后，通过 `pm path <package>` 查询所有 APK 路径。
- 使用 `adb pull` 提取 `base.apk` 和全部 split APK 到本地。
- 支持打包为 `.apks` 或 `.xapk`。
- XAPK 的 `manifest.json` 会自动从设备 `dumpsys package` 检测版本和 SDK 信息。
- 如果本机安装了 `aapt`，会进一步从 `base.apk` 检测应用名称、版本信息和图标资源。
- 扫描、失败和完成信息只显示在界面日志中，不弹出错误或完成窗口。
- 支持 Windows 和 Linux。

## 应用图标

应用自身图标是本项目生成的自有图标资产，不复用其他项目图标。

## 图标识别说明

图标来自每个应用的 `base.apk`。工具会优先使用本机 `aapt dump badging` 定位图标资源；如果没有安装 `aapt`，会按常见 launcher 图标命名从 APK 内兜底查找 PNG/GIF/JPG/WebP/SVG 图标。

以下情况会显示占位图标，但不影响选择、提取和打包：

- 应用图标是 Android XML adaptive icon。
- APK 中没有可直接由浏览器显示的图片资源。
- 某些系统包不是标准 APK，或者 base APK 无法被 `adb pull` 读取。

首次扫描会为图标读取缓存部分 base APK 到系统临时目录，后续扫描会复用缓存。

## 环境要求

- Android 设备已开启 USB 调试并完成授权。
- 本机已安装 Android Platform Tools，且 `adb` 在 `PATH` 中可用。
- 可选：安装 Android SDK Build Tools 并把 `aapt` 加入 `PATH`，用于显示更多图标和生成更完整的 XAPK manifest。
- 从源码运行需要 Node.js 20、Rust stable 和 Tauri 2 所需系统依赖。

Linux 开发环境通常还需要：

```bash
sudo apt-get install -y libwebkit2gtk-4.1-dev libappindicator3-dev librsvg2-dev patchelf
```

## 从源码运行

```bash
npm install
npm run dev
```

## 使用方式

1. 连接并授权 Android 设备。
2. 启动工具。
3. 点击“扫描第三方应用”。
4. 如需缩小范围，在过滤框中输入应用名、包名或 APK 路径片段。
5. 在应用列表中选择目标应用。
6. 用 APKS/XAPK 滑动式按钮选择输出格式。
7. 选择或输入输出目录。
8. 点击“提取并打包”。

生成文件会放在：

```text
<输出目录>/<包名>_<时间戳>/
```

其中 `apk/` 目录保存拉取到的原始 APK 文件，根目录保存最终的 `.apks` 或 `.xapk` 文件。

## GitHub Actions 自动打包

仓库内置 `.github/workflows/build.yml`：

- 每次 push 和 pull request 会运行 TypeScript 检查、Rust 检查和 Tauri 构建。
- Windows 会生成绿色版 `apk-extract-gui.exe`，并打包为 `apk-extract-gui-windows-portable-x86_64.zip`。
- Linux 会生成绿色版 `apk-extract-gui`，并打包为 `apk-extract-gui-linux-portable-x86_64.tar.gz`。
- 推送 `v*` 标签时会自动创建或更新 GitHub Release，并上传构建产物。

发布示例：

```bash
git tag v0.2.0
git push origin v0.2.0
```

## 本地构建

```bash
npm install
npm run build
```

本地构建会生成绿色版可执行文件，不生成安装包。

构建结果在：

```text
src-tauri/target/release/apk-extract-gui
src-tauri/target/release/apk-extract-gui.exe
```
