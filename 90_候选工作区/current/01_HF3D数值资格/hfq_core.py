"""Public compatibility facade for the candidate HF3D qualification toolkit."""
from hfq_types import *
from hfq_json import *
from hfq_grid import *
from hfq_campaign import *
from hfq_convergence import *
from hfq_reporting import *

__all__ = [
    "ALLOWED_DISCRETIZATION_PATHS",
    "DEFAULT_BINARY_OBSERVABLES",
    "DEFAULT_SMOOTH_OBSERVABLES",
    "ConvergenceEstimate",
    "EventSummary",
    "GridAudit",
    "PairwiseError",
    "QualificationError",
    "SeriesData",
    "apply_overrides",
    "audit_refinement_design",
    "audit_config",
    "audit_markdown",
    "canonical_sha256",
    "capillary_time_step_limit",
    "convergence_estimate",
    "deep_get",
    "deep_set",
    "dimensions_for_target_bar_resolution",
    "estimate_period_s",
    "event_consistency",
    "event_summary",
    "gate_convergence",
    "generate_campaign",
    "grid_audit",
    "integrate_configured_revolutions",
    "interpolate_time",
    "make_refinement_config",
    "max_abs_observable",
    "pairwise_error",
    "physical_signature",
    "read_json",
    "report_markdown",
    "solve_observed_order",
    "write_json",
]
