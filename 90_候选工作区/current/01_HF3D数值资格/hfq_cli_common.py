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
    if canonical_sha256(generated_config) != canonical_sha256(archived_config):
        raise UserFacingError(f"{case_id}: archived config differs from generated config")
    result = archive.result
    status = getattr(result.status, "value", str(result.status))
    return LoadedCase(
        case=dict(case),
        config=archived_config,
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
    termination = _to_plain_mapping(loaded.manifest.get("termination"))
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
        "config_sha256": canonical_sha256(loaded.config),
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
        "pressure_residual": max_abs_observable(loaded.observables, "pressure_residual"),
        "projected_divergence_s_inv": max_abs_observable(
            loaded.observables, "max_divergence"
        ),
        "advective_cfl": max_abs_observable(loaded.observables, "cfl_advective"),
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

def _series(loaded: LoadedCase, name: str) -> SeriesData:
    if name not in loaded.observables:
        raise UserFacingError(f"{loaded.case['id']}: missing observable {name}")
    return SeriesData(loaded.time_s, loaded.observables[name])

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
