#!/usr/bin/env python3
"""Independent algebraic benchmarks for the conditional SH--TF match.

The checks are deliberately smaller than either production solver.  They test
the conservation contract at an artificial axisymmetric matching ring.  A
PASS here means that the proposed port signs and equation count are internally
consistent; it is not evidence that an SH--TF overlap exists in the apparatus.
"""

from __future__ import annotations

import json
import math
import platform
from fractions import Fraction


def matrix_rank(rows: list[list[Fraction]]) -> int:
    """Exact Gaussian rank over the rationals."""
    matrix = [row[:] for row in rows]
    if not matrix:
        return 0
    n_rows = len(matrix)
    n_cols = len(matrix[0])
    pivot_row = 0
    for column in range(n_cols):
        pivot = next(
            (row for row in range(pivot_row, n_rows) if matrix[row][column]),
            None,
        )
        if pivot is None:
            continue
        matrix[pivot_row], matrix[pivot] = matrix[pivot], matrix[pivot_row]
        pivot_value = matrix[pivot_row][column]
        matrix[pivot_row] = [value / pivot_value for value in matrix[pivot_row]]
        for row in range(n_rows):
            if row == pivot_row:
                continue
            factor = matrix[row][column]
            if factor:
                matrix[row] = [
                    value - factor * pivot_value
                    for value, pivot_value in zip(matrix[row], matrix[pivot_row])
                ]
        pivot_row += 1
        if pivot_row == n_rows:
            break
    return pivot_row


def check_close(
    name: str,
    observed: float,
    expected: float,
    tolerance: float,
    *,
    detail: dict[str, object] | None = None,
) -> dict[str, object]:
    residual = observed - expected
    result: dict[str, object] = {
        "name": name,
        "status": "PASS" if abs(residual) <= tolerance else "FAIL",
        "observed": observed,
        "expected": expected,
        "absolute_residual": abs(residual),
        "tolerance": tolerance,
    }
    if detail:
        result["detail"] = detail
    return result


def main() -> int:
    checks: list[dict[str, object]] = []
    counterexamples: list[dict[str, object]] = []

    # M1: Leibniz transport at a moving ring.  Dividing all volumes by pi
    # keeps the identity exact over the rationals.
    a = Fraction(3, 5)
    a_dot = Fraction(1, 20)
    time = Fraction(2, 5)
    h_0 = Fraction(1, 5)
    alpha = Fraction(-3, 100)
    beta = Fraction(1, 10)
    h_match = h_0 + alpha * time + beta * a * a
    q_lab = -alpha * a / 2
    volume_rate_over_pi = a * a * alpha + 2 * a * a_dot * h_match
    relative_flux_over_pi = 2 * a * (q_lab - a_dot * h_match)
    exact_mass_residual = volume_rate_over_pi + relative_flux_over_pi
    checks.append(
        {
            "name": "moving_ring_relative_flux_mass_identity",
            "status": "PASS" if exact_mass_residual == 0 else "FAIL",
            "observed_exact_residual_over_pi": str(exact_mass_residual),
            "identity": "dV_f/dt = -2*pi*a*(Q_f-a_dot*h_m)",
            "parameters": {
                "a": float(a),
                "a_dot": float(a_dot),
                "h_match": float(h_match),
                "Q_f": float(q_lab),
            },
        }
    )
    omitted_sweep_over_pi = 2 * a * a_dot * h_match
    counterexamples.append(
        {
            "name": "lab_flux_at_moving_ring_is_not_conservative",
            "status": "EXPECTED_FAIL_REPRODUCED",
            "mass_residual_m3_per_s_for_unit_consistent_inputs": float(
                math.pi * omitted_sweep_over_pi
            ),
            "missing_term": "2*pi*a*a_dot*h_m",
        }
    )

    # M2: the same accepted interface flux must enter the two subdomain
    # ledgers with opposite signs on every discrete step.
    film_volume = Fraction(3, 10)
    bulk_volume = Fraction(7, 10)
    initial_total_volume = film_volume + bulk_volume
    discrete_steps = [
        (Fraction(1, 10), Fraction(1, 50)),
        (Fraction(1, 20), Fraction(-1, 100)),
        (Fraction(3, 100), Fraction(3, 200)),
    ]
    discrete_residuals: list[Fraction] = []
    for step_size, shared_flux in discrete_steps:
        film_volume -= step_size * shared_flux
        bulk_volume += step_size * shared_flux
        discrete_residuals.append(
            film_volume + bulk_volume - initial_total_volume
        )
    checks.append(
        {
            "name": "discrete_shared_flux_global_volume_ledger",
            "status": "PASS"
            if all(residual == 0 for residual in discrete_residuals)
            else "FAIL",
            "step_residuals_exact": [str(value) for value in discrete_residuals],
            "final_film_volume_exact": str(film_volume),
            "final_bulk_volume_exact": str(bulk_volume),
        }
    )

    # K1: the material free-surface condition and the moving seam derivative.
    u_top = Fraction(3, 25)
    slope = 2 * beta * a
    h_t = alpha
    w_top = h_t + u_top * slope
    seam_rate = h_t + a_dot * slope
    reconstructed_seam_rate = w_top + (a_dot - u_top) * slope
    checks.append(
        {
            "name": "free_surface_seam_kinematics",
            "status": "PASS" if seam_rate == reconstructed_seam_rate else "FAIL",
            "observed_exact": str(reconstructed_seam_rate),
            "expected_exact": str(seam_rate),
            "identity": "d(h_m)/dt = w_top + (a_dot-u_top)*h_r",
        }
    )
    counterexamples.append(
        {
            "name": "vertical_velocity_alone_is_not_seam_speed",
            "status": "EXPECTED_FAIL_REPRODUCED",
            "kinematic_residual_m_per_s_for_unit_consistent_inputs": float(
                seam_rate - w_top
            ),
            "missing_term": "(a_dot-u_top)*h_r",
        }
    )

    # P1: pressure work and the capillary line work cancel between disjoint
    # energy partitions when the traces use opposite outward orientations.
    a_si = 0.006
    sigma = 0.072
    slope_si = 0.05
    h_dot_si = -2.0e-5
    q_si = 1.0e-8
    chi_si = 50.0
    film_port_power = 2 * math.pi * a_si * (
        sigma * slope_si * h_dot_si - chi_si * q_si
    )
    bulk_port_power = 2 * math.pi * a_si * (
        -sigma * slope_si * h_dot_si + chi_si * q_si
    )
    checks.append(
        check_close(
            "compatible_reduced_pressure_and_capillary_power_cancellation",
            film_port_power + bulk_port_power,
            0.0,
            1.0e-20,
            detail={
                "film_port_power_W": film_port_power,
                "bulk_port_power_W": bulk_port_power,
                "surface_representation": "same small-slope capillary port on both sides",
            },
        )
    )
    counterexamples.extend(
        [
            {
                "name": "omitting_outer_capillary_line_work",
                "status": "EXPECTED_FAIL_REPRODUCED",
                "power_residual_W": 2
                * math.pi
                * a_si
                * sigma
                * slope_si
                * h_dot_si,
            },
            {
                "name": "using_same_pressure_work_sign_on_both_sides",
                "status": "EXPECTED_FAIL_REPRODUCED",
                "power_residual_W": -2
                * (2 * math.pi * a_si * chi_si * q_si),
            },
            {
                "name": "exact_SH_and_small_slope_TF_capillary_ports_do_not_exactly_cancel",
                "status": "EXPECTED_FAIL_REPRODUCED",
                "power_residual_W": 2
                * math.pi
                * a_si
                * sigma
                * (
                    slope_si
                    - slope_si / math.sqrt(1.0 + slope_si * slope_si)
                )
                * h_dot_si,
                "reason": "SH uses exact area while T2/T9 use the small-slope surface energy",
            },
        ]
    )

    # C1: the TF radial Stokes profile is a normalized scalar flux lifting.
    h = Fraction(1, 100)
    slip = Fraction(1, 1000)
    shape_integral = slip * h * h + h * h * h / 3
    mobility_times_mu = h * h * h / 3 + slip * h * h
    checks.append(
        {
            "name": "thin_film_velocity_lifting_normalization",
            "status": "PASS" if shape_integral == mobility_times_mu else "FAIL",
            "observed_exact_m3": str(shape_integral),
            "expected_exact_m3": str(mobility_times_mu),
            "shape": "b*h + h*y - y^2/2",
        }
    )

    # E1: one pressure/flux port has two trace unknowns and two independent
    # subdomain equations.  An independently imposed interface flux is a third
    # condition and is inconsistent unless it happens to equal the solved flux.
    r_f = Fraction(2)
    r_s = Fraction(3)
    p_left = Fraction(10)
    p_right = Fraction(0)
    proper_a = [[Fraction(1), r_f], [Fraction(1), -r_s]]
    proper_b = [[p_left], [p_right]]
    rank_a = matrix_rank(proper_a)
    rank_augmented = matrix_rank(
        [row + right for row, right in zip(proper_a, proper_b)]
    )
    solved_flux = (p_left - p_right) / (r_f + r_s)
    solved_pressure = p_left - r_f * solved_flux
    checks.append(
        {
            "name": "single_port_equation_count",
            "status": "PASS" if rank_a == rank_augmented == 2 else "FAIL",
            "coefficient_rank": rank_a,
            "augmented_rank": rank_augmented,
            "unknowns": ["p_match", "J_match"],
            "solution": {
                "p_match": str(solved_pressure),
                "J_match": str(solved_flux),
            },
        }
    )
    hydraulic_input_power = (p_left - p_right) * solved_flux
    hydraulic_dissipation = (r_f + r_s) * solved_flux * solved_flux
    checks.append(
        {
            "name": "two_domain_hydraulic_port_energy_balance",
            "status": "PASS"
            if hydraulic_input_power == hydraulic_dissipation
            else "FAIL",
            "input_power_exact": str(hydraulic_input_power),
            "film_dissipation_exact": str(r_f * solved_flux * solved_flux),
            "bulk_dissipation_exact": str(r_s * solved_flux * solved_flux),
            "total_dissipation_exact": str(hydraulic_dissipation),
            "interface_power_pair_exact": [
                str(-solved_pressure * solved_flux),
                str(solved_pressure * solved_flux),
            ],
        }
    )
    prescribed_flux = Fraction(1)
    over_a = proper_a + [[Fraction(0), Fraction(1)]]
    over_b = proper_b + [[prescribed_flux]]
    over_rank_a = matrix_rank(over_a)
    over_rank_augmented = matrix_rank(
        [row + right for row, right in zip(over_a, over_b)]
    )
    checks.append(
        {
            "name": "arbitrary_extra_interface_flux_is_detected",
            "status": "PASS" if over_rank_augmented > over_rank_a else "FAIL",
            "coefficient_rank": over_rank_a,
            "augmented_rank": over_rank_augmented,
            "prescribed_incompatible_J": str(prescribed_flux),
            "required_J": str(solved_flux),
        }
    )
    counterexamples.append(
        {
            "name": "pressure_and_incompatible_flow_on_same_internal_port",
            "status": "EXPECTED_FAIL_REPRODUCED",
            "coefficient_rank": over_rank_a,
            "augmented_rank": over_rank_augmented,
        }
    )

    # S1: an exact overlap split is insensitive to the arbitrary matching
    # location.  A deliberately mismatched resistance density exposes drift.
    match_locations = [Fraction(1, 5), Fraction(2, 5), Fraction(3, 5), Fraction(4, 5)]
    exact_fluxes: list[Fraction] = []
    mismatched_fluxes: list[float] = []
    pressure_drop = Fraction(3)
    for location in match_locations:
        r_inner = location + location * location / 2
        r_outer = (1 - location) + (1 - location * location) / 2
        exact_fluxes.append(pressure_drop / (r_inner + r_outer))

        # The outer model now uses 1+1.2*x rather than the common 1+x.
        r_outer_bad = (1 - location) + Fraction(3, 5) * (
            1 - location * location
        )
        mismatched_fluxes.append(float(pressure_drop / (r_inner + r_outer_bad)))

    exact_spread = max(exact_fluxes) - min(exact_fluxes)
    bad_spread = max(mismatched_fluxes) - min(mismatched_fluxes)
    checks.append(
        {
            "name": "manufactured_matching_location_invariance",
            "status": "PASS" if exact_spread == 0 else "FAIL",
            "locations": [str(value) for value in match_locations],
            "fluxes_exact": [str(value) for value in exact_fluxes],
            "spread_exact": str(exact_spread),
        }
    )
    counterexamples.append(
        {
            "name": "constitutive_mismatch_causes_match_location_drift",
            "status": "EXPECTED_FAIL_REPRODUCED",
            "fluxes": mismatched_fluxes,
            "absolute_spread": bad_spread,
            "relative_spread_to_mean": bad_spread
            / (sum(mismatched_fluxes) / len(mismatched_fluxes)),
        }
    )

    passed = sum(check["status"] == "PASS" for check in checks)
    failed = len(checks) - passed
    result = {
        "schema": "air-vortex-sh-tf-match-benchmark-v1",
        "claim": "PASS_SYNTHETIC_CONSERVATION_CONTRACT__NO_GO_DEVICE_COUPLING",
        "source_head_at_start": "3d974e554a34cadfa95a3978c69218e107277512",
        "python": platform.python_version(),
        "summary": {
            "checks_total": len(checks),
            "checks_passed": passed,
            "checks_failed": failed,
            "expected_counterexamples": len(counterexamples),
        },
        "checks": checks,
        "counterexamples": counterexamples,
        "not_tested": [
            "existence of a common SH/TF asymptotic overlap in the apparatus",
            "full pointwise traction and velocity continuity",
            "azimuthal velocity, shear, torque, and power continuity",
            "SH or TF production-solver integration",
            "space/time convergence of the coupled free boundary",
            "physical parameters, calibration, or holdout experiment",
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
