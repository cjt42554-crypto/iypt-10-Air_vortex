"""Masked Cartesian finite volumes for the three-dimensional HF solver.

The grid covers a rectangular box around the cylindrical vessel.  Cell-centre
masking removes the exterior of the cylinder and the stationary spherocylinder
that represents the stir bar in its co-rotating frame.  Normal velocities live
on a MAC grid.  All transport functions operate on physical face fluxes and
therefore conserve an active-cell integral up to the explicitly reported open
top flux and boundedness repair.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from numpy.typing import NDArray


FloatArray = NDArray[np.float64]
BoolArray = NDArray[np.bool_]

SIDE_WALL = 1
FLOOR_WALL = 2
BAR_WALL = 3


@dataclass(frozen=True, slots=True)
class CartesianTransportDiagnostics:
    """Complete integral and boundedness budget for one scalar update."""

    old_integral: float
    prescribed_boundary_flux_m3_s: float
    expected_integral: float
    raw_integral: float
    clipped_integral: float
    final_integral: float
    conservation_residual: float
    clip_signed_integral: float
    clip_absolute_integral: float
    repair_signed_integral: float
    repair_absolute_integral: float
    raw_minimum: float
    raw_maximum: float


class ImmersedCartesianGrid:
    """Uniform Cartesian MAC grid with a cylinder and fixed capsule removed."""

    def __init__(
        self,
        nx: int,
        ny: int,
        nz: int,
        radius_m: float,
        height_m: float,
        *,
        bar_half_length_m: float,
        bar_radius_m: float,
        bar_centre_z_m: float,
    ) -> None:
        for name, value in (("nx", nx), ("ny", ny), ("nz", nz)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 6:
                raise ValueError(f"{name} must be an integer >= 6")
        for name, value in (
            ("radius_m", radius_m),
            ("height_m", height_m),
            ("bar_half_length_m", bar_half_length_m),
            ("bar_radius_m", bar_radius_m),
            ("bar_centre_z_m", bar_centre_z_m),
        ):
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        if radius_m <= 0.0 or height_m <= 0.0:
            raise ValueError("vessel dimensions must be positive")
        if bar_half_length_m <= 0.0 or bar_radius_m <= 0.0:
            raise ValueError("bar dimensions must be positive")
        if bar_half_length_m + bar_radius_m >= radius_m:
            raise ValueError("bar must fit inside the cylindrical vessel")
        if not bar_radius_m < bar_centre_z_m < height_m - bar_radius_m:
            raise ValueError("bar must fit vertically inside the domain")

        self.nx = nx
        self.ny = ny
        self.nz = nz
        self.radius_m = float(radius_m)
        self.height_m = float(height_m)
        self.bar_half_length_m = float(bar_half_length_m)
        self.bar_radius_m = float(bar_radius_m)
        self.bar_centre_z_m = float(bar_centre_z_m)
        self.dx = 2.0 * self.radius_m / self.nx
        self.dy = 2.0 * self.radius_m / self.ny
        self.dz = self.height_m / self.nz
        self.cell_volume_m3 = self.dx * self.dy * self.dz
        self.x_centres = (
            -self.radius_m + (np.arange(self.nx, dtype=np.float64) + 0.5) * self.dx
        )
        self.y_centres = (
            -self.radius_m + (np.arange(self.ny, dtype=np.float64) + 0.5) * self.dy
        )
        self.z_centres = (
            (np.arange(self.nz, dtype=np.float64) + 0.5) * self.dz
        )
        self.x_faces = np.linspace(-self.radius_m, self.radius_m, self.nx + 1)
        self.y_faces = np.linspace(-self.radius_m, self.radius_m, self.ny + 1)
        self.z_faces = np.linspace(0.0, self.height_m, self.nz + 1)

        xx, yy, zz = np.meshgrid(
            self.x_centres,
            self.y_centres,
            self.z_centres,
            indexing="ij",
        )
        self.x_cell = xx
        self.y_cell = yy
        self.z_cell = zz
        radius = np.hypot(xx, yy)
        self.vessel_mask = radius < self.radius_m

        # The configured half length is the total capsule half length.  The
        # line segment is shortened by the hemispherical cap radius.
        segment_half = max(self.bar_half_length_m - self.bar_radius_m, 0.0)
        closest_x = np.clip(xx, -segment_half, segment_half)
        bx = xx - closest_x
        by = yy
        bz = zz - self.bar_centre_z_m
        centreline_distance = np.sqrt(bx * bx + by * by + bz * bz)
        self.bar_signed_distance_m = centreline_distance - self.bar_radius_m
        exact_bar_volume = (
            math.pi * self.bar_radius_m**2 * (2.0 * segment_half)
            + 4.0 * math.pi * self.bar_radius_m**3 / 3.0
        )
        # A centre-test voxelisation can over- or under-shoot the tiny bar by
        # tens of percent on an interactive grid.  Select the nearest vessel
        # cells by exact signed distance while pre-registering the closest
        # integer volume.  This preserves a capsule-shaped staircase and makes
        # the residual volume error explicit and at most half one cell.
        desired_bar_cells = max(
            1, int(round(exact_bar_volume / self.cell_volume_m3))
        )
        vessel_flat = np.flatnonzero(self.vessel_mask.ravel())
        order = np.argsort(
            self.bar_signed_distance_m.ravel()[vessel_flat], kind="stable"
        )
        chosen = vessel_flat[order[:desired_bar_cells]]
        self.bar_mask = np.zeros(self.shape, dtype=bool)
        self.bar_mask.ravel()[chosen] = True
        self.active_mask = self.vessel_mask & ~self.bar_mask
        if not np.any(self.bar_mask):
            raise ValueError("Cartesian grid does not resolve the stir bar")
        if not np.any(self.active_mask[:, :, -1]):
            raise ValueError("Cartesian grid has no open atmospheric top cells")

        # Normals point from a solid into the fluid and are used only in the
        # geometric contact-angle construction and boundary diagnostics.
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
        side_distance = self.radius_m - radius
        floor_distance = zz
        bar_distance = self.bar_signed_distance_m
        distances = np.stack((side_distance, floor_distance, bar_distance), axis=0)
        nearest = np.argmin(distances, axis=0)
        self.nearest_solid_distance_m = np.min(distances, axis=0)
        self.nearest_solid_kind = np.choose(
            nearest,
            np.asarray([SIDE_WALL, FLOOR_WALL, BAR_WALL], dtype=np.int8),
        ).astype(np.int8)
        self.wall_normal_x = np.choose(nearest, (side_nx, floor_nx, bar_nx))
        self.wall_normal_y = np.choose(nearest, (side_ny, floor_ny, bar_ny))
        self.wall_normal_z = np.choose(nearest, (side_nz, floor_nz, bar_nz))
        for value in (
            self.nearest_solid_distance_m,
            self.wall_normal_x,
            self.wall_normal_y,
            self.wall_normal_z,
        ):
            value[~self.active_mask] = 0.0

        self.open_x = np.zeros((self.nx + 1, self.ny, self.nz), dtype=bool)
        self.open_y = np.zeros((self.nx, self.ny + 1, self.nz), dtype=bool)
        self.open_z = np.zeros((self.nx, self.ny, self.nz + 1), dtype=bool)
        self.open_x[1:-1] = self.active_mask[:-1] & self.active_mask[1:]
        self.open_y[:, 1:-1] = self.active_mask[:, :-1] & self.active_mask[:, 1:]
        self.open_z[:, :, 1:-1] = (
            self.active_mask[:, :, :-1] & self.active_mask[:, :, 1:]
        )
        # The only open physical boundary is the atmospheric top.
        self.open_z[:, :, -1] = self.active_mask[:, :, -1]

        self.index = np.full(self.shape, -1, dtype=np.int64)
        self.index[self.active_mask] = np.arange(
            int(np.sum(self.active_mask)), dtype=np.int64
        )

    @property
    def shape(self) -> tuple[int, int, int]:
        return (self.nx, self.ny, self.nz)

    @property
    def face_shape_x(self) -> tuple[int, int, int]:
        return (self.nx + 1, self.ny, self.nz)

    @property
    def face_shape_y(self) -> tuple[int, int, int]:
        return (self.nx, self.ny + 1, self.nz)

    @property
    def face_shape_z(self) -> tuple[int, int, int]:
        return (self.nx, self.ny, self.nz + 1)

    @property
    def active_cell_count(self) -> int:
        return int(np.sum(self.active_mask))

    @property
    def minimum_spacing_m(self) -> float:
        return min(self.dx, self.dy, self.dz)

    @property
    def maximum_spacing_m(self) -> float:
        return max(self.dx, self.dy, self.dz)

    @property
    def exact_bar_volume_m3(self) -> float:
        segment_half = max(self.bar_half_length_m - self.bar_radius_m, 0.0)
        return (
            math.pi * self.bar_radius_m**2 * (2.0 * segment_half)
            + 4.0 * math.pi * self.bar_radius_m**3 / 3.0
        )

    @property
    def rasterized_bar_volume_m3(self) -> float:
        return float(np.sum(self.bar_mask) * self.cell_volume_m3)

    @property
    def exact_vessel_volume_m3(self) -> float:
        return math.pi * self.radius_m**2 * self.height_m

    @property
    def rasterized_vessel_volume_m3(self) -> float:
        return float(np.sum(self.vessel_mask) * self.cell_volume_m3)

    def require_cell_field(self, field: NDArray[np.floating], name: str = "field") -> None:
        if np.shape(field) != self.shape:
            raise ValueError(f"{name} must have shape {self.shape}, got {np.shape(field)}")

    def require_face_velocity(
        self,
        u_x: NDArray[np.floating],
        u_y: NDArray[np.floating],
        u_z: NDArray[np.floating],
    ) -> None:
        expected = (self.face_shape_x, self.face_shape_y, self.face_shape_z)
        observed = (np.shape(u_x), np.shape(u_y), np.shape(u_z))
        if observed != expected:
            raise ValueError(f"face velocity shapes must be {expected}, got {observed}")

    def integrate(self, field: NDArray[np.floating]) -> float:
        self.require_cell_field(field)
        return float(
            np.sum(np.asarray(field, dtype=np.float64)[self.active_mask])
            * self.cell_volume_m3
        )

    def divergence(
        self,
        u_x: NDArray[np.floating],
        u_y: NDArray[np.floating],
        u_z: NDArray[np.floating],
    ) -> FloatArray:
        self.require_face_velocity(u_x, u_y, u_z)
        ux = np.where(self.open_x, np.asarray(u_x, dtype=np.float64), 0.0)
        uy = np.where(self.open_y, np.asarray(u_y, dtype=np.float64), 0.0)
        uz = np.where(self.open_z, np.asarray(u_z, dtype=np.float64), 0.0)
        result = (
            (ux[1:] - ux[:-1]) / self.dx
            + (uy[:, 1:] - uy[:, :-1]) / self.dy
            + (uz[:, :, 1:] - uz[:, :, :-1]) / self.dz
        )
        return np.where(self.active_mask, result, 0.0)

    def faces_to_cells(
        self,
        u_x: NDArray[np.floating],
        u_y: NDArray[np.floating],
        u_z: NDArray[np.floating],
    ) -> tuple[FloatArray, FloatArray, FloatArray]:
        self.require_face_velocity(u_x, u_y, u_z)
        ux = 0.5 * (np.asarray(u_x[:-1]) + np.asarray(u_x[1:]))
        uy = 0.5 * (np.asarray(u_y[:, :-1]) + np.asarray(u_y[:, 1:]))
        uz = 0.5 * (np.asarray(u_z[:, :, :-1]) + np.asarray(u_z[:, :, 1:]))
        return tuple(
            np.where(self.active_mask, value, 0.0).astype(np.float64, copy=False)
            for value in (ux, uy, uz)
        )  # type: ignore[return-value]

    def cells_to_faces(
        self,
        u_x_cell: NDArray[np.floating],
        u_y_cell: NDArray[np.floating],
        u_z_cell: NDArray[np.floating],
    ) -> tuple[FloatArray, FloatArray, FloatArray]:
        for field, name in (
            (u_x_cell, "u_x_cell"),
            (u_y_cell, "u_y_cell"),
            (u_z_cell, "u_z_cell"),
        ):
            self.require_cell_field(field, name)
        ux = np.zeros(self.face_shape_x, dtype=np.float64)
        uy = np.zeros(self.face_shape_y, dtype=np.float64)
        uz = np.zeros(self.face_shape_z, dtype=np.float64)
        ux[1:-1] = 0.5 * (u_x_cell[:-1] + u_x_cell[1:])
        uy[:, 1:-1] = 0.5 * (u_y_cell[:, :-1] + u_y_cell[:, 1:])
        uz[:, :, 1:-1] = 0.5 * (u_z_cell[:, :, :-1] + u_z_cell[:, :, 1:])
        # Zero-gradient velocity at the pressure-open top.
        uz[:, :, -1] = u_z_cell[:, :, -1]
        ux *= self.open_x
        uy *= self.open_y
        uz *= self.open_z
        return ux, uy, uz

    def boundary_relaxation_targets(
        self, angular_velocity_rad_s: float, layer_cells: float
    ) -> tuple[FloatArray, FloatArray, FloatArray, FloatArray]:
        """Return wall-layer weight and co-rotating no-slip target velocity."""

        if not math.isfinite(angular_velocity_rad_s):
            raise ValueError("angular velocity must be finite")
        if not math.isfinite(layer_cells) or layer_cells <= 0.0:
            raise ValueError("layer_cells must be finite and positive")
        thickness = layer_cells * self.maximum_spacing_m
        weight = np.clip(
            1.0 - self.nearest_solid_distance_m / thickness,
            0.0,
            1.0,
        ) * self.active_mask
        container = self.nearest_solid_kind != BAR_WALL
        # A stationary laboratory-frame vessel has -Omega x r velocity in the
        # frame that rotates with the stir bar.  The bar itself is stationary.
        target_x = np.where(
            container,
            angular_velocity_rad_s * self.y_cell,
            0.0,
        )
        target_y = np.where(
            container,
            -angular_velocity_rad_s * self.x_cell,
            0.0,
        )
        target_z = np.zeros(self.shape, dtype=np.float64)
        for value in (target_x, target_y, target_z):
            value[~self.active_mask] = 0.0
        return weight, target_x, target_y, target_z


def bounded_mass_correction_masked(
    scalar: NDArray[np.floating],
    grid: ImmersedCartesianGrid,
    target_integral: float,
    *,
    lower: float = 0.0,
    upper: float = 1.0,
) -> FloatArray:
    """Bound a scalar and recover its active-cell physical integral."""

    grid.require_cell_field(scalar)
    q = np.zeros(grid.shape, dtype=np.float64)
    q[grid.active_mask] = np.clip(
        np.asarray(scalar, dtype=np.float64)[grid.active_mask], lower, upper
    )
    active_volume = grid.active_cell_count * grid.cell_volume_m3
    target = float(np.clip(target_integral, lower * active_volume, upper * active_volume))
    tolerance = 64.0 * np.finfo(float).eps * max(active_volume, abs(target), 1.0e-30)
    for _ in range(20):
        residual = target - grid.integrate(q)
        if abs(residual) <= tolerance:
            return q
        if residual > 0.0:
            eligible = grid.active_mask & (q < upper - 8.0 * np.finfo(float).eps)
        else:
            eligible = grid.active_mask & (q > lower + 8.0 * np.finfo(float).eps)
        count = int(np.sum(eligible))
        if count == 0:
            break
        q[eligible] = np.clip(
            q[eligible] + residual / (count * grid.cell_volume_m3),
            lower,
            upper,
        )
    if abs(grid.integrate(q) - target) > 10.0 * tolerance:
        raise FloatingPointError("masked bounded mass correction could not reach target")
    return q


def advect_conserved_cell_quantity(
    quantity: NDArray[np.floating],
    u_x: NDArray[np.floating],
    u_y: NDArray[np.floating],
    u_z: NDArray[np.floating],
    grid: ImmersedCartesianGrid,
    dt_s: float,
    *,
    top_inflow_value: float | NDArray[np.floating] = 0.0,
) -> tuple[FloatArray, float]:
    """First-order upwind transport of a cell-integrated density.

    Returns the raw updated field and the signed outward top-boundary integral
    flux.  Positive boundary flux removes the conserved quantity from the box.
    """

    grid.require_cell_field(quantity, "quantity")
    grid.require_face_velocity(u_x, u_y, u_z)
    if not math.isfinite(dt_s) or dt_s < 0.0:
        raise ValueError("dt_s must be finite and non-negative")
    q = np.asarray(quantity, dtype=np.float64)
    fx = np.zeros(grid.face_shape_x, dtype=np.float64)
    fy = np.zeros(grid.face_shape_y, dtype=np.float64)
    fz = np.zeros(grid.face_shape_z, dtype=np.float64)
    vx = np.asarray(u_x, dtype=np.float64)[1:-1]
    vy = np.asarray(u_y, dtype=np.float64)[:, 1:-1]
    vz = np.asarray(u_z, dtype=np.float64)[:, :, 1:-1]
    fx[1:-1] = np.where(vx >= 0.0, q[:-1], q[1:]) * vx
    fy[:, 1:-1] = np.where(vy >= 0.0, q[:, :-1], q[:, 1:]) * vy
    fz[:, :, 1:-1] = np.where(vz >= 0.0, q[:, :, :-1], q[:, :, 1:]) * vz
    top_velocity = np.asarray(u_z, dtype=np.float64)[:, :, -1]
    inflow = np.broadcast_to(
        np.asarray(top_inflow_value, dtype=np.float64),
        (grid.nx, grid.ny),
    )
    fz[:, :, -1] = np.where(top_velocity >= 0.0, q[:, :, -1], inflow) * top_velocity
    fx *= grid.open_x
    fy *= grid.open_y
    fz *= grid.open_z
    divergence = (
        (fx[1:] - fx[:-1]) / grid.dx
        + (fy[:, 1:] - fy[:, :-1]) / grid.dy
        + (fz[:, :, 1:] - fz[:, :, :-1]) / grid.dz
    )
    updated = np.zeros(grid.shape, dtype=np.float64)
    updated[grid.active_mask] = (
        q[grid.active_mask] - dt_s * divergence[grid.active_mask]
    )
    outward_flux = float(
        np.sum(fz[:, :, -1]) * grid.dx * grid.dy
    )
    return updated, outward_flux


def advect_bounded_scalar(
    scalar: NDArray[np.floating],
    u_x: NDArray[np.floating],
    u_y: NDArray[np.floating],
    u_z: NDArray[np.floating],
    grid: ImmersedCartesianGrid,
    dt_s: float,
    *,
    top_inflow_value: float = 0.0,
) -> tuple[FloatArray, CartesianTransportDiagnostics]:
    """Conservative bounded phase transport with a fully exposed repair budget."""

    old = grid.integrate(scalar)
    raw, boundary_flux = advect_conserved_cell_quantity(
        scalar,
        u_x,
        u_y,
        u_z,
        grid,
        dt_s,
        top_inflow_value=top_inflow_value,
    )
    expected = old - dt_s * boundary_flux
    raw_integral = grid.integrate(raw)
    clipped = np.zeros(grid.shape, dtype=np.float64)
    clipped[grid.active_mask] = np.clip(raw[grid.active_mask], 0.0, 1.0)
    clipped_integral = grid.integrate(clipped)
    repaired = bounded_mass_correction_masked(clipped, grid, expected)
    final = grid.integrate(repaired)
    diagnostics = CartesianTransportDiagnostics(
        old_integral=old,
        prescribed_boundary_flux_m3_s=boundary_flux,
        expected_integral=expected,
        raw_integral=raw_integral,
        clipped_integral=clipped_integral,
        final_integral=final,
        conservation_residual=raw_integral - expected,
        clip_signed_integral=clipped_integral - raw_integral,
        clip_absolute_integral=float(
            np.sum(np.abs(clipped - raw)[grid.active_mask])
            * grid.cell_volume_m3
        ),
        repair_signed_integral=final - clipped_integral,
        repair_absolute_integral=float(
            np.sum(np.abs(repaired - clipped)[grid.active_mask])
            * grid.cell_volume_m3
        ),
        raw_minimum=float(np.min(raw[grid.active_mask])),
        raw_maximum=float(np.max(raw[grid.active_mask])),
    )
    return repaired, diagnostics


def advective_cfl(
    u_x: NDArray[np.floating],
    u_y: NDArray[np.floating],
    u_z: NDArray[np.floating],
    grid: ImmersedCartesianGrid,
    dt_s: float,
) -> float:
    """Return the global donor-cell CFL sum used by the boundedness proof."""

    grid.require_face_velocity(u_x, u_y, u_z)
    ux_c, uy_c, uz_c = grid.faces_to_cells(u_x, u_y, u_z)
    rate = (
        np.abs(ux_c) / grid.dx
        + np.abs(uy_c) / grid.dy
        + np.abs(uz_c) / grid.dz
    )
    return float(dt_s * np.max(rate[grid.active_mask]))


__all__ = [
    "BAR_WALL",
    "FLOOR_WALL",
    "SIDE_WALL",
    "CartesianTransportDiagnostics",
    "ImmersedCartesianGrid",
    "advect_bounded_scalar",
    "advect_conserved_cell_quantity",
    "advective_cfl",
    "bounded_mass_correction_masked",
]
