"""Self-describing JSON/CSV/NPZ run archives with SHA-256 verification."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import math
import os
from pathlib import Path
import platform
import re
import shutil
import tempfile
from typing import Any, Mapping

import numpy as np

from airvortex.core import (
    BackendMode,
    Event,
    ResultError,
    RunStatus,
    SimulationConfig,
    SimulationResult,
    sha256_file,
)
from airvortex.core.protocols import json_ready
from airvortex.version import __version__

from .config_io import (
    canonical_json_bytes,
    config_sha256,
    dumps_json_strict,
    load_config_json,
    read_json_strict,
)


ARCHIVE_FORMAT_VERSION = 1
PROVENANCE_FORMAT_VERSION = 2

_SOURCE_HASH_DOMAIN = b"airvortex-source-tree-v1\0"
_SOURCE_SCOPE = (
    "pyproject.toml, top-level run.py/run_gui.sh launchers when present, and regular "
    "*.py files below src/airvortex; "
    "allow-list excludes outputs, caches, bytecode, tests, documents, and user data"
)
_PEAK_RSS_ATTRIBUTION = (
    "process-lifetime high-water mark, not a per-run increment; it can include memory "
    "peaks from earlier work and concurrent tasks in the same process"
)
_HEX_OBJECT_ID = re.compile(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}")


class ArchiveError(ValueError):
    """Raised for corrupt, incomplete, or unsupported run archives."""


@dataclass(frozen=True, slots=True)
class ArchivedRun:
    path: Path
    config: SimulationConfig
    result: SimulationResult
    manifest: Mapping[str, Any]
    provenance: Mapping[str, Any]


def _distribution_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def _unavailable(*, unit: str | None, reason: str, **fields: object) -> dict[str, Any]:
    record: dict[str, Any] = {
        "available": False,
        "value": None,
        "unit": unit,
        "reason": reason,
    }
    record.update(fields)
    return record


def _discover_project_root() -> Path | None:
    """Return a source-checkout root without storing its absolute path."""

    for candidate in Path(__file__).resolve().parents:
        if (
            (candidate / "pyproject.toml").is_file()
            and (candidate / "src" / "airvortex").is_dir()
        ):
            return candidate
    return None


def source_tree_identity(project_root: str | Path | None = None) -> dict[str, Any]:
    """Hash the controlled executable source set independent of absolute paths.

    The digest is domain-separated and frames each POSIX relative path and file
    payload with an eight-byte length.  This makes it insensitive to directory
    enumeration order while preventing ambiguous path/content concatenations.
    Generated data is excluded by a positive allow-list, not by a fragile list
    of names to ignore.
    """

    root = (
        Path(project_root).resolve()
        if project_root is not None
        else _discover_project_root()
    )
    if root is None:
        return {
            **_unavailable(
                unit=None,
                reason=(
                    "installed distribution does not expose a source checkout with "
                    "pyproject.toml and src/airvortex"
                ),
            ),
            "algorithm": "sha256",
            "scope": _SOURCE_SCOPE,
            "file_count": 0,
            "total_bytes": 0,
            "absolute_root_stored": False,
        }

    candidates: list[Path] = []
    for relative in ("pyproject.toml", "run.py", "run_gui.sh"):
        candidate = root / relative
        if candidate.is_file() and not candidate.is_symlink():
            candidates.append(candidate)
    package_root = root / "src" / "airvortex"
    if package_root.is_dir() and not package_root.is_symlink():
        candidates.extend(
            path
            for path in package_root.rglob("*.py")
            if path.is_file() and not path.is_symlink()
        )
    files = sorted(candidates, key=lambda item: item.relative_to(root).as_posix())
    if not files:
        return {
            **_unavailable(
                unit=None,
                reason="no regular files matched the controlled source allow-list",
            ),
            "algorithm": "sha256",
            "scope": _SOURCE_SCOPE,
            "file_count": 0,
            "total_bytes": 0,
            "absolute_root_stored": False,
        }

    digest = hashlib.sha256()
    digest.update(_SOURCE_HASH_DOMAIN)
    total_bytes = 0
    try:
        for path in files:
            relative = path.relative_to(root).as_posix().encode("utf-8")
            payload = path.read_bytes()
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            digest.update(len(payload).to_bytes(8, "big"))
            digest.update(payload)
            total_bytes += len(payload)
    except OSError as exc:
        return {
            **_unavailable(
                unit=None,
                reason=f"controlled source could not be read: {type(exc).__name__}",
            ),
            "algorithm": "sha256",
            "scope": _SOURCE_SCOPE,
            "file_count": 0,
            "total_bytes": 0,
            "absolute_root_stored": False,
        }
    return {
        "available": True,
        "value": digest.hexdigest(),
        "unit": None,
        "reason": None,
        "algorithm": "sha256",
        "scope": _SOURCE_SCOPE,
        "file_count": len(files),
        "total_bytes": total_bytes,
        "absolute_root_stored": False,
    }


def _unavailable_source_tree(reason: str, sample_point: str) -> dict[str, Any]:
    return {
        **_unavailable(unit=None, reason=reason),
        "algorithm": "sha256",
        "scope": _SOURCE_SCOPE,
        "file_count": 0,
        "total_bytes": 0,
        "absolute_root_stored": False,
        "sample_point": sample_point,
    }


def _unavailable_git_revision(reason: str, sample_point: str) -> dict[str, Any]:
    return {
        **_unavailable(
            unit=None,
            reason=reason,
            vcs="git",
            object_id_algorithm=None,
        ),
        "sample_point": sample_point,
    }


def _git_directory(root: Path) -> Path | None:
    marker = root / ".git"
    if marker.is_dir():
        return marker
    if not marker.is_file():
        return None
    try:
        line = marker.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    prefix = "gitdir:"
    if not line.lower().startswith(prefix):
        return None
    value = line[len(prefix) :].strip()
    if not value:
        return None
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = marker.parent / candidate
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    return resolved if resolved.is_dir() else None


def _read_git_ref(git_dir: Path, reference: str) -> str | None:
    if not reference.startswith("refs/") or ".." in Path(reference).parts:
        return None
    common = git_dir
    common_marker = git_dir / "commondir"
    if common_marker.is_file():
        try:
            common_value = common_marker.read_text(encoding="utf-8").strip()
            candidate = Path(common_value)
            common = (
                (git_dir / candidate).resolve()
                if not candidate.is_absolute()
                else candidate.resolve()
            )
        except (OSError, UnicodeDecodeError):
            common = git_dir
    for base in (git_dir, common):
        loose = base.joinpath(*reference.split("/"))
        try:
            value = loose.read_text(encoding="ascii").strip()
        except (OSError, UnicodeDecodeError):
            continue
        if _HEX_OBJECT_ID.fullmatch(value):
            return value.lower()
    packed = common / "packed-refs"
    try:
        lines = packed.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    for line in lines:
        if not line or line.startswith(("#", "^")):
            continue
        parts = line.split(" ", 1)
        if (
            len(parts) == 2
            and parts[1] == reference
            and _HEX_OBJECT_ID.fullmatch(parts[0])
        ):
            return parts[0].lower()
    return None


def git_revision_identity(project_root: str | Path | None = None) -> dict[str, Any]:
    """Read a Git object id from metadata only; never invent a revision."""

    root = (
        Path(project_root).resolve()
        if project_root is not None
        else _discover_project_root()
    )
    if root is None:
        return _unavailable(
            unit=None,
            reason="source checkout unavailable; no Git revision claimed",
            vcs="git",
            object_id_algorithm=None,
        )
    git_dir = _git_directory(root)
    if git_dir is None:
        return _unavailable(
            unit=None,
            reason="Git metadata unavailable; no revision claimed",
            vcs="git",
            object_id_algorithm=None,
        )
    try:
        head = (git_dir / "HEAD").read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return _unavailable(
            unit=None,
            reason="Git HEAD could not be read; no revision claimed",
            vcs="git",
            object_id_algorithm=None,
        )
    if head.startswith("ref:"):
        revision = _read_git_ref(git_dir, head[4:].strip())
    else:
        revision = head.lower() if _HEX_OBJECT_ID.fullmatch(head) else None
    if revision is None:
        return _unavailable(
            unit=None,
            reason=(
                "Git HEAD metadata did not resolve to a syntactically valid object id; "
                "no revision claimed"
            ),
            vcs="git",
            object_id_algorithm=None,
        )
    return {
        "available": True,
        "value": revision,
        "unit": None,
        "reason": None,
        "vcs": "git",
        "object_id_algorithm": "sha1" if len(revision) == 40 else "sha256",
    }


def capture_peak_rss() -> dict[str, Any]:
    """Capture the process-lifetime resident-set high-water mark in bytes."""

    source = "resource.getrusage(resource.RUSAGE_SELF).ru_maxrss"
    try:
        import resource
    except ImportError:
        return _unavailable(
            unit="byte",
            reason="Python resource module is unavailable on this platform",
            source=source,
            scope="current process lifetime high-water mark",
            attribution_note=_PEAK_RSS_ATTRIBUTION,
            native_unit=None,
            conversion_to_bytes=None,
        )
    try:
        raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except (AttributeError, OSError, ValueError) as exc:
        return _unavailable(
            unit="byte",
            reason=f"ru_maxrss could not be sampled: {type(exc).__name__}",
            source=source,
            scope="current process lifetime high-water mark",
            attribution_note=_PEAK_RSS_ATTRIBUTION,
            native_unit=None,
            conversion_to_bytes=None,
        )
    if (
        isinstance(raw, bool)
        or not isinstance(raw, (int, float))
        or not math.isfinite(raw)
        or raw < 0
    ):
        return _unavailable(
            unit="byte",
            reason="ru_maxrss returned an invalid value",
            source=source,
            scope="current process lifetime high-water mark",
            attribution_note=_PEAK_RSS_ATTRIBUTION,
            native_unit=None,
            conversion_to_bytes=None,
        )
    system = platform.system()
    if system == "Linux":
        native_unit, factor = "KiB", 1024
    elif system == "Darwin":
        native_unit, factor = "byte", 1
    else:
        return _unavailable(
            unit="byte",
            reason=(
                "ru_maxrss native unit is not standardized for platform "
                f"{system or 'unknown'}"
            ),
            source=source,
            scope="current process lifetime high-water mark",
            attribution_note=_PEAK_RSS_ATTRIBUTION,
            native_unit="platform-dependent",
            conversion_to_bytes=None,
        )
    return {
        "available": True,
        "value": int(raw * factor),
        "unit": "byte",
        "reason": None,
        "source": source,
        "scope": "current process lifetime high-water mark",
        "attribution_note": _PEAK_RSS_ATTRIBUTION,
        "native_unit": native_unit,
        "conversion_to_bytes": factor,
    }


def _process_cpu_record(process_cpu_seconds: float | None) -> dict[str, Any]:
    if process_cpu_seconds is None:
        return _unavailable(
            unit="s",
            reason="calling runtime did not provide a time.process_time interval",
            source="time.process_time",
            scope="process-wide CPU time over the backend execution interval",
            concurrency_note=(
                "process-wide intervals can include concurrent threads or runs in the same process"
            ),
        )
    if (
        isinstance(process_cpu_seconds, bool)
        or not isinstance(process_cpu_seconds, (int, float))
        or not math.isfinite(process_cpu_seconds)
        or process_cpu_seconds < 0
    ):
        raise ResultError("process_cpu_seconds must be a finite non-negative real number")
    return {
        "available": True,
        "value": float(process_cpu_seconds),
        "unit": "s",
        "reason": None,
        "source": "time.process_time",
        "scope": "process-wide CPU time over the backend execution interval",
        "concurrency_note": (
            "process-wide intervals can include concurrent threads or runs in the same process"
        ),
    }


def _invocation_record(
    entry_point: str,
    argument_summary: Mapping[str, object],
) -> dict[str, Any]:
    if not isinstance(entry_point, str) or not entry_point or any(
        character in entry_point for character in "\r\n\0"
    ):
        raise ResultError("invocation entry_point must be a non-empty single-line string")
    arguments_json = json_ready(
        argument_summary, "provenance.invocation.argument_summary"
    )
    if not isinstance(arguments_json, dict):
        raise ResultError("invocation argument_summary must be a mapping")
    return {
        "entry_point": entry_point,
        "argument_summary": arguments_json,
        "argument_summary_sha256": hashlib.sha256(
            canonical_json_bytes(arguments_json)
        ).hexdigest(),
        "raw_argv_stored": False,
        "absolute_paths_stored": False,
        "privacy_policy": (
            "raw argv and filesystem paths are omitted because they may contain credentials "
            "or private user paths; reproducibility uses the archived config and its SHA-256"
        ),
    }


def collect_provenance(
    config: SimulationConfig,
    extra: Mapping[str, object] | None = None,
    *,
    process_cpu_seconds: float | None = None,
    peak_rss: Mapping[str, object] | None = None,
    invocation_entry_point: str = "airvortex.io.save_run_archive",
    invocation_argument_summary: Mapping[str, object] | None = None,
    source_tree_execution_start: Mapping[str, object] | None = None,
    source_tree_execution_end: Mapping[str, object] | None = None,
    git_revision_execution_start: Mapping[str, object] | None = None,
    git_revision_execution_end: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Collect enough environment identity to interpret a numerical run."""

    extra_json = json_ready(extra or {}, "provenance.extra")
    if not isinstance(extra_json, dict):
        raise ResultError("extra provenance must be a mapping")
    peak_rss_json = json_ready(
        capture_peak_rss() if peak_rss is None else peak_rss,
        "provenance.resources.peak_rss",
    )
    if not isinstance(peak_rss_json, dict):
        raise ResultError("peak_rss must be a mapping")
    argument_summary = (
        {
            "backend": config.backend.value,
            "config_sha256": config_sha256(config),
        }
        if invocation_argument_summary is None
        else invocation_argument_summary
    )
    tree_start_raw = (
        _unavailable_source_tree(
            "calling runtime did not provide an execution-start source-tree snapshot",
            "unavailable",
        )
        if source_tree_execution_start is None
        else source_tree_execution_start
    )
    tree_end_raw = (
        {
            **source_tree_identity(),
            "sample_point": "during archive creation after result serialization",
        }
        if source_tree_execution_end is None
        else source_tree_execution_end
    )
    revision_start_raw = (
        _unavailable_git_revision(
            "calling runtime did not provide an execution-start Git snapshot",
            "unavailable",
        )
        if git_revision_execution_start is None
        else git_revision_execution_start
    )
    revision_end_raw = (
        {
            **git_revision_identity(),
            "sample_point": "during archive creation after result serialization",
        }
        if git_revision_execution_end is None
        else git_revision_execution_end
    )
    tree_start = json_ready(tree_start_raw, "provenance.source_identity.tree.start")
    tree_end = json_ready(tree_end_raw, "provenance.source_identity.tree.end")
    revision_start = json_ready(
        revision_start_raw, "provenance.source_identity.revision.start"
    )
    revision_end = json_ready(
        revision_end_raw, "provenance.source_identity.revision.end"
    )
    if not all(
        isinstance(item, dict)
        for item in (tree_start, tree_end, revision_start, revision_end)
    ):
        raise ResultError("source identity boundary snapshots must be mappings")
    tree_match = (
        tree_start.get("value") == tree_end.get("value")
        if tree_start.get("available") is True and tree_end.get("available") is True
        else None
    )
    revision_match = (
        revision_start.get("value") == revision_end.get("value")
        if revision_start.get("available") is True
        and revision_end.get("available") is True
        else None
    )
    if tree_match is False:
        reproducibility_status = "invalid_source_tree_changed_between_boundaries"
    elif tree_match is None:
        reproducibility_status = "indeterminate_missing_source_tree_boundary"
    elif revision_match is False:
        reproducibility_status = "qualified_content_match_git_revision_changed"
    else:
        reproducibility_status = "qualified_source_tree_boundary_match"
    provenance = {
        "provenance_format_version": PROVENANCE_FORMAT_VERSION,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "airvortex_version": __version__,
        "config_sha256": config_sha256(config),
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "dependencies": {
            "numpy": np.__version__,
            "scipy": _distribution_version("scipy"),
        },
        "source_identity": {
            "source_tree_sha256_start": (
                tree_start.get("value") if tree_start.get("available") else None
            ),
            "source_tree_sha256_end": (
                tree_end.get("value") if tree_end.get("available") else None
            ),
            "unchanged": tree_match,
            "execution_source_identity_valid": tree_match is True,
            "disk_content_tree": {
                "execution_start": tree_start,
                "execution_end": tree_end,
                "boundary_match": tree_match,
            },
            "git_revision": {
                "execution_start": revision_start,
                "execution_end": revision_end,
                "boundary_match": revision_match,
            },
            "loaded_module_bytes_verified": False,
            "reproducibility_status": reproducibility_status,
            "interpretation": (
                "disk boundary hashes identify allow-listed files at two sample points. A match "
                "does not exclude a transient edit-and-restore or prove that already imported "
                "Python module bytes equal either disk snapshot. A mismatch invalidates a single "
                "execution-source identity. Git revisions are reported only when metadata "
                "resolves HEAD and are never synthesized"
            ),
        },
        "invocation": _invocation_record(invocation_entry_point, argument_summary),
        "resources": {
            "process_cpu_time": _process_cpu_record(process_cpu_seconds),
            "peak_resident_set_size": peak_rss_json,
        },
        "extra": extra_json,
    }
    _validate_provenance(provenance)
    return provenance


def _write_json(path: Path, value: object) -> None:
    path.write_text(dumps_json_strict(value, indent=2) + "\n", encoding="utf-8")


def _array_descriptor(npz_key: str, array: np.ndarray) -> dict[str, Any]:
    return {"npz_key": npz_key, "dtype": str(array.dtype), "shape": list(array.shape)}


def _result_index(result: SimulationResult) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    arrays: dict[str, np.ndarray] = {"time_s": np.asarray(result.time_s)}
    observables: dict[str, Any] = {}
    for index, (name, array) in enumerate(sorted(result.observables.items())):
        key = f"observable_{index:04d}"
        value = np.asarray(array)
        arrays[key] = value
        observables[name] = _array_descriptor(key, value)
    fields: dict[str, Any] = {}
    for index, (name, array) in enumerate(sorted(result.fields.items())):
        key = f"field_{index:04d}"
        value = np.asarray(array)
        arrays[key] = value
        fields[name] = _array_descriptor(key, value)
    index_json = {
        "backend": result.backend.value,
        "status": result.status.value,
        "metadata": dict(result.metadata),
        "events": [event.to_dict() for event in result.events],
        "arrays": {
            "time_s": _array_descriptor("time_s", arrays["time_s"]),
            "observables": observables,
            "fields": fields,
        },
    }
    return index_json, arrays


def _write_timeseries_csv(path: Path, result: SimulationResult) -> list[str]:
    scalar_names = sorted(
        name for name, value in result.observables.items() if np.asarray(value).ndim == 1
    )
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["time_s", *scalar_names])
        for index, time_s in enumerate(result.time_s):
            writer.writerow(
                [format(float(time_s), ".17g")]
                + [format(float(result.observables[name][index]), ".17g") for name in scalar_names]
            )
    return scalar_names


def save_run_archive(
    path: str | Path,
    config: SimulationConfig,
    result: SimulationResult,
    *,
    extra_provenance: Mapping[str, object] | None = None,
    process_cpu_seconds: float | None = None,
    peak_rss: Mapping[str, object] | None = None,
    invocation_entry_point: str = "airvortex.io.save_run_archive",
    invocation_argument_summary: Mapping[str, object] | None = None,
    source_tree_execution_start: Mapping[str, object] | None = None,
    source_tree_execution_end: Mapping[str, object] | None = None,
    git_revision_execution_start: Mapping[str, object] | None = None,
    git_revision_execution_end: Mapping[str, object] | None = None,
) -> Path:
    """Create a new archive directory atomically; existing targets are never replaced."""

    if not isinstance(config, SimulationConfig):
        raise TypeError("config must be a SimulationConfig")
    if not isinstance(result, SimulationResult):
        raise TypeError("result must be a SimulationResult")
    if result.backend is not config.backend:
        raise ArchiveError("result backend does not match configuration backend")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(target)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=target.parent))
    try:
        _write_json(staging / "config.json", config.to_dict())
        result_index, arrays = _result_index(result)
        _write_json(staging / "result.json", result_index)
        np.savez_compressed(staging / "arrays.npz", **arrays)
        csv_columns = _write_timeseries_csv(staging / "timeseries.csv", result)
        provenance = collect_provenance(
            config,
            extra_provenance,
            process_cpu_seconds=process_cpu_seconds,
            peak_rss=peak_rss,
            invocation_entry_point=invocation_entry_point,
            invocation_argument_summary=invocation_argument_summary,
            source_tree_execution_start=source_tree_execution_start,
            source_tree_execution_end=source_tree_execution_end,
            git_revision_execution_start=git_revision_execution_start,
            git_revision_execution_end=git_revision_execution_end,
        )
        _write_json(staging / "provenance.json", provenance)
        payload_names = (
            "config.json",
            "result.json",
            "arrays.npz",
            "timeseries.csv",
            "provenance.json",
        )
        files = {
            name: {
                "sha256": sha256_file(staging / name),
                "size_bytes": (staging / name).stat().st_size,
            }
            for name in payload_names
        }
        manifest = {
            "archive_format_version": ARCHIVE_FORMAT_VERSION,
            "problem_id": config.problem_id,
            "backend": result.backend.value,
            "status": result.status.value,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "config_sha256": config_sha256(config),
            "provenance_reproducibility_status": provenance["source_identity"][
                "reproducibility_status"
            ],
            "execution_source_identity_valid": provenance["source_identity"][
                "execution_source_identity_valid"
            ],
            "csv_observable_columns": csv_columns,
            "files": files,
        }
        _write_json(staging / "manifest.json", manifest)
        try:
            os.rename(staging, target)
        except FileExistsError:
            raise
        return target
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _expect_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ArchiveError(f"{label} must be a JSON object")
    return value


def _validate_availability_record(
    raw: object,
    label: str,
    *,
    expected_unit: str | None,
    numeric: bool,
) -> Mapping[str, Any]:
    record = _expect_mapping(raw, label)
    available = record.get("available")
    if not isinstance(available, bool):
        raise ArchiveError(f"{label}.available must be a boolean")
    if record.get("unit") != expected_unit:
        raise ArchiveError(f"{label}.unit must be {expected_unit!r}")
    reason = record.get("reason")
    value = record.get("value")
    if available:
        if reason is not None:
            raise ArchiveError(f"{label}.reason must be null when available")
        if numeric:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ArchiveError(f"{label}.value must be finite and non-negative")
        elif not isinstance(value, str) or not value:
            raise ArchiveError(f"{label}.value must be a non-empty string")
    elif value is not None or not isinstance(reason, str) or not reason:
        raise ArchiveError(
            f"{label} must have null value and a non-empty reason when unavailable"
        )
    return record


def _validate_source_tree_snapshot(
    raw: object, label: str
) -> Mapping[str, Any]:
    content = _validate_availability_record(
        raw,
        label,
        expected_unit=None,
        numeric=False,
    )
    if content.get("algorithm") != "sha256":
        raise ArchiveError(f"{label}.algorithm must be sha256")
    if content.get("available") and not re.fullmatch(
        r"[0-9a-f]{64}", str(content.get("value"))
    ):
        raise ArchiveError(f"{label}.value must be a lowercase SHA-256")
    for name in ("file_count", "total_bytes"):
        value = content.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ArchiveError(f"{label}.{name} must be a non-negative integer")
    if content.get("absolute_root_stored") is not False:
        raise ArchiveError(f"{label} must not store the absolute root")
    if not isinstance(content.get("sample_point"), str) or not content.get(
        "sample_point"
    ):
        raise ArchiveError(f"{label}.sample_point must be a non-empty string")
    return content


def _validate_git_revision_snapshot(
    raw: object, label: str
) -> Mapping[str, Any]:
    revision = _validate_availability_record(
        raw,
        label,
        expected_unit=None,
        numeric=False,
    )
    if revision.get("vcs") != "git":
        raise ArchiveError(f"{label}.vcs must be git")
    if revision.get("available"):
        object_id = revision.get("value")
        if not isinstance(object_id, str) or not _HEX_OBJECT_ID.fullmatch(object_id):
            raise ArchiveError(f"{label}.value must be a Git metadata object id")
        expected_algorithm = "sha1" if len(object_id) == 40 else "sha256"
        if revision.get("object_id_algorithm") != expected_algorithm:
            raise ArchiveError(f"{label} object-id algorithm is inconsistent")
    elif revision.get("object_id_algorithm") is not None:
        raise ArchiveError(f"{label} cannot claim an object-id algorithm")
    if not isinstance(revision.get("sample_point"), str) or not revision.get(
        "sample_point"
    ):
        raise ArchiveError(f"{label}.sample_point must be a non-empty string")
    return revision


def _validate_provenance(raw: object) -> Mapping[str, Any]:
    provenance = _expect_mapping(raw, "provenance")
    if "provenance_format_version" not in provenance:
        legacy_fields = {
            "recorded_at_utc",
            "airvortex_version",
            "config_sha256",
            "python",
            "platform",
            "dependencies",
            "extra",
        }
        if set(provenance) != legacy_fields:
            raise ArchiveError("legacy provenance fields are incomplete or unknown")
        config_digest = provenance.get("config_sha256")
        if not isinstance(config_digest, str) or not re.fullmatch(
            r"[0-9a-f]{64}", config_digest
        ):
            raise ArchiveError("legacy provenance config_sha256 is invalid")
        _expect_mapping(provenance.get("python"), "legacy provenance.python")
        _expect_mapping(provenance.get("platform"), "legacy provenance.platform")
        _expect_mapping(provenance.get("dependencies"), "legacy provenance.dependencies")
        _expect_mapping(provenance.get("extra"), "legacy provenance.extra")
        return provenance
    required = {
        "provenance_format_version",
        "recorded_at_utc",
        "airvortex_version",
        "config_sha256",
        "python",
        "platform",
        "dependencies",
        "source_identity",
        "invocation",
        "resources",
        "extra",
    }
    if set(provenance) != required:
        raise ArchiveError("provenance fields are incomplete or unknown")
    if provenance.get("provenance_format_version") != PROVENANCE_FORMAT_VERSION:
        raise ArchiveError("unsupported provenance format version")
    recorded = provenance.get("recorded_at_utc")
    if not isinstance(recorded, str):
        raise ArchiveError("provenance.recorded_at_utc must be a string")
    try:
        recorded_datetime = datetime.fromisoformat(recorded)
    except ValueError as exc:
        raise ArchiveError("provenance.recorded_at_utc is invalid") from exc
    if recorded_datetime.utcoffset() != timezone.utc.utcoffset(None):
        raise ArchiveError("provenance.recorded_at_utc must use an explicit UTC offset")
    if not isinstance(provenance.get("airvortex_version"), str):
        raise ArchiveError("provenance.airvortex_version must be a string")
    config_digest = provenance.get("config_sha256")
    if not isinstance(config_digest, str) or not re.fullmatch(
        r"[0-9a-f]{64}", config_digest
    ):
        raise ArchiveError("provenance.config_sha256 must be a lowercase SHA-256")
    _expect_mapping(provenance.get("python"), "provenance.python")
    _expect_mapping(provenance.get("platform"), "provenance.platform")
    _expect_mapping(provenance.get("dependencies"), "provenance.dependencies")
    _expect_mapping(provenance.get("extra"), "provenance.extra")

    source = _expect_mapping(
        provenance.get("source_identity"), "provenance.source_identity"
    )
    if set(source) != {
        "source_tree_sha256_start",
        "source_tree_sha256_end",
        "unchanged",
        "execution_source_identity_valid",
        "disk_content_tree",
        "git_revision",
        "loaded_module_bytes_verified",
        "reproducibility_status",
        "interpretation",
    }:
        raise ArchiveError("provenance.source_identity fields are invalid")
    disk_tree = _expect_mapping(
        source.get("disk_content_tree"),
        "provenance.source_identity.disk_content_tree",
    )
    if set(disk_tree) != {"execution_start", "execution_end", "boundary_match"}:
        raise ArchiveError("source disk-content-tree fields are invalid")
    tree_start = _validate_source_tree_snapshot(
        disk_tree.get("execution_start"),
        "provenance.source_identity.disk_content_tree.execution_start",
    )
    tree_end = _validate_source_tree_snapshot(
        disk_tree.get("execution_end"),
        "provenance.source_identity.disk_content_tree.execution_end",
    )
    expected_tree_match = (
        tree_start.get("value") == tree_end.get("value")
        if tree_start.get("available") and tree_end.get("available")
        else None
    )
    if disk_tree.get("boundary_match") is not expected_tree_match:
        raise ArchiveError("source disk-content-tree boundary flag is inconsistent")
    expected_start_digest = (
        tree_start.get("value") if tree_start.get("available") else None
    )
    expected_end_digest = tree_end.get("value") if tree_end.get("available") else None
    if source.get("source_tree_sha256_start") != expected_start_digest:
        raise ArchiveError("source_tree_sha256_start is inconsistent")
    if source.get("source_tree_sha256_end") != expected_end_digest:
        raise ArchiveError("source_tree_sha256_end is inconsistent")
    if source.get("unchanged") is not expected_tree_match:
        raise ArchiveError("source unchanged flag is inconsistent")
    if source.get("execution_source_identity_valid") is not (
        expected_tree_match is True
    ):
        raise ArchiveError("execution source-identity quality gate is inconsistent")

    revisions = _expect_mapping(
        source.get("git_revision"), "provenance.source_identity.git_revision"
    )
    if set(revisions) != {"execution_start", "execution_end", "boundary_match"}:
        raise ArchiveError("source Git-revision fields are invalid")
    revision_start = _validate_git_revision_snapshot(
        revisions.get("execution_start"),
        "provenance.source_identity.git_revision.execution_start",
    )
    revision_end = _validate_git_revision_snapshot(
        revisions.get("execution_end"),
        "provenance.source_identity.git_revision.execution_end",
    )
    expected_revision_match = (
        revision_start.get("value") == revision_end.get("value")
        if revision_start.get("available") and revision_end.get("available")
        else None
    )
    if revisions.get("boundary_match") is not expected_revision_match:
        raise ArchiveError("source Git-revision boundary flag is inconsistent")
    expected_status = (
        "invalid_source_tree_changed_between_boundaries"
        if expected_tree_match is False
        else "indeterminate_missing_source_tree_boundary"
        if expected_tree_match is None
        else "qualified_content_match_git_revision_changed"
        if expected_revision_match is False
        else "qualified_source_tree_boundary_match"
    )
    if source.get("reproducibility_status") != expected_status:
        raise ArchiveError("source reproducibility status is inconsistent")
    if source.get("loaded_module_bytes_verified") is not False:
        raise ArchiveError("loaded module bytes must not be claimed as verified")
    if not isinstance(source.get("interpretation"), str) or not source.get(
        "interpretation"
    ):
        raise ArchiveError("source identity interpretation is missing")

    invocation = _expect_mapping(
        provenance.get("invocation"), "provenance.invocation"
    )
    if set(invocation) != {
        "entry_point",
        "argument_summary",
        "argument_summary_sha256",
        "raw_argv_stored",
        "absolute_paths_stored",
        "privacy_policy",
    }:
        raise ArchiveError("provenance.invocation fields are invalid")
    entry_point = invocation.get("entry_point")
    if not isinstance(entry_point, str) or not entry_point or any(
        character in entry_point for character in "\r\n\0"
    ):
        raise ArchiveError("provenance invocation entry point is invalid")
    argument_summary = _expect_mapping(
        invocation.get("argument_summary"), "provenance.invocation.argument_summary"
    )
    expected_summary_hash = hashlib.sha256(
        canonical_json_bytes(argument_summary)
    ).hexdigest()
    if invocation.get("argument_summary_sha256") != expected_summary_hash:
        raise ArchiveError("provenance invocation argument-summary SHA-256 mismatch")
    if invocation.get("raw_argv_stored") is not False:
        raise ArchiveError("raw argv must not be stored in provenance")
    if invocation.get("absolute_paths_stored") is not False:
        raise ArchiveError("absolute invocation paths must not be stored in provenance")
    if not isinstance(invocation.get("privacy_policy"), str):
        raise ArchiveError("provenance invocation privacy policy is missing")

    resources = _expect_mapping(provenance.get("resources"), "provenance.resources")
    if set(resources) != {"process_cpu_time", "peak_resident_set_size"}:
        raise ArchiveError("provenance resource fields are invalid")
    cpu = _validate_availability_record(
        resources.get("process_cpu_time"),
        "provenance.resources.process_cpu_time",
        expected_unit="s",
        numeric=True,
    )
    if cpu.get("source") != "time.process_time":
        raise ArchiveError("process CPU source must be time.process_time")
    peak = _validate_availability_record(
        resources.get("peak_resident_set_size"),
        "provenance.resources.peak_resident_set_size",
        expected_unit="byte",
        numeric=True,
    )
    if (
        peak.get("source")
        != "resource.getrusage(resource.RUSAGE_SELF).ru_maxrss"
    ):
        raise ArchiveError("peak RSS source is invalid")
    if peak.get("attribution_note") != _PEAK_RSS_ATTRIBUTION:
        raise ArchiveError("peak RSS attribution note is invalid")
    if peak.get("available"):
        conversion = peak.get("conversion_to_bytes")
        if isinstance(conversion, bool) or conversion not in {1, 1024}:
            raise ArchiveError("peak RSS byte conversion is invalid")
        if peak.get("native_unit") not in {"byte", "KiB"}:
            raise ArchiveError("peak RSS native unit is invalid")
    return provenance


def _verify_manifest(root: Path, manifest: Mapping[str, Any]) -> None:
    if manifest.get("archive_format_version") != ARCHIVE_FORMAT_VERSION:
        raise ArchiveError("unsupported archive format version")
    files = _expect_mapping(manifest.get("files"), "manifest.files")
    expected_files = {
        "config.json",
        "result.json",
        "arrays.npz",
        "timeseries.csv",
        "provenance.json",
    }
    if set(files) != expected_files:
        raise ArchiveError("manifest file set is incomplete or contains unknown entries")
    for name, raw_descriptor in files.items():
        descriptor = _expect_mapping(raw_descriptor, f"manifest.files.{name}")
        file_path = root / name
        if not file_path.is_file():
            raise ArchiveError(f"archive payload is missing: {name}")
        expected_hash = descriptor.get("sha256")
        expected_size = descriptor.get("size_bytes")
        if not isinstance(expected_hash, str) or len(expected_hash) != 64:
            raise ArchiveError(f"manifest hash is invalid for {name}")
        if isinstance(expected_size, bool) or not isinstance(expected_size, int):
            raise ArchiveError(f"manifest size is invalid for {name}")
        if file_path.stat().st_size != expected_size:
            raise ArchiveError(f"archive size mismatch for {name}")
        if sha256_file(file_path) != expected_hash:
            raise ArchiveError(f"archive SHA-256 mismatch for {name}")


def _load_descriptor(
    archive: np.lib.npyio.NpzFile, descriptor_raw: object, label: str
) -> np.ndarray:
    descriptor = _expect_mapping(descriptor_raw, label)
    allowed = {"npz_key", "dtype", "shape"}
    if set(descriptor) != allowed:
        raise ArchiveError(f"{label} descriptor fields are invalid")
    key = descriptor["npz_key"]
    if not isinstance(key, str) or key not in archive.files:
        raise ArchiveError(f"{label} references a missing NPZ array")
    array = archive[key]
    shape = descriptor["shape"]
    dtype = descriptor["dtype"]
    if not isinstance(shape, list) or any(
        isinstance(item, bool) or not isinstance(item, int) for item in shape
    ):
        raise ArchiveError(f"{label} has invalid shape metadata")
    if not isinstance(dtype, str) or list(array.shape) != shape or str(array.dtype) != dtype:
        raise ArchiveError(f"{label} NPZ metadata mismatch")
    return array


def load_run_archive(path: str | Path, *, verify: bool = True) -> ArchivedRun:
    root = Path(path)
    if not root.is_dir():
        raise ArchiveError(f"run archive is not a directory: {root}")
    manifest = _expect_mapping(read_json_strict(root / "manifest.json"), "manifest")
    if verify:
        _verify_manifest(root, manifest)
    config = load_config_json(root / "config.json")
    if manifest.get("config_sha256") != config_sha256(config):
        raise ArchiveError("manifest configuration hash mismatch")
    result_json = _expect_mapping(read_json_strict(root / "result.json"), "result")
    required = {"backend", "status", "metadata", "events", "arrays"}
    if set(result_json) != required:
        raise ArchiveError("result.json fields are incomplete or unknown")
    arrays_index = _expect_mapping(result_json["arrays"], "result.arrays")
    if set(arrays_index) != {"time_s", "observables", "fields"}:
        raise ArchiveError("result array index is incomplete or unknown")
    try:
        with np.load(root / "arrays.npz", allow_pickle=False) as archive:
            time_s = _load_descriptor(archive, arrays_index["time_s"], "time_s")
            observations_raw = _expect_mapping(
                arrays_index["observables"], "result.arrays.observables"
            )
            fields_raw = _expect_mapping(arrays_index["fields"], "result.arrays.fields")
            observables = {
                name: _load_descriptor(archive, descriptor, f"observable {name}")
                for name, descriptor in observations_raw.items()
            }
            fields = {
                name: _load_descriptor(archive, descriptor, f"field {name}")
                for name, descriptor in fields_raw.items()
            }
            referenced = {
                arrays_index["time_s"]["npz_key"],
                *(descriptor["npz_key"] for descriptor in observations_raw.values()),
                *(descriptor["npz_key"] for descriptor in fields_raw.values()),
            }
            if set(archive.files) != referenced:
                raise ArchiveError("NPZ contains unreferenced or missing arrays")
    except (OSError, ValueError, KeyError) as exc:
        if isinstance(exc, ArchiveError):
            raise
        raise ArchiveError(f"cannot load arrays.npz: {exc}") from exc
    try:
        backend = BackendMode(result_json["backend"])
        status = RunStatus(result_json["status"])
    except (TypeError, ValueError) as exc:
        raise ArchiveError(f"invalid backend or status: {exc}") from exc
    events_raw = result_json["events"]
    if not isinstance(events_raw, list):
        raise ArchiveError("result events must be a list")
    result = SimulationResult(
        backend=backend,
        status=status,
        time_s=time_s,
        observables=observables,
        fields=fields,
        events=tuple(Event.from_dict(item) for item in events_raw),
        metadata=result_json["metadata"],
    )
    if result.backend is not config.backend:
        raise ArchiveError("result backend does not match configuration backend")
    provenance = _validate_provenance(read_json_strict(root / "provenance.json"))
    if provenance.get("config_sha256") != config_sha256(config):
        raise ArchiveError("provenance configuration hash mismatch")
    if provenance.get("provenance_format_version") == PROVENANCE_FORMAT_VERSION:
        source_identity = _expect_mapping(
            provenance.get("source_identity"), "provenance.source_identity"
        )
        if manifest.get("provenance_reproducibility_status") != source_identity.get(
            "reproducibility_status"
        ):
            raise ArchiveError("manifest/provenance reproducibility status mismatch")
        if manifest.get("execution_source_identity_valid") is not source_identity.get(
            "execution_source_identity_valid"
        ):
            raise ArchiveError("manifest/provenance source-quality gate mismatch")
    return ArchivedRun(root, config, result, manifest, provenance)
