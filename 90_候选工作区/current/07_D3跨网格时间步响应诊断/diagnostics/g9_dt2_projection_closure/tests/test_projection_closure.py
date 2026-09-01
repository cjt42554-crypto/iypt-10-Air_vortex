from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

MODULE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))

from audit_projection_closure import (
    assemble_pressure_matrix,
    bbox_mask,
    boundary_adjacency,
    build_open_masks,
    connected_component,
    expanded_bbox,
    face_inverse_density,
    masked_divergence,
    pressure_gradient_faces,
    reconstruct_predictor,
)


def test_uniform_density_projection_closure_identity() -> None:
    active = np.ones((2, 2, 2), dtype=bool)
    spacing = (0.5, 0.75, 0.25)
    dt_s = 0.01
    open_masks = build_open_masks(active)
    density = np.full(active.shape, 4.0)
    beta = face_inverse_density(density, active, open_masks)
    pressure = np.arange(8, dtype=np.float64).reshape(active.shape) + 0.5
    u_plus = tuple(
        np.linspace(-0.3, 0.4, mask.size, dtype=np.float64).reshape(mask.shape)
        for mask in open_masks
    )
    d_plus = masked_divergence(*u_plus, active, open_masks, spacing)
    u_star = reconstruct_predictor(u_plus, pressure, beta, open_masks, spacing, dt_s)
    d_star = masked_divergence(*u_star, active, open_masks, spacing)
    matrix = assemble_pressure_matrix(active, open_masks, beta, spacing)
    volume = float(np.prod(spacing))
    residual = matrix @ pressure[active] + (volume / dt_s) * d_star[active]
    reconstructed = (dt_s / volume) * residual
    np.testing.assert_allclose(reconstructed, d_plus[active], rtol=2e-14, atol=2e-14)
    dense = matrix.toarray()
    np.testing.assert_array_equal(dense, dense.T)
    assert np.linalg.eigvalsh(dense).min() > 0.0


def test_variable_density_uses_harmonic_inverse_face_coefficient() -> None:
    active = np.ones((2, 1, 1), dtype=bool)
    density = np.array([[[2.0]], [[6.0]]])
    open_masks = build_open_masks(active)
    beta_x, _, beta_z = face_inverse_density(density, active, open_masks)
    assert beta_x[1, 0, 0] == 2.0 / (2.0 + 6.0)
    assert beta_z[0, 0, -1] == 1.0 / 2.0
    assert beta_z[1, 0, -1] == 1.0 / 6.0


def test_top_open_boundary_uses_half_cell_pressure_gradient() -> None:
    active = np.ones((1, 1, 2), dtype=bool)
    open_masks = build_open_masks(active)
    pressure = np.array([[[3.0, 5.0]]])
    spacing = (1.0, 1.0, 0.25)
    _, _, gradient_z = pressure_gradient_faces(pressure, open_masks, spacing)
    assert gradient_z[0, 0, -1] == -5.0 / (0.5 * 0.25)
    density = np.full(active.shape, 10.0)
    beta = face_inverse_density(density, active, open_masks)
    zeros = tuple(np.zeros(mask.shape) for mask in open_masks)
    _, _, predictor_z = reconstruct_predictor(
        zeros, pressure, beta, open_masks, spacing, 0.02
    )
    assert predictor_z[0, 0, -1] == 0.02 * 0.1 * gradient_z[0, 0, -1]


def test_closed_bar_face_is_excluded_and_labelled() -> None:
    active = np.array([[[True]], [[False]], [[True]]])
    bar = np.array([[[False]], [[True]], [[False]]])
    open_masks = build_open_masks(active)
    assert not np.any(open_masks[0][1:-1])
    u_x = np.full(open_masks[0].shape, 99.0)
    u_y = np.zeros(open_masks[1].shape)
    u_z = np.zeros(open_masks[2].shape)
    divergence = masked_divergence(u_x, u_y, u_z, active, open_masks, (1.0, 1.0, 1.0))
    np.testing.assert_array_equal(divergence, np.zeros(active.shape))
    labels = boundary_adjacency(active, active, bar)
    assert labels["bar_mask_adjacent"]
    assert labels["bar_adjacent_cell_count"] == 2


def test_hotspot_component_and_two_cell_expansion() -> None:
    hot = np.zeros((9, 9, 9), dtype=bool)
    hot[4, 4, 4] = True
    hot[4, 4, 5] = True
    hot[0, 0, 0] = True
    component = connected_component(hot, (4, 4, 4))
    assert int(np.sum(component)) == 2
    box = expanded_bbox(component, padding=2)
    assert box == [[2, 6], [2, 6], [2, 7]]
    expanded = bbox_mask(hot.shape, box)
    assert int(np.sum(expanded)) == 5 * 5 * 6
    assert not expanded[0, 0, 0]
