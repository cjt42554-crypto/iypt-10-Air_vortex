#!/usr/bin/env python3
"""Frozen implementation and physical-NO-GO campaign for HF3D v1.

Passing this campaign certifies only that the independent local-law program
meets its pre-registered numerical implementation gates.  Dynamic grid
convergence, apparatus calibration and experiment validation are deliberately
separate physical-GO gates and may remain NO-GO without turning an honest
implementation result into a false failure or a false prediction.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PROTOCOL = ROOT / "validation" / "hf3d_protocol_v2.json"
SCRIPT = Path(__file__).resolve()
REQUIREMENTS = Path("/home/cjt/竞赛物理研究四层通用框架与统一计算平台需求说明.md")
FORWARD_THEORY = ROOT / "deliverables" / "01_前向预测理论.md"
FORWARD_NUMERICAL_THEORY = ROOT / "deliverables" / "02_前向预测理论的数值计算理论.md"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from airvortex import NUMERICAL_THREAD_POLICY  # noqa: E402
from airvortex.backends.high_fidelity_3d import HighFidelityBackend  # noqa: E402
from airvortex.core import (  # noqa: E402
    BackendMode,
    DriveConfig,
    RunConfig,
    SimulationConfig,
)
from airvortex.io import config_sha256, save_run_archive  # noqa: E402
from airvortex.numerics import (  # noqa: E402
    ImmersedCartesianGrid,
    VariableDensityProjection3D,
    mixture_property_3d,
)
import numpy as np  # noqa: E402


SCHEMA = "airvortex-hf3d-validation-result-v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_array(value: np.ndarray) -> str:
    array = np.ascontiguousarray(value)
    digest = hashlib.sha256()
    digest.update(str(array.dtype).encode("ascii"))
    digest.update(json.dumps(list(array.shape), separators=(",", ":")).encode("ascii"))
    digest.update(array.tobytes(order="C"))
    return digest.hexdigest()


def json_ready(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, (float, np.floating)):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"non-finite JSON value: {number}")
        return number
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, Mapping):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if isinstance(value, np.ndarray):
        return json_ready(value.tolist())
    if hasattr(value, "value"):
        return json_ready(value.value)
    raise TypeError(f"unsupported JSON type: {type(value).__name__}")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        json_ready(value),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(payload, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def source_identity() -> dict[str, Any]:
    paths = list(SRC.rglob("*.py"))
    paths += list((ROOT / "tests").glob("test_*.py"))
    paths += list((ROOT / "configs").glob("*.json"))
    paths += [
        PROTOCOL,
        SCRIPT,
        REQUIREMENTS,
        FORWARD_THEORY,
        FORWARD_NUMERICAL_THEORY,
        ROOT / "pyproject.toml",
    ]
    rows: list[dict[str, Any]] = []
    tree = hashlib.sha256(b"airvortex-hf3d-validation-source-v1\0")
    for path in sorted({item.resolve() for item in paths if item.is_file()}):
        try:
            name = path.relative_to(ROOT).as_posix()
        except ValueError:
            name = str(path)
        digest = sha256_file(path)
        rows.append({"path": name, "sha256": digest, "bytes": path.stat().st_size})
        tree.update(name.encode("utf-8") + b"\0" + digest.encode("ascii") + b"\0")
    return {"tree_sha256": tree.hexdigest(), "files": rows}


def relative_difference(left: float, right: float) -> float:
    return abs(left - right) / max(abs(left), abs(right), np.finfo(float).tiny)


def build_case(
    *,
    cells: int,
    maximum_dt_s: float,
    rpm: float,
    duration_s: float,
    compression: bool,
    pressure_tolerance: float = 1.0e-8,
) -> SimulationConfig:
    base = SimulationConfig()
    output_interval = min(0.001, duration_s)
    return replace(
        base,
        backend=BackendMode.HIGH_FIDELITY,
        drive=DriveConfig(
            initial_rpm=0.0,
            target_rpm=abs(rpm),
            ramp_duration_s=min(0.002, duration_s) if rpm else 0.0,
            rotation_direction=1 if rpm >= 0.0 else -1,
        ),
        run=RunConfig(duration_s=duration_s, output_interval_s=output_interval),
        forward=replace(
            base.forward,
            integration_time_step_s=min(1.0e-4, output_interval),
        ),
        high_fidelity=replace(
            base.high_fidelity,
            radial_cells=cells,
            azimuthal_cells=cells,
            axial_cells=max(10, int(round(1.25 * cells))),
            time_step_s=maximum_dt_s,
            pressure_relative_tolerance=pressure_tolerance,
            pressure_max_iterations=1800,
            interface_compression_interval=1 if compression else 0,
            interface_compression_pseudo_cfl=0.02,
        ),
    )


def case_metrics(
    output: Path,
    case_id: str,
    config: SimulationConfig,
) -> tuple[dict[str, Any], Any]:
    started = time.perf_counter()
    result = HighFidelityBackend().run(config)
    wall_time = time.perf_counter() - started
    archive = output / "cases" / case_id
    save_run_archive(
        archive,
        config,
        result,
        extra_provenance={
            "validation_schema": SCHEMA,
            "validation_protocol_sha256": sha256_file(PROTOCOL),
            "case_id": case_id,
        },
    )
    diagnostics = result.metadata["diagnostics"]
    accelerator = diagnostics["pressure_accelerator"]
    geometry = diagnostics["geometry_resolution"]
    lab_fields = np.stack(
        [
            result.fields["final_u_x_lab_m_s_3d"],
            result.fields["final_u_y_lab_m_s_3d"],
            result.fields["final_u_z_lab_m_s_3d"],
        ]
    )
    speed = np.sqrt(np.sum(lab_fields * lab_fields, axis=0))
    metrics = {
        "case_id": case_id,
        "archive": archive.relative_to(output).as_posix(),
        "config_sha256": config_sha256(config),
        "cells_x": config.high_fidelity.radial_cells,
        "cells_y": config.high_fidelity.azimuthal_cells,
        "cells_z": config.high_fidelity.axial_cells,
        "maximum_dt_s": config.high_fidelity.time_step_s,
        "rpm": (
            config.drive.rotation_direction * config.drive.target_rpm
        ),
        "duration_s": config.run.duration_s,
        "wall_time_s": wall_time,
        "status": result.status.value,
        "completed_steps": diagnostics["completed_steps"],
        "minimum_actual_dt_s": diagnostics["minimum_time_step_s"],
        "final_kinetic_energy_lab_j": float(
            result.observables["kinetic_energy_lab_j"][-1]
        ),
        "final_angular_momentum_z_kg_m2_s": float(
            result.observables["angular_momentum_z_kg_m2_s"][-1]
        ),
        "final_maximum_speed_lab_m_s": float(np.max(speed)),
        "final_funnel_depth_m": float(result.observables["funnel_depth_m"][-1]),
        "final_axisymmetry_error": float(result.observables["axisymmetry_error"][-1]),
        "maximum_projected_divergence_s_inv": diagnostics[
            "maximum_projected_divergence_s_inv"
        ],
        "maximum_pressure_relative_residual": diagnostics[
            "maximum_pressure_relative_residual"
        ],
        "maximum_pressure_iterations": float(
            np.max(result.fields["diagnostic_pressure_iterations"])
            if result.fields["diagnostic_pressure_iterations"].size
            else 0.0
        ),
        "pressure_solver_requested": accelerator["requested"],
        "pressure_solver_selected": accelerator["selected"],
        "pressure_solver_fallback": accelerator["fallback"],
        "pressure_solver_device_name": accelerator.get("device_name") or "",
        "liquid_open_boundary_balance_error_relative": diagnostics[
            "liquid_open_boundary_balance_error_relative"
        ],
        "maximum_single_step_phase_clip_relative": diagnostics[
            "maximum_single_step_phase_clip_relative"
        ],
        "cumulative_phase_clip_relative": diagnostics[
            "cumulative_phase_clip_relative"
        ],
        "maximum_single_step_phase_repair_relative": diagnostics[
            "maximum_single_step_phase_repair_relative"
        ],
        "cumulative_phase_repair_relative": diagnostics[
            "cumulative_phase_repair_relative"
        ],
        "bar_diameter_cells_min_spacing": geometry[
            "bar_diameter_cells_min_spacing"
        ],
        "bar_volume_relative_error": geometry["bar_volume_relative_error"],
        "vessel_volume_relative_error": geometry["vessel_volume_relative_error"],
        "final_phase_sha256": sha256_array(
            result.fields["final_phase_liquid_fraction_3d"]
        ),
        "final_velocity_sha256": sha256_array(lab_fields),
    }
    return metrics, result


def projection_check() -> dict[str, Any]:
    grid = ImmersedCartesianGrid(
        12,
        12,
        16,
        0.045,
        0.080,
        bar_half_length_m=0.015,
        bar_radius_m=0.003,
        bar_centre_z_m=0.0045,
    )
    rng = np.random.default_rng(20270821)
    ux = rng.normal(scale=0.02, size=grid.face_shape_x) * grid.open_x
    uy = rng.normal(scale=0.02, size=grid.face_shape_y) * grid.open_y
    uz = rng.normal(scale=0.02, size=grid.face_shape_z) * grid.open_z
    phase = np.where(grid.z_cell < 0.060, 1.0, 0.0)
    density = mixture_property_3d(phase, 998.2, 1.204)
    solver = VariableDensityProjection3D(
        grid, relative_tolerance=1.0e-9, maximum_iterations=1200
    )
    _, _, _, pressure, diagnostics = solver.project(
        ux, uy, uz, density, 1.0e-4
    )
    return {
        "seed": 20270821,
        "divergence_before_s_inv": diagnostics.divergence_before_s_inv,
        "divergence_after_s_inv": diagnostics.divergence_after_s_inv,
        "divergence_ratio": (
            diagnostics.divergence_after_s_inv
            / diagnostics.divergence_before_s_inv
        ),
        "pressure_relative_residual": diagnostics.pressure_relative_residual,
        "iterations": diagnostics.iterations,
        "active_unknowns": diagnostics.active_unknowns,
        "matrix_nonzeros": diagnostics.matrix_nonzeros,
        "pressure_sha256": sha256_array(pressure),
    }


def velocity_relative_l2(result: Any, reference: Any) -> float:
    names = (
        "final_u_x_lab_m_s_3d",
        "final_u_y_lab_m_s_3d",
        "final_u_z_lab_m_s_3d",
    )
    value = np.stack([result.fields[name] for name in names])
    target = np.stack([reference.fields[name] for name in names])
    mask = reference.fields["active_fluid_mask_3d"].astype(bool)
    return float(
        np.linalg.norm((value - target)[:, mask])
        / max(np.linalg.norm(target[:, mask]), np.finfo(float).tiny)
    )


def reversal_metrics(positive: Any, negative: Any) -> dict[str, float]:
    phase_positive = positive.fields["final_phase_liquid_fraction_3d"]
    phase_negative = negative.fields["final_phase_liquid_fraction_3d"][:, ::-1, :]
    phase_error = float(
        np.linalg.norm(phase_positive - phase_negative)
        / max(np.linalg.norm(phase_positive), np.finfo(float).tiny)
    )
    names = (
        "final_u_x_lab_m_s_3d",
        "final_u_y_lab_m_s_3d",
        "final_u_z_lab_m_s_3d",
    )
    velocity_positive = np.stack([positive.fields[name] for name in names])
    velocity_negative = np.stack(
        [negative.fields[name][:, ::-1, :] for name in names]
    )
    # Reflection y -> -y leaves x/z vector components unchanged and flips y.
    velocity_negative[1] *= -1.0
    mask = positive.fields["active_fluid_mask_3d"].astype(bool)
    velocity_error = float(
        np.linalg.norm((velocity_positive - velocity_negative)[:, mask])
        / max(np.linalg.norm(velocity_positive[:, mask]), np.finfo(float).tiny)
    )
    positive_ke = float(positive.observables["kinetic_energy_lab_j"][-1])
    negative_ke = float(negative.observables["kinetic_energy_lab_j"][-1])
    positive_l = float(positive.observables["angular_momentum_z_kg_m2_s"][-1])
    negative_l = float(negative.observables["angular_momentum_z_kg_m2_s"][-1])
    return {
        "phase_mirror_relative_l2": phase_error,
        "velocity_mirror_relative_l2": velocity_error,
        "kinetic_energy_relative_difference": relative_difference(
            positive_ke, negative_ke
        ),
        "angular_momentum_antisymmetry_relative_difference": (
            abs(positive_l + negative_l)
            / max(abs(positive_l), abs(negative_l), np.finfo(float).tiny)
        ),
    }


def write_case_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    fields = sorted({str(key) for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def report_markdown(
    acceptance: Mapping[str, Any],
    cases: Sequence[Mapping[str, Any]],
    time_refinement: Mapping[str, Any],
    grid_audit: Mapping[str, Any],
    reversal: Mapping[str, Any],
) -> str:
    lines = [
        "# HF3D v1 冻结验证报告",
        "",
        f"- 实现结论：`{acceptance['implementation_status']}`",
        f"- 物理预测结论：`{acceptance['physical_prediction_status']}`",
        f"- 总结论：`{acceptance['overall_status']}`",
        f"- 协议 SHA-256：`{acceptance['protocol_sha256']}`",
        f"- 源码边界不变：`{acceptance['source_unchanged']}`",
        "- 声称边界：通过只证明独立 HF3D 程序满足预注册实现门；不证明装置临界转速、稳定气道半径或实验真实性。",
        "",
        "## 实现硬门",
        "",
        "| 门 | 结果 | 观测值 |",
        "|---|---:|---|",
    ]
    for key, gate in acceptance["implementation_gates"].items():
        lines.append(
            f"| `{key}` | {'PASS' if gate['passed'] else 'FAIL'} | {gate['observed']} |"
        )
    lines += [
        "",
        "## 物理 GO 审计（允许诚实 NO-GO）",
        "",
        "| 门 | 结果 | 观测值 |",
        "|---|---:|---|",
    ]
    for key, gate in acceptance["physical_go_gates"].items():
        lines.append(
            f"| `{key}` | {'GO' if gate['passed'] else 'NO-GO'} | {gate['observed']} |"
        )
    lines += [
        "",
        "## 时间细化",
        "",
        f"- 粗/中时间步相对最细三维速度误差：`{time_refinement['relative_l2_against_finest']}`。",
        f"- 中/粗误差比：`{time_refinement['medium_to_coarse_error_ratio']:.6g}`（实现趋势门通过不等于已到渐近收敛区）。",
        "",
        "## 动态网格审计",
        "",
        f"- 最细两级动能相对差：`{grid_audit['finest_pair_kinetic_energy_relative_difference']:.6g}`。",
        f"- 最细两级角动量相对差：`{grid_audit['finest_pair_angular_momentum_relative_difference']:.6g}`。",
        f"- 动态序列最细网格磁子直径仅 `"
        f"{grid_audit['finest_bar_diameter_cells']:.6g}` 个最小网格，低于协议所需 6 格。",
        "- 因此该序列的角色是暴露欠分辨和防止过度声称，不是产生装置级预测。",
        "",
        "## 旋向反演",
        "",
        f"- 相场镜像相对 L2：`{reversal['phase_mirror_relative_l2']:.6g}`。",
        f"- 实验室速度镜像相对 L2：`{reversal['velocity_mirror_relative_l2']:.6g}`。",
        f"- 动能相对差：`{reversal['kinetic_energy_relative_difference']:.6g}`。",
        f"- 角动量反对称误差：`{reversal['angular_momentum_antisymmetry_relative_difference']:.6g}`。",
        "",
        "## 案例摘要",
        "",
        "| 案例 | 网格 | max dt / s | rpm | 步数 | KE / J | Lz / kg m2 s | vmax / m s-1 | 体积收支误差 | 墙钟 / s |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in cases:
        lines.append(
            f"| `{row['case_id']}` | {row['cells_x']}×{row['cells_y']}×{row['cells_z']} | "
            f"{row['maximum_dt_s']:.4g} | {row['rpm']:.6g} | {row['completed_steps']} | "
            f"{row['final_kinetic_energy_lab_j']:.6g} | "
            f"{row['final_angular_momentum_z_kg_m2_s']:.6g} | "
            f"{row['final_maximum_speed_lab_m_s']:.6g} | "
            f"{row['liquid_open_boundary_balance_error_relative']:.3e} | "
            f"{row['wall_time_s']:.3f} |"
        )
    lines += [
        "",
        "## 明确不允许的解读",
        "",
        "- `PASS_NUMERICAL_IMPLEMENTATION_ONLY` 不是物理模型验证，也不是实验验证。",
        "- 本动态网格序列没有解析磁子直径和边界层；任何绝对临界 rpm、接触时刻、气道半径均为 NO-GO。",
        "- 体积守恒、静水保持和压力投影通过，不能替代界面曲率、润湿和湍流分辨率研究。",
        "- 本报告不评价前向理论，也不提出结构性理论。",
        "",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    global PROTOCOL
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--protocol",
        type=Path,
        default=PROTOCOL,
        help="frozen protocol JSON; defaults to the current MD-MATH-1 v2 protocol",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="run reduced smoke; never emit formal implementation PASS",
    )
    args = parser.parse_args(argv)
    PROTOCOL = args.protocol.resolve()
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    supported_schemas = {
        "airvortex-hf3d-validation-protocol-v1",
        "airvortex-hf3d-validation-protocol-v2",
    }
    if protocol.get("schema") not in supported_schemas:
        raise ValueError("unsupported HF3D protocol")
    protocol_version = protocol["schema"].rsplit("-v", 1)[-1]
    identities = (
        (REQUIREMENTS, protocol["requirements"]["sha256"]),
        (FORWARD_THEORY, protocol["separation_boundary"]["forward_theory_sha256"]),
        (
            FORWARD_NUMERICAL_THEORY,
            protocol["separation_boundary"]["forward_numerical_theory_sha256"],
        ),
    )
    for path, expected in identities:
        observed = sha256_file(path)
        if observed != expected:
            raise ValueError(f"frozen identity mismatch for {path}: {observed} != {expected}")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = (
        args.output.resolve()
        if args.output is not None
        else ROOT / "validation" / "results" / f"{timestamp}-hf3d-v{protocol_version}"
    )
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    before = source_identity()
    write_json(output / "protocol.snapshot.json", protocol)
    write_json(output / "source.before.json", before)

    command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(SRC)
    tests = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    (output / "unit_tests.txt").write_text(tests.stdout, encoding="utf-8")
    match = re.search(r"Ran\s+(\d+)\s+tests", tests.stdout)
    unit_test_count = int(match.group(1)) if match else 0

    projection = projection_check()
    write_json(output / "projection_check.json", projection)

    cases: list[dict[str, Any]] = []
    hydro_results: list[Any] = []
    hydro_levels = [8, 12, 16] if not args.quick else [8, 12]
    for cells in hydro_levels:
        row, result = case_metrics(
            output,
            f"hydrostatic-{cells}",
            build_case(
                cells=cells,
                maximum_dt_s=1.0e-4,
                rpm=0.0,
                duration_s=0.002,
                compression=False,
            ),
        )
        cases.append(row)
        hydro_results.append(result)

    time_steps = [1.5e-4, 7.5e-5, 3.75e-5]
    if args.quick:
        time_steps = time_steps[:2]
    time_rows: list[dict[str, Any]] = []
    time_results: list[Any] = []
    for dt in time_steps:
        row, result = case_metrics(
            output,
            f"time-{str(dt).replace('.', 'p')}",
            build_case(
                cells=12,
                maximum_dt_s=dt,
                rpm=200.0,
                duration_s=0.004,
                compression=True,
            ),
        )
        cases.append(row)
        time_rows.append(row)
        time_results.append(result)

    grid_levels = [8, 12, 16, 20]
    if args.quick:
        grid_levels = grid_levels[:2]
    grid_rows: list[dict[str, Any]] = []
    grid_results: list[Any] = []
    for cells in grid_levels:
        row, result = case_metrics(
            output,
            f"grid-{cells}",
            build_case(
                cells=cells,
                maximum_dt_s=7.5e-5,
                rpm=200.0,
                duration_s=0.004,
                compression=True,
            ),
        )
        cases.append(row)
        grid_rows.append(row)
        grid_results.append(result)

    negative_row, negative_result = case_metrics(
        output,
        "reversal-negative",
        build_case(
            cells=12,
            maximum_dt_s=7.5e-5,
            rpm=-200.0,
            duration_s=0.004,
            compression=True,
        ),
    )
    cases.append(negative_row)
    positive_result = grid_results[grid_levels.index(12)]
    reversal = reversal_metrics(positive_result, negative_result)
    write_json(output / "reversal.json", reversal)

    if len(time_results) >= 3:
        errors = [
            velocity_relative_l2(result, time_results[-1])
            for result in time_results[:-1]
        ] + [0.0]
        ratio = errors[1] / max(errors[0], np.finfo(float).tiny)
    else:
        errors = [0.0 for _ in time_results]
        ratio = 0.0
    time_refinement = {
        "relative_l2_against_finest": errors,
        "medium_to_coarse_error_ratio": ratio,
        "maximum_dt_s": time_steps,
    }
    write_json(output / "time_refinement.json", time_refinement)

    if len(grid_rows) >= 2:
        grid_ke_difference = relative_difference(
            grid_rows[-2]["final_kinetic_energy_lab_j"],
            grid_rows[-1]["final_kinetic_energy_lab_j"],
        )
        grid_l_difference = relative_difference(
            grid_rows[-2]["final_angular_momentum_z_kg_m2_s"],
            grid_rows[-1]["final_angular_momentum_z_kg_m2_s"],
        )
    else:
        grid_ke_difference = 0.0
        grid_l_difference = 0.0
    grid_audit = {
        "finest_pair_kinetic_energy_relative_difference": grid_ke_difference,
        "finest_pair_angular_momentum_relative_difference": grid_l_difference,
        "finest_bar_diameter_cells": grid_rows[-1][
            "bar_diameter_cells_min_spacing"
        ],
        "levels": grid_levels,
        "rows": grid_rows,
    }
    write_json(output / "dynamic_grid_audit.json", grid_audit)

    after = source_identity()
    source_unchanged = before["tree_sha256"] == after["tree_sha256"]
    write_json(output / "source.after.json", after)

    limits = protocol["implementation_gates"]
    implementation_gates: dict[str, dict[str, Any]] = {}

    def implementation_gate(name: str, passed: bool, observed: Any) -> None:
        implementation_gates[name] = {"passed": bool(passed), "observed": observed}

    implementation_gate(
        "unit_tests", tests.returncode == 0, {"returncode": tests.returncode, "count": unit_test_count}
    )
    implementation_gate("source_unchanged", source_unchanged, before["tree_sha256"])
    implementation_gate(
        "variable_density_projection",
        projection["divergence_ratio"]
        <= limits["random_variable_density_projection_divergence_ratio_max"]
        and projection["pressure_relative_residual"]
        <= limits["random_projection_relative_residual_max"],
        projection,
    )
    hydro_max_speed = max(row["final_maximum_speed_lab_m_s"] for row in cases if row["case_id"].startswith("hydrostatic-"))
    hydro_max_depth = max(abs(row["final_funnel_depth_m"]) for row in cases if row["case_id"].startswith("hydrostatic-"))
    implementation_gate(
        "hydrostatic_rest",
        hydro_max_speed <= limits["zero_drive_hydrostatic_max_speed_m_s"]
        and hydro_max_depth <= limits["zero_drive_shared_funnel_depth_max_m"],
        {"maximum_speed_m_s": hydro_max_speed, "maximum_funnel_depth_m": hydro_max_depth},
    )
    maximum_balance = max(
        abs(row["liquid_open_boundary_balance_error_relative"]) for row in cases
    )
    implementation_gate(
        "open_boundary_volume_balance",
        maximum_balance
        <= limits["maximum_open_boundary_volume_balance_error_relative"],
        maximum_balance,
    )
    quality_pass = all(
        row["maximum_single_step_phase_clip_relative"]
        <= build_case(
            cells=row["cells_x"],
            maximum_dt_s=row["maximum_dt_s"],
            rpm=row["rpm"],
            duration_s=row["duration_s"],
            compression=not row["case_id"].startswith("hydrostatic-"),
        ).high_fidelity.maximum_single_step_phase_clip_relative
        and row["cumulative_phase_clip_relative"] <= 1.0e-3
        and row["maximum_single_step_phase_repair_relative"] <= 1.0e-5
        and row["cumulative_phase_repair_relative"] <= 1.0e-3
        for row in cases
    )
    implementation_gate(
        "phase_boundedness_and_repair", quality_pass,
        {
            "maximum_cumulative_clip_relative": max(row["cumulative_phase_clip_relative"] for row in cases),
            "maximum_cumulative_repair_relative": max(row["cumulative_phase_repair_relative"] for row in cases),
        },
    )
    implementation_gate(
        "time_refinement_trend",
        len(time_results) >= 3
        and ratio <= limits["time_refinement_medium_to_coarse_error_ratio_max"],
        time_refinement,
    )
    implementation_gate(
        "rotation_reversal_symmetry",
        reversal["phase_mirror_relative_l2"]
        <= limits["reversal_phase_mirror_relative_l2_max"]
        and reversal["velocity_mirror_relative_l2"]
        <= limits["reversal_velocity_mirror_relative_l2_max"]
        and reversal["kinetic_energy_relative_difference"]
        <= limits["reversal_kinetic_energy_relative_difference_max"]
        and reversal["angular_momentum_antisymmetry_relative_difference"]
        <= limits[
            "reversal_angular_momentum_antisymmetry_relative_difference_max"
        ],
        reversal,
    )

    physical_limit = protocol["dynamic_grid_audit"][
        "physical_convergence_gate_finest_pair_relative_difference_max"
    ]
    bar_limit = protocol["dynamic_grid_audit"][
        "minimum_bar_diameter_cells_for_physical_go"
    ]
    physical_go_gates = {
        "dynamic_grid_observables": {
            "passed": bool(
                len(grid_rows) >= 4
                and grid_ke_difference <= physical_limit
                and grid_l_difference <= physical_limit
            ),
            "observed": {
                "kinetic_energy_relative_difference": grid_ke_difference,
                "angular_momentum_relative_difference": grid_l_difference,
                "limit": physical_limit,
            },
        },
        "capsule_resolution": {
            "passed": bool(
                len(grid_rows) >= 4
                and grid_audit["finest_bar_diameter_cells"] >= bar_limit
            ),
            "observed": {
                "finest_bar_diameter_cells": grid_audit[
                    "finest_bar_diameter_cells"
                ],
                "minimum": bar_limit,
            },
        },
        "sealed_experiment_validation": {
            "passed": False,
            "observed": "NOT_PROVIDED_AND_NOT_FABRICATED",
        },
    }
    formal = not args.quick
    implementation_pass = formal and all(
        gate["passed"] for gate in implementation_gates.values()
    )
    physical_go = all(gate["passed"] for gate in physical_go_gates.values())
    implementation_status = (
        "PASS_NUMERICAL_IMPLEMENTATION_ONLY"
        if implementation_pass
        else "QUICK_SMOKE_NO_FORMAL_ACCEPTANCE"
        if args.quick
        else "FAIL_IMPLEMENTATION_GATE"
    )
    physical_status = (
        "GO_FROZEN_NUMERICAL_AND_EXPERIMENTAL_VALIDATION"
        if physical_go
        else "NO_GO_UNDERRESOLVED_UNCONVERGED_UNVALIDATED"
    )
    overall = (
        "PASS_IMPLEMENTATION__NO_GO_PHYSICAL_PREDICTION"
        if implementation_pass and not physical_go
        else "PASS_IMPLEMENTATION_AND_PHYSICAL_GO"
        if implementation_pass and physical_go
        else implementation_status
    )
    acceptance = {
        "schema": SCHEMA,
        "overall_status": overall,
        "implementation_status": implementation_status,
        "physical_prediction_status": physical_status,
        "formal_protocol_complete": formal,
        "protocol_sha256": sha256_file(PROTOCOL),
        "source_tree_sha256": before["tree_sha256"],
        "source_unchanged": source_unchanged,
        "implementation_gates": implementation_gates,
        "physical_go_gates": physical_go_gates,
        "claim_boundary": protocol["claim_boundary"],
        "environment": {
            "python": sys.version,
            "executable": sys.executable,
            "numpy": np.__version__,
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "numerical_thread_policy": NUMERICAL_THREAD_POLICY.to_dict(),
        },
        "command": [sys.executable, str(SCRIPT), *(('--quick',) if args.quick else ())],
    }
    write_case_csv(output / "case_index.csv", cases)
    write_json(output / "acceptance.json", acceptance)
    write_json(
        output / "negative_results.json",
        {
            "absolute_critical_rpm_quantitative": False,
            "stable_channel_radius_quantitative": False,
            "apparatus_specific_prediction": False,
            "physical_model_experimentally_validated": False,
            "reason": physical_status,
            "legacy_h1_used": False,
            "forward_theory_used_by_hf3d": False,
        },
    )
    report = report_markdown(
        acceptance, cases, time_refinement, grid_audit, reversal
    )
    (output / "REPORT.md").write_text(report, encoding="utf-8", newline="\n")
    artifact_names = (
        "acceptance.json",
        "case_index.csv",
        "dynamic_grid_audit.json",
        "negative_results.json",
        "projection_check.json",
        "protocol.snapshot.json",
        "REPORT.md",
        "reversal.json",
        "source.after.json",
        "source.before.json",
        "time_refinement.json",
        "unit_tests.txt",
    )
    write_json(
        output / "manifest.json",
        {
            "schema": SCHEMA,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "overall_status": overall,
            "implementation_status": implementation_status,
            "physical_prediction_status": physical_status,
            "protocol_sha256": sha256_file(PROTOCOL),
            "source_tree_sha256": before["tree_sha256"],
            "unit_test_returncode": tests.returncode,
            "unit_test_count": unit_test_count,
            "case_count": len(cases),
            "artifacts": {
                name: {"sha256": sha256_file(output / name), "bytes": (output / name).stat().st_size}
                for name in artifact_names
            },
            "output_is_append_only_by_construction": True,
        },
    )
    print(json.dumps({"status": overall, "output": str(output)}, ensure_ascii=False))
    return 0 if implementation_pass or args.quick else 1


if __name__ == "__main__":
    raise SystemExit(main())
