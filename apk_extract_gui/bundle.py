from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import zipfile

from apk_extract_gui.adb import AaptInfo, PackageInfo


@dataclass(frozen=True)
class XapkMetadata:
    package_name: str
    name: str
    version_code: str | None
    version_name: str | None
    min_sdk_version: str | None
    target_sdk_version: str | None


def create_apks(apk_paths: list[Path], output_file: Path) -> Path:
    write_zip(apk_paths, output_file)
    return output_file


def create_xapk(
    apk_paths: list[Path],
    output_file: Path,
    package_info: PackageInfo,
    aapt_info: AaptInfo | None,
) -> Path:
    metadata = build_xapk_metadata(package_info, aapt_info)
    manifest = build_xapk_manifest(apk_paths, metadata)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_file, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for apk_path in apk_paths:
            archive.write(apk_path, apk_path.name)

    return output_file


def build_xapk_metadata(package_info: PackageInfo, aapt_info: AaptInfo | None) -> XapkMetadata:
    # aapt 读取的是 APK 文件自身声明；缺少 aapt 时再使用设备 dumpsys 的安装信息。
    return XapkMetadata(
        package_name=package_info.package_name,
        name=aapt_info.label if aapt_info and aapt_info.label else package_info.package_name,
        version_code=_prefer_detected(aapt_info.version_code if aapt_info else None, package_info.version_code),
        version_name=_prefer_detected(aapt_info.version_name if aapt_info else None, package_info.version_name),
        min_sdk_version=_prefer_detected(aapt_info.min_sdk_version if aapt_info else None, package_info.min_sdk_version),
        target_sdk_version=_prefer_detected(
            aapt_info.target_sdk_version if aapt_info else None,
            package_info.target_sdk_version,
        ),
    )


def build_xapk_manifest(apk_paths: list[Path], metadata: XapkMetadata) -> dict[str, object]:
    apk_names = [path.name for path in apk_paths]
    base_apk = find_base_apk_name(apk_names)
    split_apks = [name for name in apk_names if name != base_apk]
    total_size = sum(path.stat().st_size for path in apk_paths)

    return {
        "xapk_version": 2,
        "package_name": metadata.package_name,
        "name": metadata.name,
        "version_code": metadata.version_code,
        "version_name": metadata.version_name,
        "min_sdk_version": metadata.min_sdk_version,
        "target_sdk_version": metadata.target_sdk_version,
        "total_size": total_size,
        "apk_file": base_apk,
        "split_apks": split_apks,
    }


def write_zip(files: list[Path], output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_file, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in files:
            archive.write(file_path, file_path.name)


def find_base_apk_name(apk_names: list[str]) -> str:
    for apk_name in apk_names:
        if apk_name == "base.apk":
            return apk_name
    return apk_names[0]


def _prefer_detected(primary: str | None, secondary: str | None) -> str | None:
    return primary if primary is not None else secondary

