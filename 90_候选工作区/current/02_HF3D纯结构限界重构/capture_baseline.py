#!/usr/bin/env python3
"""Capture a compact, strict HF3D characterization result."""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any, Mapping


HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parents[2]
FORMAL_ROOT = REPOSITORY_ROOT / "02_模拟程序"
FORMAL_SOURCE = FORMAL_ROOT / "src"
if str(FORMAL_SOURCE) not in sys.path:
    sys.path.insert(0, str(FORMAL_SOURCE))

import numpy as np
import scipy

from airvortex.backends.high_fidelity_3d import HighFidelityBackend
from airvortex.core import Progress
from airvortex.core.protocols import json_ready
from airvortex.io.archive import source_tree_identity
from airvortex.io.config_io import config_sha256, load_config_json


FORMAT_VERSION = "HF3D_REFACTOR_CHARACTERIZATION_V1"


def array_sha256(value: np.ndarray) -> str:
    """Hash dtype, shape, and C-order payload so identity is unambiguous."""

    array = np.asarray(value)
    digest = hashlib.sha256()
    digest.update(b"hf3d-refactor-array-v1\0")
    digest.update(array.dtype.str.encode("ascii"))
    digest.update(b"\0")
    digest.update(json.dumps(list(array.shape), separators=(",", ":")).encode("ascii"))
    digest.update(b"\0")
    digest.update(np.ascontiguousarray(array).tobytes(order="C"))
    return digest.hexdigest()


def _git_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _optional_cupy_environment() -> dict[str, object]:
    try:
        import cupy
    except (ImportError, OSError) as exc:
        return {
            "available": False,
            "version": None,
            "device_count": 0,
            "detail": f"{type(exc).__name__}: {exc}",
        }
    try:
        device_count = int(cupy.cuda.runtime.getDeviceCount())
        device_name = (
            cupy.cuda.runtime.getDeviceProperties(0)["name"].decode("utf-8")
            if device_count
            else None
        )
    except (RuntimeError, OSError) as exc:
        return {
            "available": False,
            "version": cupy.__version__,
            "device_count": 0,
            "detail": f"{type(exc).__name__}: {exc}",
        }
    return {
        "available": device_count > 0,
        "version": cupy.__version__,
        "device_count": device_count,
        "device_name": device_name,
        "detail": "CUDA runtime probe completed",
    }


def _progress_to_dict(progress: Progress) -> dict[str, object]:
    return {
        "fraction": progress.fraction,
        "simulated_time_s": progress.simulated_time_s,
        "step": progress.step,
        "stage": progress.stage,
        "message": progress.message,
        "metrics": dict(progress.metrics),
    }


def _json_ready_mapping(value: Mapping[str, object]) -> dict[str, object]:
    """Use the formal result normalizer to unwrap nested mapping proxies."""

    normalized = json_ready(value)
    if not isinstance(normalized, dict):
        raise TypeError("expected a JSON-ready mapping")
    return normalized


def capture(
    *,
    config_path: Path,
    requested_device: str,
    label: str,
    manifest_path: Path,
    arrays_path: Path,
) -> dict[str, object]:
    config = load_config_json(config_path)
    config = replace(
        config,
        high_fidelity=replace(
            config.high_fidelity,
            pressure_solver_device=requested_device,
        ),
    )
    source_start = source_tree_identity(FORMAL_ROOT)
    progress_messages: list[Progress] = []
    result = HighFidelityBackend().run(config, progress=progress_messages.append)
    source_end = source_tree_identity(FORMAL_ROOT)
    if source_start != source_end:
        raise RuntimeError("formal source tree changed while capturing baseline")

    arrays: dict[str, np.ndarray] = {"time_s": np.asarray(result.time_s)}
    array_records: list[dict[str, object]] = [
        {
            "storage_key": "time_s",
            "category": "time_s",
            "logical_name": "time_s",
        }
    ]
    for category, values in (
        ("observable", result.observables),
        ("field", result.fields),
    ):
        for logical_name, value in values.items():
            storage_key = f"{category}::{logical_name}"
            arrays[storage_key] = np.asarray(value)
            array_records.append(
                {
                    "storage_key": storage_key,
                    "category": category,
                    "logical_name": logical_name,
                }
            )
    for record in array_records:
        array = arrays[str(record["storage_key"])]
        record.update(
            {
                "dtype": array.dtype.str,
                "shape": list(array.shape),
                "sha256": array_sha256(array),
            }
        )

    arrays_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(arrays_path, **arrays)
    pressure_record = result.metadata["diagnostics"]["pressure_accelerator"]
    manifest: dict[str, object] = {
        "format_version": FORMAT_VERSION,
        "capture_label": label,
        "requested_device": requested_device,
        "selected_device": pressure_record["selected"],
        "effective_config": config.to_dict(),
        "effective_config_sha256": config_sha256(config),
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "cupy": _optional_cupy_environment(),
            "platform": platform.platform(),
        },
        "provenance": {
            "git_head": _git_head(),
            "source_tree": source_start,
        },
        "array_archive": arrays_path.name,
        "arrays": array_records,
        "result": {
            "backend": result.backend.value,
            "status": result.status.value,
            "observable_keys": list(result.observables),
            "field_keys": list(result.fields),
            "events": [event.to_dict() for event in result.events],
            "metadata": _json_ready_mapping(result.metadata),
        },
        "progress": [_progress_to_dict(item) for item in progress_messages],
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=HERE / "baseline_config.json",
    )
    parser.add_argument("--device", choices=("cpu", "cuda"), required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--arrays", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = capture(
        config_path=args.config.resolve(),
        requested_device=args.device,
        label=args.label,
        manifest_path=args.manifest.resolve(),
        arrays_path=args.arrays.resolve(),
    )
    print(
        json.dumps(
            {
                "manifest": str(args.manifest),
                "arrays": str(args.arrays),
                "selected_device": manifest["selected_device"],
                "array_count": len(manifest["arrays"]),
                "completed_steps": manifest["result"]["metadata"]["diagnostics"][
                    "completed_steps"
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
