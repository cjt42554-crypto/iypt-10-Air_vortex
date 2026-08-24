"""Frozen spatial/time refinement campaign generation."""
from __future__ import annotations
from copy import deepcopy
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
            "smooth": list(
                _string_sequence(
                    _require_mapping(spec.get("observables", {}), "observables").get(
                        "smooth", list(DEFAULT_SMOOTH_OBSERVABLES)
                    ),
                    "observables.smooth",
                )
            ),
            "binary": list(
                _string_sequence(
                    _require_mapping(spec.get("observables", {}), "observables").get(
                        "binary", list(DEFAULT_BINARY_OBSERVABLES)
                    ),
                    "observables.binary",
                )
            ),
        },
        "gates": deepcopy(dict(_require_mapping(spec.get("gates", {}), "gates"))),
        "claim_boundary": {
            "maximum_possible_status": _maximum_claim_status(spec),
            "experimental_validation": "NOT_PERFORMED",
            "apparatus_prediction_allowed": False,
        },
    }
    write_json(out / "cases_manifest.json", manifest)
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
