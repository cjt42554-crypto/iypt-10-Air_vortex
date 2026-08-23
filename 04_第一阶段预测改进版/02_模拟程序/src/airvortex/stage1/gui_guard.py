"""Runtime-only GUI comparison guard; baseline GUI source stays untouched."""
from __future__ import annotations
from .contracts import ContractError, assert_observables_compatible
_INSTALLED = False

def install_gui_contract_guards() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from airvortex.gui import model as gui_model
    original = gui_model.compare_series
    def guarded(reference, candidate, signal_key):
        try:
            assert_observables_compatible(reference.metadata, candidate.metadata, signal_key)
        except ContractError as exc:
            raise ValueError(
                f"预测合同拒绝比较 {signal_key!r}: {exc}; 同名或同单位不足以证明同一物理量"
            ) from exc
        return original(reference, candidate, signal_key)
    gui_model.compare_series = guarded
    _INSTALLED = True
