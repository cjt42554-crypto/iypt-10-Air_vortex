"""Accepted-state adapter between the CP interface and F86--F88 diagnostics.

Topology is used for connectivity; radial q=0 interpolation supplies local
areas.  Area differences are taken at the *same physical z* before the moving
lower endpoint is applied.  A supplied pressure-error bound always retains its
source, finite time domain and geometric domain.  No default evidence is made
up for an open atmosphere or an unresolved channel.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np
from numpy.typing import NDArray

from airvortex.core.config import SimulationConfig
from airvortex.numerics.gas_pressure import (
    AreaRateFrame, GasPathRegime, GasPressureDiagnostic, GasPressureStatus,
    OpenReservoirInput, PressureScaleInput, RestrictedChannelInput,
    diagnose_gas_pressure,
)
from airvortex.numerics.variational_interface import (
    TopologyDiagnostics, VariationalGeometry,
)


@dataclass(frozen=True, slots=True)
class GasSectionState:
    time_s: float
    z_m: NDArray[np.float64]
    area_m2: NDArray[np.float64]
    single_central_section: NDArray[np.bool_]


@dataclass(frozen=True, slots=True)
class GasAssessment:
    valid: bool
    reason: str
    diagnostic: GasPressureDiagnostic
    sections: GasSectionState
    details: dict[str, Any]

    def summary(self) -> dict[str, Any]:
        d = self.diagnostic
        return {
            "time_s": self.sections.time_s,
            "regime": d.regime.value,
            "status": d.status.value,
            "valid": self.valid,
            "reason": self.reason,
            "diagnostic_reasons": list(d.reasons),
            "pressure_max_abs_pa": d.max_abs_pressure_pa,
            "pressure_upper_bound_pa": d.pressure_upper_bound_pa,
            "epsilon_g": d.epsilon_g,
            "pressure_reference_pa": d.pressure_reference_pa,
            **self.details,
        }


def assess_open_surface_gas(
    config: SimulationConfig, surface_m: NDArray[np.floating], dr_m: float,
    time_s: float,
) -> tuple[bool, str, dict[str, Any]]:
    """Bounded open-atmosphere evidence for a single-valued SH free surface.

    A flat-reference SH surface cannot represent a restricted gas channel.
    This helper only establishes the prescribed reservoir approximation.
    """
    gas = config.gas
    depth = max(0.0, config.container.liquid_depth_m - float(np.min(surface_m)))
    slope = float(np.max(np.abs(np.diff(surface_m) / dr_m)))
    details = {"time_s": time_s, "evidence_kind": gas.evidence_kind,
               "evidence_source": gas.evidence_source, "funnel_depth_m": depth,
               "maximum_surface_slope": slope}
    if gas.regime not in {"auto", "open_reservoir"}:
        return False, "SH_RESTRICTED_GAS_GEOMETRY_UNIMPLEMENTED", details
    if gas.evidence_kind == "unassessed" or not gas.evidence_source.strip():
        return False, "GAS_PRESSURE_EVIDENCE_MISSING", details
    if gas.evidence_valid_until_s is None or time_s > gas.evidence_valid_until_s + 1e-14:
        return False, "GAS_PRESSURE_EVIDENCE_TIME_DOMAIN_EXCEEDED", details
    if (gas.reservoir_max_funnel_depth_m is None
            or gas.reservoir_max_surface_slope is None):
        return False, "OPEN_RESERVOIR_GEOMETRIC_EVIDENCE_DOMAIN_MISSING", details
    if (depth > gas.reservoir_max_funnel_depth_m
            or slope > gas.reservoir_max_surface_slope):
        return False, "OPEN_RESERVOIR_GEOMETRIC_EVIDENCE_DOMAIN_EXCEEDED", details
    diagnostic = diagnose_gas_pressure(OpenReservoirInput(
        pressure_error_bound_pa=gas.reservoir_pressure_error_bound_pa,
        pressure_reference_pa=gas.reservoir_reference_pressure_pa,
        epsilon_g_max=config.forward.gas_pressure_error_max,
        evidence_source=gas.evidence_source,
        pressure_error_budget_pa=gas.pressure_error_budget_pa,
    ))
    details.update({"status": diagnostic.status.value,
                    "pressure_upper_bound_pa": diagnostic.pressure_upper_bound_pa,
                    "epsilon_g": diagnostic.epsilon_g})
    valid = (diagnostic.status is GasPressureStatus.EVALUATED
             and diagnostic.epsilon_limit_satisfied is True
             and diagnostic.pressure_budget_satisfied is True)
    return valid, "OPEN_RESERVOIR_VERIFIED" if valid else "GAS_PRESSURE_UNVERIFIED", details


def extract_gas_sections(
    phase: NDArray[np.floating],
    geometry: VariationalGeometry,
    topology: TopologyDiagnostics,
    time_s: float,
) -> GasSectionState:
    """Subcell circular sections; a disconnected/annular row is not a tube.

    The axisymmetric zero set makes the represented cross section circular,
    but does not prove that a physical three-dimensional gas path is circular.
    That is a separate experimental applicability condition.
    """
    grid = geometry.grid
    area = np.zeros(grid.nz)
    single = np.ones(grid.nz, dtype=bool)
    for j in range(grid.nz):
        connected = topology.top_connected_gas_mask[:, j]
        if not connected[0]:
            single[j] = not bool(np.any(connected))
            continue
        breaks = np.flatnonzero(~connected)
        k = int(breaks[0]) if breaks.size else grid.nr
        if k < grid.nr and np.any(connected[k:]):
            single[j] = False
        if k == grid.nr:
            radius = grid.radius_m
        elif not geometry.fluid_mask[k, j]:
            single[j] = False
            radius = grid.r_faces[k]
        else:
            left, right = float(phase[k - 1, j]), float(phase[k, j])
            if not left < 0.0 <= right:
                single[j] = False
                radius = grid.r_faces[k]
            else:
                fraction = -left / (right - left)
                radius = grid.r_centres[k - 1] + fraction * grid.dr
        area[j] = math.pi * radius * radius
    return GasSectionState(
        float(time_s), grid.z_centres.copy(), area, single,
    )


def assess_forward_gas(
    config: SimulationConfig,
    phase: NDArray[np.floating],
    geometry: VariationalGeometry,
    topology: TopologyDiagnostics,
    *,
    time_s: float,
    previous_sections: GasSectionState | None,
    surface_profile_m: NDArray[np.floating],
    centrifugal_potential_range_m2_s2: float,
    interface_thickness_m: float,
) -> GasAssessment:
    """Evaluate one accepted CP state with explicit evidence and error budgets."""
    gas = config.gas
    grid = geometry.grid
    sections = extract_gas_sections(phase, geometry, topology, time_s)
    details: dict[str, Any] = {
        "evidence_kind": gas.evidence_kind,
        "evidence_source": gas.evidence_source,
        "evidence_valid_until_s": gas.evidence_valid_until_s,
        "pressure_error_budget_pa": gas.pressure_error_budget_pa,
        "derivative_pressure_error_bound_pa": gas.channel_derivative_pressure_error_bound_pa,
        "area_extraction": "axisymmetric radial q=0 linear crossing at fixed z",
        "dr_m": grid.dr,
        "dz_m": grid.dz,
        "interface_thickness_m": interface_thickness_m,
        "area_rate_source": None,
        "area_rate_dt_s": None,
        "conditional_model_only": True,
    }

    def unavailable(reason: str, *, regime: GasPathRegime = GasPathRegime.OPEN_RESERVOIR,
                    status: GasPressureStatus = GasPressureStatus.UNVERIFIED) -> GasAssessment:
        diagnostic = GasPressureDiagnostic(regime=regime, status=status, reasons=(reason,))
        return GasAssessment(False, reason, diagnostic, sections, details)

    if topology.isolated_gas_volume_m3 > 0.0:
        return unavailable("CLOSED_GAS_COMPONENT", regime=GasPathRegime.CLOSED_GAS,
                           status=GasPressureStatus.ASSUMPTION_FAILED)
    if gas.evidence_kind == "unassessed" or not gas.evidence_source.strip():
        return unavailable("GAS_PRESSURE_EVIDENCE_MISSING")
    if gas.evidence_valid_until_s is None:
        return unavailable("GAS_PRESSURE_EVIDENCE_TIME_DOMAIN_MISSING")
    if time_s > gas.evidence_valid_until_s + 8.0 * np.finfo(float).eps * max(1.0, time_s):
        return unavailable("GAS_PRESSURE_EVIDENCE_TIME_DOMAIN_EXCEEDED")

    surface = np.asarray(surface_profile_m, dtype=float)
    depth = max(0.0, config.container.liquid_depth_m - float(np.min(surface)))
    slope = float(np.max(np.abs(np.diff(surface) / grid.dr)))
    details.update({"funnel_depth_m": depth, "maximum_surface_slope": slope})
    reservoir_domain_known = (
        gas.reservoir_max_funnel_depth_m is not None
        and gas.reservoir_max_surface_slope is not None
    )
    reservoir_inside = bool(
        reservoir_domain_known and not topology.touches_target
        and depth <= gas.reservoir_max_funnel_depth_m
        and slope <= gas.reservoir_max_surface_slope
    )
    selected_reservoir = gas.regime == "open_reservoir" or (
        gas.regime == "auto" and reservoir_inside
    )
    if selected_reservoir:
        if not reservoir_domain_known:
            return unavailable("OPEN_RESERVOIR_GEOMETRIC_EVIDENCE_DOMAIN_MISSING")
        if not reservoir_inside:
            return unavailable("OPEN_RESERVOIR_GEOMETRIC_EVIDENCE_DOMAIN_EXCEEDED")
        d = diagnose_gas_pressure(OpenReservoirInput(
            pressure_error_bound_pa=gas.reservoir_pressure_error_bound_pa,
            pressure_reference_pa=gas.reservoir_reference_pressure_pa,
            epsilon_g_max=config.forward.gas_pressure_error_max,
            evidence_source=gas.evidence_source,
            pressure_error_budget_pa=gas.pressure_error_budget_pa,
        ))
    else:
        regime = GasPathRegime.RESTRICTED_CHANNEL
        if gas.channel_lower_z_m is None or gas.channel_upper_z_m is None:
            return unavailable("RESTRICTED_CHANNEL_ENDPOINTS_MISSING", regime=regime)
        lower = gas.channel_lower_z_m + gas.channel_lower_speed_m_s * time_s
        upper = gas.channel_upper_z_m
        if not sections.z_m[0] <= lower < upper <= sections.z_m[-1]:
            return unavailable("RESTRICTED_CHANNEL_ENDPOINTS_OUTSIDE_SAMPLED_DOMAIN", regime=regime)
        z = np.linspace(lower, upper, gas.channel_section_count)
        area = np.interp(z, sections.z_m, sections.area_m2)
        # Every interpolation cell, including the endpoint neighbours, must
        # represent a central single gas section connected to the top seed.
        lo = max(0, int(np.searchsorted(sections.z_m, lower)) - 1)
        hi = min(grid.nz, int(np.searchsorted(sections.z_m, upper)) + 1)
        if not np.all(sections.single_central_section[lo:hi]):
            return unavailable("GAS_PATH_MULTIBRANCH_OR_ANNULAR", regime=regime,
                               status=GasPressureStatus.NOT_APPLICABLE)
        if np.any(area <= 0.0):
            return unavailable("GAS_PATH_ZERO_RADIUS", regime=regime,
                               status=GasPressureStatus.NOT_APPLICABLE)
        radius = np.sqrt(area / math.pi)
        required = max(config.forward.minimum_resolved_throat_cells * grid.dr,
                       2.0 * interface_thickness_m, 2.0 * max(grid.dr, grid.dz))
        details["minimum_required_resolved_radius_m"] = required
        if float(np.min(radius)) < required:
            return unavailable("GAS_PATH_UNDERRESOLVED", regime=regime)
        aspect = float(np.max(radius) / (upper - lower))
        wall_slope = float(np.max(np.abs(np.diff(radius) / np.diff(z))))
        details.update({"channel_radius_length_ratio": aspect, "channel_wall_slope": wall_slope})
        if aspect > gas.channel_max_radius_length_ratio or wall_slope > gas.channel_max_wall_slope:
            return unavailable("GAS_PATH_NOT_SLENDER", regime=regime,
                               status=GasPressureStatus.NOT_APPLICABLE)
        if previous_sections is None:
            rate = np.asarray(gas.channel_initial_area_rate_m2_s, dtype=float)
            if rate.size == 0:
                return unavailable("INITIAL_LOCAL_AREA_DERIVATIVE_MISSING", regime=regime)
            derivative_source = "explicit initial fixed-space section rate; " + gas.evidence_source
        else:
            dt = time_s - previous_sections.time_s
            if dt <= 0.0:
                return unavailable("LOCAL_AREA_DERIVATIVE_TIME_INVALID", regime=regime)
            if not (previous_sections.z_m[0] <= lower < upper <= previous_sections.z_m[-1]):
                return unavailable("PREVIOUS_FIXED_SPACE_SECTIONS_UNAVAILABLE", regime=regime)
            if not np.all(previous_sections.single_central_section[lo:hi]):
                return unavailable("PREVIOUS_GAS_PATH_NOT_SINGLE", regime=regime)
            previous_area = np.interp(z, previous_sections.z_m, previous_sections.area_m2)
            if np.any(previous_area <= 0.0):
                return unavailable("AREA_DERIVATIVE_CROSSES_UNRESOLVED_TOPOLOGY", regime=regime)
            rate = (area - previous_area) / dt
            derivative_source = "backward difference of accepted states at identical physical z"
            details["area_rate_dt_s"] = dt
        details["area_rate_source"] = derivative_source
        d = diagnose_gas_pressure(RestrictedChannelInput(
            z_m=z, area_m2=area, area_rate_fixed_m2_s=rate,
            gas_viscosity_pa_s=config.fluid.gas_dynamic_viscosity_pa_s,
            lower_boundary_speed_m_s=gas.channel_lower_speed_m_s,
            lower_relative_flux_m3_s=gas.channel_lower_relative_flux_m3_s,
            area_rate_frame=AreaRateFrame.FIXED_SPACE,
            area_rate_source=derivative_source,
            lower_boundary_source=gas.evidence_source,
            upper_pressure_source="top-connected atmosphere; " + gas.evidence_source,
            geometry_evidence_source="axisymmetric zero-set and connected single-section audit",
            approximation_evidence_source=gas.evidence_source,
            target_error_budget_source=gas.evidence_source,
            single_channel=True, near_circular=True, slender=True,
            upper_connected_to_atmosphere=True,
            quasi_steady_viscous=gas.channel_viscous_quasisteady,
            gas_inertia_negligible=gas.channel_viscous_quasisteady,
            compressibility_negligible=gas.channel_incompressible,
            wall_resistance_applicable=gas.channel_wall_resistance_justified,
            pressure_scale=PressureScaleInput(
                surface_tension_n_m=config.fluid.surface_tension_n_m,
                density_difference_kg_m3=config.fluid.liquid_density_kg_m3-config.fluid.gas_density_kg_m3,
                gravity_m_s2=config.fluid.gravity_m_s2,
                centrifugal_potential_range_m2_s2=centrifugal_potential_range_m2_s2,
                liquid_density_kg_m3=config.fluid.liquid_density_kg_m3,
                epsilon_g_max=config.forward.gas_pressure_error_max,
            ),
            pressure_error_budget_pa=gas.pressure_error_budget_pa,
            derivative_pressure_error_bound_pa=gas.channel_derivative_pressure_error_bound_pa,
        ))
    valid = (
        d.status is GasPressureStatus.EVALUATED
        and d.epsilon_limit_satisfied is True
        and d.pressure_budget_satisfied is True
    )
    details["epsilon_limit_satisfied"] = d.epsilon_limit_satisfied
    details["pressure_budget_satisfied"] = d.pressure_budget_satisfied
    reason = ("GAS_PRESSURE_CONDITIONAL_SCREEN_PASSED" if valid else
              "GAS_PRESSURE_SCREEN_OR_BUDGET_FAILED" if d.epsilon_limit_satisfied is False or d.pressure_budget_satisfied is False else
              "GAS_PRESSURE_SCREEN_UNVERIFIED")
    return GasAssessment(valid, reason, d, sections, details)


__all__ = ["GasAssessment", "GasSectionState", "assess_forward_gas", "extract_gas_sections"]
