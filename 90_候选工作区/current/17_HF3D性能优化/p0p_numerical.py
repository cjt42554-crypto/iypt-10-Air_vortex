"""Canonical numerical-payload identities independent of profiler metadata."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from p0_performance_contract import (
    P0PerformanceError,
    canonical_json_bytes,
    display_path,
    sha256_file,
    strict_json,
)

NUMERICAL_HASH_DOMAIN = b"hf3d-p0p-canonical-numerical-payload-v1\0"
EXCLUDED_ARCHIVE_FILES = ("manifest.json", "provenance.json", "timeseries.csv")


def _framed_update(digest: Any, label: bytes, payload: bytes) -> None:
    digest.update(len(label).to_bytes(8, "big"))
    digest.update(label)
    digest.update(len(payload).to_bytes(8, "big"))
    digest.update(payload)


def canonical_arrays_identity(arrays: Mapping[str, Any]) -> dict[str, Any]:
    """Hash names, exact dtype/shape, and C-order bytes for finite arrays."""

    import numpy as np

    digest = hashlib.sha256()
    digest.update(NUMERICAL_HASH_DOMAIN)
    records: list[dict[str, Any]] = []
    for name in sorted(arrays):
        value = np.asarray(arrays[name])
        if value.dtype.hasobject:
            raise P0PerformanceError(f"object array is prohibited: {name}")
        if np.issubdtype(value.dtype, np.number) and not bool(np.isfinite(value).all()):
            raise P0PerformanceError(f"non-finite numerical array: {name}")
        contiguous = np.ascontiguousarray(value)
        metadata = {
            "name": name,
            "dtype": contiguous.dtype.str,
            "shape": list(contiguous.shape),
            "nbytes": int(contiguous.nbytes),
        }
        _framed_update(
            digest, f"metadata:{name}".encode(), canonical_json_bytes(metadata)
        )
        _framed_update(digest, f"bytes:{name}".encode(), contiguous.tobytes(order="C"))
        records.append(
            {
                **metadata,
                "bytes_sha256": hashlib.sha256(
                    contiguous.tobytes(order="C")
                ).hexdigest(),
            }
        )
    return {
        "algorithm": "sha256",
        "sha256": digest.hexdigest(),
        "array_count": len(records),
        "arrays": records,
    }


def archive_numerical_payload(
    archive: Path, *, logical_archive_path: Path | None = None
) -> dict[str, Any]:
    """Hash only configuration, result semantics, and exact numerical arrays."""

    import numpy as np

    resolved = archive.resolve()
    if not resolved.is_dir() or resolved.is_symlink():
        raise P0PerformanceError("archive must be a real directory")
    config_path = resolved / "config.json"
    result_path = resolved / "result.json"
    arrays_path = resolved / "arrays.npz"
    config = strict_json(config_path)
    result = strict_json(result_path)
    if not arrays_path.is_file() or arrays_path.is_symlink():
        raise P0PerformanceError("archive arrays.npz is unavailable")
    with np.load(arrays_path, allow_pickle=False) as loaded:
        arrays = {name: loaded[name] for name in loaded.files}
    arrays_identity = canonical_arrays_identity(arrays)
    digest = hashlib.sha256()
    digest.update(NUMERICAL_HASH_DOMAIN)
    _framed_update(digest, b"config.json", canonical_json_bytes(config))
    _framed_update(digest, b"result.json", canonical_json_bytes(result))
    _framed_update(
        digest,
        b"arrays.identity",
        arrays_identity["sha256"].encode("ascii"),
    )
    frame = arrays.get("field__frame_time_s")
    if frame is None:
        frame = arrays.get("time_s")
    frame_uint64 = (
        np.asarray(frame, dtype=np.float64).view(np.uint64).tolist()
        if frame is not None
        else None
    )
    return {
        "schema_version": 1,
        "algorithm": "sha256",
        "sha256": digest.hexdigest(),
        "archive_path": display_path(
            logical_archive_path.absolute()
            if logical_archive_path is not None
            else resolved
        ),
        "config_canonical_sha256": hashlib.sha256(
            canonical_json_bytes(config)
        ).hexdigest(),
        "result_canonical_sha256": hashlib.sha256(
            canonical_json_bytes(result)
        ).hexdigest(),
        "arrays": arrays_identity,
        "registered_time_uint64": frame_uint64,
        "excluded_performance_metadata": list(EXCLUDED_ARCHIVE_FILES),
        "source_files": {
            "config.json": sha256_file(config_path),
            "result.json": sha256_file(result_path),
            "arrays.npz": sha256_file(arrays_path),
        },
    }


def compare_payloads(payloads: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not payloads:
        raise P0PerformanceError("at least one numerical payload is required")
    hashes = [str(payload.get("sha256")) for payload in payloads]
    configs = [str(payload.get("config_canonical_sha256")) for payload in payloads]
    times = [payload.get("registered_time_uint64") for payload in payloads]
    gates = {
        "numerical_payload_hash_identical": len(set(hashes)) == 1,
        "configuration_hash_identical": len(set(configs)) == 1,
        "registered_times_bitwise_identical": all(value == times[0] for value in times),
    }
    return {
        "gate_pass": all(gates.values()),
        "gates": gates,
        "sample_count": len(payloads),
        "hashes": hashes,
        "configuration_hashes": configs,
        "registered_time_uint64": times,
    }


__all__ = [
    "EXCLUDED_ARCHIVE_FILES",
    "NUMERICAL_HASH_DOMAIN",
    "archive_numerical_payload",
    "canonical_arrays_identity",
    "compare_payloads",
]
