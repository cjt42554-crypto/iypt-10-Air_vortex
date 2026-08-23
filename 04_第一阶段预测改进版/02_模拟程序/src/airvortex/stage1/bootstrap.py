"""Install the isolated stage-one backend registry in the current process."""
from __future__ import annotations
from .gui_guard import install_gui_contract_guards
_INSTALLED = False

STAGE1_BACKEND_IMPORTS = (
    (
        "forward",
        "airvortex.stage1.backends",
        ("ForwardStage1Backend", "ForwardTheoryBackend"),
        ("f", "forward_theory", "stage1_forward", "forward_stage1"),
    ),
    (
        "high_fidelity",
        "airvortex.stage1.backends",
        ("HighFidelityStage1Backend", "HighFidelityBackend"),
        (
            "hf",
            "highfidelity",
            "high-fidelity",
            "stage1_hf3d",
            "hf3d_stage1",
        ),
    ),
)

def install_stage1_overlay() -> None:
    global _INSTALLED
    from airvortex.runtime import registry

    # Assignment is intentionally repeated on subsequent calls: installation
    # is idempotent, and a caller cannot accidentally restore baseline backend
    # imports after Stage1 has declared itself active.
    registry._BACKEND_IMPORTS = STAGE1_BACKEND_IMPORTS
    install_gui_contract_guards()
    _INSTALLED = True


__all__ = ["STAGE1_BACKEND_IMPORTS", "install_stage1_overlay"]
