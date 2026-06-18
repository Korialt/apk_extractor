from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import queue
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from apk_extract_gui import __version__
from apk_extract_gui.adb import (
    AdbError,
    Device,
    InstalledApp,
    get_aapt_info,
    get_apk_paths,
    get_package_info,
    list_installed_apps,
    pull_apks,
    require_adb,
    require_single_device,
)
from apk_extract_gui.bundle import create_apks, create_xapk
from apk_extract_gui.icons import AppPresentation, inspect_app_presentation, safe_path_component


APP_TITLE = "APK Split 提取工具"
DEFAULT_EXPORT_DIR = "exports"
ICON_SIZE = 32


@dataclass(frozen=True)
class AppRow:
    package_name: str
    base_apk_path: str
    label: str | None
    icon_path: Path | None


@dataclass(frozen=True)
class ScanStarted:
    adb_path: Path
    device: Device
    total: int


@dataclass(frozen=True)
class ScanItem:
    index: int
    row: AppRow


@dataclass(frozen=True)
class ScanSummary:
    total: int
    icon_count: int


@dataclass(frozen=True)
class ExportResult:
    output_file: Path
    pulled_files: list[Path]
    remote_paths: list[str]
    note: str | None


class FormatSwitch(tk.Canvas):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, width=176, height=36, highlightthickness=0, bg="#f4f6f8")
        self.value = "apks"
        self.bind("<Button-1>", self._on_click)
        self.draw()

    def get(self) -> str:
        return self.value

    def _on_click(self, event: tk.Event[tk.Misc]) -> None:
        self.value = "apks" if event.x < 88 else "xapk"
        self.draw()

    def draw(self) -> None:
        self.delete("all")
        self.create_round_rect(0, 0, 176, 36, 18, fill="#d9dee5", outline="")
        selected_x = 3 if self.value == "apks" else 89
        self.create_round_rect(selected_x, 3, selected_x + 84, 33, 15, fill="#2474c6", outline="")
        self.create_text(
            44,
            18,
            text="APKS",
            fill="#ffffff" if self.value == "apks" else "#25313d",
            font=("TkDefaultFont", 10, "bold"),
        )
        self.create_text(
            132,
            18,
            text="XAPK",
            fill="#ffffff" if self.value == "xapk" else "#25313d",
            font=("TkDefaultFont", 10, "bold"),
        )

    def create_round_rect(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        radius: int,
        **kwargs: object,
    ) -> None:
        self.create_rectangle(x1 + radius, y1, x2 - radius, y2, **kwargs)
        self.create_rectangle(x1, y1 + radius, x2, y2 - radius, **kwargs)
        self.create_oval(x1, y1, x1 + radius * 2, y1 + radius * 2, **kwargs)
        self.create_oval(x2 - radius * 2, y1, x2, y1 + radius * 2, **kwargs)
        self.create_oval(x1, y2 - radius * 2, x1 + radius * 2, y2, **kwargs)
        self.create_oval(x2 - radius * 2, y2 - radius * 2, x2, y2, **kwargs)


class ApkExtractApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"{APP_TITLE} v{__version__}")
        self.root.geometry("980x660")
        self.root.minsize(820, 540)

        self.adb_path: Path | None = None
        self.device: Device | None = None
        self.apps: dict[str, AppRow] = {}
        self.icon_images: dict[str, tk.PhotoImage] = {}
        self.output_dir = tk.StringVar(value=str(Path.cwd() / DEFAULT_EXPORT_DIR))
        self.keyword = tk.StringVar()
        self.task_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.busy = False
        self.total_to_scan = 0

        self._build_ui()
        self.placeholder_icon = self._create_placeholder_icon()
        self.keyword.trace_add("write", lambda *_: self.apply_filter())
        self._set_busy(False)
        self.root.after(100, self._consume_queue)

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        top = ttk.Frame(self.root, padding=(16, 14, 16, 8))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="过滤关键词").grid(row=0, column=0, sticky="w")
        keyword_entry = ttk.Entry(top, textvariable=self.keyword)
        keyword_entry.grid(row=0, column=1, padx=(10, 10), sticky="ew")
        keyword_entry.bind("<Return>", lambda _: self.apply_filter())

        self.scan_button = ttk.Button(top, text="扫描全部", command=self.scan_apps)
        self.scan_button.grid(row=0, column=2, sticky="e")

        options = ttk.Frame(self.root, padding=(16, 4, 16, 8))
        options.grid(row=1, column=0, sticky="ew")
        options.columnconfigure(1, weight=1)

        ttk.Label(options, text="打包格式").grid(row=0, column=0, sticky="w")
        self.format_switch = FormatSwitch(options)
        self.format_switch.grid(row=0, column=1, sticky="w", padx=(10, 24))

        ttk.Label(options, text="输出目录").grid(row=0, column=2, sticky="e")
        output_entry = ttk.Entry(options, textvariable=self.output_dir)
        output_entry.grid(row=0, column=3, padx=(10, 8), sticky="ew")
        options.columnconfigure(3, weight=1)

        ttk.Button(options, text="选择", command=self.choose_output_dir).grid(row=0, column=4)

        body = ttk.Frame(self.root, padding=(16, 0, 16, 8))
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(0, weight=2)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(1, weight=1)

        ttk.Label(body, text="应用列表").grid(row=0, column=0, sticky="w")
        ttk.Label(body, text="路径和日志").grid(row=0, column=1, sticky="w", padx=(12, 0))

        package_frame = ttk.Frame(body)
        package_frame.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        package_frame.columnconfigure(0, weight=1)
        package_frame.rowconfigure(0, weight=1)

        self.app_tree = ttk.Treeview(
            package_frame,
            columns=("package", "label", "base_apk"),
            show="tree headings",
            selectmode="browse",
        )
        self.app_tree.heading("#0", text="图标")
        self.app_tree.heading("package", text="包名")
        self.app_tree.heading("label", text="应用名")
        self.app_tree.heading("base_apk", text="base APK")
        self.app_tree.column("#0", width=54, minwidth=48, stretch=False, anchor="center")
        self.app_tree.column("package", width=260, minwidth=180, stretch=True)
        self.app_tree.column("label", width=160, minwidth=100, stretch=True)
        self.app_tree.column("base_apk", width=260, minwidth=160, stretch=True)
        self.app_tree.grid(row=0, column=0, sticky="nsew")
        self.app_tree.bind("<<TreeviewSelect>>", lambda _: self.preview_selected_paths())

        package_scrollbar = ttk.Scrollbar(package_frame, orient="vertical", command=self.app_tree.yview)
        package_scrollbar.grid(row=0, column=1, sticky="ns")
        self.app_tree.configure(yscrollcommand=package_scrollbar.set)

        log_frame = ttk.Frame(body)
        log_frame.grid(row=1, column=1, sticky="nsew", padx=(12, 0), pady=(6, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, wrap="word", height=10)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        log_scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scrollbar.set)

        bottom = ttk.Frame(self.root, padding=(16, 0, 16, 16))
        bottom.grid(row=3, column=0, sticky="ew")
        bottom.columnconfigure(0, weight=1)

        self.status_label = ttk.Label(bottom, text="")
        self.status_label.grid(row=0, column=0, sticky="w")

        self.export_button = ttk.Button(bottom, text="提取并打包", command=self.export_selected)
        self.export_button.grid(row=0, column=1, sticky="e")

    def choose_output_dir(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.output_dir.get())
        if selected:
            self.output_dir.set(selected)

    def scan_apps(self) -> None:
        self._set_busy(True)
        self.apps = {}
        self.icon_images = {}
        self.total_to_scan = 0
        self._clear_app_tree()
        self._clear_log()
        self._append_log("开始扫描已安装应用...")
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def apply_filter(self) -> None:
        self._clear_app_tree()
        visible_count = 0
        for row in sorted(self.apps.values(), key=lambda app: app.package_name):
            if self._matches_filter(row):
                self._insert_app_row(row)
                visible_count += 1

        if not self.busy:
            self.status_label.configure(text=f"显示 {visible_count} / {len(self.apps)} 个应用")

    def preview_selected_paths(self) -> None:
        if self.busy:
            return

        package_name = self._selected_package()
        if package_name is None or self.adb_path is None or self.device is None:
            return

        self._set_busy(True)
        self._clear_log()
        self._append_log(f"查询 {package_name} 的 APK 路径...")
        self._run_in_thread("preview", lambda: get_apk_paths(self.adb_path, self.device, package_name))

    def export_selected(self) -> None:
        package_name = self._selected_package()
        if package_name is None:
            messagebox.showerror(APP_TITLE, "请先选择需要提取的包名。")
            return

        output_root = Path(self.output_dir.get()).expanduser()
        bundle_format = self.format_switch.get()

        self._set_busy(True)
        self._clear_log()
        self._append_log(f"开始提取 {package_name}，目标格式：{bundle_format.upper()}。")
        self._run_in_thread("export", lambda: self._export_package(package_name, output_root, bundle_format))

    def _scan_worker(self) -> None:
        try:
            adb_path = require_adb()
            device = require_single_device(adb_path)
            installed_apps = list_installed_apps(adb_path, device)
            self.task_queue.put(("scan:started", ScanStarted(adb_path=adb_path, device=device, total=len(installed_apps))))

            icon_cache = _icon_cache_root(device)
            icon_count = 0
            for index, app in enumerate(installed_apps, start=1):
                presentation = self._inspect_app_for_scan(adb_path, device, app, icon_cache)
                if presentation.note is not None:
                    self.task_queue.put(("scan:log", presentation.note))
                if presentation.icon_path is not None:
                    icon_count += 1

                row = AppRow(
                    package_name=app.package_name,
                    base_apk_path=app.base_apk_path,
                    label=presentation.label,
                    icon_path=presentation.icon_path,
                )
                self.task_queue.put(("scan:item", ScanItem(index=index, row=row)))

            self.task_queue.put(("scan:ok", ScanSummary(total=len(installed_apps), icon_count=icon_count)))
        except Exception as exc:
            self.task_queue.put(("scan:error", exc))

    def _inspect_app_for_scan(
        self,
        adb_path: Path,
        device: Device,
        app: InstalledApp,
        icon_cache: Path,
    ) -> AppPresentation:
        try:
            return inspect_app_presentation(adb_path, device, app, icon_cache)
        except AdbError as exc:
            return AppPresentation(label=None, icon_path=None, note=f"{app.package_name} 图标读取失败：{exc}")

    def _export_package(self, package_name: str, output_root: Path, bundle_format: str) -> ExportResult:
        if self.adb_path is None or self.device is None:
            self.adb_path = require_adb()
            self.device = require_single_device(self.adb_path)

        remote_paths = get_apk_paths(self.adb_path, self.device, package_name)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        package_dir = output_root / f"{package_name}_{timestamp}"
        apk_dir = package_dir / "apk"
        pulled_files = pull_apks(self.adb_path, self.device, remote_paths, apk_dir)

        bundle_file = package_dir / f"{package_name}.{bundle_format}"
        note: str | None = None
        if bundle_format == "apks":
            create_apks(pulled_files, bundle_file)
        else:
            package_info = get_package_info(self.adb_path, self.device, package_name)
            try:
                aapt_info = get_aapt_info(_find_base_apk(pulled_files))
            except AdbError as exc:
                aapt_info = None
                note = f"aapt 读取失败，已改用 dumpsys package 生成 XAPK manifest：{exc}"
            if aapt_info is None and note is None:
                note = "未检测到本机 aapt，XAPK manifest 的应用名称使用包名，版本信息使用 dumpsys package。"
            create_xapk(pulled_files, bundle_file, package_info, aapt_info)

        return ExportResult(
            output_file=bundle_file,
            pulled_files=pulled_files,
            remote_paths=remote_paths,
            note=note,
        )

    def _run_in_thread(self, task_name: str, callback: Callable[[], object]) -> None:
        def worker() -> None:
            try:
                result = callback()
            except Exception as exc:
                self.task_queue.put((f"{task_name}:error", exc))
            else:
                self.task_queue.put((f"{task_name}:ok", result))

        threading.Thread(target=worker, daemon=True).start()

    def _consume_queue(self) -> None:
        try:
            while True:
                task_name, payload = self.task_queue.get_nowait()
                self._handle_task_result(task_name, payload)
        except queue.Empty:
            pass
        self.root.after(100, self._consume_queue)

    def _handle_task_result(self, task_name: str, payload: object) -> None:
        if task_name.endswith(":error"):
            self._set_busy(False)
            self._append_log(str(payload))
            messagebox.showerror(APP_TITLE, str(payload))
            return

        if task_name == "scan:started":
            if not isinstance(payload, ScanStarted):
                raise TypeError("扫描任务返回了未知启动结果。")
            self.adb_path = payload.adb_path
            self.device = payload.device
            self.total_to_scan = payload.total
            self._append_log(f"设备：{payload.device.serial}")
            self._append_log(f"已安装包数量：{payload.total}")
            self.status_label.configure(text=f"扫描中：0 / {payload.total}")
            return

        if task_name == "scan:item":
            if not isinstance(payload, ScanItem):
                raise TypeError("扫描任务返回了未知应用结果。")
            self.apps[payload.row.package_name] = payload.row
            if self._matches_filter(payload.row):
                self._insert_app_row(payload.row)
            self.status_label.configure(text=f"扫描中：{payload.index} / {self.total_to_scan}")
            return

        if task_name == "scan:log":
            self._append_log(str(payload))
            return

        if task_name == "scan:ok":
            if not isinstance(payload, ScanSummary):
                raise TypeError("扫描任务返回了未知完成结果。")
            self._set_busy(False)
            self.apply_filter()
            self._append_log(f"扫描完成：{payload.total} 个应用，读取到 {payload.icon_count} 个图标。")
            return

        if task_name == "preview:ok":
            self._set_busy(False)
            if not isinstance(payload, list):
                raise TypeError("路径查询任务返回了未知结果。")
            self._append_log("设备 APK 路径：")
            for path in payload:
                self._append_log(f"  {path}")
            return

        if task_name == "export:ok":
            self._set_busy(False)
            if not isinstance(payload, ExportResult):
                raise TypeError("导出任务返回了未知结果。")
            self._append_log("设备 APK 路径：")
            for path in payload.remote_paths:
                self._append_log(f"  {path}")
            self._append_log("本地 APK 文件：")
            for path in payload.pulled_files:
                self._append_log(f"  {path}")
            if payload.note is not None:
                self._append_log(payload.note)
            self._append_log(f"打包完成：{payload.output_file}")
            messagebox.showinfo(APP_TITLE, f"打包完成：\n{payload.output_file}")

    def _matches_filter(self, row: AppRow) -> bool:
        keyword = self.keyword.get().strip().lower()
        if not keyword:
            return True

        if keyword in row.package_name.lower():
            return True
        return row.label is not None and keyword in row.label.lower()

    def _insert_app_row(self, row: AppRow) -> None:
        if self.app_tree.exists(row.package_name):
            self.app_tree.delete(row.package_name)

        self.app_tree.insert(
            "",
            tk.END,
            iid=row.package_name,
            image=self._image_for_row(row),
            values=(row.package_name, row.label or "", row.base_apk_path),
        )

    def _image_for_row(self, row: AppRow) -> tk.PhotoImage:
        if row.package_name in self.icon_images:
            return self.icon_images[row.package_name]
        if row.icon_path is None:
            return self.placeholder_icon

        try:
            image = tk.PhotoImage(file=str(row.icon_path))
        except tk.TclError:
            return self.placeholder_icon

        image = _subsample_to_fit(image, ICON_SIZE)
        self.icon_images[row.package_name] = image
        return image

    def _create_placeholder_icon(self) -> tk.PhotoImage:
        image = tk.PhotoImage(width=ICON_SIZE, height=ICON_SIZE)
        image.put("#d9dee5", to=(0, 0, ICON_SIZE, ICON_SIZE))
        image.put("#f7f9fb", to=(3, 3, ICON_SIZE - 3, ICON_SIZE - 3))
        image.put("#8f9baa", to=(10, 10, ICON_SIZE - 10, ICON_SIZE - 10))
        return image

    def _selected_package(self) -> str | None:
        selection = self.app_tree.selection()
        if not selection:
            return None
        return selection[0]

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        self.scan_button.configure(state=state)
        self.export_button.configure(state=state)
        self.status_label.configure(text="处理中..." if busy else f"显示 {self._visible_app_count()} / {len(self.apps)} 个应用")

    def _visible_app_count(self) -> int:
        return len(self.app_tree.get_children())

    def _clear_app_tree(self) -> None:
        for item_id in self.app_tree.get_children():
            self.app_tree.delete(item_id)

    def _clear_log(self) -> None:
        self.log_text.delete("1.0", tk.END)

    def _append_log(self, message: str) -> None:
        self.log_text.insert(tk.END, f"{message}\n")
        self.log_text.see(tk.END)


def _icon_cache_root(device: Device) -> Path:
    return Path(tempfile.gettempdir()) / "apk_extract_gui_icon_cache" / safe_path_component(device.serial)


def _subsample_to_fit(image: tk.PhotoImage, max_size: int) -> tk.PhotoImage:
    width = image.width()
    height = image.height()
    largest_side = max(width, height)
    if largest_side <= max_size:
        return image

    factor = (largest_side + max_size - 1) // max_size
    return image.subsample(factor, factor)


def _find_base_apk(apk_paths: list[Path]) -> Path:
    for apk_path in apk_paths:
        if apk_path.name == "base.apk":
            return apk_path
    return apk_paths[0]


def main() -> None:
    root = tk.Tk()
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    ApkExtractApp(root)
    root.mainloop()
