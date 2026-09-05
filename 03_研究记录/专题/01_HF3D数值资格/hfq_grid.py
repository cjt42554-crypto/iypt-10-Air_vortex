"""HF3D grid-resolution audits and refinement configuration construction."""
from __future__ import annotations
from copy import deepcopy
import math
from typing import Any, Mapping
import numpy as np
from hfq_types import GridAudit, JsonObject, QualificationError
from hfq_json import (
    _finite_positive,
    _positive_int,
    _require_mapping,
    canonical_sha256,
    deep_get,
    deep_set,
)

def _capsule_volume(half_length_m: float, radius_m: float) -> float:
    segment_half = max(half_length_m - radius_m, 0.0)
    return (
        math.pi * radius_m**2 * (2.0 * segment_half)
        + 4.0 * math.pi * radius_m**3 / 3.0
    )

def grid_audit(config: Mapping[str, Any]) -> GridAudit:
    container = _require_mapping(config.get("container"), "container")
    stir_bar = _require_mapping(config.get("stir_bar"), "stir_bar")
    hf = _require_mapping(config.get("high_fidelity"), "high_fidelity")

    radius_m = _finite_positive(container.get("radius_m"), "container.radius_m")
    height_m = _finite_positive(
        container.get("liquid_depth_m"), "container.liquid_depth_m"
    ) + _finite_positive(
        container.get("headspace_height_m"), "container.headspace_height_m"
    )
    bar_radius_m = _finite_positive(stir_bar.get("radius_m"), "stir_bar.radius_m")
    half_length_m = _finite_positive(
        stir_bar.get("half_length_m"), "stir_bar.half_length_m"
    )
    nx = _positive_int(hf.get("radial_cells"), "high_fidelity.radial_cells")
    ny = _positive_int(hf.get("azimuthal_cells"), "high_fidelity.azimuthal_cells")
    nz = _positive_int(hf.get("axial_cells"), "high_fidelity.axial_cells")

    dx_m = 2.0 * radius_m / nx
    dy_m = 2.0 * radius_m / ny
    dz_m = height_m / nz
    h_min_m = min(dx_m, dy_m, dz_m)
    h_max_m = max(dx_m, dy_m, dz_m)
    h_geometric_m = (dx_m * dy_m * dz_m) ** (1.0 / 3.0)
    bar_diameter_m = 2.0 * bar_radius_m
    exact_bar_volume_m3 = _capsule_volume(half_length_m, bar_radius_m)
    cell_volume_m3 = dx_m * dy_m * dz_m
    interface_cells = _finite_positive(
        hf.get("interface_thickness_cells"),
        "high_fidelity.interface_thickness_cells",
    )
    boundary_cells = _finite_positive(
        hf.get("boundary_layer_cells"),
        "high_fidelity.boundary_layer_cells",
    )
    interface_width_m = interface_cells * h_min_m
    boundary_layer_width_m = boundary_cells * h_min_m
    return GridAudit(
        nx=nx,
        ny=ny,
        nz=nz,
        dx_m=dx_m,
        dy_m=dy_m,
        dz_m=dz_m,
        h_min_m=h_min_m,
        h_max_m=h_max_m,
        h_geometric_m=h_geometric_m,
        aspect_ratio=h_max_m / h_min_m,
        bar_diameter_m=bar_diameter_m,
        bar_diameter_cells_best_axis=bar_diameter_m / h_min_m,
        bar_diameter_cells_worst_axis=bar_diameter_m / h_max_m,
        exact_bar_volume_m3=exact_bar_volume_m3,
        nominal_bar_voxel_count=exact_bar_volume_m3 / cell_volume_m3,
        interface_width_m=interface_width_m,
        interface_width_cells_worst_axis=interface_width_m / h_max_m,
        boundary_layer_width_m=boundary_layer_width_m,
        boundary_layer_width_cells_worst_axis=boundary_layer_width_m / h_max_m,
        box_cell_count=nx * ny * nz,
    )

def _round_up(value: int, multiple: int) -> int:
    if multiple < 1:
        raise QualificationError("round_to_multiple must be >= 1")
    return int(math.ceil(value / multiple) * multiple)

def dimensions_for_target_bar_resolution(
    config: Mapping[str, Any],
    target_cells_worst_axis: float,
    *,
    round_to_multiple: int = 2,
) -> tuple[int, int, int]:
    target = _finite_positive(target_cells_worst_axis, "target bar resolution")
    container = _require_mapping(config.get("container"), "container")
    stir_bar = _require_mapping(config.get("stir_bar"), "stir_bar")
    radius_m = _finite_positive(container.get("radius_m"), "container.radius_m")
    height_m = _finite_positive(
        container.get("liquid_depth_m"), "container.liquid_depth_m"
    ) + _finite_positive(
        container.get("headspace_height_m"), "container.headspace_height_m"
    )
    diameter_m = 2.0 * _finite_positive(stir_bar.get("radius_m"), "stir_bar.radius_m")
    h_limit_m = diameter_m / target
    nx = _round_up(max(6, math.ceil(2.0 * radius_m / h_limit_m)), round_to_multiple)
    ny = _round_up(max(6, math.ceil(2.0 * radius_m / h_limit_m)), round_to_multiple)
    nz = _round_up(max(6, math.ceil(height_m / h_limit_m)), round_to_multiple)
    return nx, ny, nz

def apply_overrides(config: Mapping[str, Any], overrides: Mapping[str, Any]) -> JsonObject:
    result = deepcopy(dict(config))
    for path, value in overrides.items():
        if not isinstance(path, str) or not path:
            raise QualificationError("override keys must be non-empty dotted paths")
        deep_set(result, path, value)
    return result

def make_refinement_config(
    base: Mapping[str, Any],
    *,
    target_bar_cells_worst_axis: float,
    time_step_s: float,
    round_to_multiple: int = 2,
) -> JsonObject:
    result = deepcopy(dict(base))
    if result.get("backend") != "high_fidelity":
        result["backend"] = "high_fidelity"
    nx, ny, nz = dimensions_for_target_bar_resolution(
        result,
        target_bar_cells_worst_axis,
        round_to_multiple=round_to_multiple,
    )
    deep_set(result, "high_fidelity.radial_cells", nx)
    deep_set(result, "high_fidelity.azimuthal_cells", ny)
    deep_set(result, "high_fidelity.axial_cells", nz)
    deep_set(
        result,
        "high_fidelity.time_step_s",
        _finite_positive(time_step_s, "time_step_s"),
    )
    # Fail early if the generated dimensions do not actually meet the target.
    audit = grid_audit(result)
    if audit.bar_diameter_cells_worst_axis + 1.0e-12 < target_bar_cells_worst_axis:
        raise QualificationError("generated grid does not meet target worst-axis bar resolution")
    return result

def integrate_configured_revolutions(config: Mapping[str, Any]) -> float:
    """Integrate the configured signed actual bar speed over the run duration."""

    run = _require_mapping(config.get("run"), "run")
    drive = _require_mapping(config.get("drive"), "drive")
    duration_s = _finite_positive(run.get("duration_s"), "run.duration_s")
    times = drive.get("actual_speed_schedule_time_s")
    speeds = drive.get("actual_speed_schedule_rpm")
    rpm_seconds = 0.0
    if isinstance(times, list) and isinstance(speeds, list) and times and speeds:
        if len(times) != len(speeds) or len(times) < 2:
            raise QualificationError("actual speed schedule arrays must have equal length >= 2")
        t_values = np.asarray(times, dtype=np.float64)
        rpm_values = np.asarray(speeds, dtype=np.float64)
        if not np.all(np.isfinite(t_values)) or not np.all(np.diff(t_values) > 0.0):
            raise QualificationError("actual speed schedule times must increase")
        if not np.all(np.isfinite(rpm_values)):
            raise QualificationError("actual speed schedule rpm must be finite")
        if duration_s <= t_values[0]:
            rpm_seconds = float(rpm_values[0] * duration_s)
        else:
            # Integrate each clipped linear segment.
            current = 0.0
            if t_values[0] > 0.0:
                current += float(rpm_values[0] * min(duration_s, t_values[0]))
            for left, right, y0, y1 in zip(
                t_values[:-1], t_values[1:], rpm_values[:-1], rpm_values[1:]
            ):
                if duration_s <= left:
                    break
                width = min(duration_s, right) - left
                if width <= 0.0:
                    continue
                slope = (y1 - y0) / (right - left)
                current += float(y0 * width + 0.5 * slope * width**2)
                if duration_s <= right:
                    break
            if duration_s > t_values[-1]:
                current += float(rpm_values[-1] * (duration_s - t_values[-1]))
            rpm_seconds = current
    else:
        direction = drive.get("rotation_direction", 1)
        if direction not in (-1, 1):
            raise QualificationError("drive.rotation_direction must be -1 or 1")
        initial = float(direction) * float(drive.get("initial_rpm", 0.0))
        target = float(direction) * float(drive.get("target_rpm", 0.0))
        ramp = float(drive.get("ramp_duration_s", 0.0))
        if not all(math.isfinite(value) for value in (initial, target, ramp)) or ramp < 0.0:
            raise QualificationError("drive values must be finite and ramp duration >= 0")
        if ramp > 0.0:
            width = min(duration_s, ramp)
            slope = (target - initial) / ramp
            rpm_seconds = initial * width + 0.5 * slope * width**2
            if duration_s > ramp:
                rpm_seconds += target * (duration_s - ramp)
        else:
            rpm_seconds = target * duration_s
    return rpm_seconds / 60.0

def audit_config(config: Mapping[str, Any], *, label: str = "config") -> JsonObject:
    grid = grid_audit(config)
    run = _require_mapping(config.get("run"), "run")
    output_interval_s = _finite_positive(
        run.get("output_interval_s"), "run.output_interval_s"
    )
    duration_s = _finite_positive(run.get("duration_s"), "run.duration_s")
    time_step_s = _finite_positive(
        deep_get(config, "high_fidelity.time_step_s"),
        "high_fidelity.time_step_s",
    )
    revolutions = integrate_configured_revolutions(config)
    warnings: list[dict[str, str]] = []

    def add(code: str, severity: str, message: str) -> None:
        warnings.append({"code": code, "severity": severity, "message": message})

    if grid.bar_diameter_cells_worst_axis < 1.0:
        add(
            "BAR_SUBCELL_WORST_AXIS",
            "critical",
            "磁子直径在最粗网格方向上不足一个单元。",
        )
    elif grid.bar_diameter_cells_worst_axis < 4.0:
        add(
            "BAR_SEVERELY_UNDERRESOLVED",
            "critical",
            "磁子直径在最差方向上不足四格，属于严重欠分辨。",
        )
    elif grid.bar_diameter_cells_worst_axis < 8.0:
        add(
            "BAR_UNDERRESOLVED_FOR_QUALIFICATION",
            "major",
            "磁子直径在最差方向上不足八格，不适合进入资格判断。",
        )
    if grid.nominal_bar_voxel_count < 50.0:
        add(
            "BAR_TOO_FEW_VOXELS",
            "major",
            "磁子精确体积对应的 Cartesian 单元少于 50 个。",
        )
    if grid.aspect_ratio > 1.25:
        add(
            "GRID_ANISOTROPY",
            "major",
            "最大与最小网格间距之比超过 1.25。",
        )
    if grid.interface_width_cells_worst_axis < 2.0:
        add(
            "INTERFACE_UNDERRESOLVED_WORST_AXIS",
            "major",
            "实现中的弥散界面宽度在最差方向上不足两格。",
        )
    if abs(revolutions) < 1.0:
        add(
            "SUB_ROTATION_WINDOW",
            "major",
            "配置时长覆盖的带符号磁子转数不足一圈。",
        )
    if duration_s / time_step_s < 20.0:
        add(
            "TOO_FEW_CONFIGURED_STEPS",
            "major",
            "按最大步长计算，配置时长不足 20 步。",
        )
    if duration_s / output_interval_s < 5.0:
        add(
            "TOO_FEW_OUTPUT_INTERVALS",
            "minor",
            "运行时长不足五个输出间隔。",
        )

    critical = any(item["severity"] == "critical" for item in warnings)
    major = any(item["severity"] == "major" for item in warnings)
    claim_ceiling = (
        "PREVIEW_ONLY"
        if critical
        else "NUMERICAL_SCREENING_ONLY"
        if major
        else "ELIGIBLE_FOR_REFINEMENT_STUDY"
    )
    return {
        "schema_version": 1,
        "label": label,
        "backend": config.get("backend"),
        "config_sha256": canonical_sha256(config),
        "grid": grid.to_dict(),
        "run": {
            "duration_s": duration_s,
            "output_interval_s": output_interval_s,
            "maximum_time_step_s": time_step_s,
            "nominal_maximum_step_count": math.ceil(duration_s / time_step_s),
            "configured_signed_bar_revolutions": revolutions,
        },
        "warnings": warnings,
        "claim_ceiling": claim_ceiling,
        "experimental_validation": "NOT_PERFORMED",
        "apparatus_prediction_allowed": False,
    }

def audit_markdown(audit: Mapping[str, Any]) -> str:
    grid = _require_mapping(audit.get("grid"), "audit.grid")
    run = _require_mapping(audit.get("run"), "audit.run")
    warnings = audit.get("warnings", [])
    lines = [
        f"# HF3D 配置分辨率审计：{audit.get('label', 'config')}",
        "",
        "## 结论",
        "",
        f"- 结论上限：`{audit.get('claim_ceiling')}`。",
        "- 本审计只检查配置所对应的离散尺度与时间窗，不是一次 HF3D 运行，也不是实验验证。",
        "- 装置级临界转速、通道形成时刻和稳定通道半径仍不得据此声称。",
        "",
        "## 关键量",
        "",
        f"- 网格：`{grid['nx']} × {grid['ny']} × {grid['nz']}`，盒状网格单元数 `{grid['box_cell_count']:,}`。",
        f"- 网格间距：$\\Delta x={grid['dx_m']:.6g}\\,\\mathrm{{m}}$，$\\Delta y={grid['dy_m']:.6g}\\,\\mathrm{{m}}$，$\\Delta z={grid['dz_m']:.6g}\\,\\mathrm{{m}}$。",
        f"- 各向异性比 $h_{{\\max}}/h_{{\\min}}={grid['aspect_ratio']:.4g}$。",
        f"- 磁子直径按最佳方向计 `{grid['bar_diameter_cells_best_axis']:.4g}` 格，按最差方向计 `{grid['bar_diameter_cells_worst_axis']:.4g}` 格。",
        f"- 磁子精确体积只相当于约 `{grid['nominal_bar_voxel_count']:.4g}` 个网格单元。",
        f"- 弥散界面物理宽度 `{grid['interface_width_m']:.6g} m`，在最差方向仅跨 `{grid['interface_width_cells_worst_axis']:.4g}` 格。",
        f"- 配置时长 `{run['duration_s']:.6g} s`，最大步长 `{run['maximum_time_step_s']:.6g} s`，带符号磁子总转数 `{run['configured_signed_bar_revolutions']:.6g}`。",
        "",
        "## 发现的问题",
        "",
    ]
    if not warnings:
        lines.append("未触发本工具的配置级警告；仍须完成实际三层网格与三层时间步收敛。")
    else:
        for item in warnings:
            lines.append(
                f"- `{item['severity'].upper()}` / `{item['code']}`：{item['message']}"
            )
    lines.extend(
        [
            "",
            "## 指标解释",
            "",
            "正式后端当前记录的 `bar_diameter_cells_min_spacing` 使用 $D/h_{\\min}$，它表示最佳分辨方向。若网格各向异性，真正保守的几何门应使用 $D/h_{\\max}$。本审计同时报告两者，并把最差方向作为资格判断依据。",
            "",
        ]
    )
    return "\n".join(lines)
