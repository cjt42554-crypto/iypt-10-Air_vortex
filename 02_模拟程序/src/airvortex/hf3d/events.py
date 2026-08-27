"""Topology-event state machine for HF3D run orchestration."""

from __future__ import annotations

import math

from airvortex.core import Event, SimulationConfig
from airvortex.hf3d.types import HF3DStepDiagnostics
from airvortex.numerics.interface3d import Topology3DDiagnostics


class TopologyEventTracker:
    """Track transitions without owning or mutating the flow state."""

    def __init__(self, config: SimulationConfig, events: list[Event]) -> None:
        self._config = config
        self.events = events
        self.previous_topology: Topology3DDiagnostics | None = None
        self.first_resolved_seen = False
        self.resolved_start_time: float | None = None
        self.sustained_reported = False

    def initialise(self, topology: Topology3DDiagnostics) -> None:
        self.previous_topology = topology

    def update(
        self,
        state_time_s: float,
        diagnostics: HF3DStepDiagnostics,
    ) -> None:
        topology = diagnostics.topology
        previous = self.previous_topology
        if topology.touches_bar and (previous is None or not previous.touches_bar):
            self.events.append(
                Event(
                    state_time_s,
                    "gas_touches_bar",
                    "Top-connected gas first contacts a voxel face of the capsule.",
                    {"bar_gas_contact_area_m2": topology.bar_gas_contact_area_m2},
                )
            )
        if topology.resolved_channel and (
            previous is None or not previous.resolved_channel
        ):
            kind = (
                "resolved_channel"
                if not self.first_resolved_seen
                else "channel_reconnect"
            )
            self.events.append(
                Event(
                    state_time_s,
                    kind,
                    "Resolution-gated top-connected gas path observed; simulation continues.",
                    {
                        "minimum_throat_radius_m": topology.minimum_throat_radius_m,
                        "required_radius_m": topology.required_resolved_radius_m,
                    },
                )
            )
            self.first_resolved_seen = True
            self.resolved_start_time = state_time_s
            self.sustained_reported = False
        if (
            previous is not None
            and previous.resolved_channel
            and not topology.resolved_channel
        ):
            self.events.append(
                Event(
                    state_time_s,
                    "channel_break",
                    "Previously resolved gas path is no longer resolution-gated connected.",
                    {"minimum_throat_radius_m": topology.minimum_throat_radius_m},
                )
            )
            self.resolved_start_time = None
            self.sustained_reported = False
        radius_ok = (
            topology.resolved_channel
            and topology.minimum_throat_radius_m
            >= self._config.high_fidelity.stable_channel_radius_m
        )
        if radius_ok:
            if self.resolved_start_time is None:
                self.resolved_start_time = state_time_s
            period = (
                math.inf
                if abs(diagnostics.actual_bar_rpm) <= 1.0e-12
                else 60.0 / abs(diagnostics.actual_bar_rpm)
            )
            required_hold = max(
                self._config.high_fidelity.stable_channel_hold_time_s,
                3.0 * period,
            )
            if (
                not self.sustained_reported
                and math.isfinite(required_hold)
                and state_time_s - self.resolved_start_time >= required_hold
            ):
                self.events.append(
                    Event(
                        state_time_s,
                        "sustained_channel_numerical",
                        "Numerical radius-and-hold convention satisfied; experimental stability is unverified.",
                        {
                            "hold_time_s": state_time_s - self.resolved_start_time,
                            "required_hold_time_s": required_hold,
                            "stable_radius_m": (
                                self._config.high_fidelity.stable_channel_radius_m
                            ),
                        },
                    )
                )
                self.sustained_reported = True
        else:
            self.resolved_start_time = None
            self.sustained_reported = False
        self.previous_topology = topology


__all__ = ["TopologyEventTracker"]
