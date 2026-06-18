from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import zipfile

from apk_extract_gui.adb import AaptInfo, PackageInfo
from apk_extract_gui.bundle import create_apks, create_xapk


class BundleTest(unittest.TestCase):
    def test_create_apks_zip_contains_apk_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "base.apk"
            split = root / "split_config.arm64_v8a.apk"
            base.write_bytes(b"base")
            split.write_bytes(b"split")

            output = create_apks([base, split], root / "app.apks")

            with zipfile.ZipFile(output) as archive:
                self.assertEqual(sorted(archive.namelist()), ["base.apk", "split_config.arm64_v8a.apk"])

    def test_create_xapk_writes_manifest_and_apk_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "base.apk"
            split = root / "split_config.arm64_v8a.apk"
            base.write_bytes(b"base")
            split.write_bytes(b"split")
            package_info = PackageInfo(
                package_name="com.example.app",
                version_code="41",
                version_name="1.2.2",
                min_sdk_version="23",
                target_sdk_version="34",
            )
            aapt_info = AaptInfo(
                package_name="com.example.app",
                label="Example App",
                version_code="42",
                version_name="1.2.3",
                min_sdk_version="24",
                target_sdk_version="35",
            )

            output = create_xapk([base, split], root / "app.xapk", package_info, aapt_info)

            with zipfile.ZipFile(output) as archive:
                self.assertEqual(sorted(archive.namelist()), ["base.apk", "manifest.json", "split_config.arm64_v8a.apk"])
                manifest = archive.read("manifest.json").decode("utf-8")

            self.assertIn('"package_name": "com.example.app"', manifest)
            self.assertIn('"name": "Example App"', manifest)
            self.assertIn('"version_code": "42"', manifest)
            self.assertIn('"apk_file": "base.apk"', manifest)
            self.assertIn('"split_config.arm64_v8a.apk"', manifest)


if __name__ == "__main__":
    unittest.main()
