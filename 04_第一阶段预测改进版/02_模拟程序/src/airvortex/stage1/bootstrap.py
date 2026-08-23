"""Install the isolated stage-one backend registry in the current process."""
from __future__ import annotations
from .gui_guard import install_gui_contract_guards
_INSTALLED = False

def install_stage1_overlay() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from airvortex.runtime import registry
    registry._BACKEND_IMPORTS = (
        ("forward", "airvortex.stage1.backends", ("ForwardTheoryBackend", "ForwardStage1Backend")),
        ("high_fidelity", "airvortex.stage1.backends", ("HighFidelityBackend", "HighFidelityStage1Backend")),
    )
    install_gui_contract_guards()
    _INSTALLED = True
