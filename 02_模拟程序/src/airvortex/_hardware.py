"""Early, dependency-free numerical thread policy.

This module must stay free of NumPy/SciPy imports.  ``airvortex.__init__`` calls
it before importing the public API so BLAS libraries see the selected limits
when they are first loaded.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from typing import Final


_THREAD_ENVIRONMENT_KEYS: Final[tuple[str, ...]] = (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "BLIS_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)
_DEFAULT_BLAS_THREADS: Final[int] = 2


@dataclass(frozen=True, slots=True)
class NumericalThreadPolicy:
    """Record the effective early-start thread policy for reproducibility."""

    requested: str
    selected_threads: int | None
    affinity_logical_cpus: int
    explicitly_overrode_library_environment: bool
    applied: tuple[tuple[str, str], ...]
    preserved: tuple[tuple[str, str], ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _affinity_cpu_count() -> int:
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        return max(1, os.cpu_count() or 1)


def configure_numerical_threads() -> NumericalThreadPolicy:
    """Apply a conservative BLAS limit before numerical libraries are loaded.

    ``AIRVORTEX_BLAS_THREADS`` has three forms:

    - unset or ``auto``: choose at most two threads, while respecting any
      explicitly supplied library-specific environment variables;
    - a positive integer: override all supported library thread variables;
    - ``inherit``: leave every library variable untouched.

    Two is the measured default for the bundled HF3D pressure workload on the
    delivery host.  It avoids the severe all-logical-CPU OpenBLAS scheduling
    overhead while remaining portable to machines with fewer CPUs.
    """

    affinity_count = _affinity_cpu_count()
    raw = os.environ.get("AIRVORTEX_BLAS_THREADS")
    requested = "auto" if raw is None else raw.strip().lower()
    explicit_override = raw is not None and requested not in {"", "auto", "inherit"}
    if requested in {"", "auto"}:
        selected: int | None = min(_DEFAULT_BLAS_THREADS, affinity_count)
    elif requested == "inherit":
        selected = None
    else:
        try:
            selected = int(requested)
        except ValueError as exc:
            raise ValueError(
                "AIRVORTEX_BLAS_THREADS must be 'auto', 'inherit', or a positive integer"
            ) from exc
        if selected < 1:
            raise ValueError("AIRVORTEX_BLAS_THREADS must be positive")
        selected = min(selected, affinity_count)

    applied: list[tuple[str, str]] = []
    preserved: list[tuple[str, str]] = []
    for key in _THREAD_ENVIRONMENT_KEYS:
        existing = os.environ.get(key)
        if selected is None:
            if existing is not None:
                preserved.append((key, existing))
            continue
        value = str(selected)
        if explicit_override or existing is None:
            os.environ[key] = value
            applied.append((key, value))
        else:
            preserved.append((key, existing))

    # Dynamic OpenMP team resizing can recreate the same oversubscription
    # pattern even when a nominal limit is present.  Respect an explicit user
    # choice, but keep the application default deterministic.
    if selected is not None:
        os.environ.setdefault("OMP_DYNAMIC", "FALSE")

    return NumericalThreadPolicy(
        requested=requested,
        selected_threads=selected,
        affinity_logical_cpus=affinity_count,
        explicitly_overrode_library_environment=explicit_override,
        applied=tuple(applied),
        preserved=tuple(preserved),
    )


NUMERICAL_THREAD_POLICY: Final[NumericalThreadPolicy] = configure_numerical_threads()


__all__ = ["NUMERICAL_THREAD_POLICY", "NumericalThreadPolicy", "configure_numerical_threads"]
