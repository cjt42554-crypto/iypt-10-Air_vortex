"""Three-dimensional colour-interface, wetting and topology diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable, Mapping

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import binary_dilation, binary_propagation, distance_transform_edt, gaussian_filter

from .cartesian3d import (
    BAR_WALL,
    FLOOR_WALL,
    SIDE_WALL,
    FloatArray,
    ImmersedCartesianGrid,
    bounded_mass_correction_masked,
)

from .phase_transfer3d import FaceTransfers, limit_phase_transfers, transfer_divergence


@dataclass(frozen=True, slots=True)
class CLS3DDiagnostics:
    target_volume_m3: float
    final_volume_m3: float
    clip_absolute_volume_m3: float
    repair_absolute_volume_m3: float
    raw_minimum: float
    raw_maximum: float
    limited_absolute_volume_m3: float = 0.0


@dataclass(frozen=True, slots=True)
class SurfaceTension3DDiagnostics:
    maximum_abs_curvature_m_inv: float
    maximum_acceleration_m_s2: float
    contact_band_cells: int
    interface_band_cells: int


@dataclass(frozen=True, slots=True)
class Topology3DDiagnostics:
    touches_bar: bool
    finite_channel_candidate: bool
    resolved_channel: bool
    bar_gas_contact_area_m2: float
    minimum_throat_radius_m: float
    required_resolved_radius_m: float
    minimum_gap_to_bar_m: float
    isolated_gas_volume_m3: float
    top_connected_gas_volume_m3: float
    throat_radius_by_height_m: FloatArray
    top_connected_gas_mask: NDArray[np.bool_]


@dataclass(frozen=True, slots=True)
class SurfaceGeometry3D:
    radial_centres_m: FloatArray
    mean_profile_m: FloatArray
    minimum_profile_m: FloatArray
    maximum_profile_m: FloatArray
    column_height_m: FloatArray
    funnel_depth_m: float
    visible_depth_m: float
    nominal_central_depression_m: float
    profile_range_m: float
    minimum_gap_to_bar_m: float
    axisymmetry_error: float


def mixture_property_3d(
    phase: NDArray[np.floating], liquid_value: float, gas_value: float
) -> FloatArray:
    c = np.clip(np.asarray(phase, dtype=np.float64), 0.0, 1.0)
    return gas_value + (liquid_value - gas_value) * c


def initialise_flat_phase_3d(
    grid: ImmersedCartesianGrid,
    liquid_height_m: float,
    interface_thickness_m: float,
    *,
    target_liquid_volume_m3: float | None = None,
) -> FloatArray:
    """Create a diffuse horizontal interface and shift it to the target volume."""

    if not 0.0 < liquid_height_m < grid.height_m:
        raise ValueError("liquid height must lie inside the domain")
    if not math.isfinite(interface_thickness_m) or interface_thickness_m <= 0.0:
        raise ValueError("interface thickness must be finite and positive")
    target = (
        math.pi * grid.radius_m**2 * liquid_height_m - grid.exact_bar_volume_m3
        if target_liquid_volume_m3 is None
        else float(target_liquid_volume_m3)
    )
    active_volume = grid.active_cell_count * grid.cell_volume_m3
    if not 0.0 < target < active_volume:
        raise ValueError("target liquid volume does not fit the active grid")

    def phase_at(offset_m: float) -> FloatArray:
        # ``interface_thickness_m`` is the physical 10--90 % colour width,
        # not the much smaller logistic scale.  With
        # c=0.5*(1+tanh(x/(2*epsilon))), that width is 2*ln(9)*epsilon.
        tanh_denominator = interface_thickness_m / math.log(9.0)
        argument = np.clip(
            (liquid_height_m + offset_m - grid.z_cell)
            / tanh_denominator,
            -40.0,
            40.0,
        )
        result = 0.5 * (1.0 + np.tanh(argument))
        return np.where(grid.active_mask, result, 0.0)

    low, high = -grid.height_m, grid.height_m
    if grid.integrate(phase_at(low)) > target or grid.integrate(phase_at(high)) < target:
        raise ValueError("target liquid volume is outside the shifted-interface range")
    result = phase_at(0.0)
    for _ in range(72):
        middle = 0.5 * (low + high)
        result = phase_at(middle)
        if grid.integrate(result) < target:
            low = middle
        else:
            high = middle
    return bounded_mass_correction_masked(result, grid, target)


def _shifted_neighbour(
    field: FloatArray,
    active: NDArray[np.bool_],
    axis: int,
    direction: int,
    *,
    top_dirichlet: float | None = None,
) -> tuple[FloatArray, NDArray[np.bool_]]:
    shifted = np.roll(field, -direction, axis=axis)
    shifted_active = np.roll(active, -direction, axis=axis)
    boundary_slice = [slice(None)] * 3
    boundary_slice[axis] = -1 if direction > 0 else 0
    shifted_active[tuple(boundary_slice)] = False
    shifted[~shifted_active] = field[~shifted_active]
    if axis == 2 and direction > 0 and top_dirichlet is not None:
        top = [slice(None), slice(None), -1]
        top_mask = active[tuple(top)]
        top_values = shifted[tuple(top)]
        original = field[tuple(top)]
        top_values[top_mask] = 2.0 * float(top_dirichlet) - original[top_mask]
        shifted[tuple(top)] = top_values
    return shifted, shifted_active


def masked_gradient_3d(
    field: NDArray[np.floating],
    grid: ImmersedCartesianGrid,
    *,
    top_dirichlet: float | None = None,
) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Central gradient with zero-normal solid ghosts and optional top value."""

    grid.require_cell_field(field)
    q = np.asarray(field, dtype=np.float64)
    gradients: list[FloatArray] = []
    for axis, spacing in enumerate((grid.dx, grid.dy, grid.dz)):
        plus, _ = _shifted_neighbour(
            q,
            grid.active_mask,
            axis,
            1,
            top_dirichlet=top_dirichlet,
        )
        minus, _ = _shifted_neighbour(q, grid.active_mask, axis, -1)
        derivative = (plus - minus) / (2.0 * spacing)
        gradients.append(np.where(grid.active_mask, derivative, 0.0))
    return gradients[0], gradients[1], gradients[2]


def masked_gradient_axis_3d(
    field: NDArray[np.floating],
    grid: ImmersedCartesianGrid,
    axis: int,
    *,
    top_dirichlet: float | None = None,
) -> FloatArray:
    """Return one component of :func:`masked_gradient_3d` without computing three.

    The ghost construction and arithmetic order are intentionally identical to
    the corresponding branch of ``masked_gradient_3d``.  This is an execution
    optimisation only; it does not introduce a second spatial discretisation.
    """

    if isinstance(axis, bool) or not isinstance(axis, int) or axis not in (0, 1, 2):
        raise ValueError("axis must be one of 0, 1, 2")
    grid.require_cell_field(field)
    q = np.asarray(field, dtype=np.float64)
    spacing = (grid.dx, grid.dy, grid.dz)[axis]
    plus, _ = _shifted_neighbour(
        q,
        grid.active_mask,
        axis,
        1,
        top_dirichlet=top_dirichlet,
    )
    minus, _ = _shifted_neighbour(q, grid.active_mask, axis, -1)
    derivative = (plus - minus) / (2.0 * spacing)
    return np.where(grid.active_mask, derivative, 0.0)


def _normal_divergence(
    normal_x: FloatArray,
    normal_y: FloatArray,
    normal_z: FloatArray,
    grid: ImmersedCartesianGrid,
) -> FloatArray:
    dnx = masked_gradient_axis_3d(normal_x, grid, 0)
    dny = masked_gradient_axis_3d(normal_y, grid, 1)
    dnz = masked_gradient_axis_3d(normal_z, grid, 2)
    return np.where(grid.active_mask, dnx + dny + dnz, 0.0)


def surface_tension_acceleration_3d(
    phase: NDArray[np.floating],
    density_kg_m3: NDArray[np.floating],
    grid: ImmersedCartesianGrid,
    surface_tension_n_m: float,
    *,
    contact_angles_deg: Mapping[int, float],
    smoothing_cells: float = 0.75,
) -> tuple[
    FloatArray,
    FloatArray,
    FloatArray,
    FloatArray,
    SurfaceTension3DDiagnostics,
]:
    """Compute a projected colour-gradient CSF force with geometric wetting.

    The wall normal is imposed only on diffuse-interface cells within 1.5 grid
    spacings of the nearest solid.  This is a local geometric equilibrium-angle
    boundary model; it is not the reduced forward contact-line mobility law.
    """

    grid.require_cell_field(phase, "phase")
    grid.require_cell_field(density_kg_m3, "density_kg_m3")
    if not math.isfinite(surface_tension_n_m) or surface_tension_n_m <= 0.0:
        raise ValueError("surface tension must be finite and positive")
    if set(contact_angles_deg) != {SIDE_WALL, FLOOR_WALL, BAR_WALL}:
        raise ValueError("contact angles must define side, floor and bar walls")
    for kind, angle in contact_angles_deg.items():
        if not math.isfinite(angle) or not 0.0 < angle < 180.0:
            raise ValueError(f"invalid contact angle for wall kind {kind}")
    if not math.isfinite(smoothing_cells) or smoothing_cells <= 0.0:
        raise ValueError("smoothing_cells must be finite and positive")

    active_float = grid.active_mask.astype(np.float64)
    sigma_xyz = (
        smoothing_cells * grid.maximum_spacing_m / grid.dx,
        smoothing_cells * grid.maximum_spacing_m / grid.dy,
        smoothing_cells * grid.maximum_spacing_m / grid.dz,
    )
    numerator = gaussian_filter(
        np.asarray(phase, dtype=np.float64) * active_float,
        sigma=sigma_xyz,
        mode="nearest",
    )
    denominator = gaussian_filter(active_float, sigma=sigma_xyz, mode="nearest")
    smooth = np.divide(
        numerator,
        denominator,
        out=np.zeros_like(numerator),
        where=denominator > 1.0e-12,
    )
    gx, gy, gz = masked_gradient_3d(smooth, grid, top_dirichlet=0.0)
    magnitude = np.sqrt(gx * gx + gy * gy + gz * gz)
    floor = 1.0e-10 / grid.minimum_spacing_m
    normal_x = -gx / np.maximum(magnitude, floor)
    normal_y = -gy / np.maximum(magnitude, floor)
    normal_z = -gz / np.maximum(magnitude, floor)
    interface_band = (
        grid.active_mask
        & (phase > 1.0e-4)
        & (phase < 1.0 - 1.0e-4)
        & (magnitude > floor)
    )
    contact_band = interface_band & (
        grid.nearest_solid_distance_m <= 1.5 * grid.maximum_spacing_m
    )
    if np.any(contact_band):
        nsx = grid.wall_normal_x
        nsy = grid.wall_normal_y
        nsz = grid.wall_normal_z
        dot = normal_x * nsx + normal_y * nsy + normal_z * nsz
        tx = normal_x - dot * nsx
        ty = normal_y - dot * nsy
        tz = normal_z - dot * nsz
        tangent_magnitude = np.sqrt(tx * tx + ty * ty + tz * tz)
        valid_tangent = contact_band & (tangent_magnitude > 1.0e-12)
        tx = np.divide(tx, tangent_magnitude, out=np.zeros_like(tx), where=valid_tangent)
        ty = np.divide(ty, tangent_magnitude, out=np.zeros_like(ty), where=valid_tangent)
        tz = np.divide(tz, tangent_magnitude, out=np.zeros_like(tz), where=valid_tangent)
        angle = np.zeros(grid.shape, dtype=np.float64)
        for kind, value in contact_angles_deg.items():
            angle[grid.nearest_solid_kind == kind] = math.radians(value)
        desired_x = np.cos(angle) * nsx + np.sin(angle) * tx
        desired_y = np.cos(angle) * nsy + np.sin(angle) * ty
        desired_z = np.cos(angle) * nsz + np.sin(angle) * tz
        normal_x[valid_tangent] = desired_x[valid_tangent]
        normal_y[valid_tangent] = desired_y[valid_tangent]
        normal_z[valid_tangent] = desired_z[valid_tangent]

    curvature = _normal_divergence(normal_x, normal_y, normal_z, grid)
    curvature = np.where(interface_band, curvature, 0.0)
    density = np.asarray(density_kg_m3, dtype=np.float64)
    coefficient = np.zeros(grid.shape, dtype=np.float64)
    coefficient[grid.active_mask] = (
        surface_tension_n_m / density[grid.active_mask]
    )
    ax = np.where(interface_band, coefficient * curvature * gx, 0.0)
    ay = np.where(interface_band, coefficient * curvature * gy, 0.0)
    az = np.where(interface_band, coefficient * curvature * gz, 0.0)
    acceleration = np.sqrt(ax * ax + ay * ay + az * az)
    diagnostics = SurfaceTension3DDiagnostics(
        maximum_abs_curvature_m_inv=float(
            np.max(np.abs(curvature[interface_band])) if np.any(interface_band) else 0.0
        ),
        maximum_acceleration_m_s2=float(
            np.max(acceleration[grid.active_mask])
        ),
        contact_band_cells=int(np.sum(contact_band)),
        interface_band_cells=int(np.sum(interface_band)),
    )
    return ax, ay, az, curvature, diagnostics


def conservative_cls_3d(
    phase: NDArray[np.floating],
    grid: ImmersedCartesianGrid,
    interface_thickness_m: float,
    *,
    pseudo_cfl: float,
    iterations: int = 1,
    transfer_callback: Callable[[FaceTransfers], None] | None = None,
) -> tuple[FloatArray, CLS3DDiagnostics]:
    """Bounded conservative CLS with optional paired momentum transport.

    The callback receives each limited, time-integrated face transfer before
    the phase update and must not mutate these transfers.
    """

    grid.require_cell_field(phase, "phase")
    if not math.isfinite(interface_thickness_m) or interface_thickness_m <= 0.0:
        raise ValueError("interface_thickness_m must be finite and positive")
    if not math.isfinite(pseudo_cfl) or not 0.0 < pseudo_cfl <= 0.20:
        raise ValueError("pseudo_cfl must lie in (0, 0.20]")
    if isinstance(iterations, bool) or not isinstance(iterations, int) or iterations < 1:
        raise ValueError("iterations must be an integer >= 1")
    c = np.where(grid.active_mask, np.asarray(phase, dtype=np.float64), 0.0).copy()
    target = grid.integrate(c)
    clip_absolute = 0.0
    repair_absolute = 0.0
    raw_minimum = float(np.min(c[grid.active_mask]))
    raw_maximum = float(np.max(c[grid.active_mask]))
    if not np.isfinite(c).all() or raw_minimum < 0.0 or raw_maximum > 1.0:
        raise ValueError("CLS requires a finite phase in [0, 1]")
    limited_absolute = 0.0
    for _ in range(iterations):
        gx, gy, gz = masked_gradient_3d(c, grid, top_dirichlet=0.0)
        magnitude = np.sqrt(gx * gx + gy * gy + gz * gz)
        floor = 1.0e-12 / grid.minimum_spacing_m
        nx = gx / np.maximum(magnitude, floor)
        ny = gy / np.maximum(magnitude, floor)
        nz = gz / np.maximum(magnitude, floor)
        fx = np.zeros(grid.face_shape_x, dtype=np.float64)
        fy = np.zeros(grid.face_shape_y, dtype=np.float64)
        fz = np.zeros(grid.face_shape_z, dtype=np.float64)
        c_face = 0.5 * (c[:-1] + c[1:])
        n_face = 0.5 * (nx[:-1] + nx[1:])
        # The conservative reinitialisation equilibrium is logistic with
        # 10--90 % width 2*ln(9)*epsilon.  Convert the public, measurable
        # interface-width convention to that epsilon here.
        epsilon = interface_thickness_m / (2.0 * math.log(9.0))
        fx[1:-1] = (
            c_face * (1.0 - c_face) * n_face
            - epsilon * (c[1:] - c[:-1]) / grid.dx
        )
        c_face = 0.5 * (c[:, :-1] + c[:, 1:])
        n_face = 0.5 * (ny[:, :-1] + ny[:, 1:])
        fy[:, 1:-1] = (
            c_face * (1.0 - c_face) * n_face
            - epsilon * (c[:, 1:] - c[:, :-1]) / grid.dy
        )
        c_face = 0.5 * (c[:, :, :-1] + c[:, :, 1:])
        n_face = 0.5 * (nz[:, :, :-1] + nz[:, :, 1:])
        fz[:, :, 1:-1] = (
            c_face * (1.0 - c_face) * n_face
            - epsilon * (c[:, :, 1:] - c[:, :, :-1]) / grid.dz
        )
        # Reinitialisation has zero flux through solids and the open top; it
        # sharpens the represented interface without adding boundary material.
        fx *= grid.open_x
        fy *= grid.open_y
        fz[:, :, -1] = 0.0
        fz *= grid.open_z
        divergence = (
            (fx[1:] - fx[:-1]) / grid.dx
            + (fy[:, 1:] - fy[:, :-1]) / grid.dy
            + (fz[:, :, 1:] - fz[:, :, :-1]) / grid.dz
        )
        raw = np.where(
            grid.active_mask,
            c - pseudo_cfl * grid.minimum_spacing_m * divergence,
            0.0,
        )
        pseudo_step = pseudo_cfl * grid.minimum_spacing_m
        transfers, limited_volume = limit_phase_transfers(
            c, (pseudo_step * fx, pseudo_step * fy, pseudo_step * fz), grid
        )
        limited_absolute += limited_volume
        if transfer_callback is not None:
            transfer_callback(transfers)
        raw_minimum = min(raw_minimum, float(np.min(raw[grid.active_mask])))
        raw_maximum = max(raw_maximum, float(np.max(raw[grid.active_mask])))
        c = np.where(grid.active_mask, c - transfer_divergence(transfers, grid), 0.0)
    return c, CLS3DDiagnostics(
        target_volume_m3=target,
        final_volume_m3=grid.integrate(c),
        clip_absolute_volume_m3=clip_absolute,
        repair_absolute_volume_m3=repair_absolute,
        raw_minimum=raw_minimum,
        raw_maximum=raw_maximum,
        limited_absolute_volume_m3=limited_absolute,
    )


def _bar_contact_area(
    connected: NDArray[np.bool_], grid: ImmersedCartesianGrid
) -> float:
    area = 0.0
    pairs = (
        (connected[:-1] & grid.bar_mask[1:], grid.dy * grid.dz),
        (connected[1:] & grid.bar_mask[:-1], grid.dy * grid.dz),
        (connected[:, :-1] & grid.bar_mask[:, 1:], grid.dx * grid.dz),
        (connected[:, 1:] & grid.bar_mask[:, :-1], grid.dx * grid.dz),
        (connected[:, :, :-1] & grid.bar_mask[:, :, 1:], grid.dx * grid.dy),
        (connected[:, :, 1:] & grid.bar_mask[:, :, :-1], grid.dx * grid.dy),
    )
    for mask, face_area in pairs:
        area += float(np.sum(mask) * face_area)
    return area


def topology_diagnostics_3d(
    phase: NDArray[np.floating],
    grid: ImmersedCartesianGrid,
    *,
    interface_thickness_m: float,
    minimum_resolved_throat_cells: int,
    gas_threshold: float = 0.5,
) -> Topology3DDiagnostics:
    """Classify connectivity, equivalent area and a resolved gas corridor.

    Equivalent radii are descriptive only. Resolution requires a connected
    eroded bulk gas path from atmosphere to gas cells above the bar. For clearance
    only, the bar is not liquid; touching solid is not an automatic zero-radius
    failure. A thin film next to liquid still fails. This is not a qualification
    of contact-line, wall-film or apparatus physics.
    """

    grid.require_cell_field(phase, "phase")
    if not math.isfinite(interface_thickness_m) or interface_thickness_m <= 0.0:
        raise ValueError("interface_thickness_m must be finite and positive")
    if not 0.0 < gas_threshold < 1.0:
        raise ValueError("gas_threshold must lie in (0, 1)")
    if (
        isinstance(minimum_resolved_throat_cells, bool)
        or minimum_resolved_throat_cells < 2
    ):
        raise ValueError("minimum_resolved_throat_cells must be >= 2")
    gas = (np.asarray(phase) < gas_threshold) & grid.active_mask
    seed = np.zeros(grid.shape, dtype=bool)
    seed[:, :, -1] = gas[:, :, -1]
    connected = binary_propagation(seed, mask=gas)
    contact_area = _bar_contact_area(connected, grid)
    touches = contact_area > 0.0
    bar_top = grid.bar_centre_z_m + grid.bar_radius_m
    start = max(0, int(np.searchsorted(grid.z_centres, bar_top, side="left")))
    throat = np.zeros(grid.nz, dtype=np.float64)
    for k in range(start, grid.nz):
        area = float(np.sum(connected[:, :, k]) * grid.dx * grid.dy)
        throat[k] = math.sqrt(area / math.pi) if area > 0.0 else 0.0
    active_throat = throat[start:]
    minimum_throat = float(np.min(active_throat)) if active_throat.size else 0.0
    finite = touches and minimum_throat > 0.0
    required = max(
        minimum_resolved_throat_cells * grid.maximum_spacing_m,
        2.0 * interface_thickness_m,
        2.0 * grid.maximum_spacing_m,
    )
    resolved = False
    # Most pre-contact steps cannot be resolved; avoid an unnecessary full-grid
    # Euclidean distance transform until a physical contact candidate exists.
    if finite and int(np.max(np.where(grid.bar_mask)[2])) + 1 < grid.nz:
        # EDT measures between voxel centres. Subtracting half the cell diagonal
        # bounds clearance to liquid/side-wall voxel volumes from below. The open
        # atmosphere and vessel floor are not artificial liquid endpoints.
        clearance_mask = (gas | grid.bar_mask) & grid.vessel_mask
        # Qualification concerns the bulk throat ABOVE the voxel bar, as did the
        # old area-based measure. Continue the terminal slice down for clearance
        # only; a capped tube need not contain a full sphere below its endpoint.
        # The connectivity search itself cannot enter that artificial continuation.
        bulk_start = int(np.max(np.where(grid.bar_mask)[2])) + 1
        clearance_mask[:, :, :bulk_start] = clearance_mask[:, :, bulk_start, None]
        padded = np.pad(clearance_mask, ((1, 1), (1, 1), (0, 0)), constant_values=False)
        clearance = distance_transform_edt(padded, sampling=(grid.dx, grid.dy, grid.dz))[1:-1, 1:-1, :]
        clearance = np.maximum(0.0, clearance - 0.5 * math.sqrt(grid.dx**2 + grid.dy**2 + grid.dz**2))
        corridor = connected & (clearance >= required)
        corridor[:, :, :bulk_start] = False
        corridor_seed = np.zeros(grid.shape, dtype=bool)
        corridor_seed[:, :, -1] = corridor[:, :, -1]
        reachable = binary_propagation(corridor_seed, mask=corridor)
        bar_neighbours = binary_dilation(grid.bar_mask) & grid.active_mask
        resolved = finite and bool(np.any(reachable & bar_neighbours))
    if np.any(connected):
        gap = max(
            0.0,
            float(np.min(grid.bar_signed_distance_m[connected]))
            - 0.5 * math.sqrt(grid.dx**2 + grid.dy**2 + grid.dz**2),
        )
    else:
        gap = math.inf
    isolated = gas & ~connected
    return Topology3DDiagnostics(
        touches_bar=touches,
        finite_channel_candidate=finite,
        resolved_channel=resolved,
        bar_gas_contact_area_m2=contact_area,
        minimum_throat_radius_m=minimum_throat,
        required_resolved_radius_m=required,
        minimum_gap_to_bar_m=gap,
        isolated_gas_volume_m3=float(np.sum(isolated) * grid.cell_volume_m3),
        top_connected_gas_volume_m3=float(
            np.sum(connected) * grid.cell_volume_m3
        ),
        throat_radius_by_height_m=throat,
        top_connected_gas_mask=connected,
    )


def azimuthal_average_3d(
    field: NDArray[np.floating],
    grid: ImmersedCartesianGrid,
    radial_bins: int,
) -> tuple[FloatArray, FloatArray]:
    """Average a Cartesian scalar into GUI-neutral radial/axial bins."""

    grid.require_cell_field(field)
    if isinstance(radial_bins, bool) or radial_bins < 2:
        raise ValueError("radial_bins must be an integer >= 2")
    radius = np.hypot(grid.x_cell[:, :, 0], grid.y_cell[:, :, 0])
    bin_index = np.floor(radius / grid.radius_m * radial_bins).astype(np.int64)
    valid_xy = bin_index < radial_bins
    result = np.zeros((radial_bins, grid.nz), dtype=np.float64)
    values = np.asarray(field, dtype=np.float64)
    for k in range(grid.nz):
        valid = valid_xy & grid.active_mask[:, :, k]
        indices = bin_index[valid]
        counts = np.bincount(indices, minlength=radial_bins)
        sums = np.bincount(indices, weights=values[:, :, k][valid], minlength=radial_bins)
        result[:, k] = np.divide(
            sums,
            counts,
            out=np.zeros(radial_bins, dtype=np.float64),
            where=counts > 0,
        )
    radial_centres = (
        np.arange(radial_bins, dtype=np.float64) + 0.5
    ) * grid.radius_m / radial_bins
    return radial_centres, result


def _column_surface_heights(
    phase: FloatArray, grid: ImmersedCartesianGrid, level: float
) -> FloatArray:
    heights = np.full((grid.nx, grid.ny), np.nan, dtype=np.float64)
    for i in range(grid.nx):
        for j in range(grid.ny):
            active = grid.active_mask[i, j]
            if not np.any(active):
                continue
            column = phase[i, j]
            transitions = np.flatnonzero(
                active[:-1]
                & active[1:]
                & (column[:-1] >= level)
                & (column[1:] < level)
            )
            if transitions.size:
                k = int(transitions[-1])
                denominator = column[k + 1] - column[k]
                fraction = (
                    0.5
                    if abs(denominator) <= np.finfo(float).eps
                    else (level - column[k]) / denominator
                )
                heights[i, j] = grid.z_centres[k] + fraction * grid.dz
            elif np.all(column[active] >= level):
                heights[i, j] = grid.height_m
            elif np.all(column[active] < level):
                heights[i, j] = 0.0
            else:
                candidates = np.flatnonzero(active)
                k = int(candidates[np.argmin(np.abs(column[candidates] - level))])
                heights[i, j] = grid.z_centres[k]
    return heights


def surface_geometry_3d(
    phase: NDArray[np.floating],
    grid: ImmersedCartesianGrid,
    *,
    liquid_height_m: float,
    bar_top_m: float,
    radial_bins: int,
    level: float = 0.5,
) -> SurfaceGeometry3D:
    """Extract azimuthal mean/envelope profiles and a non-axisymmetry residual."""

    grid.require_cell_field(phase, "phase")
    if not 0.0 < level < 1.0:
        raise ValueError("level must lie in (0, 1)")
    heights = _column_surface_heights(np.asarray(phase, dtype=np.float64), grid, level)
    radius = np.hypot(grid.x_cell[:, :, 0], grid.y_cell[:, :, 0])
    bins = np.floor(radius / grid.radius_m * radial_bins).astype(np.int64)
    radial = (np.arange(radial_bins) + 0.5) * grid.radius_m / radial_bins
    mean = np.full(radial_bins, np.nan, dtype=np.float64)
    minimum = np.full(radial_bins, np.nan, dtype=np.float64)
    maximum = np.full(radial_bins, np.nan, dtype=np.float64)
    residual_values: list[FloatArray] = []
    mean_values: list[FloatArray] = []
    for index in range(radial_bins):
        selected = (bins == index) & np.isfinite(heights)
        if not np.any(selected):
            continue
        values = heights[selected]
        mean[index] = float(np.mean(values))
        minimum[index] = float(np.min(values))
        maximum[index] = float(np.max(values))
        residual_values.append(values - mean[index])
        mean_values.append(np.full(values.shape, mean[index] - liquid_height_m))
    valid = np.isfinite(mean)
    if not np.any(valid):
        raise FloatingPointError("no surface columns could be reconstructed")
    # Fill rare empty outer bins by nearest valid interpolation for GUI arrays.
    indices = np.arange(radial_bins)
    for target in (mean, minimum, maximum):
        target[~valid] = np.interp(indices[~valid], indices[valid], target[valid])
    outer = radial >= 0.8 * grid.radius_m
    centre = radial <= max(grid.bar_half_length_m, grid.radius_m / radial_bins)
    outer_mean = float(np.mean(mean[outer]))
    centre_min = float(np.min(minimum[centre]))
    visible_depth = max(0.0, outer_mean - centre_min)
    nominal_depression = max(0.0, liquid_height_m - centre_min)
    residual = np.concatenate(residual_values) if residual_values else np.zeros(1)
    mean_departure = np.concatenate(mean_values) if mean_values else np.zeros(1)
    denominator = max(
        float(np.linalg.norm(mean_departure)),
        math.sqrt(mean_departure.size) * grid.minimum_spacing_m,
    )
    axisymmetry_error = float(np.linalg.norm(residual) / denominator)
    return SurfaceGeometry3D(
        radial_centres_m=radial,
        mean_profile_m=mean,
        minimum_profile_m=minimum,
        maximum_profile_m=maximum,
        column_height_m=heights,
        # The shared observable is a shape difference and therefore does not
        # mistake a uniform raster-volume offset for a vortex depression.
        funnel_depth_m=visible_depth,
        visible_depth_m=visible_depth,
        nominal_central_depression_m=nominal_depression,
        profile_range_m=float(np.nanmax(heights) - np.nanmin(heights)),
        minimum_gap_to_bar_m=centre_min - bar_top_m,
        axisymmetry_error=axisymmetry_error,
    )


__all__ = [
    "CLS3DDiagnostics",
    "SurfaceGeometry3D",
    "SurfaceTension3DDiagnostics",
    "Topology3DDiagnostics",
    "azimuthal_average_3d",
    "conservative_cls_3d",
    "initialise_flat_phase_3d",
    "masked_gradient_axis_3d",
    "masked_gradient_3d",
    "mixture_property_3d",
    "surface_geometry_3d",
    "surface_tension_acceleration_3d",
    "topology_diagnostics_3d",
]
