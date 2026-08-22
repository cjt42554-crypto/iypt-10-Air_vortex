"""Dimensionless groups shared by both air-vortex simulation backends."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import TYPE_CHECKING, Any, Mapping

from scipy.special import exp1

from ._common import DataValidationError, finite_float

if TYPE_CHECKING:  # Avoid coupling the numerics core back to the analysis layer.
    from airvortex.core import SimulationConfig


@dataclass(frozen=True)
class GeometryRatios:
    """Dimensionless geometry, using bar half-length ``a`` as the drive scale."""

    depth_to_radius: float
    bar_half_length_to_radius: float
    bar_diameter_to_half_length: float
    submergence_to_radius: float

    @property
    def H_over_R(self) -> float:  # noqa: N802 - equation-style public API
        return self.depth_to_radius

    @property
    def a_over_R(self) -> float:  # noqa: N802
        return self.bar_half_length_to_radius

    @property
    def d_over_a(self) -> float:
        return self.bar_diameter_to_half_length

    @property
    def s_over_R(self) -> float:  # noqa: N802
        return self.submergence_to_radius

    def as_dict(self) -> dict[str, float]:
        return {
            "H/R": self.H_over_R,
            "a/R": self.a_over_R,
            "d/a": self.d_over_a,
            "s/R": self.s_over_R,
        }


@dataclass(frozen=True)
class FiniteContainerFactors:
    r"""Finite-cylinder factors for the Burgers/Oseen swirl profile.

    The independent variable is :math:`X=(R/c)^2`.  With
    :math:`D_\infty=\ln(2)C^2/(gc^2)`, ``F`` is the wall-to-axis depth divided
    by :math:`D_\infty`, ``K`` is the volume-refill height at the wall divided
    by :math:`D_\infty`, and ``G=F-K`` is the axis depression relative to the
    undisturbed *mean* surface divided by :math:`D_\infty`.

    These are inviscid, zero-capillarity geometric factors.  In particular,
    ``G`` is not a fitted or experimentally validated correction.
    """

    X: float  # noqa: N815 - equation-style public API
    F: float  # noqa: N815
    K: float  # noqa: N815
    G: float  # noqa: N815

    def as_dict(self) -> dict[str, float]:
        return {"X": self.X, "F(X)": self.F, "K(X)": self.K, "G(X)": self.G}


_EULER_GAMMA = 0.577215664901532860606512090082402431
_TWO_LOG_TWO = 2.0 * math.log(2.0)
_FINITE_FACTOR_SERIES_CUTOFF = 1.0


def _small_x_finite_container_factors(x: float) -> FiniteContainerFactors:
    r"""Evaluate ``F``, ``K`` and ``G`` without subtractive cancellation.

    For :math:`a_n=(-1)^n(2^n-2)/n!`, direct integration of
    :math:`(1-e^{-x})^2` gives

    .. math::

       I(X)=\sum_{n\ge2}\frac{a_n X^{n-1}}{n-1},\quad
       \frac{J(X)}X=\sum_{n\ge2}\frac{a_n X^{n-1}}n,

    while ``G`` is accumulated directly from
    :math:`I-J/X=\sum a_nX^{n-1}/[n(n-1)]`.  The last series avoids losing all
    significant digits in ``F-K`` as ``X`` tends to zero.
    """

    i_terms: list[float] = []
    j_over_x_terms: list[float] = []
    g_numerator_terms: list[float] = []
    # At the cutoff X=1, 48 terms are beyond binary64 precision.  Fixed
    # length also makes the result deterministic across SciPy versions.
    for n in range(2, 50):
        coefficient = ((-1.0) ** n) * (float(2**n) - 2.0) / math.factorial(n)
        common = coefficient * x ** (n - 1)
        i_terms.append(common / (n - 1))
        j_over_x_terms.append(common / n)
        g_numerator_terms.append(common / (n * (n - 1)))

    f = math.fsum(i_terms) / _TWO_LOG_TWO
    k = math.fsum(j_over_x_terms) / _TWO_LOG_TWO
    g_factor = math.fsum(g_numerator_terms) / _TWO_LOG_TWO
    return FiniteContainerFactors(X=x, F=f, K=k, G=g_factor)


def burgers_finite_container_factors(X: float) -> FiniteContainerFactors:  # noqa: N803
    r"""Return the finite-radius and volume-refill factors at ``X=(R/c)^2``.

    For positive ``X`` the closed forms are

    .. math::

       F(X)&=\frac{2[\ln2-E_1(X)+E_1(2X)]
                    -(1-e^{-X})^2/X}{2\ln2},\\
       K(X)&=\frac{\gamma_E+\ln(X/2)+2E_1(X)-E_1(2X)}
                    {2\ln2\,X},\\
       G(X)&=F(X)-K(X).

    A power series is used near zero so the leading values
    ``F~X/(2 ln 2)`` and ``K~G~X/(4 ln 2)`` remain numerically resolvable.
    ``scipy.special.exp1`` supplies the exponentially scaled tail for the
    remaining range.
    """

    x = finite_float(X, "X", positive=True)
    if x <= _FINITE_FACTOR_SERIES_CUTOFF:
        return _small_x_finite_container_factors(x)

    e1_x = float(exp1(x))
    e1_2x = float(exp1(2.0 * x))
    one_minus_exp = -math.expm1(-x)
    i_value = (
        2.0 * (math.log(2.0) - e1_x + e1_2x)
        - one_minus_exp * one_minus_exp / x
    )
    j_value = _EULER_GAMMA + math.log(x / 2.0) + 2.0 * e1_x - e1_2x
    f = i_value / _TWO_LOG_TWO
    # Divide by X first to avoid overflowing 2*ln(2)*X near DBL_MAX.
    k = (j_value / x) / _TWO_LOG_TWO
    return FiniteContainerFactors(X=x, F=f, K=k, G=f - k)


@dataclass(frozen=True)
class DimensionlessNumbers:
    """Standard rotating-flow groups for the magnetic-stirrer air vortex.

    The common length and velocity scales are ``a`` and ``omega * a``.  These
    definitions intentionally give the exact algebraic identity ``Bo=We/Fr``.
    """

    reynolds: float
    froude: float
    weber: float
    bond: float
    ohnesorge: float
    geometry: GeometryRatios
    touch_parameter: float | None = None
    local_weber: float | None = None
    unavailable: tuple[str, ...] = ()
    finite_container: FiniteContainerFactors | None = None
    finite_touch_parameter: float | None = None
    notes: tuple[str, ...] = (
        "Pi_touch is the infinite-domain structural group ln(2) C^2/(c^2 g S); "
        "it is not a finite-container threshold.",
        "Pi_touch_finite=Pi_touch*G(X) includes finite radius and volume refill "
        "within the zero-capillarity equilibrium reduction only. Capillarity, "
        "contact-line physics, transient flow, gas connectivity, and post-contact "
        "breakup can shift the observed transition, so Pi_touch_finite=1 is not "
        "an experimentally validated threshold.",
        "C denotes the Burgers coefficient Gamma/(2*pi), never the full "
        "circulation Gamma. Omega-based groups require the actual bar speed.",
    )

    @property
    def Re(self) -> float:  # noqa: N802
        return self.reynolds

    @property
    def Fr(self) -> float:  # noqa: N802
        return self.froude

    @property
    def We(self) -> float:  # noqa: N802
        return self.weber

    @property
    def Bo(self) -> float:  # noqa: N802
        return self.bond

    @property
    def Oh(self) -> float:  # noqa: N802
        return self.ohnesorge

    @property
    def Pi_touch(self) -> float | None:  # noqa: N802
        """Infinite-domain structural group, not a finite-container threshold."""

        return self.touch_parameter

    @property
    def X(self) -> float | None:  # noqa: N802
        """Finite-container coordinate ``(R/c)^2`` when ``c`` is available."""

        return None if self.finite_container is None else self.finite_container.X

    @property
    def F_X(self) -> float | None:  # noqa: N802
        """Wall-to-axis depth factor ``F(X)``."""

        return None if self.finite_container is None else self.finite_container.F

    @property
    def K_X(self) -> float | None:  # noqa: N802
        """Finite-volume wall-refill factor ``K(X)``."""

        return None if self.finite_container is None else self.finite_container.K

    @property
    def G_X(self) -> float | None:  # noqa: N802
        """Mean-surface axis-depression factor ``G(X)=F(X)-K(X)``."""

        return None if self.finite_container is None else self.finite_container.G

    @property
    def Pi_touch_finite(self) -> float | None:  # noqa: N802
        """``Pi_touch*G(X)`` in the zero-capillarity finite-cylinder reduction."""

        return self.finite_touch_parameter

    @property
    def We_l(self) -> float | None:  # noqa: N802
        """Local gas-core Weber number, or ``None`` when it cannot be observed."""

        return self.local_weber

    def as_dict(self) -> dict[str, float | None]:
        values = {
            "Re": self.Re,
            "Fr": self.Fr,
            "We": self.We,
            "Bo": self.Bo,
            "Oh": self.Oh,
            "Pi_touch": self.Pi_touch,
            "X": self.X,
            "F(X)": self.F_X,
            "K(X)": self.K_X,
            "G(X)": self.G_X,
            "Pi_touch_finite": self.Pi_touch_finite,
            "We_l": self.We_l,
        }
        values.update(self.geometry.as_dict())
        return values

    @property
    def metadata(self) -> dict[str, object]:
        """Definitions and evidence limits suitable for result metadata."""

        return {
            "definitions": {
                "C": "Burgers coefficient C=Gamma/(2*pi), unit m^2/s",
                "X": "(container_radius/vortex_core_radius)^2",
                "F(X)": "finite-radius wall-to-axis depth divided by D_infinity",
                "K(X)": "finite-volume wall refill divided by D_infinity",
                "G(X)": "F(X)-K(X), axis depression relative to mean surface",
                "Pi_touch": "infinite-domain structural group",
                "Pi_touch_finite": "Pi_touch*G(X), zero-capillarity finite-cylinder group",
            },
            "evidence_status": "theory-derived structural indicators; not experimentally validated",
            "notes": list(self.notes),
        }


def compute_dimensionless_numbers(
    *,
    density: float,
    dynamic_viscosity: float,
    surface_tension: float,
    angular_speed: float,
    bar_half_length: float,
    gravity: float,
    container_radius: float,
    liquid_depth: float,
    bar_diameter: float,
    bar_submergence: float,
    burgers_coefficient: float | None = None,
    full_circulation: float | None = None,
    vortex_core_radius: float | None = None,
    local_relative_speed: float | None = None,
    local_air_core_radius: float | None = None,
) -> DimensionlessNumbers:
    r"""Compute ``Re, Fr, We, Bo, Oh`` and structural indicators in SI.

    ``bar_submergence`` is the vertical distance from the undisturbed surface
    to the top of the bar.  ``burgers_coefficient`` is
    :math:`C=\Gamma/(2\pi)`, not the full circulation; callers holding
    :math:`\Gamma` can instead pass ``full_circulation`` for explicit
    conversion.  ``Pi_touch=ln(2) C^2/(c^2 g S)`` is retained as the
    infinite-domain structural group.  The finite-cylinder reduction also
    reports ``X=(R/c)^2``, ``F(X)``, ``K(X)``, ``G(X)=F(X)-K(X)`` and
    ``Pi_touch_finite=Pi_touch*G(X)``.  Neither contact group includes
    capillarity or constitutes an experimentally validated threshold.  ``We_l``
    is reported only when both local relative speed and local air-core radius
    are available.  All dimensional inputs are SI values.
    """

    rho = finite_float(density, "density", positive=True)
    mu = finite_float(dynamic_viscosity, "dynamic_viscosity", positive=True)
    sigma = finite_float(surface_tension, "surface_tension", positive=True)
    omega = finite_float(angular_speed, "angular_speed")
    a = finite_float(bar_half_length, "bar_half_length", positive=True)
    g = finite_float(gravity, "gravity", positive=True)
    radius = finite_float(container_radius, "container_radius", positive=True)
    depth = finite_float(liquid_depth, "liquid_depth", positive=True)
    diameter = finite_float(bar_diameter, "bar_diameter", positive=True)
    submergence = finite_float(bar_submergence, "bar_submergence")
    if omega < 0.0:
        raise DataValidationError("angular_speed must be non-negative")
    if submergence < 0.0:
        raise DataValidationError("bar_submergence must be non-negative")

    re = rho * omega * a * a / mu
    fr = omega * omega * a / g
    we = rho * omega * omega * a**3 / sigma
    bo = rho * g * a * a / sigma
    oh = mu / math.sqrt(rho * sigma * a)
    geometry = GeometryRatios(
        depth_to_radius=depth / radius,
        bar_half_length_to_radius=a / radius,
        bar_diameter_to_half_length=diameter / a,
        submergence_to_radius=submergence / radius,
    )
    unavailable: list[str] = []
    pi_touch: float | None = None
    finite_container: FiniteContainerFactors | None = None
    finite_pi_touch: float | None = None
    if burgers_coefficient is not None and full_circulation is not None:
        raise DataValidationError(
            "provide burgers_coefficient C or full_circulation Gamma, not both"
        )
    coefficient: float | None = None
    if burgers_coefficient is not None:
        coefficient = finite_float(burgers_coefficient, "burgers_coefficient")
    elif full_circulation is not None:
        coefficient = finite_float(full_circulation, "full_circulation") / (
            2.0 * math.pi
        )
    if vortex_core_radius is None:
        unavailable.append(
            "X, F(X), K(X), and G(X) unavailable: vortex_core_radius is required"
        )
    else:
        core_radius = finite_float(
            vortex_core_radius, "vortex_core_radius", positive=True
        )
        radius_ratio = radius / core_radius
        finite_container = burgers_finite_container_factors(radius_ratio**2)

    if coefficient is None or finite_container is None:
        unavailable.append(
            "Pi_touch and Pi_touch_finite unavailable: Burgers coefficient C "
            "and vortex_core_radius are required"
        )
    else:
        # ``finite_container`` can only be non-None after validating c above.
        assert vortex_core_radius is not None
        core_radius = float(vortex_core_radius)
        if submergence <= 0.0:
            raise DataValidationError(
                "bar_submergence must be greater than zero when computing Pi_touch"
            )
        pi_touch = math.log(2.0) * coefficient**2 / (
            core_radius**2 * g * submergence
        )
        finite_pi_touch = pi_touch * finite_container.G

    local_weber: float | None = None
    if local_relative_speed is None or local_air_core_radius is None:
        unavailable.append(
            "We_l unavailable: local_relative_speed and local_air_core_radius are required"
        )
    else:
        local_speed = finite_float(local_relative_speed, "local_relative_speed")
        if local_speed < 0.0:
            raise DataValidationError("local_relative_speed must be non-negative")
        local_radius = finite_float(
            local_air_core_radius, "local_air_core_radius", positive=True
        )
        local_weber = rho * local_speed**2 * local_radius / sigma

    return DimensionlessNumbers(
        reynolds=re,
        froude=fr,
        weber=we,
        bond=bo,
        ohnesorge=oh,
        geometry=geometry,
        touch_parameter=pi_touch,
        local_weber=local_weber,
        unavailable=tuple(unavailable),
        finite_container=finite_container,
        finite_touch_parameter=finite_pi_touch,
    )


def _read_path(obj: Any, path: str) -> Any:
    current = obj
    for component in path.split("."):
        if isinstance(current, Mapping):
            if component not in current:
                raise KeyError(path)
            current = current[component]
        else:
            if not hasattr(current, component):
                raise KeyError(path)
            current = getattr(current, component)
    return current


def _first(obj: Any, *paths: str) -> Any:
    for path in paths:
        try:
            return _read_path(obj, path)
        except KeyError:
            pass
    raise DataValidationError(
        "SimulationConfig is missing all supported fields: " + ", ".join(paths)
    )


def dimensionless_from_config(
    config: "SimulationConfig",
    *,
    burgers_coefficient: float | None = None,
    full_circulation: float | None = None,
    vortex_core_radius: float | None = None,
    local_relative_speed: float | None = None,
    local_air_core_radius: float | None = None,
) -> DimensionlessNumbers:
    """Compute groups from the core :class:`SimulationConfig`.

    The core schema has evolved during development, so this adapter accepts the
    canonical nested names and a small set of legacy aliases.  It never guesses
    units: every accepted field is explicitly SI (apart from an explicitly
    named ``*_rpm`` field, which is converted to radians per second).
    """

    rho = _first(
        config,
        "fluid.liquid_density_kg_m3",
        "fluid.density_kg_m3",
        "fluid.density",
        "fluid.rho_l",
        "density_kg_m3",
    )
    mu = _first(
        config,
        "fluid.liquid_dynamic_viscosity_pa_s",
        "fluid.dynamic_viscosity_pa_s",
        "fluid.dynamic_viscosity",
        "fluid.mu_l",
        "dynamic_viscosity_pa_s",
    )
    sigma = _first(
        config,
        "fluid.surface_tension_n_m",
        "fluid.surface_tension",
        "fluid.sigma",
        "surface_tension_n_m",
    )
    g = _first(
        config,
        "fluid.gravity_m_s2",
        "fluid.gravity",
        "fluid.g",
        "gravity_m_s2",
    )
    radius = _first(
        config,
        "container.radius_m",
        "geometry.container_radius_m",
        "geometry.container_radius",
        "geometry.radius_m",
        "container_radius_m",
    )
    depth = _first(
        config,
        "container.liquid_depth_m",
        "geometry.liquid_depth_m",
        "geometry.liquid_depth",
        "geometry.initial_liquid_depth_m",
        "liquid_depth_m",
    )
    try:
        diameter = _first(
            config,
            "stir_bar.diameter_m",
            "geometry.bar_diameter_m",
            "geometry.bar_diameter",
            "bar_diameter_m",
        )
    except DataValidationError:
        # The core schema stores the bar radius rather than a redundant diameter.
        bar_radius = _first(config, "stir_bar.radius_m")
        diameter = 2.0 * float(bar_radius)

    try:
        a = _first(
            config,
            "stir_bar.half_length_m",
            "geometry.bar_half_length_m",
            "bar_half_length_m",
        )
    except DataValidationError:
        a = 0.5 * float(
            _first(
                config,
                "geometry.bar_length_m",
                "geometry.bar_length",
                "bar_length_m",
            )
        )

    try:
        submergence = _first(
            config,
            "geometry.bar_submergence_m",
            "geometry.bar_submergence",
            "bar_submergence_m",
        )
    except DataValidationError:
        try:
            bar_top = _first(
                config,
                "geometry.bar_top_height_m",
                "geometry.bar_top_z_m",
                "geometry.bar_top_height",
                "bar_top_height_m",
            )
        except DataValidationError:
            clearance = _first(config, "stir_bar.bottom_clearance_m")
            bar_radius = _first(config, "stir_bar.radius_m")
            bar_top = float(clearance) + 2.0 * float(bar_radius)
        submergence = float(depth) - float(bar_top)

    omega_paths = (
        "forcing.bar_angular_speed_rad_s",
        "forcing.measured_bar_angular_speed_rad_s",
        "forcing.angular_speed_rad_s",
        "bar_angular_speed_rad_s",
    )
    try:
        omega = _first(config, *omega_paths)
    except DataValidationError:
        rpm = _first(
            config,
            "drive.target_rpm",
            "forcing.bar_speed_rpm",
            "forcing.measured_bar_rpm",
            "forcing.rpm_bar_measured",
            "bar_speed_rpm",
        )
        omega = float(rpm) * 2.0 * math.pi / 60.0

    # The reduced model uses the Burgers coefficient C=Gamma/(2*pi), with the
    # frozen law C=k_C*omega.  It is not the full circulation Gamma.
    if burgers_coefficient is None and full_circulation is None:
        try:
            coupling = _first(config, "forward.circulation_coupling_m2")
            burgers_coefficient = float(coupling) * float(omega)
        except DataValidationError:
            pass
    if vortex_core_radius is None:
        try:
            vortex_core_radius = _first(config, "forward.effective_core_radius_m")
        except DataValidationError:
            pass

    return compute_dimensionless_numbers(
        density=rho,
        dynamic_viscosity=mu,
        surface_tension=sigma,
        angular_speed=omega,
        bar_half_length=a,
        gravity=g,
        container_radius=radius,
        liquid_depth=depth,
        bar_diameter=diameter,
        bar_submergence=submergence,
        burgers_coefficient=burgers_coefficient,
        full_circulation=full_circulation,
        vortex_core_radius=vortex_core_radius,
        local_relative_speed=local_relative_speed,
        local_air_core_radius=local_air_core_radius,
    )


# Short public alias for notebooks and CLI code.
compute_dimensionless = compute_dimensionless_numbers
