"""Archive adapters and shared fail-closed gates for the HFQ CLI."""
from __future__ import annotations
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Mapping
import numpy as np
from hfq_core import (
    SeriesData,
    canonical_sha256,
    grid_audit,
    max_abs_observable,
    physical_signature,
    read_json,
)

@dataclass(slots=True)
class LoadedCase:
    case: dict[str, Any]
    config: dict[str, Any]
    archived_config: dict[str, Any]
    time_s: np.ndarray
    observables: dict[str, np.ndarray]
    fields: dict[str, np.ndarray]
    metadata: dict[str, Any]
    manifest: dict[str, Any]
    provenance: dict[str, Any]
    result_status: str

class UserFacingError(RuntimeError):
    """A concise command failure."""

def _to_plain_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}

def _load_case(
    case: Mapping[str, Any],
    manifest_path: Path,
    runs_root: Path,
) -> LoadedCase:
    try:
        from airvortex.io import load_run_archive
    except Exception as exc:
        raise UserFacingError(
            "AirVortex package is unavailable. Run this command from 02_模拟程序 after "
            "`python -m pip install -e .`."
        ) from exc

    case_id = str(case["id"])
    archive_id = str(case.get("reuse_case_id") or case_id)
    archive = load_run_archive(runs_root / archive_id)
    generated_config = read_json(manifest_path.parent / str(case["config_file"]))
    archived_config = archive.config.to_dict()
    registered_digest = case.get("config_sha256")
    generated_digest = canonical_sha256(generated_config)
    if not isinstance(registered_digest, str) or generated_digest != registered_digest:
        raise UserFacingError(
            f"{case_id}: generated config differs from its manifest digest"
        )
    differences = _explicit_config_differences(generated_config, archived_config)
    if differences:
        raise UserFacingError(
            f"{case_id}: archived config differs at explicit generated paths: "
            + ", ".join(differences[:8])
        )
    result = archive.result
    status = getattr(result.status, "value", str(result.status))
    return LoadedCase(
        case=dict(case),
        config=generated_config,
        archived_config=archived_config,
        time_s=np.asarray(result.time_s, dtype=np.float64),
        observables={
            name: np.asarray(value, dtype=np.float64)
            for name, value in result.observables.items()
        },
        fields={
            name: np.asarray(value, dtype=np.float64)
            for name, value in result.fields.items()
        },
        metadata=_to_plain_mapping(result.metadata),
        manifest=_to_plain_mapping(archive.manifest),
        provenance=_to_plain_mapping(archive.provenance),
        result_status=status,
    )


def _explicit_config_differences(
    expected: Any,
    actual: Any,
    path: str = "",
) -> list[str]:
    """Return generated paths whose archived values are absent or different.

    Formal config loading may add source-versioned defaults.  Those additions
    are retained in the archive and anchored by the execution source hash, but
    every value explicitly frozen by the generated JSON must match exactly.
    """

    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            return [path or "<root>"]
        differences: list[str] = []
        for key, value in expected.items():
            child = f"{path}.{key}" if path else str(key)
            if key not in actual:
                differences.append(child)
            else:
                differences.extend(
                    _explicit_config_differences(value, actual[key], child)
                )
        return differences
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) != len(actual):
            return [path or "<root>"]
        differences = []
        for index, (left, right) in enumerate(zip(expected, actual)):
            differences.extend(
                _explicit_config_differences(left, right, f"{path}[{index}]")
            )
        return differences
    if isinstance(expected, bool) or isinstance(actual, bool):
        return [] if type(expected) is type(actual) and expected == actual else [path]
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return [] if float(expected) == float(actual) else [path]
    return [] if expected == actual else [path]

def _nested(mapping: Mapping[str, Any], *path: str) -> Any:
    current: Any = mapping
    for part in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current

def _run_audit(loaded: LoadedCase, expected_signature: str) -> dict[str, Any]:
    source_identity = _to_plain_mapping(loaded.provenance.get("source_identity"))
    source_digest = source_identity.get("source_tree_sha256_start")
    source_valid = source_identity.get("execution_source_identity_valid") is True
    diagnostics = _to_plain_mapping(loaded.metadata.get("diagnostics"))
    pressure_accelerator = _to_plain_mapping(
        diagnostics.get("pressure_accelerator")
    )
    numerical_thread_policy = _to_plain_mapping(
        diagnostics.get("numerical_thread_policy")
    )
    runtime_identity = {
        "python": _to_plain_mapping(loaded.provenance.get("python")),
        "dependencies": _to_plain_mapping(
            loaded.provenance.get("dependencies")
        ),
        "pressure_accelerator": {
            "requested": pressure_accelerator.get("requested"),
            "selected": pressure_accelerator.get("selected"),
            "fallback": pressure_accelerator.get("fallback"),
            "device_name": pressure_accelerator.get("device_name"),
            "compute_capability": pressure_accelerator.get(
                "compute_capability"
            ),
        },
        "numerical_thread_policy": {
            "requested": numerical_thread_policy.get("requested"),
            "selected_threads": numerical_thread_policy.get(
                "selected_threads"
            ),
        },
    }
    termination = _to_plain_mapping(loaded.manifest.get("termination"))
    provenance_extra = _to_plain_mapping(loaded.provenance.get("extra"))
    provenance_runtime = _to_plain_mapping(provenance_extra.get("runtime"))
    resources = _to_plain_mapping(loaded.provenance.get("resources"))
    peak_rss = _to_plain_mapping(resources.get("peak_resident_set_size"))
    raw_steps = loaded.fields.get("diagnostic_dt_s")
    time_steps = np.asarray(raw_steps, dtype=np.float64).reshape(-1)
    valid_time_steps = time_steps[
        np.isfinite(time_steps) & (time_steps > 0.0)
    ]
    configured_maximum_step = float(
        _nested(loaded.config, "high_fidelity", "time_step_s")
    )
    step_tolerance = max(1.0e-15, 1.0e-9 * configured_maximum_step)
    configured_step_active_fraction = (
        float(
            np.mean(
                np.abs(valid_time_steps - configured_maximum_step)
                <= step_tolerance
            )
        )
        if valid_time_steps.size
        else None
    )
    actual_end = float(loaded.time_s[-1]) if loaded.time_s.size else None
    requested_end = float(_nested(loaded.config, "run", "duration_s"))
    tolerance = max(1.0e-12, 1.0e-9 * max(1.0, requested_end))
    completed = (
        loaded.result_status == "completed"
        and actual_end is not None
        and actual_end + tolerance >= requested_end
        and diagnostics.get("termination_reason") == "configured_duration_reached"
        and termination.get("domain_limited", False) is not True
    )
    signature = canonical_sha256(physical_signature(loaded.config))
    grid = grid_audit(loaded.config)
    return {
        "completed": completed,
        "result_status": loaded.result_status,
        "requested_end_s": requested_end,
        "actual_end_s": actual_end,
        "execution_source_identity_valid": source_valid,
        "source_tree_sha256_start": source_digest,
        "physical_signature_match": signature == expected_signature,
        "physical_signature_sha256": signature,
        "generated_config_sha256": canonical_sha256(loaded.config),
        "archived_config_sha256": canonical_sha256(loaded.archived_config),
        "execution_runtime_identity": runtime_identity,
        "execution_runtime_identity_sha256": canonical_sha256(runtime_identity),
        "execution_metrics": {
            "wall_clock_duration_s": provenance_runtime.get("duration_s"),
            "peak_resident_set_size_bytes": peak_rss.get("value"),
            "diagnostic_time_step_count": int(valid_time_steps.size),
            "configured_maximum_time_step_s": configured_maximum_step,
            "minimum_actual_time_step_s": (
                float(np.min(valid_time_steps)) if valid_time_steps.size else None
            ),
            "maximum_actual_time_step_s": (
                float(np.max(valid_time_steps)) if valid_time_steps.size else None
            ),
            "configured_step_active_fraction": (
                configured_step_active_fraction
            ),
        },
        "grid": grid.to_dict(),
        "diagnostics": {
            "numerical_quality_gate_pass": (
                _nested(diagnostics, "numerical_quality_gate", "pass") is True
            ),
            "physical_prediction_gate_pass": (
                _nested(
                    diagnostics,
                    "numerical_quality_gate",
                    "physical_prediction_gate_pass",
                )
                is True
            ),
            "maximum_projected_divergence_s_inv": diagnostics.get(
                "maximum_projected_divergence_s_inv"
            ),
            "maximum_pressure_relative_residual": diagnostics.get(
                "maximum_pressure_relative_residual"
            ),
            "maximum_advective_cfl": diagnostics.get("maximum_advective_cfl"),
            "maximum_single_step_phase_clip_relative": diagnostics.get(
                "maximum_single_step_phase_clip_relative"
            ),
            "cumulative_phase_clip_relative": diagnostics.get(
                "cumulative_phase_clip_relative"
            ),
            "maximum_single_step_phase_repair_relative": diagnostics.get(
                "maximum_single_step_phase_repair_relative"
            ),
            "cumulative_phase_repair_relative": diagnostics.get(
                "cumulative_phase_repair_relative"
            ),
            "pressure_accelerator_selected": pressure_accelerator.get(
                "selected"
            ),
            "pressure_accelerator_fallback": pressure_accelerator.get(
                "fallback"
            ),
            "liquid_open_boundary_balance_error_relative": diagnostics.get(
                "liquid_open_boundary_balance_error_relative"
            ),
        },
    }

def _finite_gate(gates: Mapping[str, Any], name: str) -> float:
    value = gates.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UserFacingError(f"gate {name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result <= 0.0:
        raise UserFacingError(f"gate {name} must be finite and > 0")
    return result

def _quality_gate(loaded: LoadedCase, gates: Mapping[str, Any]) -> dict[str, Any]:
    diagnostics = _to_plain_mapping(loaded.metadata.get("diagnostics"))
    values = {
        "liquid_mass_error_relative": max_abs_observable(
            loaded.observables, "liquid_mass_error_relative"
        ),
        "liquid_volume_balance_error_relative": max_abs_observable(
            loaded.observables, "liquid_volume_balance_error_relative"
        ),
        "pressure_residual": _maximum_absolute_evidence(
            max_abs_observable(loaded.observables, "pressure_residual"),
            diagnostics.get("maximum_pressure_relative_residual"),
        ),
        "projected_divergence_s_inv": _maximum_absolute_evidence(
            max_abs_observable(loaded.observables, "max_divergence"),
            diagnostics.get("maximum_projected_divergence_s_inv"),
        ),
        "advective_cfl": _maximum_absolute_evidence(
            max_abs_observable(loaded.observables, "cfl_advective"),
            diagnostics.get("maximum_advective_cfl"),
        ),
        "cumulative_phase_clip_relative": diagnostics.get(
            "cumulative_phase_clip_relative"
        ),
        "cumulative_phase_repair_relative": diagnostics.get(
            "cumulative_phase_repair_relative"
        ),
    }
    threshold_names = {
        "liquid_mass_error_relative": "liquid_mass_error_relative_max",
        "liquid_volume_balance_error_relative": (
            "liquid_volume_balance_error_relative_max"
        ),
        "pressure_residual": "pressure_residual_max",
        "projected_divergence_s_inv": "projected_divergence_max_s_inv",
        "advective_cfl": "advective_cfl_max",
        "cumulative_phase_clip_relative": "cumulative_phase_clip_relative_max",
        "cumulative_phase_repair_relative": "cumulative_phase_repair_relative_max",
    }
    checks: dict[str, Any] = {}
    passed = True
    for name, threshold_name in threshold_names.items():
        threshold = _finite_gate(gates, threshold_name)
        raw_value = values[name]
        try:
            value = abs(float(raw_value)) if raw_value is not None else None
        except (TypeError, ValueError):
            value = None
        ok = value is not None and math.isfinite(value) and value <= threshold
        checks[name] = {"pass": ok, "value": value, "maximum": threshold}
        passed = passed and ok
    numerical_gate = _nested(diagnostics, "numerical_quality_gate", "pass") is True
    return {
        "pass": passed and numerical_gate,
        "backend_fail_closed_gate_pass": numerical_gate,
        "checks": checks,
    }


def _maximum_absolute_evidence(*values: Any) -> float | None:
    finite: list[float] = []
    for raw in values:
        if raw is None or isinstance(raw, bool):
            continue
        try:
            value = abs(float(raw))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            finite.append(value)
    return max(finite) if finite else None

def _series(loaded: LoadedCase, name: str) -> SeriesData:
    if name not in loaded.observables:
        raise UserFacingError(f"{loaded.case['id']}: missing observable {name}")
    return SeriesData(loaded.time_s, loaded.observables[name])


def _effective_time_step_gate(
    group_name: str,
    group_cases: list[LoadedCase],
    *,
    minimum_configured_step_fraction: float = 0.95,
) -> dict[str, Any]:
    """Verify that completed runs actually exercised the registered step path."""

    if group_name not in {"spatial", "temporal"}:
        raise UserFacingError("effective time-step gate requires spatial or temporal group")
    if (
        not math.isfinite(minimum_configured_step_fraction)
        or minimum_configured_step_fraction <= 0.0
        or minimum_configured_step_fraction > 1.0
    ):
        raise UserFacingError(
            "configured time-step active fraction must be finite in (0, 1]"
        )
    ordered = sorted(group_cases, key=lambda item: int(item.case["level"]))
    records: list[dict[str, Any]] = []
    for loaded in ordered:
        case_id = str(loaded.case["id"])
        configured = float(
            _nested(loaded.config, "high_fidelity", "time_step_s")
        )
        raw_steps = loaded.fields.get("diagnostic_dt_s")
        steps = np.asarray(raw_steps, dtype=np.float64).reshape(-1)
        valid = bool(
            steps.size > 0
            and np.all(np.isfinite(steps))
            and np.all(steps > 0.0)
            and math.isfinite(configured)
            and configured > 0.0
        )
        if valid:
            tolerance = max(1.0e-15, 1.0e-9 * configured)
            maximum_actual = float(np.max(steps))
            at_configured = np.abs(steps - configured) <= tolerance
            active_fraction = float(np.mean(at_configured))
            maximum_matches = math.isclose(
                maximum_actual,
                configured,
                rel_tol=1.0e-9,
                abs_tol=1.0e-15,
            )
        else:
            maximum_actual = None
            active_fraction = 0.0
            maximum_matches = False
        record_pass = bool(
            valid
            and maximum_matches
            and active_fraction >= minimum_configured_step_fraction
        )
        records.append(
            {
                "id": case_id,
                "pass": record_pass,
                "step_count": int(steps.size),
                "configured_maximum_time_step_s": configured,
                "maximum_actual_time_step_s": maximum_actual,
                "configured_step_active_fraction": active_fraction,
                "minimum_configured_step_active_fraction": (
                    minimum_configured_step_fraction
                ),
                "maximum_actual_matches_configured": maximum_matches,
            }
        )

    configured_values = [
        float(item["configured_maximum_time_step_s"]) for item in records
    ]
    actual_values = [item["maximum_actual_time_step_s"] for item in records]
    actual_numeric = [float(value) for value in actual_values if value is not None]
    if group_name == "spatial":
        registered_structure_valid = _step_values_close(configured_values)
        effective_structure_valid = (
            len(actual_numeric) == len(records)
            and _step_values_close(actual_numeric)
        )
    else:
        registered_structure_valid = _step_values_strictly_decrease(
            configured_values
        )
        effective_structure_valid = (
            len(actual_numeric) == len(records)
            and _step_values_strictly_decrease(actual_numeric)
        )
    return {
        "pass": bool(
            len(records) >= 3
            and all(bool(item["pass"]) for item in records)
            and registered_structure_valid
            and effective_structure_valid
        ),
        "group": group_name,
        "registered_structure_valid": registered_structure_valid,
        "effective_structure_valid": effective_structure_valid,
        "cases": records,
    }


def _step_values_close(values: list[float]) -> bool:
    if not values:
        return False
    reference = values[0]
    return all(
        math.isclose(value, reference, rel_tol=1.0e-9, abs_tol=1.0e-15)
        for value in values[1:]
    )


def _step_values_strictly_decrease(values: list[float]) -> bool:
    return len(values) >= 2 and all(
        left > right
        and not math.isclose(left, right, rel_tol=1.0e-9, abs_tol=1.0e-15)
        for left, right in zip(values, values[1:])
    )

def _configuration_gate_group(
    group_cases: list[LoadedCase],
    gates: Mapping[str, Any],
) -> dict[str, Any]:
    ordered = sorted(group_cases, key=lambda item: int(item.case["level"]))
    if len(ordered) < 3:
        return {"pass": False, "reason": "fewer than three levels"}
    selected = ordered[-3:]
    audits = [grid_audit(item.config) for item in selected]
    coarsest, _, finest = audits
    checks = {
        "coarsest_bar_diameter_cells_worst_axis": {
            "value": coarsest.bar_diameter_cells_worst_axis,
            "minimum": _finite_gate(
                gates, "coarsest_bar_diameter_cells_worst_axis_min"
            ),
        },
        "finest_bar_diameter_cells_worst_axis": {
            "value": finest.bar_diameter_cells_worst_axis,
            "minimum": _finite_gate(
                gates, "finest_bar_diameter_cells_worst_axis_min"
            ),
        },
        "maximum_grid_aspect_ratio": {
            "value": max(item.aspect_ratio for item in audits),
            "maximum": _finite_gate(gates, "grid_aspect_ratio_max"),
        },
        "minimum_interface_width_cells_worst_axis": {
            "value": min(item.interface_width_cells_worst_axis for item in audits),
            "minimum": _finite_gate(
                gates, "interface_width_cells_worst_axis_min"
            ),
        },
        "finest_nominal_bar_voxel_count": {
            "value": finest.nominal_bar_voxel_count,
            "minimum": _finite_gate(gates, "finest_nominal_bar_voxel_count_min"),
        },
    }
    checks["coarsest_bar_diameter_cells_worst_axis"]["pass"] = (
        checks["coarsest_bar_diameter_cells_worst_axis"]["value"]
        >= checks["coarsest_bar_diameter_cells_worst_axis"]["minimum"]
    )
    checks["finest_bar_diameter_cells_worst_axis"]["pass"] = (
        checks["finest_bar_diameter_cells_worst_axis"]["value"]
        >= checks["finest_bar_diameter_cells_worst_axis"]["minimum"]
    )
    checks["maximum_grid_aspect_ratio"]["pass"] = (
        checks["maximum_grid_aspect_ratio"]["value"]
        <= checks["maximum_grid_aspect_ratio"]["maximum"]
    )
    checks["minimum_interface_width_cells_worst_axis"]["pass"] = (
        checks["minimum_interface_width_cells_worst_axis"]["value"]
        >= checks["minimum_interface_width_cells_worst_axis"]["minimum"]
    )
    checks["finest_nominal_bar_voxel_count"]["pass"] = (
        checks["finest_nominal_bar_voxel_count"]["value"]
        >= checks["finest_nominal_bar_voxel_count"]["minimum"]
    )
    return {
        "pass": all(bool(item["pass"]) for item in checks.values()),
        "checks": checks,
    }
