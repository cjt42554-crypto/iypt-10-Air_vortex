#!/usr/bin/env python3
"""Quantify one small-domain consequence of paired versus unpaired CLS."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from airvortex.numerics.cartesian3d import ImmersedCartesianGrid
from airvortex.numerics.interface3d import conservative_cls_3d, mixture_property_3d
from airvortex.numerics.phase_transfer3d import (
    transport_momentum_with_phase_transfers,
)


HERE = Path(__file__).resolve().parent


def build_result() -> dict[str, object]:
    grid = ImmersedCartesianGrid(
        12,
        12,
        16,
        0.045,
        0.080,
        bar_half_length_m=0.015,
        bar_radius_m=0.003,
        bar_centre_z_m=0.0045,
    )
    phase = np.where(
        grid.active_mask,
        np.clip(
            0.5
            + 0.32 * np.sin(70.0 * grid.x_cell)
            + 0.23 * np.cos(55.0 * grid.y_cell)
            + 0.15 * np.sin(40.0 * grid.z_cell),
            0.0,
            1.0,
        ),
        0.0,
    )
    density = mixture_property_3d(phase, 998.2, 1.204)
    velocity = tuple(
        np.where(grid.active_mask, value, 0.0)
        for value in (
            2.0 + 15.0 * grid.x_cell - 8.0 * grid.y_cell,
            -1.0 + 10.0 * grid.y_cell + 4.0 * grid.z_cell,
            0.3 + 6.0 * grid.x_cell - 5.0 * grid.z_cell,
        )
    )
    paired_density = density.copy()
    paired_velocity = tuple(value.copy() for value in velocity)

    def apply_paired_transfer(transfers: tuple[np.ndarray, np.ndarray, np.ndarray]) -> None:
        nonlocal paired_density, paired_velocity
        paired_density, paired_velocity = transport_momentum_with_phase_transfers(
            paired_density,
            paired_velocity,
            transfers,
            998.2 - 1.204,
            grid,
        )

    final_phase, diagnostics = conservative_cls_3d(
        phase,
        grid,
        2.5 * grid.minimum_spacing_m,
        pseudo_cfl=0.2,
        iterations=12,
        transfer_callback=apply_paired_transfer,
    )
    unpaired_density = mixture_property_3d(final_phase, 998.2, 1.204)

    def momentum(rho: np.ndarray, values: tuple[np.ndarray, ...]) -> np.ndarray:
        return np.asarray([grid.integrate(rho * value) for value in values])

    def kinetic_energy(rho: np.ndarray, values: tuple[np.ndarray, ...]) -> float:
        return 0.5 * grid.integrate(rho * sum(value * value for value in values))

    initial_momentum = momentum(density, velocity)
    paired_momentum = momentum(paired_density, paired_velocity)
    # This comparator applies the CLS density change while retaining the
    # pre-CLS velocity.  It isolates the missing artificial-flux momentum update
    # in the historical algorithm; it is not a full historical trajectory.
    unpaired_momentum = momentum(unpaired_density, velocity)
    initial_energy = kinetic_energy(density, velocity)
    paired_energy = kinetic_energy(paired_density, paired_velocity)
    unpaired_energy = kinetic_energy(unpaired_density, velocity)

    return {
        "schema_version": 1,
        "experiment_id": "HF3D_10_05_SMALL_DOMAIN_CLS_PAIRING_V1",
        "scope": (
            "One deterministic synthetic CLS substep comparison; no physical "
            "time advance and no Q1R3 convergence claim."
        ),
        "grid_shape": list(grid.shape),
        "density_kg_m3": {"liquid": 998.2, "gas": 1.204},
        "cls": {
            "interface_thickness_cells": 2.5,
            "pseudo_cfl": 0.2,
            "iterations": 12,
            "maximum_phase_change": float(np.max(np.abs(final_phase - phase))),
            "limited_absolute_volume_m3": diagnostics.limited_absolute_volume_m3,
        },
        "initial_momentum_kg_m_s": initial_momentum.tolist(),
        "paired": {
            "momentum_kg_m_s": paired_momentum.tolist(),
            "momentum_relative_error": float(
                np.linalg.norm(paired_momentum - initial_momentum)
                / np.linalg.norm(initial_momentum)
            ),
            "kinetic_energy_j": paired_energy,
            "kinetic_energy_relative_change": paired_energy / initial_energy - 1.0,
        },
        "unpaired_density_with_pre_cls_velocity_comparator": {
            "momentum_kg_m_s": unpaired_momentum.tolist(),
            "momentum_relative_error": float(
                np.linalg.norm(unpaired_momentum - initial_momentum)
                / np.linalg.norm(initial_momentum)
            ),
            "kinetic_energy_j": unpaired_energy,
            "kinetic_energy_relative_change": unpaired_energy / initial_energy - 1.0,
        },
        "initial_kinetic_energy_j": initial_energy,
        "interpretation": (
            "Paired CLS conserves total momentum to roundoff and dissipates a small "
            "amount of kinetic energy.  Applying the same density change without its "
            "momentum flux changes momentum and creates energy in this counterexample."
        ),
        "claim_limit": (
            "This establishes that the historical missing pairing can contaminate "
            "momentum/energy observables.  It does not quantify the old Q1R3 error or "
            "prove that pairing alone restores three-grid convergence."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=HERE / "cls_pairing.json")
    arguments = parser.parse_args()
    arguments.output.write_text(
        json.dumps(build_result(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
