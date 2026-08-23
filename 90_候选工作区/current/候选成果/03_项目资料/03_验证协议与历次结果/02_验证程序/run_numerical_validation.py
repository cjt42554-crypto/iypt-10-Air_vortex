#!/usr/bin/env python3
"""Reproducible numerical validation for the air-vortex research platform.

The public entry point orchestrates each HF-preview case in a fresh process.
An internal worker writes scalar histories, step diagnostics, final fields and
cryptographic array summaries.  The script never edits ``src/``, ``tests/`` or
``docs/``.  Its assertions concern implementation/numerics only; no successful
gate is labelled as experimental or physical validation.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import platform
import resource
import shutil
import subprocess
import sys
import time
import traceback
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
BASE_PROTOCOL_PATH = ROOT / "validation" / "protocol_v1.json"
V2_PROTOCOL_PATH = ROOT / "validation" / "protocol_v2.json"
V3_PROTOCOL_PATH = ROOT / "validation" / "protocol_v3.json"
V4_PROTOCOL_PATH = ROOT / "validation" / "protocol_v4.json"
PROTOCOL_PATH = ROOT / "validation" / "protocol_v5.json"
FORWARD_V1_PROTOCOL_PATH = ROOT / "validation" / "forward_protocol_v1.json"
FORWARD_V2_PROTOCOL_PATH = ROOT / "validation" / "forward_protocol_v2.json"
FORWARD_V3_PROTOCOL_PATH = ROOT / "validation" / "forward_protocol_v3.json"
FORWARD_V4_PROTOCOL_PATH = ROOT / "validation" / "forward_protocol_v4.json"
FORWARD_PROTOCOL_PATH = ROOT / "validation" / "forward_protocol_v5.json"
INDEPENDENT_BVP_PATH = ROOT / "research" / "scripts" / "verify_nonlinear_surface.py"
SCRIPT_PATH = Path(__file__).resolve()
FORWARD_RUNNER_PATH = ROOT / "validation" / "run_forward_matrix.py"
ACTIVE_SRC = Path(os.environ.get("AIRVORTEX_VALIDATION_SRC", str(SRC))).resolve()
ACTIVE_CONFIG_DIR = Path(
    os.environ.get("AIRVORTEX_VALIDATION_CONFIG_DIR", str(ROOT / "configs"))
).resolve()
if str(ACTIVE_SRC) not in sys.path:
    sys.path.insert(0, str(ACTIVE_SRC))

import numpy as np  # noqa: E402
import scipy  # noqa: E402
from scipy.integrate import quad, trapezoid  # noqa: E402
from scipy.optimize import brentq, minimize  # noqa: E402
from scipy.special import expi  # noqa: E402


SCHEMA = "airvortex-numerical-validation-result-v1"
TINY = np.finfo(np.float64).tiny


def jsonable(value: Any) -> Any:
    """Convert NumPy/enums/mappings to strict-JSON values."""

    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, (float, np.floating)):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"non-finite value cannot be archived: {number}")
        return number
    if isinstance(value, np.integer):
        return int(value)
    if hasattr(value, "value") and isinstance(value.value, (str, int, float)):
        return jsonable(value.value)
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [jsonable(item) for item in value]
    if isinstance(value, np.ndarray):
        return jsonable(value.tolist())
    raise TypeError(f"cannot convert {type(value).__name__} to JSON")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(
        jsonable(value), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
    ) + "\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_rows(path: Path, fieldnames: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})
    os.replace(temporary, path)


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_array(array: np.ndarray) -> str:
    contiguous = np.ascontiguousarray(array)
    digest = hashlib.sha256()
    digest.update(str(contiguous.dtype).encode("ascii"))
    digest.update(json.dumps(list(contiguous.shape), separators=(",", ":")).encode("ascii"))
    digest.update(contiguous.tobytes(order="C"))
    return digest.hexdigest()


def source_manifest(
    source_dir: Path | None = None,
    config_dir: Path | None = None,
) -> dict[str, Any]:
    active_source = (ACTIVE_SRC if source_dir is None else source_dir).resolve()
    active_configs = (
        ACTIVE_CONFIG_DIR if config_dir is None else config_dir
    ).resolve()
    candidates: list[Path] = []
    candidates.extend(active_source.glob("**/*.py"))
    candidates.extend(active_configs.glob("*.json"))
    candidates.extend(
        [
            BASE_PROTOCOL_PATH,
            V2_PROTOCOL_PATH,
            V3_PROTOCOL_PATH,
            V4_PROTOCOL_PATH,
            PROTOCOL_PATH,
            FORWARD_V1_PROTOCOL_PATH,
            FORWARD_V2_PROTOCOL_PATH,
            FORWARD_V3_PROTOCOL_PATH,
            FORWARD_V4_PROTOCOL_PATH,
            FORWARD_PROTOCOL_PATH,
            INDEPENDENT_BVP_PATH,
            SCRIPT_PATH,
            FORWARD_RUNNER_PATH,
        ]
    )
    files = sorted({path.resolve() for path in candidates if path.is_file()})
    rows: list[dict[str, Any]] = []
    tree = hashlib.sha256()
    for path in files:
        if path.is_relative_to(active_source):
            relative = "src/" + path.relative_to(active_source).as_posix()
        elif path.is_relative_to(active_configs):
            relative = "configs/" + path.relative_to(active_configs).as_posix()
        else:
            relative = path.relative_to(ROOT.resolve()).as_posix()
        digest = sha256_file(path)
        size = path.stat().st_size
        rows.append({"path": relative, "sha256": digest, "bytes": size})
        tree.update(relative.encode("utf-8") + b"\0")
        tree.update(digest.encode("ascii") + b"\0")
    return {"tree_sha256": tree.hexdigest(), "files": rows}


def environment_manifest() -> dict[str, Any]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        np.show_config()
    return {
        "python_executable": sys.executable,
        "python_version": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "numpy_version": np.__version__,
        "scipy_version": scipy.__version__,
        "numpy_configuration": buffer.getvalue(),
        "thread_environment": {
            name: os.environ.get(name)
            for name in (
                "OMP_NUM_THREADS",
                "OPENBLAS_NUM_THREADS",
                "MKL_NUM_THREADS",
                "NUMEXPR_NUM_THREADS",
            )
        },
    }


def load_protocol() -> dict[str, Any]:
    v5 = read_json(PROTOCOL_PATH)
    if v5.get("schema") != "airvortex-numerical-validation-protocol-v5":
        raise ValueError("unsupported validation protocol")
    actual_v4_hash = sha256_file(V4_PROTOCOL_PATH)
    if actual_v4_hash != v5.get("base_protocol_sha256"):
        raise ValueError("protocol_v4 hash no longer matches the immutable v5 base")
    v4 = read_json(V4_PROTOCOL_PATH)
    if v4.get("schema") != "airvortex-numerical-validation-protocol-v4":
        raise ValueError("unsupported v4 validation amendment")
    actual_v3_hash = sha256_file(V3_PROTOCOL_PATH)
    if actual_v3_hash != v4.get("base_protocol_sha256"):
        raise ValueError("protocol_v3 hash no longer matches the immutable v4 base")
    v3 = read_json(V3_PROTOCOL_PATH)
    if v3.get("schema") != "airvortex-numerical-validation-protocol-v3":
        raise ValueError("unsupported v3 validation amendment")
    actual_v2_hash = sha256_file(V2_PROTOCOL_PATH)
    if actual_v2_hash != v3.get("base_protocol_sha256"):
        raise ValueError("protocol_v2 hash no longer matches the immutable v3 base")
    amendment = read_json(V2_PROTOCOL_PATH)
    if amendment.get("schema") != "airvortex-numerical-validation-protocol-v2":
        raise ValueError("unsupported v2 validation amendment")
    actual_base_hash = sha256_file(BASE_PROTOCOL_PATH)
    if actual_base_hash != amendment.get("base_protocol_sha256"):
        raise ValueError(
            "protocol_v1 hash no longer matches the immutable v2 amendment base"
        )
    base = read_json(BASE_PROTOCOL_PATH)
    if base.get("schema") != "airvortex-numerical-validation-protocol-v1":
        raise ValueError("unsupported base validation protocol")
    base["schema"] = v5["schema"]
    base["v1_schema"] = "airvortex-numerical-validation-protocol-v1"
    base["v2_amendment"] = amendment
    base["v3_amendment"] = v3
    base["v4_amendment"] = v4
    base["v5_amendment"] = v5
    replacement = v5["amendments"]["case_replacement"]
    for case in base["hf_cases"]:
        if case["id"] == replacement["superseded_id"]:
            case["supersedes_case_id"] = case["id"]
            case["id"] = replacement["effective_id"]
            case["tags"] = replacement["tags"]
            case["expected_termination_reason"] = replacement[
                "expected_termination_reason"
            ]
            case["expected_status"] = replacement["expected_status"]
    return base


def make_hf_config(case: Mapping[str, Any]):
    from airvortex import SimulationConfig

    raw = read_json(ACTIVE_CONFIG_DIR / "hf_preview_quick.json")
    raw["backend"] = "high_fidelity"
    raw["drive"].update(
        {
            "initial_rpm": 0.0,
            "target_rpm": float(case["rpm"]),
            "ramp_duration_s": float(case["ramp_s"]),
            "rotation_direction": int(case["direction"]),
        }
    )
    raw["run"].update(
        {
            "duration_s": float(case["duration_s"]),
            "output_interval_s": float(case["output_interval_s"]),
        }
    )
    raw["high_fidelity"].update(
        {
            "radial_cells": int(case["nr"]),
            "axial_cells": int(case["nz"]),
            "azimuthal_cells": 16,
            "time_step_s": float(case["dt_s"]),
            "interface_thickness_cells": float(case["interface_thickness_cells"]),
        }
    )
    return SimulationConfig.from_dict(raw)


def finite_summary(arrays: Iterable[np.ndarray]) -> bool:
    return all(np.isfinite(np.asarray(array)).all() for array in arrays)


def _worker_hf(case_path: Path, case_output: Path) -> int:
    """Run one HF case in isolation and persist its validation evidence."""

    case_output.mkdir(parents=True, exist_ok=False)
    case = read_json(case_path)
    write_json(case_output / "case_descriptor.json", case)
    started = datetime.now(timezone.utc).isoformat()
    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    try:
        worker_source_start = source_manifest()
        from airvortex.backends.high_fidelity import HighFidelityBackend

        config = make_hf_config(case)
        options = dict(case["backend_options"])
        write_json(case_output / "config.json", config.to_dict())
        write_json(case_output / "backend_options.json", options)
        backend = HighFidelityBackend(**options)
        result = backend.run(config)
        wall_s = time.perf_counter() - wall_start
        cpu_s = time.process_time() - cpu_start

        scalar_names = sorted(
            name
            for name, array in result.observables.items()
            if np.asarray(array).ndim == 1
        )
        timeseries_rows = []
        for index, t_value in enumerate(result.time_s):
            row: dict[str, Any] = {"time_s": float(t_value)}
            for name in scalar_names:
                row[name] = float(np.asarray(result.observables[name])[index])
            timeseries_rows.append(row)
        write_rows(
            case_output / "timeseries.csv",
            ["time_s", *scalar_names],
            timeseries_rows,
        )

        diagnostic_fields = sorted(
            name for name in result.fields if name.startswith("diagnostic_")
        )
        if diagnostic_fields:
            lengths = {np.asarray(result.fields[name]).size for name in diagnostic_fields}
            if len(lengths) != 1:
                raise RuntimeError("per-step diagnostic arrays have inconsistent lengths")
            length = lengths.pop()
            diagnostic_rows = [
                {
                    name: float(np.asarray(result.fields[name])[index])
                    for name in diagnostic_fields
                }
                for index in range(length)
            ]
            write_rows(
                case_output / "step_diagnostics.csv",
                diagnostic_fields,
                diagnostic_rows,
            )

        radius = np.asarray(result.fields["r_m"], dtype=np.float64)
        z_axis = np.asarray(result.fields["z_m"], dtype=np.float64)
        profiles = np.asarray(result.observables["surface_profile_m"], dtype=np.float64)
        profile_rows = (
            {
                "time_s": float(result.time_s[k]),
                "radial_index": i,
                "r_m": float(radius[i]),
                "surface_z_m": float(profiles[k, i]),
            }
            for k in range(result.time_s.size)
            for i in range(radius.size)
        )
        write_rows(
            case_output / "surface_profiles.csv",
            ["time_s", "radial_index", "r_m", "surface_z_m"],
            profile_rows,
        )

        final_field_names = [
            "phase_liquid_fraction",
            "u_r_m_s",
            "u_z_m_s",
            "u_theta_m_s",
            "pressure_projection_pa",
        ]
        final_arrays = {
            name: np.asarray(result.fields[name], dtype=np.float64)[-1]
            for name in final_field_names
        }
        final_rows = (
            {
                "radial_index": i,
                "axial_index": j,
                "r_m": float(radius[i]),
                "z_m": float(z_axis[j]),
                **{name: float(array[i, j]) for name, array in final_arrays.items()},
            }
            for i in range(radius.size)
            for j in range(z_axis.size)
        )
        write_rows(
            case_output / "final_field.csv",
            [
                "radial_index",
                "axial_index",
                "r_m",
                "z_m",
                *final_field_names,
            ],
            final_rows,
        )

        all_arrays = [
            np.asarray(result.time_s),
            *(np.asarray(item) for item in result.observables.values()),
            *(np.asarray(item) for item in result.fields.values()),
        ]
        fields_finite = finite_summary(all_arrays)
        phase = np.asarray(result.fields["phase_liquid_fraction"], dtype=np.float64)
        ur = np.asarray(result.fields["u_r_m_s"], dtype=np.float64)
        uz = np.asarray(result.fields["u_z_m_s"], dtype=np.float64)
        utheta = np.asarray(result.fields["u_theta_m_s"], dtype=np.float64)
        speed_max = float(
            np.max(np.sqrt(np.square(ur) + np.square(uz) + np.square(utheta)))
        )
        initial_final_phase_linf = float(np.max(np.abs(phase[-1] - phase[0])))
        volume = np.asarray(result.observables["liquid_volume_m3"], dtype=np.float64)
        initial_volume = float(volume[0])
        diagnostics = dict(result.metadata["diagnostics"])
        requested_duration_s = float(config.run.duration_s)
        actual_end_time_s = float(diagnostics["simulated_duration_s"])
        termination_reason = str(diagnostics["termination_reason"])
        duration_tolerance_s = 1.0e-12 * max(1.0, requested_duration_s)
        configured_duration_reached = (
            actual_end_time_s >= requested_duration_s - duration_tolerance_s
        )
        domain_limited = termination_reason == (
            "resolved_instantaneous_connection_candidate_domain_limit"
        )
        max_divergence = float(diagnostics["maximum_projected_divergence_s-1"])
        dr = config.container.radius_m / config.high_fidelity.radial_cells
        domain_height = 1.25 * config.container.liquid_depth_m
        dz = domain_height / config.high_fidelity.axial_cells
        h = min(dr, dz)
        omega_bar = abs(config.drive.target_rpm) * 2.0 * math.pi / 60.0
        velocity_scale = max(
            speed_max,
            omega_bar * config.stir_bar.half_length_m,
            h / config.run.duration_s,
            1.0e-14,
        )
        dimensionless_divergence = max_divergence * h / velocity_scale
        completed_steps = int(diagnostics["completed_steps"])
        average_dt = (
            float(diagnostics["simulated_duration_s"]) / completed_steps
            if completed_steps > 0
            else 0.0
        )
        cumulative_repair = float(
            diagnostics["cumulative_phase_mass_repair_absolute_volume_m3"]
        )
        cumulative_clip = float(
            diagnostics["cumulative_phase_clip_absolute_volume_m3"]
        )
        per_step_clip = np.asarray(
            result.fields["diagnostic_phase_clip_absolute_volume_m3"],
            dtype=np.float64,
        )
        per_step_repair = np.asarray(
            result.fields["diagnostic_phase_mass_repair_absolute_volume_m3"],
            dtype=np.float64,
        )
        worker_source_end = source_manifest()
        source_unchanged = (
            worker_source_start["tree_sha256"]
            == worker_source_end["tree_sha256"]
        )

        array_summaries: dict[str, Any] = {}
        for namespace, mapping in (
            ("observable", result.observables),
            ("field", result.fields),
        ):
            for name, array_like in mapping.items():
                array = np.asarray(array_like)
                array_summaries[f"{namespace}:{name}"] = {
                    "shape": list(array.shape),
                    "dtype": str(array.dtype),
                    "sha256": sha256_array(array),
                    "minimum": float(np.min(array)) if array.size else None,
                    "maximum": float(np.max(array)) if array.size else None,
                }

        summary = {
            "schema": SCHEMA,
            "case_id": case["id"],
            "tags": case["tags"],
            "started_utc": started,
            "completed_utc": datetime.now(timezone.utc).isoformat(),
            "status": result.status.value,
            "requested_duration_s": requested_duration_s,
            "actual_end_time_s": actual_end_time_s,
            "configured_duration_reached": configured_duration_reached,
            "termination_reason": termination_reason,
            "domain_limited": domain_limited,
            "source_tree_sha256_start": worker_source_start["tree_sha256"],
            "source_tree_sha256_end": worker_source_end["tree_sha256"],
            "source_unchanged_during_case": source_unchanged,
            "all_outputs_finite": fields_finite,
            "wall_time_s": wall_s,
            "cpu_time_s": cpu_s,
            "peak_rss_kib": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
            "completed_steps": completed_steps,
            "average_effective_dt_s": average_dt,
            "minimum_effective_dt_s": float(diagnostics["minimum_time_step_s"]),
            "effective_2d_cell_steps": int(
                config.high_fidelity.radial_cells
                * config.high_fidelity.axial_cells
                * completed_steps
            ),
            "effective_2d_cell_steps_per_wall_s": (
                config.high_fidelity.radial_cells
                * config.high_fidelity.axial_cells
                * completed_steps
                / max(wall_s, TINY)
            ),
            "azimuthal_cells_counted_in_work": False,
            "configured_cfl_limit": config.high_fidelity.cfl_limit,
            "maximum_speed_m_s": speed_max,
            "maximum_funnel_depth_m": float(
                np.max(result.observables["funnel_depth_m"])
            ),
            "final_funnel_depth_m": float(
                result.observables["funnel_depth_m"][-1]
            ),
            "initial_final_phase_linf": initial_final_phase_linf,
            "phase_minimum": float(np.min(phase)),
            "phase_maximum": float(np.max(phase)),
            "maximum_relative_liquid_volume_error": float(
                np.max(np.abs(result.observables["liquid_mass_error_relative"]))
            ),
            "maximum_raw_phase_mass_error_relative": float(
                diagnostics["maximum_raw_phase_mass_error_relative"]
            ),
            "cumulative_phase_clip_absolute_volume_m3": cumulative_clip,
            "cumulative_phase_mass_repair_absolute_volume_m3": cumulative_repair,
            "cumulative_phase_clip_fraction": cumulative_clip / initial_volume,
            "cumulative_phase_mass_repair_fraction": cumulative_repair / initial_volume,
            "maximum_single_step_phase_clip_fraction": (
                float(np.max(per_step_clip)) / initial_volume
                if per_step_clip.size
                else 0.0
            ),
            "maximum_single_step_phase_mass_repair_fraction": (
                float(np.max(per_step_repair)) / initial_volume
                if per_step_repair.size
                else 0.0
            ),
            "maximum_projected_divergence_s-1": max_divergence,
            "maximum_dimensionless_projected_divergence": dimensionless_divergence,
            "maximum_pressure_relative_residual": float(
                diagnostics["maximum_pressure_relative_residual"]
            ),
            "maximum_advective_cfl": float(diagnostics["maximum_advective_cfl"]),
            "maximum_viscous_cfl": float(
                np.max(result.observables["cfl_viscous"])
            ),
            "maximum_capillary_cfl": float(
                np.max(result.observables["cfl_capillary"])
            ),
            "maximum_rotation_increment_rad": float(
                diagnostics["maximum_rotation_increment_rad"]
            ),
            "connection_candidate_observed": bool(
                diagnostics["connection_candidate_observed"]
            ),
            "metadata": result.metadata,
            "events": [event.to_dict() for event in result.events],
            "array_summaries": array_summaries,
        }
        write_json(case_output / "summary.json", summary)
        return 0
    except Exception as exc:  # evidence must survive an isolated-case failure
        failure = {
            "schema": SCHEMA,
            "case_id": case.get("id", "unknown"),
            "started_utc": started,
            "failed_utc": datetime.now(timezone.utc).isoformat(),
            "wall_time_s": time.perf_counter() - wall_start,
            "exception_type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
        write_json(case_output / "failure.json", failure)
        return 1


def _safe_relative_l2(
    difference: np.ndarray,
    references: Sequence[np.ndarray],
    weights: np.ndarray | None = None,
) -> float:
    diff = np.ravel(difference)
    if weights is None:
        numerator = float(np.linalg.norm(diff))
        norms = [float(np.linalg.norm(np.ravel(reference))) for reference in references]
    else:
        weight = np.ravel(weights)
        if weight.shape != diff.shape or np.any(weight < 0.0):
            raise ValueError("weighted relative L2 received incompatible weights")
        numerator = math.sqrt(float(np.dot(weight, np.square(diff))))
        norms = [
            math.sqrt(float(np.dot(weight, np.square(np.ravel(reference)))))
            for reference in references
        ]
    denominator = max(*norms, TINY)
    return numerator / denominator


def load_final_field(case_dir: Path) -> dict[str, np.ndarray]:
    rows = read_rows(case_dir / "final_field.csv")
    columns = rows[0].keys() if rows else []
    return {
        name: np.asarray([float(row[name]) for row in rows], dtype=np.float64)
        for name in columns
        if name not in {"radial_index", "axial_index"}
    }


def load_final_surface(case_dir: Path) -> tuple[np.ndarray, np.ndarray]:
    rows = read_rows(case_dir / "surface_profiles.csv")
    if not rows:
        raise ValueError(f"no surface profiles in {case_dir}")
    final_time = max(float(row["time_s"]) for row in rows)
    selected = [row for row in rows if float(row["time_s"]) == final_time]
    selected.sort(key=lambda row: int(row["radial_index"]))
    return (
        np.asarray([float(row["r_m"]) for row in selected]),
        np.asarray([float(row["surface_z_m"]) for row in selected]),
    )


def gate_row(case_id: str, gate: str, value: Any, limit: Any, passed: bool) -> dict[str, Any]:
    return {
        "scope": case_id,
        "gate": gate,
        "value": value,
        "limit": limit,
        "status": "PASS" if passed else "FAIL",
    }


def evaluate_case_gates(
    case: Mapping[str, Any], summary: Mapping[str, Any], protocol: Mapping[str, Any]
) -> list[dict[str, Any]]:
    limits = protocol["acceptance"]["common_hard_gates"]
    expected_termination = case.get("expected_termination_reason")
    backend_completed = summary["status"] == "completed"
    configured_duration_reached = bool(summary["configured_duration_reached"])
    termination_reason = str(summary["termination_reason"])
    if expected_termination is None:
        completion_passed = (
            backend_completed
            and configured_duration_reached
            and termination_reason == "configured_duration_reached"
        )
        completion_actual: Any = {
            "backend_status": summary["status"],
            "requested_duration_s": summary["requested_duration_s"],
            "actual_end_time_s": summary["actual_end_time_s"],
            "termination_reason": termination_reason,
        }
        completion_expected: Any = {
            "backend_status": "completed",
            "configured_duration_reached": True,
            "termination_reason": "configured_duration_reached",
        }
        completion_gate_name = "completed_status_and_configured_duration_reached"
    else:
        completion_passed = (
            backend_completed
            and not configured_duration_reached
            and termination_reason == expected_termination
            and bool(summary["domain_limited"])
        )
        completion_actual = {
            "backend_status": summary["status"],
            "requested_duration_s": summary["requested_duration_s"],
            "actual_end_time_s": summary["actual_end_time_s"],
            "termination_reason": termination_reason,
            "domain_limited": summary["domain_limited"],
        }
        completion_expected = {
            "status": "DOMAIN_LIMIT_EXPECTED",
            "termination_reason": expected_termination,
            "configured_duration_reached": False,
        }
        completion_gate_name = "expected_model_domain_limit"
    rows = [
        gate_row(case["id"], "source_unchanged_during_case", summary["source_unchanged_during_case"], True, bool(summary["source_unchanged_during_case"])),
        gate_row(
            case["id"],
            completion_gate_name,
            completion_actual,
            completion_expected,
            completion_passed,
        ),
        gate_row(case["id"], "finite_outputs", summary["all_outputs_finite"], True, bool(summary["all_outputs_finite"])),
        gate_row(case["id"], "phase_minimum", summary["phase_minimum"], -limits["phase_bound_tolerance"], summary["phase_minimum"] >= -limits["phase_bound_tolerance"]),
        gate_row(case["id"], "phase_maximum", summary["phase_maximum"], 1.0 + limits["phase_bound_tolerance"], summary["phase_maximum"] <= 1.0 + limits["phase_bound_tolerance"]),
        gate_row(case["id"], "relative_liquid_volume_error", summary["maximum_relative_liquid_volume_error"], limits["maximum_relative_liquid_volume_error"], summary["maximum_relative_liquid_volume_error"] <= limits["maximum_relative_liquid_volume_error"]),
        gate_row(case["id"], "advective_cfl", summary["maximum_advective_cfl"], limits["maximum_advective_cfl_fraction_of_configured_limit"] * summary["configured_cfl_limit"], summary["maximum_advective_cfl"] <= limits["maximum_advective_cfl_fraction_of_configured_limit"] * summary["configured_cfl_limit"]),
        gate_row(case["id"], "viscous_cfl", summary["maximum_viscous_cfl"], limits["maximum_viscous_cfl"], summary["maximum_viscous_cfl"] <= limits["maximum_viscous_cfl"]),
        gate_row(case["id"], "capillary_cfl", summary["maximum_capillary_cfl"], limits["maximum_capillary_cfl"], summary["maximum_capillary_cfl"] <= limits["maximum_capillary_cfl"]),
        gate_row(case["id"], "rotation_increment", summary["maximum_rotation_increment_rad"], limits["maximum_rotation_increment_rad"], summary["maximum_rotation_increment_rad"] <= limits["maximum_rotation_increment_rad"]),
        gate_row(case["id"], "pressure_relative_residual", summary["maximum_pressure_relative_residual"], limits["maximum_pressure_relative_residual"], summary["maximum_pressure_relative_residual"] <= limits["maximum_pressure_relative_residual"]),
        gate_row(case["id"], "dimensionless_projected_divergence", summary["maximum_dimensionless_projected_divergence"], limits["maximum_dimensionless_projected_divergence"], summary["maximum_dimensionless_projected_divergence"] <= limits["maximum_dimensionless_projected_divergence"]),
    ]
    if "static" in case["tags"]:
        static = protocol["acceptance"]["static_hard_gates"]
        rows.extend(
            [
                gate_row(case["id"], "static_maximum_speed_m_s", summary["maximum_speed_m_s"], static["maximum_speed_m_s"], summary["maximum_speed_m_s"] <= static["maximum_speed_m_s"]),
                gate_row(case["id"], "static_maximum_funnel_depth_m", summary["maximum_funnel_depth_m"], static["maximum_funnel_depth_m"], summary["maximum_funnel_depth_m"] <= static["maximum_funnel_depth_m"]),
                gate_row(case["id"], "static_initial_final_phase_linf", summary["initial_final_phase_linf"], static["initial_final_phase_linf"], summary["initial_final_phase_linf"] <= static["initial_final_phase_linf"]),
            ]
        )
    interventions = protocol["v3_amendment"]["amendments"][
        "numerical_intervention_gates"
    ]
    single_repair_limit = interventions[
        "maximum_single_step_mass_repair_fraction_hard"
    ]
    cumulative_repair_limit = interventions[
        "maximum_cumulative_mass_repair_fraction_hard"
    ]
    rows.extend(
        [
            gate_row(
                case["id"],
                "maximum_single_step_mass_repair_fraction",
                summary["maximum_single_step_phase_mass_repair_fraction"],
                single_repair_limit,
                summary["maximum_single_step_phase_mass_repair_fraction"]
                <= single_repair_limit,
            ),
            gate_row(
                case["id"],
                "cumulative_mass_repair_fraction",
                summary["cumulative_phase_mass_repair_fraction"],
                cumulative_repair_limit,
                summary["cumulative_phase_mass_repair_fraction"]
                <= cumulative_repair_limit,
            ),
        ]
    )
    clip_limits = protocol["v4_amendment"]["amendments"][
        "numerical_intervention_gates"
    ]
    for gate_name, value_name, limit_name in (
        (
            "maximum_single_step_absolute_clip_fraction",
            "maximum_single_step_phase_clip_fraction",
            "maximum_single_step_absolute_clip_fraction_hard",
        ),
        (
            "cumulative_absolute_clip_fraction",
            "cumulative_phase_clip_fraction",
            "maximum_cumulative_absolute_clip_fraction_hard",
        ),
    ):
        value = summary[value_name]
        limit = clip_limits[limit_name]
        rows.append(
            gate_row(case["id"], gate_name, value, limit, value <= limit)
        )
    backend_quality = summary["metadata"]["diagnostics"]["numerical_quality_gate"]
    for backend_name, protocol_value in (
        (
            "maximum_single_step_phase_clip_relative",
            clip_limits["maximum_single_step_absolute_clip_fraction_hard"],
        ),
        (
            "maximum_cumulative_phase_clip_relative",
            clip_limits["maximum_cumulative_absolute_clip_fraction_hard"],
        ),
        (
            "maximum_single_step_mass_repair_relative",
            single_repair_limit,
        ),
        (
            "maximum_cumulative_mass_repair_relative",
            cumulative_repair_limit,
        ),
    ):
        backend_value = float(backend_quality[backend_name])
        rows.append(
            gate_row(
                case["id"],
                f"backend_constant_matches_protocol:{backend_name}",
                backend_value,
                protocol_value,
                math.isclose(
                    backend_value,
                    protocol_value,
                    rel_tol=0.0,
                    abs_tol=0.0,
                ),
            )
        )
    return rows


def synthetic_throat_tests(output_dir: Path) -> list[dict[str, Any]]:
    from airvortex.numerics import AxisymmetricGrid, gas_connection_diagnostics

    grid = AxisymmetricGrid(24, 36, 0.045, 0.075)
    bar_half = 0.015
    bar_top = 0.0075
    interface_thickness = 2.5 * min(grid.dr, grid.dz)
    required_radius = max(
        2.0 * interface_thickness, 4.0 * max(grid.dr, grid.dz)
    )
    required_cells = int(math.ceil(required_radius / grid.dr - 1.0e-14))
    axial_target = grid.z_centres <= bar_top + grid.dz
    start = int(np.flatnonzero(axial_target)[-1])
    tests: list[tuple[str, np.ndarray, bool, bool, int]] = []

    base = np.ones(grid.shape, dtype=np.float64)
    tests.append(("zero_width", base.copy(), False, False, 0))
    widths = (
        ("one_cell_precursor", 1, False),
        ("one_below_required", required_cells - 1, False),
        ("exactly_required", required_cells, True),
        ("one_above_required", required_cells + 1, True),
    )
    for name, width, expected_resolved in widths:
        phase = base.copy()
        phase[:width, start:] = 0.0
        tests.append((name, phase, True, expected_resolved, width))
    bottleneck = base.copy()
    bottleneck[: required_cells + 1, start:] = 0.0
    bottleneck[required_cells - 1 :, start + 2] = 1.0
    tests.append(
        (
            "wide_with_one_below_required_bottleneck",
            bottleneck,
            True,
            False,
            required_cells - 1,
        )
    )
    disconnected = base.copy()
    disconnected[:required_cells, start:] = 0.0
    disconnected[:required_cells, start + 2] = 1.0
    tests.append(("required_width_disconnected", disconnected, False, False, 0))

    rows: list[dict[str, Any]] = []
    for name, phase, expected_precursor, expected_resolved, expected_throat in tests:
        measured = gas_connection_diagnostics(
            phase,
            grid,
            bar_half,
            bar_top,
            interface_thickness_m=interface_thickness,
        )
        passed = (
            measured.one_cell_precursor == expected_precursor
            and measured.resolved_candidate == expected_resolved
            and measured.minimum_throat_cells == expected_throat
        )
        rows.append(
            {
                "test": name,
                "expected_precursor": expected_precursor,
                "actual_precursor": measured.one_cell_precursor,
                "expected_resolved": expected_resolved,
                "actual_resolved": measured.resolved_candidate,
                "expected_throat_cells": expected_throat,
                "actual_throat_cells": measured.minimum_throat_cells,
                "required_throat_cells": measured.required_throat_cells,
                "expected_required_throat_cells": required_cells,
                "required_throat_radius_m": measured.required_throat_radius_m,
                "interface_thickness_m": interface_thickness,
                "minimum_throat_radius_m": measured.minimum_throat_radius_m,
                "status": "PASS" if passed else "FAIL",
            }
        )
    write_rows(output_dir / "synthetic_throat_tests.csv", list(rows[0]), rows)
    write_json(output_dir / "synthetic_throat_tests.json", rows)
    return rows


def quality_gate_poison_tests(output_dir: Path) -> list[dict[str, Any]]:
    """Inject diagnostic-only poison to prove clip/repair gates fail closed.

    The physical state is advanced by the real H1 step.  A subclass changes
    only the returned intervention diagnostics, allowing cancellation and
    clip-only counterexamples that ordinary stable cases need not encounter.
    """

    from airvortex.backends.high_fidelity import (
        HighFidelityBackend,
        MAX_CUMULATIVE_MASS_REPAIR_RELATIVE,
        MAX_CUMULATIVE_PHASE_CLIP_RELATIVE,
        MAX_SINGLE_STEP_MASS_REPAIR_RELATIVE,
        MAX_SINGLE_STEP_PHASE_CLIP_RELATIVE,
    )

    protocol = load_protocol()
    template_case = next(
        case for case in protocol["hf_cases"] if case["id"] == "hf_static_16x24_short"
    )
    template = make_hf_config(template_case)
    write_json(output_dir / "poison_test_config.json", template.to_dict())

    class PoisonBackend(HighFidelityBackend):
        def __init__(
            self,
            *,
            clip_absolute_fraction: float = 0.0,
            clip_signed_fraction: float = 0.0,
            repair_absolute_fraction: float = 0.0,
            repair_signed_fraction: float = 0.0,
        ) -> None:
            super().__init__(cls_compression_interval=0)
            self._clip_absolute_fraction = clip_absolute_fraction
            self._clip_signed_fraction = clip_signed_fraction
            self._repair_absolute_fraction = repair_absolute_fraction
            self._repair_signed_fraction = repair_signed_fraction

        def advance_one_step(self, *args: Any, initial_liquid_volume: float, **kwargs: Any):
            diagnostics = super().advance_one_step(
                *args,
                initial_liquid_volume=initial_liquid_volume,
                **kwargs,
            )
            return replace(
                diagnostics,
                phase_clip_signed_volume_m3=(
                    self._clip_signed_fraction * initial_liquid_volume
                ),
                phase_clip_absolute_volume_m3=(
                    self._clip_absolute_fraction * initial_liquid_volume
                ),
                phase_mass_repair_signed_volume_m3=(
                    self._repair_signed_fraction * initial_liquid_volume
                ),
                phase_mass_repair_absolute_volume_m3=(
                    self._repair_absolute_fraction * initial_liquid_volume
                ),
            )

    definitions = [
        {
            "test": "below_limit_control",
            "duration_s": 0.001,
            "clip_absolute_fraction_per_step": 0.25e-8,
            "clip_signed_fraction_per_step": 0.0,
            "repair_absolute_fraction_per_step": 0.0,
            "repair_signed_fraction_per_step": 0.0,
            "expected_exception_substring": None,
        },
        {
            "test": "clip_only_single_step_poison",
            "duration_s": 0.001,
            "clip_absolute_fraction_per_step": 2.0e-8,
            "clip_signed_fraction_per_step": 2.0e-8,
            "repair_absolute_fraction_per_step": 0.0,
            "repair_signed_fraction_per_step": 0.0,
            "expected_exception_substring": "phase clipping numerical quality gate failed",
        },
        {
            "test": "positive_negative_clip_cancellation_poison",
            "duration_s": 0.001,
            "clip_absolute_fraction_per_step": 2.0e-8,
            "clip_signed_fraction_per_step": 0.0,
            "repair_absolute_fraction_per_step": 0.0,
            "repair_signed_fraction_per_step": 0.0,
            "expected_exception_substring": "phase clipping numerical quality gate failed",
        },
        {
            "test": "cumulative_clip_poison",
            "duration_s": 0.1,
            "clip_absolute_fraction_per_step": 0.75e-8,
            "clip_signed_fraction_per_step": 0.0,
            "repair_absolute_fraction_per_step": 0.0,
            "repair_signed_fraction_per_step": 0.0,
            "expected_exception_substring": "phase clipping numerical quality gate failed",
        },
        {
            "test": "repair_only_single_step_poison",
            "duration_s": 0.001,
            "clip_absolute_fraction_per_step": 0.0,
            "clip_signed_fraction_per_step": 0.0,
            "repair_absolute_fraction_per_step": 2.0e-8,
            "repair_signed_fraction_per_step": 0.0,
            "expected_exception_substring": "phase mass-repair numerical quality gate failed",
        },
    ]
    rows: list[dict[str, Any]] = []
    for definition in definitions:
        config = replace(
            template,
            run=replace(
                template.run,
                duration_s=definition["duration_s"],
                output_interval_s=min(0.001, definition["duration_s"]),
            ),
            drive=replace(
                template.drive,
                initial_rpm=0.0,
                target_rpm=0.0,
                ramp_duration_s=0.0,
            ),
        )
        backend = PoisonBackend(
            clip_absolute_fraction=definition[
                "clip_absolute_fraction_per_step"
            ],
            clip_signed_fraction=definition["clip_signed_fraction_per_step"],
            repair_absolute_fraction=definition[
                "repair_absolute_fraction_per_step"
            ],
            repair_signed_fraction=definition["repair_signed_fraction_per_step"],
        )
        expected = definition["expected_exception_substring"]
        actual_exception = None
        actual_traceback = None
        completed = False
        try:
            backend.run(config)
            completed = True
        except Exception as exc:
            actual_exception = f"{type(exc).__name__}: {exc}"
            actual_traceback = traceback.format_exc()
        passed = (
            (expected is None and completed)
            or (
                expected is not None
                and not completed
                and actual_exception is not None
                and expected in actual_exception
            )
        )
        rows.append(
            {
                **definition,
                "completed": completed,
                "actual_exception": actual_exception,
                "actual_traceback": actual_traceback,
                "backend_constants": {
                    "single_clip": MAX_SINGLE_STEP_PHASE_CLIP_RELATIVE,
                    "cumulative_clip": MAX_CUMULATIVE_PHASE_CLIP_RELATIVE,
                    "single_repair": MAX_SINGLE_STEP_MASS_REPAIR_RELATIVE,
                    "cumulative_repair": MAX_CUMULATIVE_MASS_REPAIR_RELATIVE,
                },
                "status": "PASS" if passed else "FAIL",
            }
        )
    csv_rows = [
        {key: value for key, value in row.items() if key not in {"actual_traceback", "backend_constants"}}
        for row in rows
    ]
    write_rows(output_dir / "quality_gate_poison_tests.csv", list(csv_rows[0]), csv_rows)
    write_json(output_dir / "quality_gate_poison_tests.json", rows)
    return rows


def comparison_row(group: str, metric: str, value: Any, status: str, note: str = "") -> dict[str, Any]:
    return {
        "group": group,
        "metric": metric,
        "value": value,
        "status": status,
        "note": note,
    }


def compare_reversal(cases_dir: Path, protocol: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    positive = load_final_field(cases_dir / "hf_reversal_positive")
    negative = load_final_field(cases_dir / "hf_reversal_negative")
    _, surface_pos = load_final_surface(cases_dir / "hf_reversal_positive")
    radius, surface_neg = load_final_surface(cases_dir / "hf_reversal_negative")
    field_weights = positive["r_m"]
    phase_rel = _safe_relative_l2(
        positive["phase_liquid_fraction"] - negative["phase_liquid_fraction"],
        [positive["phase_liquid_fraction"], negative["phase_liquid_fraction"]],
        field_weights,
    )
    surface_weights = radius
    pos_surface = surface_pos - np.dot(surface_weights, surface_pos) / np.sum(surface_weights)
    neg_surface = surface_neg - np.dot(surface_weights, surface_neg) / np.sum(surface_weights)
    surface_rel = _safe_relative_l2(
        pos_surface - neg_surface,
        [pos_surface, neg_surface],
        surface_weights,
    )
    meridional_pos = np.concatenate([positive["u_r_m_s"], positive["u_z_m_s"]])
    meridional_neg = np.concatenate([negative["u_r_m_s"], negative["u_z_m_s"]])
    meridional_weights = np.concatenate([field_weights, field_weights])
    meridional_rel = _safe_relative_l2(
        meridional_pos - meridional_neg,
        [meridional_pos, meridional_neg],
        meridional_weights,
    )
    swirl_rel = _safe_relative_l2(
        positive["u_theta_m_s"] + negative["u_theta_m_s"],
        [positive["u_theta_m_s"], negative["u_theta_m_s"]],
        field_weights,
    )
    values = {
        "phase_relative_l2": phase_rel,
        "surface_relative_l2": surface_rel,
        "meridional_velocity_symmetry_relative_l2": meridional_rel,
        "azimuthal_velocity_antisymmetry_relative_l2": swirl_rel,
    }
    limits = protocol["acceptance"]["reversal_hard_gates"]
    comparisons = [
        comparison_row(
            "rotation_reversal",
            name,
            value,
            "PASS" if value <= limits[name] else "FAIL",
            "轴对称方程的奇偶对称；通过不代表真实三维装置无旋向效应",
        )
        for name, value in values.items()
    ]
    gates = [
        gate_row("rotation_reversal", name, value, limits[name], value <= limits[name])
        for name, value in values.items()
    ]
    return comparisons, gates


def interpolated_surface(case_dir: Path, x_common: np.ndarray) -> np.ndarray:
    radius, surface = load_final_surface(case_dir)
    config = read_json(case_dir / "config.json")
    vessel_radius = float(config["container"]["radius_m"])
    x = radius / vessel_radius
    area_mean = float(np.dot(radius, surface) / np.sum(radius))
    centred = surface - area_mean
    # Cell centres do not lie on either boundary.  Constant extension follows
    # the axis symmetry and H1 preview's wall-Neumann convention without
    # stretching a grid-dependent last centre to x=1.
    extended_x = np.concatenate(([0.0], x, [1.0]))
    extended_surface = np.concatenate(([centred[0]], centred, [centred[-1]]))
    return np.interp(x_common, extended_x, extended_surface)


def axisymmetric_profile_rms(profile: np.ndarray, x: np.ndarray) -> float:
    return math.sqrt(max(0.0, 2.0 * float(trapezoid(np.square(profile) * x, x=x))))


def convergence_group(
    cases_dir: Path,
    group: str,
    ids: Sequence[str],
    refinement_ratio: float,
    summaries: Mapping[str, Mapping[str, Any]],
    axis: str,
) -> list[dict[str, Any]]:
    x_common = np.linspace(0.0, 1.0, 1024)
    profiles = [interpolated_surface(cases_dir / case_id, x_common) for case_id in ids]
    errors = [
        axisymmetric_profile_rms(profiles[i] - profiles[i + 1], x_common)
        for i in range(len(profiles) - 1)
    ]
    rows: list[dict[str, Any]] = []
    for index, error in enumerate(errors):
        rows.append(
            comparison_row(
                group,
                f"surface_rms_{ids[index]}_vs_{ids[index + 1]}_m",
                error,
                "RECORDED",
            )
        )
    if len(errors) >= 2:
        if max(errors) <= 128.0 * np.finfo(float).eps:
            status = "ROUND_OFF_LIMITED"
            order = None
        elif all(errors[index + 1] < errors[index] for index in range(len(errors) - 1)):
            status = "CONVERGENCE_TREND"
            order = math.log(errors[-2] / errors[-1]) / math.log(refinement_ratio)
        else:
            status = "NOT_DEMONSTRATED"
            order = None
        if axis == "dt":
            effective = [float(summaries[case_id]["average_effective_dt_s"]) for case_id in ids]
            ratios = [effective[i] / effective[i + 1] for i in range(len(effective) - 1)]
            if any(abs(ratio - refinement_ratio) > 0.15 * refinement_ratio for ratio in ratios):
                status = "INCONCLUSIVE_ADAPTIVE_LIMIT"
            rows.append(comparison_row(group, "average_effective_dt_s", json.dumps(effective), "RECORDED"))
        rows.append(
            comparison_row(
                group,
                "observed_order_from_last_three",
                order if order is not None else "",
                status,
                "离散差下降只是数值趋势，不是物理验证",
            )
        )
    for case_id in ids:
        rows.append(
            comparison_row(
                group,
                f"final_funnel_depth_m:{case_id}",
                summaries[case_id]["final_funnel_depth_m"],
                "RECORDED",
            )
        )
    # A profile norm can decrease while a decision-facing scalar oscillates.
    # Archive and judge the funnel-depth sequence separately so a low-order
    # profile trend cannot make a non-monotone threshold observable disappear
    # from ``negative_results.json``.
    depths = [float(summaries[case_id]["final_funnel_depth_m"]) for case_id in ids]
    if len(depths) >= 3:
        depth_steps = np.diff(np.asarray(depths, dtype=np.float64))
        nonzero = np.abs(depth_steps) > 128.0 * np.finfo(float).eps * max(
            1.0, max(abs(value) for value in depths)
        )
        nonzero_steps = depth_steps[nonzero]
        same_direction = (
            nonzero_steps.size > 0
            and (np.all(nonzero_steps > 0.0) or np.all(nonzero_steps < 0.0))
        )
        decreasing_changes = all(
            abs(depth_steps[index + 1]) < abs(depth_steps[index])
            for index in range(len(depth_steps) - 1)
        )
        scalar_status = (
            "CONVERGENCE_TREND"
            if same_direction and decreasing_changes
            else "NOT_DEMONSTRATED"
        )
        rows.append(
            comparison_row(
                group,
                "funnel_depth_m_monotonic_convergence",
                json.dumps(depths, separators=(",", ":")),
                scalar_status,
                (
                    "主判据标量必须单调趋近且相邻改变量递减；"
                    "剖面范数下降不能覆盖标量振荡"
                ),
            )
        )
    return rows


def interface_sensitivity(
    cases_dir: Path, ids: Sequence[str], summaries: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    base_id = "hf_interface_base"
    x_common = np.linspace(0.0, 1.0, 1024)
    base_profile = interpolated_surface(cases_dir / base_id, x_common)
    base_depth = float(summaries[base_id]["final_funnel_depth_m"])
    rows: list[dict[str, Any]] = []
    for case_id in ids:
        if case_id == base_id:
            continue
        profile = interpolated_surface(cases_dir / case_id, x_common)
        rms = axisymmetric_profile_rms(profile - base_profile, x_common)
        depth = float(summaries[case_id]["final_funnel_depth_m"])
        relative_depth = abs(depth - base_depth) / max(abs(base_depth), TINY)
        rows.extend(
            [
                comparison_row(
                    "interface_sensitivity",
                    f"surface_rms_vs_base_m:{case_id}",
                    rms,
                    "SENSITIVITY_RECORDED_NO_PHYSICAL_TOLERANCE",
                ),
                comparison_row(
                    "interface_sensitivity",
                    f"relative_final_depth_change:{case_id}",
                    relative_depth,
                    "SENSITIVITY_RECORDED_NO_PHYSICAL_TOLERANCE",
                ),
            ]
        )
    return rows


def exact_centrifugal_potential(radius: np.ndarray, core_radius: float) -> np.ndarray:
    """Closed independent primitive of integral (u_theta/C)^2/r dr."""

    r = np.asarray(radius, dtype=np.float64)
    x = np.square(r / core_radius)
    value = np.zeros_like(x)
    positive = x > 0.0
    xp = x[positive]
    integral_x = (
        2.0 * (math.log(2.0) + expi(-xp) - expi(-2.0 * xp))
        - np.square(-np.expm1(-xp)) / xp
    )
    value[positive] = integral_x / (2.0 * core_radius**2)
    return value


def nonlinear_surface_energy_solution(
    config: Any,
    burgers_c_m2_s: float,
    *,
    points: int = 257,
    initial: np.ndarray | None = None,
) -> dict[str, Any]:
    """Independent exact-curvature graph solution by constrained energy minimisation."""

    from airvortex.backends.forward import solve_unit_surface_response

    cfg = replace(config, forward=replace(config.forward, surface_radial_points=points))
    radius = np.linspace(0.0, cfg.container.radius_m, points)
    dr = float(radius[1] - radius[0])
    radial_weights = radius * dr
    radial_weights[-1] *= 0.5
    weight_sum = float(np.sum(radial_weights))
    potential = exact_centrifugal_potential(radius, cfg.forward.effective_core_radius_m)
    rho = cfg.fluid.liquid_density_kg_m3
    gravity = cfg.fluid.gravity_m_s2
    sigma = cfg.fluid.surface_tension_n_m
    r_mid = 0.5 * (radius[:-1] + radius[1:])

    if initial is None:
        unit = solve_unit_surface_response(cfg)
        x0 = burgers_c_m2_s**2 * unit.surface_per_circulation_sq
    else:
        x0 = np.asarray(initial, dtype=np.float64).copy()
        if x0.shape != radius.shape:
            raise ValueError("nonlinear initial profile has wrong shape")

    def project(x: np.ndarray) -> np.ndarray:
        return x - np.dot(radial_weights, x) / weight_sum

    x0 = project(x0)

    def raw_energy_gradient(x: np.ndarray) -> tuple[float, np.ndarray]:
        delta = project(x)
        slopes = np.diff(delta) / dr
        bulk_density = (
            0.5 * rho * gravity * np.square(delta)
            - rho * burgers_c_m2_s**2 * potential * delta
        )
        energy = 2.0 * math.pi * (
            float(np.dot(radial_weights, bulk_density))
            + sigma
            * float(
                np.sum(r_mid * dr * (np.sqrt(1.0 + np.square(slopes)) - 1.0))
            )
        )
        gradient_delta = 2.0 * math.pi * radial_weights * (
            rho * gravity * delta - rho * burgers_c_m2_s**2 * potential
        )
        surface_flux = 2.0 * math.pi * sigma * r_mid * slopes / np.sqrt(
            1.0 + np.square(slopes)
        )
        gradient_delta[:-1] -= surface_flux
        gradient_delta[1:] += surface_flux
        gradient = gradient_delta - radial_weights * (
            float(np.sum(gradient_delta)) / weight_sum
        )
        return energy, gradient

    initial_energy, _ = raw_energy_gradient(x0)
    depth_scale = max(float(np.ptp(x0)), 1.0e-4)
    energy_scale = max(
        abs(initial_energy),
        rho * gravity * math.pi * cfg.container.radius_m**2 * depth_scale**2,
        1.0e-12,
    )

    def objective(x: np.ndarray) -> tuple[float, np.ndarray]:
        energy, gradient = raw_energy_gradient(x)
        return energy / energy_scale, gradient / energy_scale

    result = minimize(
        objective,
        x0,
        method="L-BFGS-B",
        jac=True,
        options={"maxiter": 4000, "ftol": 1.0e-15, "gtol": 1.0e-9, "maxls": 80},
    )
    delta = project(np.asarray(result.x, dtype=np.float64))
    _, final_gradient = raw_energy_gradient(delta)
    slopes = np.diff(delta) / dr
    return {
        "radius_m": radius,
        "surface_perturbation_m": delta,
        "success": bool(result.success),
        "status": int(result.status),
        "message": str(result.message),
        "iterations": int(result.nit),
        "function_evaluations": int(result.nfev),
        "energy_j": float(raw_energy_gradient(delta)[0]),
        "projected_gradient_infinity": float(np.max(np.abs(final_gradient))) / energy_scale,
        "radial_volume_mean_m": float(np.dot(radial_weights, delta) / weight_sum),
        "maximum_absolute_slope": float(np.max(np.abs(slopes))),
        "wall_last_segment_slope": float(slopes[-1]),
        "funnel_depth_m": float(np.ptp(delta)),
        "minimum_surface_z_m": float(cfg.container.liquid_depth_m + np.min(delta)),
    }


def make_forward_config(
    rpm: float,
    *,
    initial_rpm: float | None = None,
    direction: int = 1,
    duration_s: float = 0.4,
    output_interval_s: float = 0.05,
    radial_points: int = 256,
    sigma: float = 0.072,
    radius_m: float = 0.045,
    core_radius_m: float = 0.010,
    integration_dt_s: float = 0.001,
):
    from airvortex import SimulationConfig

    raw = read_json(ROOT / "configs" / "forward_default.json")
    raw["backend"] = "forward"
    raw["container"]["radius_m"] = radius_m
    raw["fluid"]["surface_tension_n_m"] = sigma
    raw["drive"].update(
        {
            "initial_rpm": rpm if initial_rpm is None else initial_rpm,
            "target_rpm": rpm,
            "ramp_duration_s": 0.0,
            "rotation_direction": direction,
        }
    )
    raw["run"].update(
        {"duration_s": duration_s, "output_interval_s": output_interval_s}
    )
    raw["forward"].update(
        {
            "surface_radial_points": radial_points,
            "effective_core_radius_m": core_radius_m,
            "integration_time_step_s": integration_dt_s,
        }
    )
    return SimulationConfig.from_dict(raw)


def run_forward_validation(output_dir: Path) -> dict[str, Any]:
    from airvortex.backends.forward import (
        ForwardBackend,
        critical_actual_rpm,
        solve_unit_surface_response,
        stir_bar_top_height_m,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    backend = ForwardBackend()
    started = time.perf_counter()
    metrics: dict[str, Any] = {}
    gates: list[dict[str, Any]] = []

    zero_cfg = make_forward_config(0.0)
    zero = backend.run(zero_cfg)
    zero_flat_error = float(
        np.max(np.abs(zero.observables["surface_profile_m"] - zero_cfg.container.liquid_depth_m))
    )
    metrics["zero_speed_flat_error_m"] = zero_flat_error
    gates.append(gate_row("forward", "zero_speed_flat_error_m", zero_flat_error, 2.0e-12, zero_flat_error <= 2.0e-12))

    base_cfg = make_forward_config(850.0)
    unit = solve_unit_surface_response(base_cfg)
    volume_mean = float(np.dot(unit.volume_weights, unit.surface_per_circulation_sq))
    metrics["unit_response_discrete_volume_mean"] = volume_mean
    gates.append(gate_row("forward", "unit_response_volume_mean", abs(volume_mean), 1.0e-12, abs(volume_mean) <= 1.0e-12))

    low = backend.run(make_forward_config(350.0))
    high = backend.run(make_forward_config(700.0))
    omega_squared_ratio_error = abs(
        float(high.observables["funnel_depth_m"][-1])
        / float(low.observables["funnel_depth_m"][-1])
        - 4.0
    )
    metrics["omega_squared_depth_ratio_error"] = omega_squared_ratio_error
    gates.append(gate_row("forward", "omega_squared_depth_ratio_error", omega_squared_ratio_error, 1.0e-10, omega_squared_ratio_error <= 1.0e-10))

    pos = backend.run(make_forward_config(720.0, direction=1))
    neg = backend.run(make_forward_config(720.0, direction=-1))
    reversal_surface = _safe_relative_l2(
        pos.observables["surface_profile_m"] - neg.observables["surface_profile_m"],
        [pos.observables["surface_profile_m"], neg.observables["surface_profile_m"]],
    )
    reversal_c = _safe_relative_l2(
        pos.observables["burgers_C_m2_s"] + neg.observables["burgers_C_m2_s"],
        [pos.observables["burgers_C_m2_s"], neg.observables["burgers_C_m2_s"]],
    )
    metrics["rotation_reversal_surface_relative_l2"] = reversal_surface
    metrics["rotation_reversal_C_antisymmetry_relative_l2"] = reversal_c
    gates.extend(
        [
            gate_row("forward", "rotation_reversal_surface_relative_l2", reversal_surface, 1.0e-10, reversal_surface <= 1.0e-10),
            gate_row("forward", "rotation_reversal_C_antisymmetry_relative_l2", reversal_c, 1.0e-10, reversal_c <= 1.0e-10),
        ]
    )

    step_cfg = make_forward_config(
        600.0,
        initial_rpm=0.0,
        duration_s=0.8,
        output_interval_s=0.05,
        integration_dt_s=0.001,
    )
    step = backend.run(step_cfg)
    omega_target = 600.0 * 2.0 * math.pi / 60.0
    c_target = step_cfg.forward.circulation_coupling_m2 * omega_target
    expected_c = c_target * (1.0 - np.exp(-step.time_s / step_cfg.forward.spin_up_time_s))
    step_response_error = _safe_relative_l2(
        step.observables["burgers_C_m2_s"] - expected_c,
        [expected_c],
    )
    metrics["circulation_step_response_relative_l2"] = step_response_error
    gates.append(gate_row("forward", "circulation_step_response_relative_l2", step_response_error, 1.0e-10, step_response_error <= 1.0e-10))

    analytic_cfg = make_forward_config(
        100.0,
        radial_points=256,
        sigma=1.0e-10,
        radius_m=0.100,
        core_radius_m=0.005,
    )
    analytic_unit = solve_unit_surface_response(analytic_cfg)
    selected_indices = np.linspace(1, analytic_unit.radius_m.size - 1, 12, dtype=int)
    quadrature_rows: list[dict[str, Any]] = []
    maximum_quadrature_relative_error = 0.0
    c_core = analytic_cfg.forward.effective_core_radius_m
    for index in selected_indices:
        radius_value = float(analytic_unit.radius_m[index])

        def integrand(s: float) -> float:
            if s == 0.0:
                return 0.0
            return (-math.expm1(-(s / c_core) ** 2)) ** 2 / s**3

        independent, error_estimate = quad(
            integrand, 0.0, radius_value, epsabs=1.0e-10, epsrel=2.0e-12, limit=500
        )
        implemented = float(analytic_unit.potential_per_circulation_sq[index])
        relative = abs(implemented - independent) / max(abs(independent), TINY)
        maximum_quadrature_relative_error = max(maximum_quadrature_relative_error, relative)
        quadrature_rows.append(
            {
                "radial_index": int(index),
                "r_m": radius_value,
                "implemented_potential_per_C2": implemented,
                "independent_quad_potential_per_C2": independent,
                "quad_absolute_error_estimate": error_estimate,
                "relative_difference": relative,
            }
        )
    write_rows(output_dir / "independent_potential_quadrature.csv", list(quadrature_rows[0]), quadrature_rows)
    metrics["maximum_independent_quadrature_relative_error"] = maximum_quadrature_relative_error
    gates.append(gate_row("forward", "maximum_independent_quadrature_relative_error", maximum_quadrature_relative_error, 1.0e-8, maximum_quadrature_relative_error <= 1.0e-8))

    x = (analytic_cfg.container.radius_m / c_core) ** 2
    finite_closed = (
        -(-math.expm1(-x)) ** 2 / x
        + 2.0 * (expi(-x) - expi(-2.0 * x) + math.log(2.0))
    ) / (2.0 * c_core**2)
    finite_error = abs(
        float(analytic_unit.potential_per_circulation_sq[-1]) - finite_closed
    ) / finite_closed
    infinite_depth = math.log(2.0) / (
        analytic_cfg.fluid.gravity_m_s2 * c_core**2
    )
    numerical_depth = float(np.ptp(analytic_unit.surface_per_circulation_sq))
    infinite_error = abs(numerical_depth - infinite_depth) / infinite_depth
    metrics["finite_radius_closed_form_relative_error"] = finite_error
    metrics["large_radius_limit_relative_error"] = infinite_error
    gates.extend(
        [
            gate_row("forward", "finite_radius_closed_form_relative_error", finite_error, 1.0e-7, finite_error <= 1.0e-7),
            gate_row("forward", "large_radius_limit_relative_error", infinite_error, 0.003, infinite_error <= 0.003),
        ]
    )

    resolution_rows: list[dict[str, Any]] = []
    for points in (32, 64, 128, 256, 512):
        cfg = make_forward_config(850.0, radial_points=points)
        response = solve_unit_surface_response(cfg)
        threshold = critical_actual_rpm(cfg, response)
        critical_c = cfg.forward.circulation_coupling_m2 * threshold * 2.0 * math.pi / 60.0
        critical_surface = cfg.container.liquid_depth_m + critical_c**2 * response.surface_per_circulation_sq
        slopes = np.gradient(critical_surface, response.radius_m, edge_order=2)
        resolution_rows.append(
            {
                "radial_points": points,
                "critical_actual_rpm": threshold,
                "unit_depth_per_C2": float(np.ptp(response.surface_per_circulation_sq)),
                "volume_mean_per_C2": float(np.dot(response.volume_weights, response.surface_per_circulation_sq)),
                "critical_max_abs_slope": float(np.max(np.abs(slopes))),
            }
        )
    reference_threshold = resolution_rows[-1]["critical_actual_rpm"]
    for row in resolution_rows:
        row["critical_rpm_relative_to_512"] = abs(row["critical_actual_rpm"] - reference_threshold) / reference_threshold
    write_rows(output_dir / "forward_radial_resolution.csv", list(resolution_rows[0]), resolution_rows)
    threshold_errors = [row["critical_rpm_relative_to_512"] for row in resolution_rows[:-1]]
    radial_status = (
        "CONVERGENCE_TREND"
        if all(threshold_errors[i + 1] < threshold_errors[i] for i in range(len(threshold_errors) - 1))
        else "NOT_DEMONSTRATED"
    )
    metrics["radial_resolution_status"] = radial_status
    metrics["critical_rpm_512"] = reference_threshold
    metrics["critical_slope_512"] = resolution_rows[-1]["critical_max_abs_slope"]

    nonlinear_cfg = make_forward_config(1.0, radial_points=257)
    linear_critical = critical_actual_rpm(nonlinear_cfg)
    nonlinear_rows: list[dict[str, Any]] = []
    nonlinear_profile_rows: list[dict[str, Any]] = []
    previous: np.ndarray | None = None
    for ratio in (0.25, 0.50, 0.75, 0.90, 1.00):
        rpm = ratio * linear_critical
        burgers_c = nonlinear_cfg.forward.circulation_coupling_m2 * rpm * 2.0 * math.pi / 60.0
        solved = nonlinear_surface_energy_solution(
            nonlinear_cfg, burgers_c, points=257, initial=previous
        )
        previous = np.asarray(solved["surface_perturbation_m"])
        cfg_ratio = replace(
            nonlinear_cfg,
            drive=replace(
                nonlinear_cfg.drive, initial_rpm=rpm, target_rpm=rpm
            ),
        )
        linear_result = backend.run(cfg_ratio)
        linear_surface = np.asarray(linear_result.observables["surface_profile_m"][-1])
        nonlinear_surface = nonlinear_cfg.container.liquid_depth_m + previous
        linear_depth = float(np.ptp(linear_surface))
        nonlinear_depth = float(np.ptp(nonlinear_surface))
        rms = float(np.sqrt(np.mean(np.square(linear_surface - nonlinear_surface))))
        depth_relative = abs(linear_depth - nonlinear_depth) / max(nonlinear_depth, TINY)
        nonlinear_rows.append(
            {
                "linear_critical_fraction": ratio,
                "actual_bar_rpm": rpm,
                "burgers_C_m2_s": burgers_c,
                "optimizer_success": solved["success"],
                "optimizer_iterations": solved["iterations"],
                "projected_gradient_infinity_scaled": solved["projected_gradient_infinity"],
                "nonlinear_volume_mean_m": solved["radial_volume_mean_m"],
                "nonlinear_wall_last_segment_slope": solved["wall_last_segment_slope"],
                "linear_depth_m": linear_depth,
                "nonlinear_depth_m": nonlinear_depth,
                "depth_relative_difference": depth_relative,
                "profile_rms_difference_m": rms,
                "linear_max_abs_slope": float(linear_result.observables["max_abs_surface_slope"][-1]),
                "nonlinear_max_abs_slope": solved["maximum_absolute_slope"],
                "linear_minimum_surface_z_m": float(np.min(linear_surface)),
                "nonlinear_minimum_surface_z_m": solved["minimum_surface_z_m"],
            }
        )
        for radius_value, linear_value, nonlinear_value in zip(
            solved["radius_m"], linear_surface, nonlinear_surface
        ):
            nonlinear_profile_rows.append(
                {
                    "linear_critical_fraction": ratio,
                    "r_m": float(radius_value),
                    "linear_surface_z_m": float(linear_value),
                    "nonlinear_surface_z_m": float(nonlinear_value),
                    "difference_m": float(linear_value - nonlinear_value),
                }
            )
    write_rows(output_dir / "forward_vs_nonlinear_curvature.csv", list(nonlinear_rows[0]), nonlinear_rows)
    write_rows(output_dir / "forward_vs_nonlinear_profiles.csv", list(nonlinear_profile_rows[0]), nonlinear_profile_rows)

    nonlinear_critical_rows: list[dict[str, Any]] = []
    for points in (129, 257):
        cache: dict[float, dict[str, Any]] = {}

        def gap_at_ratio(ratio: float) -> float:
            key = round(float(ratio), 12)
            if key not in cache:
                rpm = ratio * linear_critical
                burgers_c = nonlinear_cfg.forward.circulation_coupling_m2 * rpm * 2.0 * math.pi / 60.0
                cache[key] = nonlinear_surface_energy_solution(
                    nonlinear_cfg, burgers_c, points=points
                )
            return float(cache[key]["minimum_surface_z_m"] - stir_bar_top_height_m(nonlinear_cfg))

        lower, upper = 0.0, 1.2
        while gap_at_ratio(upper) > 0.0 and upper < 3.0:
            upper *= 1.25
        if gap_at_ratio(upper) > 0.0:
            nonlinear_critical_rows.append(
                {
                    "points": points,
                    "root_status": "NO_BRACKET_BELOW_3X_LINEAR",
                    "nonlinear_critical_ratio": "",
                    "nonlinear_critical_rpm": "",
                    "root_gap_m": "",
                    "function_evaluations": len(cache),
                }
            )
            continue
        root_ratio = brentq(gap_at_ratio, lower, upper, xtol=2.0e-5, rtol=2.0e-5)
        root_gap = gap_at_ratio(root_ratio)
        nonlinear_critical_rows.append(
            {
                "points": points,
                "root_status": "SOLVED_MODEL_INTERNAL",
                "nonlinear_critical_ratio": root_ratio,
                "nonlinear_critical_rpm": root_ratio * linear_critical,
                "root_gap_m": root_gap,
                "function_evaluations": len(cache),
            }
        )
    write_rows(output_dir / "nonlinear_contact_resolution.csv", list(nonlinear_critical_rows[0]), nonlinear_critical_rows)
    successful_nonlinear = all(bool(row["optimizer_success"]) for row in nonlinear_rows)
    gates.append(gate_row("forward", "nonlinear_optimizer_success", successful_nonlinear, True, successful_nonlinear))
    metrics["linear_contact_proxy_rpm"] = linear_critical
    metrics["nonlinear_contact_model_rows"] = nonlinear_critical_rows
    metrics["nonlinear_comparison_evidence"] = "MODEL_FORM_DIFFERENCE_NOT_EXPERIMENTAL_UNCERTAINTY"
    metrics["wall_time_s"] = time.perf_counter() - started
    write_json(output_dir / "metrics.json", metrics)
    write_json(output_dir / "acceptance.json", gates)
    return {"metrics": metrics, "gates": gates, "nonlinear_rows": nonlinear_rows}


def selected_cases(protocol: Mapping[str, Any], suite: str) -> list[dict[str, Any]]:
    if suite in {"forward"}:
        return []
    if suite == "all":
        return [dict(case) for case in protocol["hf_cases"]]
    tag = suite
    return [dict(case) for case in protocol["hf_cases"] if tag in case["tags"]]


def create_output_path(suite: str, explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit).expanduser().resolve()
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = ROOT / "validation" / "results" / f"{stamp}-{suite}"
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing result directory: {path}")
    path.mkdir(parents=True, exist_ok=False)
    return path


def run_orchestrator(suite: str, output_arg: str | None) -> int:
    protocol = load_protocol()
    cases = selected_cases(protocol, suite)
    output = create_output_path(suite, output_arg)
    workspace_source_start = source_manifest(SRC, ROOT / "configs")
    frozen_root = output / "frozen_execution_input"
    frozen_src = frozen_root / "src"
    frozen_configs = frozen_root / "configs"
    shutil.copytree(SRC, frozen_src, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(ROOT / "configs", frozen_configs)
    frozen_validation = frozen_root / "validation"
    frozen_validation.mkdir(parents=True)
    for path in (
        BASE_PROTOCOL_PATH,
        V2_PROTOCOL_PATH,
        V3_PROTOCOL_PATH,
        V4_PROTOCOL_PATH,
        PROTOCOL_PATH,
        FORWARD_V1_PROTOCOL_PATH,
        FORWARD_V2_PROTOCOL_PATH,
        FORWARD_V3_PROTOCOL_PATH,
        FORWARD_V4_PROTOCOL_PATH,
        FORWARD_PROTOCOL_PATH,
        SCRIPT_PATH,
        FORWARD_RUNNER_PATH,
    ):
        shutil.copy2(path, frozen_validation / path.name)
    frozen_bvp = (
        frozen_root / "research" / "scripts" / INDEPENDENT_BVP_PATH.name
    )
    frozen_bvp.parent.mkdir(parents=True)
    shutil.copy2(INDEPENDENT_BVP_PATH, frozen_bvp)
    if (ROOT / "tests").is_dir():
        shutil.copytree(
            ROOT / "tests",
            frozen_root / "tests",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    frozen_execution_source = source_manifest(frozen_src, frozen_configs)
    manifest: dict[str, Any] = {
        "schema": SCHEMA,
        "suite": suite,
        "status": "running",
        "physical_validation_claimed": False,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "cwd": str(Path.cwd()),
        "command": [sys.executable, str(SCRIPT_PATH), *sys.argv[1:]],
        "protocol_path": str(PROTOCOL_PATH),
        "protocol_sha256": sha256_file(PROTOCOL_PATH),
        "workspace_source_start": workspace_source_start,
        "frozen_execution_source": frozen_execution_source,
        "immutable_execution_snapshot": str(frozen_root),
        "environment": environment_manifest(),
        "cases": [],
    }
    write_json(output / "protocol_v1.json", read_json(BASE_PROTOCOL_PATH))
    write_json(output / "protocol_v2.json", read_json(V2_PROTOCOL_PATH))
    write_json(output / "protocol_v3.json", read_json(V3_PROTOCOL_PATH))
    write_json(output / "protocol_v4.json", read_json(V4_PROTOCOL_PATH))
    write_json(output / "protocol_v5.json", read_json(PROTOCOL_PATH))
    write_json(
        output / "forward_protocol_v1.json", read_json(FORWARD_V1_PROTOCOL_PATH)
    )
    write_json(
        output / "forward_protocol_v2.json", read_json(FORWARD_V2_PROTOCOL_PATH)
    )
    write_json(
        output / "forward_protocol_v3.json", read_json(FORWARD_V3_PROTOCOL_PATH)
    )
    write_json(
        output / "forward_protocol_v4.json", read_json(FORWARD_V4_PROTOCOL_PATH)
    )
    write_json(
        output / "forward_protocol_v5.json", read_json(FORWARD_PROTOCOL_PATH)
    )
    write_json(output / "effective_protocol.json", protocol)
    write_json(output / "source_manifest.json", frozen_execution_source)
    write_json(output / "manifest.json", manifest)

    gates: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []
    negative: list[dict[str, Any]] = []
    summaries: dict[str, dict[str, Any]] = {}

    if suite in {"forward", "all"}:
        forward_command = [
            sys.executable,
            str(frozen_validation / FORWARD_RUNNER_PATH.name),
            "--output",
            str(output / "forward"),
        ]
        forward_environment = os.environ.copy()
        forward_environment["AIRVORTEX_VALIDATION_SRC"] = str(frozen_src)
        forward_environment["AIRVORTEX_VALIDATION_CONFIG_DIR"] = str(
            frozen_configs
        )
        forward_environment["AIRVORTEX_VALIDATION_BVP_SCRIPT"] = str(
            frozen_bvp
        )
        forward_environment["AIRVORTEX_FORWARD_PROTOCOL_V1"] = str(
            frozen_validation / FORWARD_V1_PROTOCOL_PATH.name
        )
        forward_environment["AIRVORTEX_FORWARD_PROTOCOL_V2"] = str(
            frozen_validation / FORWARD_V2_PROTOCOL_PATH.name
        )
        forward_environment["AIRVORTEX_FORWARD_PROTOCOL_V3"] = str(
            frozen_validation / FORWARD_V3_PROTOCOL_PATH.name
        )
        forward_environment["AIRVORTEX_FORWARD_PROTOCOL_V4"] = str(
            frozen_validation / FORWARD_V4_PROTOCOL_PATH.name
        )
        forward_environment["AIRVORTEX_FORWARD_PROTOCOL"] = str(
            frozen_validation / FORWARD_PROTOCOL_PATH.name
        )
        forward_environment["PYTHONPATH"] = str(frozen_src)
        forward_environment["PYTHONDONTWRITEBYTECODE"] = "1"
        forward_completed = subprocess.run(
            forward_command,
            cwd=frozen_root,
            env=forward_environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        (output / "forward_worker.stdout.txt").write_text(
            forward_completed.stdout, encoding="utf-8"
        )
        (output / "forward_worker.stderr.txt").write_text(
            forward_completed.stderr, encoding="utf-8"
        )
        manifest["forward_worker"] = {
            "command": forward_command,
            "returncode": forward_completed.returncode,
            "status": (
                "completed"
                if forward_completed.returncode == 0
                else "failed"
            ),
        }
        write_json(output / "manifest.json", manifest)
        forward_worker_gate = gate_row(
            "forward_worker",
            "returncode",
            forward_completed.returncode,
            0,
            forward_completed.returncode == 0,
        )
        gates.append(forward_worker_gate)
        if (output / "forward" / "acceptance.json").exists():
            forward_gates = read_json(output / "forward" / "acceptance.json")
            gates.extend(forward_gates)
        if (output / "forward" / "negative_results.json").exists():
            negative.extend(read_json(output / "forward" / "negative_results.json"))
        if forward_completed.returncode != 0:
            failure = {
                "scope": "forward",
                "status": "FAIL_EXCEPTION",
                "returncode": forward_completed.returncode,
                "command": forward_command,
                "stdout": forward_completed.stdout,
                "stderr": forward_completed.stderr,
            }
            negative.append(failure)

    cases_root = output / "cases"
    cases_root.mkdir(exist_ok=True)
    for case in cases:
        descriptor = output / f".{case['id']}.json"
        write_json(descriptor, case)
        case_dir = cases_root / case["id"]
        command = [
            sys.executable,
            str(SCRIPT_PATH),
            "--_worker-case",
            str(descriptor),
            "--_case-output",
            str(case_dir),
        ]
        environment = os.environ.copy()
        environment["AIRVORTEX_VALIDATION_SRC"] = str(frozen_src)
        environment["AIRVORTEX_VALIDATION_CONFIG_DIR"] = str(frozen_configs)
        environment["PYTHONPATH"] = str(frozen_src) + (
            os.pathsep + environment["PYTHONPATH"]
            if environment.get("PYTHONPATH")
            else ""
        )
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        (case_dir / "worker.stdout.txt").write_text(completed.stdout, encoding="utf-8")
        (case_dir / "worker.stderr.txt").write_text(completed.stderr, encoding="utf-8")
        descriptor.unlink()
        record = {
            "id": case["id"],
            "tags": case["tags"],
            "worker_returncode": completed.returncode,
            "command": command,
            "status": "completed" if completed.returncode == 0 else "failed",
        }
        manifest["cases"].append(record)
        write_json(output / "manifest.json", manifest)
        if completed.returncode != 0:
            failure = read_json(case_dir / "failure.json") if (case_dir / "failure.json").exists() else record
            negative.append({"source": "hf_case", **failure})
            continue
        summary = read_json(case_dir / "summary.json")
        summaries[case["id"]] = summary
        if case.get("expected_termination_reason") is not None:
            record["status"] = (
                "domain_limit_expected"
                if (
                    summary["termination_reason"]
                    == case["expected_termination_reason"]
                    and summary["domain_limited"]
                    and not summary["configured_duration_reached"]
                )
                else "unexpected_termination"
            )
            write_json(output / "manifest.json", manifest)
            negative.append(
                {
                    "source": "expected_model_domain_limit",
                    "case_id": case["id"],
                    "status": "DOMAIN_LIMIT_EXPECTED_NOT_FULL_DURATION",
                    "requested_duration_s": summary["requested_duration_s"],
                    "actual_end_time_s": summary["actual_end_time_s"],
                    "termination_reason": summary["termination_reason"],
                    "interpretation": (
                        "pre-contact H1 model domain reached; not a complete "
                        "configured-duration stability or performance result"
                    ),
                }
            )
        case_gates = evaluate_case_gates(case, summary, protocol)
        gates.extend(case_gates)
        for row in case_gates:
            if row["status"] != "PASS":
                negative.append({"source": "hf_gate", **row})

    if cases:
        synthetic_dir = output / "synthetic"
        synthetic_command = [
            sys.executable,
            str(SCRIPT_PATH),
            "--_synthetic-output",
            str(synthetic_dir),
        ]
        synthetic_environment = os.environ.copy()
        synthetic_environment["AIRVORTEX_VALIDATION_SRC"] = str(frozen_src)
        synthetic_environment["AIRVORTEX_VALIDATION_CONFIG_DIR"] = str(frozen_configs)
        synthetic_environment["PYTHONPATH"] = str(frozen_src)
        synthetic_completed = subprocess.run(
            synthetic_command,
            cwd=ROOT,
            env=synthetic_environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if synthetic_completed.returncode != 0:
            failure = {
                "scope": "synthetic_throat",
                "status": "FAIL_EXCEPTION",
                "returncode": synthetic_completed.returncode,
                "stdout": synthetic_completed.stdout,
                "stderr": synthetic_completed.stderr,
            }
            negative.append(failure)
            throat_rows = []
        else:
            throat_rows = read_json(synthetic_dir / "synthetic_throat_tests.json")
            shutil.copy2(synthetic_dir / "synthetic_throat_tests.csv", output / "synthetic_throat_tests.csv")
            shutil.copy2(synthetic_dir / "synthetic_throat_tests.json", output / "synthetic_throat_tests.json")
        for row in throat_rows:
            gate = gate_row("synthetic_throat", row["test"], row["status"], "PASS", row["status"] == "PASS")
            gates.append(gate)
            if gate["status"] != "PASS":
                negative.append({"source": "synthetic_throat", **gate})
        if synthetic_completed.returncode == 0:
            poison_rows = read_json(
                synthetic_dir / "quality_gate_poison_tests.json"
            )
            for filename in (
                "quality_gate_poison_tests.csv",
                "quality_gate_poison_tests.json",
            ):
                shutil.copy2(synthetic_dir / filename, output / filename)
            for row in poison_rows:
                gate = gate_row(
                    "quality_gate_poison",
                    row["test"],
                    row["status"],
                    "PASS",
                    row["status"] == "PASS",
                )
                gates.append(gate)
                if gate["status"] != "PASS":
                    negative.append({"source": "quality_gate_poison", **gate})

    available = set(summaries)
    reversal_ids = {"hf_reversal_positive", "hf_reversal_negative"}
    if reversal_ids <= available:
        rows, reversal_gates = compare_reversal(cases_root, protocol)
        comparisons.extend(rows)
        gates.extend(reversal_gates)
        negative.extend(
            {"source": "reversal", **row}
            for row in rows
            if row["status"] == "FAIL"
        )

    groups = [
        ("time_step", ["hf_dt_800us", "hf_dt_400us", "hf_dt_200us"], 2.0, "dt"),
        ("grid_short", ["hf_grid_short_n16", "hf_grid_short_n24", "hf_grid_short_n36", "hf_grid_short_n54"], 1.5, "grid"),
        ("grid_moderate", ["hf_grid_moderate_n24", "hf_grid_moderate_n36", "hf_grid_moderate_n54"], 1.5, "grid"),
    ]
    for group, ids, ratio, axis in groups:
        if set(ids) <= available:
            rows = convergence_group(cases_root, group, ids, ratio, summaries, axis)
            comparisons.extend(rows)
            negative.extend(
                {"source": "convergence", **row}
                for row in rows
                if row["status"] in {"NOT_DEMONSTRATED", "INCONCLUSIVE_ADAPTIVE_LIMIT"}
            )

    interface_ids = [
        "hf_interface_base",
        "hf_interface_thickness_1p5",
        "hf_interface_thickness_4p0",
        "hf_interface_cls_off",
        "hf_interface_cls_interval4",
        "hf_interface_cls_speed_0p02",
        "hf_interface_cls_speed_0p08",
    ]
    if set(interface_ids) <= available:
        comparisons.extend(interface_sensitivity(cases_root, interface_ids, summaries))

    case_index_rows: list[dict[str, Any]] = []
    for case in cases:
        if case["id"] not in summaries:
            case_index_rows.append({"case_id": case["id"], "status": "failed"})
            continue
        summary = summaries[case["id"]]
        case_index_rows.append(
            {
                "case_id": case["id"],
                "status": summary["status"],
                "nr": case["nr"],
                "nz": case["nz"],
                "configured_dt_s": case["dt_s"],
                "duration_s": case["duration_s"],
                "requested_duration_s": summary["requested_duration_s"],
                "actual_end_time_s": summary["actual_end_time_s"],
                "configured_duration_reached": summary[
                    "configured_duration_reached"
                ],
                "termination_reason": summary["termination_reason"],
                "domain_limited": summary["domain_limited"],
                "rpm": case["rpm"],
                "wall_time_s": summary["wall_time_s"],
                "completed_steps": summary["completed_steps"],
                "cell_steps_per_wall_s": summary["effective_2d_cell_steps_per_wall_s"],
                "final_funnel_depth_m": summary["final_funnel_depth_m"],
                "max_relative_volume_error": summary["maximum_relative_liquid_volume_error"],
                "dimensionless_divergence": summary["maximum_dimensionless_projected_divergence"],
                "max_advective_cfl": summary["maximum_advective_cfl"],
                "cumulative_mass_repair_fraction": summary["cumulative_phase_mass_repair_fraction"],
            }
        )
    if case_index_rows:
        fields = sorted({key for row in case_index_rows for key in row})
        write_rows(output / "case_index.csv", fields, case_index_rows)
    if comparisons:
        write_rows(output / "comparisons.csv", ["group", "metric", "value", "status", "note"], comparisons)
    write_json(output / "acceptance.json", gates)
    write_json(output / "negative_results.json", negative)

    execution_source_end = source_manifest(frozen_src, frozen_configs)
    workspace_source_end = source_manifest(SRC, ROOT / "configs")
    execution_unchanged = (
        execution_source_end["tree_sha256"]
        == frozen_execution_source["tree_sha256"]
    )
    source_gate = gate_row(
        "suite",
        "immutable_execution_source_unchanged",
        execution_source_end["tree_sha256"],
        frozen_execution_source["tree_sha256"],
        execution_unchanged,
    )
    gates.append(source_gate)
    if not execution_unchanged:
        negative.append(
            {
                "source": "suite_source",
                **source_gate,
                "status_detail": "INVALID_SOURCE_CHANGED_DURING_RUN",
            }
        )
    write_json(output / "acceptance.json", gates)
    write_json(output / "negative_results.json", negative)

    hard_failures = [row for row in gates if row["status"] == "FAIL"]
    case_failures = [
        record
        for record in manifest["cases"]
        if record["status"] in {"failed", "unexpected_termination"}
    ]
    manifest["completed_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["hard_gate_failures"] = len(hard_failures)
    manifest["case_failures"] = len(case_failures)
    manifest["negative_or_inconclusive_records"] = len(negative)
    manifest["execution_source_end"] = execution_source_end
    manifest["execution_source_unchanged"] = execution_unchanged
    manifest["workspace_source_end"] = workspace_source_end
    manifest["workspace_changed_while_snapshot_remained_immutable"] = (
        workspace_source_end["tree_sha256"]
        != workspace_source_start["tree_sha256"]
    )
    if hard_failures or case_failures:
        manifest["status"] = "completed_with_failures"
    elif negative:
        manifest["status"] = "completed_with_negative_or_inconclusive_results"
    else:
        manifest["status"] = "completed"
    manifest["result_directory"] = str(output)
    write_json(output / "manifest.json", manifest)
    print(json.dumps({"result_directory": str(output), "status": manifest["status"]}, ensure_ascii=False))
    return 1 if hard_failures or case_failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        choices=("forward", "hf-smoke", "hf-core", "hf-extended", "all"),
        default="hf-smoke",
    )
    parser.add_argument("--output", help="new result directory; existing paths are rejected")
    parser.add_argument("--list", action="store_true", help="list frozen cases without running")
    parser.add_argument("--_worker-case", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--_case-output", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--_synthetic-output", type=Path, help=argparse.SUPPRESS)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args._worker_case is not None:
        if args._case_output is None:
            raise SystemExit("internal worker requires --_case-output")
        return _worker_hf(args._worker_case.resolve(), args._case_output.resolve())
    if args._synthetic_output is not None:
        args._synthetic_output.mkdir(parents=True, exist_ok=False)
        synthetic_throat_tests(args._synthetic_output.resolve())
        quality_gate_poison_tests(args._synthetic_output.resolve())
        return 0
    protocol = load_protocol()
    if args.list:
        print(f"protocol_sha256={sha256_file(PROTOCOL_PATH)}")
        for case in protocol["hf_cases"]:
            print(f"{case['id']:36s} tags={','.join(case['tags'])}")
        return 0
    return run_orchestrator(args.suite, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
