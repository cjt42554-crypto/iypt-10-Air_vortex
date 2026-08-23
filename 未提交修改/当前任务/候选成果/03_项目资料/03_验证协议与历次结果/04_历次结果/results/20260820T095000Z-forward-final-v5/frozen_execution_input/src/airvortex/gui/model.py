"""无显示依赖的 GUI 状态、校验和数据转换。

本模块刻意不导入 tkinter、matplotlib 或求解器。这样参数规则、运行状态机、
实验 CSV 和结果转换都可在 CI/服务器上直接测试。GUI 不在此实现任何物理
控制方程；运行配置只做单位换算后交给 runtime/backend。
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import asdict, dataclass, field, fields as dataclass_fields, is_dataclass
from datetime import datetime
from enum import Enum
import csv
import json
import math
from pathlib import Path
import re
import threading
from typing import Any, Mapping, Sequence

from airvortex.core import (
    PROBLEM_ID,
    BackendMode,
    ContainerConfig,
    DriveConfig,
    FluidConfig,
    ForwardModelConfig,
    HighFidelityConfig,
    RunConfig,
    SimulationConfig,
    StirBarConfig,
)
from airvortex.io import load_config_json, save_config_json


def coerce_backend_mode(value: object) -> BackendMode:
    if isinstance(value, BackendMode):
        return value
    normalized = str(value).strip().lower().replace("-", "_")
    aliases = {
        "forward": BackendMode.FORWARD,
        "theory": BackendMode.FORWARD,
        "forward_theory": BackendMode.FORWARD,
        "前向理论": BackendMode.FORWARD,
        "high_fidelity": BackendMode.HIGH_FIDELITY,
        "hf": BackendMode.HIGH_FIDELITY,
        "hf_preview": BackendMode.HIGH_FIDELITY,
        "h1": BackendMode.HIGH_FIDELITY,
        "h1高保真预览": BackendMode.HIGH_FIDELITY,
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise ValueError(f"未知后端模式：{value!r}") from exc


class RunState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    COMPLETED = "completed"
    DOMAIN_LIMIT = "domain_limit"
    CANCELLED = "cancelled"
    FAILED = "failed"


DOMAIN_LIMIT_WARNING = "在模型域边界提前终止（非完整时长/非稳定气道）"
DEMONSTRATION_WARNING = "演示数据，不得用于校准或验证"
FORWARD_POST_CONTACT_WARNING = "首次几何接触后的点仅为数学外推，不代表真实气道"


_MISSING = object()


@dataclass(frozen=True)
class BackendPresentation:
    mode: BackendMode
    display_name: str
    badge: str
    capability: str
    evidence: str
    warning: str


BACKEND_PRESENTATIONS: dict[BackendMode, BackendPresentation] = {
    BackendMode.FORWARD: BackendPresentation(
        mode=BackendMode.FORWARD,
        display_name="前向理论",
        badge="DERIVED / CALIBRATION-REQUIRED",
        capability="能力：快速瞬态、阈值趋势、一维扫描与实验对照；Burgers 衰减指数固定为 1",
        evidence="证据等级：DERIVED（理论导出）；CALIBRATION-REQUIRED（须实验标定）",
        warning="适用于模型假设范围内的预测，不把拟合一致等同于机制证实。",
    ),
    BackendMode.HIGH_FIDELITY: BackendPresentation(
        mode=BackendMode.HIGH_FIDELITY,
        display_name="H1 高保真预览",
        badge="MECHANISM / PRE-CONTACT ONLY",
        capability=(
            "能力：轴对称相场界面与速度场的探索性瞬态预览；压力采用直接 LU，"
            "迭代上限字段不适用（兼容预留）；方位网格配置固定且 "
            "configured-but-unused"
        ),
        evidence="证据等级：MECHANISM（机制检验）；PRE-CONTACT ONLY（仅首次空气—磁子几何接触前）",
        warning=(
            "明确边界：这是 H1 相场预览，不是完整 VOF；只解释首次空气—磁子几何接触前，"
            "也不代表已收敛的三维 CFD。"
        ),
    ),
}


@dataclass(frozen=True)
class ParameterSpec:
    key: str
    group: str
    label: str
    unit: str
    default: float | int
    minimum: float
    maximum: float
    runtime_key: str
    scale_to_si: float = 1.0
    integer: bool = False
    editable: bool = True
    modes: tuple[BackendMode, ...] = (
        BackendMode.FORWARD,
        BackendMode.HIGH_FIDELITY,
    )
    help_text: str = ""

    def parse(self, raw: object) -> float | int:
        text = str(raw).strip()
        if not text:
            raise ValueError(f"{self.label}不能为空")
        try:
            value = float(text)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{self.label}必须是数值") from exc
        if not math.isfinite(value):
            raise ValueError(f"{self.label}必须是有限数")
        if self.integer:
            rounded = int(round(value))
            if abs(value - rounded) > 1e-9:
                raise ValueError(f"{self.label}必须是整数")
            value = rounded
        if value < self.minimum or value > self.maximum:
            raise ValueError(
                f"{self.label}须在 {self.minimum:g}–{self.maximum:g} {self.unit or ''} 范围内"
            )
        return value


PARAMETER_SPECS: tuple[ParameterSpec, ...] = (
    ParameterSpec(
        "vessel_radius_mm", "装置", "容器内半径", "mm", 45.0, 10.0, 250.0,
        "container.radius_m", 1e-3,
    ),
    ParameterSpec(
        "liquid_depth_mm", "装置", "静止液深", "mm", 60.0, 5.0, 450.0,
        "container.liquid_depth_m", 1e-3,
        help_text="H1 轴向域高取静止液深的 1.25 倍以容纳气层，顶边界闭合；不是容器总高。",
    ),
    ParameterSpec(
        "liquid_density_kg_m3", "流体", "液体密度", "kg·m⁻³", 998.2, 100.0, 3000.0,
        "fluid.liquid_density_kg_m3",
    ),
    ParameterSpec(
        "liquid_viscosity_mpas", "流体", "液体动力黏度", "mPa·s", 1.002, 0.05, 10000.0,
        "fluid.liquid_dynamic_viscosity_pa_s", 1e-3,
    ),
    ParameterSpec(
        "gas_density_kg_m3", "流体", "气体密度", "kg·m⁻³", 1.204, 0.01, 100.0,
        "fluid.gas_density_kg_m3",
    ),
    ParameterSpec(
        "gas_viscosity_upas", "流体", "气体动力黏度", "μPa·s", 18.25, 1.0, 1000.0,
        "fluid.gas_dynamic_viscosity_pa_s", 1e-6,
    ),
    ParameterSpec(
        "surface_tension_mnm", "流体", "表面张力", "mN·m⁻¹", 72.0, 1.0, 200.0,
        "fluid.surface_tension_n_m", 1e-3,
    ),
    ParameterSpec(
        "gravity_m_s2", "流体", "重力加速度", "m·s⁻²", 9.80665, 0.1, 30.0,
        "fluid.gravity_m_s2",
    ),
    ParameterSpec(
        "stirbar_length_mm", "磁子", "磁子全长", "mm", 30.0, 2.0, 120.0,
        "stir_bar.half_length_m", 0.5e-3,
    ),
    ParameterSpec(
        "stirbar_diameter_mm", "磁子", "磁子直径", "mm", 6.0, 1.0, 50.0,
        "stir_bar.radius_m", 0.5e-3,
    ),
    ParameterSpec(
        "stirbar_clearance_mm", "磁子", "离底间隙", "mm", 1.5, 0.0, 50.0,
        "stir_bar.bottom_clearance_m", 1e-3,
    ),
    ParameterSpec(
        "initial_rpm", "驱动", "初始实际棒转速", "r/min", 0.0, 0.0, 6000.0,
        "drive.initial_rpm",
    ),
    ParameterSpec(
        "rpm", "驱动", "目标实际棒转速", "r/min", 900.0, 0.0, 6000.0,
        "drive.target_rpm",
        help_text="必须输入实际磁子转速；面板设定值仅用于说明，不用于阈值计算。",
    ),
    ParameterSpec(
        "ramp_time_s", "驱动", "升速时间", "s", 2.0, 0.0, 120.0,
        "drive.ramp_duration_s",
    ),
    ParameterSpec(
        "rotation_direction", "驱动", "旋转方向（±1）", "", 1, -1, 1,
        "drive.rotation_direction", integer=True,
    ),
    ParameterSpec(
        "duration_s", "求解", "仿真时长", "s", 10.0, 0.001, 600.0,
        "run.duration_s",
    ),
    ParameterSpec(
        "output_dt_s", "求解", "输出时间步", "s", 0.02, 0.0001, 10.0,
        "run.output_interval_s",
    ),
    ParameterSpec(
        "circulation_coupling_m2", "前向专属", "环量耦合系数", "m²", 9.0e-5, 1e-9, 1.0,
        "forward.circulation_coupling_m2", modes=(BackendMode.FORWARD,),
    ),
    ParameterSpec(
        "effective_core_radius_mm", "前向专属", "有效涡核半径", "mm", 10.0, 0.1, 200.0,
        "forward.effective_core_radius_m", 1e-3, modes=(BackendMode.FORWARD,),
    ),
    ParameterSpec(
        "azimuthal_decay_power", "前向专属", "Burgers 衰减指数（固定）", "", 1.0, 1.0, 1.0,
        "forward.azimuthal_decay_power", modes=(BackendMode.FORWARD,), editable=False,
        help_text="闭合 Burgers 模型固定为 1，不是可拟合控件。",
    ),
    ParameterSpec(
        "spin_up_time_s", "前向专属", "旋起时间", "s", 1.2, 1e-4, 120.0,
        "forward.spin_up_time_s", modes=(BackendMode.FORWARD,),
    ),
    ParameterSpec(
        "surface_response_time_s", "前向专属", "表面响应时间", "s", 0.0, 0.0, 120.0,
        "forward.surface_response_time_s", modes=(BackendMode.FORWARD,),
    ),
    ParameterSpec(
        "air_core_depth_threshold_mm", "前向专属", "气芯判据深度", "mm", 1.0, 0.0, 200.0,
        "forward.air_core_depth_threshold_m", 1e-3, modes=(BackendMode.FORWARD,),
    ),
    ParameterSpec(
        "surface_radial_points", "前向专属", "表面径向点数", "", 256, 16, 4096,
        "forward.surface_radial_points", integer=True, modes=(BackendMode.FORWARD,),
    ),
    ParameterSpec(
        "forward_dt_s", "前向专属", "积分时间步", "s", 1e-3, 1e-7, 1.0,
        "forward.integration_time_step_s", modes=(BackendMode.FORWARD,),
    ),
    ParameterSpec(
        "forward_rtol", "前向专属", "相对容差", "", 1e-6, 1e-14, 0.1,
        "forward.relative_tolerance", modes=(BackendMode.FORWARD,),
    ),
    ParameterSpec(
        "forward_atol", "前向专属", "绝对容差", "", 1e-9, 1e-16, 0.1,
        "forward.absolute_tolerance", modes=(BackendMode.FORWARD,),
    ),
    ParameterSpec(
        "grid_nr", "H1 专属", "径向网格数", "", 48, 8, 512,
        "high_fidelity.radial_cells", integer=True, modes=(BackendMode.HIGH_FIDELITY,),
    ),
    ParameterSpec(
        "grid_ntheta", "H1 专属", "方位网格数（固定兼容字段；H1 不使用）", "", 64, 64, 64,
        "high_fidelity.azimuthal_cells", integer=True, modes=(BackendMode.HIGH_FIDELITY,),
        editable=False,
        help_text=(
            "H1 当前轴对称预览仅保留 core 兼容字段；"
            "configured-but-unused，改值不应被包装成物理调参。"
        ),
    ),
    ParameterSpec(
        "grid_nz", "H1 专属", "轴向网格数", "", 72, 8, 768,
        "high_fidelity.axial_cells", integer=True, modes=(BackendMode.HIGH_FIDELITY,),
    ),
    ParameterSpec(
        "hf_dt_s", "H1 专属", "求解时间步", "s", 2e-4, 1e-7, 0.1,
        "high_fidelity.time_step_s", modes=(BackendMode.HIGH_FIDELITY,),
    ),
    ParameterSpec(
        "cfl", "H1 专属", "CFL 上限", "", 0.40, 0.001, 1.0,
        "high_fidelity.cfl_limit", modes=(BackendMode.HIGH_FIDELITY,),
    ),
    ParameterSpec(
        "interface_thickness_cells", "H1 专属", "界面厚度", "网格", 2.5, 1.0, 8.0,
        "high_fidelity.interface_thickness_cells", modes=(BackendMode.HIGH_FIDELITY,),
    ),
    ParameterSpec(
        "pressure_rtol", "H1 专属", "压力相对容差", "", 1e-7, 1e-14, 0.1,
        "high_fidelity.pressure_relative_tolerance", modes=(BackendMode.HIGH_FIDELITY,),
    ),
    ParameterSpec(
        "pressure_max_iterations", "H1 专属", "迭代上限（直接 LU 不适用）", "", 400, 400, 400,
        "high_fidelity.pressure_max_iterations", integer=True,
        modes=(BackendMode.HIGH_FIDELITY,), editable=False,
        help_text="core 兼容预留字段；H1 当前直接 LU 路径不消费它。",
    ),
)

PARAMETER_BY_KEY = {spec.key: spec for spec in PARAMETER_SPECS}


@dataclass(frozen=True)
class ValidationIssue:
    key: str
    message: str


@dataclass(frozen=True)
class LogEntry:
    timestamp: str
    level: str
    message: str


@dataclass(frozen=True)
class SignalSeries:
    key: str
    label: str
    unit: str
    values: tuple[float, ...]
    unit_origin: str = "explicit"


@dataclass(frozen=True)
class FieldFrame:
    time_s: float
    phase: tuple[tuple[float, ...], ...] | None = None
    speed_m_s: tuple[tuple[float, ...], ...] | None = None
    radial_velocity_m_s: tuple[tuple[float, ...], ...] | None = None
    vertical_velocity_m_s: tuple[tuple[float, ...], ...] | None = None
    extent_m: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class SurfaceFrame:
    time_s: float
    radius_m: tuple[float, ...]
    height_m: tuple[float, ...]


@dataclass(frozen=True)
class SimulationView:
    backend: BackendMode
    time_s: tuple[float, ...]
    signals: dict[str, SignalSeries]
    surface_frames: tuple[SurfaceFrame, ...] = ()
    field_frames: tuple[FieldFrame, ...] = ()
    diagnostics: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    raw: Any = field(default=None, repr=False, compare=False)

    def signal(self, key: str) -> SignalSeries | None:
        return self.signals.get(key)

    @property
    def frame_count(self) -> int:
        return max(len(self.time_s), len(self.surface_frames), len(self.field_frames), 1)

    @property
    def domain_limited(self) -> bool:
        return DOMAIN_LIMIT_WARNING in self.warnings


@dataclass(frozen=True)
class SpectrumView:
    """等间隔时间序列的 Hann 去均值单边幅值谱。"""

    signal_key: str
    signal_label: str
    amplitude_unit: str
    available: bool
    reason: str
    sample_count: int
    duration_s: float
    frequency_resolution_hz: float | None
    frequencies_hz: tuple[float, ...] = ()
    amplitudes: tuple[float, ...] = ()
    warning: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExperimentData:
    path: Path
    time_s: tuple[float, ...]
    signals: dict[str, SignalSeries]
    source_headers: tuple[str, ...]
    role: str = "unassigned_quick_import"
    metadata: dict[str, Any] = field(default_factory=dict)
    raw: Any = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class ComparisonMetrics:
    signal_key: str
    samples: int
    rmse: float
    mae: float
    max_abs: float
    bias: float
    unit: str


@dataclass(frozen=True)
class ScanPointView:
    input_value: float
    observables: dict[str, float]
    status: str
    termination_annotation: str = ""


@dataclass(frozen=True)
class ScanView:
    backend: BackendMode
    parameter_key: str
    parameter_label: str
    parameter_unit: str
    runtime_path: str
    points: tuple[ScanPointView, ...]
    plan_hash: str
    planned_count: int
    diagnostics: tuple[str, ...] = ()
    raw_results: tuple[Any, ...] = field(default=(), repr=False, compare=False)


@dataclass(frozen=True)
class ButtonPolicy:
    start: bool
    pause: bool
    resume: bool
    stop: bool
    physical_controls: bool
    file_controls: bool


_ALLOWED_TRANSITIONS: dict[RunState, set[RunState]] = {
    RunState.IDLE: {RunState.RUNNING},
    RunState.RUNNING: {
        RunState.PAUSED,
        RunState.STOPPING,
        RunState.COMPLETED,
        RunState.DOMAIN_LIMIT,
        RunState.CANCELLED,
        RunState.FAILED,
    },
    RunState.PAUSED: {
        RunState.RUNNING,
        RunState.STOPPING,
        RunState.COMPLETED,
        RunState.DOMAIN_LIMIT,
        RunState.CANCELLED,
        RunState.FAILED,
    },
    RunState.STOPPING: {
        RunState.IDLE,
        RunState.COMPLETED,
        RunState.DOMAIN_LIMIT,
        RunState.CANCELLED,
        RunState.FAILED,
    },
    RunState.COMPLETED: {RunState.RUNNING, RunState.IDLE},
    RunState.DOMAIN_LIMIT: {RunState.RUNNING, RunState.IDLE},
    RunState.CANCELLED: {RunState.RUNNING, RunState.IDLE},
    RunState.FAILED: {RunState.RUNNING, RunState.IDLE},
}


class GuiViewModel:
    """GUI 的单一状态源，保持对 Tk 和具体后端实现的独立。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.backend_mode = BackendMode.FORWARD
        self.parameters: dict[str, str] = {
            spec.key: _format_number(spec.default) for spec in PARAMETER_SPECS
        }
        self.run_state = RunState.IDLE
        self.progress = 0.0
        self.progress_message = "就绪"
        self.logs: list[LogEntry] = []
        self.diagnostics: dict[str, Any] = {}
        self.results: dict[BackendMode, SimulationView] = {}
        self.result_configs: dict[BackendMode, SimulationConfig] = {}
        self.result_runtime_manifests: dict[BackendMode, dict[str, Any]] = {}
        self.scan_result: ScanView | None = None
        self.last_job_warnings: list[str] = []
        self.experiment: ExperimentData | None = None
        self.backend_availability: dict[BackendMode, tuple[bool, str]] = {
            BackendMode.FORWARD: (False, "等待运行时发现"),
            BackendMode.HIGH_FIDELITY: (False, "等待运行时发现"),
        }
        self.active_job_id: str | None = None

    @property
    def presentation(self) -> BackendPresentation:
        return BACKEND_PRESENTATIONS[self.backend_mode]

    def specs_for_mode(self, mode: BackendMode | str | None = None) -> tuple[ParameterSpec, ...]:
        selected = self.backend_mode if mode is None else coerce_backend_mode(mode)
        return tuple(
            spec for spec in PARAMETER_SPECS if selected in spec.modes and spec.editable
        )

    def set_backend(self, mode: BackendMode | str) -> None:
        with self._lock:
            if self.run_state in {RunState.RUNNING, RunState.PAUSED, RunState.STOPPING}:
                raise RuntimeError("运行期间不能切换计算后端")
            self.backend_mode = coerce_backend_mode(mode)

    def set_parameter(self, key: str, value: object) -> None:
        if key not in PARAMETER_BY_KEY:
            raise KeyError(f"未知参数：{key}")
        with self._lock:
            if not self.button_policy().physical_controls:
                raise RuntimeError("运行期间物理与求解参数已锁定")
            self.parameters[key] = str(value).strip()

    def parsed_parameters(
        self, mode: BackendMode | str | None = None
    ) -> tuple[dict[str, float | int], tuple[ValidationIssue, ...]]:
        selected = self.backend_mode if mode is None else coerce_backend_mode(mode)
        values: dict[str, float | int] = {}
        issues: list[ValidationIssue] = []
        for spec in PARAMETER_SPECS:
            if selected not in spec.modes:
                continue
            try:
                values[spec.key] = spec.parse(self.parameters.get(spec.key, ""))
            except ValueError as exc:
                issues.append(ValidationIssue(spec.key, str(exc)))

        if issues:
            return values, tuple(issues)

        def issue(key: str, message: str) -> None:
            issues.append(ValidationIssue(key, message))

        if values["stirbar_length_mm"] >= 2.0 * values["vessel_radius_mm"]:
            issue("stirbar_length_mm", "磁子长度必须小于容器内径")
        occupied_height = (
            values["stirbar_clearance_mm"] + values["stirbar_diameter_mm"]
        )
        if occupied_height >= values["liquid_depth_mm"]:
            issue("stirbar_clearance_mm", "磁子及离底间隙必须位于静止液面以下")
        if values["output_dt_s"] > values["duration_s"]:
            issue("output_dt_s", "输出时间步不能大于仿真时长")
        if values["ramp_time_s"] > values["duration_s"]:
            issue("ramp_time_s", "升速时间不能大于仿真时长")
        if values["liquid_density_kg_m3"] <= values["gas_density_kg_m3"]:
            issue("liquid_density_kg_m3", "液体密度必须大于气体密度")
        if values["rotation_direction"] not in (-1, 1):
            issue("rotation_direction", "旋转方向只能填 +1 或 -1")
        if selected is BackendMode.FORWARD:
            if values["effective_core_radius_mm"] >= values["vessel_radius_mm"]:
                issue("effective_core_radius_mm", "有效涡核半径必须小于容器半径")
            if values["air_core_depth_threshold_mm"] >= values["liquid_depth_mm"]:
                issue("air_core_depth_threshold_mm", "气芯判据深度必须小于初始液深")
            if values["forward_dt_s"] > values["output_dt_s"]:
                issue("forward_dt_s", "前向积分时间步不能大于输出时间步")
        if selected is BackendMode.HIGH_FIDELITY and values["hf_dt_s"] > values["output_dt_s"]:
            issue("hf_dt_s", "H1 求解时间步不能大于输出时间步")
        return values, tuple(issues)

    def runtime_config(self, mode: BackendMode | str | None = None) -> SimulationConfig:
        """构造 core 的唯一 ``SimulationConfig``，不维护第二套运行 schema。"""

        selected = self.backend_mode if mode is None else coerce_backend_mode(mode)
        values, issues = self.parsed_parameters(selected)
        if issues:
            raise ValueError("；".join(item.message for item in issues))
        snapshot = SimulationConfig(backend=selected).to_dict()
        for spec in PARAMETER_SPECS:
            # 隐藏的另一后端专属参数也属于 core 完整配置，按同一套严格规则读取。
            try:
                value = spec.parse(self.parameters.get(spec.key, ""))
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
            converted: float | int = (
                int(value) if spec.integer else float(value) * spec.scale_to_si
            )
            _set_dotted(snapshot, spec.runtime_key, converted)
        snapshot["backend"] = selected.value
        return SimulationConfig.from_dict(snapshot)

    def config_document(self) -> dict[str, Any]:
        return self.runtime_config().to_dict()

    def save_config(self, path: str | Path) -> Path:
        destination = Path(path).expanduser().resolve()
        return save_config_json(destination, self.runtime_config(), overwrite=True).resolve()

    def load_config(self, path: str | Path) -> tuple[str, ...]:
        source = Path(path).expanduser().resolve()
        loaded = load_config_json(source)
        document = loaded.to_dict()
        with self._lock:
            if not self.button_policy().file_controls:
                raise RuntimeError("运行期间不能载入配置")
            candidate: dict[str, str] = {}
            for spec in PARAMETER_SPECS:
                stored = _get_dotted(document, spec.runtime_key)
                display_value = float(stored) / spec.scale_to_si
                candidate[spec.key] = _format_number(
                    int(round(display_value)) if spec.integer else display_value
                )
            old_parameters = self.parameters
            old_mode = self.backend_mode
            self.parameters = candidate
            try:
                self.backend_mode = loaded.backend
                issues_by_key: dict[tuple[str, str], ValidationIssue] = {}
                for mode in (BackendMode.FORWARD, BackendMode.HIGH_FIDELITY):
                    _, mode_issues = self.parsed_parameters(mode)
                    for item in mode_issues:
                        issues_by_key[(item.key, item.message)] = item
                issues = tuple(issues_by_key.values())
                if issues:
                    raise ValueError("；".join(item.message for item in issues))
            except Exception:
                self.parameters = old_parameters
                self.backend_mode = old_mode
                raise
        return ()

    def apply_hf_quick_preset(self) -> str:
        """显式应用可完成的 H1 烟测预设，不在模式切换时静默改参。"""

        if not self.button_policy().physical_controls:
            raise RuntimeError("运行期间不能应用预设")
        preset = {
            "duration_s": "0.05",
            "output_dt_s": "0.01",
            "ramp_time_s": "0.02",
            "grid_nr": "16",
            "grid_nz": "24",
            "hf_dt_s": "0.0002",
        }
        self.parameters.update(preset)
        return self.hf_work_estimate()

    def hf_work_estimate(self) -> str:
        """给出透明的离散工作量，不虚构与机器相关的墙钟 ETA。"""

        try:
            steps = math.ceil(float(self.parameters["duration_s"]) / float(self.parameters["hf_dt_s"]))
            nr = int(float(self.parameters["grid_nr"]))
            nt = int(float(self.parameters["grid_ntheta"]))
            nz = int(float(self.parameters["grid_nz"]))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            return "H1 工作量：参数未通过校验"
        meridional = nr * nz
        declared = nr * nt * nz
        workload = steps * meridional
        tier = "快速预览" if workload <= 200_000 else ("中等" if workload <= 5_000_000 else "较重")
        return (
            f"H1 工作量：{steps:,} 步 × {nr}×{nz} 轴对称单元"
            f"（方位配置 {nt} 为 configured-but-unused，"
            f"不参与本次数组/工作量；三维声明量 {declared:,}）"
            f"≈ {workload:,} 单元步；{tier}。"
        )

    def set_run_state(self, new_state: RunState | str, *, force: bool = False) -> None:
        state = new_state if isinstance(new_state, RunState) else RunState(str(new_state))
        with self._lock:
            if state == self.run_state:
                return
            if not force and state not in _ALLOWED_TRANSITIONS[self.run_state]:
                raise RuntimeError(f"非法运行状态转换：{self.run_state.value} → {state.value}")
            self.run_state = state
            if state == RunState.RUNNING and self.progress >= 1.0:
                self.progress = 0.0
            if state == RunState.IDLE:
                self.active_job_id = None

    def button_policy(self) -> ButtonPolicy:
        state = self.run_state
        busy = state in {RunState.RUNNING, RunState.PAUSED, RunState.STOPPING}
        return ButtonPolicy(
            start=state
            in {
                RunState.IDLE,
                RunState.COMPLETED,
                RunState.DOMAIN_LIMIT,
                RunState.CANCELLED,
                RunState.FAILED,
            },
            pause=state == RunState.RUNNING,
            resume=state == RunState.PAUSED,
            stop=state in {RunState.RUNNING, RunState.PAUSED},
            physical_controls=not busy,
            file_controls=not busy,
        )

    def update_progress(self, value: object, message: str | None = None) -> None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = 0.0
        if number > 1.0 and number <= 100.0:
            number /= 100.0
        with self._lock:
            self.progress = min(1.0, max(0.0, number))
            if message:
                self.progress_message = str(message)

    def append_log(self, level: str, message: object) -> LogEntry:
        entry = LogEntry(
            timestamp=datetime.now().astimezone().strftime("%H:%M:%S"),
            level=str(level).upper(),
            message=str(message),
        )
        with self._lock:
            self.logs.append(entry)
            if len(self.logs) > 5000:
                del self.logs[:1000]
        return entry

    def set_backend_availability(
        self, mode: BackendMode | str, available: bool, detail: str = ""
    ) -> None:
        self.backend_availability[coerce_backend_mode(mode)] = (bool(available), str(detail))

    def begin_result_batch(self) -> None:
        """清除上一个任务的醒目警告，供单次/比较/扫描共用。"""

        with self._lock:
            self.last_job_warnings.clear()

    def _record_result_warnings(
        self, mode: BackendMode, warnings: Sequence[str], *, context: str = ""
    ) -> None:
        prefix = context or BACKEND_PRESENTATIONS[mode].display_name
        for warning in warnings:
            message = f"{prefix}：{warning}"
            if message not in self.last_job_warnings:
                self.last_job_warnings.append(message)

    def last_job_hit_domain_limit(self) -> bool:
        return any(DOMAIN_LIMIT_WARNING in item for item in self.last_job_warnings)

    def last_job_domain_limit_message(self) -> str | None:
        return next(
            (
                item
                for item in self.last_job_warnings
                if DOMAIN_LIMIT_WARNING in item
            ),
            None,
        )

    def accept_result(
        self,
        result: Any,
        mode: BackendMode | str | None = None,
        *,
        config: SimulationConfig | None = None,
        runtime_manifest: Mapping[str, Any] | None = None,
    ) -> SimulationView:
        selected = self.backend_mode if mode is None else coerce_backend_mode(mode)
        stored_config = self.runtime_config(selected) if config is None else config
        if not isinstance(stored_config, SimulationConfig) or stored_config.backend is not selected:
            raise ValueError("结果配置必须是同一 backend 的 SimulationConfig")
        normalized = normalize_simulation_result(result, selected)
        manifest: dict[str, Any] | None = None
        if runtime_manifest is not None:
            ready_manifest = _json_ready(runtime_manifest)
            if not isinstance(ready_manifest, dict):
                raise TypeError("runtime manifest 必须序列化为对象")
            manifest = ready_manifest
        with self._lock:
            self.results[selected] = normalized
            self.result_configs[selected] = stored_config
            if manifest is not None:
                self.result_runtime_manifests[selected] = manifest
            self.diagnostics = dict(normalized.diagnostics)
            self._record_result_warnings(selected, normalized.warnings)
            if normalized.domain_limited:
                configured_duration = float(stored_config.run.duration_s)
                simulated_duration = float(normalized.time_s[-1]) if normalized.time_s else 0.0
                self.progress = min(1.0, max(0.0, simulated_duration / configured_duration))
                self.progress_message = DOMAIN_LIMIT_WARNING
            else:
                self.progress = 1.0
                self.progress_message = (
                    f"计算完成；{normalized.warnings[0]}"
                    if normalized.warnings
                    else "计算完成"
                )
        return normalized

    def accept_scan_results(
        self,
        *,
        mode: BackendMode | str,
        parameter_key: str,
        input_values: Sequence[float],
        results: Sequence[Any],
        plan_hash: str,
        planned_count: int,
    ) -> ScanView:
        selected = coerce_backend_mode(mode)
        spec = PARAMETER_BY_KEY.get(parameter_key)
        if spec is None:
            raise KeyError(f"未知扫描参数：{parameter_key}")
        points: list[ScanPointView] = []
        diagnostics: list[str] = []
        for index, (input_value, raw_result) in enumerate(zip(input_values, results)):
            view = normalize_simulation_result(raw_result, selected)
            final_values: dict[str, float] = {}
            for key, signal in view.signals.items():
                finite = [item for item in signal.values if math.isfinite(item)]
                if finite:
                    final_values[key] = float(finite[-1])
            raw_status = _object_mapping(raw_result).get("status", "completed")
            status = getattr(raw_status, "value", str(raw_status))
            annotation = " | ".join(view.warnings)
            points.append(
                ScanPointView(float(input_value), final_values, status, annotation)
            )
            if not final_values:
                diagnostics.append(f"扫描点 {index + 1} 没有有限标量输出")
            if view.warnings:
                reason = view.diagnostics.get("termination_reason", "unknown")
                diagnostics.append(
                    f"扫描点 {index + 1}：{annotation}；termination_reason={reason}"
                )
                self._record_result_warnings(
                    selected, view.warnings, context=f"扫描点 {index + 1}"
                )
        scan = ScanView(
            backend=selected,
            parameter_key=parameter_key,
            parameter_label=spec.label,
            parameter_unit=spec.unit,
            runtime_path=spec.runtime_key,
            points=tuple(points),
            plan_hash=str(plan_hash),
            planned_count=int(planned_count),
            diagnostics=tuple(diagnostics),
            raw_results=tuple(results),
        )
        self.scan_result = scan
        self.diagnostics = {
            "scan.backend": selected.value,
            "scan.planned_count": int(planned_count),
            "scan.completed_count": len(points),
            "scan.diagnostics": list(diagnostics),
        }
        return scan

    def import_experiment_csv(self, path: str | Path) -> ExperimentData:
        data = read_experiment_csv(path)
        self.experiment = data
        return data

    def scan_values(
        self,
        parameter_key: str,
        start: object,
        stop: object,
        count: object,
        *,
        logarithmic: bool = False,
    ) -> tuple[float, ...]:
        spec = PARAMETER_BY_KEY.get(parameter_key)
        if spec is None:
            raise KeyError(f"未知扫描参数：{parameter_key}")
        start_value = spec.parse(start)
        stop_value = spec.parse(stop)
        try:
            steps = int(str(count).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError("扫描点数必须是整数") from exc
        if steps < 2 or steps > 500:
            raise ValueError("扫描点数须在 2–500 范围内")
        a, b = float(start_value), float(stop_value)
        if not b > a:
            raise ValueError("扫描终值必须大于起值")
        if logarithmic:
            if a <= 0.0:
                raise ValueError("对数扫描的起值必须大于零")
            left, right = math.log(a), math.log(b)
            values = [math.exp(left + (right - left) * i / (steps - 1)) for i in range(steps)]
        else:
            values = [a + (b - a) * i / (steps - 1) for i in range(steps)]
        if spec.integer:
            rounded = tuple(float(int(round(value))) for value in values)
            if len(set(rounded)) != len(rounded):
                raise ValueError("整数参数的扫描范围不足以产生互异点")
            return rounded
        return tuple(values)

    def export_result_bundle(
        self, path: str | Path, mode: BackendMode | str | None = None
    ) -> tuple[Path, Path]:
        """导出数值 CSV 及不可省略的证据/来源元数据旁车文件。"""

        selected = self.backend_mode if mode is None else coerce_backend_mode(mode)
        try:
            result = self.results[selected]
            result_config = self.result_configs[selected]
        except KeyError as exc:
            raise ValueError(
                f"{BACKEND_PRESENTATIONS[selected].display_name}尚无带完整配置来源的可导出结果"
            ) from exc
        destination = Path(path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        metadata_path = destination.with_suffix(destination.suffix + ".metadata.json")
        metadata_document = {
            "format": "airvortex-result-metadata/v1",
            "problem_id": PROBLEM_ID,
            "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "backend": selected.value,
            "backend_name": BACKEND_PRESENTATIONS[selected].display_name,
            "evidence_badge": BACKEND_PRESENTATIONS[selected].badge,
            "capability": BACKEND_PRESENTATIONS[selected].capability,
            "evidence": BACKEND_PRESENTATIONS[selected].evidence,
            "warning": BACKEND_PRESENTATIONS[selected].warning,
            "config": result_config.to_dict(),
            "signals": [
                {
                    "key": item.key,
                    "label": item.label,
                    "unit": item.unit,
                    "unit_origin": item.unit_origin,
                }
                for item in result.signals.values()
            ],
            "result_metadata": result.metadata,
            "diagnostics": result.diagnostics,
            "result_warnings": list(result.warnings),
            "experiment_overlay": (
                {
                    "path": str(self.experiment.path),
                    "role": self.experiment.role,
                    "rows": len(self.experiment.time_s),
                    "source_headers": list(self.experiment.source_headers),
                    "signals": [
                        {
                            "key": item.key,
                            "label": item.label,
                            "unit": item.unit,
                            "unit_origin": item.unit_origin,
                        }
                        for item in self.experiment.signals.values()
                    ],
                    "metadata": self.experiment.metadata,
                }
                if self.experiment is not None
                else None
            ),
            "runtime_manifest": self.result_runtime_manifests.get(selected),
            "runtime_manifest_status": (
                "captured" if selected in self.result_runtime_manifests else "not_supplied"
            ),
        }
        # 先完整序列化元数据；任何不受支持的对象都会显式失败，不生成“无来源”CSV。
        metadata_text = json.dumps(
            _json_ready(metadata_document), ensure_ascii=False, indent=2, allow_nan=False
        ) + "\n"
        metadata_path.write_text(metadata_text, encoding="utf-8")
        signals = list(result.signals.values())
        with destination.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["time_s", *[_csv_signal_header(item) for item in signals]])
            for index, time_value in enumerate(result.time_s):
                writer.writerow(
                    [
                        _format_export(time_value),
                        *[
                            _format_export(item.values[index]) if index < len(item.values) else ""
                            for item in signals
                        ],
                    ]
                )
        return destination, metadata_path

    def export_result_csv(self, path: str | Path, mode: BackendMode | str | None = None) -> Path:
        destination, _metadata_path = self.export_result_bundle(path, mode)
        return destination

    def export_scan_bundle(self, path: str | Path) -> tuple[Path, Path]:
        scan = self.scan_result
        if scan is None:
            raise ValueError("尚无可导出的一维扫描结果")
        destination = Path(path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        metadata_path = destination.with_suffix(destination.suffix + ".metadata.json")
        observable_keys = sorted(
            {key for point in scan.points for key in point.observables}
        )
        metadata = {
            "format": "airvortex-scan-metadata/v1",
            "problem_id": PROBLEM_ID,
            "backend": scan.backend.value,
            "backend_name": BACKEND_PRESENTATIONS[scan.backend].display_name,
            "evidence_badge": BACKEND_PRESENTATIONS[scan.backend].badge,
            "warning": BACKEND_PRESENTATIONS[scan.backend].warning,
            "parameter": {
                "gui_key": scan.parameter_key,
                "runtime_path": scan.runtime_path,
                "label": scan.parameter_label,
                "display_unit": scan.parameter_unit,
            },
            "base_config_hash": scan.plan_hash,
            "planned_count": scan.planned_count,
            "completed_count": len(scan.points),
            "diagnostics": list(scan.diagnostics),
            "observable_columns": observable_keys,
        }
        metadata_text = json.dumps(
            _json_ready(metadata), ensure_ascii=False, indent=2, allow_nan=False
        ) + "\n"
        metadata_path.write_text(metadata_text, encoding="utf-8")
        with destination.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    f"{scan.parameter_key}[{scan.parameter_unit}]"
                    if scan.parameter_unit
                    else scan.parameter_key,
                    "status",
                    "termination_annotation",
                    *observable_keys,
                ]
            )
            for point in scan.points:
                writer.writerow(
                    [
                        _format_export(point.input_value),
                        point.status,
                        point.termination_annotation,
                        *[
                            _format_export(point.observables[key])
                            if key in point.observables
                            else ""
                            for key in observable_keys
                        ],
                    ]
                )
        return destination, metadata_path


def normalize_simulation_result(result: Any, mode: BackendMode | str) -> SimulationView:
    """把 core ``SimulationResult`` 或兼容映射归一化为只读绘图数据。"""

    selected = coerce_backend_mode(mode)
    payload = _object_mapping(result)
    result_backend = payload.get("backend")
    if result_backend is not None and coerce_backend_mode(result_backend) is not selected:
        raise ValueError("求解结果的 backend 与所选模式不一致")
    time_raw = _first_present(payload, "time_s", "times_s", "time", "times", "t")
    time_s = _float_vector(time_raw)

    metadata = _mapping_copy(_first_present(payload, "metadata", "meta", "provenance"))
    declared_units = metadata.get("observable_units", metadata.get("units", {}))
    if not isinstance(declared_units, Mapping):
        declared_units = {}

    signals: dict[str, SignalSeries] = {}
    explicit_signals = payload.get("observables", payload.get("signals"))
    if isinstance(explicit_signals, Mapping):
        for raw_key, raw_value in explicit_signals.items():
            series = _normalize_signal(
                str(raw_key),
                raw_value,
                declared_unit=(
                    declared_units[raw_key] if raw_key in declared_units else _MISSING
                ),
            )
            if series:
                signals[series.key] = series

    signal_aliases = {
        "surface_depth_m": (
            "surface_depth_m", "funnel_depth_m", "surface_depth", "core_depth_m",
            "air_core_depth_m", "vortex_depth_m", "depth_m", "core_depth",
        ),
        "air_core_bottom_radius_m": (
            "air_core_bottom_radius_m", "core_radius_m", "air_core_radius_m", "vortex_radius_m",
        ),
        "surface_min_z_m": (
            "surface_min_z_m", "surface_min_z", "surface_min_height_m",
            "minimum_surface_height_m", "z_min_m",
        ),
        "actual_bar_rpm": (
            "actual_bar_rpm", "equivalent_bar_rpm", "stirbar_speed_rpm", "rpm", "speed_rpm",
        ),
        "volume_error_percent": ("volume_error_percent", "volume_error_pct"),
    }
    for canonical, aliases in signal_aliases.items():
        if canonical in signals:
            continue
        raw_value = _first_present(payload, *aliases)
        if raw_value is not None:
            vector = _float_vector(raw_value)
            if vector:
                label, unit = _signal_presentation(canonical)
                signals[canonical] = SignalSeries(
                    canonical, label, unit, vector, "inferred_from_canonical"
                )

    if not time_s:
        inferred_length = max((len(item.values) for item in signals.values()), default=0)
        if inferred_length:
            time_s = tuple(float(index) for index in range(inferred_length))
    if time_s:
        signals = {
            key: SignalSeries(
                item.key,
                item.label,
                item.unit,
                item.values[: len(time_s)],
                item.unit_origin,
            )
            for key, item in signals.items()
            if item.values
        }

    surface_frames = _normalize_core_surface_frames(payload, time_s)
    if not surface_frames:
        surface_frames = _normalize_surface_frames(payload, time_s)
    field_frames = _normalize_core_field_frames(payload, time_s)
    if not field_frames:
        field_frames = _normalize_field_frames(payload, time_s)
    diagnostics = _mapping_copy(metadata.get("diagnostics"))
    for key in (
        "fidelity_label",
        "evidence_level",
        "small_slope_validity",
        "small_slope_warning",
        "domain_status",
        "connection_status",
        "connected_channel_interpretation",
        "post_contact_interpretation",
        "bubble_entrainment_simulated",
        "topology_change_simulated",
        "wetting_and_contact_line_simulated",
        "boundary_conditions",
        "limitations",
        "capabilities",
    ):
        if key in metadata:
            diagnostics.setdefault(key, metadata[key])
    diagnostics.update(
        _mapping_copy(_first_present(payload, "diagnostics", "diagnostic", "metrics", "quality"))
    )
    status = payload.get("status")
    if status is not None:
        diagnostics.setdefault("run_status", getattr(status, "value", str(status)))
    events = payload.get("events")
    if isinstance(events, Sequence):
        diagnostics.setdefault("event_count", len(events))
    warnings: list[str] = []
    termination_reason = diagnostics.get("termination_reason")
    if termination_reason is not None and "domain_limit" in str(termination_reason).lower():
        diagnostics["domain_limit_early_termination"] = True
        diagnostics["domain_limit_warning"] = DOMAIN_LIMIT_WARNING
        warnings.append(DOMAIN_LIMIT_WARNING)
    domain_status = str(diagnostics.get("domain_status", "")).strip().upper()
    if selected is BackendMode.FORWARD and domain_status == "OUT_OF_MODEL_DOMAIN":
        diagnostics["post_contact_extrapolation_warning"] = (
            FORWARD_POST_CONTACT_WARNING
        )
        warnings.append(FORWARD_POST_CONTACT_WARNING)
    return SimulationView(
        backend=selected,
        time_s=time_s,
        signals=signals,
        surface_frames=surface_frames,
        field_frames=field_frames,
        diagnostics=diagnostics,
        metadata=metadata,
        warnings=tuple(warnings),
        raw=result,
    )


def read_experiment_csv(path: str | Path) -> ExperimentData:
    source = Path(path).expanduser().resolve()
    metadata_path = source.with_suffix(".meta.json")
    if metadata_path.exists():
        from airvortex.analysis import load_experiment

        dataset = load_experiment(source, metadata_path)
        specs_by_canonical = {
            spec.canonical: spec for spec in dataset.columns if spec.role == "observable"
        }
        formal_contract: dict[str, tuple[str, str]] = {
            "surface_depth": ("surface_depth_m", "length"),
            "surface_depth_m": ("surface_depth_m", "length"),
            "min_gap_to_bar": ("min_gap_to_bar_m", "length"),
            "min_gap_to_bar_m": ("min_gap_to_bar_m", "length"),
            "bar_speed": ("actual_angular_velocity_rad_s", "angular_speed"),
            "actual_angular_velocity_rad_s": (
                "actual_angular_velocity_rad_s",
                "angular_speed",
            ),
            "actual_bar_rpm": ("actual_angular_velocity_rad_s", "angular_speed"),
            "local_air_core_radius": ("local_air_core_radius_m", "length"),
            "local_air_core_radius_m": ("local_air_core_radius_m", "length"),
            "local_relative_speed": ("local_relative_speed_m_s", "speed"),
            "local_relative_speed_m_s": ("local_relative_speed_m_s", "speed"),
        }
        bar_speed_present = "bar_speed" in dataset.observables
        rotation_direction: int | None = None
        if bar_speed_present:
            raw_direction = dataset.metadata.get("rotation_direction")
            if type(raw_direction) is not int or raw_direction not in (-1, 1):
                raise ValueError(
                    "正式实验 bar_speed 是非负 |Ω_b|；metadata 必须显式"
                    "声明 rotation_direction=+1 或 -1"
                )
            rotation_direction = raw_direction

        signals: dict[str, SignalSeries] = {}
        adapter_records: dict[str, dict[str, object]] = {}
        for canonical, raw_values in dataset.observables.items():
            spec = specs_by_canonical[canonical]
            unknown_target = re.sub(
                r"[^a-z0-9]+", "_", canonical.strip().lower()
            ).strip("_")
            target, expected_dimension = formal_contract.get(
                canonical, (unknown_target or canonical, spec.dimension)
            )
            if spec.dimension != expected_dimension:
                raise ValueError(
                    f"正式实验量 {canonical!r} 维度为 {spec.dimension!r}，"
                    f"但 GUI 契约要求 {expected_dimension!r}；拒绝同名异维数据"
                )
            if target in signals:
                raise ValueError(f"多个正式实验列映射到同一 GUI 观测量 {target!r}")
            values = tuple(float(item) for item in raw_values)
            if canonical == "bar_speed":
                if any(item < 0.0 for item in values):
                    raise ValueError(
                        "正式实验 bar_speed 必须存储非负 |Ω_b|；"
                        "旋转符号只能由 metadata.rotation_direction 给出"
                    )
                assert rotation_direction is not None
                values = tuple(rotation_direction * item for item in values)
            si_unit = _normalize_unit_spelling(str(dataset.units[canonical]))
            label, _default_unit = _signal_presentation(target)
            signals[target] = SignalSeries(target, label, si_unit, values)
            adapter_records[canonical] = {
                "gui_key": target,
                "dimension": spec.dimension,
                "stored_unit": si_unit,
            }
            if canonical == "bar_speed":
                adapter_records[canonical].update(
                    {
                        "source_semantics": "nonnegative_angular_speed_magnitude",
                        "rotation_direction": rotation_direction,
                        "stored_semantics": "signed_angular_velocity",
                    }
                )

            if target == "actual_angular_velocity_rad_s":
                rpm_values = tuple(item * 60.0 / (2.0 * math.pi) for item in values)
                rpm_key = "actual_bar_rpm"
                if rpm_key in signals:
                    raise ValueError("正式实验数据重复定义实际磁子转速")
                signals[rpm_key] = SignalSeries(
                    rpm_key,
                    _signal_presentation(rpm_key)[0],
                    "r/min",
                    rpm_values,
                )
                adapter_records[canonical]["derived_gui_key"] = rpm_key
                adapter_records[canonical]["derived_unit"] = "r/min"
        headers = tuple(spec.source for spec in dataset.columns)
        metadata = dict(dataset.metadata)
        metadata["gui_formal_adapter"] = {
            "contract": "analysis-canonical-SI-to-gui/v1",
            "mappings": adapter_records,
            "angular_speed_display_transform": (
                "bar_speed magnitude * rotation_direction -> signed rad/s; "
                "signed rad/s to signed r/min by 60/(2*pi)"
            ),
        }
        if _is_demonstration_metadata(metadata):
            existing_warning = str(metadata.get("warning", "")).strip()
            metadata["warning"] = (
                f"{DEMONSTRATION_WARNING}；{existing_warning}"
                if existing_warning
                else DEMONSTRATION_WARNING
            )
            metadata["calibration_validation_eligible"] = False
        return ExperimentData(
            source,
            tuple(float(item) for item in dataset.time_s),
            signals,
            headers,
            role=dataset.role,
            metadata=metadata,
            raw=dataset,
        )
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(handle, dialect=dialect)
        if not reader.fieldnames:
            raise ValueError("实验 CSV 缺少表头")
        headers = tuple(str(item).strip() for item in reader.fieldnames)
        rows = list(reader)
    if not rows:
        raise ValueError("实验 CSV 没有数据行")

    time_header = next((header for header in headers if _column_kind(header)[0] == "time_s"), None)
    if time_header is None:
        raise ValueError("实验 CSV 必须含时间列，例如 time_s、t_s 或 时间(s)")

    columns: dict[str, list[float]] = {header: [] for header in headers}
    for row_number, row in enumerate(rows, start=2):
        parsed_row: dict[str, float] = {}
        for header in headers:
            raw = str(row.get(header, "")).strip()
            if raw == "":
                if header == time_header:
                    raise ValueError(f"实验 CSV 第 {row_number} 行时间为空")
                parsed_row[header] = math.nan
                continue
            try:
                value = float(raw)
            except ValueError as exc:
                raise ValueError(f"实验 CSV 第 {row_number} 行“{header}”不是数值") from exc
            if not math.isfinite(value):
                raise ValueError(f"实验 CSV 第 {row_number} 行“{header}”不是有限数")
            parsed_row[header] = value
        for header, value in parsed_row.items():
            columns[header].append(value)

    time_key, time_scale, _, _ = _column_kind(time_header)
    assert time_key == "time_s"
    time_s = tuple(value * time_scale for value in columns[time_header])
    if any(b <= a for a, b in zip(time_s, time_s[1:])):
        raise ValueError("实验 CSV 时间列必须严格递增")

    signals: dict[str, SignalSeries] = {}
    for header in headers:
        if header == time_header:
            continue
        key, scale, label, unit = _column_kind(header)
        if key in signals:
            raise ValueError(f"实验 CSV 中有多个列映射到“{key}”")
        values = tuple(value * scale if math.isfinite(value) else value for value in columns[header])
        signals[key] = SignalSeries(key, label, unit, values)
    if not signals:
        raise ValueError("实验 CSV 除时间外至少需要一个数值观测量")
    return ExperimentData(
        source,
        time_s,
        signals,
        headers,
        role="unassigned_quick_import",
        metadata={
            "warning": (
                "未找到同名 .meta.json；仅按表头单位快速导入用于叠图，"
                "不能作为密封验证或标定数据集。"
            )
        },
    )


def compute_hann_spectrum(
    time_s: Sequence[float],
    signal: SignalSeries,
    *,
    minimum_samples: int = 16,
    uniform_relative_tolerance: float = 1.0e-4,
) -> SpectrumView:
    """把 GUI ``SignalSeries`` 适配到 analysis 的严格公共频谱 API。"""

    sample_count = len(time_s)
    try:
        duration_s = (
            float(time_s[-1]) - float(time_s[0]) if sample_count >= 2 else 0.0
        )
    except (TypeError, ValueError, OverflowError):
        duration_s = 0.0

    def unavailable(reason: str) -> SpectrumView:
        return SpectrumView(
            signal_key=signal.key,
            signal_label=signal.label,
            amplitude_unit=signal.unit,
            available=False,
            reason=reason,
            sample_count=sample_count,
            duration_s=duration_s,
            frequency_resolution_hz=None,
        )

    if signal.unit_origin in {"inferred_from_canonical", "invalid_declared"}:
        return unavailable(
            f"单位来源为 {signal.unit_origin}，缺少显式物理单位；"
            "只可叠图，频谱门禁拒绝"
        )

    from airvortex.analysis import (
        DataValidationError,
        compute_hann_spectrum as analysis_compute_hann_spectrum,
    )

    try:
        estimate = analysis_compute_hann_spectrum(
            time_s,
            signal.values,
            value_unit=signal.unit,
            scaling="amplitude",
            minimum_samples=minimum_samples,
            uniform_rtol=uniform_relative_tolerance,
        )
    except DataValidationError as exc:
        detail = str(exc)
        lowered = detail.lower()
        if "at least" in lowered and "samples" in lowered:
            reason = f"记录过短；{detail}；不生成伪谱峰"
        elif "finite" in lowered:
            reason = f"序列含缺测或非有限样本；{detail}；不插值"
        elif "uniform" in lowered or "interpolate" in lowered:
            reason = f"采样非等间隔；{detail}；GUI 不会自动插值"
        else:
            reason = f"频谱门禁拒绝：{detail}；不填补、不插值"
        return unavailable(reason)

    warning = (
        "探索性频谱：去均值 + 周期 Hann 窗 + 单边幅值归一化；"
        f"样本跨度 {estimate.sample_span_s:.4g} s，FFT 记录长度 "
        f"{estimate.fft_record_length_s:.4g} s，频率分辨率 {estimate.df_hz:.4g} Hz，"
        f"ENBW={estimate.enbw_hz:.4g} Hz。"
        "短记录或单次谱峰不能作为稳态/稳定性证据。"
    )
    return SpectrumView(
        signal_key=signal.key,
        signal_label=signal.label,
        amplitude_unit=estimate.spectrum_unit,
        available=True,
        reason="",
        sample_count=estimate.sample_count,
        duration_s=estimate.sample_span_s,
        frequency_resolution_hz=estimate.df_hz,
        frequencies_hz=tuple(float(item) for item in estimate.frequencies_hz),
        amplitudes=tuple(float(item) for item in estimate.spectral_values),
        warning=warning,
        metadata=dict(estimate.metadata),
    )


def compare_series(
    reference: SimulationView | ExperimentData,
    candidate: SimulationView | ExperimentData,
    signal_key: str,
) -> ComparisonMetrics:
    """调用 analysis 公共比较 API；任一缺测/非有限样本都显式拒绝。"""

    for side, dataset in (("参考", reference), ("候选", candidate)):
        if isinstance(dataset, ExperimentData) and dataset.role == "unassigned_quick_import":
            raise ValueError(
                f"{side}数据是快速导入/未分配角色；只可叠图，"
                "不计算标定或验证误差"
            )

    try:
        ref_signal = reference.signals[signal_key]
        candidate_signal = candidate.signals[signal_key]
    except KeyError as exc:
        raise ValueError(f"比较数据缺少共同观测量：{signal_key}") from exc
    if len(reference.time_s) != len(ref_signal.values):
        raise ValueError("参考序列的时间与观测量长度不一致")
    if len(candidate.time_s) != len(candidate_signal.values):
        raise ValueError("候选序列的时间与观测量长度不一致")
    ref_unit = _validated_comparison_unit(ref_signal, "参考")
    candidate_unit = _validated_comparison_unit(candidate_signal, "候选")
    if ref_unit != candidate_unit:
        raise ValueError(
            f"比较单位不一致：{ref_unit!r} ≠ {candidate_unit!r}，拒绝计算伪误差"
        )

    ref_pairs = [
        (float(t), float(v))
        for t, v in zip(reference.time_s, ref_signal.values)
        if math.isfinite(float(t)) and math.isfinite(float(v))
    ]
    candidate_pairs = [
        (float(t), float(v))
        for t, v in zip(candidate.time_s, candidate_signal.values)
        if math.isfinite(float(t)) and math.isfinite(float(v))
    ]
    if len(ref_pairs) != len(ref_signal.values) or len(candidate_pairs) != len(
        candidate_signal.values
    ):
        raise ValueError(
            "比较拒绝含缺测/非有限样本的序列："
            f"参考 {len(ref_pairs)}/{len(ref_signal.values)} 有限，"
            f"候选 {len(candidate_pairs)}/{len(candidate_signal.values)} 有限；"
            "不填补、不跨缺口插值"
        )
    if len(ref_pairs) < 2 or len(candidate_pairs) < 2:
        raise ValueError("每个序列至少需要两个有限样本，缺失值不会被伪填")
    from airvortex.analysis import ObservableSeries, compare_series as analysis_compare_series

    ref_core = ObservableSeries(
        time=[item[0] for item in ref_pairs],
        values=[item[1] for item in ref_pairs],
        unit=ref_unit,
        source="reference",
    )
    candidate_core = ObservableSeries(
        time=[item[0] for item in candidate_pairs],
        values=[item[1] for item in candidate_pairs],
        unit=candidate_unit,
        source="candidate",
    )
    comparison = analysis_compare_series(
        candidate_core,
        ref_core,
        observable=signal_key,
        normalization="right_range",
    )
    residuals = [float(item) for item in comparison.residual]
    count = len(residuals)
    return ComparisonMetrics(
        signal_key=signal_key,
        samples=count,
        rmse=comparison.metrics.rmse,
        mae=comparison.metrics.mae,
        max_abs=comparison.metrics.max_absolute_error,
        bias=sum(residuals) / count,
        unit=ref_unit,
    )


def interpolate(times: Sequence[float], values: Sequence[float], at: float) -> float | None:
    if not times or len(times) != len(values) or at < times[0] or at > times[-1]:
        return None
    index = bisect_left(times, at)
    if index < len(times) and times[index] == at:
        return float(values[index])
    if index == 0 or index >= len(times):
        return None
    left_t, right_t = float(times[index - 1]), float(times[index])
    left_v, right_v = float(values[index - 1]), float(values[index])
    if right_t == left_t:
        return left_v
    weight = (at - left_t) / (right_t - left_t)
    return left_v + weight * (right_v - left_v)


def nearest_index(times: Sequence[float], at: float) -> int:
    if not times:
        return 0
    index = bisect_left(times, at)
    if index <= 0:
        return 0
    if index >= len(times):
        return len(times) - 1
    return index if times[index] - at < at - times[index - 1] else index - 1


def _normalize_signal(
    key: str, value: Any, *, declared_unit: object = _MISSING
) -> SignalSeries | None:
    canonical = _canonical_signal_key(key)
    label, unit = _signal_presentation(canonical)
    unit_origin = "inferred_from_canonical"
    if declared_unit is not _MISSING:
        if isinstance(declared_unit, str):
            unit = declared_unit
            unit_origin = "declared"
        else:
            unit = ""
            unit_origin = "invalid_declared"
    if isinstance(value, Mapping):
        vector = _float_vector(_first_present(value, "values", "data", "y", "series"))
        label = str(value.get("label", label))
        if "unit" in value:
            raw_unit = value["unit"]
            if isinstance(raw_unit, str):
                unit = raw_unit
                unit_origin = "declared"
            else:
                unit = ""
                unit_origin = "invalid_declared"
    else:
        vector = _float_vector(value)
    if not vector:
        return None
    normalized_unit = _normalize_unit_spelling(unit) if isinstance(unit, str) else ""
    return SignalSeries(canonical, label, normalized_unit, vector, unit_origin)


def _normalize_core_surface_frames(
    payload: Mapping[str, Any], times: Sequence[float]
) -> tuple[SurfaceFrame, ...]:
    observables = payload.get("observables")
    fields = payload.get("fields")
    if not isinstance(observables, Mapping) or not isinstance(fields, Mapping):
        return ()
    radius = _float_vector(_first_present(fields, "radius_m", "r_m"))
    profiles = _matrix_or_frames(
        _first_present(observables, "surface_profile_m", "free_surface_profile_m")
    )
    frame_times = _float_vector(fields.get("frame_time_s")) or tuple(times)
    if not radius or not profiles:
        return ()
    return tuple(
        SurfaceFrame(_time_for_index(frame_times, index), radius, profile)
        for index, profile in enumerate(profiles)
        if len(profile) == len(radius)
    )


def _normalize_core_field_frames(
    payload: Mapping[str, Any], times: Sequence[float]
) -> tuple[FieldFrame, ...]:
    fields = payload.get("fields")
    if not isinstance(fields, Mapping):
        return ()
    phase_frames = _cube_frames(
        _first_present(fields, "phase_liquid_fraction", "phase", "phi", "volume_fraction")
    )
    radial_frames = _cube_frames(_first_present(fields, "u_r_m_s", "radial_velocity_m_s"))
    vertical_frames = _cube_frames(_first_present(fields, "u_z_m_s", "vertical_velocity_m_s"))
    azimuthal_frames = _cube_frames(_first_present(fields, "u_theta_m_s", "azimuthal_velocity_m_s"))
    explicit_speed = _cube_frames(
        _first_present(fields, "speed_m_s", "velocity_magnitude_m_s")
    )
    count = max(
        map(
            len,
            (phase_frames, radial_frames, vertical_frames, azimuthal_frames, explicit_speed),
        ),
        default=0,
    )
    if count == 0:
        return ()
    radius = _float_vector(_first_present(fields, "r_m", "radius_m"))
    height = _float_vector(_first_present(fields, "z_m", "height_m"))
    extent = None
    if radius and height:
        extent = (0.0, max(radius), 0.0, max(height))
    frame_times = _float_vector(fields.get("frame_time_s")) or tuple(times)
    frames: list[FieldFrame] = []
    for index in range(count):
        phase = _frame_or_none(phase_frames, index)
        radial = _frame_or_none(radial_frames, index)
        vertical = _frame_or_none(vertical_frames, index)
        azimuthal = _frame_or_none(azimuthal_frames, index)
        speed = _frame_or_none(explicit_speed, index)
        if speed is None:
            speed = _vector_magnitude(radial, vertical, azimuthal)
        # core 数组按 (r,z) 存储；GUI 的 imshow 按 (y,x) 使用，集中在此转置。
        frames.append(
            FieldFrame(
                _time_for_index(frame_times, index),
                _transpose_matrix(phase),
                _transpose_matrix(speed),
                _transpose_matrix(radial),
                _transpose_matrix(vertical),
                extent,
            )
        )
    return tuple(frames)


def _normalize_surface_frames(
    payload: Mapping[str, Any], times: Sequence[float]
) -> tuple[SurfaceFrame, ...]:
    raw_frames = _first_present(payload, "surface_frames", "surface_profiles", "free_surface")
    frames: list[SurfaceFrame] = []
    if isinstance(raw_frames, Mapping):
        radii = _first_present(raw_frames, "radius_m", "r_m", "r")
        heights = _first_present(raw_frames, "height_m", "z_m", "z", "values")
        height_rows = _matrix_or_frames(heights)
        radius_rows = _matrix_or_frames(radii)
        for index, row in enumerate(height_rows):
            radius = radius_rows[index] if len(radius_rows) == len(height_rows) else (radius_rows[0] if radius_rows else ())
            if radius and len(radius) == len(row):
                frames.append(SurfaceFrame(_time_for_index(times, index), radius, row))
    elif isinstance(raw_frames, Sequence) and not isinstance(raw_frames, (str, bytes)):
        for index, item in enumerate(raw_frames):
            frame = _object_mapping(item)
            radius = _float_vector(_first_present(frame, "radius_m", "r_m", "r", "radius"))
            height = _float_vector(_first_present(frame, "height_m", "z_m", "z", "height"))
            if radius and len(radius) == len(height):
                frame_time = _scalar_or_default(_first_present(frame, "time_s", "time", "t"), _time_for_index(times, index))
                frames.append(SurfaceFrame(frame_time, radius, height))
    if frames:
        return tuple(frames)

    radius = _float_vector(_first_present(payload, "surface_radius_m", "free_surface_r_m"))
    heights = _matrix_or_frames(_first_present(payload, "surface_height_m", "free_surface_z_m"))
    if radius and heights:
        return tuple(
            SurfaceFrame(_time_for_index(times, index), radius, row)
            for index, row in enumerate(heights)
            if len(row) == len(radius)
        )
    return ()


def _normalize_field_frames(
    payload: Mapping[str, Any], times: Sequence[float]
) -> tuple[FieldFrame, ...]:
    raw_frames = _first_present(payload, "field_frames", "fields", "snapshots")
    frames: list[FieldFrame] = []
    if isinstance(raw_frames, Sequence) and not isinstance(raw_frames, (str, bytes, Mapping)):
        for index, item in enumerate(raw_frames):
            frame = _object_mapping(item)
            phase = _float_matrix(_first_present(frame, "phase", "phi", "phase_field", "volume_fraction"))
            speed = _float_matrix(_first_present(frame, "speed_m_s", "velocity_magnitude_m_s", "speed"))
            radial = _float_matrix(_first_present(frame, "radial_velocity_m_s", "u_r_m_s", "u_r"))
            vertical = _float_matrix(_first_present(frame, "vertical_velocity_m_s", "u_z_m_s", "u_z"))
            if phase is None and speed is None and radial is None and vertical is None:
                continue
            extent = _extent(_first_present(frame, "extent_m", "extent"))
            frame_time = _scalar_or_default(_first_present(frame, "time_s", "time", "t"), _time_for_index(times, index))
            frames.append(FieldFrame(frame_time, phase, speed, radial, vertical, extent))
        return tuple(frames)

    phase_frames = _cube_frames(_first_present(payload, "phase", "phi", "phase_field"))
    speed_frames = _cube_frames(_first_present(payload, "speed_m_s", "velocity_magnitude_m_s", "speed"))
    radial_frames = _cube_frames(_first_present(payload, "radial_velocity_m_s", "u_r_m_s", "u_r"))
    vertical_frames = _cube_frames(_first_present(payload, "vertical_velocity_m_s", "u_z_m_s", "u_z"))
    count = max(map(len, (phase_frames, speed_frames, radial_frames, vertical_frames)), default=0)
    extent = _extent(_first_present(payload, "field_extent_m", "extent_m", "extent"))
    for index in range(count):
        frames.append(
            FieldFrame(
                _time_for_index(times, index),
                _frame_or_none(phase_frames, index),
                _frame_or_none(speed_frames, index),
                _frame_or_none(radial_frames, index),
                _frame_or_none(vertical_frames, index),
                extent,
            )
        )
    return tuple(frames)


def _object_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        # ``SimulationResult`` 含 MappingProxyType/ndarray；asdict 会深拷贝并失败。
        return {item.name: getattr(value, item.name) for item in dataclass_fields(value)}
    for method_name in ("to_dict", "as_dict", "model_dump"):
        method = getattr(value, method_name, None)
        if callable(method):
            candidate = method()
            if isinstance(candidate, Mapping):
                return dict(candidate)
    if hasattr(value, "__dict__"):
        return {key: item for key, item in vars(value).items() if not key.startswith("_")}
    raise TypeError(f"无法识别的求解结果类型：{type(value).__name__}")


def _first_present(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _to_plain(value: Any) -> Any:
    if hasattr(value, "tolist") and callable(value.tolist):
        return value.tolist()
    return value


def _float_vector(value: Any) -> tuple[float, ...]:
    value = _to_plain(value)
    if value is None or isinstance(value, (str, bytes, Mapping)):
        return ()
    if isinstance(value, Sequence):
        output: list[float] = []
        for item in value:
            item = _to_plain(item)
            if isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
                return ()
            try:
                output.append(float(item))
            except (TypeError, ValueError):
                return ()
        return tuple(output)
    return ()


def _float_matrix(value: Any) -> tuple[tuple[float, ...], ...] | None:
    value = _to_plain(value)
    if value is None or isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Sequence):
        return None
    rows: list[tuple[float, ...]] = []
    for raw_row in value:
        row = _float_vector(raw_row)
        if not row:
            return None
        if rows and len(row) != len(rows[0]):
            return None
        rows.append(row)
    return tuple(rows) if rows else None


def _transpose_matrix(
    matrix: tuple[tuple[float, ...], ...] | None,
) -> tuple[tuple[float, ...], ...] | None:
    if matrix is None:
        return None
    return tuple(tuple(row[index] for row in matrix) for index in range(len(matrix[0])))


def _vector_magnitude(
    radial: tuple[tuple[float, ...], ...] | None,
    vertical: tuple[tuple[float, ...], ...] | None,
    azimuthal: tuple[tuple[float, ...], ...] | None,
) -> tuple[tuple[float, ...], ...] | None:
    components = [item for item in (radial, vertical, azimuthal) if item is not None]
    if not components:
        return None
    rows, columns = len(components[0]), len(components[0][0])
    if any(len(item) != rows or any(len(row) != columns for row in item) for item in components):
        return None
    return tuple(
        tuple(
            math.sqrt(sum(component[row][column] ** 2 for component in components))
            for column in range(columns)
        )
        for row in range(rows)
    )


def _cube_frames(value: Any) -> tuple[tuple[tuple[float, ...], ...], ...]:
    value = _to_plain(value)
    if value is None or isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Sequence):
        return ()
    direct = _float_matrix(value)
    if direct is not None:
        return (direct,)
    frames: list[tuple[tuple[float, ...], ...]] = []
    for raw_frame in value:
        frame = _float_matrix(raw_frame)
        if frame is not None:
            frames.append(frame)
    return tuple(frames)


def _matrix_or_frames(value: Any) -> tuple[tuple[float, ...], ...]:
    value = _to_plain(value)
    if value is None:
        return ()
    vector = _float_vector(value)
    if vector:
        return (vector,)
    matrix = _float_matrix(value)
    return matrix or ()


def _frame_or_none(frames: Sequence[Any], index: int) -> Any:
    if not frames:
        return None
    return frames[min(index, len(frames) - 1)]


def _extent(value: Any) -> tuple[float, float, float, float] | None:
    vector = _float_vector(value)
    if len(vector) == 4:
        return vector[0], vector[1], vector[2], vector[3]
    return None


def _time_for_index(times: Sequence[float], index: int) -> float:
    if not times:
        return float(index)
    return float(times[min(index, len(times) - 1)])


def _scalar_or_default(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _mapping_copy(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    return {}


def _set_dotted(document: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current: dict[str, Any] = document
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            raise KeyError(f"core 配置路径不存在：{path}")
        current = child
    if parts[-1] not in current:
        raise KeyError(f"core 配置字段不存在：{path}")
    current[parts[-1]] = value


def _get_dotted(document: Mapping[str, Any], path: str) -> Any:
    current: Any = document
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise KeyError(f"core 配置路径不存在：{path}")
        current = current[part]
    return current


def _canonical_signal_key(key: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.strip().lower()).strip("_")
    aliases = {
        "funnel_depth_m": "surface_depth_m",
        "surface_depth": "surface_depth_m",
        "core_depth_m": "surface_depth_m",
        "air_core_depth_m": "surface_depth_m",
        "vortex_depth_m": "surface_depth_m",
        "depth_m": "surface_depth_m",
        "air_core_radius_m": "air_core_bottom_radius_m",
        "core_radius_m": "air_core_bottom_radius_m",
        "vortex_radius_m": "air_core_bottom_radius_m",
        "surface_min_z": "surface_min_z_m",
        "min_gap_to_bar": "min_gap_to_bar_m",
        "minimum_gap_m": "min_gap_to_bar_m",
        "rpm": "actual_bar_rpm",
        "speed_rpm": "actual_bar_rpm",
        "stirbar_speed_rpm": "actual_bar_rpm",
        "equivalent_bar_rpm": "actual_bar_rpm",
    }
    return aliases.get(normalized, normalized or "signal")


def _normalize_unit_spelling(unit: object) -> str:
    """只合并数值等价的单位拼写；不在此处做数值缩放。"""

    raw = str(unit).strip()
    compact = re.sub(r"\s+", " ", raw).lower()
    aliases = {
        "rpm": "r/min",
        "r/min": "r/min",
        "rev/min": "r/min",
        "rad/s": "rad/s",
        "rad s^-1": "rad/s",
        "rad s⁻¹": "rad/s",
        "rad·s⁻¹": "rad/s",
        "m/s": "m/s",
        "m s^-1": "m/s",
        "m s⁻¹": "m/s",
        "m·s⁻¹": "m/s",
        "m^2 s^-1": "m²·s⁻¹",
        "m² s⁻¹": "m²·s⁻¹",
        "m²·s⁻¹": "m²·s⁻¹",
        "m^3": "m³",
        "m³": "m³",
    }
    return aliases.get(compact, raw)


def _validated_comparison_unit(signal: SignalSeries, side: str) -> str:
    if signal.unit_origin in {"inferred_from_canonical", "invalid_declared"}:
        raise ValueError(
            f"{side}序列单位来源为 {signal.unit_origin}，未显式声明物理单位；"
            "只可叠图，拒绝计算伪误差"
        )
    unit: object = signal.unit
    if not isinstance(unit, str):
        raise ValueError(f"{side}序列单位必须是明确字符串，得到 {type(unit).__name__}")
    normalized = _normalize_unit_spelling(unit)
    if not normalized.strip():
        raise ValueError(f"{side}序列单位缺失；只可叠图，拒绝计算伪误差")
    if normalized.strip().casefold() in {
        "si",
        "unknown",
        "unspecified",
        "n/a",
        "na",
        "?",
    }:
        raise ValueError(
            f"{side}序列单位 {unit!r} 是占位符，未标识物理维度；"
            "只可叠图，拒绝计算伪误差"
        )
    return normalized


def _is_demonstration_metadata(metadata: Mapping[str, Any]) -> bool:
    status = metadata.get("evidence_status")
    explicit_status = (
        isinstance(status, str) and status.strip().casefold() == "demonstration"
    )
    return explicit_status or metadata.get("is_demonstration") is True


def _signal_presentation(key: str) -> tuple[str, str]:
    table = {
        "surface_depth_m": ("自由表面漏斗深度（气芯代理）", "m"),
        "air_core_bottom_radius_m": ("气芯底部半径", "m"),
        "surface_min_z_m": ("最低自由表面高度", "m"),
        "min_gap_to_bar_m": ("自由表面—磁子最小间隙", "m"),
        "actual_bar_rpm": ("实际磁子转速", "r/min"),
        "critical_actual_rpm": ("临界实际磁子转速", "r/min"),
        "actual_angular_velocity_rad_s": ("实际角速度", "rad·s⁻¹"),
        "burgers_c_m2_s": ("Burgers 系数 C", "m²·s⁻¹"),
        "circulation_m2_s": ("Burgers 系数 C（兼容别名，非 Γ）", "m²·s⁻¹"),
        "physical_circulation_gamma_m2_s": ("远场物理环量 Γ", "m²·s⁻¹"),
        "surface_state_m4_s2": ("表面状态量", "m⁴·s⁻²"),
        "half_width_m": ("漏斗半宽", "m"),
        "liquid_volume_error_m3": ("液体体积误差", "m³"),
        "liquid_volume_error": ("液体相对体积误差", "1"),
        "liquid_mass_error_relative": ("液体相对质量误差", "1"),
        "liquid_volume_m3": ("液体体积", "m³"),
        "connected_proxy": ("连通代理判据", "1"),
        "first_contact_proxy": ("首次接触代理", "1"),
        "connection_candidate": ("气体连通候选", "1"),
        "max_divergence": ("最大散度", "s⁻¹"),
        "divergence_before_projection": ("投影前散度", "s⁻¹"),
        "cfl_advective": ("对流 CFL", "1"),
        "cfl_viscous": ("黏性 CFL", "1"),
        "cfl_capillary": ("毛细 CFL", "1"),
        "pressure_residual": ("压力残差", "1"),
        "kinetic_energy_j": ("动能", "J"),
        "volume_error_percent": ("体积误差", "%"),
    }
    return table.get(key, (key.replace("_", " "), ""))


def _column_kind(header: str) -> tuple[str, float, str, str]:
    raw = header.strip()
    unit_match = re.search(
        r"[\[(（]\s*(ms|s|mm|cm|m|rpm)\s*[\])）]\s*$", raw, flags=re.IGNORECASE
    )
    unit_hint = unit_match.group(1).lower() if unit_match else ""
    name_part = raw[: unit_match.start()].strip() if unit_match else raw
    compact = re.sub(r"[\s_\-/·（）()\[\]]+", "", name_part.lower())
    if compact in {"t", "time", "times", "ts", "时间", "时间s", "时间秒"}:
        return "time_s", 1e-3 if unit_hint == "ms" else 1.0, "时间", "s"
    if compact in {"timems", "tms", "时间ms", "时间毫秒"}:
        return "time_s", 1e-3, "时间", "s"

    depth_terms = (
        "surfacedepth", "funneldepth", "coredepth", "aircoredepth", "vortexdepth",
        "自由表面漏斗深度", "漏斗深度", "气芯深度", "涡深",
    )
    radius_terms = (
        "aircorebottomradius", "coreradius", "aircoreradius", "vortexradius",
        "气芯底部半径", "气芯半径", "涡核半径",
    )
    for terms, key, label in (
        (depth_terms, "surface_depth_m", "自由表面漏斗深度（气芯代理）"),
        (radius_terms, "air_core_bottom_radius_m", "气芯底部半径"),
    ):
        if any(compact.startswith(term) for term in terms):
            if unit_hint == "mm" or compact.endswith("mm") or "毫米" in compact:
                return key, 1e-3, label, "m"
            if unit_hint == "cm" or compact.endswith("cm") or "厘米" in compact:
                return key, 1e-2, label, "m"
            return key, 1.0, label, "m"
    if unit_hint == "rpm" or compact in {
        "rpm", "speedrpm", "stirbarspeedrpm", "actualbarrpm", "实际磁子转速", "转速", "转速rpm"
    }:
        return "actual_bar_rpm", 1.0, "实际磁子转速", "r/min"

    key = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "_", raw).strip("_").lower() or "signal"
    return key, 1.0, raw, ""


def _csv_signal_header(signal: SignalSeries) -> str:
    return f"{signal.key}[{signal.unit}]" if signal.unit else signal.key


def _format_export(value: float) -> str:
    if not math.isfinite(float(value)):
        return ""
    return f"{float(value):.12g}"


def _format_number(value: float | int) -> str:
    return str(value) if isinstance(value, int) else f"{value:g}"


def _json_ready(value: Any) -> Any:
    """严格、可追踪地把常见数值容器转成 JSON；不丢字段。"""

    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("元数据含 NaN/Inf，拒绝静默导出")
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return _json_ready(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_ready(item) for item in value]
    if hasattr(value, "tolist") and callable(value.tolist):
        return _json_ready(value.tolist())
    if hasattr(value, "item") and callable(value.item):
        return _json_ready(value.item())
    raise TypeError(f"元数据含不可序列化对象：{type(value).__name__}")


__all__ = [
    "BACKEND_PRESENTATIONS",
    "DEMONSTRATION_WARNING",
    "DOMAIN_LIMIT_WARNING",
    "FORWARD_POST_CONTACT_WARNING",
    "PARAMETER_BY_KEY",
    "PARAMETER_SPECS",
    "BackendMode",
    "BackendPresentation",
    "ButtonPolicy",
    "ComparisonMetrics",
    "ExperimentData",
    "FieldFrame",
    "GuiViewModel",
    "LogEntry",
    "ParameterSpec",
    "RunState",
    "ScanPointView",
    "ScanView",
    "SignalSeries",
    "SimulationView",
    "SpectrumView",
    "SurfaceFrame",
    "ValidationIssue",
    "compare_series",
    "compute_hann_spectrum",
    "coerce_backend_mode",
    "interpolate",
    "nearest_index",
    "normalize_simulation_result",
    "read_experiment_csv",
]
