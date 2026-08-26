"""Frozen spatial/time refinement campaign generation."""
from __future__ import annotations
from copy import deepcopy
import math
from pathlib import Path
from typing import Any, Mapping
from hfq_types import (
    ALLOWED_DISCRETIZATION_PATHS,
    DEFAULT_BINARY_OBSERVABLES,
    DEFAULT_SMOOTH_OBSERVABLES,
    JsonObject,
    QualificationError,
)
from hfq_json import (
    _finite_positive,
    _positive_int,
    _require_mapping,
    canonical_sha256,
    physical_signature,
    read_json,
    write_json,
)
from hfq_grid import apply_overrides, grid_audit, make_refinement_config
from hfq_quality_contract import (
    LEGACY_QUALITY_CONTRACT_ID,
    audit_quality_contract,
    quality_contract_id,
)

_CAPILLARY_STABILITY_COEFFICIENT = 0.20
_DESIGN_RELATIVE_TOLERANCE = 1.0e-12


def capillary_time_step_limit(config: Mapping[str, Any]) -> float:
    """Return the static HF3D capillary step ceiling for one raw config.

    This mirrors the frozen local-law integrator's capillary limiter.  It is a
    necessary preflight bound: other state-dependent limits may reduce the
    effective step further and are audited from completed archives.
    """

    fluid = _require_mapping(config.get("fluid"), "fluid")
    gas_density = _finite_positive(
        fluid.get("gas_density_kg_m3"), "fluid.gas_density_kg_m3"
    )
    surface_tension = _finite_positive(
        fluid.get("surface_tension_n_m"), "fluid.surface_tension_n_m"
    )
    h_min = grid_audit(config).h_min_m
    return _CAPILLARY_STABILITY_COEFFICIENT * math.sqrt(
        gas_density * h_min**3 / (math.pi * surface_tension)
    )


def audit_refinement_design(manifest_path: str | Path) -> JsonObject:
    """Audit whether registered maximum steps can identify space/time error."""

    manifest_file = Path(manifest_path).resolve()
    manifest = read_json(manifest_file)
    raw_cases = manifest.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise QualificationError("manifest has no cases")

    records: list[JsonObject] = []
    reasons: list[str] = []
    for raw_case in raw_cases:
        if not isinstance(raw_case, Mapping):
            raise QualificationError("manifest contains a non-object case")
        case_id = raw_case.get("id")
        group = raw_case.get("group")
        config_file = raw_case.get("config_file")
        if not isinstance(case_id, str) or not case_id:
            raise QualificationError("manifest case requires a non-empty id")
        if group not in {"spatial", "temporal"}:
            raise QualificationError(f"{case_id}: group must be spatial or temporal")
        if not isinstance(config_file, str) or not config_file:
            raise QualificationError(f"{case_id}: config_file must be a non-empty path")
        config = read_json(manifest_file.parent / config_file)
        high_fidelity = _require_mapping(
            config.get("high_fidelity"), f"{case_id}.high_fidelity"
        )
        configured_step = _finite_positive(
            high_fidelity.get("time_step_s"),
            f"{case_id}.high_fidelity.time_step_s",
        )
        manifest_step = _finite_positive(
            raw_case.get("maximum_time_step_s"),
            f"{case_id}.maximum_time_step_s",
        )
        capillary_limit = capillary_time_step_limit(config)
        manifest_matches = math.isclose(
            configured_step,
            manifest_step,
            rel_tol=_DESIGN_RELATIVE_TOLERANCE,
            abs_tol=0.0,
        )
        configured_active = configured_step <= capillary_limit * (
            1.0 + _DESIGN_RELATIVE_TOLERANCE
        )
        if not manifest_matches:
            reasons.append(f"{case_id}: manifest/config time-step mismatch")
        if not configured_active:
            reasons.append(
                f"{case_id}: configured {configured_step:.9g} s exceeds static "
                f"capillary ceiling {capillary_limit:.9g} s"
            )
        level = raw_case.get("level")
        if isinstance(level, bool) or not isinstance(level, int) or level < 0:
            raise QualificationError(f"{case_id}: level must be an integer >= 0")
        records.append(
            {
                "id": case_id,
                "group": group,
                "level": level,
                "configured_maximum_time_step_s": configured_step,
                "manifest_maximum_time_step_s": manifest_step,
                "manifest_matches_config": manifest_matches,
                "static_capillary_time_step_ceiling_s": capillary_limit,
                "configured_step_active_under_static_capillary_bound": (
                    configured_active
                ),
                "static_effective_maximum_time_step_s": min(
                    configured_step, capillary_limit
                ),
            }
        )

    groups: JsonObject = {}
    for group_name in ("spatial", "temporal"):
        selected = sorted(
            (item for item in records if item["group"] == group_name),
            key=lambda item: int(item["level"]),
        )
        configured = [
            float(item["configured_maximum_time_step_s"]) for item in selected
        ]
        effective = [
            float(item["static_effective_maximum_time_step_s"]) for item in selected
        ]
        has_three_levels = len(selected) >= 3
        all_registered_steps_active = bool(selected) and all(
            bool(item["configured_step_active_under_static_capillary_bound"])
            and bool(item["manifest_matches_config"])
            for item in selected
        )
        if group_name == "spatial":
            registered_structure_valid = _all_close(configured)
            effective_structure_valid = _all_close(effective)
            if not registered_structure_valid:
                reasons.append("spatial: registered maximum time steps are not common")
            if not effective_structure_valid:
                reasons.append(
                    "spatial: static effective time steps differ across grids"
                )
        else:
            registered_structure_valid = _strictly_decreasing(configured)
            effective_structure_valid = _strictly_decreasing(effective)
            if not registered_structure_valid:
                reasons.append(
                    "temporal: registered maximum time steps are not strictly refined"
                )
            if not effective_structure_valid:
                reasons.append(
                    "temporal: static effective time steps collapse under stability limits"
                )
        if not has_three_levels:
            reasons.append(f"{group_name}: fewer than three refinement levels")
        group_pass = (
            has_three_levels
            and all_registered_steps_active
            and registered_structure_valid
            and effective_structure_valid
        )
        groups[group_name] = {
            "pass": group_pass,
            "case_ids_in_level_order": [str(item["id"]) for item in selected],
            "configured_maximum_time_step_s": configured,
            "static_capillary_time_step_ceiling_s": [
                float(item["static_capillary_time_step_ceiling_s"])
                for item in selected
            ],
            "static_effective_maximum_time_step_s": effective,
            "all_registered_steps_active": all_registered_steps_active,
            "registered_structure_valid": registered_structure_valid,
            "effective_structure_valid": effective_structure_valid,
        }

    return {
        "pass": bool(groups["spatial"]["pass"] and groups["temporal"]["pass"]),
        "gate_identity": "HF3D_STATIC_TIME_STEP_IDENTIFIABILITY_V1",
        "capillary_limiter": {
            "coefficient": _CAPILLARY_STABILITY_COEFFICIENT,
            "formula": "coefficient*sqrt(rho_g*h_min^3/(pi*sigma))",
            "scope": (
                "necessary static preflight only; completed runs still require "
                "an effective-step audit for dynamic limiters"
            ),
        },
        "groups": groups,
        "cases": records,
        "reasons": list(dict.fromkeys(reasons)),
    }


def _all_close(values: list[float]) -> bool:
    if not values:
        return False
    reference = values[0]
    return all(
        math.isclose(
            value,
            reference,
            rel_tol=_DESIGN_RELATIVE_TOLERANCE,
            abs_tol=0.0,
        )
        for value in values[1:]
    )


def _strictly_decreasing(values: list[float]) -> bool:
    return len(values) >= 2 and all(
        left > right
        and not math.isclose(
            left,
            right,
            rel_tol=_DESIGN_RELATIVE_TOLERANCE,
            abs_tol=0.0,
        )
        for left, right in zip(values, values[1:])
    )

def generate_campaign(
    spec_path: str | Path,
    output_dir: str | Path,
) -> JsonObject:
    spec_file = Path(spec_path).resolve()
    spec = read_json(spec_file)
    if spec.get("schema_version") != 1:
        raise QualificationError("campaign schema_version must be 1")
    base_ref = spec.get("base_config")
    if not isinstance(base_ref, str) or not base_ref:
        raise QualificationError("campaign base_config must be a non-empty path")
    base_path = (spec_file.parent / base_ref).resolve()
    base = read_json(base_path)
    overrides = spec.get("case_overrides", {})
    if not isinstance(overrides, Mapping):
        raise QualificationError("case_overrides must be an object")
    base = apply_overrides(base, overrides)
    base["backend"] = "high_fidelity"
    out = Path(output_dir)
    if out.exists() and any(out.iterdir()):
        raise QualificationError(f"output directory is not empty: {out}")
    out.mkdir(parents=True, exist_ok=True)

    cases: list[JsonObject] = []
    spatial = _require_mapping(spec.get("spatial_study"), "spatial_study")
    targets = spatial.get("target_bar_diameter_cells_worst_axis")
    if not isinstance(targets, list) or len(targets) < 3:
        raise QualificationError("spatial study requires at least three target resolutions")
    spatial_targets = sorted(
        _finite_positive(value, "spatial target resolution") for value in targets
    )
    common_dt = _finite_positive(
        spatial.get("common_time_step_s"), "spatial_study.common_time_step_s"
    )
    multiple = _positive_int(
        spatial.get("round_to_multiple", 2), "spatial_study.round_to_multiple"
    )
    for level, target in enumerate(spatial_targets):
        config = make_refinement_config(
            base,
            target_bar_cells_worst_axis=target,
            time_step_s=common_dt,
            round_to_multiple=multiple,
        )
        case_id = f"space_L{level}_{target:g}cells"
        config_path = out / f"{case_id}.json"
        write_json(config_path, config)
        audit = grid_audit(config)
        cases.append(
            {
                "id": case_id,
                "group": "spatial",
                "level": level,
                "target_bar_diameter_cells_worst_axis": target,
                "config_file": config_path.name,
                "config_sha256": canonical_sha256(config),
                "effective_refinement_coordinate_m": audit.h_max_m,
                "maximum_time_step_s": common_dt,
                "grid": audit.to_dict(),
            }
        )

    temporal = _require_mapping(spec.get("temporal_study"), "temporal_study")
    temporal_target = _finite_positive(
        temporal.get("target_bar_diameter_cells_worst_axis"),
        "temporal_study.target_bar_diameter_cells_worst_axis",
    )
    time_steps = temporal.get("time_step_s")
    if not isinstance(time_steps, list) or len(time_steps) < 3:
        raise QualificationError("temporal study requires at least three time steps")
    temporal_steps = sorted(
        (_finite_positive(value, "temporal time step") for value in time_steps),
        reverse=True,
    )
    for level, dt_s in enumerate(temporal_steps):
        config = make_refinement_config(
            base,
            target_bar_cells_worst_axis=temporal_target,
            time_step_s=dt_s,
            round_to_multiple=multiple,
        )
        case_id = f"time_L{level}_{dt_s:.3e}s".replace("+", "")
        config_path = out / f"{case_id}.json"
        write_json(config_path, config)
        audit = grid_audit(config)
        cases.append(
            {
                "id": case_id,
                "group": "temporal",
                "level": level,
                "target_bar_diameter_cells_worst_axis": temporal_target,
                "config_file": config_path.name,
                "config_sha256": canonical_sha256(config),
                "effective_refinement_coordinate_m": dt_s,
                "maximum_time_step_s": dt_s,
                "grid": audit.to_dict(),
            }
        )

    # A finest spatial case is often identical to the finest temporal case.
    # Record an alias instead of scheduling the same expensive HF3D run twice.
    first_case_by_hash: dict[str, dict[str, Any]] = {}
    for case in cases:
        digest = str(case["config_sha256"])
        original = first_case_by_hash.get(digest)
        if original is None:
            first_case_by_hash[digest] = case
            continue
        duplicate_path = out / str(case["config_file"])
        if duplicate_path.exists() and duplicate_path.name != original["config_file"]:
            duplicate_path.unlink()
        case["reuse_case_id"] = original["id"]
        case["config_file"] = original["config_file"]

    signatures = {
        canonical_sha256(physical_signature(read_json(out / case["config_file"])))
        for case in cases
    }
    if len(signatures) != 1:
        raise QualificationError(
            "generated cases differ in physical inputs; only discretization controls may vary"
        )
    observable_spec = _require_mapping(spec.get("observables", {}), "observables")
    smooth_observables = list(
        _string_sequence(
            observable_spec.get("smooth", list(DEFAULT_SMOOTH_OBSERVABLES)),
            "observables.smooth",
        )
    )
    binary_observables = list(
        _string_sequence(
            observable_spec.get("binary", list(DEFAULT_BINARY_OBSERVABLES)),
            "observables.binary",
        )
    )
    frozen_gates = deepcopy(
        dict(_require_mapping(spec.get("gates", {}), "gates"))
    )
    manifest = {
        "schema_version": 1,
        "study_name": spec.get("study_name", "HF3D numerical qualification"),
        "scope": spec.get("scope", "unspecified"),
        "base_config_source": str(base_ref),
        "base_config_sha256": canonical_sha256(base),
        "physical_signature_sha256": next(iter(signatures)),
        "allowed_discretization_paths": list(ALLOWED_DISCRETIZATION_PATHS),
        "cases": cases,
        "unique_run_count": len(first_case_by_hash),
        "observables": {
            "smooth": smooth_observables,
            "binary": binary_observables,
        },
        "gates": frozen_gates,
        "execution_requirements": deepcopy(
            dict(
                _require_mapping(
                    spec.get("execution_requirements", {}),
                    "execution_requirements",
                )
            )
        ),
        "claim_boundary": {
            "maximum_possible_status": _maximum_claim_status(spec),
            "experimental_validation": "NOT_PERFORMED",
            "apparatus_prediction_allowed": False,
        },
    }
    raw_numerical_quality = frozen_gates.get("numerical_quality")
    numerical_quality = (
        dict(raw_numerical_quality)
        if isinstance(raw_numerical_quality, Mapping)
        else {}
    )
    if quality_contract_id(numerical_quality) != LEGACY_QUALITY_CONTRACT_ID:
        manifest["quality_contract_audit"] = audit_quality_contract(
            numerical_quality,
            smooth_observables=smooth_observables,
        )
    manifest_path = out / "cases_manifest.json"
    write_json(manifest_path, manifest)
    manifest["refinement_design_audit"] = audit_refinement_design(manifest_path)
    write_json(manifest_path, manifest)
    return manifest

def _maximum_claim_status(spec: Mapping[str, Any]) -> str:
    value = spec.get(
        "maximum_claim_status",
        "NUMERICALLY_QUALIFIED_WITHIN_FROZEN_MODEL",
    )
    allowed = {
        "NUMERICAL_SCREENING_ONLY",
        "NUMERICALLY_QUALIFIED_SMOOTH_REGIME_ONLY",
        "NUMERICALLY_QUALIFIED_WITHIN_FROZEN_MODEL",
    }
    if value not in allowed:
        raise QualificationError(
            "maximum_claim_status must be one of: " + ", ".join(sorted(allowed))
        )
    return str(value)

def _string_sequence(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise QualificationError(f"{label} must be a non-empty array")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise QualificationError(f"{label} entries must be non-empty strings")
        result.append(item)
    return tuple(result)
