from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import zipfile

from apk_extract_gui.adb import AdbError, Device, InstalledApp, get_aapt_info, pull_remote_file


SUPPORTED_TK_ICON_EXTENSIONS = {".png", ".gif"}


@dataclass(frozen=True)
class AppPresentation:
    label: str | None
    icon_path: Path | None
    note: str | None


def inspect_app_presentation(
    adb_path: Path,
    device: Device,
    app: InstalledApp,
    cache_root: Path,
) -> AppPresentation:
    if not app.base_apk_path.lower().endswith(".apk"):
        return AppPresentation(label=None, icon_path=None, note=None)

    package_cache_dir = cache_root / safe_path_component(app.package_name)
    base_apk = package_cache_dir / "base.apk"
    icon_dir = package_cache_dir / "icons"

    if not base_apk.is_file():
        pull_remote_file(adb_path, device, app.base_apk_path, base_apk)

    label: str | None = None
    icon_resource: str | None = None
    note: str | None = None

    try:
        aapt_info = get_aapt_info(base_apk)
    except AdbError as exc:
        aapt_info = None
        note = f"aapt 读取 {app.package_name} 失败，已使用图标兜底规则：{exc}"

    if aapt_info is not None:
        label = aapt_info.label
        icon_resource = aapt_info.icon_resource

    if icon_resource is None:
        icon_resource = find_launcher_icon_resource(base_apk)

    icon_path = None
    if icon_resource is not None:
        icon_path = extract_icon_resource(base_apk, icon_resource, icon_dir, app.package_name)

    return AppPresentation(label=label, icon_path=icon_path, note=note)


def extract_icon_resource(apk_path: Path, resource_path: str, icon_dir: Path, package_name: str) -> Path | None:
    suffix = Path(resource_path).suffix.lower()
    if suffix not in SUPPORTED_TK_ICON_EXTENSIONS:
        return None

    icon_dir.mkdir(parents=True, exist_ok=True)
    output_path = icon_dir / f"{safe_path_component(package_name)}{suffix}"

    try:
        with zipfile.ZipFile(apk_path) as archive:
            try:
                data = archive.read(resource_path)
            except KeyError:
                return None
    except zipfile.BadZipFile as exc:
        raise AdbError(f"APK 文件不是有效 ZIP：{apk_path}") from exc

    output_path.write_bytes(data)
    return output_path


def find_launcher_icon_resource(apk_path: Path) -> str | None:
    try:
        with zipfile.ZipFile(apk_path) as archive:
            names = archive.namelist()
    except zipfile.BadZipFile as exc:
        raise AdbError(f"APK 文件不是有效 ZIP：{apk_path}") from exc

    candidates = [
        name
        for name in names
        if _is_supported_resource_image(name) and _looks_like_launcher_icon(name)
    ]
    if not candidates:
        return None

    # 没有 aapt 时只能基于常见 launcher 图标命名和密度目录选择最可能的资源。
    return max(candidates, key=_icon_candidate_score)


def safe_path_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", value)


def _is_supported_resource_image(name: str) -> bool:
    path = Path(name)
    return name.startswith("res/") and path.suffix.lower() in SUPPORTED_TK_ICON_EXTENSIONS


def _looks_like_launcher_icon(name: str) -> bool:
    lower_name = name.lower()
    return "launcher" in lower_name or "ic_launcher" in lower_name or "icon" in lower_name


def _icon_candidate_score(name: str) -> tuple[int, int]:
    lower_name = name.lower()
    token_score = 0
    if "ic_launcher" in lower_name:
        token_score = 3
    elif "launcher" in lower_name:
        token_score = 2
    elif "icon" in lower_name:
        token_score = 1

    density_score = 0
    for density, score in (
        ("xxxhdpi", 6),
        ("xxhdpi", 5),
        ("xhdpi", 4),
        ("hdpi", 3),
        ("mdpi", 2),
        ("nodpi", 1),
    ):
        if density in lower_name:
            density_score = score
            break

    return token_score, density_score
