"""Tk/Matplotlib 绘图部件；只消费 ViewModel 的归一化数据。"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from typing import Callable

import numpy as np
from matplotlib import font_manager, rcParams
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch

from .model import (
    BACKEND_PRESENTATIONS,
    BackendMode,
    GuiViewModel,
    SignalSeries,
    SimulationView,
    compare_series,
    compute_hann_spectrum,
    nearest_index,
)


PREFERRED_CJK_FONTS = (
    "Noto Sans CJK SC",
    "Noto Sans CJK JP",
    "Source Han Sans SC",
    "Microsoft YaHei",
    "WenQuanYi Micro Hei",
    "SimHei",
    "Arial Unicode MS",
)


def configure_chinese_fonts(root: tk.Misc) -> str:
    """探测 Tk 与 Matplotlib 共同可用的中文字体并设置安全回退。"""

    tk_families = set(tkfont.families(root))
    mpl_families = {item.name for item in font_manager.fontManager.ttflist}
    selected = next(
        (name for name in PREFERRED_CJK_FONTS if name in tk_families and name in mpl_families),
        None,
    )
    if selected is None:
        selected = next((name for name in PREFERRED_CJK_FONTS if name in tk_families), "TkDefaultFont")
    mpl_choice = selected if selected in mpl_families else "DejaVu Sans"
    rcParams["font.sans-serif"] = [mpl_choice, *PREFERRED_CJK_FONTS, "DejaVu Sans"]
    rcParams["axes.unicode_minus"] = False
    for named_font in (
        "TkDefaultFont",
        "TkTextFont",
        "TkMenuFont",
        "TkHeadingFont",
        "TkCaptionFont",
        "TkSmallCaptionFont",
        "TkIconFont",
        "TkTooltipFont",
    ):
        try:
            tkfont.nametofont(named_font).configure(family=selected)
        except tk.TclError:
            continue
    return selected


class _FigureFrame(ttk.Frame):
    def __init__(self, master: tk.Misc, *, toolbar: bool = True) -> None:
        super().__init__(master)
        self.figure = Figure(figsize=(8.0, 5.2), dpi=100, constrained_layout=True)
        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        widget = self.canvas.get_tk_widget()
        widget.pack(fill=tk.BOTH, expand=True)
        if toolbar:
            self.toolbar = NavigationToolbar2Tk(self.canvas, self, pack_toolbar=False)
            self.toolbar.update()
            self.toolbar.pack(fill=tk.X)
        else:
            self.toolbar = None

    def draw(self) -> None:
        self.canvas.draw_idle()


class ProcessPlots(_FigureFrame):
    """装置截面、自由表面动画与主时间序列。"""

    def __init__(
        self,
        master: tk.Misc,
        *,
        on_seek: Callable[[float], None],
        on_toggle_play: Callable[[], None],
    ) -> None:
        super().__init__(master)
        controls = ttk.Frame(self)
        controls.pack(fill=tk.X, padx=8, pady=(0, 6))
        self.play_button = ttk.Button(controls, text="▶ 播放", command=on_toggle_play)
        self.play_button.pack(side=tk.LEFT)
        self.time_var = tk.DoubleVar(value=0.0)
        self.scale = ttk.Scale(
            controls,
            from_=0.0,
            to=1.0,
            variable=self.time_var,
            command=lambda value: on_seek(float(value)),
        )
        self.scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10)
        self.time_label = ttk.Label(controls, text="t = 0.000 s", width=18, anchor=tk.E)
        self.time_label.pack(side=tk.RIGHT)
        self._updating_scale = False

    def set_playing(self, playing: bool) -> None:
        self.play_button.configure(text="⏸ 暂停动画" if playing else "▶ 播放动画")

    def set_timeline(self, maximum: float, value: float) -> None:
        self._updating_scale = True
        try:
            self.scale.configure(to=max(float(maximum), 1e-9))
            self.time_var.set(float(value))
            self.time_label.configure(text=f"t = {float(value):.3f} s")
        finally:
            self._updating_scale = False

    @property
    def updating_scale(self) -> bool:
        return self._updating_scale

    def render(self, model: GuiViewModel, mode: BackendMode, at_time: float = 0.0) -> None:
        self.figure.clear()
        grid = self.figure.add_gridspec(
            2, 2, width_ratios=(1.0, 1.08), height_ratios=(1.0, 0.82)
        )
        apparatus = self.figure.add_subplot(grid[:, 0])
        series_axis = self.figure.add_subplot(grid[0, 1])
        spectrum_axis = self.figure.add_subplot(grid[1, 1])
        result = model.results.get(mode)
        config = model.result_configs.get(mode)
        if config is None:
            try:
                config = model.runtime_config(mode)
            except Exception:
                config = None
        if config is not None:
            self._draw_apparatus(apparatus, result, config, at_time)
        else:
            apparatus.text(0.5, 0.5, "参数尚未通过校验", ha="center", va="center")
            apparatus.set_axis_off()
        selected_signal = self._draw_series(series_axis, model, result, at_time)
        self._draw_spectrum(spectrum_axis, result, selected_signal)
        self.draw()

    @staticmethod
    def _draw_apparatus(axis, result: SimulationView | None, config, at_time: float) -> None:
        radius = config.container.radius_m
        depth = config.container.liquid_depth_m
        profile_r = np.linspace(0.0, radius, 96)
        profile_z = np.full_like(profile_r, depth)
        used_result_profile = False
        if result and result.surface_frames:
            index = nearest_index([item.time_s for item in result.surface_frames], at_time)
            frame = result.surface_frames[index]
            profile_r = np.asarray(frame.radius_m, dtype=float)
            profile_z = np.asarray(frame.height_m, dtype=float)
            used_result_profile = True

        full_r = np.concatenate((-profile_r[:0:-1], profile_r))
        full_z = np.concatenate((profile_z[:0:-1], profile_z))
        x_mm = full_r * 1000.0
        z_mm = full_z * 1000.0
        bottom = 0.0
        top_mm = max(depth * 1.16, float(np.max(full_z)) * 1.08) * 1000.0
        axis.fill_between(x_mm, bottom, z_mm, color="#77bce8", alpha=0.52, label="液体")
        axis.fill_between(x_mm, z_mm, top_mm, color="#f3f8fb", alpha=0.9, label="气体")
        axis.plot(x_mm, z_mm, color="#075985", linewidth=2.2, label="自由表面")
        vessel_r_mm = radius * 1000.0
        axis.plot([-vessel_r_mm, -vessel_r_mm], [0, top_mm], color="#334155", lw=2.2)
        axis.plot([vessel_r_mm, vessel_r_mm], [0, top_mm], color="#334155", lw=2.2)
        axis.plot([-vessel_r_mm, vessel_r_mm], [0, 0], color="#334155", lw=2.2)

        half_length = config.stir_bar.half_length_m * 1000.0
        diameter = 2.0 * config.stir_bar.radius_m * 1000.0
        clearance = config.stir_bar.bottom_clearance_m * 1000.0
        magnet = FancyBboxPatch(
            (-half_length, clearance),
            2.0 * half_length,
            diameter,
            boxstyle=f"round,pad=0,rounding_size={diameter / 2.0:g}",
            facecolor="#ef4444",
            edgecolor="#7f1d1d",
            linewidth=1.5,
            label="底部磁子",
        )
        axis.add_patch(magnet)
        axis.axhline(depth * 1000.0, color="#64748b", ls=":", lw=1.0, label="静止液面")
        axis.set_xlim(-vessel_r_mm * 1.12, vessel_r_mm * 1.12)
        axis.set_ylim(-top_mm * 0.03, top_mm)
        axis.set_aspect("equal", adjustable="box")
        axis.set_xlabel("径向位置 r / mm")
        axis.set_ylabel("轴向高度 z / mm")
        suffix = "求解器自由表面" if used_result_profile else "初始装置截面"
        axis.set_title(f"装置截面｜{suffix}\nt = {at_time:.3f} s")
        axis.grid(alpha=0.18)
        axis.legend(loc="upper right", fontsize=8)

    @staticmethod
    def _draw_series(
        axis, model: GuiViewModel, result: SimulationView | None, at_time: float
    ) -> SignalSeries | None:
        if result is None or not result.signals:
            axis.text(
                0.5,
                0.5,
                "运行后显示时间序列\n优先：漏斗深度 / 最小间隙 / 实际棒转速",
                ha="center",
                va="center",
                transform=axis.transAxes,
                color="#475569",
            )
            axis.set_xlabel("时间 t / s")
            axis.set_title("时间序列")
            axis.grid(alpha=0.2)
            return None
        priorities = (
            "surface_depth_m",
            "min_gap_to_bar_m",
            "actual_bar_rpm",
            "surface_min_z_m",
        )
        key = next((item for item in priorities if item in result.signals), next(iter(result.signals)))
        signal = result.signals[key]
        scale, display_unit = _display_scale(signal.unit)
        times = np.asarray(result.time_s[: len(signal.values)], dtype=float)
        values = np.asarray(signal.values[: len(times)], dtype=float) * scale
        axis.plot(times, values, color="#0369a1", lw=2.0, label=signal.label)
        if len(times):
            index = nearest_index(times.tolist(), at_time)
            axis.scatter([times[index]], [values[index]], color="#dc2626", s=42, zorder=4)

        experiment = model.experiment
        if experiment is not None and key in experiment.signals:
            exp_signal = experiment.signals[key]
            if exp_signal.unit == signal.unit:
                role = (
                    "密封验证"
                    if experiment.role == "sealed_validation"
                    else ("标定" if experiment.role == "calibration" else "快速导入/未分配角色")
                )
                axis.scatter(
                    experiment.time_s,
                    np.asarray(exp_signal.values) * scale,
                    s=16,
                    facecolors="none",
                    edgecolors="#ea580c",
                    label=f"实验（{role}）",
                )
        axis.axvline(at_time, color="#64748b", ls="--", lw=0.9)
        axis.set_xlabel("时间 t / s")
        axis.set_ylabel(f"{signal.label} / {display_unit or '1'}")
        axis.set_title(f"{signal.label}时间序列")
        axis.grid(alpha=0.25)
        axis.legend(loc="best", fontsize=8)
        return signal

    @staticmethod
    def _draw_spectrum(
        axis, result: SimulationView | None, signal: SignalSeries | None
    ) -> None:
        axis.set_xlabel("频率 f / Hz")
        axis.set_title("标量频谱｜去均值 + Hann 窗")
        axis.grid(alpha=0.22)
        if result is None or signal is None:
            axis.text(
                0.5,
                0.5,
                "运行后对当前标量显示单边幅值谱\n"
                "仅接受有限、样本足够、近等间隔采样",
                ha="center",
                va="center",
                transform=axis.transAxes,
                color="#475569",
                fontsize=8,
            )
            return
        spectrum = compute_hann_spectrum(result.time_s, signal)
        if not spectrum.available:
            axis.text(
                0.5,
                0.5,
                f"频谱不可用\n{spectrum.reason}\n不自动插值",
                ha="center",
                va="center",
                transform=axis.transAxes,
                color="#9a3412",
                fontsize=8,
                wrap=True,
            )
            axis.set_ylabel(f"幅值 / {signal.unit or '1'}")
            return

        frequencies = np.asarray(spectrum.frequencies_hz, dtype=float)
        amplitudes = np.asarray(spectrum.amplitudes, dtype=float)
        axis.plot(frequencies, amplitudes, color="#7c3aed", lw=1.5)
        axis.fill_between(frequencies, 0.0, amplitudes, color="#c4b5fd", alpha=0.35)
        axis.set_ylabel(f"单边幅值 / {spectrum.amplitude_unit or '1'}")
        axis.set_title(f"{signal.label}频谱｜探索性")
        axis.text(
            0.01,
            0.97,
            f"Δf={spectrum.frequency_resolution_hz:.3g} Hz｜"
            "短记录谱峰≠稳态/稳定性证据",
            transform=axis.transAxes,
            va="top",
            fontsize=7,
            color="#854d0e",
            bbox={"facecolor": "#fef9c3", "edgecolor": "none", "alpha": 0.8},
        )

        rpm_signal = result.signals.get("actual_bar_rpm")
        if rpm_signal is None or len(rpm_signal.values) != len(result.time_s):
            return
        rpm = np.asarray(rpm_signal.values, dtype=float)
        if not np.all(np.isfinite(rpm)) or rpm.size < 4:
            return
        settled = rpm[max(0, rpm.size * 3 // 4) :]
        mean_rpm = float(np.mean(settled))
        variation = float(np.ptp(settled))
        if abs(mean_rpm) <= 1.0e-12 or variation > max(1.0, 0.01 * abs(mean_rpm)):
            axis.text(
                0.99,
                0.97,
                "实际棒转速非近稳态：2Ω 不是单一频率",
                transform=axis.transAxes,
                ha="right",
                va="top",
                fontsize=7,
                color="#9a3412",
            )
            return
        m2_hz = 2.0 * abs(mean_rpm) / 60.0
        nyquist_hz = float(frequencies[-1]) if frequencies.size else 0.0
        if m2_hz <= nyquist_hz:
            axis.axvline(
                m2_hz,
                color="#dc2626",
                ls="--",
                lw=1.0,
                label=f"m=2 参照 2Ω｜{m2_hz:.3g} Hz",
            )
            axis.legend(loc="best", fontsize=7)
        else:
            axis.text(
                0.99,
                0.97,
                f"2Ω 参照 {m2_hz:.3g} Hz 超过 Nyquist {nyquist_hz:.3g} Hz",
                transform=axis.transAxes,
                ha="right",
                va="top",
                fontsize=7,
                color="#9a3412",
            )


class FieldPlots(_FigureFrame):
    """H1 液相相场与速度场。"""

    def __init__(self, master: tk.Misc) -> None:
        banner = ttk.Label(
            master,
            text=(
                "H1 = MECHANISM / PRE-CONTACT ONLY｜相场与速度场是轴对称机制预览；"
                "不是完整 VOF，不支持把首次空气—磁子几何接触后的结果当作定量预测。"
            ),
            style="Warning.TLabel",
            anchor=tk.W,
            padding=(10, 7),
        )
        banner.pack(fill=tk.X)
        super().__init__(master)

    def render(self, model: GuiViewModel, at_time: float = 0.0) -> None:
        self.figure.clear()
        phase_axis = self.figure.add_subplot(1, 2, 1)
        speed_axis = self.figure.add_subplot(1, 2, 2)
        result = model.results.get(BackendMode.HIGH_FIDELITY)
        if result is None or not result.field_frames:
            for axis, title in (
                (phase_axis, "H1 液相体积分数"),
                (speed_axis, "H1 速度场"),
            ):
                axis.text(
                    0.5,
                    0.5,
                    "运行 H1 后显示\n（建议先应用快速预览预设）",
                    ha="center",
                    va="center",
                    transform=axis.transAxes,
                    color="#475569",
                )
                axis.set_title(title)
                axis.set_xlabel("r / mm")
                axis.set_ylabel("z / mm")
            self.draw()
            return
        index = nearest_index([item.time_s for item in result.field_frames], at_time)
        frame = result.field_frames[index]
        extent = frame.extent_m or (0.0, 1.0, 0.0, 1.0)
        extent_mm = tuple(item * 1000.0 for item in extent)
        if frame.phase is not None:
            image = phase_axis.imshow(
                np.asarray(frame.phase, dtype=float),
                origin="lower",
                extent=extent_mm,
                aspect="auto",
                cmap="coolwarm",
                vmin=0.0,
                vmax=1.0,
            )
            self.figure.colorbar(image, ax=phase_axis, label="液相体积分数 / 1")
        else:
            phase_axis.text(0.5, 0.5, "该结果未提供相场", ha="center", va="center")
        phase_axis.set_title(f"H1 相场预览｜t = {frame.time_s:.3f} s")
        phase_axis.set_xlabel("径向位置 r / mm")
        phase_axis.set_ylabel("轴向高度 z / mm")

        if frame.speed_m_s is not None:
            speed = np.asarray(frame.speed_m_s, dtype=float)
            image = speed_axis.imshow(
                speed,
                origin="lower",
                extent=extent_mm,
                aspect="auto",
                cmap="viridis",
            )
            self.figure.colorbar(image, ax=speed_axis, label="速度大小 / m/s")
            if frame.radial_velocity_m_s is not None and frame.vertical_velocity_m_s is not None:
                radial = np.asarray(frame.radial_velocity_m_s, dtype=float)
                vertical = np.asarray(frame.vertical_velocity_m_s, dtype=float)
                row_step = max(1, radial.shape[0] // 16)
                column_step = max(1, radial.shape[1] // 16)
                x = np.linspace(extent_mm[0], extent_mm[1], radial.shape[1])
                y = np.linspace(extent_mm[2], extent_mm[3], radial.shape[0])
                xx, yy = np.meshgrid(x[::column_step], y[::row_step])
                sampled_r = radial[::row_step, ::column_step]
                sampled_z = vertical[::row_step, ::column_step]
                if np.max(np.hypot(sampled_r, sampled_z)) > 1e-14:
                    speed_axis.quiver(
                        xx,
                        yy,
                        sampled_r,
                        sampled_z,
                        color="white",
                        alpha=0.78,
                        scale=None,
                        width=0.004,
                    )
        else:
            speed_axis.text(0.5, 0.5, "该结果未提供速度场", ha="center", va="center")
        speed_axis.set_title("速度大小与 r-z 矢量")
        speed_axis.set_xlabel("径向位置 r / mm")
        speed_axis.set_ylabel("轴向高度 z / mm")
        self.draw()


class ComparisonPlots(_FigureFrame):
    """双后端/实验叠图及一维扫描结果。"""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self.metrics_text = "尚未形成双后端共同观测量。"

    def render(self, model: GuiViewModel) -> str:
        self.figure.clear()
        comparison_axis = self.figure.add_subplot(1, 2, 1)
        scan_axis = self.figure.add_subplot(1, 2, 2)
        self.metrics_text = self._draw_comparison(comparison_axis, model)
        self._draw_scan(scan_axis, model)
        self.draw()
        return self.metrics_text

    @staticmethod
    def _draw_comparison(axis, model: GuiViewModel) -> str:
        forward = model.results.get(BackendMode.FORWARD)
        high_fidelity = model.results.get(BackendMode.HIGH_FIDELITY)
        if forward is None and high_fidelity is None:
            axis.text(0.5, 0.5, "分别运行两个后端后自动比较", ha="center", va="center")
            axis.set_title("双后端比较")
            axis.set_xlabel("时间 t / s")
            return "尚无后端结果。"
        candidates = [item for item in (forward, high_fidelity) if item is not None]
        common = set(candidates[0].signals)
        for item in candidates[1:]:
            common &= set(item.signals)
        priority = (
            "surface_depth_m",
            "min_gap_to_bar_m",
            "surface_min_z_m",
            "actual_bar_rpm",
        )
        key = next((item for item in priority if item in common), None)
        if key is None:
            key = next(iter(common), None)
        if key is None:
            axis.text(0.5, 0.5, "结果之间没有共同标量观测量", ha="center", va="center")
            axis.set_title("双后端比较")
            return "没有共同标量观测量，未计算误差。"

        base = next(item for item in candidates if key in item.signals)
        base_signal = base.signals[key]
        scale, display_unit = _display_scale(base_signal.unit)
        styles = {
            BackendMode.FORWARD: ("#0369a1", "前向理论｜DERIVED"),
            BackendMode.HIGH_FIDELITY: ("#7c3aed", "H1｜MECHANISM / PRE-CONTACT ONLY"),
        }
        for result in candidates:
            signal = result.signals.get(key)
            if signal is None or signal.unit != base_signal.unit:
                continue
            color, label = styles[result.backend]
            axis.plot(result.time_s, np.asarray(signal.values) * scale, color=color, lw=2.0, label=label)
        experiment = model.experiment
        if experiment is not None and key in experiment.signals:
            signal = experiment.signals[key]
            if signal.unit == base_signal.unit:
                axis.scatter(
                    experiment.time_s,
                    np.asarray(signal.values) * scale,
                    s=18,
                    facecolors="none",
                    edgecolors="#ea580c",
                    label=f"实验｜{experiment.role}",
                )
        axis.set_xlabel("时间 t / s")
        axis.set_ylabel(f"{base_signal.label} / {display_unit or '1'}")
        axis.set_title("同输入条件下的共同观测量")
        axis.grid(alpha=0.25)
        axis.legend(loc="best", fontsize=8)

        if forward is not None and high_fidelity is not None:
            try:
                metrics = compare_series(forward, high_fidelity, key)
            except ValueError as exc:
                return f"双后端误差未计算：{exc}"
            return (
                f"共同观测量：{base_signal.label}；对齐样本 {metrics.samples}；"
                f"RMSE={metrics.rmse:.4g} {metrics.unit}，MAE={metrics.mae:.4g} {metrics.unit}，"
                f"最大绝对误差={metrics.max_abs:.4g} {metrics.unit}，偏差(H1−F)={metrics.bias:.4g} {metrics.unit}。"
            )
        return "当前只有一个后端结果；未计算双后端误差。"

    @staticmethod
    def _draw_scan(axis, model: GuiViewModel) -> None:
        scan = model.scan_result
        if scan is None or not scan.points:
            axis.text(0.5, 0.5, "从左侧“一维扫描”入口运行后显示", ha="center", va="center")
            axis.set_title("一维参数扫描")
            return
        common = set(scan.points[0].observables)
        for point in scan.points[1:]:
            common &= set(point.observables)
        priority = ("surface_depth_m", "min_gap_to_bar_m", "critical_actual_rpm")
        key = next((item for item in priority if item in common), next(iter(common), None))
        if key is None:
            axis.text(0.5, 0.5, "扫描结果没有共同有限输出", ha="center", va="center")
            axis.set_title("一维参数扫描")
            return
        label, unit = _signal_label_unit(key)
        scale, display_unit = _display_scale(unit)
        x = [item.input_value for item in scan.points if key in item.observables]
        y = [item.observables[key] * scale for item in scan.points if key in item.observables]
        axis.plot(x, y, marker="o", color="#0f766e", lw=1.8)
        axis.set_xlabel(
            f"{scan.parameter_label} / {scan.parameter_unit}" if scan.parameter_unit else scan.parameter_label
        )
        axis.set_ylabel(f"末时刻 {label} / {display_unit or '1'}")
        axis.set_title(f"{BACKEND_PRESENTATIONS[scan.backend].display_name}｜一维扫描")
        axis.grid(alpha=0.25)


def _signal_label_unit(key: str) -> tuple[str, str]:
    # 避免绘图模块依赖 ViewModel 私有函数；这里只列扫描图优先输出。
    table = {
        "surface_depth_m": ("漏斗/气芯深度", "m"),
        "min_gap_to_bar_m": ("自由表面—磁子最小间隙", "m"),
        "critical_actual_rpm": ("临界实际磁子转速", "r/min"),
    }
    return table.get(key, (key.replace("_", " "), ""))


def _display_scale(unit: str) -> tuple[float, str]:
    if unit == "m":
        return 1000.0, "mm"
    if unit == "m³":
        return 1e6, "cm³"
    return 1.0, unit


__all__ = [
    "ComparisonPlots",
    "FieldPlots",
    "ProcessPlots",
    "configure_chinese_fonts",
]
