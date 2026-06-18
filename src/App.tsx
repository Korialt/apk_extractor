import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import type { UnlistenFn } from "@tauri-apps/api/event";
import { open } from "@tauri-apps/plugin-dialog";
import { useEffect, useMemo, useState } from "react";
import type { AppEntry, BundleFormat, Device, ExportResult, ScanItem, ScanStarted, ScanSummary, Theme } from "./types";

function appName(app: AppEntry): string {
  return app.label?.trim() || app.packageName;
}

function matchesQuery(app: AppEntry, query: string): boolean {
  const text = query.trim().toLowerCase();
  if (!text) return true;
  if (app.packageName.toLowerCase().includes(text)) return true;
  if (app.baseApkPath.toLowerCase().includes(text)) return true;
  return app.label?.toLowerCase().includes(text) ?? false;
}

function formatCount(value: number): string {
  return Number.isFinite(value) ? value.toLocaleString() : "0";
}

function safeLogMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  return "操作失败，未返回错误详情。";
}

function FormatSwitch({ value, onChange, disabled }: { value: BundleFormat; onChange: (value: BundleFormat) => void; disabled: boolean }) {
  return (
    <div className="format-switch" aria-label="打包格式">
      <button type="button" className={value === "apks" ? "active" : ""} disabled={disabled} onClick={() => onChange("apks")}>
        APKS
      </button>
      <button type="button" className={value === "xapk" ? "active" : ""} disabled={disabled} onClick={() => onChange("xapk")}>
        XAPK
      </button>
    </div>
  );
}

function AppIcon({ app }: { app: AppEntry }) {
  if (app.iconDataUrl) {
    return <img className="app-icon" src={app.iconDataUrl} alt="" />;
  }
  return <div className="app-icon placeholder" />;
}

function AppRow({
  app,
  active,
  disabled,
  onSelect,
}: {
  app: AppEntry;
  active: boolean;
  disabled: boolean;
  onSelect: (app: AppEntry) => void;
}) {
  return (
    <button type="button" className={active ? "app-row active" : "app-row"} disabled={disabled} onClick={() => onSelect(app)}>
      <AppIcon app={app} />
      <div className="app-main">
        <strong>{appName(app)}</strong>
        <code title={app.packageName}>{app.packageName}</code>
        <span title={app.baseApkPath}>{app.baseApkPath}</span>
      </div>
    </button>
  );
}

function PathList({ title, paths }: { title: string; paths: string[] }) {
  return (
    <div className="path-block">
      <div className="path-block-title">{title}</div>
      {paths.length === 0 ? (
        <p className="empty-inline">暂无路径</p>
      ) : (
        <div className="path-list compact">
          {paths.map((path) => (
            <code key={path} title={path}>
              {path}
            </code>
          ))}
        </div>
      )}
    </div>
  );
}

export default function App() {
  const [theme, setTheme] = useState<Theme>("light");
  const [apps, setApps] = useState<AppEntry[]>([]);
  const [selectedPackage, setSelectedPackage] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [outputDir, setOutputDir] = useState("exports");
  const [format, setFormat] = useState<BundleFormat>("apks");
  const [busy, setBusy] = useState(false);
  const [scanRunning, setScanRunning] = useState(false);
  const [phase, setPhase] = useState("就绪");
  const [devices, setDevices] = useState<Device[]>([]);
  const [devicePickerOpen, setDevicePickerOpen] = useState(false);
  const [deviceSerial, setDeviceSerial] = useState<string | null>(null);
  const [scanTotal, setScanTotal] = useState(0);
  const [scanIndex, setScanIndex] = useState(0);
  const [iconCount, setIconCount] = useState(0);
  const [previewPaths, setPreviewPaths] = useState<string[]>([]);
  const [exportResult, setExportResult] = useState<ExportResult | null>(null);
  const [logs, setLogs] = useState<string[]>([]);

  const selectedApp = selectedPackage ? apps.find((app) => app.packageName === selectedPackage) ?? null : null;
  const filteredApps = useMemo(() => apps.filter((app) => matchesQuery(app, query)), [apps, query]);
  const scanPercent = scanTotal > 0 ? Math.round((scanIndex / scanTotal) * 100) : null;
  const actionBusy = busy || scanRunning;

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    const unlistenPromises: Promise<UnlistenFn>[] = [
      listen<ScanStarted>("scan-started", (event) => {
        setDeviceSerial(event.payload.deviceSerial);
        setScanTotal(event.payload.total);
        setScanIndex(0);
        setIconCount(0);
        setScanRunning(true);
        setPhase(`正在扫描 ${event.payload.deviceSerial} 设备`);
        appendLog(`设备：${event.payload.deviceSerial}`);
        appendLog(`第三方应用数量：${event.payload.total}`);
      }),
      listen<ScanItem>("scan-item", (event) => {
        setApps((current) => {
          const next = current.filter((app) => app.packageName !== event.payload.app.packageName);
          next.push(event.payload.app);
          next.sort((left, right) => left.packageName.localeCompare(right.packageName));
          return next;
        });
        setScanIndex(event.payload.index);
      }),
      listen<string>("scan-log", (event) => appendLog(event.payload)),
      listen<string>("scan-error", (event) => {
        setScanRunning(false);
        appendLog(event.payload);
        setPhase("扫描失败，详情见日志");
      }),
      listen<ScanSummary>("scan-finished", (event) => {
        setIconCount(event.payload.iconCount);
        setScanIndex(event.payload.total);
        setScanRunning(false);
        appendLog(`扫描完成：${event.payload.total} 个应用，读取到 ${event.payload.iconCount} 个图标。`);
        setPhase(`扫描完成：${event.payload.total} 个应用`);
      }),
    ];

    return () => {
      void Promise.all(unlistenPromises).then((unlisteners) => unlisteners.forEach((unlisten) => unlisten()));
    };
  }, []);

  function appendLog(message: string) {
    setLogs((current) => [...current.slice(-180), `${new Date().toLocaleTimeString()}  ${message}`]);
  }

  async function scanApps() {
    if (scanRunning) return;

    setBusy(true);
    setPhase("检测设备");
    setDevicePickerOpen(false);
    appendLog("检测已授权 Android 设备...");
    try {
      const foundDevices = await invoke<Device[]>("list_devices");
      setDevices(foundDevices);

      if (foundDevices.length === 0) {
        setPhase("未检测到设备");
        appendLog("未检测到设备。");
        return;
      }

      if (foundDevices.length > 1) {
        setPhase(`检测到 ${foundDevices.length} 台设备，请选择扫描设备`);
        appendLog(`检测到 ${foundDevices.length} 台设备，请选择其中一台。`);
        setDevicePickerOpen(true);
        return;
      }

      await startScan(foundDevices[0].serial);
    } catch (error) {
      const message = safeLogMessage(error);
      appendLog(message);
      setPhase("设备检测失败，详情见日志");
    } finally {
      setBusy(false);
    }
  }

  async function startScan(serial: string) {
    const normalizedSerial = serial.trim();
    if (!normalizedSerial) {
      appendLog("请选择需要扫描的设备。");
      setPhase("请选择设备");
      return;
    }

    setBusy(true);
    setScanRunning(true);
    setDevicePickerOpen(false);
    setDeviceSerial(normalizedSerial);
    setPhase(`正在扫描 ${normalizedSerial} 设备`);
    setApps([]);
    setSelectedPackage(null);
    setPreviewPaths([]);
    setExportResult(null);
    setLogs([]);
    setScanTotal(0);
    setScanIndex(0);
    setIconCount(0);
    appendLog(`正在扫描 ${normalizedSerial} 设备`);
    try {
      await invoke<void>("start_scan", { deviceSerial: normalizedSerial });
    } catch (error) {
      const message = safeLogMessage(error);
      appendLog(message);
      setScanRunning(false);
      setPhase("扫描启动失败，详情见日志");
    } finally {
      setBusy(false);
    }
  }

  async function chooseOutputDir() {
    const selected = await open({ directory: true, multiple: false, title: "选择输出目录" });
    if (typeof selected === "string") {
      setOutputDir(selected);
    }
  }

  async function selectApp(app: AppEntry) {
    if (!deviceSerial) {
      appendLog("请先扫描并选择设备。");
      setPhase("请先扫描设备");
      return;
    }

    setSelectedPackage(app.packageName);
    setPreviewPaths([]);
    setExportResult(null);
    setBusy(true);
    setPhase("查询 APK 路径");
    appendLog(`查询 ${app.packageName} 的 APK 路径...`);
    try {
      const paths = await invoke<string[]>("preview_paths", { deviceSerial, packageName: app.packageName });
      setPreviewPaths(paths);
      appendLog(`找到 ${paths.length} 个 APK 路径。`);
      setPhase("路径查询完成");
    } catch (error) {
      const message = safeLogMessage(error);
      appendLog(message);
      setPhase("路径查询失败，详情见日志");
    } finally {
      setBusy(false);
    }
  }

  async function exportSelected() {
    if (!selectedApp) {
      appendLog("请先选择需要提取的应用。将不会弹出窗口。 ");
      setPhase("请先选择应用");
      return;
    }
    if (!deviceSerial) {
      appendLog("请先扫描并选择设备。");
      setPhase("请先扫描设备");
      return;
    }

    setBusy(true);
    setPhase("提取并打包");
    setExportResult(null);
    appendLog(`开始提取 ${selectedApp.packageName}，目标格式：${format.toUpperCase()}。`);
    try {
      const result = await invoke<ExportResult>("export_package", {
        deviceSerial,
        packageName: selectedApp.packageName,
        outputDir,
        bundleFormat: format,
      });
      setExportResult(result);
      setPreviewPaths(result.remotePaths);
      result.remotePaths.forEach((path) => appendLog(`设备路径：${path}`));
      result.pulledFiles.forEach((path) => appendLog(`本地文件：${path}`));
      if (result.note) appendLog(result.note);
      appendLog(`打包完成：${result.outputFile}`);
      setPhase("打包完成");
    } catch (error) {
      const message = safeLogMessage(error);
      appendLog(message);
      setPhase("打包失败，详情见日志");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div>
            <h1>APK Split Extractor</h1>
            <p>扫描第三方应用，提取 split APK 并打包</p>
          </div>
          <button type="button" className="theme-toggle" onClick={() => setTheme(theme === "light" ? "dark" : "light")}>
            {theme === "light" ? "深色" : "浅色"}
          </button>
        </div>

        <section className="panel">
          <div className="panel-title">设备扫描</div>
          <button type="button" className="primary" disabled={actionBusy} onClick={() => void scanApps()}>
            扫描第三方应用
          </button>
          <p className="status">{phase}</p>
          <div className="progress-block">
            <div className="progress-bar" data-indeterminate={actionBusy && scanPercent == null ? "true" : "false"}>
              <div className="progress-fill" style={{ width: scanPercent == null ? "42%" : `${scanPercent}%` }} />
            </div>
            <div className="progress-meta">
              <span>{scanTotal > 0 ? `${scanIndex} / ${scanTotal}` : "尚未扫描"}</span>
              {deviceSerial ? <span>设备 {deviceSerial}</span> : null}
            </div>
          </div>
        </section>

        <section className="panel stats">
          <div>
            <small>总应用</small>
            <span>{formatCount(apps.length)}</span>
          </div>
          <div>
            <small>当前显示</small>
            <span>{formatCount(filteredApps.length)}</span>
          </div>
          <div>
            <small>已读图标</small>
            <span>{formatCount(iconCount)}</span>
          </div>
        </section>

        <section className="panel">
          <div className="panel-title">打包设置</div>
          <FormatSwitch value={format} onChange={setFormat} disabled={actionBusy} />
          <label className="field-block">
            输出目录
            <div className="inline-field">
              <input value={outputDir} disabled={actionBusy} onChange={(event) => setOutputDir(event.target.value)} />
              <button type="button" disabled={actionBusy} onClick={() => void chooseOutputDir()}>
                选择
              </button>
            </div>
          </label>
          <button type="button" className="primary" disabled={actionBusy} onClick={() => void exportSelected()}>
            提取并打包
          </button>
        </section>
      </aside>

      <section className="content">
        <header className="toolbar">
          <div>
            <h2>应用列表</h2>
            <p>{selectedApp ? `已选择 ${selectedApp.packageName}` : "选择一个应用后会自动查询全部 split APK 路径"}</p>
          </div>
          <div className="toolbar-actions">
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="过滤应用名、包名或 APK 路径" />
          </div>
        </header>

        <div className="workspace">
          <section className="app-list-panel">
            {filteredApps.length === 0 ? (
              <div className="empty">{apps.length === 0 ? "点击“扫描第三方应用”开始。" : "没有匹配当前关键词的应用。"}</div>
            ) : (
              <div className="app-list">
                {filteredApps.map((app) => (
                  <AppRow key={app.packageName} app={app} active={app.packageName === selectedPackage} disabled={actionBusy} onSelect={(value) => void selectApp(value)} />
                ))}
              </div>
            )}
          </section>

          <aside className="detail-panel">
            <section className="detail-section">
              <div className="section-header compact">
                <h3>选择详情</h3>
                {selectedApp ? <span>{format.toUpperCase()}</span> : null}
              </div>
              {selectedApp ? (
                <div className="selected-app">
                  <AppIcon app={selectedApp} />
                  <div>
                    <strong>{appName(selectedApp)}</strong>
                    <code title={selectedApp.packageName}>{selectedApp.packageName}</code>
                  </div>
                </div>
              ) : (
                <p className="empty-inline">尚未选择应用。</p>
              )}
            </section>

            <PathList title="设备 APK 路径" paths={previewPaths} />
            <PathList title="本地提取文件" paths={exportResult?.pulledFiles ?? []} />

            <section className="detail-section">
              <div className="section-header compact">
                <h3>日志</h3>
                <span>{logs.length} 条</span>
              </div>
              <div className="log-list">
                {logs.length === 0 ? <p className="empty-inline">暂无日志</p> : logs.map((log, index) => <p key={`${index}-${log}`}>{log}</p>)}
              </div>
            </section>
          </aside>
        </div>
      </section>

      {devicePickerOpen ? (
        <div className="modal-backdrop">
          <section className="device-modal" role="dialog" aria-modal="true" aria-labelledby="device-picker-title">
            <div>
              <h3 id="device-picker-title">选择设备</h3>
              <p>检测到 {devices.length} 台已授权设备</p>
            </div>
            <div className="device-list">
              {devices.map((device) => (
                <button type="button" className="device-option" key={device.serial} disabled={actionBusy} onClick={() => void startScan(device.serial)}>
                  <span className="device-serial">{device.serial}</span>
                </button>
              ))}
            </div>
            <div className="modal-actions">
              <button type="button" disabled={actionBusy} onClick={() => setDevicePickerOpen(false)}>
                取消
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </main>
  );
}
