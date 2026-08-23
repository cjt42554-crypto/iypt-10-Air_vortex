"""Cooperative background execution with a strict, observable state machine."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
import queue
import tempfile
import threading
import time
import traceback
from typing import Any, Generic, TypeVar
from uuid import uuid4

from airvortex.core import Control, Progress, RunStatus, SimulationResult


T = TypeVar("T")


class JobState(str, Enum):
    IDLE = "idle"
    VALIDATING = "validating"
    RUNNING = "running"
    PAUSING = "pausing"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


TERMINAL_STATES = frozenset({JobState.CANCELLED, JobState.COMPLETED, JobState.FAILED})

_ALLOWED_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.IDLE: frozenset({JobState.VALIDATING}),
    JobState.VALIDATING: frozenset(
        {JobState.RUNNING, JobState.CANCELLING, JobState.FAILED}
    ),
    JobState.RUNNING: frozenset(
        {
            JobState.PAUSING,
            JobState.CANCELLING,
            JobState.COMPLETED,
            JobState.CANCELLED,
            JobState.FAILED,
        }
    ),
    JobState.PAUSING: frozenset(
        {
            JobState.PAUSED,
            JobState.CANCELLING,
            JobState.CANCELLED,
            JobState.COMPLETED,
            JobState.FAILED,
        }
    ),
    JobState.PAUSED: frozenset(
        {
            JobState.RUNNING,
            JobState.CANCELLING,
            JobState.CANCELLED,
            JobState.COMPLETED,
            JobState.FAILED,
        }
    ),
    JobState.CANCELLING: frozenset({JobState.CANCELLED, JobState.FAILED}),
    JobState.CANCELLED: frozenset(),
    JobState.COMPLETED: frozenset(),
    JobState.FAILED: frozenset(),
}


class InvalidStateTransition(RuntimeError):
    """Raised when a caller requests an operation invalid for the current state."""


@dataclass(frozen=True, slots=True)
class JobMessage:
    kind: str
    timestamp_utc: str
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class StateTransition:
    previous: JobState
    current: JobState
    monotonic_s: float


class _RuntimeControl(Control):
    """Core Control with an acknowledgement at the backend's safe checkpoint."""

    def __init__(self, on_pause_ack: Callable[[], None]) -> None:
        super().__init__()
        self._on_pause_ack = on_pause_ack
        self._pause_ack = threading.Event()

    def pause(self) -> bool:
        self._pause_ack.clear()
        return super().pause()

    def checkpoint(self) -> bool:
        if self.paused:
            self._on_pause_ack()
            self._pause_ack.set()
        return super().checkpoint()

    def wait_for_pause_ack(self, timeout: float | None) -> bool:
        return self._pause_ack.wait(timeout)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as stream:
        temp = Path(stream.name)
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
    temp.replace(path)


class JobController(Generic[T]):
    """Run one cooperative job in a background thread.

    A thread is intentionally used at this boundary because backend objects and
    callbacks are not guaranteed to be spawn-pickleable.  Real pause/continue and
    soft cancellation are nevertheless implemented by the shared core ``Control``
    object: a pause is only acknowledged after the worker enters ``checkpoint``.

    Each instance owns exactly one job and cannot be restarted after a terminal
    state.  This makes illegal GUI/CLI transitions explicit instead of silently
    reusing stale controls or results.
    """

    def __init__(
        self,
        *,
        manifest_path: str | Path | None = None,
        message_queue_size: int = 1024,
        run_id: str | None = None,
    ) -> None:
        if message_queue_size < 1:
            raise ValueError("message_queue_size must be positive")
        self.run_id = run_id or uuid4().hex
        self._state = JobState.IDLE
        self._condition = threading.Condition(threading.RLock())
        self._messages: queue.Queue[JobMessage] = queue.Queue(maxsize=message_queue_size)
        self._control = _RuntimeControl(self._acknowledge_pause)
        self._thread: threading.Thread | None = None
        self._result: T | None = None
        self._error: BaseException | None = None
        self._traceback = ""
        self._started_at_utc: str | None = None
        self._ended_at_utc: str | None = None
        self._started_monotonic: float | None = None
        self._ended_monotonic: float | None = None
        self._transitions: list[StateTransition] = []
        self._manifest_path = Path(manifest_path) if manifest_path is not None else None
        self._progress_count = 0

    @property
    def state(self) -> JobState:
        with self._condition:
            return self._state

    @property
    def result(self) -> T | None:
        with self._condition:
            return self._result

    @property
    def error(self) -> BaseException | None:
        with self._condition:
            return self._error

    @property
    def traceback_text(self) -> str:
        with self._condition:
            return self._traceback

    @property
    def transitions(self) -> tuple[StateTransition, ...]:
        with self._condition:
            return tuple(self._transitions)

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def is_alive(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    def _transition(self, new_state: JobState) -> None:
        with self._condition:
            previous = self._state
            if new_state not in _ALLOWED_TRANSITIONS[previous]:
                raise InvalidStateTransition(
                    f"illegal job transition: {previous.value} -> {new_state.value}"
                )
            self._state = new_state
            transition = StateTransition(previous, new_state, time.monotonic())
            self._transitions.append(transition)
            self._condition.notify_all()
        self._put_message(
            "state",
            {"previous": previous.value, "current": new_state.value},
            preserve=True,
        )

    def _put_message(
        self, kind: str, payload: Mapping[str, Any], *, preserve: bool = False
    ) -> None:
        message = JobMessage(kind, _utc_now(), dict(payload))
        try:
            self._messages.put_nowait(message)
            return
        except queue.Full:
            pass
        # Progress is lossy under back-pressure.  State/error messages replace
        # the oldest item so the GUI cannot miss a terminal condition.
        if not preserve:
            return
        try:
            self._messages.get_nowait()
        except queue.Empty:
            pass
        try:
            self._messages.put_nowait(message)
        except queue.Full:
            pass

    def emit(self, progress: Progress | Mapping[str, Any] | str) -> None:
        if isinstance(progress, Progress):
            payload: dict[str, Any] = {
                "fraction": progress.fraction,
                "simulated_time_s": progress.simulated_time_s,
                "step": progress.step,
                "stage": progress.stage,
                "message": progress.message,
                "metrics": dict(progress.metrics),
            }
        elif isinstance(progress, Mapping):
            payload = dict(progress)
        elif isinstance(progress, str):
            payload = {"message": progress}
        else:
            raise TypeError("progress must be Progress, a mapping, or a string")
        with self._condition:
            self._progress_count += 1
        self._put_message("progress", payload)

    def poll(self, *, limit: int | None = None) -> list[JobMessage]:
        """Drain up to ``limit`` messages without blocking."""

        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative or None")
        messages: list[JobMessage] = []
        while limit is None or len(messages) < limit:
            try:
                messages.append(self._messages.get_nowait())
            except queue.Empty:
                break
        return messages

    def start(
        self,
        target: Callable[[Control, Callable[[Progress], None]], T],
        *,
        validator: Callable[[], None] | None = None,
    ) -> "JobController[T]":
        """Validate and invoke ``target(control, progress)`` in the background."""

        if not callable(target):
            raise TypeError("target must be callable")
        with self._condition:
            if self._state is not JobState.IDLE:
                raise InvalidStateTransition(
                    f"start requires idle state, got {self._state.value}"
                )
            self._started_at_utc = _utc_now()
            self._started_monotonic = time.monotonic()
        self._transition(JobState.VALIDATING)
        self._thread = threading.Thread(
            target=self._worker,
            args=(target, validator),
            name=f"airvortex-job-{self.run_id[:8]}",
            daemon=False,
        )
        self._thread.start()
        return self

    def _worker(
        self,
        target: Callable[[Control, Callable[[Progress], None]], T],
        validator: Callable[[], None] | None,
    ) -> None:
        try:
            if validator is not None:
                validator()
            if self._control.stop_requested:
                self._finish_cancelled(None)
                return
            self._transition(JobState.RUNNING)
            result = target(self._control, self.emit)
            with self._condition:
                self._result = result
                stopped_result = (
                    isinstance(result, SimulationResult)
                    and result.status is RunStatus.STOPPED
                )
                # The state decision and transition are atomic with respect to
                # pause/cancel callers.  Whichever obtains the lock first wins:
                # a completed job rejects a late cancel; an accepted cancel can
                # never be overwritten by COMPLETED.
                if self._control.stop_requested or stopped_result:
                    self._finish_cancelled(result)
                else:
                    self._finish(JobState.COMPLETED)
        except BaseException as exc:  # preserve backend traceback for failed manifests
            with self._condition:
                self._error = exc
                self._traceback = "".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                )
            self._put_message(
                "error",
                {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "traceback": self._traceback,
                },
                preserve=True,
            )
            self._finish(JobState.FAILED)

    def _finish_cancelled(self, result: T | None) -> None:
        with self._condition:
            if result is not None:
                self._result = result
            state = self._state
            if state not in {
                JobState.CANCELLING,
                JobState.PAUSING,
                JobState.PAUSED,
                JobState.RUNNING,
            }:
                raise InvalidStateTransition(f"cannot settle cancellation from {state.value}")
            self._finish(JobState.CANCELLED)

    def _finish(self, terminal: JobState) -> None:
        with self._condition:
            self._ended_at_utc = _utc_now()
            self._ended_monotonic = time.monotonic()
        self._transition(terminal)
        if self._manifest_path is not None:
            _atomic_json(self._manifest_path, self.manifest())

    def _acknowledge_pause(self) -> None:
        with self._condition:
            if self._state is not JobState.PAUSING:
                return
            self._transition(JobState.PAUSED)

    def pause(self, *, wait: bool = True, timeout: float | None = 5.0) -> bool:
        """Request pause and optionally wait for a backend checkpoint acknowledgement."""

        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be non-negative or None")
        with self._condition:
            if self._state is not JobState.RUNNING:
                raise InvalidStateTransition(
                    f"pause requires running state, got {self._state.value}"
                )
            self._transition(JobState.PAUSING)
            if not self._control.pause():
                raise RuntimeError("control refused pause despite running job state")
        if not wait:
            return True
        acknowledged = self._control.wait_for_pause_ack(timeout)
        if not acknowledged:
            return False
        return self.state is JobState.PAUSED

    def resume(self) -> None:
        with self._condition:
            if self._state is not JobState.PAUSED:
                raise InvalidStateTransition(
                    f"resume requires paused state, got {self._state.value}"
                )
            if not self._control.resume():
                raise RuntimeError("control refused resume despite paused job state")
            self._transition(JobState.RUNNING)

    def cancel(self, reason: str = "user requested stop") -> None:
        with self._condition:
            if self._state not in {
                JobState.VALIDATING,
                JobState.RUNNING,
                JobState.PAUSING,
                JobState.PAUSED,
            }:
                raise InvalidStateTransition(
                    f"cancel is invalid from {self._state.value} state"
                )
            self._transition(JobState.CANCELLING)
            self._control.request_stop(reason)

    def wait_for_state(self, state: JobState, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while self._state is not state:
                if self._state in TERMINAL_STATES:
                    return False
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def join(self, timeout: float | None = None) -> bool:
        """Wait for the worker; return ``True`` only once it has terminated."""

        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def manifest(self) -> dict[str, Any]:
        with self._condition:
            duration = None
            if self._started_monotonic is not None:
                end = self._ended_monotonic or time.monotonic()
                duration = max(0.0, end - self._started_monotonic)
            return {
                "runtime_manifest_version": 1,
                "run_id": self.run_id,
                "state": self._state.value,
                "terminal": self._state in TERMINAL_STATES,
                "started_at_utc": self._started_at_utc,
                "ended_at_utc": self._ended_at_utc,
                "duration_s": duration,
                "progress_messages": self._progress_count,
                "error": None
                if self._error is None
                else {
                    "type": type(self._error).__name__,
                    "message": str(self._error),
                    "traceback": self._traceback,
                },
                "transitions": [
                    {
                        "previous": item.previous.value,
                        "current": item.current.value,
                        "monotonic_s": item.monotonic_s,
                    }
                    for item in self._transitions
                ],
            }


__all__ = [
    "InvalidStateTransition",
    "JobController",
    "JobMessage",
    "JobState",
    "StateTransition",
    "TERMINAL_STATES",
]
