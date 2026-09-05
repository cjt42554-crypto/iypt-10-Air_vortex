"""Targeted diagnostics for the frozen Q1R numerical failures.

This candidate-only module does not alter the formal HF3D implementation.  It
extracts the dense pressure-projection trace from a completed archive, checks
the integrity of a terminal failure archive, and can replay one reconstructed
step from archived final cell-centred fields at stricter pressure tolerances.

The CFL helpers deliberately distinguish a controller target from the hard
post-step gate.  They are design probes for a future formal implementation;
they are not silently injected into the formal backend.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Sequence

import numpy as np


class FailureDiagnosticError(RuntimeError):
    """Raised when frozen failure evidence is missing or internally invalid."""


@dataclass(frozen=True, slots=True)
class ProjectionTrace:
    """Dense per-step projection diagnostics loaded from one run archive."""

    time_s: np.ndarray
    dt_s: np.ndarray
    divergence_s_inv: np.ndarray
    pressure_relative_residual: np.ndarray
    pressure_iterations: np.ndarray


def _finite_nonnegative(name: str, value: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a real number") from exc
    if not math.isfinite(result) or result < 0.0:
        raise ValueError(f"{name} must be finite and non-negative")
    return result


def _finite_positive(name: str, value: float) -> float:
    result = _finite_nonnegative(name, value)
    if result <= 0.0:
        raise ValueError(f"{name} must be positive")
    return result


def independent_cfl_limit_dt(
    rate_s_inv: float,
    source_rate_acceleration_s_inv2: float,
    cfl_limit: float,
    registered_dt_s: float,
) -> float:
    """Mirror the independent advective/source minimum used by Q1R.

    ``source_rate_acceleration_s_inv2`` is a velocity acceleration converted
    to a CFL-rate acceleration, for example ``a / h`` on an isotropic grid.
    Bounding ``R*dt`` and ``A*dt**2`` independently does not bound their sum.
    """

    rate = _finite_nonnegative("rate_s_inv", rate_s_inv)
    acceleration = _finite_nonnegative(
        "source_rate_acceleration_s_inv2",
        source_rate_acceleration_s_inv2,
    )
    limit = _finite_positive("cfl_limit", cfl_limit)
    registered = _finite_positive("registered_dt_s", registered_dt_s)
    advective_dt = math.inf if rate == 0.0 else limit / rate
    source_dt = math.inf if acceleration == 0.0 else math.sqrt(limit / acceleration)
    return min(registered, advective_dt, source_dt)


def combined_cfl_target_dt(
    rate_s_inv: float,
    source_rate_acceleration_s_inv2: float,
    cfl_target: float,
    registered_dt_s: float,
) -> float:
    """Return the positive-root bound for ``R*dt + A*dt**2 <= target``."""

    rate = _finite_nonnegative("rate_s_inv", rate_s_inv)
    acceleration = _finite_nonnegative(
        "source_rate_acceleration_s_inv2",
        source_rate_acceleration_s_inv2,
    )
    target = _finite_positive("cfl_target", cfl_target)
    registered = _finite_positive("registered_dt_s", registered_dt_s)
    if acceleration == 0.0:
        bounded = math.inf if rate == 0.0 else target / rate
    else:
        # This form avoids subtractive cancellation when A is small.
        bounded = 2.0 * target / (
            rate + math.sqrt(rate * rate + 4.0 * acceleration * target)
        )
    return min(registered, bounded)


def retry_dt_from_post_cfl(
    attempted_dt_s: float,
    observed_post_cfl: float,
    hard_cfl_limit: float,
    *,
    target_fraction: float = 0.95,
) -> float:
    """Propose a fail-closed first retry using a separate controller target.

    The proposal assumes only local first-order CFL scaling.  A formal retry
    loop must re-evaluate the complete step and must not commit either the flow
    state or pressure-solver history until the hard gate passes.
    """

    attempted = _finite_positive("attempted_dt_s", attempted_dt_s)
    observed = _finite_positive("observed_post_cfl", observed_post_cfl)
    hard_limit = _finite_positive("hard_cfl_limit", hard_cfl_limit)
    fraction = _finite_positive("target_fraction", target_fraction)
    if fraction >= 1.0:
        raise ValueError("target_fraction must be less than 1")
    if observed <= hard_limit:
        return attempted
    proposed = attempted * (fraction * hard_limit / observed)
    if not 0.0 < proposed < attempted:
        raise AssertionError("retry proposal must strictly reduce a failed step")
    return proposed


def candidate_pressure_tolerance(
    current_tolerance: float,
    observed_max_divergence_s_inv: float,
    divergence_gate_s_inv: float,
    *,
    safety_fraction: float = 0.8,
) -> float:
    """Return a conservative decade tolerance for a targeted replay.

    PCG relative residual and post-projection divergence are not identical
    norms.  This scaling is therefore a preregistration heuristic, not a PASS
    prediction.  The result is rounded down to a power of ten and must be
    verified against the unchanged absolute divergence gate.
    """

    current = _finite_positive("current_tolerance", current_tolerance)
    observed = _finite_positive(
        "observed_max_divergence_s_inv", observed_max_divergence_s_inv
    )
    gate = _finite_positive("divergence_gate_s_inv", divergence_gate_s_inv)
    safety = _finite_positive("safety_fraction", safety_fraction)
    if safety >= 1.0:
        raise ValueError("safety_fraction must be less than 1")
    if observed <= gate:
        return current
    raw = current * (gate / observed) * safety
    decade = 10.0 ** math.floor(math.log10(raw))
    return min(current, decade)


def _require_trace_arrays(arrays: Iterable[np.ndarray]) -> tuple[np.ndarray, ...]:
    values = tuple(np.asarray(item, dtype=np.float64) for item in arrays)
    if not values:
        raise ValueError("at least one trace array is required")
    size = values[0].size
    if size == 0:
        raise ValueError("projection trace must not be empty")
    for item in values:
        if item.ndim != 1 or item.size != size:
            raise ValueError("projection trace arrays must be same-length vectors")
        if not np.isfinite(item).all():
            raise ValueError("projection trace arrays must be finite")
    return values


def _one_based_contiguous_ranges(indices: np.ndarray) -> list[list[int]]:
    if indices.size == 0:
        return []
    gaps = np.flatnonzero(np.diff(indices) > 1)
    starts = np.r_[indices[0], indices[gaps + 1]]
    ends = np.r_[indices[gaps], indices[-1]]
    return [[int(start + 1), int(end + 1)] for start, end in zip(starts, ends)]


def diagnose_projection_trace(
    trace: ProjectionTrace,
    divergence_gate_s_inv: float,
) -> dict[str, Any]:
    """Summarise the dense trace without collapsing it to one maximum."""

    gate = _finite_positive("divergence_gate_s_inv", divergence_gate_s_inv)
    time_s, dt_s, divergence, residual, iterations = _require_trace_arrays(
        (
            trace.time_s,
            trace.dt_s,
            trace.divergence_s_inv,
            trace.pressure_relative_residual,
            trace.pressure_iterations,
        )
    )
    if np.any(dt_s <= 0.0) or np.any(time_s < 0.0) or np.any(iterations < 0.0):
        raise ValueError("time steps, times and iteration counts are out of range")
    peak_index = int(np.argmax(divergence))
    exceeded = np.flatnonzero(divergence > gate)
    quantile_levels = (0.0, 0.5, 0.9, 0.99, 0.999, 1.0)
    return {
        "completed_steps": int(time_s.size),
        "divergence_gate_s_inv": gate,
        "gate_pass": bool(exceeded.size == 0),
        "exceeding_step_count": int(exceeded.size),
        "exceeding_step_fraction": float(exceeded.size / time_s.size),
        "exceeding_step_ranges_one_based_inclusive": (
            _one_based_contiguous_ranges(exceeded)
        ),
        "peak": {
            "array_index_zero_based": peak_index,
            "step_one_based": peak_index + 1,
            "time_s": float(time_s[peak_index]),
            "dt_s": float(dt_s[peak_index]),
            "projected_divergence_s_inv": float(divergence[peak_index]),
            "pressure_relative_residual": float(residual[peak_index]),
            "pressure_iterations": int(round(float(iterations[peak_index]))),
        },
        "quantile_levels": list(quantile_levels),
        "projected_divergence_quantiles_s_inv": [
            float(value) for value in np.quantile(divergence, quantile_levels)
        ],
        "pressure_relative_residual_quantiles": [
            float(value) for value in np.quantile(residual, quantile_levels)
        ],
        "pressure_iteration_quantiles": [
            float(value) for value in np.quantile(iterations, quantile_levels)
        ],
    }


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FailureDiagnosticError(f"cannot read JSON object {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise FailureDiagnosticError(f"expected JSON object: {path}")
    return raw


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_boundary_summary(archive: Path) -> dict[str, Any]:
    provenance = _read_json_object(archive / "provenance.json")
    source = provenance.get("source_identity")
    if not isinstance(source, dict):
        raise FailureDiagnosticError("provenance lacks source_identity object")
    disk = source.get("disk_content_tree")
    if not isinstance(disk, dict):
        raise FailureDiagnosticError("source identity lacks disk_content_tree")
    start = disk.get("execution_start")
    end = disk.get("execution_end")
    if not isinstance(start, dict) or not isinstance(end, dict):
        raise FailureDiagnosticError("source boundary snapshots are missing")
    return {
        "execution_source_identity_valid": bool(
            source.get("execution_source_identity_valid")
        ),
        "boundary_match": bool(disk.get("boundary_match")),
        "source_tree_sha256_start": start.get("value"),
        "source_tree_sha256_end": end.get("value"),
        "loaded_module_bytes_verified": bool(
            source.get("loaded_module_bytes_verified")
        ),
    }


def _json_differences(
    left: Any,
    right: Any,
    path: str = "",
) -> list[dict[str, Any]]:
    if isinstance(left, dict) and isinstance(right, dict):
        differences: list[dict[str, Any]] = []
        for key in sorted(set(left) | set(right)):
            child = f"{path}.{key}" if path else str(key)
            if key not in left:
                differences.append(
                    {"path": child, "original": None, "replay": right[key]}
                )
            elif key not in right:
                differences.append(
                    {"path": child, "original": left[key], "replay": None}
                )
            else:
                differences.extend(_json_differences(left[key], right[key], child))
        return differences
    if isinstance(left, list) and isinstance(right, list):
        if left == right:
            return []
        return [{"path": path, "original": left, "replay": right}]
    if left == right:
        return []
    return [{"path": path, "original": left, "replay": right}]


def _archive_array(
    arrays: np.lib.npyio.NpzFile,
    mapping: dict[str, Any],
    name: str,
) -> np.ndarray:
    entry = mapping.get(name)
    if not isinstance(entry, dict) or not isinstance(entry.get("npz_key"), str):
        raise FailureDiagnosticError(f"archive field mapping is missing {name!r}")
    key = entry["npz_key"]
    if key not in arrays.files:
        raise FailureDiagnosticError(f"arrays.npz is missing mapped key {key!r}")
    return np.asarray(arrays[key])


def load_projection_trace(archive: Path) -> ProjectionTrace:
    """Load the committed-name dense fields from an AirVortex run archive."""

    archive = Path(archive)
    result = _read_json_object(archive / "result.json")
    arrays_meta = result.get("arrays")
    fields = arrays_meta.get("fields") if isinstance(arrays_meta, dict) else None
    if not isinstance(fields, dict):
        raise FailureDiagnosticError("result.json lacks arrays.fields mapping")
    try:
        with np.load(archive / "arrays.npz", allow_pickle=False) as arrays:
            values = {
                name: np.asarray(_archive_array(arrays, fields, name), dtype=np.float64)
                for name in (
                    "diagnostic_step_time_s",
                    "diagnostic_dt_s",
                    "diagnostic_projected_divergence_s_inv",
                    "diagnostic_pressure_relative_residual",
                    "diagnostic_pressure_iterations",
                )
            }
    except (OSError, ValueError) as exc:
        raise FailureDiagnosticError(f"cannot load arrays.npz: {exc}") from exc
    return ProjectionTrace(
        time_s=values["diagnostic_step_time_s"],
        dt_s=values["diagnostic_dt_s"],
        divergence_s_inv=values["diagnostic_projected_divergence_s_inv"],
        pressure_relative_residual=values[
            "diagnostic_pressure_relative_residual"
        ],
        pressure_iterations=values["diagnostic_pressure_iterations"],
    )


def _frame_diagnostics(archive: Path) -> list[dict[str, float]]:
    result = _read_json_object(archive / "result.json")
    arrays_meta = result.get("arrays")
    observables = (
        arrays_meta.get("observables") if isinstance(arrays_meta, dict) else None
    )
    if not isinstance(observables, dict):
        raise FailureDiagnosticError("result.json lacks arrays.observables mapping")
    names = (
        "divergence_before_projection",
        "max_divergence",
        "pressure_residual",
        "pressure_iterations",
        "cfl_advective",
    )
    with np.load(archive / "arrays.npz", allow_pickle=False) as arrays:
        if "time_s" not in arrays.files:
            raise FailureDiagnosticError("arrays.npz lacks time_s")
        time_s = np.asarray(arrays["time_s"], dtype=np.float64)
        values = {
            name: np.asarray(_archive_array(arrays, observables, name), dtype=np.float64)
            for name in names
        }
    _require_trace_arrays((time_s, *(values[name] for name in names)))
    return [
        {
            "time_s": float(time_s[index]),
            "divergence_before_projection_s_inv": float(
                values["divergence_before_projection"][index]
            ),
            "projected_divergence_s_inv": float(values["max_divergence"][index]),
            "pressure_relative_residual": float(values["pressure_residual"][index]),
            "pressure_iterations": int(
                round(float(values["pressure_iterations"][index]))
            ),
            "advective_cfl": float(values["cfl_advective"][index]),
        }
        for index in range(time_s.size)
    ]


def _verify_terminal_payloads(archive: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise FailureDiagnosticError("terminal manifest lacks files integrity map")
    checks: dict[str, Any] = {}
    for name, expected in sorted(files.items()):
        if not isinstance(name, str) or not isinstance(expected, dict):
            raise FailureDiagnosticError("terminal files integrity map is malformed")
        path = archive / name
        actual_size = path.stat().st_size
        actual_sha = _sha256(path)
        expected_size = expected.get("size_bytes")
        expected_sha = expected.get("sha256")
        valid = actual_size == expected_size and actual_sha == expected_sha
        checks[name] = {
            "size_bytes": actual_size,
            "sha256": actual_sha,
            "matches_manifest": valid,
        }
        if not valid:
            raise FailureDiagnosticError(f"terminal payload integrity failed: {path}")
    return checks


_POST_CFL_RE = re.compile(
    r"post-source advective CFL\s+(?P<observed>[0-9.eE+-]+)\s+exceeds "
    r"configured limit\s+(?P<limit>[0-9.eE+-]+)"
)
_PROGRESS_RE = re.compile(
    r"^\[progress\]\s+(?P<fraction>[0-9.]+)%\s+step=(?P<step>[0-9]+)\s+",
    re.MULTILINE,
)


def diagnose_terminal_cfl_failure(archive: Path) -> dict[str, Any]:
    """Validate and extract the frozen fine-grid terminal failure."""

    archive = Path(archive)
    manifest = _read_json_object(archive / "manifest.json")
    if manifest.get("archive_format") != "airvortex-runtime-terminal-v2":
        raise FailureDiagnosticError("fine-grid evidence is not a v2 terminal archive")
    if manifest.get("state") != "failed" or manifest.get("complete_run_archive"):
        raise FailureDiagnosticError("fine-grid terminal archive is not fail-closed")
    payload_checks = _verify_terminal_payloads(archive, manifest)
    error = manifest.get("error")
    message = error.get("message") if isinstance(error, dict) else None
    if not isinstance(message, str):
        raise FailureDiagnosticError("terminal manifest lacks an error message")
    match = _POST_CFL_RE.search(message)
    if match is None:
        raise FailureDiagnosticError("terminal error is not a post-source CFL failure")
    observed = float(match.group("observed"))
    hard_limit = float(match.group("limit"))
    config = _read_json_object(archive / "config.json")
    high_fidelity = config.get("high_fidelity")
    if not isinstance(high_fidelity, dict):
        raise FailureDiagnosticError("terminal config lacks high_fidelity object")
    attempted_dt = _finite_positive(
        "high_fidelity.time_step_s", high_fidelity.get("time_step_s")
    )
    configured_limit = _finite_positive(
        "high_fidelity.cfl_limit", high_fidelity.get("cfl_limit")
    )
    if not math.isclose(configured_limit, hard_limit, rel_tol=0.0, abs_tol=5.0e-5):
        raise FailureDiagnosticError("terminal error limit disagrees with config")
    log_path = archive.parent / f"{archive.name}.log"
    last_progress: dict[str, Any] | None = None
    if log_path.is_file():
        log_text = log_path.read_text(encoding="utf-8")
        progress = list(_PROGRESS_RE.finditer(log_text))
        if progress:
            last = progress[-1]
            last_progress = {
                "completed_step": int(last.group("step")),
                "reported_fraction_percent": float(last.group("fraction")),
                "log_sha256": _sha256(log_path),
            }
    return {
        "archive_format": manifest["archive_format"],
        "artifact_class": manifest.get("artifact_class"),
        "state": manifest.get("state"),
        "complete_run_archive": bool(manifest.get("complete_run_archive")),
        "execution_source_identity_valid": bool(
            manifest.get("execution_source_identity_valid")
        ),
        "payload_integrity": payload_checks,
        "error_type": error.get("type") if isinstance(error, dict) else None,
        "error_message": message,
        "attempted_registered_dt_s": attempted_dt,
        "observed_post_source_advective_cfl": observed,
        "hard_cfl_limit": hard_limit,
        "relative_excess": observed / hard_limit - 1.0,
        "candidate_first_retry_dt_s_at_target_fraction_0_95": (
            retry_dt_from_post_cfl(
                attempted_dt,
                observed,
                hard_limit,
                target_fraction=0.95,
            )
        ),
        "last_completed_progress": last_progress,
        "missing_failure_step_diagnostics": [
            "pre_source_advective_cfl",
            "selected_time_step_limit",
            "maximum_surface_tension_acceleration_m_s2",
            "maximum_viscous_acceleration_m_s2",
            "maximum_boundary_acceleration_m_s2",
            "pressure_accelerator_selected_for_failed_step",
        ],
    }


def replay_archived_final_step(
    archive: Path,
    tolerances: Sequence[float],
) -> dict[str, Any]:
    """Replay one reconstructed final-state step with the formal CUDA backend.

    Archives contain final cell-centred velocities rather than exact MAC faces.
    The replay therefore reconstructs faces and is a mechanism test, not an
    exact replay of the dense-trace peak.
    """

    from airvortex.backends.high_fidelity_3d import (  # local optional import
        HF3DState,
        HighFidelityBackend,
        _make_pressure_projection,
    )
    from airvortex.io import load_config_json
    from airvortex.io.archive import source_tree_identity

    archive = Path(archive)
    checked_tolerances = tuple(
        _finite_positive("pressure tolerance", value) for value in tolerances
    )
    if not checked_tolerances:
        raise ValueError("at least one replay tolerance is required")
    base = load_config_json(archive / "config.json")
    backend = HighFidelityBackend()
    grid = backend.make_grid(base)
    result = _read_json_object(archive / "result.json")
    arrays_meta = result.get("arrays")
    fields_meta = arrays_meta.get("fields") if isinstance(arrays_meta, dict) else None
    observables_meta = (
        arrays_meta.get("observables") if isinstance(arrays_meta, dict) else None
    )
    if not isinstance(fields_meta, dict) or not isinstance(observables_meta, dict):
        raise FailureDiagnosticError("archive array mappings are incomplete")
    field_names = (
        "final_phase_liquid_fraction_3d",
        "final_pressure_gauge_pa_3d",
        "final_u_x_rotating_m_s_3d",
        "final_u_y_rotating_m_s_3d",
        "final_u_z_rotating_m_s_3d",
    )
    with np.load(archive / "arrays.npz", allow_pickle=False) as arrays:
        fields = {
            name: np.asarray(
                _archive_array(arrays, fields_meta, name), dtype=np.float64
            )
            for name in field_names
        }
        bar_angle = float(
            np.asarray(
                _archive_array(arrays, observables_meta, "bar_angle_rad"),
                dtype=np.float64,
            )[-1]
        )
    ux_face, uy_face, uz_face = grid.cells_to_faces(
        fields["final_u_x_rotating_m_s_3d"],
        fields["final_u_y_rotating_m_s_3d"],
        fields["final_u_z_rotating_m_s_3d"],
    )
    phase = fields["final_phase_liquid_fraction_3d"]
    expected_volume = grid.integrate(phase)
    completed_steps = load_projection_trace(archive).time_s.size
    records: list[dict[str, Any]] = []
    for tolerance in checked_tolerances:
        config = replace(
            base,
            high_fidelity=replace(
                base.high_fidelity,
                pressure_solver_device="cuda",
                pressure_relative_tolerance=tolerance,
                pressure_max_iterations=max(
                    1200, base.high_fidelity.pressure_max_iterations
                ),
            ),
        )
        projection, accelerator = _make_pressure_projection(config, grid)
        if accelerator.get("selected") != "cuda":
            raise FailureDiagnosticError("targeted replay did not select CUDA")
        state = HF3DState(
            time_s=base.run.duration_s,
            step=int(completed_steps),
            bar_angle_rad=bar_angle,
            u_x_face=ux_face.copy(),
            u_y_face=uy_face.copy(),
            u_z_face=uz_face.copy(),
            phase=phase.copy(),
            pressure_pa=fields["final_pressure_gauge_pa_3d"].copy(),
        )
        diagnostics = backend.advance_one_step(
            state,
            config,
            grid,
            projection,
            base.high_fidelity.time_step_s,
            expected_liquid_volume_m3=expected_volume,
        )
        synchronize = getattr(projection, "synchronize", None)
        if callable(synchronize):
            synchronize()
        records.append(
            {
                "pressure_relative_tolerance": tolerance,
                "pressure_accelerator_selected": accelerator["selected"],
                "pressure_accelerator_device_name": accelerator.get("device_name"),
                "divergence_before_projection_s_inv": (
                    diagnostics.divergence_before_s_inv
                ),
                "projected_divergence_s_inv": diagnostics.divergence_after_s_inv,
                "pressure_relative_residual": (
                    diagnostics.pressure_relative_residual
                ),
                "pressure_iterations": diagnostics.pressure_iterations,
                "post_source_advective_cfl": diagnostics.advective_cfl,
                "maximum_surface_tension_acceleration_m_s2": (
                    diagnostics.maximum_surface_tension_acceleration_m_s2
                ),
                "maximum_viscous_acceleration_m_s2": (
                    diagnostics.maximum_viscous_acceleration_m_s2
                ),
                "maximum_boundary_acceleration_m_s2": (
                    diagnostics.maximum_boundary_acceleration_m_s2
                ),
            }
        )
    return {
        "identity": "archive-final-cell-velocity-reconstructed-to-MAC-faces",
        "exact_peak_replay": False,
        "limitation": (
            "The completed archive stores final cell-centred velocity, not the "
            "pre-projection MAC state at the dense-trace peak."
        ),
        "formal_step_time_s": base.high_fidelity.time_step_s,
        "configured_cells_x_y_z": [grid.nx, grid.ny, grid.nz],
        "live_formal_source_tree_identity": source_tree_identity(),
        "records": records,
    }


def diagnose_completed_pressure_replay(
    original_archive: Path,
    replay_archive: Path,
    *,
    divergence_gate_s_inv: float,
) -> dict[str, Any]:
    """Validate the full medium-grid pressure-tolerance replay."""

    from airvortex.io import load_run_archive
    from hfq_cli_common import LoadedCase, _quality_gate

    original_archive = Path(original_archive)
    replay_archive = Path(replay_archive)
    original = load_run_archive(original_archive)
    replay = load_run_archive(replay_archive)
    original_config = original.config.to_dict()
    replay_config = replay.config.to_dict()
    differences = _json_differences(original_config, replay_config)
    expected_difference = [
        {
            "path": "high_fidelity.pressure_relative_tolerance",
            "original": 1.0e-6,
            "replay": 1.0e-7,
        }
    ]
    if differences != expected_difference:
        raise FailureDiagnosticError(
            "completed pressure replay differs from Q1R medium at unexpected paths"
        )
    original_source = _source_boundary_summary(original_archive)
    replay_source = _source_boundary_summary(replay_archive)
    if (
        not original_source["execution_source_identity_valid"]
        or not replay_source["execution_source_identity_valid"]
        or original_source["source_tree_sha256_start"]
        != replay_source["source_tree_sha256_start"]
        or replay_source["source_tree_sha256_start"]
        != replay_source["source_tree_sha256_end"]
    ):
        raise FailureDiagnosticError("completed pressure replay source identity mismatch")
    status = getattr(replay.result.status, "value", str(replay.result.status))
    if status != "completed":
        raise FailureDiagnosticError("completed pressure replay result is not completed")
    if not math.isclose(
        float(replay.result.time_s[-1]),
        replay.config.run.duration_s,
        rel_tol=0.0,
        abs_tol=1.0e-14,
    ):
        raise FailureDiagnosticError("completed pressure replay did not reach duration")
    metadata = dict(replay.result.metadata)
    diagnostics = metadata.get("diagnostics")
    if not isinstance(diagnostics, dict):
        raise FailureDiagnosticError("completed pressure replay lacks diagnostics")
    accelerator = diagnostics.get("pressure_accelerator")
    if not isinstance(accelerator, dict) or accelerator.get("selected") != "cuda":
        raise FailureDiagnosticError("completed pressure replay did not select CUDA")
    spec = _read_json_object(Path(__file__).with_name("campaign_screening_q1r.json"))
    gates_root = spec.get("gates")
    numerical_gates = (
        gates_root.get("numerical_quality") if isinstance(gates_root, dict) else None
    )
    if not isinstance(numerical_gates, dict):
        raise FailureDiagnosticError("Q1R numerical quality gates are unavailable")
    loaded = LoadedCase(
        case={"id": "medium_pressure_1e-7_targeted_replay"},
        config=replay_config,
        archived_config=replay_config,
        time_s=np.asarray(replay.result.time_s, dtype=np.float64),
        observables={
            name: np.asarray(value, dtype=np.float64)
            for name, value in replay.result.observables.items()
        },
        fields={
            name: np.asarray(value, dtype=np.float64)
            for name, value in replay.result.fields.items()
        },
        metadata=metadata,
        manifest=dict(replay.manifest),
        provenance=dict(replay.provenance),
        result_status=status,
    )
    quality = _quality_gate(loaded, numerical_gates)
    original_trace = diagnose_projection_trace(
        load_projection_trace(original_archive), divergence_gate_s_inv
    )
    replay_trace = diagnose_projection_trace(
        load_projection_trace(replay_archive), divergence_gate_s_inv
    )
    runtime_extra = replay.provenance.get("extra")
    runtime = (
        runtime_extra.get("runtime") if isinstance(runtime_extra, dict) else None
    )
    runtime = runtime if isinstance(runtime, dict) else {}
    replay_peak = replay_trace["peak"]
    original_peak = original_trace["peak"]
    passed = bool(quality["pass"] and replay_trace["gate_pass"])
    return {
        "status": (
            "PASS_TARGETED_MEDIUM_PRESSURE_REPLAY"
            if passed
            else "FAIL_TARGETED_MEDIUM_PRESSURE_REPLAY"
        ),
        "claim_boundary": (
            "single medium-grid numerical-quality replay only; not Q1R2 or convergence"
        ),
        "archive": str(replay_archive),
        "archive_hashes": {
            name: _sha256(replay_archive / name)
            for name in ("manifest.json", "provenance.json", "result.json", "arrays.npz")
        },
        "only_config_difference_from_q1r_medium": differences,
        "source_boundary": replay_source,
        "result_status": status,
        "simulated_duration_s": float(replay.result.time_s[-1]),
        "wall_time_s": runtime.get("duration_s"),
        "pressure_accelerator": {
            "selected": accelerator.get("selected"),
            "fallback": accelerator.get("fallback"),
            "device_name": accelerator.get("device_name"),
        },
        "projection_trace": replay_trace,
        "q1r_numerical_quality_gate": quality,
        "comparison_to_q1r_medium": {
            "same_peak_step": (
                original_peak["step_one_based"] == replay_peak["step_one_based"]
            ),
            "same_peak_time": math.isclose(
                original_peak["time_s"],
                replay_peak["time_s"],
                rel_tol=0.0,
                abs_tol=1.0e-15,
            ),
            "original_peak_projected_divergence_s_inv": original_peak[
                "projected_divergence_s_inv"
            ],
            "replay_peak_projected_divergence_s_inv": replay_peak[
                "projected_divergence_s_inv"
            ],
            "peak_divergence_reduction_factor": (
                original_peak["projected_divergence_s_inv"]
                / replay_peak["projected_divergence_s_inv"]
            ),
            "original_exceeding_step_count": original_trace[
                "exceeding_step_count"
            ],
            "replay_exceeding_step_count": replay_trace[
                "exceeding_step_count"
            ],
        },
    }


def diagnose_q1r_failures(
    medium_archive: Path,
    fine_terminal_archive: Path,
    *,
    divergence_gate_s_inv: float,
    replay_tolerances: Sequence[float] = (),
    completed_pressure_replay_archive: Path | None = None,
) -> dict[str, Any]:
    """Build one structured diagnosis tied to the frozen Q1R artifacts."""

    medium_archive = Path(medium_archive)
    fine_terminal_archive = Path(fine_terminal_archive)
    trace = load_projection_trace(medium_archive)
    projection = diagnose_projection_trace(trace, divergence_gate_s_inv)
    config = _read_json_object(medium_archive / "config.json")
    high_fidelity = config.get("high_fidelity")
    if not isinstance(high_fidelity, dict):
        raise FailureDiagnosticError("medium config lacks high_fidelity object")
    current_tolerance = _finite_positive(
        "pressure_relative_tolerance",
        high_fidelity.get("pressure_relative_tolerance"),
    )
    peak_divergence = projection["peak"]["projected_divergence_s_inv"]
    frame_records = _frame_diagnostics(medium_archive)
    peak_time = projection["peak"]["time_s"]
    relevant_frames = [
        record for record in frame_records if abs(record["time_s"] - peak_time) <= 0.002
    ]
    blockers = [
        "formal controller has no separate CFL target and hard gate",
        "fine failure step lacks pre-source and per-operator diagnostics",
        "no complete Q1R2 spatial or temporal campaign has run",
    ]
    result: dict[str, Any] = {
        "schema_version": "hfq-q1r-failure-diagnosis-v1",
        "status": "DIAGNOSED__Q1R2_NOT_FROZEN",
        "claim_ceiling": "CANDIDATE_NUMERICAL_DIAGNOSIS_ONLY",
        "inputs": {
            "medium_archive": str(medium_archive),
            "medium_result_json_sha256": _sha256(medium_archive / "result.json"),
            "medium_arrays_npz_sha256": _sha256(medium_archive / "arrays.npz"),
            "medium_execution_source_boundary": _source_boundary_summary(
                medium_archive
            ),
            "fine_terminal_archive": str(fine_terminal_archive),
            "fine_terminal_manifest_sha256": _sha256(
                fine_terminal_archive / "manifest.json"
            ),
            "fine_execution_source_boundary": _source_boundary_summary(
                fine_terminal_archive
            ),
        },
        "medium_projection_trace": projection,
        "medium_output_frames_within_2ms_of_peak": relevant_frames,
        "candidate_pressure_relative_tolerance_for_targeted_replay": (
            candidate_pressure_tolerance(
                current_tolerance,
                peak_divergence,
                divergence_gate_s_inv,
            )
        ),
        "fine_terminal_cfl_failure": diagnose_terminal_cfl_failure(
            fine_terminal_archive
        ),
        "gate_changes": {
            "projected_divergence_max_s_inv": "UNCHANGED",
            "advective_cfl_max": "UNCHANGED",
            "physical_parameters": "UNCHANGED",
        },
        "q1r2": {
            "status": "NOT_FROZEN",
            "blockers": blockers,
        },
    }
    if replay_tolerances:
        result["medium_archived_final_state_projection_replay"] = (
            replay_archived_final_step(medium_archive, replay_tolerances)
        )
    if completed_pressure_replay_archive is not None:
        completed_replay = diagnose_completed_pressure_replay(
            medium_archive,
            completed_pressure_replay_archive,
            divergence_gate_s_inv=divergence_gate_s_inv,
        )
        result["medium_completed_pressure_tolerance_replay"] = completed_replay
        if completed_replay["status"] == "PASS_TARGETED_MEDIUM_PRESSURE_REPLAY":
            result["status"] = (
                "DIAGNOSED_PRESSURE_REPLAY_PASS__CFL_OPEN__Q1R2_NOT_FROZEN"
            )
    return result


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _diagnose_command(args: argparse.Namespace) -> int:
    default_tolerances = (1.0e-6, 1.0e-7, 1.0e-8)
    payload = diagnose_q1r_failures(
        args.medium_archive,
        args.fine_terminal_archive,
        divergence_gate_s_inv=args.divergence_gate_s_inv,
        replay_tolerances=(
            ()
            if args.skip_projection_replay
            else (args.replay_tolerance or default_tolerances)
        ),
        completed_pressure_replay_archive=args.completed_pressure_replay_archive,
    )
    _write_json(args.output, payload)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("medium_archive", type=Path)
    parser.add_argument("fine_terminal_archive", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--divergence-gate-s-inv", type=float, default=1.0e-4)
    parser.add_argument(
        "--replay-tolerance",
        type=float,
        action="append",
        default=None,
    )
    parser.add_argument("--skip-projection-replay", action="store_true")
    parser.add_argument("--completed-pressure-replay-archive", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _diagnose_command(args)
    except (FailureDiagnosticError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


__all__ = [
    "FailureDiagnosticError",
    "ProjectionTrace",
    "candidate_pressure_tolerance",
    "combined_cfl_target_dt",
    "diagnose_projection_trace",
    "diagnose_completed_pressure_replay",
    "diagnose_q1r_failures",
    "diagnose_terminal_cfl_failure",
    "independent_cfl_limit_dt",
    "load_projection_trace",
    "replay_archived_final_step",
    "retry_dt_from_post_cfl",
]


if __name__ == "__main__":
    raise SystemExit(main())
