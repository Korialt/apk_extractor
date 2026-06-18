from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import zipfile

from apk_extract_gui.icons import extract_icon_resource, find_launcher_icon_resource, safe_path_component


class IconsTest(unittest.TestCase):
    def test_extract_icon_resource_writes_supported_icon(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apk_path = root / "base.apk"
            with zipfile.ZipFile(apk_path, "w") as archive:
                archive.writestr("res/mipmap-xxhdpi/ic_launcher.png", b"png-data")

            icon_path = extract_icon_resource(
                apk_path,
                "res/mipmap-xxhdpi/ic_launcher.png",
                root / "icons",
                "com.example.app",
            )

            self.assertIsNotNone(icon_path)
            assert icon_path is not None
            self.assertEqual(icon_path.read_bytes(), b"png-data")

    def test_find_launcher_icon_resource_prefers_launcher_name_and_density(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            apk_path = Path(tmp) / "base.apk"
            with zipfile.ZipFile(apk_path, "w") as archive:
                archive.writestr("res/drawable-mdpi/icon.png", b"mdpi")
                archive.writestr("res/mipmap-xxhdpi/ic_launcher.png", b"xxhdpi")

            self.assertEqual(find_launcher_icon_resource(apk_path), "res/mipmap-xxhdpi/ic_launcher.png")

    def test_safe_path_component_replaces_unsafe_characters(self) -> None:
        self.assertEqual(safe_path_component("serial:01/package"), "serial_01_package")


if __name__ == "__main__":
    unittest.main()
