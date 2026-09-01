#!/usr/bin/env python3
"""Generate or verify the deterministic P0-P2 contract and effective configs."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from p0p2_contract import (
    BENCHMARKS,
    FORMAL_ROOT,
    FORMAL_SOURCE,
    FORMAL_SOURCE_SHA256,
    P0P2Error,
    generated_outputs,
    pretty_json_bytes,
    relative,
    verify_generated_outputs,
)

if str(FORMAL_SOURCE) not in sys.path:
    sys.path.insert(0, str(FORMAL_SOURCE))

from airvortex.core import SimulationConfig
from airvortex.io.archive import source_tree_identity
from p0p2_contract import effective_config


def _write_exclusive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o644)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise P0P2Error(f"short write while creating {relative(path)}")
            view = view[written:]
        os.fchmod(descriptor, 0o644)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def formal_round_trip_report() -> dict[str, Any]:
    source_identity = source_tree_identity(FORMAL_ROOT)
    gates: dict[str, bool] = {
        "formal_source_available": source_identity.get("available") is True,
        "formal_source_tree_exact": source_identity.get("value")
        == FORMAL_SOURCE_SHA256,
    }
    records: dict[str, Any] = {}
    for benchmark in BENCHMARKS:
        payload, identity = effective_config(benchmark)
        config = SimulationConfig.from_dict(payload)
        round_trip = config.to_dict()
        record_gates = {
            "formal_from_dict_pass": isinstance(config, SimulationConfig),
            "exact_to_dict_round_trip": round_trip == payload,
            "duration_ramp_equal": (
                config.run.duration_s == config.drive.ramp_duration_s == 0.005
            ),
            "output_interval_exact": config.run.output_interval_s == 0.001,
            "actual_speed_schedule_empty": (
                config.drive.actual_speed_schedule_time_s == ()
                and config.drive.actual_speed_schedule_rpm == ()
            ),
            "pressure_solver_cuda_requested": (
                config.high_fidelity.pressure_solver_device == "cuda"
            ),
        }
        records[benchmark] = {
            "gate_pass": all(record_gates.values()),
            "gates": record_gates,
            "identity": identity,
        }
        gates[f"{benchmark}_formal_round_trip"] = records[benchmark]["gate_pass"]
    return {
        "schema_version": 1,
        "status": (
            "HF3D_P0P2_FORMAL_CONFIG_ROUND_TRIP_PASS"
            if all(gates.values())
            else "HF3D_P0P2_FORMAL_CONFIG_ROUND_TRIP_FAILED_STOPPED"
        ),
        "gate_pass": all(gates.values()),
        "gates": gates,
        "formal_source_identity": source_identity,
        "benchmarks": records,
    }


def write_outputs() -> dict[str, Any]:
    report = formal_round_trip_report()
    if not report["gate_pass"]:
        raise P0P2Error("formal config round-trip failed before contract publication")
    outputs = generated_outputs()
    for path in outputs:
        if path.exists() or path.is_symlink():
            raise P0P2Error(f"generated output already exists: {relative(path)}")
    for path, payload in outputs.items():
        _write_exclusive(path, payload)
    verification = verify_generated_outputs()
    if not verification["gate_pass"]:
        raise P0P2Error("generated P0-P2 outputs are not byte-identical")
    return {
        "schema_version": 1,
        "status": "HF3D_P0P2_CONTRACT_AND_EFFECTIVE_CONFIGS_GENERATED",
        "gate_pass": True,
        "formal_round_trip": report,
        "generated_outputs": verification,
    }


def check_outputs() -> dict[str, Any]:
    report = formal_round_trip_report()
    verification = verify_generated_outputs()
    gate_pass = report["gate_pass"] and verification["gate_pass"]
    return {
        "schema_version": 1,
        "status": (
            "HF3D_P0P2_CONTRACT_GENERATION_CHECK_PASS"
            if gate_pass
            else "HF3D_P0P2_CONTRACT_GENERATION_CHECK_FAILED_STOPPED"
        ),
        "gate_pass": gate_pass,
        "formal_round_trip": report,
        "generated_outputs": verification,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    try:
        report = write_outputs() if arguments.write else check_outputs()
    except P0P2Error as exc:
        report = {
            "schema_version": 1,
            "status": "HF3D_P0P2_CONTRACT_GENERATION_FAILED_STOPPED",
            "gate_pass": False,
            "message": str(exc),
        }
    print(pretty_json_bytes(report).decode("utf-8"), end="")
    return 0 if report["gate_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
