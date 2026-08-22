"""Volume-constrained variational discretisation of forward theory F1.0.

This module contains numerical machinery only.  The physical model is frozen
in ``deliverables/01_前向预测理论.md``.  A smooth order parameter is used as
an interface representation because it survives contact, merger and pinch-off
without changing the evolution rule.  Its energy and mobility are scaled so
the sharp-interface limit is the theory's forced mean-curvature flow.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import binary_propagation, distance_transform_edt

from .axisymmetric import AxisymmetricGrid, FloatArray


PHASE_FIELD_SURFACE_CONSTANT = 2.0 * math.sqrt(2.0) / 3.0
WALL = 0
FLOOR = 1
TARGET = 2
BOUNDARY_NAMES = {WALL: "wall", FLOOR: "floor", TARGET: "target"}


class VariationalStepError(RuntimeError):
    """Raised when a dissipative, volume-conserving step cannot be found."""


@dataclass(frozen=True, slots=True)
class ContactLaw:
    """One solid's receding/equilibrium/advancing law in SI/radians."""

    receding_angle_rad: float
    equilibrium_angle_rad: float
    advancing_angle_rad: float
    friction_pa_s: float

    def __post_init__(self) -> None:
        if not (
            0.0
            < self.receding_angle_rad
            <= self.equilibrium_angle_rad
            <= self.advancing_angle_rad
            < math.pi
        ):
            raise ValueError(
                "contact angles must satisfy 0 < receding <= equilibrium "
                "<= advancing < pi"
            )
        if not math.isfinite(self.friction_pa_s) or self.friction_pa_s <= 0.0:
            raise ValueError("contact friction must be finite and positive")


@dataclass(frozen=True, slots=True)
class BoundaryFaces:
    """Fluid cells adjacent to material walls and their physical face data."""

    radial_index: NDArray[np.int64]
    axial_index: NDArray[np.int64]
    area_m2: FloatArray
    normal_from_solid_r: FloatArray
    normal_from_solid_z: FloatArray
    kind: NDArray[np.int8]

    @property
    def size(self) -> int:
        return int(self.area_m2.size)


@dataclass(frozen=True, slots=True)
class VariationalGeometry:
    """Grid-aligned fluid/target geometry used by one convergence member."""

    grid: AxisymmetricGrid
    fluid_mask: NDArray[np.bool_]
    target_mask: NDArray[np.bool_]
    top_reservoir_mask: NDArray[np.bool_]
    boundary_faces: BoundaryFaces
    liquid_height_m: float
    target_bottom_m: float
    target_top_m: float
    target_radius_m: float
    target_liquid_volume_m3: float
    exact_target_solid_volume_m3: float
    rasterized_target_solid_volume_m3: float


@dataclass(frozen=True, slots=True)
class ContactDiagnostics:
    pinned_faces: int
    advancing_faces: int
    receding_faces: int
    inactive_faces: int
    minimum_angle_deg: float
    maximum_angle_deg: float


@dataclass(frozen=True, slots=True)
class EnergyBreakdown:
    total_j: float
    surface_j: float
    bulk_potential_j: float
    wall_j: float


@dataclass(frozen=True, slots=True)
class StepDiagnostics:
    accepted_dt_s: float
    rejected_attempts: int
    maximum_phase_rate_s_inv: float
    maximum_phase_change: float
    lagrange_pressure_pa: float
    volume_before_m3: float
    volume_after_m3: float
    energy_before_j: float
    energy_after_j: float
    contact: ContactDiagnostics


@dataclass(frozen=True, slots=True)
class TopologyDiagnostics:
    touches_target: bool
    finite_channel_candidate: bool
    resolved_channel: bool
    target_gas_area_m2: float
    minimum_throat_radius_m: float
    required_resolved_radius_m: float
    minimum_gap_to_target_m: float
    isolated_gas_volume_m3: float
    top_connected_gas_volume_m3: float
    throat_radius_by_height_m: FloatArray
    top_connected_gas_mask: NDArray[np.bool_]


def smooth_liquid_fraction(order_parameter: NDArray[np.floating]) -> FloatArray:
    """Return the bounded liquid indicator h(q), with h(-1)=0 and h(+1)=1."""

    q = np.clip(np.asarray(order_parameter, dtype=np.float64), -1.0, 1.0)
    return 0.5 + 0.75 * q - 0.25 * q**3


def smooth_liquid_fraction_derivative(
    order_parameter: NDArray[np.floating],
) -> FloatArray:
    q = np.clip(np.asarray(order_parameter, dtype=np.float64), -1.0, 1.0)
    return 0.75 * (1.0 - q * q)


def double_well(order_parameter: NDArray[np.floating]) -> FloatArray:
    q = np.asarray(order_parameter, dtype=np.float64)
    return 0.25 * np.square(1.0 - q * q)


def _annular_face_areas(grid: AxisymmetricGrid) -> FloatArray:
    return math.pi * (grid.r_faces[1:] ** 2 - grid.r_faces[:-1] ** 2)


def build_variational_geometry(
    grid: AxisymmetricGrid,
    *,
    liquid_height_m: float,
    target_bottom_m: float,
    target_top_m: float,
    target_radius_m: float,
    top_seed_depth_m: float,
) -> VariationalGeometry:
    """Rasterise the effective central target and enumerate solid faces."""

    if not 0.0 < liquid_height_m < grid.height_m:
        raise ValueError("liquid height must lie inside the computational domain")
    if not 0.0 <= target_bottom_m < target_top_m < liquid_height_m:
        raise ValueError("effective target must lie below the initial liquid surface")
    if not 0.0 < target_radius_m < grid.radius_m:
        raise ValueError("effective target radius must lie inside the vessel")
    if not 0.0 < top_seed_depth_m < grid.height_m - liquid_height_m:
        raise ValueError("top seed must fit wholly inside the headspace")

    r = grid.r_centres[:, None]
    z = grid.z_centres[None, :]
    target = (
        (r <= target_radius_m)
        & (z >= target_bottom_m)
        & (z <= target_top_m)
    )
    if not np.any(target):
        raise ValueError("grid does not resolve the effective target")
    fluid = ~target
    top = fluid & (z >= grid.height_m - top_seed_depth_m)
    if not np.any(top[:, -1]):
        raise ValueError("top reservoir seed does not reach the upper grid row")

    cell_i: list[int] = []
    cell_j: list[int] = []
    areas: list[float] = []
    normals_r: list[float] = []
    normals_z: list[float] = []
    kinds: list[int] = []

    def add(i: int, j: int, area: float, nr: float, nz: float, kind: int) -> None:
        if not fluid[i, j]:
            raise RuntimeError("boundary face must be attached to a fluid cell")
        cell_i.append(i)
        cell_j.append(j)
        areas.append(float(area))
        normals_r.append(float(nr))
        normals_z.append(float(nz))
        kinds.append(kind)

    # Vessel side wall and floor.  r=0 is an axis, not a material wall.
    side_area = 2.0 * math.pi * grid.radius_m * grid.dz
    for j in range(grid.nz):
        add(grid.nr - 1, j, side_area, -1.0, 0.0, WALL)
    annular = _annular_face_areas(grid)
    for i in range(grid.nr):
        add(i, 0, float(annular[i]), 0.0, 1.0, FLOOR)

    # Every target-fluid face is explicit.  Axisymmetric radial faces use the
    # true swept area; horizontal faces use the annular cell area.
    for i, j in np.argwhere(target):
        i = int(i)
        j = int(j)
        if i + 1 < grid.nr and fluid[i + 1, j]:
            add(
                i + 1,
                j,
                2.0 * math.pi * grid.r_faces[i + 1] * grid.dz,
                1.0,
                0.0,
                TARGET,
            )
        if i > 0 and fluid[i - 1, j]:
            add(
                i - 1,
                j,
                2.0 * math.pi * grid.r_faces[i] * grid.dz,
                -1.0,
                0.0,
                TARGET,
            )
        if j + 1 < grid.nz and fluid[i, j + 1]:
            add(i, j + 1, float(annular[i]), 0.0, 1.0, TARGET)
        if j > 0 and fluid[i, j - 1]:
            add(i, j - 1, float(annular[i]), 0.0, -1.0, TARGET)

    faces = BoundaryFaces(
        radial_index=np.asarray(cell_i, dtype=np.int64),
        axial_index=np.asarray(cell_j, dtype=np.int64),
        area_m2=np.asarray(areas, dtype=np.float64),
        normal_from_solid_r=np.asarray(normals_r, dtype=np.float64),
        normal_from_solid_z=np.asarray(normals_z, dtype=np.float64),
        kind=np.asarray(kinds, dtype=np.int8),
    )
    exact_target_solid_volume = (
        math.pi * target_radius_m**2 * (target_top_m - target_bottom_m)
    )
    target_volume = (
        math.pi * grid.radius_m**2 * liquid_height_m
        - exact_target_solid_volume
    )
    rasterized_target_solid_volume = float(
        np.sum(grid.cell_volumes[target])
    )
    return VariationalGeometry(
        grid=grid,
        fluid_mask=fluid,
        target_mask=target,
        top_reservoir_mask=top,
        boundary_faces=faces,
        liquid_height_m=float(liquid_height_m),
        target_bottom_m=float(target_bottom_m),
        target_top_m=float(target_top_m),
        target_radius_m=float(target_radius_m),
        target_liquid_volume_m3=target_volume,
        exact_target_solid_volume_m3=exact_target_solid_volume,
        rasterized_target_solid_volume_m3=rasterized_target_solid_volume,
    )


def liquid_volume_m3(
    order_parameter: NDArray[np.floating], geometry: VariationalGeometry
) -> float:
    geometry.grid.require_cell_field(order_parameter, "order_parameter")
    liquid = smooth_liquid_fraction(order_parameter)
    return float(
        np.sum(liquid * geometry.grid.cell_volumes * geometry.fluid_mask)
    )


def correct_liquid_volume(
    order_parameter: NDArray[np.floating],
    geometry: VariationalGeometry,
    target_volume_m3: float,
    *,
    free_mask: NDArray[np.bool_] | None = None,
    relative_tolerance: float = 1.0e-10,
) -> FloatArray:
    """Project onto the discrete h(q)-volume using an interface-local shift."""

    grid = geometry.grid
    grid.require_cell_field(order_parameter, "order_parameter")
    if not math.isfinite(target_volume_m3) or target_volume_m3 <= 0.0:
        raise ValueError("target liquid volume must be finite and positive")
    if not math.isfinite(relative_tolerance) or relative_tolerance <= 0.0:
        raise ValueError("relative_tolerance must be finite and positive")
    q0 = np.clip(np.asarray(order_parameter, dtype=np.float64), -1.0, 1.0).copy()
    q0[geometry.target_mask] = 1.0
    q0[geometry.top_reservoir_mask] = -1.0
    allowed = geometry.fluid_mask & ~geometry.top_reservoir_mask
    if free_mask is not None:
        grid.require_cell_field(free_mask, "free_mask")
        allowed &= np.asarray(free_mask, dtype=bool)
    direction = smooth_liquid_fraction_derivative(q0) * allowed
    if not np.any(direction > 0.0):
        error = liquid_volume_m3(q0, geometry) - target_volume_m3
        if abs(error) <= relative_tolerance * target_volume_m3:
            return q0
        raise VariationalStepError("no diffuse interface remains for volume projection")

    def shifted(offset: float) -> FloatArray:
        candidate = np.clip(q0 + offset * direction, -1.0, 1.0)
        candidate[geometry.target_mask] = 1.0
        candidate[geometry.top_reservoir_mask] = -1.0
        return candidate

    current = liquid_volume_m3(q0, geometry)
    tolerance = relative_tolerance * target_volume_m3
    if abs(current - target_volume_m3) <= tolerance:
        return q0

    low, high = -1.0, 1.0
    low_volume = liquid_volume_m3(shifted(low), geometry)
    high_volume = liquid_volume_m3(shifted(high), geometry)
    for _ in range(16):
        if low_volume <= target_volume_m3 <= high_volume:
            break
        low *= 2.0
        high *= 2.0
        low_volume = liquid_volume_m3(shifted(low), geometry)
        high_volume = liquid_volume_m3(shifted(high), geometry)
    else:
        raise VariationalStepError("volume target is unreachable with fixed solid/top cells")

    result = q0
    for _ in range(64):
        middle = 0.5 * (low + high)
        result = shifted(middle)
        volume = liquid_volume_m3(result, geometry)
        if abs(volume - target_volume_m3) <= tolerance:
            return result
        if volume < target_volume_m3:
            low = middle
        else:
            high = middle
    final_error = liquid_volume_m3(result, geometry) - target_volume_m3
    if abs(final_error) > tolerance:
        raise VariationalStepError(
            "volume projection did not meet tolerance: "
            f"absolute error={final_error:.6e} m^3"
        )
    return result


def initialise_flat_order_parameter(
    geometry: VariationalGeometry,
    interface_thickness_m: float,
    *,
    volume_relative_tolerance: float = 1.0e-10,
) -> FloatArray:
    if not math.isfinite(interface_thickness_m) or interface_thickness_m <= 0.0:
        raise ValueError("interface thickness must be finite and positive")
    signed = (
        geometry.grid.z_centres[None, :] - geometry.liquid_height_m
    )
    q = -np.tanh(signed / (math.sqrt(2.0) * interface_thickness_m))
    q = np.broadcast_to(q, geometry.grid.shape).copy()
    q[geometry.target_mask] = 1.0
    q[geometry.top_reservoir_mask] = -1.0
    return correct_liquid_volume(
        q,
        geometry,
        geometry.target_liquid_volume_m3,
        relative_tolerance=volume_relative_tolerance,
    )


def masked_cell_gradient(
    order_parameter: NDArray[np.floating], geometry: VariationalGeometry
) -> tuple[FloatArray, FloatArray]:
    """Central/one-sided gradient using fluid neighbours only."""

    grid = geometry.grid
    grid.require_cell_field(order_parameter, "order_parameter")
    q = np.asarray(order_parameter, dtype=np.float64)
    fluid = geometry.fluid_mask
    gr = np.zeros_like(q)
    gz = np.zeros_like(q)

    plus = np.zeros_like(fluid)
    minus = np.zeros_like(fluid)
    plus[:-1, :] = fluid[1:, :]
    minus[1:, :] = fluid[:-1, :]
    both = fluid & plus & minus
    central_r = np.zeros_like(q)
    central_r[1:-1, :] = (q[2:, :] - q[:-2, :]) / (2.0 * grid.dr)
    gr[both] = central_r[both]
    only_plus = fluid & plus & ~minus
    ii, jj = np.nonzero(only_plus)
    gr[ii, jj] = (q[ii + 1, jj] - q[ii, jj]) / grid.dr
    only_minus = fluid & minus & ~plus
    ii, jj = np.nonzero(only_minus)
    gr[ii, jj] = (q[ii, jj] - q[ii - 1, jj]) / grid.dr

    plus.fill(False)
    minus.fill(False)
    plus[:, :-1] = fluid[:, 1:]
    minus[:, 1:] = fluid[:, :-1]
    both = fluid & plus & minus
    central_z = np.zeros_like(q)
    central_z[:, 1:-1] = (q[:, 2:] - q[:, :-2]) / (2.0 * grid.dz)
    gz[both] = central_z[both]
    only_plus = fluid & plus & ~minus
    ii, jj = np.nonzero(only_plus)
    gz[ii, jj] = (q[ii, jj + 1] - q[ii, jj]) / grid.dz
    only_minus = fluid & minus & ~plus
    ii, jj = np.nonzero(only_minus)
    gz[ii, jj] = (q[ii, jj] - q[ii, jj - 1]) / grid.dz
    return gr, gz


def _face_pair_energy_gradient(
    q: FloatArray,
    fluid: NDArray[np.bool_],
    geometry: VariationalGeometry,
    surface_tension_n_m: float,
    interface_thickness_m: float,
    gradient: FloatArray,
) -> float:
    grid = geometry.grid
    prefactor = (
        surface_tension_n_m
        * interface_thickness_m
        / PHASE_FIELD_SURFACE_CONSTANT
    )
    energy = 0.0

    radial_pairs = fluid[:-1, :] & fluid[1:, :]
    radial_area = 2.0 * math.pi * grid.r_faces[1:-1, None] * grid.dz
    radial_coefficient = prefactor * radial_area / grid.dr
    radial_difference = q[1:, :] - q[:-1, :]
    active = radial_coefficient * radial_pairs
    energy += 0.5 * float(np.sum(active * radial_difference**2))
    gradient[:-1, :] -= active * radial_difference
    gradient[1:, :] += active * radial_difference

    axial_pairs = fluid[:, :-1] & fluid[:, 1:]
    axial_area = _annular_face_areas(grid)[:, None]
    axial_coefficient = prefactor * axial_area / grid.dz
    axial_difference = q[:, 1:] - q[:, :-1]
    active = axial_coefficient * axial_pairs
    energy += 0.5 * float(np.sum(active * axial_difference**2))
    gradient[:, :-1] -= active * axial_difference
    gradient[:, 1:] += active * axial_difference
    return energy


def discrete_energy_and_gradient(
    order_parameter: NDArray[np.floating],
    geometry: VariationalGeometry,
    bulk_potential_pa: NDArray[np.floating],
    *,
    surface_tension_n_m: float,
    interface_thickness_m: float,
    contact_laws: Mapping[int, ContactLaw],
    apply_hysteresis: bool = True,
) -> tuple[
    EnergyBreakdown,
    FloatArray,
    FloatArray,
    NDArray[np.bool_],
    ContactDiagnostics,
]:
    """Return equilibrium energy and the hysteretic descent derivative."""

    grid = geometry.grid
    grid.require_cell_field(order_parameter, "order_parameter")
    grid.require_cell_field(bulk_potential_pa, "bulk_potential_pa")
    if surface_tension_n_m <= 0.0 or interface_thickness_m <= 0.0:
        raise ValueError("surface tension and interface thickness must be positive")
    if set(contact_laws) != {WALL, FLOOR, TARGET}:
        raise ValueError("contact_laws must define wall, floor, and target")

    q = np.clip(np.asarray(order_parameter, dtype=np.float64), -1.0, 1.0)
    f = np.asarray(bulk_potential_pa, dtype=np.float64)
    fluid = geometry.fluid_mask
    volumes = grid.cell_volumes * fluid
    h = smooth_liquid_fraction(q)
    hp = smooth_liquid_fraction_derivative(q)
    gradient = np.zeros_like(q)

    gradient_surface = _face_pair_energy_gradient(
        q,
        fluid,
        geometry,
        surface_tension_n_m,
        interface_thickness_m,
        gradient,
    )
    well_density = (
        surface_tension_n_m
        / (PHASE_FIELD_SURFACE_CONSTANT * interface_thickness_m)
        * double_well(q)
    )
    well_energy = float(np.sum(well_density * volumes))
    gradient += (
        surface_tension_n_m
        / (PHASE_FIELD_SURFACE_CONSTANT * interface_thickness_m)
        * (q**3 - q)
        * volumes
    )
    bulk_energy = float(np.sum(h * f * volumes))
    gradient += hp * f * volumes

    faces = geometry.boundary_faces
    fi = faces.radial_index
    fj = faces.axial_index
    face_q = q[fi, fj]
    face_hp = hp[fi, fj]
    equilibrium_cos = np.asarray(
        [math.cos(contact_laws[int(kind)].equilibrium_angle_rad) for kind in faces.kind],
        dtype=np.float64,
    )
    wall_energy = -surface_tension_n_m * float(
        np.sum(equilibrium_cos * smooth_liquid_fraction(face_q) * faces.area_m2)
    )

    selected_cos = equilibrium_cos.copy()
    pinned_cells = np.zeros(grid.shape, dtype=bool)
    wall_mass = np.zeros(grid.shape, dtype=np.float64)
    inactive = 0
    pinned = 0
    advancing = 0
    receding = 0
    angles: list[float] = []
    grad_r, grad_z = masked_cell_gradient(q, geometry)
    grad_magnitude = np.hypot(grad_r[fi, fj], grad_z[fi, fj])
    interface_faces = (np.abs(face_q) < 0.95) & (
        grad_magnitude > 1.0e-12 / min(grid.dr, grid.dz)
    )
    normal_r = np.zeros(faces.size, dtype=np.float64)
    normal_z = np.zeros(faces.size, dtype=np.float64)
    normal_r[interface_faces] = -grad_r[fi[interface_faces], fj[interface_faces]] / (
        grad_magnitude[interface_faces]
    )
    normal_z[interface_faces] = -grad_z[fi[interface_faces], fj[interface_faces]] / (
        grad_magnitude[interface_faces]
    )
    cos_dynamic = np.clip(
        normal_r * faces.normal_from_solid_r
        + normal_z * faces.normal_from_solid_z,
        -1.0,
        1.0,
    )

    for index in range(faces.size):
        kind = int(faces.kind[index])
        law = contact_laws[kind]
        i = int(fi[index])
        j = int(fj[index])
        if not interface_faces[index]:
            inactive += 1
            continue
        theta = math.acos(float(cos_dynamic[index]))
        angles.append(math.degrees(theta))
        if not apply_hysteresis:
            selected_cos[index] = math.cos(law.equilibrium_angle_rad)
            wall_mass[i, j] += (
                faces.area_m2[index]
                * interface_thickness_m
                * law.friction_pa_s
                / PHASE_FIELD_SURFACE_CONSTANT
            )
        elif theta > law.advancing_angle_rad:
            advancing += 1
            selected_cos[index] = math.cos(law.advancing_angle_rad)
            wall_mass[i, j] += (
                faces.area_m2[index]
                * interface_thickness_m
                * law.friction_pa_s
                / PHASE_FIELD_SURFACE_CONSTANT
            )
        elif theta < law.receding_angle_rad:
            receding += 1
            selected_cos[index] = math.cos(law.receding_angle_rad)
            wall_mass[i, j] += (
                faces.area_m2[index]
                * interface_thickness_m
                * law.friction_pa_s
                / PHASE_FIELD_SURFACE_CONSTANT
            )
        else:
            pinned += 1
            pinned_cells[i, j] = True

    wall_gradient_values = (
        -surface_tension_n_m * selected_cos * face_hp * faces.area_m2
    )
    np.add.at(gradient, (fi, fj), wall_gradient_values)
    contact = ContactDiagnostics(
        pinned_faces=pinned,
        advancing_faces=advancing,
        receding_faces=receding,
        inactive_faces=inactive,
        minimum_angle_deg=float(min(angles)) if angles else 0.0,
        maximum_angle_deg=float(max(angles)) if angles else 0.0,
    )
    return (
        EnergyBreakdown(
            total_j=gradient_surface + well_energy + bulk_energy + wall_energy,
            surface_j=gradient_surface + well_energy,
            bulk_potential_j=bulk_energy,
            wall_j=wall_energy,
        ),
        gradient,
        wall_mass,
        pinned_cells,
        contact,
    )


def advance_variational_phase(
    order_parameter: NDArray[np.floating],
    geometry: VariationalGeometry,
    bulk_potential_pa: NDArray[np.floating],
    *,
    dt_s: float,
    surface_tension_n_m: float,
    interface_thickness_m: float,
    interface_mobility_m_pa_s: float,
    contact_laws: Mapping[int, ContactLaw],
    target_volume_m3: float,
    max_phase_change: float,
    volume_relative_tolerance: float,
    energy_relative_tolerance: float,
    maximum_rejections: int,
) -> tuple[FloatArray, StepDiagnostics]:
    """Take one accepted constrained descent step with energy backtracking."""

    if not math.isfinite(dt_s) or dt_s <= 0.0:
        raise ValueError("dt_s must be finite and positive")
    if not math.isfinite(interface_mobility_m_pa_s) or interface_mobility_m_pa_s <= 0.0:
        raise ValueError("interface mobility must be finite and positive")
    q = np.clip(np.asarray(order_parameter, dtype=np.float64), -1.0, 1.0).copy()
    before_volume = liquid_volume_m3(q, geometry)
    energy, gradient, wall_mass, pinned, contact = discrete_energy_and_gradient(
        q,
        geometry,
        bulk_potential_pa,
        surface_tension_n_m=surface_tension_n_m,
        interface_thickness_m=interface_thickness_m,
        contact_laws=contact_laws,
        apply_hysteresis=True,
    )
    volumes = geometry.grid.cell_volumes
    bulk_mass = (
        volumes
        * interface_thickness_m
        / (interface_mobility_m_pa_s * PHASE_FIELD_SURFACE_CONSTANT)
    )
    mass = bulk_mass + wall_mass
    free = (
        geometry.fluid_mask
        & ~geometry.top_reservoir_mask
        & ~pinned
    )
    hp = smooth_liquid_fraction_derivative(q)
    constraint_gradient = hp * volumes
    weighted = np.zeros_like(q)
    weighted[free] = 1.0 / mass[free]
    denominator = float(np.sum(constraint_gradient**2 * weighted))
    if denominator <= np.finfo(float).tiny:
        raise VariationalStepError("volume multiplier is singular: no mobile interface")
    lagrange = float(
        np.sum(constraint_gradient * gradient * weighted) / denominator
    )
    rate = np.zeros_like(q)
    rate[free] = -(
        gradient[free] - lagrange * constraint_gradient[free]
    ) / mass[free]
    maximum_rate = float(np.max(np.abs(rate[free]))) if np.any(free) else 0.0
    trial_dt = float(dt_s)
    if maximum_rate > 0.0:
        trial_dt = min(trial_dt, max_phase_change / maximum_rate)
    energy_scale = max(
        abs(energy.total_j),
        surface_tension_n_m * geometry.grid.radius_m**2,
        1.0e-18,
    )
    tolerance_j = energy_relative_tolerance * energy_scale

    for rejection in range(maximum_rejections + 1):
        candidate = np.clip(q + trial_dt * rate, -1.0, 1.0)
        candidate[geometry.target_mask] = 1.0
        candidate[geometry.top_reservoir_mask] = -1.0
        candidate = correct_liquid_volume(
            candidate,
            geometry,
            target_volume_m3,
            free_mask=free,
            relative_tolerance=volume_relative_tolerance,
        )
        candidate_energy, *_ = discrete_energy_and_gradient(
            candidate,
            geometry,
            bulk_potential_pa,
            surface_tension_n_m=surface_tension_n_m,
            interface_thickness_m=interface_thickness_m,
            contact_laws=contact_laws,
            apply_hysteresis=False,
        )
        if candidate_energy.total_j <= energy.total_j + tolerance_j:
            after_volume = liquid_volume_m3(candidate, geometry)
            return candidate, StepDiagnostics(
                accepted_dt_s=trial_dt,
                rejected_attempts=rejection,
                maximum_phase_rate_s_inv=maximum_rate,
                maximum_phase_change=float(np.max(np.abs(candidate - q))),
                lagrange_pressure_pa=lagrange,
                volume_before_m3=before_volume,
                volume_after_m3=after_volume,
                energy_before_j=energy.total_j,
                energy_after_j=candidate_energy.total_j,
                contact=contact,
            )
        trial_dt *= 0.5
    raise VariationalStepError(
        "no energy-dissipating phase step found after "
        f"{maximum_rejections} backtracking rejections"
    )


def phase_curvature_m_inv(
    order_parameter: NDArray[np.floating], geometry: VariationalGeometry
) -> FloatArray:
    """Estimate div(n) for n pointing from liquid q=+1 to gas q=-1."""

    grid = geometry.grid
    gr, gz = masked_cell_gradient(order_parameter, geometry)
    magnitude = np.hypot(gr, gz)
    regularisation = 1.0e-10 / min(grid.dr, grid.dz)
    nr = -gr / np.maximum(magnitude, regularisation)
    nz = -gz / np.maximum(magnitude, regularisation)
    dnr, _ = masked_cell_gradient(nr, geometry)
    _, dnz = masked_cell_gradient(nz, geometry)
    curvature = dnr + dnz + nr / grid.r_centres[:, None]
    band = np.abs(np.asarray(order_parameter)) < 0.95
    return np.where(band & geometry.fluid_mask, curvature, 0.0)


def topology_diagnostics(
    order_parameter: NDArray[np.floating],
    geometry: VariationalGeometry,
    *,
    interface_thickness_m: float,
    minimum_resolved_throat_cells: int,
) -> TopologyDiagnostics:
    """Classify top-connected gas without promoting a one-cell path to evidence."""

    grid = geometry.grid
    grid.require_cell_field(order_parameter, "order_parameter")
    q = np.asarray(order_parameter, dtype=np.float64)
    gas = (q < 0.0) & geometry.fluid_mask
    seed = gas & geometry.top_reservoir_mask
    connected = binary_propagation(seed, mask=gas)
    faces = geometry.boundary_faces
    target_faces = faces.kind == TARGET
    ti = faces.radial_index[target_faces]
    tj = faces.axial_index[target_faces]
    target_connected = connected[ti, tj]
    target_gas_area = float(np.sum(faces.area_m2[target_faces][target_connected]))
    touches = bool(np.any(target_connected))

    target_indices = np.argwhere(geometry.target_mask)
    start_j = (
        min(int(np.max(target_indices[:, 1])) + 1, grid.nz - 1)
        if target_indices.size
        else grid.nz - 1
    )
    throat = np.zeros(grid.nz, dtype=np.float64)
    for j in range(start_j, grid.nz):
        row = connected[:, j]
        false_indices = np.flatnonzero(~row)
        count = int(false_indices[0]) if false_indices.size else grid.nr
        throat[j] = grid.r_faces[count]
    active_throat = throat[start_j:]
    minimum_throat = float(np.min(active_throat)) if active_throat.size else 0.0
    finite = touches and minimum_throat > 0.0
    required_radius = max(
        minimum_resolved_throat_cells * grid.dr,
        2.0 * interface_thickness_m,
        2.0 * max(grid.dr, grid.dz),
    )
    resolved = finite and minimum_throat >= required_radius

    gas_distance = distance_transform_edt(
        ~connected, sampling=(grid.dr, grid.dz)
    )
    if ti.size:
        minimum_gap = float(np.min(gas_distance[ti, tj]))
        minimum_gap = max(
            0.0,
            minimum_gap - 0.5 * math.hypot(grid.dr, grid.dz),
        )
    else:  # pragma: no cover - geometry construction guarantees target faces
        minimum_gap = math.inf
    isolated = gas & ~connected
    volumes = grid.cell_volumes
    return TopologyDiagnostics(
        touches_target=touches,
        finite_channel_candidate=finite,
        resolved_channel=resolved,
        target_gas_area_m2=target_gas_area,
        minimum_throat_radius_m=minimum_throat,
        required_resolved_radius_m=required_radius,
        minimum_gap_to_target_m=minimum_gap,
        isolated_gas_volume_m3=float(np.sum(volumes[isolated])),
        top_connected_gas_volume_m3=float(np.sum(volumes[connected])),
        throat_radius_by_height_m=throat,
        top_connected_gas_mask=connected,
    )


__all__ = [
    "BOUNDARY_NAMES",
    "FLOOR",
    "PHASE_FIELD_SURFACE_CONSTANT",
    "TARGET",
    "WALL",
    "BoundaryFaces",
    "ContactDiagnostics",
    "ContactLaw",
    "EnergyBreakdown",
    "StepDiagnostics",
    "TopologyDiagnostics",
    "VariationalGeometry",
    "VariationalStepError",
    "advance_variational_phase",
    "build_variational_geometry",
    "correct_liquid_volume",
    "discrete_energy_and_gradient",
    "double_well",
    "initialise_flat_order_parameter",
    "liquid_volume_m3",
    "masked_cell_gradient",
    "phase_curvature_m_inv",
    "smooth_liquid_fraction",
    "smooth_liquid_fraction_derivative",
    "topology_diagnostics",
]
