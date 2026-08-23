#!/usr/bin/env python3
"""Independent symbolic/numerical checks for the air-vortex theory audit.

This script deliberately imports neither ``airvortex`` nor any project result.
It reconstructs the audited formulae from SymPy expressions and independent
mpmath quadrature.  A non-zero exit status means that at least one identity or
quoted constant in ``research/理论公式独立审计.md`` has failed its check.
"""

from __future__ import annotations

import math

import mpmath as mp
import sympy as sp


mp.mp.dps = 80


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def close(actual: mp.mpf, expected: mp.mpf, *, rtol: str = "1e-45") -> None:
    scale = max(mp.mpf(1), abs(actual), abs(expected))
    require(abs(actual - expected) <= mp.mpf(rtol) * scale,
            f"{actual!s} != {expected!s}")


def symbolic_checks() -> None:
    r, z, c, C, nu, gamma = sp.symbols(
        "r z c C nu gamma", positive=True, finite=True
    )
    x = r**2 / c**2
    gamma_value = 4 * nu / c**2
    u_r = -gamma * r / 2
    u_z = gamma * z
    u_theta = C * (1 - sp.exp(-x)) / r

    divergence = sp.diff(r * u_r, r) / r + sp.diff(u_z, z)
    require(sp.simplify(divergence) == 0, "Burgers velocity is not solenoidal")

    omega_z = sp.simplify(sp.diff(r * u_theta, r) / r)
    require(
        sp.simplify(omega_z - 2 * C * sp.exp(-x) / c**2) == 0,
        "axial-vorticity formula failed",
    )

    # Steady axial-vorticity balance: radial advection = axial stretching +
    # viscous diffusion.  This also checks c^2=4*nu/gamma.
    vort_residual = (
        u_r * sp.diff(omega_z, r)
        - gamma * omega_z
        - nu * (sp.diff(omega_z, r, 2) + sp.diff(omega_z, r) / r)
    ).subs(gamma, gamma_value)
    require(sp.simplify(vort_residual) == 0, "Burgers vorticity balance failed")

    ell = sp.simplify(r * u_theta)
    ell_residual = (
        u_r * sp.diff(ell, r)
        - nu * (sp.diff(ell, r, 2) - sp.diff(ell, r) / r)
    ).subs(gamma, gamma_value)
    require(sp.simplify(ell_residual) == 0, "angular-momentum balance failed")

    # The full Burgers strain has pressure terms omitted by the reduced
    # cyclostrophic surface model.
    radial_viscous = sp.diff(u_r, r, 2) + sp.diff(u_r, r) / r - u_r / r**2
    require(sp.simplify(radial_viscous) == 0, "linear radial strain viscous term")
    radial_inertia = sp.simplify(u_r * sp.diff(u_r, r) - u_theta**2 / r)
    required_p_r_over_rho = u_theta**2 / r - gamma**2 * r / 4
    require(
        sp.simplify(radial_inertia + required_p_r_over_rho) == 0,
        "full-Burgers radial pressure correction failed",
    )

    # Closed primitive of the dimensionless surface integral.
    y = sp.symbols("y", positive=True, finite=True)
    I_closed = (
        2 * (sp.log(2) + sp.Ei(-y) - sp.Ei(-2 * y))
        - (1 - sp.exp(-y)) ** 2 / y
    )
    I_integrand = (1 - sp.exp(-y)) ** 2 / y**2
    require(
        sp.simplify(sp.diff(I_closed, y) - I_integrand) == 0,
        "surface exponential-integral primitive failed",
    )

    J_closed = (
        sp.EulerGamma + sp.log(y / 2) - 2 * sp.Ei(-y) + sp.Ei(-2 * y)
    )
    J_integrand = (1 - sp.exp(-y)) ** 2 / y
    require(
        sp.simplify(sp.diff(J_closed, y) - J_integrand) == 0,
        "finite-volume J primitive failed",
    )

    # Dimensionless identities for U=Omega*a.
    rho, Omega, a, mu, sigma, g = sp.symbols(
        "rho Omega a mu sigma g", positive=True, finite=True
    )
    Re = rho * Omega * a**2 / mu
    Fr = Omega**2 * a / g
    We = rho * Omega**2 * a**3 / sigma
    Bo = rho * g * a**2 / sigma
    Oh = mu / sp.sqrt(rho * sigma * a)
    Ca = mu * Omega * a / sigma
    require(sp.simplify(Bo - We / Fr) == 0, "Bo=We/Fr failed")
    require(sp.simplify(Oh**2 - We / Re**2) == 0, "Oh^2=We/Re^2 failed")
    require(sp.simplify(Ca - We / Re) == 0, "Ca=We/Re failed")

    # AGG mass-flux closure.  With a divergence-free volume-averaged velocity,
    # CH diffusion and J=-rho'(phi) M grad(lambda) conserve mixture mass only
    # when rho is affine (rho''=0).  A quadratic stand-in exposes the exact
    # residual of a naive nonlinear positive-density extension.
    coordinate, time = sp.symbols("coordinate time", real=True)
    phase = sp.Function("phase")(coordinate, time)
    chemical = sp.Function("chemical")(coordinate, time)
    rho_0, rho_1, rho_2, mobility = sp.symbols(
        "rho_0 rho_1 rho_2 mobility", finite=True
    )
    rho_trial = rho_0 + rho_1 * phase + rho_2 * phase**2
    rho_prime = rho_1 + 2 * rho_2 * phase
    mass_flux = -rho_prime * mobility * sp.diff(chemical, coordinate)
    mass_residual = sp.diff(rho_trial, time) + sp.diff(mass_flux, coordinate)
    mass_residual = mass_residual.subs(
        sp.diff(phase, time), mobility * sp.diff(chemical, coordinate, 2)
    )
    expected_residual = (
        -2 * rho_2 * mobility
        * sp.diff(phase, coordinate) * sp.diff(chemical, coordinate)
    )
    require(
        sp.simplify(mass_residual - expected_residual) == 0,
        "nonlinear-density mass residual formula failed",
    )
    require(
        sp.simplify(mass_residual.subs(rho_2, 0)) == 0,
        "affine-density AGG mass balance failed",
    )

    # Modica--Mortola normalisations: sigma_hat*c_Psi=sigma.
    order = sp.symbols("order", real=True)
    quartic = (1 - order**2) ** 2 / 4
    quartic_c = sp.integrate(sp.sqrt(2 * quartic), (order, -1, 1))
    # SymPy does not infer 1-order**2 >= 0 from integration bounds in every
    # version, so check against the elementary integral explicitly.
    require(
        sp.simplify(sp.integrate((1 - order**2) / sp.sqrt(2),
                                 (order, -1, 1)) - 2 * sp.sqrt(2) / 3) == 0,
        "quartic surface-energy normalisation failed",
    )
    require(quartic_c.is_finite is not False, "quartic integral is non-finite")
    require(
        sp.simplify(
            sp.integrate(sp.sqrt(1 - order**2), (order, -1, 1)) - sp.pi / 2
        ) == 0,
        "double-obstacle surface-energy normalisation failed",
    )


def I_closed(x: mp.mpf) -> mp.mpf:
    if x == 0:
        return mp.mpf(0)
    return (
        2 * (mp.log(2) + mp.ei(-x) - mp.ei(-2 * x))
        - (-mp.expm1(-x)) ** 2 / x
    )


def J_closed(x: mp.mpf) -> mp.mpf:
    if x == 0:
        return mp.mpf(0)
    return mp.euler + mp.log(x / 2) - 2 * mp.ei(-x) + mp.ei(-2 * x)


def I_quad(x: mp.mpf) -> mp.mpf:
    return mp.quad(lambda t: (-mp.expm1(-t)) ** 2 / t**2, [0, x])


def J_quad(x: mp.mpf) -> mp.mpf:
    return mp.quad(lambda t: (-mp.expm1(-t)) ** 2 / t, [0, x])


def finite_factors(X: mp.mpf) -> tuple[mp.mpf, mp.mpf, mp.mpf]:
    denom = 2 * mp.log(2)
    F = I_closed(X) / denom
    K = J_closed(X) / (denom * X)
    return F, K, F - K


def numerical_checks() -> dict[str, mp.mpf]:
    # Direct quadrature is independent of the Ei closed forms.
    for X in map(mp.mpf, ("1e-5", "0.01", "0.1", "1", "4", "20.25", "100")):
        close(I_closed(X), I_quad(X), rtol="2e-44")
        close(J_closed(X), J_quad(X), rtol="2e-44")

    # The infinite-radius depth integral is exactly 2 ln 2.
    I_infinity_quad = mp.quad(
        lambda t: (-mp.expm1(-t)) ** 2 / t**2,
        [0, 1, mp.inf],
    )
    close(I_infinity_quad, 2 * mp.log(2), rtol="2e-45")

    # Maximum of (1-exp(-x))/sqrt(x); discard the stationary x=0 root.
    x_velocity_max = mp.findroot(lambda x: mp.exp(x) - 2 * x - 1, (1, 2))
    r_velocity_max_over_c = mp.sqrt(x_velocity_max)
    u_max_over_C_by_c = (-mp.expm1(-x_velocity_max)) / mp.sqrt(x_velocity_max)

    # Funnel halfwidth is the midpoint between centre and the asymptotic level.
    x_half = mp.findroot(lambda x: I_closed(x) - mp.log(2), (1, 1.3))
    b_half_over_c = mp.sqrt(x_half)

    quoted_rtol = "1e-39"  # Constants below are intentionally quoted to 40 digits.
    close(x_velocity_max, mp.mpf("1.256431208626169676982737616609216326916"),
          rtol=quoted_rtol)
    close(r_velocity_max_over_c,
          mp.mpf("1.120906422778534031976766735690633359934"),
          rtol=quoted_rtol)
    close(u_max_over_C_by_c,
          mp.mpf("0.6381726863389514809353692353294663520254"),
          rtol=quoted_rtol)
    close(x_half, mp.mpf("1.124864798404129573133301988379654040828"),
          rtol=quoted_rtol)
    close(b_half_over_c,
          mp.mpf("1.060596435221300172522420472788597000888"),
          rtol=quoted_rtol)

    # Area-volume refill: mean[eta-H]=0 exactly in the inviscid finite cylinder.
    X = mp.mpf("20.25")
    F, K, G = finite_factors(X)
    mean_dimensionless = mp.quad(
        lambda x: I_closed(x) / (2 * mp.log(2)) - G,
        [0, X],
    ) / X
    close(mean_dimensionless, mp.mpf(0), rtol="2e-45")
    close(
        I_closed(X) - J_closed(X) / X,
        mp.quad(lambda x: I_closed(x), [0, X]) / X,
        rtol="2e-44",
    )

    # The geometric post-contact continuation has a square-root exponent.
    eps_values = [mp.mpf("1e-3"), mp.mpf("3e-4"), mp.mpf("1e-4")]
    radii: list[mp.mpf] = []
    for eps in eps_values:
        T = (1 + eps) ** 2  # C^2/C_c^2 for C proportional to Omega.
        target = 2 * mp.log(2) * G * (1 - 1 / T)
        x_root = mp.findroot(lambda x: I_closed(x) - target, target)
        radii.append(mp.sqrt(x_root))
    local_exponent = mp.log(radii[-2] / radii[-1]) / mp.log(
        eps_values[-2] / eps_values[-1]
    )
    require(abs(local_exponent - mp.mpf("0.5")) < mp.mpf("2e-4"),
            "near-critical radius did not approach exponent 1/2")

    # Small-X asymptotic checks use errors divided by the first omitted power.
    tiny = mp.mpf("1e-6")
    I_small = tiny - tiny**2 / 2 + 7 * tiny**3 / 36
    J_small = tiny**2 / 2 - tiny**3 / 3 + 7 * tiny**4 / 48
    require(abs(I_closed(tiny) - I_small) < tiny**4, "I small-X series failed")
    require(abs(J_closed(tiny) - J_small) < tiny**5, "J small-X series failed")

    # Default water--air affine closures become negative after very small CH
    # overshoots below phi=-1.  These are exact zero crossings, not heuristic
    # safety margins.
    rho_l = mp.mpf("998.2")
    rho_g = mp.mpf("1.204")
    mu_l = mp.mpf("1.002e-3")
    mu_g = mp.mpf("1.825e-5")
    phi_rho_zero = -(rho_l + rho_g) / (rho_l - rho_g)
    phi_mu_zero = -(mu_l + mu_g) / (mu_l - mu_g)
    require(phi_rho_zero < -1 and phi_mu_zero < -1,
            "affine positivity thresholds must lie below the gas well")

    return {
        "x_u_max": x_velocity_max,
        "r_u_max/c": r_velocity_max_over_c,
        "u_max/(C/c)": u_max_over_C_by_c,
        "x_half": x_half,
        "b_half/c": b_half_over_c,
        "I_infinity": I_infinity_quad,
        "F(20.25)": F,
        "K(20.25)": K,
        "G(20.25)": G,
        "finite_threshold_Omega_ratio": 1 / mp.sqrt(G),
        "near_critical_local_exponent": local_exponent,
        "phi_at_zero_affine_density": phi_rho_zero,
        "phi_at_zero_affine_viscosity": phi_mu_zero,
        "quartic_c_Psi": 2 * mp.sqrt(2) / 3,
        "double_obstacle_c_Psi": mp.pi / 2,
    }


def main() -> None:
    symbolic_checks()
    values = numerical_checks()
    print("AIR-VORTEX THEORY AUDIT: PASS")
    for name, value in values.items():
        print(f"{name:36s} = {mp.nstr(value, 18)}")
    print(f"python math pi                     = {math.pi:.17g}")


if __name__ == "__main__":
    main()
