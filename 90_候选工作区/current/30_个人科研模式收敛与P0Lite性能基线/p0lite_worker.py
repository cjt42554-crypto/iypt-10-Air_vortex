"""Fresh-process worker for P0-Lite runs and zero-step capability probes."""

from __future__ import annotations

import argparse
import os
import resource
import sys
import time
import traceback
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from p0lite_common import (
    EXPECTED_REGISTERED_TIMES,
    P0LiteError,
    archive_numerical_payload,
    canonical_json_bytes,
    strict_json,
    utc_now,
    write_json,
)


class WorkerFailure(P0LiteError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


def _formal_config(path: Path) -> tuple[Any, dict[str, Any]]:
    payload = strict_json(path)
    if not isinstance(payload, dict):
        raise WorkerFailure("CONFIG_IDENTITY_FAILURE", "effective config is not an object")
    try:
        from airvortex.core import SimulationConfig

        config = SimulationConfig.from_dict(payload)
    except Exception as exc:
        raise WorkerFailure(
            "CONFIG_IDENTITY_FAILURE", f"formal config construction failed: {exc}"
        ) from exc
    if config.to_dict() != payload:
        raise WorkerFailure(
            "CONFIG_IDENTITY_FAILURE", "formal SimulationConfig round-trip differs"
        )
    return config, payload


@contextmanager
def _nvtx(label: str) -> Iterator[None]:
    pushed = False
    try:
        import cupy as cp  # type: ignore[import-untyped]

        cp.cuda.nvtx.RangePush(label)
        pushed = True
    except (ImportError, AttributeError, OSError, RuntimeError):
        pushed = False
    try:
        yield
    finally:
        if pushed:
            import cupy as cp

            cp.cuda.nvtx.RangePop()


def _cuda_memory() -> dict[str, Any]:
    try:
        import cupy as cp

        free, total = cp.cuda.runtime.memGetInfo()
        pool = cp.get_default_memory_pool()
        pinned = cp.get_default_pinned_memory_pool()
        return {
            "available": True,
            "cuda_runtime_version": int(cp.cuda.runtime.runtimeGetVersion()),
            "cuda_driver_version": int(cp.cuda.runtime.driverGetVersion()),
            "device_free_bytes": int(free),
            "device_total_bytes": int(total),
            "device_used_bytes": int(total - free),
            "cupy_pool_used_bytes": int(pool.used_bytes()),
            "cupy_pool_total_bytes": int(pool.total_bytes()),
            "cupy_pinned_free_block_count": int(pinned.n_free_blocks()),
        }
    except (ImportError, AttributeError, OSError, RuntimeError, ValueError) as exc:
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}


def _process_memory() -> dict[str, Any]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines():
            key, separator, rest = line.partition(":")
            if separator and key in {"VmRSS", "VmHWM", "VmPeak", "VmSize"}:
                values[f"{key}_bytes"] = int(rest.strip().split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    values["resource_maxrss_bytes"] = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024
    return values


def _synchronize() -> None:
    try:
        import cupy as cp

        cp.cuda.Device().synchronize()
    except Exception as exc:
        raise WorkerFailure("CUDA_SYNCHRONIZE_FAILURE", str(exc)) from exc


def run_attempt(attempt: Path) -> dict[str, Any]:
    if not attempt.is_dir() or attempt.is_symlink():
        raise WorkerFailure("ATTEMPT_INVALID", "attempt directory is unavailable")
    write_json(
        attempt / "worker_identity.json",
        {"pid": os.getpid(), "started_at_utc": utc_now()},
    )
    config, payload = _formal_config(attempt / "effective_config.json")
    archive = attempt / "archive"
    if archive.exists() or archive.is_symlink():
        raise WorkerFailure("ARCHIVE_TARGET_EXISTS", "archive target must not exist")

    from airvortex.core import RunStatus
    from airvortex.io import load_run_archive, save_run_archive
    from airvortex.runtime import discover_backends

    timings: dict[str, float] = {}
    memory_before = {"process": _process_memory(), "cuda": _cuda_memory()}
    process_cpu_started = time.process_time()

    started = time.perf_counter()
    with _nvtx("P0LITE/backend_discovery_and_construction"):
        backend = discover_backends().create(config.backend)
    timings["backend_discovery_and_construction_s"] = time.perf_counter() - started

    started = time.perf_counter()
    with _nvtx("P0LITE/backend_run_including_recording"):
        result = backend.run(config)
    timings["backend_run_including_recording_s"] = time.perf_counter() - started
    if result.status is not RunStatus.COMPLETED:
        raise WorkerFailure("QUALITY_FAILURE", f"backend status is {result.status.value}")

    started = time.perf_counter()
    with _nvtx("P0LITE/archive_save"):
        save_run_archive(
            archive,
            config,
            result,
            extra_provenance={"p0lite": {"worker_pid": os.getpid()}},
            process_cpu_seconds=max(0.0, time.process_time() - process_cpu_started),
            invocation_entry_point="p0lite_worker.run_attempt",
            invocation_argument_summary={"effective_config_sha256": __import__("hashlib").sha256(canonical_json_bytes(payload)).hexdigest()},
        )
    timings["archive_save_s"] = time.perf_counter() - started

    started = time.perf_counter()
    with _nvtx("P0LITE/archive_strict_reload"):
        try:
            archived = load_run_archive(archive, verify=True)
        except Exception as exc:
            raise WorkerFailure("ARCHIVE_RELOAD_FAILURE", str(exc)) from exc
    timings["archive_strict_reload_s"] = time.perf_counter() - started

    diagnostics = archived.result.metadata.get("diagnostics")
    if not isinstance(diagnostics, Mapping):
        raise WorkerFailure("QUALITY_FAILURE", "result diagnostics are missing")
    numerical_gate = diagnostics.get("numerical_quality_gate")
    accelerator = diagnostics.get("pressure_accelerator")
    if not isinstance(numerical_gate, Mapping) or numerical_gate.get("pass") is not True:
        raise WorkerFailure("QUALITY_FAILURE", "formal numerical quality gate failed")
    if not isinstance(accelerator, Mapping) or accelerator.get("selected") != "cuda":
        raise WorkerFailure("CUDA_FALLBACK", "formal pressure accelerator is not CUDA")
    if accelerator.get("fallback") is not False:
        raise WorkerFailure("CUDA_FALLBACK", "formal pressure accelerator fell back")

    try:
        import numpy as np

        registered_exact = np.array_equal(
            archived.result.time_s,
            np.asarray(EXPECTED_REGISTERED_TIMES, dtype=np.float64),
        )
    except Exception as exc:
        raise WorkerFailure("QUALITY_FAILURE", f"cannot verify registered times: {exc}") from exc
    if not registered_exact:
        raise WorkerFailure("QUALITY_FAILURE", "registered times differ from frozen schedule")

    started = time.perf_counter()
    with _nvtx("P0LITE/canonical_numerical_payload"):
        numerical_payload = archive_numerical_payload(archive)
        write_json(attempt / "numerical_payload.json", numerical_payload)
    timings["canonical_numerical_payload_s"] = time.perf_counter() - started

    started = time.perf_counter()
    _synchronize()
    timings["final_cuda_synchronize_s"] = time.perf_counter() - started
    memory_after = {"process": _process_memory(), "cuda": _cuda_memory()}
    write_json(attempt / "memory.json", {"before": memory_before, "after": memory_after})
    write_json(attempt / "worker_timing.json", timings)

    gates = {
        "formal_config_roundtrip_exact": config.to_dict() == payload,
        "result_completed": archived.result.status is RunStatus.COMPLETED,
        "numerical_quality_gate_pass": numerical_gate.get("pass") is True,
        "cuda_selected": accelerator.get("selected") == "cuda",
        "cuda_no_fallback": accelerator.get("fallback") is False,
        "registered_times_exact": registered_exact,
        "archive_strict_reload": True,
        "canonical_payload_complete": bool(numerical_payload.get("sha256")),
    }
    quality = {
        "evaluated": True,
        "gate_pass": all(gates.values()),
        "gates": gates,
        "backend_id": archived.result.metadata.get("backend_id"),
        "pressure_accelerator": dict(accelerator),
        "numerical_quality_gate": dict(numerical_gate),
        "completed_steps": diagnostics.get("completed_steps"),
        "simulated_duration_s": diagnostics.get("simulated_duration_s"),
        "canonical_numerical_sha256": numerical_payload["sha256"],
        "registered_times_s": archived.result.time_s.tolist(),
    }
    write_json(attempt / "quality.json", quality)
    if not quality["gate_pass"]:
        raise WorkerFailure("QUALITY_FAILURE", "one or more final quality gates failed")
    return quality


def zero_step(config_path: Path, output: Path) -> dict[str, Any]:
    config, payload = _formal_config(config_path)
    from airvortex.backends.high_fidelity_3d import HighFidelityBackend
    from airvortex.hf3d.pressure import make_pressure_projection

    backend = HighFidelityBackend()
    grid = backend.make_grid(config)
    projection, accelerator = make_pressure_projection(config, grid)
    state = backend.initialise_state(config, grid)
    initial_volume = backend._initial_liquid_volume(config, grid)
    zero = backend._zero_step_diagnostics(state, config, grid, initial_volume)
    _synchronize()
    if accelerator.get("selected") != "cuda" or accelerator.get("fallback") is not False:
        raise WorkerFailure("CUDA_FALLBACK", "zero-step probe did not select actual CUDA")
    result = {
        "pid": os.getpid(),
        "formal_roundtrip_exact": config.to_dict() == payload,
        "cuda_selected": accelerator.get("selected"),
        "cuda_fallback": accelerator.get("fallback"),
        "configured_cells_x_y_z": [grid.nx, grid.ny, grid.nz],
        "active_cells": grid.active_cell_count,
        "initial_volume_m3": initial_volume,
        "initial_divergence_s_inv": zero.divergence_after_s_inv,
        "hf3d_time_steps_advanced": int(state.step),
        "cuda_memory": _cuda_memory(),
    }
    write_json(output, result)
    del projection, state, grid
    try:
        import cupy as cp

        cp.get_default_memory_pool().free_all_blocks()
        cp.get_default_pinned_memory_pool().free_all_blocks()
    except (ImportError, AttributeError, OSError, RuntimeError):
        print("warning: CuPy pools could not be released", file=sys.stderr)
    return result


def nsys_probe(output: Path) -> dict[str, Any]:
    import cupy as cp

    cp.cuda.nvtx.RangePush("P0LITE/minimal_nsys_probe")
    try:
        host = __import__("numpy").arange(4096, dtype="float64")
        device = cp.asarray(host)
        result = float(cp.asnumpy(device * 2.0).sum())
        cp.cuda.Device().synchronize()
    finally:
        cp.cuda.nvtx.RangePop()
    payload = {"pid": os.getpid(), "result": result, "completed": True}
    write_json(output, payload)
    return payload


def config_probe(config_path: Path, output: Path) -> dict[str, Any]:
    config, payload = _formal_config(config_path)
    result = {"pid": os.getpid(), "roundtrip_exact": config.to_dict() == payload}
    write_json(output, result)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("run", "zero-step", "nsys-probe", "config-probe"), required=True)
    parser.add_argument("--attempt", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.mode == "run":
            if args.attempt is None:
                raise P0LiteError("--attempt is required for run mode")
            run_attempt(args.attempt.resolve())
        elif args.mode == "zero-step":
            if args.config is None or args.output is None:
                raise P0LiteError("--config and --output are required for zero-step")
            zero_step(args.config.resolve(), args.output.resolve())
        elif args.mode == "nsys-probe":
            if args.output is None:
                raise P0LiteError("--output is required for nsys-probe")
            nsys_probe(args.output.resolve())
        else:
            if args.config is None or args.output is None:
                raise P0LiteError("--config and --output are required for config-probe")
            config_probe(args.config.resolve(), args.output.resolve())
    except BaseException as exc:  # noqa: BLE001 - retain KeyboardInterrupt evidence
        category = exc.category if isinstance(exc, WorkerFailure) else type(exc).__name__
        failure = {
            "failed_at_utc": utc_now(),
            "category": category,
            "exception_type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
        target = args.attempt / "worker_error.json" if args.attempt is not None else args.output
        if target is not None:
            try:
                write_json(target.resolve(), failure)
                if args.attempt is not None:
                    write_json(
                        args.attempt.resolve() / "quality.json",
                        {"evaluated": True, "gate_pass": False, "failure": failure},
                    )
            except Exception as write_exc:  # noqa: BLE001 - best-effort failure evidence
                print(f"warning: could not preserve worker failure evidence: {write_exc}", file=sys.stderr)
        print(f"P0-Lite worker failed [{category}]: {exc}", file=sys.stderr)
        return 130 if isinstance(exc, KeyboardInterrupt) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
