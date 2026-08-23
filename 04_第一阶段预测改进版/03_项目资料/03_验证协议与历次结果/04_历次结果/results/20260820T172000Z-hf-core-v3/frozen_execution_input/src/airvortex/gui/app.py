"""空气涡流统一 Tkinter/ttk 图形界面。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib
import math
from pathlib import Path
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Mapping, Sequence

from airvortex.core import BackendMode, Progress, SimulationConfig

from .model import (
    BACKEND_PRESENTATIONS,
    PARAMETER_BY_KEY,
    PARAMETER_SPECS,
    GuiViewModel,
    RunState,
)
from .plots import ComparisonPlots, FieldPlots, ProcessPlots, configure_chinese_fonts


MODE_TO_TEXT = {
    BackendMode.FORWARD: "前向理论",
    BackendMode.HIGH_FIDELITY: "H1 高保真预览",
}
TEXT_TO_MODE = {value: key for key, value in MODE_TO_TEXT.items()}


@dataclass(frozen=True)
class _SingleContext:
    mode: BackendMode
    config: SimulationConfig


@dataclass(frozen=True)
class _ComparisonPayload:
    results: Mapping[BackendMode, Any]
    configs: Mapping[BackendMode, SimulationConfig]


@dataclass(frozen=True)
class _ScanPayload:
    mode: BackendMode
    parameter_key: str
    display_values: tuple[float, ...]
    results: tuple[Any, ...]
    plan_hash: str
    planned_count: int


class _ScrollableFrame(ttk.Frame):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, width=355)
        self.scrollbar = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas, padding=(8, 6, 8, 10))
        self.window = self.canvas.create_window((0, 0), window=self.inner, anchor=tk.NW)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.inner.bind(
            "<Configure>",
            lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.bind(
            "<Configure>",
            lambda event: self.canvas.itemconfigure(self.window, width=event.width),
        )
        self.canvas.bind("<Enter>", lambda _event: self._bind_wheel())
        self.canvas.bind("<Leave>", lambda _event: self._unbind_wheel())

    def _bind_wheel(self) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        self.canvas.bind_all("<Button-4>", self._on_wheel)
        self.canvas.bind_all("<Button-5>", self._on_wheel)

    def _unbind_wheel(self) -> None:
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _on_wheel(self, event: tk.Event) -> None:
        if getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        else:
            delta = -int(getattr(event, "delta", 0) / 120)
        self.canvas.yview_scroll(delta, "units")


class AirVortexApp(tk.Tk):
    """底部磁子驱动自由表面气芯的双后端统一计算平台。"""

    POLL_MS = 100
    ANIMATION_MS = 160

    def __init__(self) -> None:
        super().__init__()
        self.title("空气涡流统一计算平台｜IYPT 第 10 题")
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        window_width = min(1440, max(1080, screen_width - 40))
        window_height = min(900, max(700, screen_height - 80))
        self.geometry(f"{window_width}x{window_height}")
        self.minsize(1080, 700)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.model = GuiViewModel()
        self._registry: Any = None
        self._controller: Any = None
        self._job_kind: str | None = None
        self._job_context: Any = None
        self._terminal_handled = False
        self._closing = False
        self._animation_running = False
        self._visual_time = 0.0
        self._last_progress_logged = -0.1
        self._last_stage = ""
        self._rendered_log_count = 0
        self._lockable_widgets: list[tk.Widget] = []
        self._parameter_widgets: dict[str, tk.Widget] = {}

        self.font_family = configure_chinese_fonts(self)
        self._configure_styles()
        self._build_menu()
        self._build_layout()
        self._discover_backends()
        self._refresh_backend_presentation()
        if hasattr(self, "start_button"):
            self._refresh_controls()
        self._refresh_plots()
        self.after(self.POLL_MS, self._poll_runtime)

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        available = style.theme_names()
        if "clam" in available:
            style.theme_use("clam")
        style.configure("Title.TLabel", font=(self.font_family, 18, "bold"), foreground="#0f172a")
        style.configure("Subtitle.TLabel", font=(self.font_family, 10), foreground="#475569")
        style.configure("BadgeForward.TLabel", background="#dbeafe", foreground="#1e3a8a", font=(self.font_family, 9, "bold"))
        style.configure("BadgeHF.TLabel", background="#ede9fe", foreground="#5b21b6", font=(self.font_family, 9, "bold"))
        style.configure("Capability.TLabel", background="#f8fafc", foreground="#1e293b")
        style.configure("Warning.TLabel", background="#fff7ed", foreground="#9a3412")
        style.configure("Available.TLabel", foreground="#166534")
        style.configure("Unavailable.TLabel", foreground="#b91c1c")
        style.configure("State.TLabel", font=(self.font_family, 9, "bold"))
        style.configure("Invalid.TEntry", fieldbackground="#fee2e2")
        style.configure("TLabelframe.Label", font=(self.font_family, 10, "bold"))
        style.configure("Accent.TButton", font=(self.font_family, 10, "bold"))

    def _build_menu(self) -> None:
        menu = tk.Menu(self)
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(label="保存配置…", command=self._save_config)
        file_menu.add_command(label="载入配置…", command=self._load_config)
        file_menu.add_separator()
        file_menu.add_command(label="导入实验 CSV…", command=self._import_experiment)
        file_menu.add_command(label="导出当前结果…", command=self._export_result)
        file_menu.add_command(label="导出一维扫描…", command=self._export_scan)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self._on_close)
        menu.add_cascade(label="文件", menu=file_menu)

        run_menu = tk.Menu(menu, tearoff=False)
        run_menu.add_command(label="启动", command=self._start_single)
        run_menu.add_command(label="暂停", command=self._pause_job)
        run_menu.add_command(label="继续", command=self._resume_job)
        run_menu.add_command(label="停止", command=self._stop_job)
        run_menu.add_separator()
        run_menu.add_command(label="运行双后端比较", command=self._start_comparison)
        menu.add_cascade(label="运行", menu=run_menu)

        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(label="模型与证据边界", command=self._show_boundaries)
        menu.add_cascade(label="帮助", menu=help_menu)
        self.configure(menu=menu)

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        header = ttk.Frame(self, padding=(16, 10, 16, 8))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="空气涡流统一计算平台", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="精确对象：圆柱容器内，底部水平磁子搅拌液体形成自由表面漏斗与顶部连通气芯",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.font_label = ttk.Label(
            header,
            text=f"中文字体：{self.font_family}",
            style="Subtitle.TLabel",
        )
        self.font_label.grid(row=0, column=1, rowspan=2, sticky="e")

        paned = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        paned.grid(row=1, column=0, sticky="nsew", padx=(8, 8), pady=(0, 6))
        self.left_scroll = _ScrollableFrame(paned)
        paned.add(self.left_scroll, weight=0)
        right = ttk.Frame(paned)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        paned.add(right, weight=1)
        self._build_left_panel(self.left_scroll.inner)
        self._build_notebook(right)
        self._build_statusbar()

    def _build_left_panel(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        model_frame = ttk.LabelFrame(parent, text="计算后端与证据等级", padding=8)
        model_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        model_frame.columnconfigure(0, weight=1)
        self.backend_var = tk.StringVar(value=MODE_TO_TEXT[self.model.backend_mode])
        self.backend_combo = ttk.Combobox(
            model_frame,
            state="readonly",
            textvariable=self.backend_var,
            values=tuple(TEXT_TO_MODE),
        )
        self.backend_combo.grid(row=0, column=0, sticky="ew")
        self.backend_combo.bind("<<ComboboxSelected>>", self._on_backend_changed)
        self._lockable_widgets.append(self.backend_combo)
        self.badge_label = ttk.Label(model_frame, anchor=tk.CENTER, padding=(6, 5))
        self.badge_label.grid(row=1, column=0, sticky="ew", pady=(6, 3))
        self.capability_label = ttk.Label(
            model_frame, style="Capability.TLabel", wraplength=315, justify=tk.LEFT, padding=(6, 4)
        )
        self.capability_label.grid(row=2, column=0, sticky="ew")
        self.evidence_label = ttk.Label(
            model_frame, style="Capability.TLabel", wraplength=315, justify=tk.LEFT, padding=(6, 4)
        )
        self.evidence_label.grid(row=3, column=0, sticky="ew")
        self.warning_label = ttk.Label(
            model_frame, style="Warning.TLabel", wraplength=315, justify=tk.LEFT, padding=(6, 5)
        )
        self.warning_label.grid(row=4, column=0, sticky="ew", pady=(2, 0))
        self.availability_label = ttk.Label(model_frame, wraplength=315, justify=tk.LEFT)
        self.availability_label.grid(row=5, column=0, sticky="ew", pady=(5, 0))
        self.hf_work_label = ttk.Label(
            model_frame, text="", style="Subtitle.TLabel", wraplength=315, justify=tk.LEFT
        )
        self.hf_work_label.grid(row=6, column=0, sticky="ew", pady=(4, 0))
        self.hf_quick_button = ttk.Button(
            model_frame, text="应用 H1 快速预览预设（显式改参）", command=self._apply_hf_quick
        )
        self.hf_quick_button.grid(row=7, column=0, sticky="ew", pady=(5, 0))
        self._lockable_widgets.append(self.hf_quick_button)

        self.parameter_holder = ttk.Frame(parent)
        self.parameter_holder.grid(row=1, column=0, sticky="ew")
        self.parameter_holder.columnconfigure(0, weight=1)
        self.param_vars = {
            spec.key: tk.StringVar(value=self.model.parameters[spec.key]) for spec in PARAMETER_SPECS
        }
        self._rebuild_parameter_fields()

        file_frame = ttk.LabelFrame(parent, text="配置与实验数据", padding=8)
        file_frame.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        file_frame.columnconfigure((0, 1), weight=1)
        self.save_button = ttk.Button(file_frame, text="保存配置…", command=self._save_config)
        self.load_button = ttk.Button(file_frame, text="载入配置…", command=self._load_config)
        self.import_button = ttk.Button(file_frame, text="导入实验 CSV…", command=self._import_experiment)
        self.export_button = ttk.Button(file_frame, text="导出结果+metadata…", command=self._export_result)
        self.save_button.grid(row=0, column=0, sticky="ew", padx=(0, 3), pady=2)
        self.load_button.grid(row=0, column=1, sticky="ew", padx=(3, 0), pady=2)
        self.import_button.grid(row=1, column=0, sticky="ew", padx=(0, 3), pady=2)
        self.export_button.grid(row=1, column=1, sticky="ew", padx=(3, 0), pady=2)
        self.experiment_label = ttk.Label(
            file_frame, text="实验数据：未导入", style="Subtitle.TLabel", wraplength=310, justify=tk.LEFT
        )
        self.experiment_label.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        self._lockable_widgets.extend([self.save_button, self.load_button, self.import_button])

        run_frame = ttk.LabelFrame(parent, text="运行控制", padding=8)
        run_frame.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        for column in range(4):
            run_frame.columnconfigure(column, weight=1)
        self.start_button = ttk.Button(run_frame, text="▶ 启动", style="Accent.TButton", command=self._start_single)
        self.pause_button = ttk.Button(run_frame, text="⏸ 暂停", command=self._pause_job)
        self.resume_button = ttk.Button(run_frame, text="继续", command=self._resume_job)
        self.stop_button = ttk.Button(run_frame, text="■ 停止", command=self._stop_job)
        self.start_button.grid(row=0, column=0, sticky="ew", padx=2)
        self.pause_button.grid(row=0, column=1, sticky="ew", padx=2)
        self.resume_button.grid(row=0, column=2, sticky="ew", padx=2)
        self.stop_button.grid(row=0, column=3, sticky="ew", padx=2)
        self.compare_button = ttk.Button(
            run_frame, text="以同一公共输入依次运行双后端比较", command=self._start_comparison
        )
        self.compare_button.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(6, 0))

        scan_frame = ttk.LabelFrame(parent, text="一维扫描", padding=8)
        scan_frame.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        scan_frame.columnconfigure(1, weight=1)
        self.scan_parameter_var = tk.StringVar(value="目标实际棒转速 [r/min]")
        self.scan_parameter_combo = ttk.Combobox(scan_frame, state="readonly", textvariable=self.scan_parameter_var)
        ttk.Label(scan_frame, text="参数").grid(row=0, column=0, sticky="w", padx=(0, 5))
        self.scan_parameter_combo.grid(row=0, column=1, columnspan=3, sticky="ew")
        self.scan_parameter_combo.bind("<<ComboboxSelected>>", self._on_scan_parameter_changed)
        ttk.Label(scan_frame, text="起值").grid(row=1, column=0, sticky="w", pady=(5, 0))
        self.scan_start_var = tk.StringVar(value="600")
        self.scan_stop_var = tk.StringVar(value="1200")
        self.scan_count_var = tk.StringVar(value="5")
        ttk.Entry(scan_frame, textvariable=self.scan_start_var, width=8).grid(row=1, column=1, sticky="ew", pady=(5, 0))
        ttk.Label(scan_frame, text="终值").grid(row=1, column=2, sticky="e", padx=(5, 3), pady=(5, 0))
        ttk.Entry(scan_frame, textvariable=self.scan_stop_var, width=8).grid(row=1, column=3, sticky="ew", pady=(5, 0))
        ttk.Label(scan_frame, text="点数").grid(row=2, column=0, sticky="w", pady=(5, 0))
        ttk.Spinbox(scan_frame, from_=2, to=100, textvariable=self.scan_count_var, width=8).grid(
            row=2, column=1, sticky="ew", pady=(5, 0)
        )
        self.scan_log_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(scan_frame, text="对数间隔", variable=self.scan_log_var).grid(
            row=2, column=2, columnspan=2, sticky="w", pady=(5, 0)
        )
        self.scan_button = ttk.Button(scan_frame, text="运行一维扫描", command=self._start_scan)
        self.scan_export_button = ttk.Button(scan_frame, text="导出扫描+metadata…", command=self._export_scan)
        self.scan_button.grid(row=3, column=0, columnspan=2, sticky="ew", padx=(0, 3), pady=(7, 0))
        self.scan_export_button.grid(row=3, column=2, columnspan=2, sticky="ew", padx=(3, 0), pady=(7, 0))
        self._lockable_widgets.extend(
            [self.scan_parameter_combo, self.scan_button, self.compare_button]
        )
        self._refresh_scan_choices()

    def _build_notebook(self, parent: ttk.Frame) -> None:
        self.notebook = ttk.Notebook(parent)
        self.notebook.grid(row=0, column=0, sticky="nsew")
        process_tab = ttk.Frame(self.notebook)
        field_tab = ttk.Frame(self.notebook)
        comparison_tab = ttk.Frame(self.notebook)
        diagnostics_tab = ttk.Frame(self.notebook)
        self.notebook.add(process_tab, text="装置截面与时间序列")
        self.notebook.add(field_tab, text="H1 相场 / 速度场")
        self.notebook.add(comparison_tab, text="双后端比较 / 一维扫描")
        self.notebook.add(diagnostics_tab, text="日志与诊断")

        self.process_plots = ProcessPlots(
            process_tab, on_seek=self._on_seek, on_toggle_play=self._toggle_animation
        )
        self.process_plots.pack(fill=tk.BOTH, expand=True)
        self.field_plots = FieldPlots(field_tab)
        self.field_plots.pack(fill=tk.BOTH, expand=True)
        comparison_tab.rowconfigure(0, weight=1)
        comparison_tab.columnconfigure(0, weight=1)
        self.comparison_plots = ComparisonPlots(comparison_tab)
        self.comparison_plots.grid(row=0, column=0, sticky="nsew")
        self.metrics_label = ttk.Label(
            comparison_tab,
            text="尚未形成双后端共同观测量。",
            style="Capability.TLabel",
            wraplength=900,
            justify=tk.LEFT,
            padding=(10, 7),
        )
        self.metrics_label.grid(row=1, column=0, sticky="ew")

        diagnostics_tab.rowconfigure(0, weight=1)
        diagnostics_tab.columnconfigure(0, weight=1)
        diag_paned = ttk.Panedwindow(diagnostics_tab, orient=tk.VERTICAL)
        diag_paned.grid(row=0, column=0, sticky="nsew")
        diag_frame = ttk.LabelFrame(diag_paned, text="数值与物理诊断", padding=5)
        log_frame = ttk.LabelFrame(diag_paned, text="运行日志", padding=5)
        diag_paned.add(diag_frame, weight=1)
        diag_paned.add(log_frame, weight=1)
        diag_frame.rowconfigure(0, weight=1)
        diag_frame.columnconfigure(0, weight=1)
        self.diagnostics_tree = ttk.Treeview(diag_frame, columns=("value",), show="tree headings")
        self.diagnostics_tree.heading("#0", text="诊断项")
        self.diagnostics_tree.heading("value", text="值")
        self.diagnostics_tree.column("#0", width=330, stretch=True)
        self.diagnostics_tree.column("value", width=420, stretch=True)
        diag_scroll = ttk.Scrollbar(diag_frame, orient=tk.VERTICAL, command=self.diagnostics_tree.yview)
        self.diagnostics_tree.configure(yscrollcommand=diag_scroll.set)
        self.diagnostics_tree.grid(row=0, column=0, sticky="nsew")
        diag_scroll.grid(row=0, column=1, sticky="ns")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, wrap="word", state=tk.DISABLED, height=10)
        log_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll.grid(row=0, column=1, sticky="ns")

    def _build_statusbar(self) -> None:
        footer = ttk.Frame(self, padding=(10, 4, 10, 7))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(1, weight=1)
        self.state_label = ttk.Label(footer, text="就绪", style="State.TLabel", width=12)
        self.state_label.grid(row=0, column=0, sticky="w")
        self.progressbar = ttk.Progressbar(footer, mode="determinate", maximum=1.0)
        self.progressbar.grid(row=0, column=1, sticky="ew", padx=8)
        self.progress_label = ttk.Label(footer, text="就绪", anchor=tk.E, width=42)
        self.progress_label.grid(row=0, column=2, sticky="e")

    def _rebuild_parameter_fields(self) -> None:
        for child in self.parameter_holder.winfo_children():
            child.destroy()
        self._parameter_widgets.clear()
        group_order = ("装置", "流体", "磁子", "驱动", "求解", "前向专属", "H1 专属")
        visible = self.model.specs_for_mode()
        row = 0
        for group in group_order:
            specs = [item for item in visible if item.group == group]
            if not specs:
                continue
            frame = ttk.LabelFrame(self.parameter_holder, text=group, padding=7)
            frame.grid(row=row, column=0, sticky="ew", pady=(0, 6))
            frame.columnconfigure(1, weight=1)
            for index, spec in enumerate(specs):
                ttk.Label(frame, text=spec.label).grid(row=index * 2, column=0, sticky="w", padx=(0, 5), pady=2)
                entry = ttk.Entry(frame, textvariable=self.param_vars[spec.key], width=13)
                entry.grid(row=index * 2, column=1, sticky="ew", pady=2)
                entry.bind("<FocusOut>", lambda _event, key=spec.key: self._validate_entry(key))
                ttk.Label(frame, text=spec.unit, width=9, anchor=tk.W).grid(
                    row=index * 2, column=2, sticky="w", padx=(5, 0), pady=2
                )
                self._parameter_widgets[spec.key] = entry
                if spec.help_text:
                    ttk.Label(
                        frame,
                        text=spec.help_text,
                        style="Subtitle.TLabel",
                        wraplength=290,
                        justify=tk.LEFT,
                    ).grid(row=index * 2 + 1, column=0, columnspan=3, sticky="ew", pady=(0, 3))
            row += 1
        if hasattr(self, "start_button"):
            self._refresh_controls()

    def _refresh_scan_choices(self) -> None:
        specs = [
            item
            for item in self.model.specs_for_mode()
            if not item.integer and item.key not in {"duration_s", "output_dt_s", "ramp_time_s"}
        ]
        self._scan_display_to_key = {
            f"{item.label} [{item.unit}]" if item.unit else item.label: item.key for item in specs
        }
        self.scan_parameter_combo.configure(values=tuple(self._scan_display_to_key))
        current = self.scan_parameter_var.get()
        if current not in self._scan_display_to_key:
            rpm_display = next(
                (text for text, key in self._scan_display_to_key.items() if key == "rpm"),
                next(iter(self._scan_display_to_key), ""),
            )
            self.scan_parameter_var.set(rpm_display)
            self._on_scan_parameter_changed()

    def _on_scan_parameter_changed(self, _event: tk.Event | None = None) -> None:
        key = self._scan_display_to_key.get(self.scan_parameter_var.get())
        if key is None:
            return
        try:
            value = float(self.param_vars[key].get())
        except ValueError:
            value = float(PARAMETER_BY_KEY[key].default)
        if value == 0.0:
            start, stop = 0.0, max(1.0, PARAMETER_BY_KEY[key].maximum * 0.1)
        else:
            start, stop = value * 0.75, value * 1.25
        self.scan_start_var.set(f"{start:g}")
        self.scan_stop_var.set(f"{stop:g}")

    def _on_backend_changed(self, _event: tk.Event | None = None) -> None:
        try:
            mode = TEXT_TO_MODE[self.backend_var.get()]
            self.model.set_backend(mode)
        except (KeyError, RuntimeError, ValueError) as exc:
            self.backend_var.set(MODE_TO_TEXT[self.model.backend_mode])
            messagebox.showerror("无法切换后端", str(exc), parent=self)
            return
        self._rebuild_parameter_fields()
        self._refresh_scan_choices()
        self._refresh_backend_presentation()
        self._visual_time = 0.0
        self._refresh_plots()

    def _refresh_backend_presentation(self) -> None:
        presentation = self.model.presentation
        badge_style = (
            "BadgeForward.TLabel"
            if self.model.backend_mode is BackendMode.FORWARD
            else "BadgeHF.TLabel"
        )
        self.badge_label.configure(
            text=presentation.badge.replace(" / ", "\n"), style=badge_style
        )
        self.capability_label.configure(text=presentation.capability.replace("；", "\n"))
        self.evidence_label.configure(text=presentation.evidence.replace("；", "\n"))
        result_warnings = self.model.last_job_warnings
        if result_warnings:
            shown = result_warnings[:2]
            suffix = (
                f"\n…另有 {len(result_warnings) - len(shown)} 条，详见“日志与诊断”。"
                if len(result_warnings) > len(shown)
                else ""
            )
            warning_text = "\n".join(f"⚠ {item}" for item in shown) + suffix
            warning_text += f"\n\n{presentation.warning}"
        else:
            warning_text = presentation.warning
        self.warning_label.configure(text=warning_text)
        available, detail = self.model.backend_availability[self.model.backend_mode]
        self.availability_label.configure(
            text=f"后端可用：{'是' if available else '否'}｜{detail}",
            style="Available.TLabel" if available else "Unavailable.TLabel",
        )
        is_hf = self.model.backend_mode is BackendMode.HIGH_FIDELITY
        if is_hf:
            self.hf_work_label.configure(text=self.model.hf_work_estimate())
            self.hf_quick_button.grid()
        else:
            self.hf_work_label.configure(text="")
            self.hf_quick_button.grid_remove()

    def _discover_backends(self) -> None:
        try:
            runtime = importlib.import_module("airvortex.runtime")
            self._JobController = runtime.JobController
            self._JobState = runtime.JobState
            self._registry = runtime.discover_backends(probe=True)
            errors = self._registry.discovery_errors
            for mode in (BackendMode.FORWARD, BackendMode.HIGH_FIDELITY):
                error = errors.get(mode.value)
                self.model.set_backend_availability(
                    mode,
                    error is None,
                    "已通过延迟构造探测" if error is None else error,
                )
            self._log("INFO", "runtime 与双后端注册表已发现；具体求解器按需构造。")
        except Exception as exc:
            self._registry = None
            detail = f"{type(exc).__name__}: {exc}"
            for mode in (BackendMode.FORWARD, BackendMode.HIGH_FIDELITY):
                self.model.set_backend_availability(mode, False, detail)
            self._log("ERROR", f"运行时发现失败：{detail}")

    def _sync_parameters(self) -> None:
        if not self.model.button_policy().physical_controls:
            raise RuntimeError("运行期间参数已锁定")
        for spec in PARAMETER_SPECS:
            self.model.set_parameter(spec.key, self.param_vars[spec.key].get())
        _, issues = self.model.parsed_parameters()
        for widget in self._parameter_widgets.values():
            widget.configure(style="TEntry")
        for issue in issues:
            widget = self._parameter_widgets.get(issue.key)
            if widget is not None:
                widget.configure(style="Invalid.TEntry")
        if issues:
            raise ValueError("；".join(item.message for item in issues))

    def _validate_entry(self, key: str) -> None:
        widget = self._parameter_widgets.get(key)
        if widget is None:
            return
        try:
            PARAMETER_BY_KEY[key].parse(self.param_vars[key].get())
        except ValueError:
            widget.configure(style="Invalid.TEntry")
        else:
            widget.configure(style="TEntry")
        if self.model.backend_mode is BackendMode.HIGH_FIDELITY:
            for item in ("duration_s", "hf_dt_s", "grid_nr", "grid_ntheta", "grid_nz"):
                if item in self.param_vars:
                    self.model.parameters[item] = self.param_vars[item].get()
            self.hf_work_label.configure(text=self.model.hf_work_estimate())

    def _apply_hf_quick(self) -> None:
        try:
            detail = self.model.apply_hf_quick_preset()
        except Exception as exc:
            messagebox.showerror("无法应用预设", str(exc), parent=self)
            return
        for key, value in self.model.parameters.items():
            self.param_vars[key].set(value)
        self._log("INFO", f"已显式应用 H1 快速预览预设：{detail}")
        self._refresh_backend_presentation()

    def _save_config(self) -> None:
        try:
            self._sync_parameters()
            path = filedialog.asksaveasfilename(
                parent=self,
                title="保存空气涡流 core 配置",
                defaultextension=".json",
                filetypes=(("JSON 配置", "*.json"), ("所有文件", "*.*")),
            )
            if not path:
                return
            saved = self.model.save_config(path)
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)
            return
        self._log("INFO", f"配置已保存：{saved}")

    def _load_config(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="载入空气涡流 core 配置",
            filetypes=(("JSON 配置", "*.json"), ("所有文件", "*.*")),
        )
        if not path:
            return
        try:
            self.model.load_config(path)
        except Exception as exc:
            messagebox.showerror("载入失败", str(exc), parent=self)
            return
        for key, value in self.model.parameters.items():
            self.param_vars[key].set(value)
        self.backend_var.set(MODE_TO_TEXT[self.model.backend_mode])
        self._rebuild_parameter_fields()
        self._refresh_scan_choices()
        self._refresh_backend_presentation()
        self._refresh_plots()
        self._log("INFO", f"已载入并经 core 严格校验：{Path(path).resolve()}")

    def _import_experiment(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="导入实验 CSV（推荐同时提供同名 .meta.json）",
            filetypes=(("CSV 数据", "*.csv"), ("所有文件", "*.*")),
        )
        if not path:
            return
        try:
            data = self.model.import_experiment_csv(path)
        except Exception as exc:
            messagebox.showerror("实验数据导入失败", str(exc), parent=self)
            return
        role_text = {
            "calibration": "标定集",
            "sealed_validation": "密封验证集",
            "unassigned_quick_import": "快速导入（未分配角色）",
        }.get(data.role, data.role)
        self.experiment_label.configure(
            text=f"实验数据：{data.path.name}｜{len(data.time_s)} 行｜{role_text}"
        )
        warning = data.metadata.get("warning")
        self._log("WARNING" if warning else "INFO", warning or f"实验数据已导入：{data.path}")
        self._refresh_plots()

    def _export_result(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出当前后端结果（自动写 metadata 旁车）",
            defaultextension=".csv",
            filetypes=(("CSV 数据", "*.csv"), ("所有文件", "*.*")),
        )
        if not path:
            return
        try:
            csv_path, metadata_path = self.model.export_result_bundle(path)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)
            return
        self._log("INFO", f"结果已导出：{csv_path}；证据元数据：{metadata_path}")
        messagebox.showinfo(
            "导出完成",
            f"数值：{csv_path}\n元数据：{metadata_path}\n\nmetadata 为必需旁车，未静默降级。",
            parent=self,
        )

    def _export_scan(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出一维扫描（自动写 metadata 旁车）",
            defaultextension=".csv",
            filetypes=(("CSV 数据", "*.csv"), ("所有文件", "*.*")),
        )
        if not path:
            return
        try:
            csv_path, metadata_path = self.model.export_scan_bundle(path)
        except Exception as exc:
            messagebox.showerror("扫描导出失败", str(exc), parent=self)
            return
        self._log("INFO", f"扫描已导出：{csv_path}；证据元数据：{metadata_path}")

    def _new_controller(self) -> Any:
        if self._registry is None:
            raise RuntimeError("runtime/backend 注册表不可用，请查看日志")
        if self._controller is not None:
            raise RuntimeError("已有任务正在运行")
        return self._JobController()

    def _start_single(self) -> None:
        try:
            self._sync_parameters()
            mode = self.model.backend_mode
            available, detail = self.model.backend_availability[mode]
            if not available:
                raise RuntimeError(f"所选后端不可用：{detail}")
            config = self.model.runtime_config(mode)
            backend = self._registry.create(mode.value)
            controller = self._new_controller()
            controller.start(
                lambda control, progress: backend.run(
                    config, control=control, progress=progress
                )
            )
        except Exception as exc:
            messagebox.showerror("无法启动", str(exc), parent=self)
            self._log("ERROR", f"启动失败：{type(exc).__name__}: {exc}")
            return
        self._activate_job(controller, "single", _SingleContext(mode, config))
        self._log("INFO", f"已启动 {MODE_TO_TEXT[mode]}；物理与求解控件已锁定。")

    def _start_comparison(self) -> None:
        try:
            self._sync_parameters()
            missing = [
                MODE_TO_TEXT[mode]
                for mode in (BackendMode.FORWARD, BackendMode.HIGH_FIDELITY)
                if not self.model.backend_availability[mode][0]
            ]
            if missing:
                raise RuntimeError(f"后端不可用：{'、'.join(missing)}")
            work = self.model.hf_work_estimate()
            if not messagebox.askyesno(
                "运行双后端比较",
                "将以同一公共装置/流体/磁子/实际棒转速参数依次运行两个后端。\n\n"
                f"{work}\n\nH1 仅为 MECHANISM / PRE-CONTACT ONLY，不是完整 VOF。继续吗？",
                parent=self,
            ):
                return
            configs = {
                BackendMode.FORWARD: self.model.runtime_config(BackendMode.FORWARD),
                BackendMode.HIGH_FIDELITY: self.model.runtime_config(BackendMode.HIGH_FIDELITY),
            }
            backends = {
                mode: self._registry.create(mode.value)
                for mode in (BackendMode.FORWARD, BackendMode.HIGH_FIDELITY)
            }
            controller = self._new_controller()

            def target(control, progress):
                results: dict[BackendMode, Any] = {}
                modes = (BackendMode.FORWARD, BackendMode.HIGH_FIDELITY)
                for index, mode in enumerate(modes):
                    if not control.checkpoint():
                        break

                    def relay(item: Progress, *, offset=index) -> None:
                        progress(
                            Progress(
                                fraction=(offset + item.fraction) / len(modes),
                                simulated_time_s=item.simulated_time_s,
                                step=item.step,
                                stage=f"{mode.value}:{item.stage}",
                                message=item.message,
                                metrics=item.metrics,
                            )
                        )

                    results[mode] = backends[mode].run(
                        configs[mode], control=control, progress=relay
                    )
                    if control.stop_requested:
                        break
                return _ComparisonPayload(results, configs)

            controller.start(target)
        except Exception as exc:
            messagebox.showerror("无法启动比较", str(exc), parent=self)
            self._log("ERROR", f"双后端比较启动失败：{type(exc).__name__}: {exc}")
            return
        self._activate_job(controller, "comparison", None)
        self._log("INFO", "双后端比较已启动；顺序为前向理论 → H1 高保真预览。")

    def _start_scan(self) -> None:
        key = self._scan_display_to_key.get(self.scan_parameter_var.get())
        if key is None:
            messagebox.showerror("扫描参数错误", "请选择扫描参数", parent=self)
            return
        try:
            self._sync_parameters()
            mode = self.model.backend_mode
            available, detail = self.model.backend_availability[mode]
            if not available:
                raise RuntimeError(f"所选后端不可用：{detail}")
            display_values = self.model.scan_values(
                key,
                self.scan_start_var.get(),
                self.scan_stop_var.get(),
                self.scan_count_var.get(),
                logarithmic=self.scan_log_var.get(),
            )
            spec = PARAMETER_BY_KEY[key]
            runtime_values = tuple(value * spec.scale_to_si for value in display_values)
            from airvortex.analysis import SweepAxis, generate_sweep_plan

            base_config = self.model.runtime_config(mode)
            plan = generate_sweep_plan(
                base_config,
                [SweepAxis(spec.runtime_key, runtime_values)],
            )
            controller = self._new_controller()

            def target(control, progress):
                results: list[Any] = []
                completed_values: list[float] = []
                total = len(plan.jobs)
                for index, job in enumerate(plan.jobs):
                    if not control.checkpoint():
                        break
                    config = SimulationConfig.from_dict(dict(job.config_snapshot))
                    backend = self._registry.create(mode.value)

                    def relay(item: Progress, *, offset=index) -> None:
                        progress(
                            Progress(
                                fraction=(offset + item.fraction) / total,
                                simulated_time_s=item.simulated_time_s,
                                step=item.step,
                                stage=f"scan:{offset + 1}/{total}:{item.stage}",
                                message=item.message,
                                metrics=item.metrics,
                            )
                        )

                    results.append(backend.run(config, control=control, progress=relay))
                    completed_values.append(display_values[index])
                    if control.stop_requested:
                        break
                return _ScanPayload(
                    mode,
                    key,
                    tuple(completed_values),
                    tuple(results),
                    plan.base_config_hash,
                    len(plan.jobs),
                )

            controller.start(target)
        except Exception as exc:
            messagebox.showerror("无法启动扫描", str(exc), parent=self)
            self._log("ERROR", f"扫描启动失败：{type(exc).__name__}: {exc}")
            return
        self._activate_job(controller, "scan", None)
        self._log(
            "INFO",
            f"已启动一维扫描：{PARAMETER_BY_KEY[key].label}，{len(display_values)} 点，后端={mode.value}。",
        )

    def _activate_job(self, controller: Any, kind: str, context: Any) -> None:
        self._controller = controller
        self._job_kind = kind
        self._job_context = context
        self._terminal_handled = False
        self._last_progress_logged = -0.1
        self._last_stage = ""
        self.model.begin_result_batch()
        self.model.progress = 0.0
        self.model.progress_message = "正在验证配置…"
        self.model.set_run_state(RunState.RUNNING)
        self._refresh_backend_presentation()
        self._refresh_controls()

    def _pause_job(self) -> None:
        if self._controller is None:
            return
        try:
            self._controller.pause(wait=False)
            self.model.progress_message = "已请求暂停，等待后端安全检查点…"
            self._log("INFO", "暂停请求已发送；后端将在下一个 cooperative checkpoint 确认。")
        except Exception as exc:
            messagebox.showerror("暂停失败", str(exc), parent=self)
        self._refresh_controls()

    def _resume_job(self) -> None:
        if self._controller is None:
            return
        try:
            self._controller.resume()
            self.model.set_run_state(RunState.RUNNING)
            self.model.progress_message = "已继续运行"
            self._log("INFO", "任务已继续。")
        except Exception as exc:
            messagebox.showerror("继续失败", str(exc), parent=self)
        self._refresh_controls()

    def _stop_job(self) -> None:
        if self._controller is None:
            return
        try:
            self._controller.cancel("GUI user requested stop")
            self.model.set_run_state(RunState.STOPPING)
            self.model.progress_message = "正在软停止并保留已计算数据…"
            self._log("WARNING", "已请求软停止；等待后端检查点并保留部分结果。")
        except Exception as exc:
            messagebox.showerror("停止失败", str(exc), parent=self)
        self._refresh_controls()

    def _poll_runtime(self) -> None:
        try:
            if self._controller is not None:
                for message in self._controller.poll(limit=200):
                    self._handle_job_message(message)
                state = self._controller.state
                if state is self._JobState.PAUSED and self.model.run_state is RunState.RUNNING:
                    self.model.set_run_state(RunState.PAUSED)
                    self.model.progress_message = "已暂停（后端已确认检查点）"
                elif state is self._JobState.RUNNING and self.model.run_state is RunState.PAUSED:
                    self.model.set_run_state(RunState.RUNNING)
                if self._controller.is_terminal and not self._terminal_handled:
                    self._finish_active_job()
            self._render_new_logs()
            self._refresh_controls()
        finally:
            if self.winfo_exists() and not self._closing:
                self.after(self.POLL_MS, self._poll_runtime)

    def _handle_job_message(self, message: Any) -> None:
        payload = dict(message.payload)
        if message.kind == "progress":
            fraction = payload.get("fraction", self.model.progress)
            text = str(payload.get("message") or payload.get("stage") or "运行中")
            simulated = payload.get("simulated_time_s")
            if simulated is not None:
                text = f"{text}｜模拟 t={float(simulated):.4g} s"
            self.model.update_progress(fraction, text)
            metrics = payload.get("metrics")
            if isinstance(metrics, Mapping):
                self.model.diagnostics.update({f"runtime.{key}": value for key, value in metrics.items()})
            stage = str(payload.get("stage", ""))
            if stage != self._last_stage or self.model.progress - self._last_progress_logged >= 0.05:
                self._log("INFO", f"进度 {self.model.progress:.1%}｜{text}")
                self._last_progress_logged = self.model.progress
                self._last_stage = stage
        elif message.kind == "state":
            current = payload.get("current", "unknown")
            self._log("INFO", f"runtime 状态：{payload.get('previous', '?')} → {current}")
        elif message.kind == "error":
            self._log("ERROR", f"{payload.get('type', 'Error')}: {payload.get('message', '')}")

    def _finish_active_job(self) -> None:
        controller = self._controller
        if controller is None:
            return
        self._terminal_handled = True
        state = controller.state
        result = controller.result
        try:
            if state is self._JobState.FAILED:
                error = controller.error
                self.model.set_run_state(RunState.FAILED)
                self.model.progress_message = f"失败：{error}"
                self._log("ERROR", controller.traceback_text or str(error))
                messagebox.showerror("计算失败", str(error), parent=self)
            elif state is self._JobState.CANCELLED:
                if result is not None:
                    self._accept_job_payload(result)
                self.model.set_run_state(RunState.CANCELLED)
                self.model.progress_message = (
                    "已停止；已保留可用的部分结果"
                    if result is not None
                    else "已停止；本次没有形成可用结果"
                )
                self._log(
                    "WARNING",
                    "任务已软停止；"
                    + ("可用部分结果已保留。" if result is not None else "没有可用部分结果。"),
                )
            else:
                self._accept_job_payload(result)
                if self.model.last_job_warnings:
                    self.model.set_run_state(RunState.DOMAIN_LIMIT)
                    self.model.progress_message = self.model.last_job_warnings[0]
                    for warning in self.model.last_job_warnings:
                        self._log("WARNING", warning)
                else:
                    self.model.set_run_state(RunState.COMPLETED)
                    self.model.progress = 1.0
                    self.model.progress_message = "计算完成"
                    self._log("INFO", "任务完成。")
        except Exception as exc:
            self.model.set_run_state(RunState.FAILED, force=True)
            self.model.progress_message = f"结果接收失败：{exc}"
            self._log("ERROR", f"结果接收失败：{type(exc).__name__}: {exc}")
            messagebox.showerror("结果接收失败", str(exc), parent=self)
        finally:
            controller.join(timeout=0.0)
            self._controller = None
            self._job_kind = None
            self._job_context = None
            self._refresh_plots()
            self._refresh_diagnostics()
            self._reset_timeline_to_result()
            self._refresh_backend_presentation()
            self._refresh_controls()
            if self._closing:
                self.destroy()

    def _accept_job_payload(self, payload: Any) -> None:
        runtime_manifest = self._controller.manifest() if self._controller is not None else None
        if self._job_kind == "single":
            if payload is None:
                raise RuntimeError("后端没有返回结果")
            context: _SingleContext = self._job_context
            self.model.accept_result(
                payload,
                context.mode,
                config=context.config,
                runtime_manifest=runtime_manifest,
            )
        elif self._job_kind == "comparison":
            if not isinstance(payload, _ComparisonPayload):
                raise TypeError("双后端任务返回类型不匹配")
            for mode, raw_result in payload.results.items():
                self.model.accept_result(
                    raw_result,
                    mode,
                    config=payload.configs[mode],
                    runtime_manifest=runtime_manifest,
                )
        elif self._job_kind == "scan":
            if not isinstance(payload, _ScanPayload):
                raise TypeError("扫描任务返回类型不匹配")
            self.model.accept_scan_results(
                mode=payload.mode,
                parameter_key=payload.parameter_key,
                input_values=payload.display_values,
                results=payload.results,
                plan_hash=payload.plan_hash,
                planned_count=payload.planned_count,
            )
        else:
            raise RuntimeError("未知任务类型")

    def _refresh_controls(self) -> None:
        policy = self.model.button_policy()
        runtime_state = self._controller.state if self._controller is not None else None
        self.start_button.configure(state=tk.NORMAL if policy.start else tk.DISABLED)
        can_pause = policy.pause and runtime_state is self._JobState.RUNNING if self._controller is not None else False
        can_resume = policy.resume and runtime_state is self._JobState.PAUSED if self._controller is not None else False
        self.pause_button.configure(state=tk.NORMAL if can_pause else tk.DISABLED)
        self.resume_button.configure(state=tk.NORMAL if can_resume else tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL if policy.stop else tk.DISABLED)
        for widget in self._lockable_widgets:
            try:
                if widget is self.backend_combo or widget is self.scan_parameter_combo:
                    widget.configure(state="readonly" if policy.physical_controls else tk.DISABLED)
                else:
                    widget.configure(state=tk.NORMAL if policy.physical_controls else tk.DISABLED)
            except tk.TclError:
                pass
        for widget in self._parameter_widgets.values():
            widget.configure(state=tk.NORMAL if policy.physical_controls else tk.DISABLED)
        self.compare_button.configure(state=tk.NORMAL if policy.start else tk.DISABLED)
        self.scan_button.configure(state=tk.NORMAL if policy.start else tk.DISABLED)
        self.progressbar.configure(value=self.model.progress)
        labels = {
            RunState.IDLE: "就绪",
            RunState.RUNNING: "运行中",
            RunState.PAUSED: "已暂停",
            RunState.STOPPING: "正在停止",
            RunState.COMPLETED: "已完成",
            RunState.DOMAIN_LIMIT: "模型域边界终止",
            RunState.CANCELLED: "已停止",
            RunState.FAILED: "失败",
        }
        self.state_label.configure(text=labels[self.model.run_state])
        self.progress_label.configure(text=self.model.progress_message)

    def _log(self, level: str, message: Any) -> None:
        self.model.append_log(level, message)

    def _render_new_logs(self) -> None:
        entries = self.model.logs[self._rendered_log_count :]
        if not entries:
            return
        self.log_text.configure(state=tk.NORMAL)
        for entry in entries:
            self.log_text.insert(tk.END, f"[{entry.timestamp}] {entry.level:<7} {entry.message}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)
        self._rendered_log_count = len(self.model.logs)

    def _refresh_diagnostics(self) -> None:
        self.diagnostics_tree.delete(*self.diagnostics_tree.get_children())
        presentation = self.model.presentation
        base = {
            "backend": self.model.backend_mode.value,
            "evidence_badge": presentation.badge,
            "capability_boundary": presentation.warning,
            "result_warnings": list(self.model.last_job_warnings),
            **self.model.diagnostics,
        }
        if self.model.experiment is not None:
            base["experiment.role"] = self.model.experiment.role
            base["experiment.rows"] = len(self.model.experiment.time_s)
        for key, value in _flatten_mapping(base):
            self.diagnostics_tree.insert("", tk.END, text=key, values=(_format_value(value),))

    def _reset_timeline_to_result(self) -> None:
        result = self.model.results.get(self.model.backend_mode)
        self._visual_time = 0.0 if result is None or not result.time_s else float(result.time_s[0])
        maximum = 1.0 if result is None or not result.time_s else float(result.time_s[-1])
        self.process_plots.set_timeline(maximum, self._visual_time)

    def _refresh_plots(self) -> None:
        mode = self.model.backend_mode
        result = self.model.results.get(mode)
        maximum = 1.0 if result is None or not result.time_s else float(result.time_s[-1])
        self._visual_time = min(max(self._visual_time, 0.0), maximum)
        self.process_plots.set_timeline(maximum, self._visual_time)
        self.process_plots.render(self.model, mode, self._visual_time)
        self.field_plots.render(self.model, self._visual_time)
        metrics = self.comparison_plots.render(self.model)
        self.metrics_label.configure(text=metrics)

    def _on_seek(self, value: float) -> None:
        if self.process_plots.updating_scale:
            return
        self._visual_time = float(value)
        self._animation_running = False
        self.process_plots.set_playing(False)
        self._refresh_plots()

    def _toggle_animation(self) -> None:
        result = self.model.results.get(self.model.backend_mode)
        if result is None or not result.time_s:
            return
        self._animation_running = not self._animation_running
        self.process_plots.set_playing(self._animation_running)
        if self._animation_running:
            self.after(self.ANIMATION_MS, self._advance_animation)

    def _advance_animation(self) -> None:
        if not self._animation_running or not self.winfo_exists():
            return
        result = self.model.results.get(self.model.backend_mode)
        if result is None or not result.time_s:
            self._animation_running = False
            self.process_plots.set_playing(False)
            return
        index = nearest_time_index(result.time_s, self._visual_time)
        index = (index + 1) % len(result.time_s)
        self._visual_time = float(result.time_s[index])
        self._refresh_plots()
        self.after(self.ANIMATION_MS, self._advance_animation)

    def _show_boundaries(self) -> None:
        forward = BACKEND_PRESENTATIONS[BackendMode.FORWARD]
        high = BACKEND_PRESENTATIONS[BackendMode.HIGH_FIDELITY]
        messagebox.showinfo(
            "模型与证据边界",
            f"前向理论｜{forward.badge}\n{forward.capability}\n{forward.warning}\n\n"
            f"H1 高保真预览｜{high.badge}\n{high.capability}\n{high.warning}\n\n"
            "转速输入、阈值输出均指实际磁子 RPM；搅拌面板命令只作说明。",
            parent=self,
        )

    def _on_close(self) -> None:
        if self._controller is not None and not self._controller.is_terminal:
            if not messagebox.askyesno(
                "任务仍在运行",
                "退出前将请求软停止，并等待后端安全检查点。确定退出吗？",
                parent=self,
            ):
                return
            self._closing = True
            try:
                self._controller.cancel("GUI closing")
                self.model.set_run_state(RunState.STOPPING, force=True)
            except Exception:
                pass
            self.after(self.POLL_MS, self._poll_close)
            return
        self.destroy()

    def _poll_close(self) -> None:
        if self._controller is None or self._controller.join(timeout=0.0):
            self.destroy()
        else:
            self.after(self.POLL_MS, self._poll_close)

    def save_screenshot(self, path: str | Path) -> Path:
        """保存当前窗口截图；仅用于显式请求的 GUI 烟测证据。"""

        self.update_idletasks()
        self.update()
        try:
            from PIL import ImageGrab
        except ImportError as exc:
            raise RuntimeError("截图需要 Pillow") from exc
        x = self.winfo_rootx()
        y = self.winfo_rooty()
        width = self.winfo_width()
        height = self.winfo_height()
        destination = Path(path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        ImageGrab.grab(bbox=(x, y, x + width, y + height)).save(destination)
        return destination


def nearest_time_index(times: Sequence[float], value: float) -> int:
    if not times:
        return 0
    return min(range(len(times)), key=lambda index: abs(float(times[index]) - value))


def _flatten_mapping(value: Mapping[str, Any], prefix: str = "") -> list[tuple[str, Any]]:
    output: list[tuple[str, Any]] = []
    for key in sorted(value, key=str):
        item = value[key]
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, Mapping):
            output.extend(_flatten_mapping(item, path))
        elif isinstance(item, (list, tuple)):
            output.append((path, f"[{len(item)} 项] " + "; ".join(map(str, item[:3]))))
        else:
            output.append((path, item))
    return output


def _format_value(value: Any) -> str:
    if isinstance(value, float):
        if not math.isfinite(value):
            return str(value)
        return f"{value:.8g}"
    return str(value)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="空气涡流统一 Tkinter/Matplotlib GUI")
    parser.add_argument(
        "--smoke-test",
        nargs="?",
        const=0.8,
        type=float,
        metavar="SECONDS",
        help="启动后在指定秒数自动关闭（默认 0.8 s）",
    )
    parser.add_argument("--screenshot", type=Path, help="烟测期间保存窗口截图")
    args = parser.parse_args(argv)
    if args.smoke_test is not None and args.smoke_test <= 0:
        parser.error("--smoke-test 秒数必须大于零")
    return args


def launch(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    app = AirVortexApp()

    if args.screenshot is not None:
        def capture() -> None:
            try:
                saved = app.save_screenshot(args.screenshot)
                print(f"GUI_SCREENSHOT={saved}", flush=True)
            except Exception as exc:
                print(f"GUI_SCREENSHOT_ERROR={type(exc).__name__}: {exc}", file=sys.stderr, flush=True)

        app.after(350, capture)
    if args.smoke_test is not None:
        app.after(max(250, int(args.smoke_test * 1000)), app.destroy)
    app.mainloop()


if __name__ == "__main__":
    launch()
