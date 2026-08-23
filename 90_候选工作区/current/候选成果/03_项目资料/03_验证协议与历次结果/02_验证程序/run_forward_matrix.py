#!/usr/bin/env python3
"""Independent, machine-readable validation matrix for the Forward backend.

This program is intended to be launched from an immutable validation snapshot.
It verifies implementation and numerical consistency only.  It never labels a
stable calculation as experimental or physical validation, and it never hides
failed/non-monotone refinement results.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import csv
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import platform
import resource
import subprocess
import sys
import time
import traceback
from types import SimpleNamespace
from typing import Any, Iterable, Mapping, Sequence
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
ACTIVE_SRC = Path(
    os.environ.get("AIRVORTEX_VALIDATION_SRC", str(ROOT / "src"))
).resolve()
ACTIVE_CONFIG_DIR = Path(
    os.environ.get("AIRVORTEX_VALIDATION_CONFIG_DIR", str(ROOT / "configs"))
).resolve()
INDEPENDENT_BVP_PATH = Path(
    os.environ.get(
        "AIRVORTEX_VALIDATION_BVP_SCRIPT",
        str(ROOT / "research" / "scripts" / "verify_nonlinear_surface.py"),
    )
).resolve()
FORWARD_V1_PROTOCOL_PATH = Path(
    os.environ.get(
        "AIRVORTEX_FORWARD_PROTOCOL_V1",
        str(ROOT / "validation" / "forward_protocol_v1.json"),
    )
).resolve()
FORWARD_V2_PROTOCOL_PATH = Path(
    os.environ.get(
        "AIRVORTEX_FORWARD_PROTOCOL_V2",
        str(ROOT / "validation" / "forward_protocol_v2.json"),
    )
).resolve()
FORWARD_V3_PROTOCOL_PATH = Path(
    os.environ.get(
        "AIRVORTEX_FORWARD_PROTOCOL_V3",
        str(ROOT / "validation" / "forward_protocol_v3.json"),
    )
).resolve()
FORWARD_V4_PROTOCOL_PATH = Path(
    os.environ.get(
        "AIRVORTEX_FORWARD_PROTOCOL_V4",
        str(ROOT / "validation" / "forward_protocol_v4.json"),
    )
).resolve()
FORWARD_PROTOCOL_PATH = Path(
    os.environ.get(
        "AIRVORTEX_FORWARD_PROTOCOL",
        str(ROOT / "validation" / "forward_protocol_v5.json"),
    )
).resolve()
SCRIPT_PATH = Path(__file__).resolve()

if str(ACTIVE_SRC) not in sys.path:
    sys.path.insert(0, str(ACTIVE_SRC))

import numpy as np  # noqa: E402
import scipy  # noqa: E402
from scipy.integrate import quad, trapezoid  # noqa: E402
from scipy.optimize import brentq, minimize  # noqa: E402
from scipy.special import expi  # noqa: E402


SCHEMA = "airvortex-forward-numerical-validation-result-v2"
TINY = np.finfo(np.float64).tiny


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, (float, np.floating)):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError(f"non-finite JSON value: {result}")
        return result
    if isinstance(value, np.integer):
        return int(value)
    if hasattr(value, "value") and isinstance(value.value, (str, int, float)):
        return jsonable(value.value)
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, np.ndarray):
        return jsonable(value.tolist())
    raise TypeError(f"cannot encode {type(value).__name__}")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(
        jsonable(value), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
    ) + "\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def write_rows(
    path: Path, rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str] | None = None
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = sorted({str(key) for row in rows for key in row})
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: (
                        json.dumps(jsonable(row.get(key)), ensure_ascii=False, separators=(",", ":"))
                        if isinstance(row.get(key), (dict, list, tuple))
                        else row.get(key, "")
                    )
                    for key in fieldnames
                }
            )
    os.replace(temporary, path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_array(array: np.ndarray) -> str:
    value = np.ascontiguousarray(array)
    digest = hashlib.sha256()
    digest.update(str(value.dtype).encode("ascii"))
    digest.update(json.dumps(list(value.shape), separators=(",", ":")).encode("ascii"))
    digest.update(value.tobytes(order="C"))
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def input_manifest() -> dict[str, Any]:
    paths: list[Path] = []
    paths.extend(ACTIVE_SRC.glob("**/*.py"))
    paths.extend(ACTIVE_CONFIG_DIR.glob("*.json"))
    paths.extend(
        [
            SCRIPT_PATH,
            INDEPENDENT_BVP_PATH,
            FORWARD_V1_PROTOCOL_PATH,
            FORWARD_V2_PROTOCOL_PATH,
            FORWARD_V3_PROTOCOL_PATH,
            FORWARD_V4_PROTOCOL_PATH,
            FORWARD_PROTOCOL_PATH,
        ]
    )
    test_root = ROOT / "tests"
    if test_root.is_dir():
        paths.extend(test_root.glob("**/*.py"))
    unique = sorted({path.resolve() for path in paths if path.is_file()})
    tree = hashlib.sha256()
    rows: list[dict[str, Any]] = []
    for path in unique:
        if path.is_relative_to(ACTIVE_SRC):
            relative = "src/" + path.relative_to(ACTIVE_SRC).as_posix()
        elif path.is_relative_to(ACTIVE_CONFIG_DIR):
            relative = "configs/" + path.relative_to(ACTIVE_CONFIG_DIR).as_posix()
        elif path.is_relative_to(ROOT):
            relative = path.relative_to(ROOT).as_posix()
        else:
            relative = str(path)
        digest = sha256_file(path)
        rows.append({"path": relative, "sha256": digest, "bytes": path.stat().st_size})
        tree.update(relative.encode("utf-8") + b"\0" + digest.encode("ascii") + b"\0")
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
        "cpu_count": os.cpu_count(),
        "numpy_version": np.__version__,
        "scipy_version": scipy.__version__,
        "numpy_configuration": buffer.getvalue(),
        "thread_environment": {
            key: os.environ.get(key)
            for key in (
                "OMP_NUM_THREADS",
                "OPENBLAS_NUM_THREADS",
                "MKL_NUM_THREADS",
                "NUMEXPR_NUM_THREADS",
            )
        },
    }


def load_protocol() -> tuple[dict[str, Any], dict[str, Any]]:
    base = read_json(FORWARD_V1_PROTOCOL_PATH)
    amendment_v2 = read_json(FORWARD_V2_PROTOCOL_PATH)
    amendment_v3 = read_json(FORWARD_V3_PROTOCOL_PATH)
    amendment_v4 = read_json(FORWARD_V4_PROTOCOL_PATH)
    amendment_v5 = read_json(FORWARD_PROTOCOL_PATH)
    if base.get("schema") != "airvortex-forward-numerical-validation-protocol-v1":
        raise ValueError("unsupported Forward v1 protocol")
    if amendment_v2.get("schema") != "airvortex-forward-numerical-validation-protocol-v2":
        raise ValueError("unsupported Forward v2 protocol")
    if amendment_v3.get("schema") != "airvortex-forward-numerical-validation-protocol-v3":
        raise ValueError("unsupported Forward v3 protocol")
    if amendment_v4.get("schema") != "airvortex-forward-numerical-validation-protocol-v4":
        raise ValueError("unsupported Forward v4 protocol")
    if amendment_v5.get("schema") != "airvortex-forward-numerical-validation-protocol-v5":
        raise ValueError("unsupported Forward v5 protocol")
    if sha256_file(FORWARD_V1_PROTOCOL_PATH) != amendment_v2.get("base_protocol_sha256"):
        raise ValueError("Forward v1 protocol hash no longer matches v2 amendment")
    if sha256_file(FORWARD_V2_PROTOCOL_PATH) != amendment_v3.get("base_protocol_sha256"):
        raise ValueError("Forward v2 protocol hash no longer matches v3 amendment")
    if sha256_file(FORWARD_V3_PROTOCOL_PATH) != amendment_v4.get("base_protocol_sha256"):
        raise ValueError("Forward v3 protocol hash no longer matches v4 amendment")
    if sha256_file(FORWARD_V4_PROTOCOL_PATH) != amendment_v5.get("base_protocol_sha256"):
        raise ValueError("Forward v4 protocol hash no longer matches v5 amendment")
    effective = json.loads(json.dumps(amendment_v2))
    effective["schema"] = amendment_v5["schema"]
    effective["v3_amendment"] = amendment_v3
    effective["v4_amendment"] = amendment_v4
    effective["v5_amendment"] = amendment_v5
    effective["amendments"].update(amendment_v3["amendments"])
    effective["amendments"].update(amendment_v4["amendments"])
    effective["amendments"].update(amendment_v5["amendments"])
    return base, effective


def gate(scope: str, metric: str, actual: Any, limit: Any, passed: bool) -> dict[str, Any]:
    return {
        "scope": scope,
        "metric": metric,
        "actual": jsonable(actual),
        "limit_or_expected": jsonable(limit),
        "status": "PASS" if passed else "FAIL",
        "physical_validation_claimed": False,
    }


def relative_l2(difference: np.ndarray, references: Sequence[np.ndarray]) -> float:
    numerator = float(np.linalg.norm(np.ravel(difference)))
    denominator = max(
        *(float(np.linalg.norm(np.ravel(reference))) for reference in references),
        TINY,
    )
    return numerator / denominator


def area_rms(values: np.ndarray, radius_m: np.ndarray) -> float:
    radius = np.asarray(radius_m, dtype=np.float64)
    profile = np.asarray(values, dtype=np.float64)
    vessel_radius = float(radius[-1])
    return math.sqrt(
        max(
            0.0,
            2.0
            / vessel_radius**2
            * float(trapezoid(np.square(profile) * radius, x=radius)),
        )
    )


def area_mean(values: np.ndarray, radius_m: np.ndarray) -> float:
    radius = np.asarray(radius_m, dtype=np.float64)
    return (
        2.0
        / float(radius[-1]) ** 2
        * float(trapezoid(np.asarray(values) * radius, x=radius))
    )


def exact_centrifugal_potential(radius_m: np.ndarray, core_radius_m: float) -> np.ndarray:
    """Independent closed primitive of integral (u_theta/C)^2/r dr."""

    radius = np.asarray(radius_m, dtype=np.float64)
    x = np.square(radius / core_radius_m)
    result = np.zeros_like(x)
    positive = x > 0.0
    xp = x[positive]
    result[positive] = (
        2.0 * (math.log(2.0) + expi(-xp) - expi(-2.0 * xp))
        - np.square(-np.expm1(-xp)) / xp
    ) / (2.0 * core_radius_m**2)
    return result


def independent_finite_container_factors(x_upper: float) -> dict[str, float]:
    """Compute F,K,G by quadrature, independently of production analysis code."""

    def i_integrand(value: float) -> float:
        if value == 0.0:
            return 1.0
        return (-math.expm1(-value)) ** 2 / value**2

    def j_integrand(value: float) -> float:
        if value == 0.0:
            return 0.0
        return (-math.expm1(-value)) ** 2 / value

    integral_i, error_i = quad(
        i_integrand, 0.0, x_upper, epsabs=2.0e-13, epsrel=2.0e-13, limit=600
    )
    integral_j, error_j = quad(
        j_integrand, 0.0, x_upper, epsabs=2.0e-13, epsrel=2.0e-13, limit=600
    )
    denominator = 2.0 * math.log(2.0)
    factor_f = integral_i / denominator
    factor_k = integral_j / (denominator * x_upper)
    return {
        "X": x_upper,
        "I_quad": integral_i,
        "J_quad": integral_j,
        "I_quad_error_estimate": error_i,
        "J_quad_error_estimate": error_j,
        "F": factor_f,
        "K": factor_k,
        "G": factor_f - factor_k,
    }


def make_config(
    rpm: float,
    *,
    initial_rpm: float | None = None,
    direction: int = 1,
    duration_s: float = 0.2,
    output_interval_s: float = 0.05,
    radial_points: int = 256,
    sigma: float = 0.072,
    relative_tolerance: float = 1.0e-6,
    absolute_tolerance_m: float = 1.0e-9,
    integration_dt_s: float = 0.001,
    liquid_depth_m: float = 0.060,
):
    from airvortex import SimulationConfig

    raw = read_json(ACTIVE_CONFIG_DIR / "forward_default.json")
    raw["backend"] = "forward"
    raw["container"]["liquid_depth_m"] = liquid_depth_m
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
            "relative_tolerance": relative_tolerance,
            "absolute_tolerance": absolute_tolerance_m,
            "integration_time_step_s": integration_dt_s,
        }
    )
    return SimulationConfig.from_dict(raw)


def energy_solution(
    config: Any,
    circulation_m2_s: float,
    *,
    points: int,
    initial: np.ndarray | None = None,
) -> dict[str, Any]:
    """Independent constrained minimisation of the exact graph energy.

    The objective is assembled directly from the continuum energy.  Its seed
    is the inviscid outer profile (or a previous energy solution), never a
    backend nonlinear surface.
    """

    radius = np.linspace(0.0, config.container.radius_m, points)
    dr = float(radius[1] - radius[0])
    weights = radius * dr
    weights[-1] *= 0.5
    weight_sum = float(np.sum(weights))
    potential = exact_centrifugal_potential(
        radius, config.forward.effective_core_radius_m
    )
    rho = float(config.fluid.liquid_density_kg_m3)
    gravity = float(config.fluid.gravity_m_s2)
    sigma = float(config.fluid.surface_tension_n_m)
    mid_radius = 0.5 * (radius[:-1] + radius[1:])

    def project(value: np.ndarray) -> np.ndarray:
        return value - float(np.dot(weights, value)) / weight_sum

    if initial is None:
        x0 = circulation_m2_s**2 / gravity * project(potential)
    else:
        source = np.asarray(initial, dtype=np.float64)
        old_radius = np.linspace(0.0, config.container.radius_m, source.size)
        x0 = project(np.interp(radius, old_radius, source))

    def raw(value: np.ndarray) -> tuple[float, np.ndarray]:
        elevation = project(value)
        slope = np.diff(elevation) / dr
        bulk = (
            0.5 * rho * gravity * np.square(elevation)
            - rho * circulation_m2_s**2 * potential * elevation
        )
        energy = 2.0 * math.pi * (
            float(np.dot(weights, bulk))
            + sigma
            * float(
                np.sum(mid_radius * dr * (np.sqrt(1.0 + np.square(slope)) - 1.0))
            )
        )
        gradient_elevation = 2.0 * math.pi * weights * (
            rho * gravity * elevation - rho * circulation_m2_s**2 * potential
        )
        flux = (
            2.0
            * math.pi
            * sigma
            * mid_radius
            * slope
            / np.sqrt(1.0 + np.square(slope))
        )
        gradient_elevation[:-1] -= flux
        gradient_elevation[1:] += flux
        gradient = gradient_elevation - weights * (
            float(np.sum(gradient_elevation)) / weight_sum
        )
        return energy, gradient

    initial_energy, _ = raw(x0)
    depth_scale = max(float(np.ptp(x0)), 1.0e-5)
    energy_scale = max(
        abs(initial_energy),
        rho
        * gravity
        * math.pi
        * config.container.radius_m**2
        * depth_scale**2,
        1.0e-14,
    )

    def objective(value: np.ndarray) -> tuple[float, np.ndarray]:
        value_energy, value_gradient = raw(value)
        return value_energy / energy_scale, value_gradient / energy_scale

    result = minimize(
        objective,
        x0,
        method="L-BFGS-B",
        jac=True,
        options={"maxiter": 6000, "ftol": 2.0e-15, "gtol": 3.0e-10, "maxls": 100},
    )
    elevation = project(np.asarray(result.x, dtype=np.float64))
    _, gradient = raw(elevation)
    slope = np.diff(elevation) / dr
    return {
        "radius_m": radius,
        "elevation_m": elevation,
        "success": bool(result.success),
        "status": int(result.status),
        "message": str(result.message),
        "iterations": int(result.nit),
        "function_evaluations": int(result.nfev),
        "energy_j": float(raw(elevation)[0]),
        "scaled_projected_gradient_infinity": float(np.max(np.abs(gradient)))
        / energy_scale,
        "discrete_volume_mean_m": float(np.dot(weights, elevation) / weight_sum),
        "maximum_last_segment_slope": float(np.max(np.abs(slope))),
        "wall_last_segment_slope": float(slope[-1]),
    }


def load_independent_bvp() -> tuple[Any, dict[str, Any]]:
    source_text = INDEPENDENT_BVP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source_text)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    imports_airvortex = any(name == "airvortex" or name.startswith("airvortex.") for name in imported)
    if imports_airvortex:
        raise RuntimeError("independent BVP script imports production airvortex code")
    module_name = f"independent_nonlinear_bvp_{os.getpid()}"
    spec = importlib.util.spec_from_file_location(module_name, INDEPENDENT_BVP_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load independent BVP audit")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module, {
        "path": str(INDEPENDENT_BVP_PATH),
        "sha256": sha256_file(INDEPENDENT_BVP_PATH),
        "imports": sorted(set(imported)),
        "imports_airvortex": imports_airvortex,
    }


def run_external_command(
    command: Sequence[str], output_prefix: Path, *, cwd: Path
) -> dict[str, Any]:
    started_utc = datetime.now(timezone.utc).isoformat()
    wall_start = time.perf_counter()
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        env=os.environ.copy(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    wall_time = time.perf_counter() - wall_start
    stdout_path = output_prefix.with_suffix(".stdout.txt")
    stderr_path = output_prefix.with_suffix(".stderr.txt")
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    evidence = {
        "command": list(command),
        "cwd": str(cwd),
        "started_utc": started_utc,
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "wall_time_s": wall_time,
        "returncode": completed.returncode,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "stdout_sha256": sha256_file(stdout_path),
        "stderr_sha256": sha256_file(stderr_path),
    }
    write_json(output_prefix.with_suffix(".command.json"), evidence)
    return evidence


def sequence_diagnostics(
    values: Sequence[float], *, refinement_ratio: float | None = None
) -> dict[str, Any]:
    array = np.asarray(values, dtype=np.float64)
    differences = np.diff(array)
    magnitudes = np.abs(differences)
    same_direction = bool(
        differences.size > 0
        and (np.all(differences > 0.0) or np.all(differences < 0.0))
    )
    decreasing_changes = bool(
        differences.size > 1
        and np.all(magnitudes[1:] < magnitudes[:-1])
    )
    status = (
        "CONVERGENCE_TREND"
        if same_direction and decreasing_changes
        else "NOT_DEMONSTRATED"
    )
    observed_order: float | None = None
    extrapolated_last_error: float | None = None
    if (
        refinement_ratio is not None
        and magnitudes.size >= 2
        and magnitudes[-1] > 0.0
        and magnitudes[-2] > magnitudes[-1]
    ):
        observed_order = math.log(magnitudes[-2] / magnitudes[-1]) / math.log(
            refinement_ratio
        )
        denominator = refinement_ratio**observed_order - 1.0
        if observed_order > 0.0 and denominator > 0.0:
            extrapolated_last_error = float(magnitudes[-1] / denominator)
    return {
        "values": list(array),
        "successive_signed_changes": list(differences),
        "successive_absolute_changes": list(magnitudes),
        "same_signed_approach": same_direction,
        "strictly_decreasing_changes": decreasing_changes,
        "status": status,
        "observed_order_last_three": observed_order,
        "extrapolated_last_error": extrapolated_last_error,
    }


def archive_result_arrays(output_dir: Path, result: Any, prefix: str) -> None:
    summaries: dict[str, Any] = {}
    for namespace, mapping in (
        ("observable", result.observables),
        ("field", result.fields),
    ):
        for name, value in mapping.items():
            array = np.asarray(value)
            summaries[f"{namespace}:{name}"] = {
                "shape": list(array.shape),
                "dtype": str(array.dtype),
                "sha256": sha256_array(array),
                "minimum": float(np.min(array)) if array.size else None,
                "maximum": float(np.max(array)) if array.size else None,
                "all_finite": bool(np.isfinite(array).all()),
            }
    write_json(output_dir / f"{prefix}_array_summaries.json", summaries)


def _rpm_from_forcing(config: Any, forcing: float) -> float:
    circulation_sq = (
        forcing
        * config.fluid.surface_tension_n_m
        * config.forward.effective_core_radius_m
        / config.fluid.liquid_density_kg_m3
    )
    omega = math.sqrt(circulation_sq) / config.forward.circulation_coupling_m2
    return omega * 60.0 / (2.0 * math.pi)


def _forcing_from_rpm(config: Any, rpm: float) -> float:
    omega = rpm * 2.0 * math.pi / 60.0
    circulation = config.forward.circulation_coupling_m2 * omega
    return (
        config.fluid.liquid_density_kg_m3
        * circulation**2
        / (
            config.fluid.surface_tension_n_m
            * config.forward.effective_core_radius_m
        )
    )


def run_matrix(output_dir: Path) -> dict[str, Any]:
    from airvortex import BackendMode, SimulationConfig
    from airvortex.backends.forward import (
        ForwardBackend,
        linear_critical_actual_rpm,
        solve_nonlinear_critical_surface,
        solve_unit_surface_response,
        stir_bar_top_height_m,
        surface_depth_functional_m,
        surface_profile_range_m,
    )
    import airvortex.backends.nonlinear_surface as nonlinear_module
    from airvortex.backends.nonlinear_surface import (
        NonlinearSurfaceDomainError,
        NonlinearSurfaceError,
        NonlinearSurfaceSolver,
        UnderresolvedSurfaceError,
    )

    output_dir.mkdir(parents=True, exist_ok=False)
    started_utc = datetime.now(timezone.utc).isoformat()
    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    protocol_v1, protocol_v2 = load_protocol()
    limits = protocol_v1["hard_implementation_gates"]
    source_start = input_manifest()
    write_json(output_dir / "input_manifest_start.json", source_start)
    write_json(output_dir / "environment.json", environment_manifest())
    write_json(output_dir / "forward_protocol_v1.json", protocol_v1)
    write_json(
        output_dir / "forward_protocol_v2.json",
        read_json(FORWARD_V2_PROTOCOL_PATH),
    )
    write_json(
        output_dir / "forward_protocol_v3.json",
        read_json(FORWARD_V3_PROTOCOL_PATH),
    )
    write_json(
        output_dir / "forward_protocol_v4.json",
        read_json(FORWARD_V4_PROTOCOL_PATH),
    )
    write_json(
        output_dir / "forward_protocol_v5.json",
        read_json(FORWARD_PROTOCOL_PATH),
    )
    write_json(output_dir / "effective_forward_protocol.json", protocol_v2)

    gates: list[dict[str, Any]] = []
    negative: list[dict[str, Any]] = []
    backend = ForwardBackend()

    default_config_path = ACTIVE_CONFIG_DIR / "forward_default.json"
    default_config_sha = sha256_file(default_config_path)
    declared_config_sha = protocol_v1["declared_default_input"][
        "config_file_sha256"
    ]
    gates.append(
        gate(
            "parameters",
            "default_config_file_sha256",
            default_config_sha,
            declared_config_sha,
            default_config_sha == declared_config_sha,
        )
    )
    default_config = SimulationConfig.from_dict(read_json(default_config_path))
    if default_config.backend is not BackendMode.FORWARD:
        raise RuntimeError("frozen default config is not Forward")
    bar_top_m = stir_bar_top_height_m(default_config)
    static_gap_m = default_config.container.liquid_depth_m - bar_top_m
    parameter_vector = {
        "liquid_density_kg_m3": default_config.fluid.liquid_density_kg_m3,
        "gravity_m_s2": default_config.fluid.gravity_m_s2,
        "surface_tension_n_m": default_config.fluid.surface_tension_n_m,
        "core_radius_m": default_config.forward.effective_core_radius_m,
        "vessel_radius_m": default_config.container.radius_m,
        "liquid_depth_m": default_config.container.liquid_depth_m,
        "bar_top_m": bar_top_m,
        "static_gap_m": static_gap_m,
        "bar_half_length_m": default_config.stir_bar.half_length_m,
        "circulation_coupling_m2": default_config.forward.circulation_coupling_m2,
        "surface_radial_points": default_config.forward.surface_radial_points,
        "relative_tolerance": default_config.forward.relative_tolerance,
        "absolute_tolerance_m": default_config.forward.absolute_tolerance,
    }
    parameter_vector["Bo"] = (
        parameter_vector["liquid_density_kg_m3"]
        * parameter_vector["gravity_m_s2"]
        * parameter_vector["core_radius_m"] ** 2
        / parameter_vector["surface_tension_n_m"]
    )
    parameter_vector["L_R_over_c"] = (
        parameter_vector["vessel_radius_m"] / parameter_vector["core_radius_m"]
    )
    parameter_vector["static_gap_ratio_S_over_c"] = (
        parameter_vector["static_gap_m"] / parameter_vector["core_radius_m"]
    )
    parameter_record = {
        "config_path": str(default_config_path),
        "config_file_sha256": default_config_sha,
        "parameter_vector": parameter_vector,
        "parameter_vector_sha256": canonical_sha256(parameter_vector),
    }
    write_json(output_dir / "parameters.json", parameter_record)

    # Execute the repository test suite and the standalone audit as separate
    # processes.  Their raw output and exact commands remain in the evidence.
    tests_root = ROOT / "tests"
    unit_test_evidence = run_external_command(
        [
            sys.executable,
            "-m",
            "unittest",
            str(tests_root / "test_nonlinear_surface.py"),
            str(tests_root / "test_forward.py"),
            "-v",
        ],
        output_dir / "repository_unit_tests",
        cwd=ROOT,
    )
    gates.append(
        gate(
            "independent_process",
            "repository_unit_tests_exit_code",
            unit_test_evidence["returncode"],
            0,
            unit_test_evidence["returncode"] == 0,
        )
    )
    external_audit = run_external_command(
        [sys.executable, str(INDEPENDENT_BVP_PATH), "--summary-json"],
        output_dir / "independent_bvp_full_audit",
        cwd=ROOT,
    )
    gates.append(
        gate(
            "independent_process",
            "external_independent_audit_exit_code",
            external_audit["returncode"],
            int(limits["external_independent_audit_exit_code"]),
            external_audit["returncode"]
            == int(limits["external_independent_audit_exit_code"]),
        )
    )
    independent_bvp, bvp_independence = load_independent_bvp()
    write_json(output_dir / "independent_bvp_identity.json", bvp_independence)
    gates.append(
        gate(
            "independent_bvp",
            "does_not_import_airvortex",
            bvp_independence["imports_airvortex"],
            False,
            not bvp_independence["imports_airvortex"],
        )
    )

    # Identity, symmetry, volume, exact state dynamics, and actual default run.
    identity_rows: list[dict[str, Any]] = []
    zero_config = make_config(0.0)
    zero_result = backend.run(zero_config)
    zero_profile = np.asarray(zero_result.observables["surface_profile_m"])
    zero_flat_error = float(
        np.max(np.abs(zero_profile - zero_config.container.liquid_depth_m))
    )
    zero_volume_error = float(
        np.max(np.abs(zero_result.observables["liquid_volume_error_m3"]))
    )
    identity_rows.extend(
        [
            {
                "test": "zero_speed_flat_surface",
                "value": zero_flat_error,
                "unit": "m",
                "status": "PASS"
                if zero_flat_error <= limits["zero_speed_flat_error_m_max"]
                else "FAIL",
            },
            {
                "test": "zero_speed_volume_error",
                "value": zero_volume_error,
                "unit": "m3",
                "status": "RECORDED",
            },
        ]
    )
    gates.append(
        gate(
            "identity",
            "zero_speed_flat_error_m",
            zero_flat_error,
            limits["zero_speed_flat_error_m_max"],
            zero_flat_error <= limits["zero_speed_flat_error_m_max"],
        )
    )

    volume_config = make_config(850.0)
    volume_result = backend.run(volume_config)
    final_volume_mean = float(
        np.dot(
            volume_result.fields["radial_volume_weights"],
            volume_result.observables["surface_profile_m"][-1]
            - volume_config.container.liquid_depth_m,
        )
    )
    identity_rows.append(
        {
            "test": "nonlinear_discrete_radial_volume_mean",
            "value": final_volume_mean,
            "unit": "m",
            "status": "PASS"
            if abs(final_volume_mean)
            <= limits["nonlinear_discrete_volume_mean_m_max"]
            else "FAIL",
        }
    )
    gates.append(
        gate(
            "identity",
            "nonlinear_discrete_volume_mean_m",
            abs(final_volume_mean),
            limits["nonlinear_discrete_volume_mean_m_max"],
            abs(final_volume_mean)
            <= limits["nonlinear_discrete_volume_mean_m_max"],
        )
    )

    positive = backend.run(make_config(720.0, direction=1))
    negative_rotation = backend.run(make_config(720.0, direction=-1))
    reversal_surface = relative_l2(
        positive.observables["surface_profile_m"]
        - negative_rotation.observables["surface_profile_m"],
        [
            positive.observables["surface_profile_m"]
            - positive.fields["radial_volume_weights"]
            @ positive.observables["surface_profile_m"][-1],
            negative_rotation.observables["surface_profile_m"]
            - negative_rotation.fields["radial_volume_weights"]
            @ negative_rotation.observables["surface_profile_m"][-1],
        ],
    )
    reversal_c = relative_l2(
        positive.observables["burgers_C_m2_s"]
        + negative_rotation.observables["burgers_C_m2_s"],
        [positive.observables["burgers_C_m2_s"]],
    )
    for metric, value, threshold in (
        (
            "rotation_reversal_surface_relative_l2",
            reversal_surface,
            limits["rotation_reversal_surface_relative_l2_max"],
        ),
        (
            "rotation_reversal_C_antisymmetry_relative_l2",
            reversal_c,
            limits["rotation_reversal_C_antisymmetry_relative_l2_max"],
        ),
    ):
        identity_rows.append(
            {
                "test": metric,
                "value": value,
                "unit": "1",
                "status": "PASS" if value <= threshold else "FAIL",
            }
        )
        gates.append(gate("symmetry", metric, value, threshold, value <= threshold))

    step_config = make_config(
        600.0,
        initial_rpm=0.0,
        duration_s=0.8,
        output_interval_s=0.05,
    )
    step_result = backend.run(step_config)
    target_omega = 600.0 * 2.0 * math.pi / 60.0
    target_c = step_config.forward.circulation_coupling_m2 * target_omega
    expected_c = target_c * (
        1.0 - np.exp(-step_result.time_s / step_config.forward.spin_up_time_s)
    )
    step_error = relative_l2(
        step_result.observables["burgers_C_m2_s"] - expected_c, [expected_c]
    )
    identity_rows.append(
        {
            "test": "circulation_step_response_relative_l2",
            "value": step_error,
            "unit": "1",
            "status": "PASS"
            if step_error <= limits["circulation_step_response_relative_l2_max"]
            else "FAIL",
        }
    )
    gates.append(
        gate(
            "dynamics",
            "circulation_step_response_relative_l2",
            step_error,
            limits["circulation_step_response_relative_l2_max"],
            step_error <= limits["circulation_step_response_relative_l2_max"],
        )
    )

    default_started = time.perf_counter()
    default_result = backend.run(default_config)
    default_wall_time = time.perf_counter() - default_started
    default_frame_count = int(default_result.time_s.size)
    default_status = default_result.status.value
    default_end_time = float(default_result.time_s[-1])
    default_all_finite = all(
        np.isfinite(np.asarray(value)).all()
        for value in tuple(default_result.observables.values())
        + tuple(default_result.fields.values())
    )
    default_summary = {
        "status": default_status,
        "requested_duration_s": default_config.run.duration_s,
        "actual_end_time_s": default_end_time,
        "frame_count": default_frame_count,
        "wall_time_s": default_wall_time,
        "all_arrays_finite": default_all_finite,
        "critical_status": default_result.metadata["critical_status"],
        "critical_actual_rpm": default_result.metadata.get("critical_actual_rpm"),
        "linear_critical_actual_rpm": default_result.metadata[
            "linear_baseline_critical_actual_rpm"
        ],
        "surface_solution_regime": default_result.metadata[
            "surface_solution_regime"
        ],
        "final_funnel_depth_m": float(
            default_result.observables["funnel_depth_m"][-1]
        ),
        "final_profile_range_m": float(
            default_result.observables["surface_profile_range_m"][-1]
        ),
        "final_max_abs_slope": float(
            default_result.observables["max_abs_surface_slope"][-1]
        ),
        "metadata": default_result.metadata,
    }
    write_json(output_dir / "default_run_summary.json", default_summary)
    archive_result_arrays(output_dir, default_result, "default_run")
    scalar_names = [
        name
        for name, value in default_result.observables.items()
        if np.asarray(value).ndim == 1
        and np.asarray(value).shape == default_result.time_s.shape
    ]
    default_timeseries = []
    for index, time_value in enumerate(default_result.time_s):
        default_timeseries.append(
            {
                "frame": index,
                "time_s": float(time_value),
                **{
                    name: float(np.asarray(default_result.observables[name])[index])
                    for name in scalar_names
                },
            }
        )
    write_rows(output_dir / "default_timeseries.csv", default_timeseries)
    radius_default = np.asarray(default_result.fields["radius_m"])
    final_profiles = [
        {
            "radial_index": index,
            "r_m": float(radius),
            "surface_m": float(default_result.observables["surface_profile_m"][-1, index]),
            "linear_surface_m": float(
                default_result.observables["linear_surface_profile_m"][-1, index]
            ),
            "normalised_slope": float(
                default_result.fields["nonlinear_final_normalised_slope"][index]
            ),
            "curvature_m_inv": float(
                default_result.fields["nonlinear_final_curvature_m_inv"][index]
            ),
        }
        for index, radius in enumerate(radius_default)
    ]
    write_rows(output_dir / "default_final_profiles.csv", final_profiles)
    default_passed = (
        default_status == str(limits["default_run_status"])
        and default_frame_count == int(limits["default_run_frame_count"])
        and math.isclose(
            default_end_time,
            default_config.run.duration_s,
            rel_tol=0.0,
            abs_tol=1.0e-12,
        )
        and default_all_finite
    )
    gates.append(
        gate(
            "default_run",
            "complete_10s_501_frames_finite",
            {
                "status": default_status,
                "actual_end_time_s": default_end_time,
                "frame_count": default_frame_count,
                "all_finite": default_all_finite,
            },
            {
                "status": limits["default_run_status"],
                "actual_end_time_s": default_config.run.duration_s,
                "frame_count": limits["default_run_frame_count"],
                "all_finite": True,
            },
            default_passed,
        )
    )
    gates.append(
        gate(
            "performance",
            "default_run_wall_time_s_regression",
            default_wall_time,
            limits["default_run_wall_time_s_regression_max"],
            default_wall_time <= limits["default_run_wall_time_s_regression_max"],
        )
    )
    write_rows(output_dir / "identity_and_dynamics.csv", identity_rows)

    # The shared depth operator is checked against its literal definition and
    # against H1 on an identical sampled profile extracted from an H1 state.
    synthetic_radius = np.linspace(0.0, default_config.container.radius_m, 41)
    synthetic_surface = (
        0.055
        + 0.004 * np.square(synthetic_radius / default_config.container.radius_m)
        + 0.001 * np.sin(3.0 * synthetic_radius / default_config.container.radius_m)
    )
    expected_depth = float(
        np.mean(
            synthetic_surface[
                synthetic_radius >= 0.8 * default_config.container.radius_m
            ]
        )
        - np.min(
            synthetic_surface[
                synthetic_radius <= default_config.stir_bar.half_length_m
            ]
        )
    )
    implemented_depth = surface_depth_functional_m(
        synthetic_radius,
        synthetic_surface,
        default_config.stir_bar.half_length_m,
    )
    full_range = surface_profile_range_m(synthetic_surface)
    depth_rows = [
        {
            "route": "manual_literal",
            "shared_depth_m": expected_depth,
            "profile_range_m": full_range,
        },
        {
            "route": "forward_operator",
            "shared_depth_m": implemented_depth,
            "profile_range_m": full_range,
        },
    ]
    from airvortex.backends.high_fidelity import HighFidelityBackend
    from airvortex.numerics import initialise_rotating_bucket_phase, surface_profile

    h1_config = replace(default_config, backend=BackendMode.HIGH_FIDELITY)
    h1_backend = HighFidelityBackend()
    h1_grid = h1_backend.make_grid(h1_config)
    h1_state = h1_backend.initialise_state(h1_config, h1_grid)
    h1_state.phase = initialise_rotating_bucket_phase(
        h1_grid,
        h1_config.container.liquid_depth_m,
        8.0,
        h1_config.fluid.gravity_m_s2,
        1.5 * min(h1_grid.dr, h1_grid.dz),
    )
    h1_observed = h1_backend._surface_observables(
        h1_state, h1_config, h1_grid, h1_grid.integrate(h1_state.phase)
    )
    h1_profile = surface_profile(h1_state.phase, h1_grid)
    shared_on_h1_profile = surface_depth_functional_m(
        h1_grid.r_centres,
        h1_profile,
        h1_config.stir_bar.half_length_m,
    )
    h1_depth_difference = abs(
        shared_on_h1_profile - float(h1_observed["funnel_depth_m"])
    )
    depth_rows.extend(
        [
            {
                "route": "H1_observable",
                "shared_depth_m": float(h1_observed["funnel_depth_m"]),
                "profile_range_m": float(h1_observed["surface_profile_range_m"]),
            },
            {
                "route": "Forward_operator_on_H1_profile",
                "shared_depth_m": shared_on_h1_profile,
                "profile_range_m": surface_profile_range_m(h1_profile),
            },
        ]
    )
    write_rows(output_dir / "shared_depth_operator.csv", depth_rows)
    gates.extend(
        [
            gate(
                "depth_operator",
                "manual_definition_absolute_difference_m",
                abs(implemented_depth - expected_depth),
                2.0e-15,
                abs(implemented_depth - expected_depth) <= 2.0e-15,
            ),
            gate(
                "depth_operator",
                "H1_Forward_identical_profile_difference_m",
                h1_depth_difference,
                2.0e-15,
                h1_depth_difference <= 2.0e-15,
            ),
        ]
    )

    # Independent quadrature for the Burgers potential and finite-container G.
    unit_default = solve_unit_surface_response(default_config)
    selected_indices = np.unique(
        np.linspace(1, unit_default.radius_m.size - 1, 20, dtype=int)
    )
    potential_rows: list[dict[str, Any]] = []
    max_potential_relative_error = 0.0
    core_radius = default_config.forward.effective_core_radius_m
    for index in selected_indices:
        radius_value = float(unit_default.radius_m[index])

        def integrand(radial_value: float) -> float:
            if radial_value == 0.0:
                return 0.0
            return (
                -math.expm1(-(radial_value / core_radius) ** 2)
            ) ** 2 / radial_value**3

        independent_value, independent_error = quad(
            integrand,
            0.0,
            radius_value,
            epsabs=2.0e-10,
            epsrel=2.0e-13,
            limit=600,
        )
        implemented_value = float(
            unit_default.potential_per_circulation_sq[index]
        )
        relative_error = abs(implemented_value - independent_value) / max(
            abs(independent_value), TINY
        )
        max_potential_relative_error = max(
            max_potential_relative_error, relative_error
        )
        potential_rows.append(
            {
                "radial_index": int(index),
                "r_m": radius_value,
                "implemented_potential_per_C2_m-2": implemented_value,
                "independent_quad_potential_per_C2_m-2": independent_value,
                "quad_absolute_error_estimate": independent_error,
                "relative_difference": relative_error,
            }
        )
    write_rows(output_dir / "independent_potential_quadrature.csv", potential_rows)
    gates.append(
        gate(
            "analytic",
            "independent_potential_quadrature_relative_error",
            max_potential_relative_error,
            limits["independent_potential_quadrature_relative_error_max"],
            max_potential_relative_error
            <= limits["independent_potential_quadrature_relative_error_max"],
        )
    )

    x_container = (
        default_config.container.radius_m
        / default_config.forward.effective_core_radius_m
    ) ** 2
    finite_factors = independent_finite_container_factors(x_container)
    analytic_critical_c_sq = (
        static_gap_m
        * default_config.fluid.gravity_m_s2
        * default_config.forward.effective_core_radius_m**2
        / (math.log(2.0) * finite_factors["G"])
    )
    analytic_critical_rpm = (
        math.sqrt(analytic_critical_c_sq)
        / default_config.forward.circulation_coupling_m2
        * 60.0
        / (2.0 * math.pi)
    )
    finite_factors.update(
        {
            "analytic_zero_capillary_axis_contact_C2_m4_s2": analytic_critical_c_sq,
            "analytic_zero_capillary_axis_contact_rpm": analytic_critical_rpm,
            "interpretation": (
                "finite-cylinder zero-capillarity analytic limit relative to "
                "the conserved mean surface; not an experimental threshold"
            ),
        }
    )
    write_json(output_dir / "finite_container_G.json", finite_factors)

    sigma_zero_rows: list[dict[str, Any]] = []
    sigma_reference = 1.0e-10
    for points in (64, 128, 256, 512, 1024):
        config = make_config(
            1.0,
            radial_points=points,
            sigma=sigma_reference,
            relative_tolerance=1.0e-8,
            absolute_tolerance_m=1.0e-11,
        )
        unit = solve_unit_surface_response(config)
        solver = NonlinearSurfaceSolver(
            config,
            linear_radius_m=unit.radius_m,
            linear_surface_per_circulation_sq=unit.surface_per_circulation_sq,
        )
        forcing_at_analytic_contact = (
            config.fluid.liquid_density_kg_m3
            * analytic_critical_c_sq
            / (
                config.fluid.surface_tension_n_m
                * config.forward.effective_core_radius_m
            )
        )
        outer = solver.solve_capillary_singular_limit(
            forcing_at_analytic_contact
        )
        discrete_axis_depression = -float(outer.elevation_m[0])
        unit_outer = solver.solve_capillary_singular_limit(1.0)
        grid_critical_forcing = static_gap_m / -float(unit_outer.elevation_m[0])
        grid_outer_rpm = _rpm_from_forcing(config, grid_critical_forcing)
        linear_sigma_small_rpm = linear_critical_actual_rpm(config, unit)
        sigma_zero_rows.append(
            {
                "radial_points": points,
                "dr_m": solver.radial_spacing_m,
                "finite_sigma_layer_resolved": solver.passes_capillary_resolution_floor,
                "explicit_outer_regime": outer.solution_regime,
                "axis_depression_at_analytic_contact_m": discrete_axis_depression,
                "axis_depression_relative_error": abs(
                    discrete_axis_depression - static_gap_m
                )
                / static_gap_m,
                "explicit_outer_grid_critical_rpm": grid_outer_rpm,
                "explicit_outer_grid_rpm_relative_error": abs(
                    grid_outer_rpm - analytic_critical_rpm
                )
                / analytic_critical_rpm,
                "linear_small_sigma_critical_rpm": linear_sigma_small_rpm,
                "linear_small_sigma_rpm_relative_error": abs(
                    linear_sigma_small_rpm - analytic_critical_rpm
                )
                / analytic_critical_rpm,
            }
        )
    write_rows(output_dir / "sigma_zero_G_grid.csv", sigma_zero_rows)
    sigma_zero_finest_error = max(
        sigma_zero_rows[-1]["explicit_outer_grid_rpm_relative_error"],
        sigma_zero_rows[-1]["linear_small_sigma_rpm_relative_error"],
    )
    gates.append(
        gate(
            "analytic",
            "sigma_zero_G_axis_depression_and_rpm_finest_relative_error",
            sigma_zero_finest_error,
            limits["sigma_zero_G_axis_depression_relative_error_finest_max"],
            sigma_zero_finest_error
            <= limits["sigma_zero_G_axis_depression_relative_error_finest_max"],
        )
    )

    # Finite-sigma runs stay exact-curvature and approach the explicit outer
    # limit only as a resolved coupled sigma/grid sequence.
    resolved_sigma_rows: list[dict[str, Any]] = []
    circulation_100 = (
        default_config.forward.circulation_coupling_m2
        * 100.0
        * 2.0
        * math.pi
        / 60.0
    )
    for definition in protocol_v2["amendments"][
        "resolved_finite_sigma_sequence"
    ]:
        config = make_config(
            100.0,
            radial_points=int(definition["surface_radial_points"]),
            sigma=float(definition["surface_tension_n_m"]),
        )
        unit = solve_unit_surface_response(config)
        solver = NonlinearSurfaceSolver(
            config,
            linear_radius_m=unit.radius_m,
            linear_surface_per_circulation_sq=unit.surface_per_circulation_sq,
        )
        finite_sigma = solver.solve(circulation_100**2)
        forcing = solver.forcing_from_circulation_sq(circulation_100**2)
        explicit_outer = solver.solve_capillary_singular_limit(forcing)
        centre_error = abs(
            float(finite_sigma.elevation_m[0])
            - float(explicit_outer.elevation_m[0])
        )
        resolved_sigma_rows.append(
            {
                **definition,
                "capillary_length_m": solver.capillary_length_m,
                "dr_m": solver.radial_spacing_m,
                "capillary_length_to_spacing": solver.capillary_length_to_spacing,
                "finite_capillary_layer_resolved": solver.passes_capillary_resolution_floor,
                "finite_sigma_regime": finite_sigma.solution_regime,
                "explicit_reference_regime": explicit_outer.solution_regime,
                "finite_sigma_wall_q": float(finite_sigma.normalised_slope[-1]),
                "centre_difference_to_explicit_outer_m": centre_error,
                "finite_sigma_axis_elevation_m": float(finite_sigma.elevation_m[0]),
                "outer_axis_elevation_m": float(explicit_outer.elevation_m[0]),
            }
        )
    write_rows(output_dir / "resolved_finite_sigma_sequence.csv", resolved_sigma_rows)
    centre_errors = [
        row["centre_difference_to_explicit_outer_m"] for row in resolved_sigma_rows
    ]
    resolved_sigma_passed = (
        all(
            row["finite_capillary_layer_resolved"]
            and row["finite_sigma_regime"] == "EXACT_NONLINEAR_CURVATURE"
            and row["explicit_reference_regime"]
            == "CAPILLARY_SINGULAR_LIMIT_OUTER"
            and abs(row["finite_sigma_wall_q"])
            <= protocol_v2["amendments"][
                "resolved_finite_sigma_wall_q_absolute_max"
            ]
            for row in resolved_sigma_rows
        )
        and all(
            centre_errors[index + 1] < centre_errors[index]
            for index in range(len(centre_errors) - 1)
        )
    )
    gates.append(
        gate(
            "finite_sigma",
            "resolved_sequence_exact_curvature_approaches_outer",
            {
                "centre_errors_m": centre_errors,
                "regimes": [row["finite_sigma_regime"] for row in resolved_sigma_rows],
                "wall_q": [row["finite_sigma_wall_q"] for row in resolved_sigma_rows],
            },
            {
                "strictly_decreasing_centre_errors": True,
                "finite_regime": "EXACT_NONLINEAR_CURVATURE",
                "wall_q_abs_max": protocol_v2["amendments"][
                    "resolved_finite_sigma_wall_q_absolute_max"
                ],
            },
            resolved_sigma_passed,
        )
    )

    underresolution_rows: list[dict[str, Any]] = []
    for definition in protocol_v2["amendments"][
        "underresolved_finite_sigma_cases"
    ]:
        config = make_config(
            100.0,
            radial_points=int(definition["surface_radial_points"]),
            sigma=float(definition["surface_tension_n_m"]),
        )
        unit = solve_unit_surface_response(config)
        solver = NonlinearSurfaceSolver(
            config,
            linear_radius_m=unit.radius_m,
            linear_surface_per_circulation_sq=unit.surface_per_circulation_sq,
        )
        normal_exception: str | None = None
        normal_traceback: str | None = None
        try:
            solver.solve_forcing(1.0)
        except Exception as error:  # exact type is archived below
            normal_exception = type(error).__name__
            normal_traceback = traceback.format_exc()
        explicit = solver.solve_capillary_singular_limit(1.0)
        underresolution_rows.append(
            {
                **definition,
                "capillary_length_to_spacing": solver.capillary_length_to_spacing,
                "finite_capillary_layer_resolved": solver.passes_capillary_resolution_floor,
                "normal_path_exception_type": normal_exception,
                "normal_path_traceback": normal_traceback,
                "explicit_outer_regime": explicit.solution_regime,
                "passed": (
                    not solver.passes_capillary_resolution_floor
                    and normal_exception
                    == protocol_v2["amendments"][
                        "underresolved_required_exception"
                    ]
                    and explicit.solution_regime
                    == protocol_v2["amendments"][
                        "explicit_outer_required_regime"
                    ]
                ),
            }
        )
    backend_underresolved_config = make_config(
        100.0, radial_points=256, sigma=1.0e-10
    )
    backend_underresolved_exception: str | None = None
    backend_underresolved_traceback: str | None = None
    try:
        backend.run(backend_underresolved_config)
    except Exception as error:
        backend_underresolved_exception = type(error).__name__
        backend_underresolved_traceback = traceback.format_exc()
    underresolution_evidence = {
        "solver_cases": underresolution_rows,
        "ForwardBackend_exception_type": backend_underresolved_exception,
        "ForwardBackend_traceback": backend_underresolved_traceback,
        "interpretation": (
            "expected fail-closed evidence for a positive finite sigma; not a "
            "physical instability and not a failed explicit asymptotic call"
        ),
    }
    write_json(output_dir / "underresolution_fail_closed.json", underresolution_evidence)
    underresolution_passed = (
        all(bool(row["passed"]) for row in underresolution_rows)
        and backend_underresolved_exception == "UnderresolvedSurfaceError"
    )
    gates.append(
        gate(
            "failure_policy",
            "underresolved_finite_sigma_fails_closed_outer_only_explicit",
            {
                "solver_exceptions": [
                    row["normal_path_exception_type"] for row in underresolution_rows
                ],
                "backend_exception": backend_underresolved_exception,
                "explicit_regimes": [
                    row["explicit_outer_regime"] for row in underresolution_rows
                ],
            },
            {
                "normal_exception": "UnderresolvedSurfaceError",
                "explicit_regime": "CAPILLARY_SINGULAR_LIMIT_OUTER",
            },
            underresolution_passed,
        )
    )

    # Default forcing sequence against a separately implemented collocation BVP.
    bvp_problem = independent_bvp.Problem(
        parameter_vector["Bo"],
        parameter_vector["L_R_over_c"],
        mesh_points=401,
        tolerance=3.0e-7,
        axis_fraction=1.0e-7,
    )
    backend_solver = NonlinearSurfaceSolver(
        default_config,
        linear_radius_m=unit_default.radius_m,
        linear_surface_per_circulation_sq=unit_default.surface_per_circulation_sq,
    )
    backend_seed = None
    bvp_seed = independent_bvp.static_solution(bvp_problem, nonlinear=True)
    forcing_rows: list[dict[str, Any]] = []
    forcing_profile_rows: list[dict[str, Any]] = []
    maximum_bvp_profile_relative_error = 0.0
    for forcing in protocol_v1["matrix"]["forcing_W"]:
        forcing = float(forcing)
        backend_surface = backend_solver.solve_forcing(
            forcing, initial=backend_seed
        )
        backend_seed = backend_surface
        bvp_seed = independent_bvp.advance_to(
            bvp_problem,
            forcing,
            nonlinear=True,
            seed=bvp_seed,
            maximum_step=4.0,
        )
        bvp_metrics = independent_bvp.metrics(bvp_seed)
        common_x = np.linspace(0.0, bvp_problem.radius_ratio, 3001)
        bvp_evaluation_x = np.maximum(common_x, bvp_problem.axis_cutoff)
        bvp_height = np.asarray(bvp_seed.evaluate(bvp_evaluation_x)[0])
        backend_height = np.interp(
            common_x,
            backend_solver.output_x,
            backend_surface.elevation_m / backend_solver.core_radius_m,
        )
        common_radius = common_x * backend_solver.core_radius_m
        # Remove independent quadrature/interpolation datum drift before the
        # shape norm; volume residual is reported separately and not hidden.
        backend_centred = backend_height - area_mean(backend_height, common_radius)
        bvp_centred = bvp_height - area_mean(bvp_height, common_radius)
        difference = backend_centred - bvp_centred
        profile_relative_error = area_rms(difference, common_radius) / max(
            area_rms(bvp_centred, common_radius), TINY
        )
        maximum_bvp_profile_relative_error = max(
            maximum_bvp_profile_relative_error, profile_relative_error
        )
        circulation_sq = backend_solver.circulation_sq_from_forcing(forcing)
        linear_elevation = (
            circulation_sq * unit_default.surface_per_circulation_sq
        )
        linear_axis_depth_c = -float(linear_elevation[0]) / core_radius
        backend_axis_depth_c = -float(backend_surface.elevation_m[0]) / core_radius
        bvp_axis_depth_c = -float(bvp_height[0])
        forcing_rows.append(
            {
                "W": forcing,
                "backend_axis_depth_over_c": backend_axis_depth_c,
                "independent_bvp_axis_depth_over_c": bvp_axis_depth_c,
                "linear_axis_depth_over_c": linear_axis_depth_c,
                "backend_minus_bvp_axis_depth_over_c": (
                    backend_axis_depth_c - bvp_axis_depth_c
                ),
                "profile_relative_axisymmetric_l2": profile_relative_error,
                "backend_max_abs_slope": backend_surface.maximum_absolute_slope,
                "backend_max_abs_q": backend_surface.maximum_absolute_normalised_slope,
                "backend_collocation_rms": backend_surface.maximum_collocation_rms_residual,
                "backend_boundary_residual": backend_surface.boundary_residual_norm,
                "backend_collocation_nodes": backend_surface.collocation_nodes,
                "backend_discrete_volume_residual_m3": backend_surface.volume_residual_m3,
                "independent_bvp_max_abs_slope": bvp_metrics.maximum_abs_slope,
                "independent_bvp_max_abs_q": bvp_metrics.maximum_abs_q,
                "independent_bvp_rms_residual": bvp_metrics.rms_residual,
                "independent_bvp_volume_mean_dimensionless": bvp_metrics.volume_mean,
                "independent_bvp_pressure_invariant_error": (
                    bvp_metrics.pressure_invariant_error
                ),
            }
        )
        sample_indices = np.unique(
            np.linspace(0, common_x.size - 1, 301, dtype=int)
        )
        for index in sample_indices:
            forcing_profile_rows.append(
                {
                    "W": forcing,
                    "r_over_c": float(common_x[index]),
                    "backend_elevation_over_c": float(backend_centred[index]),
                    "independent_bvp_elevation_over_c": float(bvp_centred[index]),
                    "difference_over_c": float(difference[index]),
                }
            )
    write_rows(output_dir / "forcing_cross_comparison.csv", forcing_rows)
    write_rows(output_dir / "forcing_cross_profiles.csv", forcing_profile_rows)
    gates.append(
        gate(
            "independent_bvp",
            "forcing_profile_relative_axisymmetric_l2_max",
            maximum_bvp_profile_relative_error,
            limits["independent_bvp_profile_relative_axisymmetric_l2_max"],
            maximum_bvp_profile_relative_error
            <= limits["independent_bvp_profile_relative_axisymmetric_l2_max"],
        )
    )

    # Weak forcing: exact-curvature minus linear curvature is O(W^3).
    weak_config = make_config(
        1.0,
        radial_points=512,
        relative_tolerance=1.0e-8,
        absolute_tolerance_m=1.0e-11,
    )
    weak_unit = solve_unit_surface_response(weak_config)
    weak_solver = NonlinearSurfaceSolver(
        weak_config,
        linear_radius_m=weak_unit.radius_m,
        linear_surface_per_circulation_sq=weak_unit.surface_per_circulation_sq,
    )
    weak_problem = independent_bvp.Problem(
        parameter_vector["Bo"],
        parameter_vector["L_R_over_c"],
        mesh_points=801,
        tolerance=8.0e-9,
        axis_fraction=1.0e-8,
    )
    weak_backend_seed = None
    weak_bvp_seed = independent_bvp.static_solution(weak_problem, nonlinear=True)
    weak_linear_bvp_seed = independent_bvp.static_solution(
        weak_problem, nonlinear=False
    )
    weak_rows: list[dict[str, Any]] = []
    for forcing in (0.1, 0.2, 0.4):
        weak_backend_seed = weak_solver.solve_forcing(
            forcing, initial=weak_backend_seed
        )
        circulation_sq = weak_solver.circulation_sq_from_forcing(forcing)
        linear_elevation = circulation_sq * weak_unit.surface_per_circulation_sq
        backend_correction = (
            -float(weak_backend_seed.elevation_m[0])
            + float(linear_elevation[0])
        ) / core_radius
        linear_axis_depth = -float(linear_elevation[0]) / core_radius
        weak_bvp_seed = independent_bvp.advance_to(
            weak_problem,
            forcing,
            nonlinear=True,
            seed=weak_bvp_seed,
            maximum_step=0.4,
        )
        weak_linear_bvp_seed = independent_bvp.advance_to(
            weak_problem,
            forcing,
            nonlinear=False,
            seed=weak_linear_bvp_seed,
            maximum_step=0.4,
        )
        bvp_nl_axis = float(
            weak_bvp_seed.raw.sol(weak_problem.axis_cutoff)[0]
        )
        bvp_lin_axis = float(
            weak_linear_bvp_seed.raw.sol(weak_problem.axis_cutoff)[0]
        )
        bvp_correction = -bvp_nl_axis + bvp_lin_axis
        weak_rows.append(
            {
                "W": forcing,
                "backend_nonlinear_minus_linear_axis_depth_over_c": backend_correction,
                "backend_correction_over_W3": backend_correction / forcing**3,
                "backend_absolute_relative_difference_from_linear": abs(
                    backend_correction
                )
                / max(abs(linear_axis_depth), TINY),
                "independent_bvp_nonlinear_minus_linear_axis_depth_over_c": bvp_correction,
                "independent_bvp_correction_over_W3": bvp_correction / forcing**3,
            }
        )
    write_rows(output_dir / "weak_forcing_W3.csv", weak_rows)
    backend_w3 = [row["backend_correction_over_W3"] for row in weak_rows]
    bvp_w3 = [row["independent_bvp_correction_over_W3"] for row in weak_rows]

    def relative_spread(values: Sequence[float]) -> float:
        return (max(values) - min(values)) / max(abs(values[-1]), TINY)

    backend_w3_spread = relative_spread(backend_w3)
    bvp_w3_spread = relative_spread(bvp_w3)
    backend_weak_relative = weak_rows[0][
        "backend_absolute_relative_difference_from_linear"
    ]
    backend_weak_limit = protocol_v2["amendments"][
        "backend_weak_limit_relative_axis_depth_difference_max_at_W0p1"
    ]
    gates.append(
        gate(
            "weak_forcing",
            "independent_W3_and_backend_linear_limit",
            {
                "independent_bvp_W3_relative_spread": bvp_w3_spread,
                "backend_W0p1_relative_difference_from_linear": backend_weak_relative,
            },
            {
                "independent_bvp_W3_relative_spread_max": limits[
                    "weak_nonlinear_W3_relative_spread_max"
                ],
                "backend_W0p1_relative_difference_from_linear_max": backend_weak_limit,
            },
            (
                bvp_w3_spread
                <= limits["weak_nonlinear_W3_relative_spread_max"]
                and backend_weak_relative <= backend_weak_limit
            ),
        )
    )
    if backend_w3_spread > limits["weak_nonlinear_W3_relative_spread_max"]:
        negative.append(
            {
                "source": "backend_weak_W3_resolution",
                "status": protocol_v2["amendments"][
                    "backend_weak_W3_unresolved_status"
                ],
                "W": [row["W"] for row in weak_rows],
                "correction_over_W3": backend_w3,
                "relative_spread": backend_w3_spread,
                "interpretation": (
                    "the cubic correction is below the declared production "
                    "discretisation/collocation accuracy at these loads; the "
                    "independent high-accuracy BVP resolves the asymptotic"
                ),
            }
        )

    # Default nonlinear and linear contact, independently recomputed.
    critical_backend = solve_nonlinear_critical_surface(
        default_config, unit_default, backend_solver
    )
    backend_linear_rpm = linear_critical_actual_rpm(default_config, unit_default)
    backend_linear_forcing = _forcing_from_rpm(default_config, backend_linear_rpm)
    bvp_linear_forcing, _ = independent_bvp.touch_threshold(
        bvp_problem,
        parameter_vector["static_gap_ratio_S_over_c"],
        nonlinear=False,
    )
    bvp_nonlinear_forcing, bvp_critical_surface = independent_bvp.touch_threshold(
        bvp_problem,
        parameter_vector["static_gap_ratio_S_over_c"],
        nonlinear=True,
    )
    bvp_critical_rpm = _rpm_from_forcing(default_config, bvp_nonlinear_forcing)
    bvp_linear_rpm = _rpm_from_forcing(default_config, bvp_linear_forcing)
    critical_rows = [
        {
            "route": "backend_discrete_exact_curvature",
            "surface_radial_points_or_mesh": default_config.forward.surface_radial_points,
            "relative_tolerance": default_config.forward.relative_tolerance,
            "critical_W": critical_backend.dimensionless_forcing,
            "critical_actual_rpm": critical_backend.actual_rpm,
            "max_abs_q": critical_backend.surface.maximum_absolute_normalised_slope,
            "max_abs_slope": critical_backend.surface.maximum_absolute_slope,
            "height_residual_m": critical_backend.height_residual_m,
        },
        {
            "route": "independent_BVP_continuum",
            "surface_radial_points_or_mesh": bvp_problem.mesh_points,
            "relative_tolerance": bvp_problem.tolerance,
            "critical_W": bvp_nonlinear_forcing,
            "critical_actual_rpm": bvp_critical_rpm,
            "max_abs_q": independent_bvp.metrics(bvp_critical_surface).maximum_abs_q,
            "max_abs_slope": independent_bvp.metrics(
                bvp_critical_surface
            ).maximum_abs_slope,
            "height_residual_m": (
                float(
                    bvp_critical_surface.raw.sol(bvp_problem.axis_cutoff)[0]
                )
                + parameter_vector["static_gap_ratio_S_over_c"]
            )
            * core_radius,
        },
        {
            "route": "backend_discrete_linear_baseline",
            "surface_radial_points_or_mesh": default_config.forward.surface_radial_points,
            "relative_tolerance": "linear solve",
            "critical_W": backend_linear_forcing,
            "critical_actual_rpm": backend_linear_rpm,
            "max_abs_q": "",
            "max_abs_slope": "",
            "height_residual_m": "",
        },
        {
            "route": "independent_BVP_linear_continuum",
            "surface_radial_points_or_mesh": bvp_problem.mesh_points,
            "relative_tolerance": bvp_problem.tolerance,
            "critical_W": bvp_linear_forcing,
            "critical_actual_rpm": bvp_linear_rpm,
            "max_abs_q": "",
            "max_abs_slope": "",
            "height_residual_m": "",
        },
    ]
    write_rows(output_dir / "critical_cross_routes.csv", critical_rows)
    critical_w_relative_difference = abs(
        critical_backend.dimensionless_forcing - bvp_nonlinear_forcing
    ) / bvp_nonlinear_forcing
    critical_rpm_relative_difference = abs(
        critical_backend.actual_rpm - bvp_critical_rpm
    ) / bvp_critical_rpm
    gates.append(
        gate(
            "independent_bvp",
            "critical_W_relative_difference",
            critical_w_relative_difference,
            limits["independent_bvp_critical_W_relative_difference_max"],
            critical_w_relative_difference
            <= limits["independent_bvp_critical_W_relative_difference_max"],
        )
    )

    # Lightweight radial-grid and tolerance sequences for the decision-facing
    # nonlinear contact threshold.  Non-monotone sequences remain negative.
    grid_rows: list[dict[str, Any]] = []
    for points in protocol_v2["amendments"]["backend_radial_points"]:
        config = make_config(1.0, radial_points=int(points))
        unit = solve_unit_surface_response(config)
        result = solve_nonlinear_critical_surface(config, unit)
        grid_rows.append(
            {
                "radial_points": int(points),
                "dr_m": config.container.radius_m / (int(points) - 1),
                "relative_tolerance": config.forward.relative_tolerance,
                "absolute_tolerance_m": config.forward.absolute_tolerance,
                "critical_W": result.dimensionless_forcing,
                "critical_actual_rpm": result.actual_rpm,
                "height_residual_m": result.height_residual_m,
                "max_abs_q": result.surface.maximum_absolute_normalised_slope,
                "collocation_rms": result.surface.maximum_collocation_rms_residual,
                "collocation_nodes": result.surface.collocation_nodes,
            }
        )
    grid_diagnostics = sequence_diagnostics(
        [row["critical_actual_rpm"] for row in grid_rows], refinement_ratio=2.0
    )
    for index, row in enumerate(grid_rows):
        row["difference_to_finest_rpm"] = (
            row["critical_actual_rpm"] - grid_rows[-1]["critical_actual_rpm"]
        )
        row["sequence_status"] = grid_diagnostics["status"]
    write_rows(output_dir / "backend_radial_grid_sequence.csv", grid_rows)
    write_json(output_dir / "backend_radial_grid_diagnostics.json", grid_diagnostics)
    if grid_diagnostics["status"] != "CONVERGENCE_TREND":
        negative.append(
            {
                "source": "backend_radial_grid_sequence",
                "metric": "critical_actual_rpm",
                **grid_diagnostics,
                "interpretation": "numerical convergence not demonstrated; not a physical failure",
            }
        )

    tolerance_rows: list[dict[str, Any]] = []
    for pair in protocol_v1["matrix"]["backend_tolerance_pairs"]:
        config = make_config(
            1.0,
            radial_points=256,
            relative_tolerance=float(pair["relative"]),
            absolute_tolerance_m=float(pair["absolute_m"]),
        )
        unit = solve_unit_surface_response(config)
        result = solve_nonlinear_critical_surface(config, unit)
        tolerance_rows.append(
            {
                "radial_points": 256,
                "relative_tolerance": pair["relative"],
                "absolute_tolerance_m": pair["absolute_m"],
                "critical_W": result.dimensionless_forcing,
                "critical_actual_rpm": result.actual_rpm,
                "height_residual_m": result.height_residual_m,
                "max_abs_q": result.surface.maximum_absolute_normalised_slope,
                "collocation_rms": result.surface.maximum_collocation_rms_residual,
                "collocation_nodes": result.surface.collocation_nodes,
            }
        )
    tolerance_diagnostics = sequence_diagnostics(
        [row["critical_actual_rpm"] for row in tolerance_rows]
    )
    for row in tolerance_rows:
        row["difference_to_tightest_rpm"] = (
            row["critical_actual_rpm"]
            - tolerance_rows[-1]["critical_actual_rpm"]
        )
        row["sequence_status"] = tolerance_diagnostics["status"]
    write_rows(output_dir / "backend_tolerance_sequence.csv", tolerance_rows)
    write_json(output_dir / "backend_tolerance_diagnostics.json", tolerance_diagnostics)
    if tolerance_diagnostics["status"] != "CONVERGENCE_TREND":
        negative.append(
            {
                "source": "backend_tolerance_sequence",
                "metric": "critical_actual_rpm",
                **tolerance_diagnostics,
                "interpretation": "tolerance convergence not demonstrated; not a physical failure",
            }
        )

    bvp_refinement_rows: list[dict[str, Any]] = []
    for definition in protocol_v1["matrix"]["independent_bvp_refinement"]:
        problem = independent_bvp.Problem(
            parameter_vector["Bo"],
            parameter_vector["L_R_over_c"],
            mesh_points=int(definition["mesh_points"]),
            tolerance=float(definition["tolerance"]),
            axis_fraction=float(definition["axis_fraction"]),
        )
        solution = independent_bvp.advance_to(problem, 80.0, nonlinear=True)
        observed = independent_bvp.metrics(solution)
        bvp_refinement_rows.append(
            {
                **definition,
                "W": 80.0,
                "axis_depth_over_c": -observed.minimum,
                "profile_range_over_c": observed.depth,
                "max_abs_slope": observed.maximum_abs_slope,
                "max_abs_q": observed.maximum_abs_q,
                "volume_mean": observed.volume_mean,
                "rms_residual": observed.rms_residual,
            }
        )
    bvp_refinement_diagnostics = sequence_diagnostics(
        [row["axis_depth_over_c"] for row in bvp_refinement_rows]
    )
    bvp_max_change = max(
        bvp_refinement_diagnostics["successive_absolute_changes"], default=0.0
    )
    bvp_equivalence_limit = (
        protocol_v2["amendments"][
            "bvp_refinement_equivalent_if_max_change_below_fraction_of_coarsest_tolerance"
        ]
        * float(bvp_refinement_rows[0]["tolerance"])
    )
    if bvp_max_change <= bvp_equivalence_limit:
        bvp_refinement_diagnostics["raw_signed_sequence_status"] = (
            bvp_refinement_diagnostics["status"]
        )
        bvp_refinement_diagnostics["status"] = "TOLERANCE_LIMITED_EQUIVALENT"
        bvp_refinement_diagnostics["equivalence_limit"] = bvp_equivalence_limit
    write_rows(output_dir / "independent_bvp_refinement.csv", bvp_refinement_rows)
    write_json(
        output_dir / "independent_bvp_refinement_diagnostics.json",
        bvp_refinement_diagnostics,
    )
    if bvp_refinement_diagnostics["status"] not in {
        "CONVERGENCE_TREND",
        "TOLERANCE_LIMITED_EQUIVALENT",
    }:
        negative.append(
            {
                "source": "independent_bvp_refinement",
                "metric": "axis_depth_over_c_at_W80",
                **bvp_refinement_diagnostics,
                "interpretation": "independent-route refinement not monotone",
            }
        )

    grid_last_change_rpm = abs(
        grid_rows[-1]["critical_actual_rpm"]
        - grid_rows[-2]["critical_actual_rpm"]
    )
    tolerance_last_change_rpm = abs(
        tolerance_rows[-1]["critical_actual_rpm"]
        - tolerance_rows[-2]["critical_actual_rpm"]
    )
    route_difference_rpm = abs(critical_backend.actual_rpm - bvp_critical_rpm)
    numerical_uncertainty_rpm = max(
        grid_last_change_rpm, tolerance_last_change_rpm, route_difference_rpm
    )
    numerical_uncertainty = {
        "backend_default_critical_rpm": critical_backend.actual_rpm,
        "independent_bvp_critical_rpm": bvp_critical_rpm,
        "grid_last_change_rpm": grid_last_change_rpm,
        "tolerance_last_change_rpm": tolerance_last_change_rpm,
        "backend_to_independent_route_difference_rpm": route_difference_rpm,
        "conservative_numerical_envelope_rpm": numerical_uncertainty_rpm,
        "relative_envelope": numerical_uncertainty_rpm
        / critical_backend.actual_rpm,
        "scope": "discretisation/tolerance/independent-route numerical spread only",
        "not_included": [
            "experimental uncertainty",
            "parameter calibration uncertainty",
            "model-form uncertainty",
            "post-contact physics",
        ],
    }
    write_json(output_dir / "critical_numerical_uncertainty.json", numerical_uncertainty)

    # Independent exact-energy discretisation at a representative nonlinear W.
    energy_rows: list[dict[str, Any]] = []
    energy_profile_rows: list[dict[str, Any]] = []
    energy_config = make_config(1.0, radial_points=512)
    energy_unit = solve_unit_surface_response(energy_config)
    energy_backend_solver = NonlinearSurfaceSolver(
        energy_config,
        linear_radius_m=energy_unit.radius_m,
        linear_surface_per_circulation_sq=energy_unit.surface_per_circulation_sq,
    )
    energy_backend_reference = energy_backend_solver.solve_forcing(80.0)
    energy_circulation = math.sqrt(
        energy_backend_solver.circulation_sq_from_forcing(80.0)
    )
    energy_previous: np.ndarray | None = None
    finest_energy_relative_error = math.inf
    for points in protocol_v1["matrix"]["energy_radial_points"]:
        solved = energy_solution(
            energy_config,
            energy_circulation,
            points=int(points),
            initial=energy_previous,
        )
        energy_previous = np.asarray(solved["elevation_m"])
        backend_on_energy = np.interp(
            solved["radius_m"],
            energy_backend_reference.radius_m,
            energy_backend_reference.elevation_m,
        )
        difference = np.asarray(solved["elevation_m"]) - backend_on_energy
        profile_relative_error = area_rms(
            difference, solved["radius_m"]
        ) / max(area_rms(backend_on_energy, solved["radius_m"]), TINY)
        finest_energy_relative_error = profile_relative_error
        energy_rows.append(
            {
                "W": 80.0,
                "energy_radial_points": int(points),
                "optimizer_success": solved["success"],
                "optimizer_status": solved["status"],
                "optimizer_message": solved["message"],
                "iterations": solved["iterations"],
                "function_evaluations": solved["function_evaluations"],
                "scaled_projected_gradient_infinity": solved[
                    "scaled_projected_gradient_infinity"
                ],
                "discrete_volume_mean_m": solved["discrete_volume_mean_m"],
                "wall_last_segment_slope": solved["wall_last_segment_slope"],
                "profile_relative_axisymmetric_l2_vs_backend": profile_relative_error,
                "axis_elevation_difference_m": float(
                    solved["elevation_m"][0] - backend_on_energy[0]
                ),
            }
        )
        sample_indices = np.unique(
            np.linspace(0, int(points) - 1, min(int(points), 129), dtype=int)
        )
        for index in sample_indices:
            energy_profile_rows.append(
                {
                    "W": 80.0,
                    "energy_radial_points": int(points),
                    "r_m": float(solved["radius_m"][index]),
                    "energy_elevation_m": float(solved["elevation_m"][index]),
                    "backend_elevation_m": float(backend_on_energy[index]),
                    "difference_m": float(difference[index]),
                }
            )
    write_rows(output_dir / "independent_energy_comparison.csv", energy_rows)
    write_rows(output_dir / "independent_energy_profiles.csv", energy_profile_rows)
    energy_passed = (
        all(bool(row["optimizer_success"]) for row in energy_rows)
        and finest_energy_relative_error
        <= limits[
            "independent_energy_profile_relative_axisymmetric_l2_finest_max"
        ]
    )
    gates.append(
        gate(
            "independent_energy",
            "finest_profile_relative_axisymmetric_l2",
            finest_energy_relative_error,
            limits[
                "independent_energy_profile_relative_axisymmetric_l2_finest_max"
            ],
            energy_passed,
        )
    )
    if not all(bool(row["optimizer_success"]) for row in energy_rows):
        negative.append(
            {
                "source": "independent_energy",
                "status": "OPTIMIZER_NONCONVERGENCE_RETAINED",
                "rows": energy_rows,
            }
        )

    # Applicability-domain evidence: natural continuation is archived without
    # calling its endpoint an instability, and synthetic guards prove q->1 and
    # collocation failures cannot fabricate a linear answer.
    domain_rows: list[dict[str, Any]] = []
    domain_solver = NonlinearSurfaceSolver(
        default_config,
        linear_radius_m=unit_default.radius_m,
        linear_surface_per_circulation_sq=unit_default.surface_per_circulation_sq,
    )
    domain_seed = None
    natural_failure: dict[str, Any] | None = None
    for forcing in protocol_v1["matrix"]["natural_domain_forcing_W"]:
        try:
            domain_seed = domain_solver.solve_forcing(
                float(forcing), initial=domain_seed
            )
            domain_rows.append(
                {
                    "W": forcing,
                    "status": "CERTIFIED_SINGLE_VALUED_GRAPH",
                    "max_abs_q": domain_seed.maximum_absolute_normalised_slope,
                    "max_abs_slope": domain_seed.maximum_absolute_slope,
                    "collocation_rms": domain_seed.maximum_collocation_rms_residual,
                    "boundary_residual": domain_seed.boundary_residual_norm,
                    "exception_type": "",
                    "message": "",
                }
            )
        except Exception as error:
            natural_failure = {
                "W": forcing,
                "status": "GRAPH_CONTINUATION_STOPPED",
                "exception_type": type(error).__name__,
                "message": str(error),
                "traceback": traceback.format_exc(),
                "interpretation": (
                    "numerical/single-valued-graph domain boundary; not a "
                    "physical hydrodynamic instability"
                ),
            }
            domain_rows.append(
                {
                    "W": forcing,
                    "status": natural_failure["status"],
                    "max_abs_q": "",
                    "max_abs_slope": "",
                    "collocation_rms": "",
                    "boundary_residual": "",
                    "exception_type": natural_failure["exception_type"],
                    "message": natural_failure["message"],
                }
            )
            break
    write_rows(output_dir / "natural_graph_domain_sweep.csv", domain_rows)
    if natural_failure is not None:
        negative.append({"source": "natural_graph_domain_sweep", **natural_failure})

    # Synthetic q-domain poison, evaluated between collocation nodes too.
    q_config = make_config(1.0, radial_points=80)
    q_unit = solve_unit_surface_response(q_config)
    q_solver = NonlinearSurfaceSolver(
        q_config,
        linear_radius_m=q_unit.radius_m,
        linear_surface_per_circulation_sq=q_unit.surface_per_circulation_sq,
    )
    q_margin = max(2.0e-8, 20.0 * q_solver.collocation_tolerance)
    poisoned_q = 1.0 - 0.5 * q_margin
    poisoned_angle = math.atanh(poisoned_q)
    fake_x = q_solver.base_mesh.copy()
    fake_y = np.zeros((3, fake_x.size), dtype=np.float64)
    fake_y[1] = poisoned_angle

    def fake_solution(coordinate: np.ndarray) -> np.ndarray:
        values = np.zeros((3, np.asarray(coordinate).size), dtype=np.float64)
        values[1] = poisoned_angle
        return values

    q_fake = SimpleNamespace(
        success=True,
        status=0,
        message="synthetic near-vertical q poison",
        x=fake_x,
        y=fake_y,
        p=np.zeros(1, dtype=np.float64),
        rms_residuals=np.zeros(fake_x.size - 1, dtype=np.float64),
        niter=1,
        sol=fake_solution,
    )
    q_exception: str | None = None
    q_message: str | None = None
    with patch.object(nonlinear_module, "solve_bvp", return_value=q_fake):
        try:
            q_solver.solve_forcing(1.0)
        except Exception as error:
            q_exception = type(error).__name__
            q_message = str(error)
    q_poison_passed = q_exception == str(limits["q_domain_poison_must_raise"])

    failed_bvp = SimpleNamespace(
        success=False,
        status=1,
        message="synthetic collocation failure for no-fallback audit",
    )
    direct_failure_exception: str | None = None
    direct_failure_message: str | None = None
    with patch.object(nonlinear_module, "solve_bvp", return_value=failed_bvp):
        try:
            q_solver.solve_forcing(2.0)
        except Exception as error:
            direct_failure_exception = type(error).__name__
            direct_failure_message = str(error)

    backend_failure_config = make_config(
        101.0,
        radial_points=83,
        relative_tolerance=3.0e-6,
        absolute_tolerance_m=2.0e-9,
        duration_s=0.02,
        output_interval_s=0.02,
    )
    backend_failure_exception: str | None = None
    backend_failure_message: str | None = None
    backend_fabricated_result = False
    with patch.object(nonlinear_module, "solve_bvp", return_value=failed_bvp):
        try:
            backend.run(backend_failure_config)
            backend_fabricated_result = True
        except Exception as error:
            backend_failure_exception = type(error).__name__
            backend_failure_message = str(error)

    deep_config = make_config(
        50.0,
        radial_points=128,
        relative_tolerance=1.0e-5,
        absolute_tolerance_m=1.0e-8,
        liquid_depth_m=0.60,
        duration_s=0.02,
        output_interval_s=0.02,
    )
    deep_result = backend.run(deep_config)
    deep_passed = (
        deep_result.status.value == "completed"
        and deep_result.metadata["critical_status"] == "UNAVAILABLE_GRAPH_BRANCH"
        and "critical_actual_rpm" not in deep_result.observables
        and deep_result.metadata["domain_status"] == "IN_MODEL_DOMAIN"
        and float(deep_result.observables["minimum_gap_m"][-1]) > 0.0
    )
    failure_policy = {
        "q_domain_poison": {
            "poisoned_q": poisoned_q,
            "certification_margin": q_margin,
            "exception_type": q_exception,
            "message": q_message,
            "passed": q_poison_passed,
        },
        "direct_collocation_failure": {
            "exception_type": direct_failure_exception,
            "message": direct_failure_message,
            "passed": direct_failure_exception == "NonlinearSurfaceError",
        },
        "ForwardBackend_collocation_failure": {
            "exception_type": backend_failure_exception,
            "message": backend_failure_message,
            "fabricated_result_returned": backend_fabricated_result,
            "passed": (
                not backend_fabricated_result
                and backend_failure_exception == "NonlinearSurfaceError"
            ),
        },
        "deep_vessel_low_load": {
            "run_status": deep_result.status.value,
            "critical_status": deep_result.metadata["critical_status"],
            "critical_domain_reason": deep_result.metadata[
                "critical_domain_reason"
            ],
            "critical_observable_present": "critical_actual_rpm"
            in deep_result.observables,
            "domain_status": deep_result.metadata["domain_status"],
            "minimum_gap_m": float(deep_result.observables["minimum_gap_m"][-1]),
            "passed": deep_passed,
        },
    }
    write_json(output_dir / "failure_and_domain_policy.json", failure_policy)
    no_fallback_passed = (
        failure_policy["direct_collocation_failure"]["passed"]
        and failure_policy["ForwardBackend_collocation_failure"]["passed"]
    )
    gates.extend(
        [
            gate(
                "failure_policy",
                "q_near_one_domain_poison",
                q_exception,
                limits["q_domain_poison_must_raise"],
                q_poison_passed,
            ),
            gate(
                "failure_policy",
                "collocation_failure_raises_without_linear_fallback",
                {
                    "direct_exception": direct_failure_exception,
                    "backend_exception": backend_failure_exception,
                    "fabricated_result": backend_fabricated_result,
                },
                {
                    "exception": "NonlinearSurfaceError",
                    "fabricated_result": False,
                },
                no_fallback_passed,
            ),
            gate(
                "applicability_domain",
                "deep_vessel_low_load_completes_with_critical_unavailable",
                failure_policy["deep_vessel_low_load"],
                {
                    "run_status": "completed",
                    "critical_status": "UNAVAILABLE_GRAPH_BRANCH",
                    "critical_observable_present": False,
                    "domain_status": "IN_MODEL_DOMAIN",
                },
                deep_passed,
            ),
        ]
    )

    # Final evidence integrity and summary.
    all_output_csv_finite = all(
        row.get("status") != "FAIL" for row in identity_rows
    ) and default_all_finite
    gates.append(
        gate(
            "archive",
            "all_archived_backend_arrays_finite",
            all_output_csv_finite,
            True,
            all_output_csv_finite,
        )
    )
    source_end = input_manifest()
    source_unchanged = source_end["tree_sha256"] == source_start["tree_sha256"]
    gates.append(
        gate(
            "source",
            "input_tree_unchanged_during_worker",
            source_end["tree_sha256"],
            source_start["tree_sha256"],
            source_unchanged,
        )
    )
    if not source_unchanged:
        negative.append(
            {
                "source": "source_integrity",
                "status": "INVALID_SOURCE_CHANGED_DURING_RUN",
                "start": source_start["tree_sha256"],
                "end": source_end["tree_sha256"],
            }
        )
    hard_failures = [row for row in gates if row["status"] == "FAIL"]
    negative.extend(
        {"source": "hard_gate", **row} for row in hard_failures
    )
    write_json(output_dir / "acceptance.json", gates)
    write_json(output_dir / "negative_results.json", negative)
    write_json(output_dir / "input_manifest_end.json", source_end)
    wall_time = time.perf_counter() - wall_start
    result_summary = {
        "schema": SCHEMA,
        "status": (
            "INVALID_SOURCE_CHANGED_DURING_RUN"
            if not source_unchanged
            else (
                "completed_with_hard_failures"
                if hard_failures
                else (
                    "completed_with_negative_or_inconclusive_results"
                    if negative
                    else "completed"
                )
            )
        ),
        "physical_validation_claimed": False,
        "started_utc": started_utc,
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "wall_time_s": wall_time,
        "cpu_time_s": time.process_time() - cpu_start,
        "peak_rss_kib": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
        "hard_gate_failures": len(hard_failures),
        "negative_or_inconclusive_records": len(negative),
        "source_tree_sha256": source_start["tree_sha256"],
        "source_unchanged": source_unchanged,
        "protocol_v1_sha256": sha256_file(FORWARD_V1_PROTOCOL_PATH),
        "protocol_v2_sha256": sha256_file(FORWARD_V2_PROTOCOL_PATH),
        "protocol_v3_sha256": sha256_file(FORWARD_V3_PROTOCOL_PATH),
        "protocol_v4_sha256": sha256_file(FORWARD_V4_PROTOCOL_PATH),
        "protocol_v5_sha256": sha256_file(FORWARD_PROTOCOL_PATH),
        "parameter_vector_sha256": parameter_record["parameter_vector_sha256"],
        "default": {
            "Bo": parameter_vector["Bo"],
            "L": parameter_vector["L_R_over_c"],
            "S_over_c": parameter_vector["static_gap_ratio_S_over_c"],
            "nonlinear_backend_Wc": critical_backend.dimensionless_forcing,
            "nonlinear_independent_bvp_Wc": bvp_nonlinear_forcing,
            "nonlinear_backend_rpm": critical_backend.actual_rpm,
            "nonlinear_independent_bvp_rpm": bvp_critical_rpm,
            "linear_backend_Wc": backend_linear_forcing,
            "linear_independent_bvp_Wc": bvp_linear_forcing,
            "linear_backend_rpm": backend_linear_rpm,
            "linear_independent_bvp_rpm": bvp_linear_rpm,
            "critical_W_relative_difference": critical_w_relative_difference,
            "critical_rpm_relative_difference": critical_rpm_relative_difference,
            "conservative_numerical_envelope_rpm": numerical_uncertainty_rpm,
        },
        "default_run": {
            "status": default_status,
            "actual_end_time_s": default_end_time,
            "frame_count": default_frame_count,
            "wall_time_s": default_wall_time,
        },
        "grid_sequence_status": grid_diagnostics["status"],
        "tolerance_sequence_status": tolerance_diagnostics["status"],
        "independent_bvp_refinement_status": bvp_refinement_diagnostics[
            "status"
        ],
        "evidence_level": "implementation_and_numerical_validation_only",
        "experimental_validation": "NOT_PERFORMED",
        "limitations": [
            "critical contact is a reduced-model geometric proxy",
            "C=A*Omega and c require apparatus calibration",
            "single-valued pre-contact graph only",
            "no stable-channel, wetting, breakup, or bubble-entrainment prediction",
        ],
    }
    write_json(output_dir / "result.json", result_summary)
    return result_summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output = args.output.expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {output}")
    try:
        summary = run_matrix(output)
    except Exception as error:
        output.mkdir(parents=True, exist_ok=True)
        failure = {
            "schema": SCHEMA,
            "status": "FAIL_EXCEPTION",
            "exception_type": type(error).__name__,
            "message": str(error),
            "traceback": traceback.format_exc(),
            "failed_utc": datetime.now(timezone.utc).isoformat(),
        }
        write_json(output / "failure.json", failure)
        print(json.dumps(failure, ensure_ascii=False))
        return 1
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 1 if summary["hard_gate_failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
