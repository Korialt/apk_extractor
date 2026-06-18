from __future__ import annotations

import unittest

from apk_extract_gui.adb import (
    AaptInfo,
    Device,
    InstalledApp,
    parse_aapt_badging,
    parse_adb_devices,
    parse_aapt_icon_resource,
    parse_dumpsys_package_info,
    parse_package_file_list,
    parse_pm_paths,
)


class ParsingTest(unittest.TestCase):
    def test_parse_adb_devices_only_authorized_devices(self) -> None:
        output = """List of devices attached
emulator-5554	device
offline-1	offline
unauthorized-1	unauthorized

"""

        self.assertEqual(parse_adb_devices(output), [Device(serial="emulator-5554")])

    def test_parse_package_file_list(self) -> None:
        output = """package:/data/app/~~abc/pkg/base.apk=com.example.alpha
package:/system/priv-app/Settings/Settings.apk=com.android.settings
"""

        self.assertEqual(
            parse_package_file_list(output),
            [
                InstalledApp(package_name="com.example.alpha", base_apk_path="/data/app/~~abc/pkg/base.apk"),
                InstalledApp(package_name="com.android.settings", base_apk_path="/system/priv-app/Settings/Settings.apk"),
            ],
        )

    def test_parse_pm_paths(self) -> None:
        output = """package:/data/app/~~abc/pkg/base.apk
package:/data/app/~~abc/pkg/split_config.arm64_v8a.apk
"""

        self.assertEqual(
            parse_pm_paths(output),
            [
                "/data/app/~~abc/pkg/base.apk",
                "/data/app/~~abc/pkg/split_config.arm64_v8a.apk",
            ],
        )

    def test_parse_dumpsys_package_info(self) -> None:
        output = """
Packages:
  Package [com.example.app] (abc):
    versionCode=42 minSdk=23 targetSdk=35
    versionName=1.2.3
"""

        package_info = parse_dumpsys_package_info("com.example.app", output)

        self.assertEqual(package_info.package_name, "com.example.app")
        self.assertEqual(package_info.version_code, "42")
        self.assertEqual(package_info.version_name, "1.2.3")
        self.assertEqual(package_info.min_sdk_version, "23")
        self.assertEqual(package_info.target_sdk_version, "35")

    def test_parse_aapt_badging(self) -> None:
        output = """package: name='com.example.app' versionCode='42' versionName='1.2.3'
sdkVersion:'23'
targetSdkVersion:'35'
application-label:'Example App'
application-icon-160:'res/mipmap-mdpi/ic_launcher.png'
application-icon-480:'res/mipmap-xxhdpi/ic_launcher.png'
"""

        self.assertEqual(
            parse_aapt_badging(output),
            AaptInfo(
                package_name="com.example.app",
                label="Example App",
                version_code="42",
                version_name="1.2.3",
                min_sdk_version="23",
                target_sdk_version="35",
                icon_resource="res/mipmap-xxhdpi/ic_launcher.png",
            ),
        )

    def test_parse_aapt_icon_resource_falls_back_to_application_icon(self) -> None:
        output = """package: name='com.example.app'
application: label='Example App' icon='res/drawable/icon.png'
"""

        self.assertEqual(parse_aapt_icon_resource(output), "res/drawable/icon.png")


if __name__ == "__main__":
    unittest.main()
