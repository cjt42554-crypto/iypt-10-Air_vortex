#!/usr/bin/env python3
"""Candidate-only D1 geometry installation with complete mask dependencies.

The formal HF3D solver remains untouched.  This module accepts a Boolean bar
mask, recomputes every field whose value depends on that mask, and installs the
fields before pressure projection, state initialisation, or frame recording.
It also binds nominal G1 masks to the committed D1 static audit.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parents[2]
FORMAL_ROOT = REPOSITORY_ROOT / "02_模拟程序"
FORMAL_SOURCE = FORMAL_ROOT / "src"
STATIC_ROOT = HERE.parent / "04_D1可控磁子离散"
STATIC_AUDIT_PATH = STATIC_ROOT / "reports" / "d1_static_audit.json"
if str(FORMAL_SOURCE) not in sys.path:
    sys.path.insert(0, str(FORMAL_SOURCE))
if str(STATIC_ROOT) not in sys.path:
    sys.path.insert(0, str(STATIC_ROOT))

from airvortex.numerics.cartesian3d import (
    BAR_WALL,
    FLOOR_WALL,
    SIDE_WALL,
    ImmersedCartesianGrid,
)
from controlled_bar_geometry import (
    Capsule,
    G1Build,
    GridSpec,
    build_g1_mask,
    mask_sha256,
)

BoolArray = NDArray[np.bool_]

REBUILT_FIELDS: tuple[str, ...] = (
    "bar_mask",
    "active_mask",
    "open_x",
    "open_y",
    "open_z",
    "index",
    "nearest_solid_distance_m",
    "nearest_solid_kind",
    "wall_normal_x",
    "wall_normal_y",
    "wall_normal_z",
)


class ShadowGridError(RuntimeError):
    """Raised when a candidate geometry cannot satisfy the frozen contract."""


@dataclass(frozen=True, slots=True)
class BoundG1Mask:
    """A rebuilt G1 mask whose identity matches the static audit binding."""

    mask: BoolArray
    report: Mapping[str, Any]
    expected_mask_sha256: str
    expected_bar_cell_count: int
    cells_per_bar_diameter: float


def _strict_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ShadowGridError(f"cannot read strict JSON object {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ShadowGridError(f"JSON root must be an object: {path}")
    return value


def array_sha256(value: NDArray[Any]) -> str:
    """Hash dtype, shape, and C-order bytes without path-dependent metadata."""

    array = np.asarray(value)
    digest = hashlib.sha256()
    digest.update(array.dtype.str.encode("ascii"))
    digest.update(b"\0")
    digest.update(json.dumps(list(array.shape), separators=(",", ":")).encode("ascii"))
    digest.update(b"\0")
    digest.update(np.ascontiguousarray(array).tobytes(order="C"))
    return digest.hexdigest()


def snapshot_rebuilt_fields(grid: ImmersedCartesianGrid) -> dict[str, NDArray[Any]]:
    """Copy every preregistered mask-dependent array from a grid."""

    return {name: np.asarray(getattr(grid, name)).copy() for name in REBUILT_FIELDS}


def rebuilt_field_manifest(grid: ImmersedCartesianGrid) -> dict[str, dict[str, Any]]:
    """Return machine-readable dtype, shape, and SHA-256 identities."""

    result: dict[str, dict[str, Any]] = {}
    for name in REBUILT_FIELDS:
        value = np.asarray(getattr(grid, name))
        result[name] = {
            "dtype": value.dtype.str,
            "shape": list(value.shape),
            "sha256": array_sha256(value),
        }
    return result


def compare_rebuilt_fields(
    expected: Mapping[str, NDArray[Any]],
    observed: ImmersedCartesianGrid | Mapping[str, NDArray[Any]],
) -> dict[str, Any]:
    """Compare all required arrays by field, dtype, shape, and element bytes."""

    fields: dict[str, dict[str, Any]] = {}
    for name in REBUILT_FIELDS:
        left = np.asarray(expected[name])
        right = np.asarray(
            getattr(observed, name)
            if isinstance(observed, ImmersedCartesianGrid)
            else observed[name]
        )
        dtype_equal = left.dtype == right.dtype
        shape_equal = left.shape == right.shape
        values_equal = bool(shape_equal and np.array_equal(left, right, equal_nan=True))
        fields[name] = {
            "dtype_equal": bool(dtype_equal),
            "shape_equal": bool(shape_equal),
            "values_equal": values_equal,
            "expected_sha256": array_sha256(left),
            "observed_sha256": array_sha256(right),
        }
    return {
        "pass": all(
            item["dtype_equal"] and item["shape_equal"] and item["values_equal"]
            for item in fields.values()
        ),
        "fields": fields,
    }


def _validated_bar_mask(
    grid: ImmersedCartesianGrid,
    bar_mask: NDArray[np.bool_],
) -> BoolArray:
    candidate = np.asarray(bar_mask)
    if candidate.dtype != np.bool_:
        raise ShadowGridError("candidate bar mask dtype must be bool")
    if candidate.shape != grid.shape:
        raise ShadowGridError(
            f"candidate bar mask shape {candidate.shape} does not match {grid.shape}"
        )
    candidate = candidate.copy(order="C")
    if not np.any(candidate):
        raise ShadowGridError("candidate bar mask must contain at least one solid cell")
    if np.any(candidate & ~grid.vessel_mask):
        raise ShadowGridError("candidate bar mask contains cells outside the vessel")
    return candidate


def install_shadow_bar_mask(
    grid: ImmersedCartesianGrid,
    bar_mask: NDArray[np.bool_],
) -> dict[str, Any]:
    """Recompute and install the complete geometry state for ``bar_mask``.

    The function deliberately has no G0 fast path.  Passing the formal G0 mask
    executes the same analytic reconstruction as any perturbed or G1 mask.
    """

    if not isinstance(grid, ImmersedCartesianGrid):
        raise TypeError("grid must be an ImmersedCartesianGrid")
    candidate = _validated_bar_mask(grid, bar_mask)

    xx = np.asarray(grid.x_cell, dtype=np.float64)
    yy = np.asarray(grid.y_cell, dtype=np.float64)
    zz = np.asarray(grid.z_cell, dtype=np.float64)
    radius = np.hypot(xx, yy)
    vessel_mask = radius < grid.radius_m
    if not np.array_equal(vessel_mask, grid.vessel_mask):
        raise ShadowGridError("analytic vessel reconstruction changed vessel_mask")

    segment_half = max(grid.bar_half_length_m - grid.bar_radius_m, 0.0)
    closest_x = np.clip(xx, -segment_half, segment_half)
    bx = xx - closest_x
    by = yy
    bz = zz - grid.bar_centre_z_m
    centreline_distance = np.sqrt(bx * bx + by * by + bz * bz)
    bar_signed_distance = centreline_distance - grid.bar_radius_m

    active_mask = vessel_mask & ~candidate
    if not np.any(active_mask[:, :, -1]):
        raise ShadowGridError("candidate geometry leaves no atmospheric top cells")

    safe_bar_distance = np.maximum(centreline_distance, np.finfo(float).tiny)
    bar_nx = bx / safe_bar_distance
    bar_ny = by / safe_bar_distance
    bar_nz = bz / safe_bar_distance
    safe_radius = np.maximum(radius, np.finfo(float).tiny)
    side_nx = -xx / safe_radius
    side_ny = -yy / safe_radius
    side_nz = np.zeros_like(xx)
    floor_nx = np.zeros_like(xx)
    floor_ny = np.zeros_like(xx)
    floor_nz = np.ones_like(xx)
    distances = np.stack(
        (
            grid.radius_m - radius,
            zz,
            bar_signed_distance,
        ),
        axis=0,
    )
    nearest = np.argmin(distances, axis=0)
    nearest_solid_distance = np.min(distances, axis=0)
    nearest_solid_kind = np.choose(
        nearest,
        np.asarray([SIDE_WALL, FLOOR_WALL, BAR_WALL], dtype=np.int8),
    ).astype(np.int8)
    wall_normal_x = np.choose(nearest, (side_nx, floor_nx, bar_nx))
    wall_normal_y = np.choose(nearest, (side_ny, floor_ny, bar_ny))
    wall_normal_z = np.choose(nearest, (side_nz, floor_nz, bar_nz))
    for value in (
        nearest_solid_distance,
        wall_normal_x,
        wall_normal_y,
        wall_normal_z,
    ):
        value[~active_mask] = 0.0

    open_x = np.zeros((grid.nx + 1, grid.ny, grid.nz), dtype=bool)
    open_y = np.zeros((grid.nx, grid.ny + 1, grid.nz), dtype=bool)
    open_z = np.zeros((grid.nx, grid.ny, grid.nz + 1), dtype=bool)
    open_x[1:-1] = active_mask[:-1] & active_mask[1:]
    open_y[:, 1:-1] = active_mask[:, :-1] & active_mask[:, 1:]
    open_z[:, :, 1:-1] = active_mask[:, :, :-1] & active_mask[:, :, 1:]
    open_z[:, :, -1] = active_mask[:, :, -1]
    index = np.full(grid.shape, -1, dtype=np.int64)
    index[active_mask] = np.arange(int(np.sum(active_mask)), dtype=np.int64)

    # Publish only after every derived field has been constructed successfully.
    grid.vessel_mask = vessel_mask
    grid.bar_signed_distance_m = bar_signed_distance
    grid.bar_mask = candidate
    grid.active_mask = active_mask
    grid.open_x = open_x
    grid.open_y = open_y
    grid.open_z = open_z
    grid.index = index
    grid.nearest_solid_distance_m = nearest_solid_distance
    grid.nearest_solid_kind = nearest_solid_kind
    grid.wall_normal_x = wall_normal_x
    grid.wall_normal_y = wall_normal_y
    grid.wall_normal_z = wall_normal_z

    return {
        "mask_sha256": mask_sha256(grid.bar_mask),
        "bar_cell_count": int(np.sum(grid.bar_mask)),
        "active_cell_count": grid.active_cell_count,
        "rebuilt_fields": rebuilt_field_manifest(grid),
        "installer_has_g0_noop_special_case": False,
        "analytic_bar_signed_distance_sha256": array_sha256(grid.bar_signed_distance_m),
    }


def _nominal_static_case(
    audit: Mapping[str, Any],
    grid: ImmersedCartesianGrid,
) -> Mapping[str, Any]:
    cases = audit.get("nominal_cases")
    if not isinstance(cases, Sequence):
        raise ShadowGridError("static audit nominal_cases is missing")
    matches: list[Mapping[str, Any]] = []
    for raw in cases:
        if not isinstance(raw, Mapping):
            continue
        g1 = raw.get("g1")
        if not isinstance(g1, Mapping):
            continue
        if list(g1.get("grid", [])) == [grid.nx, grid.ny, grid.nz]:
            matches.append(raw)
    if len(matches) != 1:
        raise ShadowGridError(
            f"expected one nominal static binding for grid {grid.shape}, found {len(matches)}"
        )
    return matches[0]


def build_bound_nominal_g1_mask(
    grid: ImmersedCartesianGrid,
    *,
    static_audit_path: Path = STATIC_AUDIT_PATH,
    selection_cache: dict[str, tuple[object, dict[str, object]]] | None = None,
) -> BoundG1Mask:
    """Rebuild G1 and require its nominal hash/count to match the static audit."""

    audit = _strict_json_object(static_audit_path)
    if audit.get("gate_pass") is not True:
        raise ShadowGridError("bound D1 static audit is not a passing audit")
    case = _nominal_static_case(audit, grid)
    g1_expected = case.get("g1")
    if not isinstance(g1_expected, Mapping):
        raise ShadowGridError("nominal static case has no G1 record")
    expected_hash = g1_expected.get("mask_sha256")
    expected_count = g1_expected.get("bar_cell_count")
    level = case.get("cells_per_bar_diameter")
    if not isinstance(expected_hash, str) or len(expected_hash) != 64:
        raise ShadowGridError("static G1 mask SHA-256 binding is invalid")
    if isinstance(expected_count, bool) or not isinstance(expected_count, int):
        raise ShadowGridError("static G1 bar-cell-count binding is invalid")
    if isinstance(level, bool) or not isinstance(level, (int, float)):
        raise ShadowGridError("static G1 resolution binding is invalid")

    spec = GridSpec(
        grid.nx,
        grid.ny,
        grid.nz,
        grid.radius_m,
        grid.height_m,
    )
    capsule = Capsule(
        grid.bar_half_length_m,
        grid.bar_radius_m,
        (0.0, 0.0, grid.bar_centre_z_m),
    )
    build: G1Build = build_g1_mask(
        spec,
        capsule,
        selection_cache=selection_cache,
    )
    observed_hash = mask_sha256(build.mask)
    observed_count = int(np.sum(build.mask))
    if observed_hash != expected_hash or observed_count != expected_count:
        raise ShadowGridError(
            "rebuilt G1 mask does not match the static audit binding: "
            f"hash={observed_hash}, cells={observed_count}"
        )
    reported_resolution = 2.0 * grid.bar_radius_m / grid.minimum_spacing_m
    if not math.isclose(reported_resolution, float(level), rel_tol=0.0, abs_tol=0.1):
        raise ShadowGridError(
            f"grid resolution {reported_resolution} does not match binding {level}"
        )
    return BoundG1Mask(
        mask=build.mask.copy(),
        report=build.report,
        expected_mask_sha256=expected_hash,
        expected_bar_cell_count=expected_count,
        cells_per_bar_diameter=float(level),
    )


__all__ = [
    "REBUILT_FIELDS",
    "BoundG1Mask",
    "ShadowGridError",
    "array_sha256",
    "build_bound_nominal_g1_mask",
    "compare_rebuilt_fields",
    "install_shadow_bar_mask",
    "rebuilt_field_manifest",
    "snapshot_rebuilt_fields",
]
