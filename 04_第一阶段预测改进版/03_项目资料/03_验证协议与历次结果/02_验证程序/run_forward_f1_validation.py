#!/usr/bin/env python3
"""Frozen numerical-verification campaign for the topology-capable F1 backend.

The campaign verifies implementation identities, conservation/dissipation and
time/space/interface refinement.  It deliberately does not call a numerical
PASS an experimental or physical validation of forward theory F1.0.
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
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PROTOCOL = ROOT / "validation" / "forward_f1_protocol_v2.json"
THEORY = ROOT / "deliverables" / "01_前向预测理论.md"
SCRIPT = Path(__file__).resolve()
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from airvortex.backends.forward_topology import ForwardTopologyBackend  # noqa: E402
from airvortex.core import BackendMode, DriveConfig, RunConfig, SimulationConfig  # noqa: E402
from airvortex.io import config_sha256, save_run_archive  # noqa: E402


SCHEMA = "airvortex-forward-f1-validation-result-v1"


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
    # The formal campaign executes the complete project unit-test suite, so
    # every discovered test module belongs to the frozen source identity.
    paths += list((ROOT / "tests").glob("test_*.py"))
    paths += [PROTOCOL, THEORY, SCRIPT, ROOT / "pyproject.toml"]
    rows: list[dict[str, Any]] = []
    tree = hashlib.sha256(b"airvortex-forward-f1-validation-source-v1\0")
    for path in sorted({item.resolve() for item in paths if item.is_file()}):
        relative = path.relative_to(ROOT).as_posix()
        digest = sha256_file(path)
        rows.append(
            {"path": relative, "sha256": digest, "bytes": path.stat().st_size}
        )
        tree.update(relative.encode("utf-8") + b"\0" + digest.encode("ascii") + b"\0")
    return {"tree_sha256": tree.hexdigest(), "files": rows}


def event_time(result: Any, kind: str) -> float | None:
    for event in result.events:
        if event.kind == kind:
            return float(event.time_s)
    return None


def build_case(
    *, rpm: float, duration_s: float, nr: int, nz: int, dt_s: float
) -> SimulationConfig:
    base = SimulationConfig()
    return replace(
        base,
        backend=BackendMode.FORWARD,
        drive=DriveConfig(target_rpm=rpm, ramp_duration_s=0.5),
        run=RunConfig(duration_s=duration_s, output_interval_s=0.025),
        forward=replace(
            base.forward,
            radial_cells=nr,
            axial_cells=nz,
            integration_time_step_s=dt_s,
            interface_thickness_cells=1.5,
            minimum_resolved_throat_cells=3,
            stable_channel_radius_m=0.001,
            stable_channel_hold_time_s=0.2,
        ),
    )


def run_case(output: Path, case_id: str, config: SimulationConfig) -> dict[str, Any]:
    started = time.perf_counter()
    result = ForwardTopologyBackend().run(config)
    wall = time.perf_counter() - started
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
    discretisation = result.metadata["discretisation"]
    grid_height = config.container.liquid_depth_m + config.container.headspace_height_m
    metrics = {
        "case_id": case_id,
        "config_sha256": config_sha256(config),
        "rpm": config.drive.target_rpm,
        "duration_s": config.run.duration_s,
        "radial_cells": config.forward.radial_cells,
        "axial_cells": config.forward.axial_cells,
        "nominal_dt_s": config.forward.integration_time_step_s,
        "h_max_m": max(
            config.container.radius_m / config.forward.radial_cells,
            grid_height / config.forward.axial_cells,
        ),
        "epsilon_m": (
            config.forward.interface_thickness_cells
            * max(
                config.container.radius_m / config.forward.radial_cells,
                grid_height / config.forward.axial_cells,
            )
        ),
        "wall_time_s": wall,
        "status": result.status.value,
        "domain_status": result.metadata["domain_status"],
        "solution_class": result.metadata["solution_class"],
        "final_depth_m": float(result.observables["surface_depth_m"][-1]),
        "final_gap_m": float(result.observables["minimum_gap_m"][-1]),
        "final_throat_radius_m": float(
            result.observables["minimum_throat_radius_m"][-1]
        ),
        "formed_channel": bool(np.max(result.observables["finite_channel"]) > 0.5),
        "formed_resolved_channel": bool(
            np.max(result.observables["resolved_channel"]) > 0.5
        ),
        "first_touch_time_s": event_time(result, "first_target_touch"),
        "finite_channel_time_s": event_time(result, "finite_channel"),
        "maximum_abs_liquid_volume_error_m3": discretisation[
            "maximum_abs_liquid_volume_error_m3"
        ],
        "maximum_fixed_forcing_energy_increase_j": discretisation[
            "maximum_fixed_forcing_energy_increase_j"
        ],
        "volume_gate_passed": discretisation["volume_gate_passed"],
        "energy_gate_passed": discretisation["energy_gate_passed"],
        "accepted_internal_steps": discretisation["accepted_internal_steps"],
        "final_phase_sha256": sha256_array(
            result.fields["final_phase_order_parameter"]
        ),
        "archive": archive.relative_to(output).as_posix(),
    }
    return metrics


def relative_difference(left: float, right: float) -> float:
    return abs(left - right) / max(abs(left), abs(right), np.finfo(float).tiny)


def fit_event_extrapolation(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    h = np.asarray([row["h_max_m"] for row in rows], dtype=np.float64)
    event = np.asarray([row["first_touch_time_s"] for row in rows], dtype=np.float64)
    slope, intercept = np.polyfit(h, event, 1)
    residual = event - (slope * h + intercept)
    return {
        "slope_s_per_m": float(slope),
        "zero_h_intercept_s": float(intercept),
        "rms_s": float(np.sqrt(np.mean(residual**2))),
        "normalized_rms": float(
            np.sqrt(np.mean(residual**2)) / max(abs(intercept), np.finfo(float).tiny)
        ),
    }


def write_case_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    fields = sorted({str(key) for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def markdown_report(
    acceptance: Mapping[str, Any],
    cases: Sequence[Mapping[str, Any]],
    extrapolation: Mapping[str, Any],
) -> str:
    lines = [
        "# F1.0 前向数值实现冻结验证报告",
        "",
        f"- 总结论：`{acceptance['overall_status']}`",
        f"- 协议 SHA-256：`{acceptance['protocol_sha256']}`",
        f"- 源码边界不变：`{acceptance['source_unchanged']}`",
        "- 声称边界：这只验证数值实现与离散趋势，**不验证物理理论，不给出实验临界转速**。",
        "",
        "## 硬门",
        "",
        "| 门 | 结果 | 数值 |",
        "|---|---:|---|",
    ]
    for key, gate in acceptance["gates"].items():
        lines.append(
            f"| `{key}` | {'PASS' if gate['passed'] else 'FAIL'} | "
            f"{gate.get('observed', '')} |"
        )
    lines += [
        "",
        "## 空间—界面联合外推",
        "",
        f"- 四级线性 (h\\to0) 首次触及外推："
        f"`{extrapolation['all_levels']['zero_h_intercept_s']:.6g} s`。",
        f"- 归一化拟合 RMS："
        f"`{extrapolation['all_levels']['normalized_rms']:.6g}`。",
        f"- 最终三级与全四级外推相对差："
        f"`{extrapolation['stability_relative_difference']:.6g}`。",
        "",
        "该外推是数值尖界面极限诊断，不是装置级预测；参数仍为 "
        "`FIXED_REFERENCE_NOT_EXPERIMENTALLY_CALIBRATED`。",
        "",
        "## 案例摘要",
        "",
        "| 案例 | nr×nz | dt / s | 深度 / m | 首次触及 / s | 颈半径 / m | 墙钟 / s |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in cases:
        touch = row["first_touch_time_s"]
        lines.append(
            f"| `{row['case_id']}` | {row['radial_cells']}×{row['axial_cells']} | "
            f"{row['nominal_dt_s']:.4g} | {row['final_depth_m']:.6g} | "
            f"{'' if touch is None else f'{touch:.6g}'} | "
            f"{row['final_throat_radius_m']:.6g} | {row['wall_time_s']:.3f} |"
        )
    lines += [
        "",
        "## 负结果与不允许的解读",
        "",
        "- 单一网格的接触/连通时刻不是定量结果；它对界面厚度有可见依赖。",
        "- 通过能量和体积门不等于旋流闭合、轴对称假设或常数迁移率已被实验验证。",
        "- 一格连通只是前兆；必须通过颈宽—界面厚度—网格联合门。",
        "- 高保真后端和结构性理论不在本报告中。",
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
        help="run two-level smoke only; never emits a formal PASS",
    )
    args = parser.parse_args(argv)

    PROTOCOL = args.protocol.resolve()
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    supported_schemas = {
        "airvortex-forward-f1-numerical-validation-protocol-v1",
        "airvortex-forward-f1-numerical-validation-protocol-v2",
    }
    if protocol.get("schema") not in supported_schemas:
        raise ValueError("unsupported F1 validation protocol")
    protocol_version = protocol["schema"].rsplit("-v", 1)[-1]
    theory_hash = sha256_file(THEORY)
    if theory_hash != protocol["theory"]["sha256"]:
        raise ValueError(
            "frozen theory identity mismatch: "
            f"{theory_hash} != {protocol['theory']['sha256']}"
        )
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = (
        args.output.resolve()
        if args.output is not None
        else ROOT / "validation" / "results" / f"{timestamp}-forward-f1-v{protocol_version}"
    )
    if output.exists():
        raise FileExistsError(f"validation output already exists: {output}")
    output.mkdir(parents=True)
    before = source_identity()
    write_json(output / "protocol.snapshot.json", protocol)
    write_json(output / "source.before.json", before)

    test_command = [
        sys.executable,
        "-m",
        "unittest",
        "discover",
        "-s",
        "tests",
        "-v",
    ]
    test_environment = dict(os.environ)
    test_environment["PYTHONPATH"] = str(SRC)
    tests = subprocess.run(
        test_command,
        cwd=ROOT,
        env=test_environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    (output / "unit_tests.txt").write_text(tests.stdout, encoding="utf-8")

    levels = protocol["space_interface_refinement"]["levels"]
    if args.quick:
        levels = levels[:2]
    cases: list[dict[str, Any]] = []
    for level in levels:
        nr = int(level["radial_cells"])
        nz = int(level["axial_cells"])
        dt = float(level["nominal_dt_s"])
        cases.append(
            run_case(
                output,
                f"space-pre-{nr}x{nz}",
                build_case(rpm=1200.0, duration_s=1.5, nr=nr, nz=nz, dt_s=dt),
            )
        )
        cases.append(
            run_case(
                output,
                f"space-top-{nr}x{nz}",
                build_case(rpm=2000.0, duration_s=2.0, nr=nr, nz=nz, dt_s=dt),
            )
        )

    time_rows: list[dict[str, Any]] = []
    time_steps = protocol["time_refinement"]["nominal_dt_s"]
    if args.quick:
        time_steps = time_steps[:2]
    for dt in time_steps:
        row = run_case(
            output,
            f"time-dt-{str(dt).replace('.', 'p')}",
            build_case(
                rpm=2000.0,
                duration_s=2.0,
                nr=32,
                nz=56,
                dt_s=float(dt),
            ),
        )
        cases.append(row)
        time_rows.append(row)

    pre = [row for row in cases if row["case_id"].startswith("space-pre-")]
    top = [row for row in cases if row["case_id"].startswith("space-top-")]
    after = source_identity()
    source_unchanged = before["tree_sha256"] == after["tree_sha256"]
    write_json(output / "source.after.json", after)

    if len(top) >= 3:
        all_fit = fit_event_extrapolation(top)
        last_three_fit = fit_event_extrapolation(top[-3:])
        extrapolate_stability = relative_difference(
            all_fit["zero_h_intercept_s"],
            last_three_fit["zero_h_intercept_s"],
        )
    else:
        all_fit = {
            "slope_s_per_m": 0.0,
            "zero_h_intercept_s": 0.0,
            "rms_s": 0.0,
            "normalized_rms": 0.0,
        }
        last_three_fit = dict(all_fit)
        extrapolate_stability = 0.0
    extrapolation = {
        "all_levels": all_fit,
        "last_three_levels": last_three_fit,
        "stability_relative_difference": extrapolate_stability,
    }

    gates: dict[str, dict[str, Any]] = {}

    def gate(name: str, passed: bool, observed: Any) -> None:
        gates[name] = {"passed": bool(passed), "observed": observed}

    gate("unit_tests", tests.returncode == 0, f"returncode={tests.returncode}")
    gate("source_unchanged", source_unchanged, before["tree_sha256"])
    gate(
        "all_volume_energy",
        all(row["volume_gate_passed"] and row["energy_gate_passed"] for row in cases),
        {
            "max_volume_error_m3": max(
                row["maximum_abs_liquid_volume_error_m3"] for row in cases
            ),
            "max_energy_increase_j": max(
                row["maximum_fixed_forcing_energy_increase_j"] for row in cases
            ),
        },
    )
    gate(
        "subcritical_disconnected",
        all(not row["formed_channel"] for row in pre),
        [row["formed_channel"] for row in pre],
    )
    if len(pre) >= 2:
        pre_depth_difference = relative_difference(
            pre[-2]["final_depth_m"], pre[-1]["final_depth_m"]
        )
    else:
        pre_depth_difference = 0.0
    gate(
        "subcritical_finest_depth",
        len(pre) >= 4 and pre_depth_difference <= 0.05,
        pre_depth_difference,
    )
    gate(
        "topology_all_resolved",
        all(row["formed_resolved_channel"] for row in top),
        [row["formed_resolved_channel"] for row in top],
    )
    top_times = [float(row["first_touch_time_s"]) for row in top]
    monotone = all(right <= left + 1.0e-12 for left, right in zip(top_times, top_times[1:]))
    gate("topology_event_monotone", monotone, top_times)
    gate(
        "topology_extrapolation_fit",
        len(top) >= 4 and all_fit["normalized_rms"] <= 0.01,
        all_fit["normalized_rms"],
    )
    gate(
        "topology_extrapolation_stability",
        len(top) >= 4 and extrapolate_stability <= 0.03,
        extrapolate_stability,
    )
    if len(top) >= 2:
        throat_difference = relative_difference(
            top[-2]["final_throat_radius_m"],
            top[-1]["final_throat_radius_m"],
        )
    else:
        throat_difference = 0.0
    gate(
        "topology_finest_throat",
        len(top) >= 4 and throat_difference <= 0.10,
        throat_difference,
    )
    time_events = [float(row["first_touch_time_s"]) for row in time_rows]
    time_throats = [float(row["final_throat_radius_m"]) for row in time_rows]
    time_event_difference = (
        abs(time_events[-1] - time_events[-2]) if len(time_events) >= 2 else 0.0
    )
    time_throat_difference = (
        relative_difference(time_throats[-1], time_throats[-2])
        if len(time_throats) >= 2
        else 0.0
    )
    gate(
        "time_finest_event",
        len(time_rows) >= 3 and time_event_difference <= 0.025,
        time_event_difference,
    )
    gate(
        "time_finest_throat",
        len(time_rows) >= 3 and time_throat_difference <= 0.02,
        time_throat_difference,
    )
    gate(
        "time_all_resolved",
        all(row["formed_resolved_channel"] for row in time_rows),
        [row["formed_resolved_channel"] for row in time_rows],
    )

    formal = not args.quick
    overall = "PASS_NUMERICAL_IMPLEMENTATION_ONLY" if formal and all(
        item["passed"] for item in gates.values()
    ) else "QUICK_SMOKE_NO_FORMAL_ACCEPTANCE" if args.quick else "FAIL_OR_NOT_DEMONSTRATED"
    acceptance = {
        "schema": SCHEMA,
        "overall_status": overall,
        "formal_protocol_complete": formal,
        "protocol_sha256": sha256_file(PROTOCOL),
        "theory_sha256": theory_hash,
        "source_unchanged": source_unchanged,
        "gates": gates,
        "claim_boundary": protocol["claim_boundary"],
        "environment": {
            "python": sys.version,
            "executable": sys.executable,
            "numpy": np.__version__,
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
        },
        "command": [
            sys.executable,
            str(SCRIPT),
            *(("--quick",) if args.quick else ()),
        ],
    }
    write_case_csv(output / "case_index.csv", cases)
    write_json(output / "extrapolation.json", extrapolation)
    write_json(output / "acceptance.json", acceptance)
    write_json(
        output / "negative_results.json",
        {
            "single_grid_event_time_quantitative": False,
            "physical_model_validated": False,
            "experimental_threshold_established": False,
            "unassessed": [
                "apparatus-specific A,c,tau_C calibration",
                "multi-height S_z calibration",
                "multi-scale M_Gamma calibration",
                "wetting-law calibration",
                "axisymmetry error from 3D observation",
                "comparison against independent high-fidelity backend",
                "comparison against frozen experimental validation set"
            ]
        },
    )
    (output / "REPORT.md").write_text(
        markdown_report(acceptance, cases, extrapolation),
        encoding="utf-8",
        newline="\n",
    )
    write_json(
        output / "manifest.json",
        {
            "schema": SCHEMA,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "overall_status": overall,
            "protocol": "protocol.snapshot.json",
            "protocol_sha256": sha256_file(PROTOCOL),
            "theory_sha256": theory_hash,
            "source_tree_sha256": before["tree_sha256"],
            "case_count": len(cases),
            "unit_test_returncode": tests.returncode,
            "output_is_append_only_by_construction": True,
        },
    )
    print(json.dumps({"status": overall, "output": str(output)}, ensure_ascii=False))
    return 0 if overall == "PASS_NUMERICAL_IMPLEMENTATION_ONLY" or args.quick else 1


if __name__ == "__main__":
    raise SystemExit(main())
