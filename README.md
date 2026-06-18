# APK Split 提取工具

一个跨平台桌面工具，用于从已连接并授权的 Android 设备中扫描全部已安装应用，展示应用图标、包名和 base APK 路径，选择目标应用后提取全部 split APK，并打包为 `.apks` 或 `.xapk`。

## 功能

- 一键扫描连接设备里的全部已安装包。
- 列表展示应用图标、包名、应用名和 base APK 路径。
- 支持输入关键词在本地过滤应用名或包名。
- 选择目标包名后，通过 `pm path <package>` 查询所有 APK 路径。
- 使用 `adb pull` 提取 `base.apk` 和全部 split APK 到本地。
- 支持打包为 `.apks` 或 `.xapk`。
- XAPK 的 `manifest.json` 会自动从设备 `dumpsys package` 检测版本和 SDK 信息。
- 如果本机安装了 `aapt`，会进一步从 `base.apk` 检测应用名称、版本信息和图标资源。
- 支持 Windows 和 Linux。

## 图标识别说明

图标来自每个应用的 `base.apk`。工具会优先使用本机 `aapt dump badging` 定位图标资源；如果没有安装 `aapt`，会按常见 launcher 图标命名从 APK 内兜底查找 PNG/GIF 图标。

以下情况会显示占位图标，但不影响选择、提取和打包：

- 应用图标是 XML adaptive icon。
- 应用图标是当前 Tk 无法直接显示的格式，例如 WebP。
- 某些系统包不是标准 APK，或者 base APK 无法被 `adb pull` 读取。

首次扫描会为图标读取缓存部分 base APK 到系统临时目录，后续扫描会复用缓存。

## 环境要求

- Android 设备已开启 USB 调试并完成授权。
- 本机已安装 Android Platform Tools，且 `adb` 在 `PATH` 中可用。
- 从源码运行需要 Python 3.10 或更高版本。
- 可选：安装 Android SDK Build Tools 并把 `aapt` 加入 `PATH`，用于显示更多图标和生成更完整的 XAPK manifest。

## 从源码运行

```bash
python -m apk_extract_gui
```

## 使用方式

1. 连接并授权 Android 设备。
2. 启动工具。
3. 点击“扫描全部”。
4. 如需缩小范围，在“过滤关键词”中输入应用名或包名片段。
5. 在应用列表中选择目标包名。
6. 用滑动按钮选择 `APKS` 或 `XAPK`。
7. 选择输出目录。
8. 点击“提取并打包”。

生成文件会放在：

```text
<输出目录>/<包名>_<时间戳>/
```

其中 `apk/` 目录保存拉取到的原始 APK 文件，根目录保存最终的 `.apks` 或 `.xapk` 文件。

## GitHub Actions 自动打包

仓库内置 `.github/workflows/build.yml`：

- 每次 push 和 pull request 会运行测试。
- Windows 会生成 `apk-extract-gui.exe` 并打包为 zip。
- Linux 会生成 `apk-extract-gui` 单文件可执行程序并打包为 tar.gz。
- 推送 `v*` 标签时会自动创建 GitHub Release 并上传构建产物。

发布示例：

```bash
git tag v0.1.0
git push origin v0.1.0
```

## 本地打包

```bash
python -m pip install -r requirements-dev.txt
pyinstaller --noconfirm --clean --onefile --windowed --name apk-extract-gui apk_extract_gui/__main__.py
```

构建结果在 `dist/` 目录中。

## 测试

```bash
python -m unittest discover -s tests
```
