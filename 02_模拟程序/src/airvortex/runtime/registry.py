"""Backend discovery without importing solver modules at CLI import time."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import importlib
import threading
from typing import Any

from airvortex.core import Backend, BackendMode


class BackendRegistryError(LookupError):
    """Raised for duplicate, unavailable, or malformed backend registrations."""


BackendFactory = Callable[[], Backend]


@dataclass(frozen=True, slots=True)
class BackendDescriptor:
    """Small display-safe description of a lazily constructed backend."""

    backend_id: str
    factory: BackendFactory
    aliases: tuple[str, ...] = ()


def _normalise_id(value: str | BackendMode) -> str:
    if isinstance(value, BackendMode):
        return value.value
    if not isinstance(value, str) or not value.strip():
        raise BackendRegistryError("backend id must be a non-empty string")
    return value.strip().lower().replace("-", "_")


class BackendRegistry:
    """Thread-safe registry of lazy backend factories.

    Registering classes rather than instances prevents solver state from leaking
    between runs and keeps imports of optional/high-cost backends lazy.
    """

    def __init__(self) -> None:
        self._descriptors: dict[str, BackendDescriptor] = {}
        self._aliases: dict[str, str] = {}
        self._lock = threading.RLock()
        self._discovery_errors: dict[str, str] = {}

    def register(
        self,
        backend_id: str | BackendMode,
        factory: BackendFactory,
        *,
        aliases: Iterable[str] = (),
        replace: bool = False,
    ) -> None:
        canonical = _normalise_id(backend_id)
        if not callable(factory):
            raise TypeError("backend factory must be callable")
        normalised_aliases = tuple(dict.fromkeys(_normalise_id(alias) for alias in aliases))
        if canonical in normalised_aliases:
            normalised_aliases = tuple(alias for alias in normalised_aliases if alias != canonical)
        with self._lock:
            occupied = {canonical, *normalised_aliases}
            collisions = sorted(
                key
                for key in occupied
                if (key in self._descriptors or key in self._aliases)
                and not (replace and self._aliases.get(key, key) == canonical)
            )
            if collisions:
                raise BackendRegistryError(
                    f"backend id/alias already registered: {', '.join(collisions)}"
                )
            if replace and canonical in self._descriptors:
                old = self._descriptors[canonical]
                for alias in old.aliases:
                    self._aliases.pop(alias, None)
            descriptor = BackendDescriptor(canonical, factory, normalised_aliases)
            self._descriptors[canonical] = descriptor
            for alias in normalised_aliases:
                self._aliases[alias] = canonical

    def unregister(self, backend_id: str | BackendMode) -> None:
        canonical = self.resolve_id(backend_id)
        with self._lock:
            descriptor = self._descriptors.pop(canonical)
            for alias in descriptor.aliases:
                self._aliases.pop(alias, None)

    def resolve_id(self, backend_id: str | BackendMode) -> str:
        key = _normalise_id(backend_id)
        with self._lock:
            canonical = self._aliases.get(key, key)
            if canonical not in self._descriptors:
                choices = ", ".join(self.ids()) or "<none>"
                raise BackendRegistryError(
                    f"unknown backend {key!r}; available backends: {choices}"
                )
            return canonical

    def create(self, backend_id: str | BackendMode) -> Backend:
        canonical = self.resolve_id(backend_id)
        with self._lock:
            factory = self._descriptors[canonical].factory
        try:
            backend = factory()
        except Exception as exc:  # discovery must report a usable error boundary
            raise BackendRegistryError(
                f"cannot construct backend {canonical!r}: {type(exc).__name__}: {exc}"
            ) from exc
        run = getattr(backend, "run", None)
        if not callable(run):
            raise BackendRegistryError(f"backend {canonical!r} has no callable run()")
        mode = getattr(backend, "mode", None)
        if mode is not None:
            mode_value = _normalise_id(mode)
            if mode_value != canonical:
                raise BackendRegistryError(
                    f"backend {canonical!r} reports incompatible mode {mode_value!r}"
                )
        return backend

    def ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._descriptors))

    def descriptors(self) -> tuple[BackendDescriptor, ...]:
        with self._lock:
            return tuple(self._descriptors[key] for key in sorted(self._descriptors))

    @property
    def discovery_errors(self) -> dict[str, str]:
        with self._lock:
            return dict(self._discovery_errors)

    def _record_discovery_error(self, backend_id: str, exc: BaseException) -> None:
        with self._lock:
            self._discovery_errors[backend_id] = f"{type(exc).__name__}: {exc}"


_BACKEND_IMPORTS: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "forward",
        "airvortex.backends.forward_topology",
        ("ForwardTheoryBackend", "ForwardTopologyBackend"),
        ("f", "forward_theory"),
    ),
    (
        "high_fidelity",
        "airvortex.backends.high_fidelity_3d",
        ("HighFidelityBackend", "HF3DBackend"),
        ("hf", "highfidelity", "high-fidelity"),
    ),
)


def _lazy_factory(module_name: str, class_names: tuple[str, ...]) -> BackendFactory:
    def create() -> Backend:
        module = importlib.import_module(module_name)
        for class_name in class_names:
            cls: Any = getattr(module, class_name, None)
            if cls is not None:
                break
        else:
            expected = " or ".join(class_names)
            raise AttributeError(f"{module_name} provides neither {expected}")
        return cls()

    return create


def discover_backends(*, probe: bool = False) -> BackendRegistry:
    """Return a fresh registry for the built-in backends.

    By default no solver module is imported.  ``probe=True`` additionally
    constructs each backend and records import/construction errors; registrations
    remain present so callers receive a precise error when selecting one.
    """

    registry = BackendRegistry()
    for backend_id, module_name, class_names, aliases in _BACKEND_IMPORTS:
        registry.register(
            backend_id,
            _lazy_factory(module_name, class_names),
            aliases=aliases,
        )
        if probe:
            try:
                registry.create(backend_id)
            except Exception as exc:
                registry._record_discovery_error(backend_id, exc)
    return registry


__all__ = [
    "BackendDescriptor",
    "BackendFactory",
    "BackendRegistry",
    "BackendRegistryError",
    "discover_backends",
]
