#!/usr/bin/env python3
"""Independent nonlinear Young--Laplace audit for the air-vortex model.

The script intentionally does not import :mod:`airvortex`.  It solves the
dimensionless axisymmetric free-surface problem from the equations documented
in ``research/非线性自由面_毛细与接触线理论.md`` and compares it with
the small-slope problem used by the executable reduced-order backend.

Run from the repository root with::

    python3 research/scripts/verify_nonlinear_surface.py

For the frozen default regression without human-readable tables::

    python3 research/scripts/verify_nonlinear_surface.py --summary-json

A non-zero exit status means that a solver, conservation, refinement, or
asymptotic check failed.  Numerical values are printed as Markdown tables so
that the reported evidence can be regenerated without project-internal code.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from functools import lru_cache
import json
import math
from pathlib import Path
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from scipy.integrate import quad, simpson, solve_bvp
from scipy.special import expi


FloatArray = NDArray[np.float64]
BoundaryKind = Literal["contact_angle", "pinned"]
DEFAULT_DENSITY = 998.2
DEFAULT_GRAVITY = 9.80665
DEFAULT_SURFACE_TENSION = 0.072
DEFAULT_CORE_RADIUS = 0.010
DEFAULT_VESSEL_RADIUS = 0.045
DEFAULT_LIQUID_DEPTH = 0.060
DEFAULT_MAGNET_BOTTOM_GAP = 0.0015
DEFAULT_MAGNET_RADIUS = 0.003
DEFAULT_C_PER_OMEGA = 9.0e-5
DEFAULT_BO = (
    DEFAULT_DENSITY
    * DEFAULT_GRAVITY
    * DEFAULT_CORE_RADIUS**2
    / DEFAULT_SURFACE_TENSION
)
DEFAULT_RADIUS_RATIO = DEFAULT_VESSEL_RADIUS / DEFAULT_CORE_RADIUS
DEFAULT_GAP = DEFAULT_LIQUID_DEPTH - (
    DEFAULT_MAGNET_BOTTOM_GAP + 2.0 * DEFAULT_MAGNET_RADIUS
)
DEFAULT_GAP_RATIO = DEFAULT_GAP / DEFAULT_CORE_RADIUS


@dataclass(frozen=True, slots=True)
class Problem:
    """Dimensionless capillary-surface problem."""

    bond: float
    radius_ratio: float
    boundary: BoundaryKind = "contact_angle"
    contact_angle_deg: float = 90.0
    pinned_height: float = 0.0
    mesh_points: int = 401
    tolerance: float = 3.0e-7
    axis_fraction: float = 1.0e-7

    def __post_init__(self) -> None:
        if not (self.bond > 0.0 and self.radius_ratio > 0.0):
            raise ValueError("bond and radius_ratio must be positive")
        if self.boundary == "contact_angle" and not (
            1.0 < self.contact_angle_deg < 179.0
        ):
            raise ValueError("contact angle must lie strictly between 1 and 179 deg")
        if self.mesh_points < 80 or self.tolerance <= 0.0:
            raise ValueError("mesh_points/tolerance do not define an audit-quality solve")

    @property
    def axis_cutoff(self) -> float:
        return max(1.0e-8, self.axis_fraction * self.radius_ratio)


@dataclass(slots=True)
class SurfaceSolution:
    """A converged SciPy collocation solution plus its physical metadata."""

    problem: Problem
    drive: float
    nonlinear: bool
    raw: object

    def evaluate(self, x: FloatArray) -> tuple[FloatArray, FloatArray, FloatArray]:
        values = self.raw.sol(x)
        height = np.asarray(values[0], dtype=np.float64)
        if self.nonlinear:
            # q=tanh(a)=y'/sqrt(1+y'^2) is the bounded normalised slope;
            # the liquid-to-gas normal has radial component n_r=-q.
            # Also dy/dx=sinh(a), so this state cannot step outside |q|<1.
            angle_variable = np.asarray(values[1], dtype=np.float64)
            slope = np.sinh(angle_variable)
            q = np.tanh(angle_variable)
        else:
            slope = np.asarray(values[1], dtype=np.float64)
            q = slope / np.sqrt(1.0 + slope * slope)
        return height, slope, q


@dataclass(frozen=True, slots=True)
class Metrics:
    minimum: float
    maximum: float
    depth: float
    maximum_abs_slope: float
    maximum_abs_q: float
    curvature_linearisation_l2_error: float | None
    volume_mean: float
    rms_residual: float
    pressure_invariant_error: float | None


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _potential_series_coefficients(count: int = 36) -> FloatArray:
    """Coefficients of ``((1-exp(-t))/t)**2`` about t=0."""

    base = np.array(
        [(-1.0) ** k / math.factorial(k + 1) for k in range(count)],
        dtype=np.float64,
    )
    return np.convolve(base, base)


_POTENTIAL_COEFFICIENTS = _potential_series_coefficients()


def burgers_pressure_potential(x: FloatArray | float) -> FloatArray:
    r"""Return the dimensionless Burgers centrifugal potential ``F(x)``.

    .. math::

       F(x)=\int_0^x\frac{(1-e^{-s^2})^2}{s^3}\,ds
           =\frac12 I(x^2), \qquad F(\infty)=\ln 2.

    The exponential-integral closed form suffers cancellation near the axis;
    there a 36-term Taylor evaluation is used instead.
    """

    values = np.asarray(x, dtype=np.float64)
    if np.any(values < 0.0):
        raise ValueError("x must be non-negative")
    t = values * values
    result = np.empty_like(t)
    small = t < 0.25

    t_small = t[small]
    series = np.zeros_like(t_small)
    # I(t)=sum_n b_n*t**(n+1)/(n+1), F=I/2.
    for n in range(35, -1, -1):
        series = (series + _POTENTIAL_COEFFICIENTS[n] / (n + 1)) * t_small
    result[small] = 0.5 * series

    t_large = t[~small]
    if t_large.size:
        i_value = 2.0 * (
            np.log(2.0) + expi(-t_large) - expi(-2.0 * t_large)
        ) - np.square(-np.expm1(-t_large)) / t_large
        result[~small] = 0.5 * i_value
    return result


def _wall_residual(problem: Problem, y_wall: float, state_wall: float,
                   nonlinear: bool) -> float:
    if problem.boundary == "pinned":
        return y_wall - problem.pinned_height
    theta = math.radians(problem.contact_angle_deg)
    if nonlinear:
        return math.tanh(state_wall) - math.cos(theta)
    return state_wall - 1.0 / math.tan(theta)


def solve_surface(
    problem: Problem,
    drive: float,
    *,
    nonlinear: bool,
    seed: SurfaceSolution | None = None,
) -> SurfaceSolution:
    """Solve one load point using an axis-regularised fourth-order collocation BVP."""

    if drive < 0.0 or not math.isfinite(drive):
        raise ValueError("drive must be finite and non-negative")
    eps = problem.axis_cutoff
    x = np.linspace(eps, problem.radius_ratio, problem.mesh_points)
    if seed is None:
        initial = np.zeros((3, x.size), dtype=np.float64)
        pressure_guess = np.array([0.0], dtype=np.float64)
    else:
        initial = np.asarray(seed.raw.sol(x), dtype=np.float64)
        # Convert between the exact angle variable and the linear slope only
        # when a caller deliberately supplies a cross-model seed.
        if seed.nonlinear != nonlinear:
            if nonlinear:
                initial[1] = np.arcsinh(initial[1])
            else:
                initial[1] = np.sinh(initial[1])
        pressure_guess = np.asarray(seed.raw.p, dtype=np.float64).copy()

    if nonlinear:

        def equations(x_value: FloatArray, state: FloatArray,
                      pressure: FloatArray) -> FloatArray:
            a = state[1]
            # Successful audit branches remain far below this numerical guard.
            a_safe = np.clip(a, -15.0, 15.0)
            q = np.tanh(a_safe)
            return np.vstack(
                (
                    np.sinh(a_safe),
                    np.cosh(a_safe) ** 2
                    * (
                        problem.bond * state[0]
                        - drive * burgers_pressure_potential(x_value)
                        - pressure[0]
                        - q / x_value
                    ),
                    state[0] * x_value,
                )
            )

        def boundary(left: FloatArray, right: FloatArray,
                     pressure: FloatArray) -> FloatArray:
            axis_rhs = (
                problem.bond * left[0]
                - drive * float(burgers_pressure_potential(eps))
                - pressure[0]
            )
            # q=A(0)x/2+O(x^3), V=y(0)x^2/2+O(x^4).
            return np.array(
                (
                    math.tanh(left[1]) - 0.5 * axis_rhs * eps,
                    left[2] - 0.5 * left[0] * eps * eps,
                    _wall_residual(
                        problem, right[0], right[1], nonlinear=True
                    ),
                    right[2],
                ),
                dtype=np.float64,
            )

        def equations_jacobian(
            x_value: FloatArray, state: FloatArray, pressure: FloatArray
        ) -> tuple[FloatArray, FloatArray]:
            a = np.clip(state[1], -15.0, 15.0)
            sinh_a = np.sinh(a)
            cosh_a = np.cosh(a)
            cosh_sq = cosh_a * cosh_a
            q = np.tanh(a)
            forcing_residual = (
                problem.bond * state[0]
                - drive * burgers_pressure_potential(x_value)
                - pressure[0]
                - q / x_value
            )
            state_jac = np.zeros((3, 3, x_value.size), dtype=np.float64)
            parameter_jac = np.zeros((3, 1, x_value.size), dtype=np.float64)
            state_jac[0, 1] = cosh_a
            state_jac[1, 0] = problem.bond * cosh_sq
            state_jac[1, 1] = (
                2.0 * sinh_a * cosh_a * forcing_residual - 1.0 / x_value
            )
            state_jac[2, 0] = x_value
            parameter_jac[1, 0] = -cosh_sq
            return state_jac, parameter_jac

        def boundary_jacobian(
            left: FloatArray, right: FloatArray, pressure: FloatArray
        ) -> tuple[FloatArray, FloatArray, FloatArray]:
            del pressure
            left_jac = np.zeros((4, 3), dtype=np.float64)
            right_jac = np.zeros((4, 3), dtype=np.float64)
            parameter_jac = np.zeros((4, 1), dtype=np.float64)
            left_jac[0] = (-0.5 * problem.bond * eps,
                           1.0 / math.cosh(left[1]) ** 2, 0.0)
            parameter_jac[0, 0] = 0.5 * eps
            left_jac[1] = (-0.5 * eps * eps, 0.0, 1.0)
            if problem.boundary == "pinned":
                right_jac[2, 0] = 1.0
            else:
                right_jac[2, 1] = 1.0 / math.cosh(right[1]) ** 2
            right_jac[3, 2] = 1.0
            return left_jac, right_jac, parameter_jac

    else:

        def equations(x_value: FloatArray, state: FloatArray,
                      pressure: FloatArray) -> FloatArray:
            return np.vstack(
                (
                    state[1],
                    problem.bond * state[0]
                    - drive * burgers_pressure_potential(x_value)
                    - pressure[0]
                    - state[1] / x_value,
                    state[0] * x_value,
                )
            )

        def boundary(left: FloatArray, right: FloatArray,
                     pressure: FloatArray) -> FloatArray:
            axis_rhs = (
                problem.bond * left[0]
                - drive * float(burgers_pressure_potential(eps))
                - pressure[0]
            )
            return np.array(
                (
                    left[1] - 0.5 * axis_rhs * eps,
                    left[2] - 0.5 * left[0] * eps * eps,
                    _wall_residual(
                        problem, right[0], right[1], nonlinear=False
                    ),
                    right[2],
                ),
                dtype=np.float64,
            )

        def equations_jacobian(
            x_value: FloatArray, state: FloatArray, pressure: FloatArray
        ) -> tuple[FloatArray, FloatArray]:
            del state, pressure
            state_jac = np.zeros((3, 3, x_value.size), dtype=np.float64)
            parameter_jac = np.zeros((3, 1, x_value.size), dtype=np.float64)
            state_jac[0, 1] = 1.0
            state_jac[1, 0] = problem.bond
            state_jac[1, 1] = -1.0 / x_value
            state_jac[2, 0] = x_value
            parameter_jac[1, 0] = -1.0
            return state_jac, parameter_jac

        def boundary_jacobian(
            left: FloatArray, right: FloatArray, pressure: FloatArray
        ) -> tuple[FloatArray, FloatArray, FloatArray]:
            del left, right, pressure
            left_jac = np.zeros((4, 3), dtype=np.float64)
            right_jac = np.zeros((4, 3), dtype=np.float64)
            parameter_jac = np.zeros((4, 1), dtype=np.float64)
            left_jac[0] = (-0.5 * problem.bond * eps, 1.0, 0.0)
            parameter_jac[0, 0] = 0.5 * eps
            left_jac[1] = (-0.5 * eps * eps, 0.0, 1.0)
            if problem.boundary == "pinned":
                right_jac[2, 0] = 1.0
            else:
                right_jac[2, 1] = 1.0
            right_jac[3, 2] = 1.0
            return left_jac, right_jac, parameter_jac

    raw = solve_bvp(
        equations,
        boundary,
        x,
        initial,
        p=pressure_guess,
        fun_jac=equations_jacobian,
        bc_jac=boundary_jacobian,
        tol=problem.tolerance,
        bc_tol=problem.tolerance,
        max_nodes=60_000,
        verbose=0,
    )
    if not raw.success:
        raise RuntimeError(
            f"BVP failed: Bo={problem.bond:g}, L={problem.radius_ratio:g}, "
            f"W={drive:g}, nonlinear={nonlinear}: {raw.message}"
        )
    if nonlinear and float(np.max(np.abs(raw.y[1]))) >= 12.0:
        raise RuntimeError(
            "converged branch entered the hyperbolic-function overflow guard; "
            "the reported graph is not certified"
        )
    return SurfaceSolution(problem, drive, nonlinear, raw)


def static_solution(problem: Problem, *, nonlinear: bool) -> SurfaceSolution:
    """Find the zero-drive meniscus, continuing from 90 deg if necessary."""

    if problem.boundary != "contact_angle" or math.isclose(
        problem.contact_angle_deg, 90.0, abs_tol=1.0e-13
    ):
        return solve_surface(problem, 0.0, nonlinear=nonlinear)
    target = problem.contact_angle_deg
    seed: SurfaceSolution | None = None
    for angle in np.linspace(90.0, target, 19):
        intermediate = replace(problem, contact_angle_deg=float(angle))
        seed = solve_surface(intermediate, 0.0, nonlinear=nonlinear, seed=seed)
    # Attach the exact requested problem rather than the final temporary copy.
    assert seed is not None
    seed.problem = problem
    return seed


def advance_to(
    problem: Problem,
    target_drive: float,
    *,
    nonlinear: bool,
    seed: SurfaceSolution | None = None,
    maximum_step: float = 5.0,
) -> SurfaceSolution:
    """Natural-parameter continuation in W, with bounded predictor steps."""

    current = static_solution(problem, nonlinear=nonlinear) if seed is None else seed
    if math.isclose(current.drive, target_drive, rel_tol=0.0, abs_tol=1.0e-14):
        return current
    distance = target_drive - current.drive
    steps = max(1, math.ceil(abs(distance) / maximum_step))
    for drive in np.linspace(current.drive, target_drive, steps + 1)[1:]:
        current = solve_surface(
            problem, float(drive), nonlinear=nonlinear, seed=current
        )
    return current


@lru_cache(maxsize=16)
def _mean_potential(radius_ratio: float) -> float:
    x = np.linspace(0.0, radius_ratio, 40_001)
    return float(
        2.0
        * simpson(burgers_pressure_potential(x) * x, x=x)
        / radius_ratio**2
    )


def mean_potential_closed_form(radius_ratio: float) -> float:
    """Return the finite-vessel area mean M(L) in analytic Ei form.

    The formula is evaluated only for positive ``L``.  It is well conditioned
    for the parameter range audited here; very small vessels should instead
    use the Taylor series of F before taking the area mean.
    """

    if radius_ratio <= 0.0 or not math.isfinite(radius_ratio):
        raise ValueError("radius_ratio must be finite and positive")
    squared_radius = radius_ratio**2
    one_minus_exp = -math.expm1(-squared_radius)
    i_value = 2.0 * (
        math.log(2.0)
        + float(expi(-squared_radius))
        - float(expi(-2.0 * squared_radius))
    ) - one_minus_exp**2 / squared_radius
    j_value = (
        float(np.euler_gamma)
        + math.log(squared_radius / 2.0)
        - 2.0 * float(expi(-squared_radius))
        + float(expi(-2.0 * squared_radius))
    )
    return 0.5 * (i_value - j_value / squared_radius)


def capillary_singular_outer_rpm(radius_ratio: float) -> float:
    """Physical first-contact rpm of the sigma->0 finite-vessel outer solution."""

    mean_potential = mean_potential_closed_form(radius_ratio)
    omega = (
        DEFAULT_CORE_RADIUS
        / DEFAULT_C_PER_OMEGA
        * math.sqrt(DEFAULT_GRAVITY * DEFAULT_GAP / mean_potential)
    )
    return omega * 60.0 / (2.0 * math.pi)


def metrics(solution: SurfaceSolution) -> Metrics:
    problem = solution.problem
    x = np.linspace(problem.axis_cutoff, problem.radius_ratio, 12_001)
    height, slope, q = solution.evaluate(x)
    volume_mean = float(
        2.0 * simpson(height * x, x=x) / problem.radius_ratio**2
    )
    wall_state = solution.raw.sol(problem.radius_ratio)
    # The integrated flux is q for exact curvature and y' for the linearised
    # equation.  Computing it from the converged wall state makes the invariant
    # valid for both contact-angle and pinned boundaries.
    wall_flux = (
        math.tanh(float(wall_state[1]))
        if solution.nonlinear
        else float(wall_state[1])
    )
    expected_pressure = (
        -solution.drive * _mean_potential(problem.radius_ratio)
        - 2.0 * wall_flux / problem.radius_ratio
    )
    pressure_error = float(solution.raw.p[0] - expected_pressure)
    curvature_error: float | None = None
    if solution.nonlinear:
        angle = np.asarray(solution.raw.sol(x)[1], dtype=np.float64)
        exact_divergence = (
            problem.bond * height
            - solution.drive * burgers_pressure_potential(x)
            - float(solution.raw.p[0])
        )
        q_derivative = exact_divergence - q / x
        slope_derivative = np.cosh(angle) ** 3 * q_derivative
        linear_divergence_on_exact_shape = slope_derivative + slope / x
        numerator = simpson(
            np.square(linear_divergence_on_exact_shape - exact_divergence) * x,
            x=x,
        )
        denominator = simpson(np.square(exact_divergence) * x, x=x)
        curvature_error = float(
            math.sqrt(numerator / max(denominator, 1.0e-300))
        )
    return Metrics(
        minimum=float(np.min(height)),
        maximum=float(np.max(height)),
        depth=float(np.max(height) - np.min(height)),
        maximum_abs_slope=float(np.max(np.abs(slope))),
        maximum_abs_q=float(np.max(np.abs(q))),
        curvature_linearisation_l2_error=curvature_error,
        volume_mean=volume_mean,
        rms_residual=float(np.max(solution.raw.rms_residuals)),
        pressure_invariant_error=pressure_error,
    )


def weighted_shape_error(reference: SurfaceSolution,
                         approximation: SurfaceSolution) -> float:
    problem = reference.problem
    x = np.linspace(problem.axis_cutoff, problem.radius_ratio, 12_001)
    exact = reference.evaluate(x)[0]
    approx = approximation.evaluate(x)[0]
    numerator = simpson(np.square(approx - exact) * x, x=x)
    denominator = simpson(np.square(exact) * x, x=x)
    return float(math.sqrt(numerator / max(denominator, 1.0e-300)))


def _contact_measure(solution: SurfaceSolution, gap: float) -> float:
    # The axisymmetric surrogate defines first contact at the magnet centre.
    # A non-90-degree wall meniscus can be lower at r=R without being above
    # the stir bar; using the global surface minimum would then be a geometry
    # error rather than a conservative contact criterion.
    axis_height = float(solution.raw.sol(solution.problem.axis_cutoff)[0])
    return -axis_height - gap


def touch_threshold(
    problem: Problem,
    gap: float,
    *,
    nonlinear: bool,
) -> tuple[float, SurfaceSolution]:
    """Find W where the axis first reaches y=-gap in the magnet-centre surrogate."""

    if gap <= 0.0:
        raise ValueError("gap must be positive")
    base = static_solution(problem, nonlinear=nonlinear)
    if _contact_measure(base, gap) >= 0.0:
        return 0.0, base

    # The W=1 linear response provides a scale even for a nonlinear search.
    linear_base = static_solution(problem, nonlinear=False)
    linear_one = advance_to(problem, 1.0, nonlinear=False, seed=linear_base)
    base_axis = float(linear_base.raw.sol(problem.axis_cutoff)[0])
    one_axis = float(linear_one.raw.sol(problem.axis_cutoff)[0])
    response = base_axis - one_axis
    estimate = max(1.0, gap / max(response, 1.0e-12))
    lower_drive = 0.0
    lower = base
    upper_drive = max(1.0, 1.15 * estimate)
    upper = advance_to(
        problem,
        upper_drive,
        nonlinear=nonlinear,
        seed=lower,
        maximum_step=max(1.0, upper_drive / 24.0),
    )
    while _contact_measure(upper, gap) < 0.0:
        lower_drive, lower = upper_drive, upper
        upper_drive *= 1.4
        upper = advance_to(
            problem,
            upper_drive,
            nonlinear=nonlinear,
            seed=lower,
            maximum_step=max(1.0, upper_drive / 24.0),
        )

    # Thirty-six bisections reduce the W bracket below 3e-9 even from the
    # broadest default search interval.  This is tighter than the independent
    # direct-contact BVP comparison and prevents finite-difference threshold
    # sensitivities from being dominated by root-bracket noise.
    for _ in range(36):
        middle_drive = 0.5 * (lower_drive + upper_drive)
        seed = lower if middle_drive - lower_drive <= upper_drive - middle_drive else upper
        middle = solve_surface(
            problem, middle_drive, nonlinear=nonlinear, seed=seed
        )
        if _contact_measure(middle, gap) >= 0.0:
            upper_drive, upper = middle_drive, middle
        else:
            lower_drive, lower = middle_drive, middle
    final_drive = 0.5 * (lower_drive + upper_drive)
    final_seed = lower if final_drive - lower_drive <= upper_drive - final_drive else upper
    final = solve_surface(
        problem, final_drive, nonlinear=nonlinear, seed=final_seed
    )
    return final_drive, final


def direct_radial_q_contact_check(
    problem: Problem,
    gap: float,
    seed: SurfaceSolution,
) -> tuple[float, float, float, int]:
    """Cross-check contact with the independent state ``w=x*q``.

    Unlike the production audit branch, this formulation includes the true
    axis ``x=0`` and solves ``W`` and ``Lambda`` simultaneously from the fixed
    contact condition ``y(0)=-gap``.  Its numerical Jacobian is intentionally
    left to SciPy so that it does not share the angle-state analytic Jacobian.
    """

    if problem.boundary != "contact_angle" or not math.isclose(
        problem.contact_angle_deg, 90.0, abs_tol=1.0e-13
    ):
        raise ValueError("direct w=x*q check is implemented for the 90 deg branch")
    if not seed.nonlinear or gap <= 0.0:
        raise ValueError("the direct check needs a nonlinear seed and positive gap")

    x = np.linspace(0.0, problem.radius_ratio, 500)
    seed_x = np.maximum(x, problem.axis_cutoff)
    height, _, q = seed.evaluate(seed_x)
    axis_state = seed.raw.sol(problem.axis_cutoff)
    axis_source = (
        problem.bond * float(axis_state[0])
        - seed.drive * float(burgers_pressure_potential(problem.axis_cutoff))
        - float(seed.raw.p[0])
    )
    height[0] = float(axis_state[0]) - 0.25 * axis_source * problem.axis_cutoff**2
    q[0] = 0.0
    radial_q = x * q
    integrand = x * height
    volume = np.zeros_like(x)
    volume[1:] = np.cumsum(
        0.5 * (integrand[:-1] + integrand[1:]) * np.diff(x)
    )
    initial = np.vstack((height, radial_q, volume))

    def equations(
        coordinate: FloatArray, state: FloatArray, parameters: FloatArray
    ) -> FloatArray:
        pressure, drive = parameters
        q_value = np.divide(
            state[1],
            coordinate,
            out=np.zeros_like(coordinate),
            where=coordinate > 0.0,
        )
        # This is only an overflow guard.  The result is rejected below if the
        # converged branch comes remotely close to it.
        q_safe = np.clip(q_value, -0.999999999, 0.999999999)
        return np.vstack(
            (
                q_safe / np.sqrt(1.0 - q_safe * q_safe),
                coordinate
                * (
                    problem.bond * state[0]
                    - drive * burgers_pressure_potential(coordinate)
                    - pressure
                ),
                coordinate * state[0],
            )
        )

    def boundary(
        left: FloatArray, right: FloatArray, parameters: FloatArray
    ) -> FloatArray:
        del parameters
        return np.array(
            (left[1], left[2], right[1], right[2], left[0] + gap),
            dtype=np.float64,
        )

    raw = solve_bvp(
        equations,
        boundary,
        x,
        initial,
        p=np.array([float(seed.raw.p[0]), seed.drive], dtype=np.float64),
        tol=1.0e-8,
        bc_tol=1.0e-10,
        max_nodes=30_000,
    )
    if not raw.success:
        raise RuntimeError(f"direct w=x*q contact check failed: {raw.message}")
    comparison_x = np.linspace(0.0, problem.radius_ratio, 12_001)
    direct_state = raw.sol(comparison_x)
    direct_q = np.divide(
        direct_state[1],
        comparison_x,
        out=np.zeros_like(comparison_x),
        where=comparison_x > 0.0,
    )
    require(float(np.max(np.abs(direct_q))) < 0.99,
            "direct w=x*q solution entered its q overflow guard")
    seed_height = seed.raw.sol(
        np.maximum(comparison_x, problem.axis_cutoff)
    )[0]
    maximum_height_difference = float(
        np.max(np.abs(direct_state[0] - seed_height))
    )
    return (
        float(raw.p[1]),
        float(np.max(raw.rms_residuals)),
        maximum_height_difference,
        int(raw.x.size),
    )


def slope_boundary(
    problem: Problem,
    target_slope: float,
) -> tuple[float, SurfaceSolution]:
    """Locate an operational graph/small-slope boundary by max |dy/dx|."""

    if target_slope <= 0.0:
        raise ValueError("target_slope must be positive")
    lower_drive = 0.0
    lower = static_solution(problem, nonlinear=True)
    if metrics(lower).maximum_abs_slope >= target_slope:
        return 0.0, lower
    upper_drive = max(1.0, 0.5 * problem.bond)
    upper = advance_to(problem, upper_drive, nonlinear=True, seed=lower)
    while metrics(upper).maximum_abs_slope < target_slope:
        lower_drive, lower = upper_drive, upper
        upper_drive *= 1.5
        upper = advance_to(
            problem,
            upper_drive,
            nonlinear=True,
            seed=lower,
            maximum_step=max(1.0, upper_drive / 24.0),
        )
        if upper_drive > 20_000.0:
            raise RuntimeError("no finite operational slope boundary was found")
    for _ in range(34):
        middle_drive = 0.5 * (lower_drive + upper_drive)
        seed = lower if middle_drive - lower_drive <= upper_drive - middle_drive else upper
        middle = solve_surface(
            problem, middle_drive, nonlinear=True, seed=seed
        )
        if metrics(middle).maximum_abs_slope >= target_slope:
            upper_drive, upper = middle_drive, middle
        else:
            lower_drive, lower = middle_drive, middle
    final_drive = 0.5 * (lower_drive + upper_drive)
    final_seed = lower if final_drive - lower_drive <= upper_drive - final_drive else upper
    final = solve_surface(problem, final_drive, nonlinear=True, seed=final_seed)
    return final_drive, final


def solve_pinned_at_wall_q(
    problem: Problem,
    wall_q: float,
    *,
    seed: SurfaceSolution | None = None,
) -> SurfaceSolution:
    """Continue a pinned branch with wall normalised slope q(R) as parameter.

    Natural continuation in W becomes ill-conditioned as a pinned meniscus
    approaches a vertical wall tangent.  Here W and Lambda are both unknowns,
    while q(R) is prescribed.  This distinguishes a geometric graph boundary
    from an arbitrary failure of W-continuation.
    """

    if problem.boundary != "pinned":
        raise ValueError("q-wall continuation is defined only for pinned boundaries")
    if not (-1.0 < wall_q < 1.0):
        raise ValueError("wall_q must lie strictly between -1 and 1")
    eps = problem.axis_cutoff
    x = np.linspace(eps, problem.radius_ratio, problem.mesh_points)
    if seed is None:
        regular = advance_to(problem, 40.0, nonlinear=True)
        initial = np.asarray(regular.raw.sol(x), dtype=np.float64)
        parameters = np.array([regular.raw.p[0], regular.drive], dtype=np.float64)
    else:
        initial = np.asarray(seed.raw.sol(x), dtype=np.float64)
        parameters = np.array(
            [seed.raw.p[0], seed.drive], dtype=np.float64
        )

    def equations(x_value: FloatArray, state: FloatArray,
                  parameters_value: FloatArray) -> FloatArray:
        pressure, drive = parameters_value
        a = np.clip(state[1], -18.0, 18.0)
        q = np.tanh(a)
        return np.vstack(
            (
                np.sinh(a),
                np.cosh(a) ** 2
                * (
                    problem.bond * state[0]
                    - drive * burgers_pressure_potential(x_value)
                    - pressure
                    - q / x_value
                ),
                state[0] * x_value,
            )
        )

    def boundary(left: FloatArray, right: FloatArray,
                 parameters_value: FloatArray) -> FloatArray:
        pressure, drive = parameters_value
        axis_rhs = (
            problem.bond * left[0]
            - drive * float(burgers_pressure_potential(eps))
            - pressure
        )
        return np.array(
            (
                math.tanh(left[1]) - 0.5 * axis_rhs * eps,
                left[2] - 0.5 * left[0] * eps * eps,
                right[0] - problem.pinned_height,
                right[2],
                math.tanh(right[1]) - wall_q,
            ),
            dtype=np.float64,
        )

    def equations_jacobian(
        x_value: FloatArray, state: FloatArray, parameters_value: FloatArray
    ) -> tuple[FloatArray, FloatArray]:
        pressure, drive = parameters_value
        a = np.clip(state[1], -18.0, 18.0)
        sinh_a = np.sinh(a)
        cosh_a = np.cosh(a)
        cosh_sq = cosh_a * cosh_a
        q = np.tanh(a)
        potential = burgers_pressure_potential(x_value)
        forcing_residual = (
            problem.bond * state[0]
            - drive * potential
            - pressure
            - q / x_value
        )
        state_jac = np.zeros((3, 3, x_value.size), dtype=np.float64)
        parameter_jac = np.zeros((3, 2, x_value.size), dtype=np.float64)
        state_jac[0, 1] = cosh_a
        state_jac[1, 0] = problem.bond * cosh_sq
        state_jac[1, 1] = (
            2.0 * sinh_a * cosh_a * forcing_residual - 1.0 / x_value
        )
        state_jac[2, 0] = x_value
        parameter_jac[1, 0] = -cosh_sq
        parameter_jac[1, 1] = -cosh_sq * potential
        return state_jac, parameter_jac

    def boundary_jacobian(
        left: FloatArray, right: FloatArray, parameters_value: FloatArray
    ) -> tuple[FloatArray, FloatArray, FloatArray]:
        del parameters_value
        left_jac = np.zeros((5, 3), dtype=np.float64)
        right_jac = np.zeros((5, 3), dtype=np.float64)
        parameter_jac = np.zeros((5, 2), dtype=np.float64)
        left_jac[0] = (-0.5 * problem.bond * eps,
                       1.0 / math.cosh(left[1]) ** 2, 0.0)
        parameter_jac[0, 0] = 0.5 * eps
        parameter_jac[0, 1] = (
            0.5 * eps * float(burgers_pressure_potential(eps))
        )
        left_jac[1] = (-0.5 * eps * eps, 0.0, 1.0)
        right_jac[2, 0] = 1.0
        right_jac[3, 2] = 1.0
        right_jac[4, 1] = 1.0 / math.cosh(right[1]) ** 2
        return left_jac, right_jac, parameter_jac

    raw = solve_bvp(
        equations,
        boundary,
        x,
        initial,
        p=parameters,
        fun_jac=equations_jacobian,
        bc_jac=boundary_jacobian,
        tol=problem.tolerance,
        bc_tol=problem.tolerance,
        max_nodes=100_000,
    )
    if not raw.success:
        raise RuntimeError(
            f"pinned q-continuation failed at q_wall={wall_q}: {raw.message}"
        )
    if float(np.max(np.abs(raw.y[1]))) >= 15.0:
        raise RuntimeError(
            "pinned branch entered the hyperbolic-function overflow guard; "
            "the endpoint extrapolation is not certified"
        )
    return SurfaceSolution(problem, float(raw.p[1]), True, raw)


@lru_cache(maxsize=4)
def pinned_graph_limit(problem: Problem) -> tuple[float, float, list[SurfaceSolution]]:
    """Extrapolate q(R)->-1 using the observed square-root endpoint law."""

    targets = (-0.80, -0.90, -0.95, -0.98, -0.99, -0.995,
               -0.998, -0.999, -0.9995, -0.9998)
    solutions: list[SurfaceSolution] = []
    seed: SurfaceSolution | None = None
    for target in targets:
        seed = solve_pinned_at_wall_q(problem, target, seed=seed)
        certificate = metrics(seed)
        left_state = seed.raw.sol(problem.axis_cutoff)
        right_state = seed.raw.sol(problem.radius_ratio)
        axis_volume_residual = float(
            left_state[2]
            - 0.5 * left_state[0] * problem.axis_cutoff**2
        )
        require(
            max(abs(axis_volume_residual), abs(float(right_state[2])))
            < 2.0e-10,
            "pinned continuation BVP volume boundary failed",
        )
        require(certificate.rms_residual < 4.0 * problem.tolerance,
                "pinned continuation collocation residual failed")
        require(
            certificate.pressure_invariant_error is not None
            and abs(certificate.pressure_invariant_error) < 2.0e-7,
            "pinned continuation integrated-pressure invariant failed",
        )
        solutions.append(seed)

    # If delta=1+q_wall, q=s/sqrt(1+s^2) gives |s|~(2 delta)^-1/2.
    # A quadratic fit in sqrt(delta) therefore has a physically motivated
    # finite intercept.  The last three points give a reproducible one-sided
    # endpoint estimate; it is an extrapolation, not a claimed exact fold.
    tail = solutions[-3:]
    delta = np.array([1.0 + targets[-3 + i] for i in range(3)])
    design = np.column_stack((np.ones(3), np.sqrt(delta), delta))
    drive_limit = float(
        np.linalg.solve(design, np.array([item.drive for item in tail]))[0]
    )
    depth_limit = float(
        np.linalg.solve(
            design,
            np.array([-metrics(item).minimum for item in tail]),
        )[0]
    )
    return drive_limit, depth_limit, solutions


def pinned_endpoint_tail_fits(
    solutions: list[SurfaceSolution],
) -> list[tuple[int, float, float]]:
    """Fit the q->-1 intercept over the last 3, 4, and 5 branch points."""

    if len(solutions) < 5:
        raise ValueError("at least five pinned continuation points are required")
    q_wall = np.array(
        [
            math.tanh(float(item.raw.sol(item.problem.radius_ratio)[1]))
            for item in solutions
        ],
        dtype=np.float64,
    )
    drives = np.array([item.drive for item in solutions], dtype=np.float64)
    depths = np.array(
        [-metrics(item).minimum for item in solutions], dtype=np.float64
    )
    estimates: list[tuple[int, float, float]] = []
    for count in (3, 4, 5):
        delta = 1.0 + q_wall[-count:]
        design = np.column_stack(
            (np.ones(count), np.sqrt(delta), delta)
        )
        drive_coefficients = np.linalg.lstsq(
            design, drives[-count:], rcond=None
        )[0]
        depth_coefficients = np.linalg.lstsq(
            design, depths[-count:], rcond=None
        )[0]
        estimates.append(
            (count, float(drive_coefficients[0]), float(depth_coefficients[0]))
        )
    return estimates


def drive_from_rpm(rpm: float) -> float:
    """Convert actual magnet speed to W using the frozen C=A*Omega closure."""

    omega = rpm * 2.0 * math.pi / 60.0
    circulation_scale = DEFAULT_C_PER_OMEGA * omega
    return (
        DEFAULT_DENSITY
        * circulation_scale**2
        / (DEFAULT_SURFACE_TENSION * DEFAULT_CORE_RADIUS)
    )


def rpm_from_drive(drive: float) -> float:
    """Invert ``drive_from_rpm`` for non-negative dimensionless drive W."""

    if drive < 0.0:
        raise ValueError("drive must be non-negative")
    omega = math.sqrt(
        drive
        * DEFAULT_SURFACE_TENSION
        * DEFAULT_CORE_RADIUS
        / (DEFAULT_DENSITY * DEFAULT_C_PER_OMEGA**2)
    )
    return omega * 60.0 / (2.0 * math.pi)


@lru_cache(maxsize=1)
def local_contact_sensitivities() -> dict[str, float]:
    """Centered finite-difference sensitivities of the exact 90 deg threshold."""

    angle_step_deg = 1.0
    lower_angle = Problem(
        DEFAULT_BO,
        DEFAULT_RADIUS_RATIO,
        contact_angle_deg=90.0 - angle_step_deg,
        tolerance=1.0e-8,
    )
    upper_angle = replace(
        lower_angle, contact_angle_deg=90.0 + angle_step_deg
    )
    lower_angle_drive, _ = touch_threshold(
        lower_angle, DEFAULT_GAP_RATIO, nonlinear=True
    )
    upper_angle_drive, _ = touch_threshold(
        upper_angle, DEFAULT_GAP_RATIO, nonlinear=True
    )

    gap_step = 0.01
    problem = Problem(
        DEFAULT_BO, DEFAULT_RADIUS_RATIO, tolerance=1.0e-8
    )
    lower_gap_drive, _ = touch_threshold(
        problem, DEFAULT_GAP_RATIO - gap_step, nonlinear=True
    )
    upper_gap_drive, _ = touch_threshold(
        problem, DEFAULT_GAP_RATIO + gap_step, nonlinear=True
    )
    drive_per_degree = (
        (upper_angle_drive - lower_angle_drive) / (2.0 * angle_step_deg)
    )
    rpm_per_degree = (
        rpm_from_drive(upper_angle_drive) - rpm_from_drive(lower_angle_drive)
    ) / (2.0 * angle_step_deg)
    drive_per_gap_ratio = (
        upper_gap_drive - lower_gap_drive
    ) / (2.0 * gap_step)
    rpm_per_gap_ratio = (
        rpm_from_drive(upper_gap_drive) - rpm_from_drive(lower_gap_drive)
    ) / (2.0 * gap_step)
    return {
        "angle_step_deg": angle_step_deg,
        "gap_ratio_step": gap_step,
        "drive_per_contact_angle_deg": drive_per_degree,
        "rpm_per_contact_angle_deg": rpm_per_degree,
        "drive_per_gap_ratio": drive_per_gap_ratio,
        "rpm_per_gap_ratio": rpm_per_gap_ratio,
        "rpm_per_gap_mm": rpm_per_gap_ratio * 1.0e-3 / DEFAULT_CORE_RADIUS,
    }


def machine_summary() -> dict[str, object]:
    """Return the frozen default regression as a machine-readable object."""

    problem = Problem(
        DEFAULT_BO,
        DEFAULT_RADIUS_RATIO,
        tolerance=1.0e-8,
    )
    linear_drive, linear_touch = touch_threshold(
        problem, DEFAULT_GAP_RATIO, nonlinear=False
    )
    nonlinear_drive, nonlinear_touch = touch_threshold(
        problem, DEFAULT_GAP_RATIO, nonlinear=True
    )
    touch_metrics = metrics(nonlinear_touch)
    direct_drive, direct_residual, direct_shape_difference, direct_nodes = (
        direct_radial_q_contact_check(
            problem, DEFAULT_GAP_RATIO, nonlinear_touch
        )
    )

    rpm = 900.0
    drive_900 = drive_from_rpm(rpm)
    linear_900 = advance_to(problem, drive_900, nonlinear=False)
    nonlinear_900 = advance_to(problem, drive_900, nonlinear=True)
    linear_900_metrics = metrics(linear_900)
    nonlinear_900_metrics = metrics(nonlinear_900)

    pinned = Problem(DEFAULT_BO, DEFAULT_RADIUS_RATIO, boundary="pinned")
    graph_drive, graph_depth, graph_solutions = pinned_graph_limit(pinned)
    endpoint_fits = pinned_endpoint_tail_fits(graph_solutions)

    return {
        "schema": "iypt-air-vortex/nonlinear-surface-audit/v1",
        "constants_si": {
            "density_kg_m3": DEFAULT_DENSITY,
            "gravity_m_s2": DEFAULT_GRAVITY,
            "surface_tension_N_m": DEFAULT_SURFACE_TENSION,
            "core_radius_m": DEFAULT_CORE_RADIUS,
            "vessel_radius_m": DEFAULT_VESSEL_RADIUS,
            "liquid_depth_m": DEFAULT_LIQUID_DEPTH,
            "magnet_bottom_gap_m": DEFAULT_MAGNET_BOTTOM_GAP,
            "magnet_radius_m": DEFAULT_MAGNET_RADIUS,
            "C_per_omega_m2": DEFAULT_C_PER_OMEGA,
        },
        "dimensionless": {
            "bond": DEFAULT_BO,
            "radius_ratio": DEFAULT_RADIUS_RATIO,
            "magnet_clearance_ratio": DEFAULT_GAP_RATIO,
        },
        "numerics": {
            "angle_state_initial_mesh_points": problem.mesh_points,
            "angle_state_tolerance": problem.tolerance,
            "angle_state_axis_fraction": problem.axis_fraction,
            "touch_bisections": 36,
            "direct_w_state_tolerance": 1.0e-8,
            "pinned_continuation_tolerance": pinned.tolerance,
            "pinned_last_prescribed_q_wall": -0.9998,
        },
        "capillary_singular_outer": {
            "status": "sigma->0 finite-vessel outer solution, not finite-sigma exact curvature",
            "mean_potential": mean_potential_closed_form(DEFAULT_RADIUS_RATIO),
            "mean_potential_over_ln2": (
                mean_potential_closed_form(DEFAULT_RADIUS_RATIO)
                / math.log(2.0)
            ),
            "first_contact_rpm": capillary_singular_outer_rpm(
                DEFAULT_RADIUS_RATIO
            ),
        },
        "contact_threshold_90deg": {
            "linear_drive": linear_drive,
            "nonlinear_drive": nonlinear_drive,
            "linear_rpm": rpm_from_drive(linear_drive),
            "nonlinear_rpm": rpm_from_drive(nonlinear_drive),
            "nonlinear_to_linear_rpm_ratio": math.sqrt(
                nonlinear_drive / linear_drive
            ),
            "nonlinear_max_abs_slope": touch_metrics.maximum_abs_slope,
            "nonlinear_max_abs_q": touch_metrics.maximum_abs_q,
            "linear_curvature_operator_l2_error_on_nonlinear_shape": (
                touch_metrics.curvature_linearisation_l2_error
            ),
        },
        "contact_threshold_cross_formulation": {
            "state": "w=x*q with W and Lambda solved simultaneously",
            "drive": direct_drive,
            "drive_difference_from_angle_continuation": (
                direct_drive - nonlinear_drive
            ),
            "maximum_collocation_rms_residual": direct_residual,
            "maximum_height_difference_dimensionless": direct_shape_difference,
            "collocation_nodes": direct_nodes,
        },
        "local_contact_threshold_sensitivities": local_contact_sensitivities(),
        "at_900_rpm": {
            "drive": drive_900,
            "linear_axis_depth_mm": (
                -linear_900_metrics.minimum * DEFAULT_CORE_RADIUS * 1.0e3
            ),
            "nonlinear_axis_depth_mm": (
                -nonlinear_900_metrics.minimum * DEFAULT_CORE_RADIUS * 1.0e3
            ),
            "nonlinear_max_abs_slope": (
                nonlinear_900_metrics.maximum_abs_slope
            ),
            "nonlinear_max_abs_q": nonlinear_900_metrics.maximum_abs_q,
            "linear_curvature_operator_l2_error_on_nonlinear_shape": (
                nonlinear_900_metrics.curvature_linearisation_l2_error
            ),
            "linear_depth_underestimate_fraction": (
                (-nonlinear_900_metrics.minimum + linear_900_metrics.minimum)
                / (-nonlinear_900_metrics.minimum)
            ),
        },
        "pinned_y_wall_zero_graph_endpoint": {
            "method": "three-point quadratic extrapolation in sqrt(1+q_wall)",
            "drive": graph_drive,
            "axis_depth_ratio": graph_depth,
            "rpm": rpm_from_drive(graph_drive),
            "tail_fit_estimates": [
                {
                    "points": count,
                    "drive": drive_estimate,
                    "axis_depth_ratio": depth_estimate,
                }
                for count, drive_estimate, depth_estimate in endpoint_fits
            ],
            "status": "single-valued graph endpoint; not a proved flow instability",
        },
    }


def print_drive_table() -> None:
    problem = Problem(DEFAULT_BO, DEFAULT_RADIUS_RATIO)
    print("\nTABLE A -- exact-curvature versus linear-curvature drive sequence")
    print("| Bo | R/c | W | D_lin/c | D_nl/c | depth_nl/c | "
          "max|slope| | center error (%) | weighted L2 error (%) | "
          "curvature-op L2 error (%) |")
    print("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    linear_seed = static_solution(problem, nonlinear=False)
    nonlinear_seed = static_solution(problem, nonlinear=True)
    for drive in (1.0, 10.0, 40.0, 80.0, 130.0):
        linear_seed = advance_to(
            problem, drive, nonlinear=False, seed=linear_seed
        )
        nonlinear_seed = advance_to(
            problem, drive, nonlinear=True, seed=nonlinear_seed
        )
        linear_metrics = metrics(linear_seed)
        nonlinear_metrics = metrics(nonlinear_seed)
        d_linear = -linear_metrics.minimum
        d_nonlinear = -nonlinear_metrics.minimum
        center_error = 100.0 * abs(d_linear - d_nonlinear) / d_nonlinear
        shape_error = 100.0 * weighted_shape_error(nonlinear_seed, linear_seed)
        require(
            nonlinear_metrics.curvature_linearisation_l2_error is not None,
            "nonlinear curvature diagnostic was not computed",
        )
        curvature_error = (
            100.0 * nonlinear_metrics.curvature_linearisation_l2_error
        )
        print(
            f"| {problem.bond:.2f} | {problem.radius_ratio:.1f} | {drive:.1f} "
            f"| {d_linear:.6f} | {d_nonlinear:.6f} "
            f"| {nonlinear_metrics.depth:.6f} "
            f"| {nonlinear_metrics.maximum_abs_slope:.6f} "
            f"| {center_error:.4f} | {shape_error:.4f} "
            f"| {curvature_error:.4f} |"
        )
        require(abs(nonlinear_metrics.volume_mean) < 3.0e-9,
                "nonlinear volume constraint failed")
        require(nonlinear_metrics.rms_residual < 4.0 * problem.tolerance,
                "nonlinear collocation residual failed")
        require(
            nonlinear_metrics.pressure_invariant_error is not None
            and abs(nonlinear_metrics.pressure_invariant_error) < 2.0e-7,
            "integrated-pressure invariant failed",
        )


def print_parameter_table() -> None:
    print("\nTABLE B -- Bond number, confinement, and drive sensitivity")
    print("| Bo | R/c | W | D_lin/c | D_nl/c | max|slope| | "
          "linear center error (%) |")
    print("|---:|---:|---:|---:|---:|---:|---:|")
    cases = (
        (0.50, 4.5, 4.0),
        (2.00, 4.5, 8.0),
        (DEFAULT_BO, 2.0, 40.0),
        (DEFAULT_BO, DEFAULT_RADIUS_RATIO, 40.0),
        (DEFAULT_BO, 8.0, 40.0),
        (100.0, 4.5, 100.0),
    )
    for bond, radius_ratio, drive in cases:
        problem = Problem(bond, radius_ratio)
        linear = advance_to(problem, drive, nonlinear=False)
        nonlinear = advance_to(problem, drive, nonlinear=True)
        ml = metrics(linear)
        mn = metrics(nonlinear)
        d_linear = -ml.minimum
        d_nonlinear = -mn.minimum
        error = 100.0 * abs(d_linear - d_nonlinear) / d_nonlinear
        print(
            f"| {bond:.2f} | {radius_ratio:.1f} | {drive:.1f} "
            f"| {d_linear:.6f} | {d_nonlinear:.6f} "
            f"| {mn.maximum_abs_slope:.6f} | {error:.4f} |"
        )
        require(abs(mn.volume_mean) < 3.0e-9,
                "parameter-table volume constraint failed")
        require(mn.rms_residual < 4.0 * problem.tolerance,
                "parameter-table collocation residual failed")
        require(
            mn.pressure_invariant_error is not None
            and abs(mn.pressure_invariant_error) < 2.0e-7,
            "parameter-table integrated-pressure invariant failed",
        )


def print_threshold_table() -> None:
    print(
        f"\nTABLE C -- geometric-contact threshold "
        f"(gap S/c={DEFAULT_GAP_RATIO:.6g})"
    )
    print("| boundary | Bo | R/c | Wc_lin | Wc_nl | "
          "Omega_nl/Omega_lin | max|slope| at contact | q_max |")
    print("|:--|---:|---:|---:|---:|---:|---:|---:|")
    cases = (
        ("angle 90 deg", Problem(DEFAULT_BO, DEFAULT_RADIUS_RATIO)),
        ("angle 75 deg", Problem(DEFAULT_BO, DEFAULT_RADIUS_RATIO,
                                  contact_angle_deg=75.0)),
        ("angle 105 deg", Problem(DEFAULT_BO, DEFAULT_RADIUS_RATIO,
                                   contact_angle_deg=105.0)),
    )
    for label, problem in cases:
        linear_drive, _ = touch_threshold(
            problem, DEFAULT_GAP_RATIO, nonlinear=False
        )
        nonlinear_drive, nonlinear = touch_threshold(
            problem, DEFAULT_GAP_RATIO, nonlinear=True
        )
        mn = metrics(nonlinear)
        omega_ratio = (
            math.sqrt(nonlinear_drive / linear_drive)
            if linear_drive > 0.0 else float("nan")
        )
        print(
            f"| {label} | {problem.bond:.2f} | {problem.radius_ratio:.1f} "
            f"| {linear_drive:.6f} | {nonlinear_drive:.6f} "
            f"| {omega_ratio:.6f} | {mn.maximum_abs_slope:.6f} "
            f"| {mn.maximum_abs_q:.6f} |"
        )
        require(abs(mn.volume_mean) < 3.0e-9,
                "threshold-table volume constraint failed")
        require(mn.rms_residual < 4.0 * problem.tolerance,
                "threshold-table collocation residual failed")
        require(
            mn.pressure_invariant_error is not None
            and abs(mn.pressure_invariant_error) < 2.0e-7,
            "threshold-table integrated-pressure invariant failed",
        )
    pinned = Problem(DEFAULT_BO, DEFAULT_RADIUS_RATIO, boundary="pinned")
    linear_drive, _ = touch_threshold(
        pinned, DEFAULT_GAP_RATIO, nonlinear=False
    )
    graph_drive, graph_depth, _ = pinned_graph_limit(pinned)
    print(
        f"| pinned y(R)=0 | {pinned.bond:.2f} | {pinned.radius_ratio:.1f} "
        f"| {linear_drive:.6f} | not reached: graph ends at W~{graph_drive:.6f} "
        f"| n/a | graph-limit D/c~{graph_depth:.6f} | 1 (limit) |"
    )
    sensitivity = local_contact_sensitivities()
    require(sensitivity["drive_per_gap_ratio"] > 0.0,
            "contact threshold did not increase with static gap")
    print("\nLOCAL EXACT-CURVATURE CONTACT SENSITIVITY")
    print(
        "centered at theta=90 deg, S/c=5.25: "
        f"dWc/dtheta={sensitivity['drive_per_contact_angle_deg']:.8f} per deg, "
        f"d(rpm)/dtheta={sensitivity['rpm_per_contact_angle_deg']:.8f} rpm/deg"
    )
    print(
        f"dWc/d(S/c)={sensitivity['drive_per_gap_ratio']:.8f}, "
        f"d(rpm)/dS={sensitivity['rpm_per_gap_mm']:.8f} rpm/mm"
    )


def print_domain_table() -> None:
    problem = Problem(DEFAULT_BO, DEFAULT_RADIUS_RATIO)
    print(
        f"\nTABLE D -- operational slope/graph boundaries, "
        f"Bo={DEFAULT_BO:.8f}, R/c={DEFAULT_RADIUS_RATIO:g}"
    )
    print("| criterion | W | max|slope| | q_max | interpretation |")
    print("|:--|---:|---:|---:|:--|")
    for target, interpretation in (
        (0.3, "small-slope warning"),
        (1.0, "45 deg local tangent"),
        (3.0, "strongly nonlinear graph"),
        (7.017923929582521, "q=0.99 numerical graph guard"),
    ):
        drive, solution = slope_boundary(problem, target)
        m = metrics(solution)
        print(
            f"| max|dy/dx|={target:.6g} | {drive:.6f} "
            f"| {m.maximum_abs_slope:.6f} | {m.maximum_abs_q:.6f} "
            f"| {interpretation} |"
        )
    pinned = Problem(DEFAULT_BO, DEFAULT_RADIUS_RATIO, boundary="pinned")
    graph_drive, graph_depth, branches = pinned_graph_limit(pinned)
    print("\nPINNED-WALL GRAPH ENDPOINT")
    print("| q_wall | W | D/c | wall slope | nodes |")
    print("|---:|---:|---:|---:|---:|")
    for solution in branches[-5:]:
        wall_state = solution.raw.sol(problem.radius_ratio)
        wall_slope = math.sinh(float(wall_state[1]))
        print(
            f"| {math.tanh(float(wall_state[1])):.7f} | {solution.drive:.6f} "
            f"| {-metrics(solution).minimum:.6f} | {wall_slope:.6f} "
            f"| {solution.raw.x.size:d} |"
        )
    print(
        f"sqrt(1+q_wall) endpoint fit: W_graph={graph_drive:.6f}, "
        f"D_graph/c={graph_depth:.6f}"
    )
    fits = pinned_endpoint_tail_fits(branches)
    drive_spread = max(item[1] for item in fits) - min(item[1] for item in fits)
    depth_spread = max(item[2] for item in fits) - min(item[2] for item in fits)
    require(drive_spread < 3.0e-5,
            "pinned endpoint is unstable to the chosen tail-fit window")
    require(depth_spread < 2.0e-6,
            "pinned endpoint depth is unstable to the tail-fit window")
    print("tail-window sensitivity (same quadratic endpoint law):")
    for count, drive_estimate, depth_estimate in fits:
        print(
            f"  last {count:d} points -> W*={drive_estimate:.9f}, "
            f"D*/c={depth_estimate:.9f}"
        )


def refinement_and_asymptotic_checks() -> None:
    print(
        f"\nTABLE E -- numerical refinement at Bo={DEFAULT_BO:.8f}, "
        f"R/c={DEFAULT_RADIUS_RATIO:g}, W=80"
    )
    print("| mesh guess | tol | axis eps/L | D/c | max|slope| | "
          "curvature-op L2 error | max RMS residual |")
    print("|---:|---:|---:|---:|---:|---:|---:|")
    values: list[tuple[float, float, float]] = []
    for points, tolerance, axis_fraction in (
        (201, 1.0e-6, 1.0e-6),
        (401, 3.0e-7, 1.0e-7),
        (801, 8.0e-8, 1.0e-8),
    ):
        problem = Problem(
            DEFAULT_BO,
            DEFAULT_RADIUS_RATIO,
            mesh_points=points,
            tolerance=tolerance,
            axis_fraction=axis_fraction,
        )
        solution = advance_to(problem, 80.0, nonlinear=True)
        m = metrics(solution)
        require(m.curvature_linearisation_l2_error is not None,
                "refinement curvature diagnostic was not computed")
        values.append(
            (
                -m.minimum,
                m.maximum_abs_slope,
                m.curvature_linearisation_l2_error,
            )
        )
        print(
            f"| {points:d} | {tolerance:.1e} | {axis_fraction:.1e} "
            f"| {-m.minimum:.9f} | {m.maximum_abs_slope:.9f} "
            f"| {m.curvature_linearisation_l2_error:.9f} "
            f"| {m.rms_residual:.3e} |"
        )
    require(abs(values[-1][0] - values[-2][0]) < 2.0e-7,
            "depth did not converge under refinement")
    require(abs(values[-1][1] - values[-2][1]) < 3.0e-6,
            "maximum slope did not converge under refinement")
    require(abs(values[-1][2] - values[-2][2]) < 1.0e-7,
            "curvature diagnostic did not converge under refinement")

    # Exact curvature is odd in slope, so the first shape correction is cubic
    # in W.  Consequently (D_nl-D_lin)/W**3 tends to a constant as W -> 0.
    problem = Problem(DEFAULT_BO, DEFAULT_RADIUS_RATIO, tolerance=8.0e-9)
    ratios: list[float] = []
    for drive in (0.4, 0.2, 0.1):
        exact = advance_to(problem, drive, nonlinear=True)
        linear = advance_to(problem, drive, nonlinear=False)
        correction = -metrics(exact).minimum + metrics(linear).minimum
        ratios.append(correction / drive**3)
    relative_spread = (max(ratios) - min(ratios)) / abs(ratios[-1])
    require(relative_spread < 2.0e-3,
            "weakly nonlinear W^3 asymptotic was not observed")
    print("\nWEAK-NONLINEAR CHECK")
    print("(D_nl-D_lin)/W^3 = " + ", ".join(f"{v:.10e}" for v in ratios))
    print(f"relative spread = {relative_spread:.3e}")


def cross_formulation_check() -> None:
    """Certify the threshold with a second state representation and root path."""

    problem = Problem(
        DEFAULT_BO,
        DEFAULT_RADIUS_RATIO,
        mesh_points=401,
        tolerance=1.0e-8,
    )
    continued_drive, continued = touch_threshold(
        problem, DEFAULT_GAP_RATIO, nonlinear=True
    )
    direct_drive, residual, shape_difference, nodes = (
        direct_radial_q_contact_check(
            problem, DEFAULT_GAP_RATIO, continued
        )
    )
    require(abs(direct_drive - continued_drive) < 2.0e-8,
            "w=x*q contact root disagrees with angle-state continuation")
    require(residual < 1.1e-8,
            "w=x*q cross-formulation residual is too large")
    require(shape_difference < 2.0e-8,
            "w=x*q cross-formulation shape disagrees with angle-state solve")
    print("\nDIRECT FIXED-CONTACT CROSS-FORMULATION")
    print("| formulation | Wc | delta W | max RMS residual | max |delta y| | nodes |")
    print("|:--|---:|---:|---:|---:|---:|")
    print(
        f"| w=x*q; W,Lambda unknown | {direct_drive:.9f} "
        f"| {direct_drive - continued_drive:+.3e} | {residual:.3e} "
        f"| {shape_difference:.3e} | {nodes:d} |"
    )


def _closed_form_checks() -> None:
    # Closed-form forcing checks independent of the collocation solves.
    require(abs(float(burgers_pressure_potential(0.0))) < 1.0e-15,
            "F(0) != 0")
    require(
        abs(float(burgers_pressure_potential(100.0)) - math.log(2.0)) < 5.1e-5,
        "large-radius Burgers potential did not approach ln(2)",
    )
    for coordinate in (1.0e-3, 0.1, 1.0, DEFAULT_RADIUS_RATIO):
        quadrature = quad(
            lambda value: (
                0.0
                if value == 0.0
                else math.expm1(-value * value) ** 2 / value**3
            ),
            0.0,
            coordinate,
            epsabs=2.0e-14,
            epsrel=2.0e-14,
            limit=200,
        )[0]
        require(
            abs(float(burgers_pressure_potential(coordinate)) - quadrature)
            < 8.0e-13,
            f"Burgers pressure potential disagreed with quadrature at x={coordinate}",
        )
    require(
        abs(
            mean_potential_closed_form(DEFAULT_RADIUS_RATIO)
            - _mean_potential(DEFAULT_RADIUS_RATIO)
        )
        < 2.0e-11,
        "finite-vessel analytic mean potential disagreed with quadrature",
    )


def _default_config_alignment_checks() -> None:
    """Fail if the frozen audit constants drift from the shipped JSON config."""

    project_root = Path(__file__).resolve().parents[2]
    config_path = project_root / "configs" / "forward_default.json"
    if not config_path.is_file():
        raise AssertionError(f"default config not found at {config_path}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    expected = {
        "density": float(config["fluid"]["liquid_density_kg_m3"]),
        "gravity": float(config["fluid"]["gravity_m_s2"]),
        "surface_tension": float(config["fluid"]["surface_tension_n_m"]),
        "core_radius": float(config["forward"]["effective_core_radius_m"]),
        "vessel_radius": float(config["container"]["radius_m"]),
        "liquid_depth": float(config["container"]["liquid_depth_m"]),
        "magnet_bottom_gap": float(config["stir_bar"]["bottom_clearance_m"]),
        "magnet_radius": float(config["stir_bar"]["radius_m"]),
        "circulation_coupling": float(
            config["forward"]["circulation_coupling_m2"]
        ),
    }
    frozen = {
        "density": DEFAULT_DENSITY,
        "gravity": DEFAULT_GRAVITY,
        "surface_tension": DEFAULT_SURFACE_TENSION,
        "core_radius": DEFAULT_CORE_RADIUS,
        "vessel_radius": DEFAULT_VESSEL_RADIUS,
        "liquid_depth": DEFAULT_LIQUID_DEPTH,
        "magnet_bottom_gap": DEFAULT_MAGNET_BOTTOM_GAP,
        "magnet_radius": DEFAULT_MAGNET_RADIUS,
        "circulation_coupling": DEFAULT_C_PER_OMEGA,
    }
    require(expected == frozen,
            f"audit constants drifted from forward_default.json: {expected!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary-json",
        action="store_true",
        help="emit only the frozen default regression as JSON",
    )
    args = parser.parse_args()
    _closed_form_checks()
    _default_config_alignment_checks()
    if args.summary_json:
        print(
            json.dumps(
                machine_summary(),
                allow_nan=False,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return

    print("NONLINEAR YOUNG-LAPLACE AUDIT")
    print("definitions: Bo=rho*g*c^2/sigma, L=R/c, W=rho*C^2/(sigma*c)")
    mean_potential = mean_potential_closed_form(DEFAULT_RADIUS_RATIO)
    print(
        "sigma->0 finite-vessel outer reference: "
        f"M(L)={mean_potential:.10f}, "
        f"contact={capillary_singular_outer_rpm(DEFAULT_RADIUS_RATIO):.6f} rpm "
        "(not a finite-sigma exact-curvature solve)"
    )
    print_drive_table()
    print_parameter_table()
    print_threshold_table()
    print_domain_table()
    refinement_and_asymptotic_checks()
    cross_formulation_check()
    print("\nNONLINEAR YOUNG-LAPLACE AUDIT: PASS")


if __name__ == "__main__":
    main()
