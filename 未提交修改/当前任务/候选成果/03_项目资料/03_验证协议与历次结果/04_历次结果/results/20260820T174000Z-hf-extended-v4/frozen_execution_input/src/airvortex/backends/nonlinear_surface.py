r"""Exact-curvature, axisymmetric Young--Laplace free-surface solver.

The reduced forward model prescribes the Burgers/Oseen swirl

.. math::

   u_\theta(r)=\frac{C}{r}\left(1-e^{-r^2/c^2}\right),

and closes the pre-contact free surface with radial momentum balance and the
*nonlinear* mean curvature of a graph.  Writing ``z=H+eta(r)`` and taking the
normal from liquid to gas gives

.. math::

   g\eta+\frac{\sigma}{\rho}\kappa=\Phi+\lambda,
   \qquad
   \kappa=-\frac1r\frac{d}{dr}
       \left(\frac{r\eta_r}{\sqrt{1+\eta_r^2}}\right).

This module solves that boundary-value problem with a regularised axial
expansion, a 90 degree free contact line at the vertical wall, and an explicit
volume-integral state.  Continuation in the non-negative forcing ``C**2`` is
mandatory: a failed or near-vertical graph raises :class:`NonlinearSurfaceError`.
There is deliberately no silent fallback to the linear-curvature baseline.

The single-valued graph is a pre-contact closure.  It does not represent the
topology change, wetting dynamics, gas-core breakup, or bubble entrainment that
can follow contact with the stir bar.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Final

import numpy as np
from numpy.typing import NDArray
from scipy.integrate import solve_bvp
from scipy.special import exp1

from ..core import SimulationConfig


FloatArray = NDArray[np.float64]

NONLINEAR_SURFACE_MODEL_ID: Final = "axisymmetric-exact-curvature-yl-v1"


class NonlinearSurfaceError(RuntimeError):
    """The exact-curvature graph could not be certified at the requested load."""


class NonlinearSurfaceDomainError(NonlinearSurfaceError):
    """The continuation reached the single-valued-graph domain boundary."""


@dataclass(frozen=True, slots=True)
class NonlinearSurfaceSolution:
    """One certified quasi-static surface at a prescribed ``C**2``.

    ``elevation_m`` is relative to the configured mean depth and has zero
    discrete radial-volume mean.  ``normalised_slope`` is
    ``q=eta_r/sqrt(1+eta_r**2)`` and therefore remains bounded as the graph
    approaches a vertical tangent.

    The private-looking collocation arrays are intentionally retained: they
    are the warm-start state used by deterministic natural-parameter
    continuation.  They are immutable by convention and never exposed as
    physical output fields by the backend.
    """

    radius_m: FloatArray
    elevation_m: FloatArray
    normalised_slope: FloatArray
    slope: FloatArray
    curvature_m_inv: FloatArray
    circulation_sq_m4_s2: float
    dimensionless_forcing: float
    dimensionless_pressure: float
    maximum_absolute_slope: float
    maximum_absolute_normalised_slope: float
    volume_residual_m3: float
    continuous_volume_residual_dimensionless: float
    maximum_collocation_rms_residual: float
    boundary_residual_norm: float
    collocation_nodes: int
    nonlinear_iterations: int
    continuation_substeps: int
    solution_regime: str
    collocation_x: FloatArray
    collocation_state: FloatArray
    collocation_parameter: FloatArray


@dataclass(frozen=True, slots=True)
class NonlinearCriticalResult:
    """Certified first-contact root on the exact-curvature graph branch."""

    actual_rpm: float
    circulation_sq_m4_s2: float
    dimensionless_forcing: float
    surface: NonlinearSurfaceSolution
    bracket_dimensionless_forcing: tuple[float, float]
    root_iterations: int
    height_residual_m: float
    solution_regime: str


def burgers_dimensionless_potential(x: FloatArray | float) -> FloatArray:
    """Return ``F(x)=integral_0^x (1-exp(-s^2))^2/s^3 ds``.

    The closed form

    ``ln(2)-E1(x^2)+E1(2*x^2)-(1-exp(-x^2))^2/(2*x^2)``

    loses many digits near the axis.  A factorial convolution supplies the
    analytic Taylor series there.  The result is dimensionless and satisfies
    ``F(0)=0`` and ``F(infinity)=ln(2)``.
    """

    values = np.asarray(x, dtype=np.float64)
    if np.any(~np.isfinite(values)) or np.any(values < 0.0):
        raise ValueError("x must contain finite non-negative radii")
    flat = values.reshape(-1)
    squared = np.square(flat)
    result = np.empty_like(flat)
    small = squared < 2.0e-2

    if np.any(small):
        t = squared[small]
        series = np.zeros_like(t)
        # If (1-exp(-t))^2=sum_{n>=2} a_n t^n, then
        # F=1/2 sum a_n*t^(n-1)/(n-1).  Fourteen terms give ample margin at
        # t=0.02 (the first omitted contribution is below double precision).
        for order in range(2, 16):
            coefficient = 0.0
            for left_order in range(1, order):
                right_order = order - left_order
                coefficient += (
                    (-1.0) ** (left_order + 1)
                    / math.factorial(left_order)
                    * (-1.0) ** (right_order + 1)
                    / math.factorial(right_order)
                )
            series += (
                0.5
                * coefficient
                * np.power(t, order - 1)
                / float(order - 1)
            )
        result[small] = series

    regular = ~small
    if np.any(regular):
        t = squared[regular]
        one_minus_exp = -np.expm1(-t)
        result[regular] = (
            math.log(2.0)
            - exp1(t)
            + exp1(2.0 * t)
            - np.square(one_minus_exp) / (2.0 * t)
        )
    return result.reshape(values.shape)


def _radial_volume_weights(radius_m: FloatArray) -> FloatArray:
    spacing = np.diff(radius_m)
    if (
        radius_m.ndim != 1
        or radius_m.size < 3
        or radius_m[0] != 0.0
        or np.any(spacing <= 0.0)
        or not np.allclose(spacing, spacing[0], rtol=1.0e-12, atol=0.0)
    ):
        raise ValueError("radius_m must be a uniform grid increasing from zero")
    weights = radius_m * float(spacing[0])
    weights[0] *= 0.5
    weights[-1] *= 0.5
    weights *= 2.0 / float(radius_m[-1]) ** 2
    weights /= float(np.sum(weights))
    return weights


def _cumulative_trapezoid(values: FloatArray, coordinate: FloatArray) -> FloatArray:
    result = np.zeros_like(values)
    result[1:] = np.cumsum(
        0.5 * (values[:-1] + values[1:]) * np.diff(coordinate)
    )
    return result


class NonlinearSurfaceSolver:
    """Continuation solver for the 90 degree-wall exact-curvature branch."""

    # A four-unit continuation jump remains inside the Newton basin for both
    # the default case and the more strongly curved low-Bond-number cases
    # exercised by the verification suite.
    _MAX_FORCING_STEP: Final = 4.0

    def __init__(
        self,
        config: SimulationConfig,
        *,
        linear_radius_m: FloatArray | None = None,
        linear_surface_per_circulation_sq: FloatArray | None = None,
    ) -> None:
        self.config = config
        self.core_radius_m = float(config.forward.effective_core_radius_m)
        self.vessel_radius_m = float(config.container.radius_m)
        self.radius_ratio = self.vessel_radius_m / self.core_radius_m
        self.density = float(config.fluid.liquid_density_kg_m3)
        self.surface_tension = float(config.fluid.surface_tension_n_m)
        self.gravity = float(config.fluid.gravity_m_s2)
        self.bond_number = (
            self.density
            * self.gravity
            * self.core_radius_m**2
            / self.surface_tension
        )
        self.output_radius_m = np.linspace(
            0.0,
            self.vessel_radius_m,
            config.forward.surface_radial_points,
            dtype=np.float64,
        )
        self.output_x = self.output_radius_m / self.core_radius_m
        self.volume_weights = _radial_volume_weights(self.output_radius_m)
        first_positive_x = float(self.output_x[1])
        self.axis_epsilon = min(
            self.radius_ratio * 1.0e-6,
            first_positive_x * 1.0e-3,
        )
        self.base_mesh = np.concatenate(
            (
                np.array([self.axis_epsilon], dtype=np.float64),
                self.output_x[1:].copy(),
            )
        )
        self.relative_tolerance = float(config.forward.relative_tolerance)
        self.absolute_tolerance_dimensionless = (
            float(config.forward.absolute_tolerance) / self.core_radius_m
        )
        self.collocation_tolerance = max(
            self.relative_tolerance,
            self.absolute_tolerance_dimensionless,
            5.0e-11,
        )
        self.maximum_nodes = max(12_000, 48 * self.base_mesh.size)
        spacing_m = float(self.output_radius_m[1] - self.output_radius_m[0])
        capillary_length_m = math.sqrt(
            self.surface_tension / (self.density * self.gravity)
        )
        # If the capillary wall layer is far below the declared radial grid,
        # resolving the finite-sigma Neumann layer would be fictitious.  The
        # explicitly labelled sigma->0 outer solution is then the certified
        # singular limit, not a fallback to linear curvature.
        self.uses_capillary_singular_limit = capillary_length_m < 0.05 * spacing_m

        self._linear_radius_m: FloatArray | None = None
        self._linear_unit_surface: FloatArray | None = None
        if (linear_radius_m is None) != (linear_surface_per_circulation_sq is None):
            raise ValueError(
                "linear radius and unit surface must either both be provided or both omitted"
            )
        if linear_radius_m is not None and linear_surface_per_circulation_sq is not None:
            linear_radius = np.asarray(linear_radius_m, dtype=np.float64)
            linear_surface = np.asarray(
                linear_surface_per_circulation_sq, dtype=np.float64
            )
            if (
                linear_radius.shape != linear_surface.shape
                or linear_radius.ndim != 1
                or linear_radius.size < 3
                or linear_radius[0] != 0.0
                or not math.isclose(
                    float(linear_radius[-1]),
                    self.vessel_radius_m,
                    rel_tol=1.0e-12,
                    abs_tol=1.0e-15,
                )
                or np.any(np.diff(linear_radius) <= 0.0)
                or not np.isfinite(linear_surface).all()
            ):
                raise ValueError("invalid linear surface baseline")
            self._linear_radius_m = linear_radius.copy()
            self._linear_unit_surface = linear_surface.copy()

    def forcing_from_circulation_sq(self, circulation_sq_m4_s2: float) -> float:
        if (
            not math.isfinite(float(circulation_sq_m4_s2))
            or circulation_sq_m4_s2 < 0.0
        ):
            raise ValueError("circulation_sq_m4_s2 must be finite and non-negative")
        return (
            self.density
            * float(circulation_sq_m4_s2)
            / (self.surface_tension * self.core_radius_m)
        )

    def circulation_sq_from_forcing(self, forcing: float) -> float:
        if not math.isfinite(float(forcing)) or forcing < 0.0:
            raise ValueError("forcing must be finite and non-negative")
        return (
            float(forcing)
            * self.surface_tension
            * self.core_radius_m
            / self.density
        )

    def _zero_solution(self) -> NonlinearSurfaceSolution:
        x = self.base_mesh.copy()
        state = np.zeros((3, x.size), dtype=np.float64)
        return NonlinearSurfaceSolution(
            radius_m=self.output_radius_m.copy(),
            elevation_m=np.zeros_like(self.output_radius_m),
            normalised_slope=np.zeros_like(self.output_radius_m),
            slope=np.zeros_like(self.output_radius_m),
            curvature_m_inv=np.zeros_like(self.output_radius_m),
            circulation_sq_m4_s2=0.0,
            dimensionless_forcing=0.0,
            dimensionless_pressure=0.0,
            maximum_absolute_slope=0.0,
            maximum_absolute_normalised_slope=0.0,
            volume_residual_m3=0.0,
            continuous_volume_residual_dimensionless=0.0,
            maximum_collocation_rms_residual=0.0,
            boundary_residual_norm=0.0,
            collocation_nodes=int(x.size),
            nonlinear_iterations=0,
            continuation_substeps=0,
            solution_regime="EXACT_NONLINEAR_CURVATURE",
            collocation_x=x,
            collocation_state=state,
            collocation_parameter=np.zeros(1, dtype=np.float64),
        )

    def _linear_initial_guess(self, forcing: float) -> tuple[FloatArray, FloatArray]:
        mesh = self.base_mesh.copy()
        if self._linear_radius_m is None or self._linear_unit_surface is None:
            state = np.zeros((3, mesh.size), dtype=np.float64)
            pressure = np.array([-0.60 * forcing], dtype=np.float64)
            return state, pressure

        circulation_sq = self.circulation_sq_from_forcing(forcing)
        mesh_radius = mesh * self.core_radius_m
        elevation = circulation_sq * np.interp(
            mesh_radius, self._linear_radius_m, self._linear_unit_surface
        )
        dimensionless_elevation = elevation / self.core_radius_m
        slope = np.gradient(dimensionless_elevation, mesh, edge_order=2)
        slope[0] = 0.0
        slope[-1] = 0.0
        angle = np.arcsinh(slope)
        volume_state = _cumulative_trapezoid(
            mesh * dimensionless_elevation, mesh
        )
        # The exact pressure follows from the area integral when q=0 at both
        # ends.  Computing its coefficient once by high-order quadrature is
        # unnecessary: the BVP parameter will correct this close estimate.
        potential_mean = float(
            2.0
            / self.radius_ratio**2
            * np.trapz(
                mesh * burgers_dimensionless_potential(mesh), mesh
            )
        )
        pressure = np.array([-forcing * potential_mean], dtype=np.float64)
        state = np.vstack((dimensionless_elevation, angle, volume_state))
        return state, pressure

    def _capillary_singular_solution(
        self, forcing: float
    ) -> NonlinearSurfaceSolution:
        circulation_sq = self.circulation_sq_from_forcing(forcing)
        potential = burgers_dimensionless_potential(self.output_x)
        potential_mean = float(np.dot(self.volume_weights, potential))
        # Bo*y=W*(F-<F>) is the finite-vessel sigma->0 outer solution.
        dimensionless_elevation = (
            forcing / self.bond_number * (potential - potential_mean)
        )
        dimensionless_elevation -= float(
            np.dot(self.volume_weights, dimensionless_elevation)
        )
        x = self.output_x
        slope = np.zeros_like(x)
        positive = x > 0.0
        numerator = np.square(-np.expm1(-np.square(x[positive])))
        slope[positive] = (
            forcing
            / self.bond_number
            * numerator
            / np.power(x[positive], 3)
        )
        normalised_slope = slope / np.sqrt(1.0 + np.square(slope))
        elevation_m = self.core_radius_m * dimensionless_elevation
        volume_residual = (
            math.pi
            * self.vessel_radius_m**2
            * float(np.dot(self.volume_weights, elevation_m))
        )
        mesh_state = np.vstack(
            (
                dimensionless_elevation[1:],
                np.arcsinh(slope[1:]),
                _cumulative_trapezoid(
                    x[1:] * dimensionless_elevation[1:], x[1:]
                ),
            )
        )
        radial_q = normalised_slope * self.output_radius_m
        radial_q_derivative = np.gradient(
            radial_q, self.output_radius_m, edge_order=2
        )
        outer_curvature = np.empty_like(self.output_radius_m)
        outer_curvature[1:] = -radial_q_derivative[1:] / self.output_radius_m[1:]
        outer_curvature[0] = -2.0 * normalised_slope[1] / self.output_radius_m[1]
        return NonlinearSurfaceSolution(
            radius_m=self.output_radius_m.copy(),
            elevation_m=elevation_m,
            normalised_slope=normalised_slope,
            slope=slope,
            # The inviscid outer solution omits the unresolved O(l_c) wall
            # layer; its outer curvature is still finite and computable.
            curvature_m_inv=outer_curvature,
            circulation_sq_m4_s2=circulation_sq,
            dimensionless_forcing=forcing,
            dimensionless_pressure=-forcing * potential_mean,
            maximum_absolute_slope=float(np.max(np.abs(slope))),
            maximum_absolute_normalised_slope=float(
                np.max(np.abs(normalised_slope))
            ),
            volume_residual_m3=volume_residual,
            continuous_volume_residual_dimensionless=0.0,
            maximum_collocation_rms_residual=0.0,
            boundary_residual_norm=0.0,
            collocation_nodes=int(self.output_x.size),
            nonlinear_iterations=0,
            continuation_substeps=1,
            solution_regime="CAPILLARY_SINGULAR_LIMIT_OUTER",
            collocation_x=self.output_x[1:].copy(),
            collocation_state=mesh_state,
            collocation_parameter=np.array(
                [-forcing * potential_mean], dtype=np.float64
            ),
        )

    def _solve_single(
        self,
        forcing: float,
        seed: NonlinearSurfaceSolution | None,
    ) -> NonlinearSurfaceSolution:
        if self.uses_capillary_singular_limit:
            return self._capillary_singular_solution(forcing)

        if seed is None or seed.solution_regime != "EXACT_NONLINEAR_CURVATURE":
            initial_state, initial_parameter = self._linear_initial_guess(forcing)
            mesh = self.base_mesh.copy()
        else:
            mesh = seed.collocation_x.copy()
            initial_state = seed.collocation_state.copy()
            if seed.dimensionless_forcing > 0.0:
                ratio = forcing / seed.dimensionless_forcing
                # Scaling the elevation and pressure is a useful predictor;
                # asinh(slope) is scaled through slope, not as an angle.
                initial_state[0] *= ratio
                predicted_slope = np.sinh(
                    np.clip(initial_state[1], -20.0, 20.0)
                ) * ratio
                initial_state[1] = np.arcsinh(predicted_slope)
                initial_state[2] *= ratio
                initial_parameter = seed.collocation_parameter * ratio
            else:
                initial_state, initial_parameter = self._linear_initial_guess(forcing)
                mesh = self.base_mesh.copy()

        bond = self.bond_number
        axis_epsilon = self.axis_epsilon
        axis_potential = float(
            burgers_dimensionless_potential(
                np.array([axis_epsilon], dtype=np.float64)
            )[0]
        )

        def equations(
            coordinate: FloatArray,
            state: FloatArray,
            parameter: FloatArray,
        ) -> FloatArray:
            angle = np.clip(state[1], -20.0, 20.0)
            cosh_sq = np.square(np.cosh(angle))
            return np.vstack(
                (
                    np.sinh(angle),
                    cosh_sq
                    * (
                        bond * state[0]
                        - forcing * burgers_dimensionless_potential(coordinate)
                        - parameter[0]
                        - np.tanh(angle) / coordinate
                    ),
                    coordinate * state[0],
                )
            )

        def boundary_conditions(
            left: FloatArray,
            right: FloatArray,
            parameter: FloatArray,
        ) -> FloatArray:
            axial_source = (
                bond * left[0] - forcing * axis_potential - parameter[0]
            )
            return np.array(
                [
                    left[1] - 0.5 * axial_source * axis_epsilon,
                    left[2] - 0.5 * left[0] * axis_epsilon**2,
                    right[1],
                    right[2],
                ],
                dtype=np.float64,
            )

        result = solve_bvp(
            equations,
            boundary_conditions,
            mesh,
            initial_state,
            p=initial_parameter,
            tol=self.collocation_tolerance,
            bc_tol=self.collocation_tolerance,
            max_nodes=self.maximum_nodes,
            verbose=0,
        )
        if not result.success:
            raise NonlinearSurfaceError(
                "exact-curvature Young-Laplace solve failed closed at "
                f"W={forcing:.9g}: status={result.status}, {result.message}"
            )
        if not (
            np.isfinite(result.y).all()
            and np.isfinite(result.p).all()
            and np.isfinite(result.rms_residuals).all()
        ):
            raise NonlinearSurfaceError(
                f"exact-curvature solve produced non-finite state at W={forcing:.9g}"
            )

        evaluated = result.sol(self.output_x[1:])
        left_state = result.y[:, 0]
        axial_source = (
            bond * left_state[0]
            - forcing * axis_potential
            - float(result.p[0])
        )
        axial_elevation = (
            left_state[0] - 0.25 * axial_source * self.axis_epsilon**2
        )
        dimensionless_elevation = np.concatenate(
            (np.array([axial_elevation]), evaluated[0])
        )
        angle = np.concatenate((np.array([0.0]), evaluated[1]))
        normalised_slope = np.tanh(angle)
        slope = np.sinh(np.clip(angle, -20.0, 20.0))

        # Project away interpolation-level volume drift with a constant shift.
        # This is exactly a pressure-datum change and leaves both curvature and
        # the two Neumann conditions invariant.
        discrete_mean = float(np.dot(self.volume_weights, dimensionless_elevation))
        dimensionless_elevation -= discrete_mean
        pressure = float(result.p[0]) - bond * discrete_mean
        elevation_m = self.core_radius_m * dimensionless_elevation
        potential = burgers_dimensionless_potential(self.output_x)
        curvature = (
            forcing * potential + pressure - bond * dimensionless_elevation
        ) / self.core_radius_m
        continuous_volume_residual = float(result.y[2, -1])
        boundary_residual = float(
            np.linalg.norm(
                boundary_conditions(result.y[:, 0], result.y[:, -1], result.p),
                ord=np.inf,
            )
        )
        maximum_rms = float(np.max(result.rms_residuals))
        q_max = float(np.max(np.abs(normalised_slope)))
        q_margin = max(2.0e-8, 20.0 * self.collocation_tolerance)
        if q_max >= 1.0 - q_margin:
            raise NonlinearSurfaceDomainError(
                "exact-curvature continuation reached the single-valued-graph "
                f"limit at W={forcing:.9g}: max|q|={q_max:.9g}"
            )
        residual_gate = 1.25 * self.collocation_tolerance
        boundary_gate = 5.0 * self.collocation_tolerance
        if maximum_rms > residual_gate or boundary_residual > boundary_gate:
            raise NonlinearSurfaceError(
                "exact-curvature solution failed residual certification at "
                f"W={forcing:.9g}: rms={maximum_rms:.3e}, "
                f"bc={boundary_residual:.3e}, tol={self.collocation_tolerance:.3e}"
            )

        volume_residual = (
            math.pi
            * self.vessel_radius_m**2
            * float(np.dot(self.volume_weights, elevation_m))
        )
        return NonlinearSurfaceSolution(
            radius_m=self.output_radius_m.copy(),
            elevation_m=elevation_m,
            normalised_slope=normalised_slope,
            slope=slope,
            curvature_m_inv=curvature,
            circulation_sq_m4_s2=self.circulation_sq_from_forcing(forcing),
            dimensionless_forcing=forcing,
            dimensionless_pressure=pressure,
            maximum_absolute_slope=float(np.max(np.abs(slope))),
            maximum_absolute_normalised_slope=q_max,
            volume_residual_m3=volume_residual,
            continuous_volume_residual_dimensionless=continuous_volume_residual,
            maximum_collocation_rms_residual=maximum_rms,
            boundary_residual_norm=boundary_residual,
            collocation_nodes=int(result.x.size),
            nonlinear_iterations=int(result.niter),
            continuation_substeps=1,
            solution_regime="EXACT_NONLINEAR_CURVATURE",
            collocation_x=result.x.copy(),
            collocation_state=result.y.copy(),
            collocation_parameter=result.p.copy(),
        )

    def solve_forcing(
        self,
        forcing: float,
        *,
        initial: NonlinearSurfaceSolution | None = None,
    ) -> NonlinearSurfaceSolution:
        """Solve at dimensionless forcing ``W`` with guarded continuation."""

        if not math.isfinite(float(forcing)) or forcing < 0.0:
            raise ValueError("forcing must be finite and non-negative")
        forcing = float(forcing)
        zero_gate = max(1.0e-14, self.relative_tolerance * 1.0e-10)
        if forcing <= zero_gate:
            return self._zero_solution()
        if self.uses_capillary_singular_limit:
            return self._capillary_singular_solution(forcing)

        seed = initial
        start = 0.0 if seed is None else float(seed.dimensionless_forcing)
        delta = forcing - start
        substeps = max(1, int(math.ceil(abs(delta) / self._MAX_FORCING_STEP)))
        solution = seed
        for index in range(1, substeps + 1):
            intermediate = start + delta * index / substeps
            solution = self._solve_single(intermediate, solution)
        if solution is None:  # pragma: no cover - defensive invariant
            raise NonlinearSurfaceError("continuation produced no solution")
        return NonlinearSurfaceSolution(
            radius_m=solution.radius_m,
            elevation_m=solution.elevation_m,
            normalised_slope=solution.normalised_slope,
            slope=solution.slope,
            curvature_m_inv=solution.curvature_m_inv,
            circulation_sq_m4_s2=solution.circulation_sq_m4_s2,
            dimensionless_forcing=solution.dimensionless_forcing,
            dimensionless_pressure=solution.dimensionless_pressure,
            maximum_absolute_slope=solution.maximum_absolute_slope,
            maximum_absolute_normalised_slope=(
                solution.maximum_absolute_normalised_slope
            ),
            volume_residual_m3=solution.volume_residual_m3,
            continuous_volume_residual_dimensionless=(
                solution.continuous_volume_residual_dimensionless
            ),
            maximum_collocation_rms_residual=(
                solution.maximum_collocation_rms_residual
            ),
            boundary_residual_norm=solution.boundary_residual_norm,
            collocation_nodes=solution.collocation_nodes,
            nonlinear_iterations=solution.nonlinear_iterations,
            continuation_substeps=substeps,
            solution_regime=solution.solution_regime,
            collocation_x=solution.collocation_x,
            collocation_state=solution.collocation_state,
            collocation_parameter=solution.collocation_parameter,
        )

    def solve(
        self,
        circulation_sq_m4_s2: float,
        *,
        initial: NonlinearSurfaceSolution | None = None,
    ) -> NonlinearSurfaceSolution:
        """Solve at physical Burgers-state forcing ``C**2``."""

        return self.solve_forcing(
            self.forcing_from_circulation_sq(circulation_sq_m4_s2),
            initial=initial,
        )


def solve_first_contact(
    solver: NonlinearSurfaceSolver,
    *,
    static_gap_m: float,
    circulation_coupling_m2: float,
    linear_critical_circulation_sq_m4_s2: float,
) -> NonlinearCriticalResult:
    """Locate first geometric contact by continuation and safeguarded bisection.

    The returned threshold is a prediction of the specified reduced model.  It
    is neither an experimental calibration nor evidence that the post-contact
    air channel is stable.
    """

    if not math.isfinite(static_gap_m):
        raise ValueError("static_gap_m must be finite")
    if static_gap_m <= 0.0:
        surface = solver.solve_forcing(0.0)
        return NonlinearCriticalResult(
            actual_rpm=0.0,
            circulation_sq_m4_s2=0.0,
            dimensionless_forcing=0.0,
            surface=surface,
            bracket_dimensionless_forcing=(0.0, 0.0),
            root_iterations=0,
            height_residual_m=static_gap_m,
            solution_regime=surface.solution_regime,
        )
    if (
        not math.isfinite(circulation_coupling_m2)
        or circulation_coupling_m2 <= 0.0
    ):
        raise ValueError("circulation_coupling_m2 must be finite and positive")
    if (
        not math.isfinite(linear_critical_circulation_sq_m4_s2)
        or linear_critical_circulation_sq_m4_s2 <= 0.0
    ):
        raise ValueError(
            "linear_critical_circulation_sq_m4_s2 must be finite and positive"
        )

    high = solver.forcing_from_circulation_sq(
        linear_critical_circulation_sq_m4_s2
    )
    low = 0.0
    low_solution = solver.solve_forcing(0.0)
    high_solution = solver.solve_forcing(high, initial=low_solution)

    def height_residual(surface: NonlinearSurfaceSolution) -> float:
        return static_gap_m + float(np.min(surface.elevation_m))

    high_residual = height_residual(high_solution)
    expansion_count = 0
    while high_residual > 0.0:
        low = high
        low_solution = high_solution
        high *= 1.35
        expansion_count += 1
        if expansion_count > 14:
            raise NonlinearSurfaceDomainError(
                "first contact was not bracketed before the continuation limit"
            )
        try:
            high_solution = solver.solve_forcing(high, initial=low_solution)
        except NonlinearSurfaceError as error:
            raise NonlinearSurfaceDomainError(
                "the single-valued exact-curvature branch ended before first contact"
            ) from error
        high_residual = height_residual(high_solution)

    if solver.uses_capillary_singular_limit:
        # The singular-limit surface is exactly homogeneous in W.
        unit_depth_per_forcing = -float(np.min(high_solution.elevation_m)) / high
        root_forcing = static_gap_m / unit_depth_per_forcing
        root_surface = solver.solve_forcing(root_forcing)
        circulation_sq = solver.circulation_sq_from_forcing(root_forcing)
        omega = math.sqrt(circulation_sq) / circulation_coupling_m2
        residual = height_residual(root_surface)
        return NonlinearCriticalResult(
            actual_rpm=omega * 60.0 / (2.0 * math.pi),
            circulation_sq_m4_s2=circulation_sq,
            dimensionless_forcing=root_forcing,
            surface=root_surface,
            bracket_dimensionless_forcing=(low, high),
            root_iterations=1,
            height_residual_m=residual,
            solution_regime=root_surface.solution_regime,
        )

    height_tolerance = max(
        solver.config.forward.absolute_tolerance,
        solver.config.forward.relative_tolerance * static_gap_m,
        2.0e-11,
    )
    forcing_tolerance_factor = max(
        solver.config.forward.relative_tolerance, 2.0e-9
    )
    root_surface = high_solution
    root_iterations = 0
    for root_iterations in range(1, 65):
        middle = 0.5 * (low + high)
        # Warm start from the closer certified endpoint.
        if middle - low <= high - middle:
            seed = low_solution
        else:
            seed = high_solution
        root_surface = solver.solve_forcing(middle, initial=seed)
        residual = height_residual(root_surface)
        if residual > 0.0:
            low = middle
            low_solution = root_surface
        else:
            high = middle
            high_solution = root_surface
        if (
            abs(residual) <= height_tolerance
            and high - low
            <= forcing_tolerance_factor * max(1.0, abs(middle))
        ):
            break
    else:  # pragma: no cover - deliberately hard fail on a violated invariant
        raise NonlinearSurfaceError("first-contact bisection did not converge")

    root_forcing = float(root_surface.dimensionless_forcing)
    circulation_sq = solver.circulation_sq_from_forcing(root_forcing)
    omega = math.sqrt(circulation_sq) / circulation_coupling_m2
    residual = height_residual(root_surface)
    return NonlinearCriticalResult(
        actual_rpm=omega * 60.0 / (2.0 * math.pi),
        circulation_sq_m4_s2=circulation_sq,
        dimensionless_forcing=root_forcing,
        surface=root_surface,
        bracket_dimensionless_forcing=(low, high),
        root_iterations=root_iterations,
        height_residual_m=residual,
        solution_regime=root_surface.solution_regime,
    )


__all__ = [
    "NONLINEAR_SURFACE_MODEL_ID",
    "NonlinearCriticalResult",
    "NonlinearSurfaceDomainError",
    "NonlinearSurfaceError",
    "NonlinearSurfaceSolution",
    "NonlinearSurfaceSolver",
    "burgers_dimensionless_potential",
    "solve_first_contact",
]
