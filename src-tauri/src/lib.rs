use base64::{engine::general_purpose::STANDARD, Engine as _};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::{
    fs::{self, File},
    io::{Read, Write},
    path::{Path, PathBuf},
    process::Command,
    time::{SystemTime, UNIX_EPOCH},
};
use tauri::{AppHandle, Emitter};
use zip::{write::SimpleFileOptions, CompressionMethod, ZipArchive, ZipWriter};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct Device {
    serial: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct InstalledApp {
    package_name: String,
    base_apk_path: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct AppEntry {
    package_name: String,
    base_apk_path: String,
    label: Option<String>,
    icon_data_url: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct ScanStarted {
    device_serial: String,
    total: usize,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct ScanItem {
    index: usize,
    total: usize,
    app: AppEntry,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct ScanSummary {
    total: usize,
    icon_count: usize,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct ExportResult {
    output_file: String,
    package_dir: String,
    pulled_files: Vec<String>,
    remote_paths: Vec<String>,
    note: Option<String>,
}

#[derive(Debug, Clone)]
struct PackageInfo {
    package_name: String,
    version_code: Option<String>,
    version_name: Option<String>,
    min_sdk_version: Option<String>,
    target_sdk_version: Option<String>,
}

#[derive(Debug, Clone)]
struct AaptInfo {
    label: Option<String>,
    version_code: Option<String>,
    version_name: Option<String>,
    min_sdk_version: Option<String>,
    target_sdk_version: Option<String>,
    icon_resource: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
enum BundleFormat {
    Apks,
    Xapk,
}

#[tauri::command]
fn scan_apps(app_handle: AppHandle) -> Result<ScanSummary, String> {
    let device = require_single_device()?;
    let apps = list_installed_apps(&device)?;
    let total = apps.len();
    emit_event(
        &app_handle,
        "scan-started",
        &ScanStarted {
            device_serial: device.serial.clone(),
            total,
        },
    );

    let cache_root = icon_cache_root(&device.serial)?;
    let mut icon_count = 0;
    for (index, app) in apps.iter().enumerate() {
        let presentation = inspect_app_presentation(&device, app, &cache_root, &app_handle);
        if presentation.icon_data_url.is_some() {
            icon_count += 1;
        }

        emit_event(
            &app_handle,
            "scan-item",
            &ScanItem {
                index: index + 1,
                total,
                app: AppEntry {
                    package_name: app.package_name.clone(),
                    base_apk_path: app.base_apk_path.clone(),
                    label: presentation.label,
                    icon_data_url: presentation.icon_data_url,
                },
            },
        );
    }

    let summary = ScanSummary { total, icon_count };
    emit_event(&app_handle, "scan-finished", &summary);
    Ok(summary)
}

#[tauri::command]
fn preview_paths(package_name: String) -> Result<Vec<String>, String> {
    let device = require_single_device()?;
    get_apk_paths(&device, &package_name)
}

#[tauri::command]
fn export_package(package_name: String, output_dir: String, bundle_format: BundleFormat) -> Result<ExportResult, String> {
    if package_name.trim().is_empty() {
        return Err("请先选择需要提取的包名。".to_string());
    }
    if output_dir.trim().is_empty() {
        return Err("请选择输出目录。".to_string());
    }

    let device = require_single_device()?;
    let remote_paths = get_apk_paths(&device, &package_name)?;
    let timestamp = unix_timestamp_seconds()?;
    let package_dir = PathBuf::from(output_dir).join(format!("{}_{}", safe_component(&package_name), timestamp));
    let apk_dir = package_dir.join("apk");
    fs::create_dir_all(&apk_dir).map_err(|err| format!("创建输出目录失败：{}", err))?;

    let pulled_files = pull_apks(&device, &remote_paths, &apk_dir)?;
    let extension = match bundle_format {
        BundleFormat::Apks => "apks",
        BundleFormat::Xapk => "xapk",
    };
    let output_file = package_dir.join(format!("{}.{}", package_name, extension));

    let mut note = None;
    match bundle_format {
        BundleFormat::Apks => create_apks(&pulled_files, &output_file)?,
        BundleFormat::Xapk => {
            let package_info = get_package_info(&device, &package_name)?;
            let base_apk = find_base_apk(&pulled_files)?;
            let aapt_info = match get_aapt_info(base_apk) {
                Ok(info) => info,
                Err(err) => {
                    note = Some(format!("aapt 读取失败，已改用 dumpsys package 生成 XAPK manifest：{}", err));
                    None
                }
            };
            if aapt_info.is_none() && note.is_none() {
                note = Some("未检测到本机 aapt，XAPK manifest 的应用名称使用包名，版本信息使用 dumpsys package。".to_string());
            }
            create_xapk(&pulled_files, &output_file, &package_info, aapt_info.as_ref())?;
        }
    }

    Ok(ExportResult {
        output_file: output_file.to_string_lossy().into_owned(),
        package_dir: package_dir.to_string_lossy().into_owned(),
        pulled_files: pulled_files.iter().map(|path| path.to_string_lossy().into_owned()).collect(),
        remote_paths,
        note,
    })
}

struct AppPresentation {
    label: Option<String>,
    icon_data_url: Option<String>,
}

fn inspect_app_presentation(
    device: &Device,
    app: &InstalledApp,
    cache_root: &Path,
    app_handle: &AppHandle,
) -> AppPresentation {
    if !app.base_apk_path.to_lowercase().ends_with(".apk") {
        return AppPresentation { label: None, icon_data_url: None };
    }

    let package_cache = cache_root.join(safe_component(&app.package_name));
    let base_apk = package_cache.join("base.apk");
    if !base_apk.is_file() {
        if let Err(err) = fs::create_dir_all(&package_cache) {
            emit_log(app_handle, format!("{} 图标缓存目录创建失败：{}", app.package_name, err));
            return AppPresentation { label: None, icon_data_url: None };
        }
        if let Err(err) = pull_remote_file(device, &app.base_apk_path, &base_apk) {
            emit_log(app_handle, format!("{} 图标读取失败：{}", app.package_name, err));
            return AppPresentation { label: None, icon_data_url: None };
        }
    }

    let mut label = None;
    let mut icon_resource = None;
    match get_aapt_info(&base_apk) {
        Ok(Some(info)) => {
            label = info.label;
            icon_resource = info.icon_resource;
        }
        Ok(None) => {}
        Err(err) => emit_log(app_handle, format!("{} aapt 读取失败，使用兜底图标规则：{}", app.package_name, err)),
    }

    if icon_resource.is_none() {
        icon_resource = find_launcher_icon_resource(&base_apk).unwrap_or_else(|err| {
            emit_log(app_handle, format!("{} 图标解析失败：{}", app.package_name, err));
            None
        });
    }

    let icon_data_url = icon_resource
        .as_deref()
        .and_then(|resource| match extract_icon_data_url(&base_apk, resource) {
            Ok(value) => value,
            Err(err) => {
                emit_log(app_handle, format!("{} 图标提取失败：{}", app.package_name, err));
                None
            }
        });

    AppPresentation { label, icon_data_url }
}

fn require_single_device() -> Result<Device, String> {
    let output = run_command("adb", &["devices"])?;
    let devices = parse_adb_devices(&output);
    if devices.is_empty() {
        return Err("未检测到已授权的 Android 设备。请连接设备，并确认 USB 调试授权。".to_string());
    }
    if devices.len() > 1 {
        let serials = devices.iter().map(|device| device.serial.as_str()).collect::<Vec<_>>().join(", ");
        return Err(format!("检测到多个设备：{}。当前版本需要只连接一个设备。", serials));
    }
    Ok(devices[0].clone())
}

fn list_installed_apps(device: &Device) -> Result<Vec<InstalledApp>, String> {
    let output = run_command("adb", &["-s", &device.serial, "shell", "pm", "list", "packages", "-f"])?;
    let mut apps = parse_package_file_list(&output);
    if apps.is_empty() {
        return Err("设备没有返回已安装应用列表。".to_string());
    }
    apps.sort_by(|left, right| left.package_name.cmp(&right.package_name));
    Ok(apps)
}

fn get_apk_paths(device: &Device, package_name: &str) -> Result<Vec<String>, String> {
    let output = run_command("adb", &["-s", &device.serial, "shell", "pm", "path", package_name])?;
    let paths = parse_pm_paths(&output);
    if paths.is_empty() {
        return Err(format!("未找到 {} 的 APK 路径。", package_name));
    }
    Ok(paths)
}

fn pull_apks(device: &Device, remote_paths: &[String], output_dir: &Path) -> Result<Vec<PathBuf>, String> {
    let mut local_paths = Vec::new();
    let mut used_names = Vec::new();
    for remote_path in remote_paths {
        let mut file_name = remote_file_name(remote_path)?;
        if used_names.iter().any(|name| name == &file_name) {
            file_name = format!("{}_{}", used_names.len() + 1, file_name);
        }
        used_names.push(file_name.clone());
        let local_path = output_dir.join(file_name);
        pull_remote_file(device, remote_path, &local_path)?;
        local_paths.push(local_path);
    }
    Ok(local_paths)
}

fn pull_remote_file(device: &Device, remote_path: &str, local_path: &Path) -> Result<(), String> {
    if let Some(parent) = local_path.parent() {
        fs::create_dir_all(parent).map_err(|err| format!("创建输出目录失败：{}", err))?;
    }
    let local = local_path.to_string_lossy().into_owned();
    run_command("adb", &["-s", &device.serial, "pull", remote_path, &local])?;
    if !local_path.is_file() {
        return Err(format!("adb pull 未生成本地文件：{}", local_path.display()));
    }
    Ok(())
}

fn get_package_info(device: &Device, package_name: &str) -> Result<PackageInfo, String> {
    let output = run_command("adb", &["-s", &device.serial, "shell", "dumpsys", "package", package_name])?;
    Ok(parse_dumpsys_package_info(package_name, &output))
}

fn get_aapt_info(base_apk: &Path) -> Result<Option<AaptInfo>, String> {
    let apk = base_apk.to_string_lossy().into_owned();
    match run_command("aapt", &["dump", "badging", &apk]) {
        Ok(output) => Ok(Some(parse_aapt_badging(&output)?)),
        Err(err) if err.starts_with("命令不存在") => Ok(None),
        Err(err) => Err(err),
    }
}

fn create_apks(files: &[PathBuf], output_file: &Path) -> Result<(), String> {
    write_zip(files, output_file, None)
}

fn create_xapk(
    files: &[PathBuf],
    output_file: &Path,
    package_info: &PackageInfo,
    aapt_info: Option<&AaptInfo>,
) -> Result<(), String> {
    let apk_names = files.iter().map(|path| file_name_from_path(path)).collect::<Result<Vec<_>, _>>()?;
    let base_apk = find_base_apk_name(&apk_names)?;
    let split_apks = apk_names
        .iter()
        .filter(|name| *name != &base_apk)
        .cloned()
        .collect::<Vec<_>>();
    let total_size = files
        .iter()
        .map(|path| fs::metadata(path).map(|meta| meta.len()).map_err(|err| format!("读取文件大小失败：{}", err)))
        .collect::<Result<Vec<_>, _>>()?
        .into_iter()
        .sum::<u64>();

    let manifest = json!({
        "xapk_version": 2,
        "package_name": package_info.package_name,
        "name": aapt_info.and_then(|info| info.label.clone()).unwrap_or_else(|| package_info.package_name.clone()),
        "version_code": prefer_option(aapt_info.and_then(|info| info.version_code.clone()), package_info.version_code.clone()),
        "version_name": prefer_option(aapt_info.and_then(|info| info.version_name.clone()), package_info.version_name.clone()),
        "min_sdk_version": prefer_option(aapt_info.and_then(|info| info.min_sdk_version.clone()), package_info.min_sdk_version.clone()),
        "target_sdk_version": prefer_option(aapt_info.and_then(|info| info.target_sdk_version.clone()), package_info.target_sdk_version.clone()),
        "total_size": total_size,
        "apk_file": base_apk,
        "split_apks": split_apks,
    });
    let manifest_bytes = serde_json::to_vec_pretty(&manifest).map_err(|err| format!("生成 XAPK manifest 失败：{}", err))?;
    write_zip(files, output_file, Some(("manifest.json", manifest_bytes)))
}

fn write_zip(files: &[PathBuf], output_file: &Path, extra_file: Option<(&str, Vec<u8>)>) -> Result<(), String> {
    if let Some(parent) = output_file.parent() {
        fs::create_dir_all(parent).map_err(|err| format!("创建输出目录失败：{}", err))?;
    }
    let file = File::create(output_file).map_err(|err| format!("创建压缩包失败：{}", err))?;
    let mut zip = ZipWriter::new(file);
    let options = SimpleFileOptions::default().compression_method(CompressionMethod::Deflated);

    if let Some((name, data)) = extra_file {
        zip.start_file(name, options).map_err(|err| format!("写入压缩包失败：{}", err))?;
        zip.write_all(&data).map_err(|err| format!("写入压缩包失败：{}", err))?;
    }

    for apk_path in files {
        let name = file_name_from_path(apk_path)?;
        let mut apk_file = File::open(apk_path).map_err(|err| format!("打开 APK 文件失败：{}", err))?;
        zip.start_file(name, options).map_err(|err| format!("写入压缩包失败：{}", err))?;
        std::io::copy(&mut apk_file, &mut zip).map_err(|err| format!("写入压缩包失败：{}", err))?;
    }

    zip.finish().map_err(|err| format!("完成压缩包失败：{}", err))?;
    Ok(())
}

fn extract_icon_data_url(apk_path: &Path, resource_path: &str) -> Result<Option<String>, String> {
    let mime = match Path::new(resource_path).extension().and_then(|ext| ext.to_str()).map(|ext| ext.to_lowercase()) {
        Some(ext) if ext == "png" => "image/png",
        Some(ext) if ext == "gif" => "image/gif",
        Some(ext) if ext == "jpg" || ext == "jpeg" => "image/jpeg",
        Some(ext) if ext == "webp" => "image/webp",
        Some(ext) if ext == "svg" => "image/svg+xml",
        _ => return Ok(None),
    };

    let file = File::open(apk_path).map_err(|err| format!("打开 APK 文件失败：{}", err))?;
    let mut archive = ZipArchive::new(file).map_err(|err| format!("APK 文件不是有效 ZIP：{}", err))?;
    let mut icon_file = match archive.by_name(resource_path) {
        Ok(file) => file,
        Err(_) => return Ok(None),
    };
    let mut data = Vec::new();
    icon_file.read_to_end(&mut data).map_err(|err| format!("读取图标资源失败：{}", err))?;
    Ok(Some(format!("data:{};base64,{}", mime, STANDARD.encode(data))))
}

fn find_launcher_icon_resource(apk_path: &Path) -> Result<Option<String>, String> {
    let file = File::open(apk_path).map_err(|err| format!("打开 APK 文件失败：{}", err))?;
    let mut archive = ZipArchive::new(file).map_err(|err| format!("APK 文件不是有效 ZIP：{}", err))?;
    let mut candidates = Vec::new();
    for index in 0..archive.len() {
        let file = archive.by_index(index).map_err(|err| format!("读取 APK 资源列表失败：{}", err))?;
        let name = file.name().to_string();
        if is_supported_image_resource(&name) && looks_like_launcher_icon(&name) {
            candidates.push(name);
        }
    }
    candidates.sort_by_key(|name| icon_candidate_score(name));
    Ok(candidates.pop())
}

fn run_command(program: &str, args: &[&str]) -> Result<String, String> {
    let mut command = Command::new(program);
    command.args(args);
    hide_command_window(&mut command);
    let output = command.output().map_err(|err| {
        if err.kind() == std::io::ErrorKind::NotFound {
            format!("命令不存在：{}", program)
        } else {
            format!("执行命令失败：{}：{}", program, err)
        }
    })?;

    if !output.status.success() {
        return Err(command_error(program, args, &output));
    }

    Ok(String::from_utf8_lossy(&output.stdout).into_owned())
}

fn command_error(program: &str, args: &[&str], output: &std::process::Output) -> String {
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let detail = if !stderr.is_empty() { stderr } else { stdout };
    if detail.is_empty() {
        format!("命令执行失败：{} {}", program, args.join(" "))
    } else {
        format!("命令执行失败：{} {}\n{}", program, args.join(" "), detail)
    }
}

fn hide_command_window(_command: &mut Command) {
    #[cfg(windows)]
    {
        const CREATE_NO_WINDOW: u32 = 0x08000000;
        _command.creation_flags(CREATE_NO_WINDOW);
    }
}

fn parse_adb_devices(output: &str) -> Vec<Device> {
    output
        .lines()
        .skip(1)
        .filter_map(|line| {
            let columns = line.split_whitespace().collect::<Vec<_>>();
            if columns.len() >= 2 && columns[1] == "device" {
                Some(Device { serial: columns[0].to_string() })
            } else {
                None
            }
        })
        .collect()
}

fn parse_package_file_list(output: &str) -> Vec<InstalledApp> {
    output
        .lines()
        .filter_map(|line| {
            let text = line.trim();
            let value = text.strip_prefix("package:")?;
            let (apk_path, package_name) = value.rsplit_once('=')?;
            if apk_path.is_empty() || package_name.is_empty() {
                None
            } else {
                Some(InstalledApp { package_name: package_name.to_string(), base_apk_path: apk_path.to_string() })
            }
        })
        .collect()
}

fn parse_pm_paths(output: &str) -> Vec<String> {
    output
        .lines()
        .filter_map(|line| line.trim().strip_prefix("package:").map(|path| path.to_string()))
        .collect()
}

fn parse_dumpsys_package_info(package_name: &str, output: &str) -> PackageInfo {
    let mut version_code = None;
    let mut version_name = None;
    let mut min_sdk_version = None;
    let mut target_sdk_version = None;

    for line in output.lines() {
        let text = line.trim();
        if let Some(value) = text.strip_prefix("versionName=") {
            version_name = Some(value.trim().to_string());
        }
        for token in text.split_whitespace() {
            if let Some(value) = token.strip_prefix("versionCode=") {
                version_code = Some(value.to_string());
            } else if let Some(value) = token.strip_prefix("minSdk=") {
                min_sdk_version = Some(value.to_string());
            } else if let Some(value) = token.strip_prefix("targetSdk=") {
                target_sdk_version = Some(value.to_string());
            }
        }
    }

    PackageInfo {
        package_name: package_name.to_string(),
        version_code,
        version_name,
        min_sdk_version,
        target_sdk_version,
    }
}

fn parse_aapt_badging(output: &str) -> Result<AaptInfo, String> {
    let package_line = line_starting_with(output, "package:");
    if quoted_value(package_line, "name").is_none() {
        return Err("aapt 输出缺少 package name。".to_string());
    }

    let label = quoted_value(line_starting_with(output, "application-label:"), "application-label")
        .or_else(|| quoted_value(line_starting_with(output, "application:"), "label"));

    Ok(AaptInfo {
        label,
        version_code: quoted_value(package_line, "versionCode"),
        version_name: quoted_value(package_line, "versionName"),
        min_sdk_version: quoted_value(line_starting_with(output, "sdkVersion:"), "sdkVersion"),
        target_sdk_version: quoted_value(line_starting_with(output, "targetSdkVersion:"), "targetSdkVersion"),
        icon_resource: parse_aapt_icon_resource(output),
    })
}

fn parse_aapt_icon_resource(output: &str) -> Option<String> {
    let mut candidates = Vec::new();
    for line in output.lines() {
        if let Some(rest) = line.strip_prefix("application-icon-") {
            if let Some((density, value)) = rest.split_once(':') {
                if let Ok(density) = density.parse::<i32>() {
                    let resource = value.trim().trim_matches('\'').to_string();
                    candidates.push((density, resource));
                }
            }
        }
    }

    candidates.sort_by_key(|(density, _)| *density);
    candidates.pop().map(|(_, resource)| resource).or_else(|| quoted_value(line_starting_with(output, "application:"), "icon"))
}

fn quoted_value(line: &str, key: &str) -> Option<String> {
    for separator in ["='", ":'"] {
        let marker = format!("{}{}", key, separator);
        if let Some(start) = line.find(&marker) {
            let value_start = start + marker.len();
            let rest = &line[value_start..];
            let value_end = rest.find('\'')?;
            return Some(rest[..value_end].to_string());
        }
    }
    None
}

fn line_starting_with<'a>(text: &'a str, prefix: &str) -> &'a str {
    text.lines().find(|line| line.starts_with(prefix)).unwrap_or("")
}

fn remote_file_name(remote_path: &str) -> Result<String, String> {
    remote_path
        .rsplit('/')
        .next()
        .filter(|name| !name.is_empty())
        .map(|name| name.to_string())
        .ok_or_else(|| format!("无法从远程路径解析文件名：{}", remote_path))
}

fn file_name_from_path(path: &Path) -> Result<String, String> {
    path.file_name()
        .and_then(|name| name.to_str())
        .map(|name| name.to_string())
        .ok_or_else(|| format!("无法从路径解析文件名：{}", path.display()))
}

fn find_base_apk(files: &[PathBuf]) -> Result<&PathBuf, String> {
    files
        .iter()
        .find(|path| path.file_name().and_then(|name| name.to_str()) == Some("base.apk"))
        .or_else(|| files.first())
        .ok_or_else(|| "没有可打包的 APK 文件。".to_string())
}

fn find_base_apk_name(apk_names: &[String]) -> Result<String, String> {
    apk_names
        .iter()
        .find(|name| name.as_str() == "base.apk")
        .or_else(|| apk_names.first())
        .cloned()
        .ok_or_else(|| "没有可打包的 APK 文件。".to_string())
}

fn prefer_option(primary: Option<String>, secondary: Option<String>) -> Option<String> {
    primary.or(secondary)
}

fn icon_cache_root(serial: &str) -> Result<PathBuf, String> {
    let path = std::env::temp_dir().join("apk_extract_gui_icon_cache").join(safe_component(serial));
    fs::create_dir_all(&path).map_err(|err| format!("创建图标缓存目录失败：{}", err))?;
    Ok(path)
}

fn safe_component(value: &str) -> String {
    value
        .chars()
        .map(|ch| if ch.is_ascii_alphanumeric() || matches!(ch, '.' | '_' | '-') { ch } else { '_' })
        .collect()
}

fn is_supported_image_resource(name: &str) -> bool {
    let lower = name.to_lowercase();
    name.starts_with("res/")
        && (lower.ends_with(".png")
            || lower.ends_with(".gif")
            || lower.ends_with(".jpg")
            || lower.ends_with(".jpeg")
            || lower.ends_with(".webp")
            || lower.ends_with(".svg"))
}

fn looks_like_launcher_icon(name: &str) -> bool {
    let lower = name.to_lowercase();
    lower.contains("ic_launcher") || lower.contains("launcher") || lower.contains("icon")
}

fn icon_candidate_score(name: &str) -> (i32, i32) {
    let lower = name.to_lowercase();
    let token_score = if lower.contains("ic_launcher") {
        3
    } else if lower.contains("launcher") {
        2
    } else if lower.contains("icon") {
        1
    } else {
        0
    };

    let density_score = [
        ("xxxhdpi", 6),
        ("xxhdpi", 5),
        ("xhdpi", 4),
        ("hdpi", 3),
        ("mdpi", 2),
        ("nodpi", 1),
    ]
    .iter()
    .find_map(|(density, score)| lower.contains(density).then_some(*score))
    .unwrap_or(0);

    (token_score, density_score)
}

fn unix_timestamp_seconds() -> Result<u64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .map_err(|err| format!("读取系统时间失败：{}", err))
}

fn emit_log(app_handle: &AppHandle, message: String) {
    let _ = app_handle.emit("scan-log", message);
}

fn emit_event<T: Serialize + Clone>(app_handle: &AppHandle, event: &str, payload: &T) {
    let _ = app_handle.emit(event, payload.clone());
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![scan_apps, preview_paths, export_package])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
