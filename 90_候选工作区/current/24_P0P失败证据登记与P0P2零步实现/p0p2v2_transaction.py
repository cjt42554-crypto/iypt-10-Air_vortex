"""Single-writer lock and atomic, non-replaceable P0-P sample transactions."""

from __future__ import annotations

import ctypes
import errno
import fcntl
import json
import os
import stat
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Self

from p0p2v2_contract import P0PerformanceError, atomic_json


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _rename_directory_noreplace(source: Path, target: Path) -> None:
    """Atomically publish a directory without replacing any target object."""

    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise P0PerformanceError("renameat2 is required for no-replace publication")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        -100,  # AT_FDCWD
        os.fsencode(source),
        -100,
        os.fsencode(target),
        1,  # RENAME_NOREPLACE
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise P0PerformanceError("sample target appeared before publication")
    raise P0PerformanceError(
        f"atomic no-replace sample publication failed: errno={error_number}"
    )


class ExclusiveCampaignLock:
    """Advisory lock acquired through a no-follow descriptor."""

    def __init__(self, path: Path) -> None:
        self.path = path.absolute()
        self._descriptor: int | None = None
        self._identity: dict[str, Any] | None = None

    def __enter__(self) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.parent.is_dir() or self.path.parent.is_symlink():
            raise P0PerformanceError("campaign lock parent must be a real directory")
        flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW
        descriptor = os.open(self.path, flags, 0o600)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise P0PerformanceError(
                    "campaign lock must be one regular non-hardlinked file"
                )
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(descriptor)
            raise P0PerformanceError(
                "another P0-P writer holds the campaign lock"
            ) from exc
        except BaseException:
            os.close(descriptor)
            raise
        try:
            os.fchmod(descriptor, 0o600)
            identity = {
                "pid": os.getpid(),
                "process_start_ticks": _self_start_ticks(),
                "acquired_monotonic_ns": time.monotonic_ns(),
                "lock_path": str(self.path),
                "lock_device": opened.st_dev,
                "lock_inode": opened.st_ino,
            }
            payload = (json.dumps(identity, sort_keys=True) + "\n").encode("utf-8")
            os.ftruncate(descriptor, 0)
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    raise P0PerformanceError("campaign lock identity write stopped")
                offset += written
            os.fsync(descriptor)
            _fsync_directory(self.path.parent)
        except BaseException:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
            raise
        self._descriptor = descriptor
        self._identity = identity
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._descriptor is not None:
            fcntl.flock(self._descriptor, fcntl.LOCK_UN)
            os.close(self._descriptor)
            self._descriptor = None
            self._identity = None

    @property
    def identity(self) -> dict[str, Any]:
        if self._descriptor is None or self._identity is None:
            raise P0PerformanceError("campaign lock is not currently held")
        return dict(self._identity)


def _self_start_ticks() -> int:
    raw = (Path("/proc") / str(os.getpid()) / "stat").read_text(encoding="utf-8")
    close = raw.rfind(")")
    if close < 0:
        raise P0PerformanceError("current PID stat is invalid")
    fields = raw[close + 2 :].split()
    if len(fields) < 20:
        raise P0PerformanceError("current PID stat is too short")
    return int(fields[19])


class SampleTransaction:
    """Publish exactly one success or failure directory without replacement."""

    def __init__(self, output_root: Path, sample_id: str) -> None:
        if not sample_id or Path(sample_id).name != sample_id:
            raise P0PerformanceError("sample ID must be one plain path component")
        self.output_root = output_root.absolute()
        self.sample_id = sample_id
        self.target = self.output_root / sample_id
        self.staging: Path | None = None
        self._published = False

    def begin(self) -> Path:
        if self.output_root.is_symlink():
            raise P0PerformanceError("sample output root must not be a symlink")
        self.output_root.mkdir(parents=True, exist_ok=True)
        if self.target.exists() or self.target.is_symlink():
            raise P0PerformanceError(
                "sample target already exists and cannot be replaced"
            )
        staging_name = tempfile.mkdtemp(
            dir=self.output_root,
            prefix=f".{self.sample_id}.staging.{os.getpid()}.",
        )
        staging = Path(staging_name).absolute()
        if staging.parent != self.output_root or staging.is_symlink():
            raise P0PerformanceError("sample staging directory escaped the output root")
        self.staging = staging
        return staging

    def _publish(self, filename: str, record: Mapping[str, Any]) -> Path:
        if self.staging is None or self._published:
            raise P0PerformanceError("sample transaction is not publishable")
        atomic_json(self.staging / filename, record)
        _fsync_directory(self.staging)
        _rename_directory_noreplace(self.staging, self.target)
        _fsync_directory(self.output_root)
        self._published = True
        return self.target

    def publish_success(self, manifest: Mapping[str, Any]) -> Path:
        return self._publish("sample_manifest.json", manifest)

    def publish_failure(self, failure: Mapping[str, Any]) -> Path:
        return self._publish("sample_failure.json", failure)


class DirectoryPublicationTransaction:
    """Stage a complete evidence directory and publish its terminal record last."""

    def __init__(self, parent: Path, name: str) -> None:
        if not name or Path(name).name != name:
            raise P0PerformanceError("publication name must be one plain component")
        self.parent = parent.absolute()
        self.name = name
        self.target = self.parent / name
        self.staging: Path | None = None
        self._published = False
        self._prepared_terminal: str | None = None

    def begin(self) -> Path:
        if self.parent.is_symlink():
            raise P0PerformanceError("publication parent must not be a symlink")
        self.parent.mkdir(parents=True, exist_ok=True)
        stale = sorted(self.parent.glob(f".{self.name}.staging.*"))
        if stale:
            raise P0PerformanceError(
                "publication has retained stale staging: "
                + ", ".join(path.name for path in stale)
            )
        if not self.parent.is_dir() or self.target.exists() or self.target.is_symlink():
            raise P0PerformanceError("publication target is not uniquely available")
        staging = Path(
            tempfile.mkdtemp(
                dir=self.parent,
                prefix=f".{self.name}.staging.{os.getpid()}.",
            )
        ).absolute()
        if staging.parent != self.parent or staging.is_symlink():
            raise P0PerformanceError("publication staging escaped its parent")
        self.staging = staging
        return staging

    def prepare_terminal(
        self, filename: str, terminal_record: Mapping[str, Any]
    ) -> Path:
        if (
            self.staging is None
            or self._published
            or not filename
            or Path(filename).name != filename
        ):
            raise P0PerformanceError("publication transaction is not publishable")
        terminal = self.staging / filename
        if terminal.exists() or terminal.is_symlink():
            raise P0PerformanceError("terminal publication record already exists")
        atomic_json(terminal, terminal_record)
        _fsync_directory(self.staging)
        self._prepared_terminal = filename
        return terminal

    def publish_prepared_terminal(self, filename: str) -> Path:
        if (
            self.staging is None
            or self._published
            or self._prepared_terminal != filename
            or not filename
            or Path(filename).name != filename
        ):
            raise P0PerformanceError("prepared publication is not publishable")
        terminal = self.staging / filename
        if not terminal.is_file() or terminal.is_symlink():
            raise P0PerformanceError("prepared terminal record is unavailable")
        _fsync_directory(self.staging)
        _rename_directory_noreplace(self.staging, self.target)
        # After the atomic rename the target is already the unique visible
        # terminal.  Record that fact before the parent fsync so a caller can
        # recover a valid success if the fsync reports an error after rename.
        self._published = True
        _fsync_directory(self.parent)
        return self.target

    def publish_terminal(
        self, filename: str, terminal_record: Mapping[str, Any]
    ) -> Path:
        self.prepare_terminal(filename, terminal_record)
        return self.publish_prepared_terminal(filename)


__all__ = [
    "DirectoryPublicationTransaction",
    "ExclusiveCampaignLock",
    "SampleTransaction",
]
