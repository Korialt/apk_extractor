from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess


class AdbError(RuntimeError):
    pass


@dataclass(frozen=True)
class Device:
    serial: str


@dataclass(frozen=True)
class InstalledApp:
    package_name: str
    base_apk_path: str


@dataclass(frozen=True)
class PackageInfo:
    package_name: str
    version_code: str | None
    version_name: str | None
    min_sdk_version: str | None
    target_sdk_version: str | None


@dataclass(frozen=True)
class AaptInfo:
    package_name: str
    label: str | None
    version_code: str | None
    version_name: str | None
    min_sdk_version: str | None
    target_sdk_version: str | None
    icon_resource: str | None = None


def require_adb() -> Path:
    adb_path = shutil.which("adb")
    if adb_path is None:
        raise AdbError("未找到 adb。请先安装 Android Platform Tools，并把 adb 加入 PATH。")
    return Path(adb_path)


def require_single_device(adb_path: Path) -> Device:
    output = run_command([str(adb_path), "devices"])
    devices = parse_adb_devices(output)
    if not devices:
        raise AdbError("未检测到已授权的 Android 设备。请连接设备，并确认 USB 调试授权。")
    if len(devices) > 1:
        serials = ", ".join(device.serial for device in devices)
        raise AdbError(f"检测到多个设备：{serials}。当前版本需要只连接一个设备。")
    return devices[0]


def list_installed_apps(adb_path: Path, device: Device) -> list[InstalledApp]:
    output = run_command([str(adb_path), "-s", device.serial, "shell", "pm", "list", "packages", "-f"])
    apps = parse_package_file_list(output)
    if not apps:
        raise AdbError("设备没有返回已安装应用列表。")
    return apps


def get_apk_paths(adb_path: Path, device: Device, package_name: str) -> list[str]:
    output = run_command([str(adb_path), "-s", device.serial, "shell", "pm", "path", package_name])
    paths = parse_pm_paths(output)
    if not paths:
        raise AdbError(f"未找到 {package_name} 的 APK 路径。")
    return paths


def pull_remote_file(adb_path: Path, device: Device, remote_path: str, local_path: Path) -> Path:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    run_command([str(adb_path), "-s", device.serial, "pull", remote_path, str(local_path)])
    if not local_path.is_file():
        raise AdbError(f"adb pull 未生成本地文件：{local_path}")
    return local_path


def pull_apks(adb_path: Path, device: Device, remote_paths: list[str], output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    local_paths: list[Path] = []
    used_names: set[str] = set()

    for remote_path in remote_paths:
        filename = Path(remote_path).name
        if filename in used_names:
            filename = f"{len(used_names) + 1}_{filename}"
        used_names.add(filename)

        local_path = output_dir / filename
        pull_remote_file(adb_path, device, remote_path, local_path)
        local_paths.append(local_path)

    return local_paths


def get_package_info(adb_path: Path, device: Device, package_name: str) -> PackageInfo:
    output = run_command([str(adb_path), "-s", device.serial, "shell", "dumpsys", "package", package_name])
    return parse_dumpsys_package_info(package_name, output)


def get_aapt_info(base_apk: Path) -> AaptInfo | None:
    aapt_path = shutil.which("aapt")
    if aapt_path is None:
        return None

    output = run_command([aapt_path, "dump", "badging", str(base_apk)])
    return parse_aapt_badging(output)


def run_command(args: list[str]) -> str:
    try:
        completed = subprocess.run(
            args,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise AdbError(f"命令不存在：{args[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout).strip()
        message = detail if detail else "命令执行失败但没有返回错误详情"
        raise AdbError(f"命令执行失败：{' '.join(args)}\n{message}") from exc

    return completed.stdout


def parse_adb_devices(output: str) -> list[Device]:
    devices: list[Device] = []
    for line in output.splitlines()[1:]:
        columns = line.strip().split()
        if len(columns) >= 2 and columns[1] == "device":
            devices.append(Device(serial=columns[0]))
    return devices


def parse_package_file_list(output: str) -> list[InstalledApp]:
    apps: list[InstalledApp] = []
    for line in output.splitlines():
        text = line.strip()
        if not text.startswith("package:"):
            continue

        value = text.removeprefix("package:")
        if "=" not in value:
            continue

        apk_path, package_name = value.rsplit("=", 1)
        if apk_path and package_name:
            apps.append(InstalledApp(package_name=package_name, base_apk_path=apk_path))
    return apps


def parse_pm_paths(output: str) -> list[str]:
    paths: list[str] = []
    for line in output.splitlines():
        text = line.strip()
        if text.startswith("package:"):
            paths.append(text.removeprefix("package:"))
    return paths


def parse_dumpsys_package_info(package_name: str, output: str) -> PackageInfo:
    version_code = _first_group(r"versionCode=(\S+)", output)
    version_name = _first_group(r"versionName=([^\r\n]+)", output)
    min_sdk = _first_group(r"\bminSdk=(\S+)", output)
    target_sdk = _first_group(r"\btargetSdk=(\S+)", output)

    return PackageInfo(
        package_name=package_name,
        version_code=version_code,
        version_name=version_name,
        min_sdk_version=min_sdk,
        target_sdk_version=target_sdk,
    )


def parse_aapt_badging(output: str) -> AaptInfo:
    package_line = _line_starting_with(output, "package:")
    package_name = _quoted_value(package_line, "name")
    if package_name is None:
        raise AdbError("aapt 输出缺少 package name。")

    label = _quoted_value(_line_starting_with(output, "application-label:"), "application-label")
    if label is None:
        label = _quoted_value(_line_starting_with(output, "application:"), "label")

    return AaptInfo(
        package_name=package_name,
        label=label,
        version_code=_quoted_value(package_line, "versionCode"),
        version_name=_quoted_value(package_line, "versionName"),
        min_sdk_version=_quoted_value(_line_starting_with(output, "sdkVersion:"), "sdkVersion"),
        target_sdk_version=_quoted_value(_line_starting_with(output, "targetSdkVersion:"), "targetSdkVersion"),
        icon_resource=parse_aapt_icon_resource(output),
    )


def parse_aapt_icon_resource(output: str) -> str | None:
    candidates: list[tuple[int, str]] = []
    for line in output.splitlines():
        match = re.match(r"application-icon-(\d+):'([^']+)'", line)
        if match is not None:
            candidates.append((int(match.group(1)), match.group(2)))

    if candidates:
        return max(candidates, key=lambda item: item[0])[1]

    return _quoted_value(_line_starting_with(output, "application:"), "icon")


def _first_group(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text)
    if match is None:
        return None
    return match.group(1).strip()


def _line_starting_with(text: str, prefix: str) -> str:
    for line in text.splitlines():
        if line.startswith(prefix):
            return line
    return ""


def _quoted_value(line: str, key: str) -> str | None:
    match = re.search(rf"{re.escape(key)}[:=]'([^']*)'", line)
    if match is None:
        return None
    return match.group(1)
