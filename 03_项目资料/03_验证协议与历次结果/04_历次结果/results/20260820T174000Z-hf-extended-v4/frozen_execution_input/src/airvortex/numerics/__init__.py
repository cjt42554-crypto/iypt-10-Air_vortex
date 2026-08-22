"""Numerical building blocks for the axisymmetric HF-preview backend."""

from .axisymmetric import AxisymmetricGrid, impose_cell_velocity_boundaries
from .interface import (
    ConnectionDiagnostics,
    balanced_csf_force,
    gas_connection_candidate,
    gas_connection_diagnostics,
    initialise_flat_phase,
    initialise_rotating_bucket_phase,
    initialize_flat_phase,
    mixture_property,
    projected_color_gradient_csf,
    reinitialise_cls,
    reinitialize_cls,
    surface_profile,
    solid_body_surface_profile,
)
from .projection import AxisymmetricProjection, ProjectionDiagnostics
from .transport import (
    ScalarTransportDiagnostics,
    bounded_mass_correction,
    conservative_cls_compress,
    conservative_advect_scalar,
    semi_lagrangian_advect,
)

__all__ = [
    "AxisymmetricGrid",
    "AxisymmetricProjection",
    "ConnectionDiagnostics",
    "ProjectionDiagnostics",
    "ScalarTransportDiagnostics",
    "balanced_csf_force",
    "bounded_mass_correction",
    "conservative_advect_scalar",
    "conservative_cls_compress",
    "gas_connection_candidate",
    "gas_connection_diagnostics",
    "impose_cell_velocity_boundaries",
    "initialise_flat_phase",
    "initialise_rotating_bucket_phase",
    "initialize_flat_phase",
    "mixture_property",
    "projected_color_gradient_csf",
    "reinitialise_cls",
    "reinitialize_cls",
    "semi_lagrangian_advect",
    "surface_profile",
    "solid_body_surface_profile",
]
