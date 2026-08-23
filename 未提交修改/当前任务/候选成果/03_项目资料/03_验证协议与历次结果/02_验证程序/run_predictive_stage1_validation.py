#!/usr/bin/env python3
"""Run fail-closed implementation gates for the isolated Stage1 copy."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import traceback
from typing import Any, Callable, Mapping


def _discover_repository_root(start: Path) -> Path:
    """Find the containing Git worktree without assuming candidate depth."""
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    raise RuntimeError(f"cannot find Git repository above {start}")


HERE = Path(__file__).resolve()
STAGE1 = HERE.parents[3]
SIM = STAGE1 / "02_模拟程序"
REPO = _discover_repository_root(STAGE1)
PROTOCOL_PATH = HERE.with_name("PREDICTIVE_STAGE1_PROTOCOL.json")
RESULTS_ROOT = STAGE1 / "03_项目资料" / "03_验证协议与历次结果" / "04_历次结果"

ALLOWED_REPOSITORY_FILES = {"README.md", "AGENTS.md"}
ALLOWED_REPOSITORY_PREFIXES = ("未提交修改/", "每次任务前阅读/")

REQUIRED_TESTS = (
    "test_stage1_registry_and_entrypoints.py",
    "test_stage1_shared_observable_operator.py",
    "test_stage1_core_comparison_contract.py",
    "test_stage1_units.py",
    "test_stage1_events_and_sampling.py",
    "test_stage1_camera_integration.py",
    "test_stage1_transport_conservation.py",
    "test_stage1_cls_conservation.py",
    "test_stage1_widest_path_topology.py",
    "test_stage1_hf3d_strict_warnings.py",
    "test_stage1_archive_roundtrip.py",
    "test_stage1_validator_end_to_end.py",
)

ORIGINAL_TREES = {
    "01_最终理论": "238914477bfa15cf6d8dcd1a7e731b1559b6506d",
    "02_模拟程序": "2d65edcd2717ac262d028ab51d1feecc850b838f",
    "03_项目资料": "84f0e9e210b86b556935b9117310b5e668813bd7",
}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strict_json(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=lambda token: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON token {token}")
        ),
    )


def _source_content_identity() -> dict[str, Any]:
    excluded_parts = {"__pycache__", ".pytest_cache", ".mypy_cache"}
    result_fragment = (
        "03_项目资料",
        "03_验证协议与历次结果",
        "04_历次结果",
    )
    records: list[tuple[str, bytes]] = []
    for path in STAGE1.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(STAGE1)
        if any(part in excluded_parts for part in relative.parts):
            continue
        if relative.parts[: len(result_fragment)] == result_fragment:
            continue
        records.append((relative.as_posix(), path.read_bytes()))
    digest = hashlib.sha256()
    total_bytes = 0
    for relative, content in sorted(records):
        encoded_path = relative.encode("utf-8")
        digest.update(len(encoded_path).to_bytes(8, "big"))
        digest.update(encoded_path)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
        total_bytes += len(content)
    return {
        "algorithm": "sha256",
        "canonicalization": "sorted relative UTF-8 path and length-prefixed bytes",
        "excluded": [
            "validation result history",
            "Python/test caches",
        ],
        "sha256": digest.hexdigest(),
        "file_count": len(records),
        "total_bytes": total_bytes,
    }


def _git(*args: str, check: bool = True) -> str:
    process = subprocess.run(
        ["git", *args],
        cwd=REPO,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if check and process.returncode != 0:
        raise RuntimeError(process.stdout.strip())
    return process.stdout.strip()


def _environment() -> dict[str, Any]:
    dependencies: dict[str, str | None] = {}
    for name in ("numpy", "scipy", "matplotlib", "pytest"):
        try:
            dependencies[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            dependencies[name] = None
    return {
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "dependencies": dependencies,
        "pythonpath_for_subprocesses": str(SIM / "src"),
    }


def _base_env(*, strict_warnings: bool = False) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SIM / "src")
    env["AIRVORTEX_PYTHON"] = sys.executable
    if strict_warnings:
        env["PYTHONWARNINGS"] = "error"
    else:
        env.pop("PYTHONWARNINGS", None)
    return env


def _gate_record(
    *,
    name: str,
    required: bool,
    command: list[str] | None,
    cwd: Path,
    returncode: int,
    output: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "required": required,
        "command": command,
        "cwd": str(cwd),
        "returncode": returncode,
        "passed": returncode == 0,
        "output_sha256": _sha256_bytes(output.encode("utf-8")),
        "output": output,
    }


def run_command(
    name: str,
    command: list[str],
    *,
    cwd: Path = SIM,
    required: bool = True,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    try:
        process = subprocess.run(
            command,
            cwd=cwd,
            env=dict(_base_env() if env is None else env),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        return _gate_record(
            name=name,
            required=required,
            command=command,
            cwd=cwd,
            returncode=process.returncode,
            output=process.stdout,
        )
    except BaseException:
        return _gate_record(
            name=name,
            required=required,
            command=command,
            cwd=cwd,
            returncode=125,
            output=traceback.format_exc(),
        )


def run_callable(
    name: str,
    action: Callable[[], None],
    *,
    required: bool = True,
) -> dict[str, Any]:
    buffer = io.StringIO()
    returncode = 0
    with redirect_stdout(buffer), redirect_stderr(buffer):
        try:
            action()
        except BaseException:
            traceback.print_exc()
            returncode = 1
    return _gate_record(
        name=name,
        required=required,
        command=None,
        cwd=REPO,
        returncode=returncode,
        output=buffer.getvalue(),
    )


def _write_smoke_configs(output: Path) -> tuple[Path, Path]:
    sys.path.insert(0, str(SIM / "src"))
    from airvortex.core import (
        BackendMode,
        DriveConfig,
        RunConfig,
        SimulationConfig,
    )

    base = SimulationConfig()
    drive = DriveConfig(initial_rpm=0.0, target_rpm=0.0, ramp_duration_s=0.0)
    forward = replace(
        base,
        backend=BackendMode.FORWARD,
        drive=drive,
        run=RunConfig(duration_s=0.01, output_interval_s=0.005),
        forward=replace(
            base.forward,
            integration_time_step_s=0.001,
            surface_radial_points=128,
        ),
    )
    high_fidelity = replace(
        base,
        backend=BackendMode.HIGH_FIDELITY,
        drive=drive,
        run=RunConfig(duration_s=0.0002, output_interval_s=0.0001),
        forward=replace(base.forward, integration_time_step_s=0.0001),
        high_fidelity=replace(
            base.high_fidelity,
            radial_cells=16,
            azimuthal_cells=16,
            axial_cells=24,
            time_step_s=0.0001,
            pressure_relative_tolerance=1.0e-6,
            pressure_max_iterations=800,
        ),
    )
    inputs = output / "inputs"
    inputs.mkdir(parents=True)
    forward_path = inputs / "forward_smoke.json"
    hf_path = inputs / "hf3d_smoke.json"
    forward_path.write_text(
        json.dumps(forward.to_dict(), ensure_ascii=False, indent=2, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    hf_path.write_text(
        json.dumps(
            high_fidelity.to_dict(),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return forward_path, hf_path


def compose_acceptance(
    gates: Mapping[str, Mapping[str, Any]],
    *,
    environment: Mapping[str, Any],
    source_identity: Mapping[str, Any],
    git_identity: Mapping[str, Any],
    original_trees: Mapping[str, str],
) -> dict[str, Any]:
    required_pass = all(
        bool(gate.get("passed"))
        for gate in gates.values()
        if bool(gate.get("required", True))
    )

    def passed(*names: str) -> bool:
        return all(bool(gates.get(name, {}).get("passed")) for name in names)

    six_gates = {
        "G1_entrypoints": passed(
            "backend_cli_self_check",
            "strict_warnings_self_check",
            "gui_backend_discovery",
            "forward_cli_archive",
            "hf3d_cli_archive",
        ),
        "G2_shared_observable_operator": passed(
            "stage1_required_tests", "archive_roundtrip_and_contract"
        ),
        "G3_comparison_contract": passed(
            "identity_mismatch_counterexample", "comparison_report_generation"
        ),
        "G4_events_and_camera": passed(
            "stage1_required_tests", "camera_end_to_end"
        ),
        "G5_numeric_auditability": passed(
            "numeric_algorithm_tests",
            "numeric_archive_thresholds",
            "strict_warnings_self_check",
            "archive_roundtrip_and_contract",
        ),
        "G6_validator": passed(
            "compileall",
            "stage1_required_tests",
            "source_boundary",
            "archive_roundtrip_and_contract",
            "comparison_report_generation",
        ),
    }
    implementation_pass = required_pass and all(six_gates.values())
    overall_status = (
        "PASS_STAGE1_IMPLEMENTATION__NO_GO_PHYSICAL_PREDICTION"
        if implementation_pass
        else "FAIL_STAGE1_IMPLEMENTATION__NO_GO_PHYSICAL_PREDICTION"
    )
    return {
        "schema": "airvortex-predictive-stage1-result-v2",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "overall_status": overall_status,
        "implementation_pass": implementation_pass,
        "physical_prediction_go": False,
        "six_gates": {
            key: {"passed": value} for key, value in six_gates.items()
        },
        "gate_results": {
            key: {
                field: value
                for field, value in gate.items()
                if field != "output"
            }
            for key, gate in gates.items()
        },
        "environment": dict(environment),
        "source_content_identity": dict(source_identity),
        "git": dict(git_identity),
        "original_tree_sha": dict(original_trees),
        "physical_go_gates": {
            "static_drop_laplace_pressure": False,
            "spurious_current_grid_convergence": False,
            "capillary_wave_frequency_and_damping": False,
            "joint_grid_time_interface_refinement": False,
            "measured_bar_trajectory": False,
            "independent_calibration_and_blind_validation": False,
        },
        "interpretation": (
            "Implementation evidence only. HF3D and forward agreement is not "
            "apparatus-level validation."
        ),
    }


def _source_boundary_check() -> tuple[dict[str, str], dict[str, Any]]:
    actual = {
        path: _git("rev-parse", f"HEAD:{path}") for path in ORIGINAL_TREES
    }
    if actual != ORIGINAL_TREES:
        raise AssertionError(f"protected tree mismatch: {actual}")
    changed_output = "\n".join(
        item
        for item in (
            _git("diff", "--name-only", check=False),
            _git("diff", "--cached", "--name-only", check=False),
            _git("diff", "--name-only", "origin/main...HEAD", check=False),
            _git("ls-files", "--others", "--exclude-standard", check=False),
        )
        if item
    )
    changed = sorted(set(line for line in changed_output.splitlines() if line))
    outside = [
        path
        for path in changed
        if path not in ALLOWED_REPOSITORY_FILES
        and not path.startswith(ALLOWED_REPOSITORY_PREFIXES)
    ]
    if outside:
        raise AssertionError(f"changes outside allowed Stage1 tree: {outside}")
    return actual, {"changed_paths": changed, "outside_allowed_scope": outside}


def _run_validation(args: argparse.Namespace) -> tuple[Path, dict[str, Any]]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = (
        args.output.resolve()
        if args.output is not None
        else RESULTS_ROOT / f"{stamp}-predictive-stage1-repair-v2"
    )
    output.mkdir(parents=True, exist_ok=False)
    logs = output / "logs"
    logs.mkdir()
    source_identity = _source_content_identity()
    forward_config, hf_config = _write_smoke_configs(output)
    gates: dict[str, dict[str, Any]] = {}

    def record(gate: dict[str, Any]) -> None:
        name = str(gate["name"])
        log_path = logs / f"{len(gates) + 1:02d}_{name}.log"
        log_path.write_text(str(gate["output"]), encoding="utf-8")
        gate["log_path"] = str(log_path.relative_to(output))
        gate["log_sha256"] = _sha256_file(log_path)
        gates[name] = gate

    record(
        run_command(
            "compileall",
            [
                sys.executable,
                "-m",
                "compileall",
                "-q",
                "src/airvortex/stage1",
                "run_stage1_gui.py",
                "run_stage1_cli.py",
            ],
        )
    )
    record(
        run_command(
            "stage1_required_tests",
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                *[f"tests/{name}" for name in REQUIRED_TESTS],
            ],
        )
    )
    if args.include_baseline_tests:
        record(
            run_command(
                "full_baseline_regression",
                [sys.executable, "-m", "pytest", "-q"],
            )
        )
    record(
        run_command(
            "backend_cli_self_check",
            [sys.executable, "run_stage1_cli.py", "self-check", "--pretty"],
        )
    )
    record(
        run_command(
            "strict_warnings_self_check",
            [sys.executable, "run_stage1_cli.py", "self-check", "--pretty"],
            env=_base_env(strict_warnings=True),
        )
    )
    gui_probe = (
        "from airvortex.stage1 import install_stage1_overlay; "
        "install_stage1_overlay(); "
        "from airvortex.gui.model import GuiViewModel; GuiViewModel(); "
        "from airvortex.runtime import discover_backends; "
        "r=discover_backends(probe=True); assert not r.discovery_errors; "
        "a=r.create('forward'); b=r.create('high_fidelity'); "
        "assert type(a).__module__=='airvortex.stage1.backends'; "
        "assert type(b).__module__=='airvortex.stage1.backends'; "
        "print(type(a).__name__, type(b).__name__)"
    )
    record(
        run_command(
            "gui_backend_discovery",
            [sys.executable, "-c", gui_probe],
        )
    )

    archives = output / "archives"
    archives.mkdir()
    forward_archive = archives / "forward"
    hf_archive = archives / "hf3d"
    record(
        run_command(
            "forward_cli_archive",
            [
                sys.executable,
                "run_stage1_cli.py",
                "run",
                "--backend",
                "forward",
                "--config",
                str(forward_config),
                "--output",
                str(forward_archive),
                "--quiet",
            ],
        )
    )
    record(
        run_command(
            "hf3d_cli_archive",
            [
                sys.executable,
                "run_stage1_cli.py",
                "run",
                "--backend",
                "high_fidelity",
                "--config",
                str(hf_config),
                "--output",
                str(hf_archive),
                "--quiet",
            ],
        )
    )

    def archive_roundtrip() -> None:
        from airvortex.analysis import compare_results
        from airvortex.io import load_run_archive

        forward = load_run_archive(forward_archive)
        hf3d = load_run_archive(hf_archive)
        for archived in (forward, hf3d):
            result = archived.result
            assert "camera_visible_depth_m" in result.observables
            assert "camera_channel_width_m" in result.observables
            assert "observable_contract" in result.metadata
            compare_results(
                result, result, "visible_outer_to_center_depth_m"
            )
        for key in (
            "diagnostic_phase_conservation_residual_m3",
            "diagnostic_density_conservation_residual_kg",
            "diagnostic_momentum_remap_absolute_kg_m_s",
            "diagnostic_cls_momentum_remap_absolute_kg_m_s",
        ):
            assert key in hf3d.result.fields, key
        print("archive roundtrip preserved identities, camera, and diagnostics")

    record(
        run_callable(
            "archive_roundtrip_and_contract",
            archive_roundtrip,
        )
    )

    def mismatch_counterexample() -> None:
        import numpy as np
        from airvortex.analysis import DataValidationError, compare_results
        from airvortex.core import BackendMode, SimulationResult
        from airvortex.stage1.contracts import contract_document

        base_parameters = {
            "central_observation_radius_m": 0.003,
            "outer_annulus_inner_radius_m": 0.036,
            "outer_annulus_outer_radius_m": 0.045,
            "interface_level": 0.5,
            "static_reference_height_m": 0.06,
            "static_reference_source": "nominal_configured_liquid_depth_not_measured_H0",
            "central_aggregation": "minimum",
            "outer_aggregation": "annular_area_weighted_mean",
        }
        different = dict(base_parameters)
        different["central_observation_radius_m"] = 0.006
        left = SimulationResult(
            BackendMode.FORWARD,
            np.array([0.0, 1.0, 2.0]),
            {"surface_depth_m": np.array([0.0, 1.0, 2.0])},
            metadata={
                "backend_id": "forward",
                "observable_units": {"surface_depth_m": "m"},
                "observable_contract": contract_document(
                    ["surface_depth_m"],
                    surface_operator_parameters=base_parameters,
                ),
            },
        )
        right = SimulationResult(
            BackendMode.HIGH_FIDELITY,
            np.array([0.0, 1.0, 2.0]),
            {"surface_depth_m": np.array([0.0, 0.0, 1.0])},
            metadata={
                "backend_id": "high_fidelity",
                "observable_units": {"surface_depth_m": "m"},
                "observable_contract": contract_document(
                    ["surface_depth_m"],
                    surface_operator_parameters=different,
                ),
            },
        )
        try:
            compare_results(left, right, "surface_depth_m")
        except DataValidationError as exc:
            print("REJECTED", exc)
            return
        raise AssertionError(
            "identity mismatch incorrectly produced an RMSE instead of rejection"
        )

    record(
        run_callable(
            "identity_mismatch_counterexample", mismatch_counterexample
        )
    )
    record(
        run_command(
            "camera_end_to_end",
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/test_stage1_camera_integration.py",
            ],
        )
    )
    record(
        run_command(
            "numeric_algorithm_tests",
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/test_stage1_transport_conservation.py",
                "tests/test_stage1_cls_conservation.py",
                "tests/test_stage1_widest_path_topology.py",
            ],
        )
    )

    def numeric_thresholds() -> None:
        from airvortex.io import load_run_archive

        protocol = _strict_json(PROTOCOL_PATH)
        limits = protocol["numeric_diagnostic_gates"]
        result = load_run_archive(hf_archive).result
        summary = result.metadata["diagnostics"][
            "conservation_and_momentum_remap"
        ]
        checks = {
            "maximum_absolute_phase_conservation_residual_m3": summary[
                "phase_conservation_residual_m3"
            ]["maximum_absolute"],
            "maximum_absolute_density_conservation_residual_kg": summary[
                "density_conservation_residual_kg"
            ]["maximum_absolute"],
            "maximum_absolute_momentum_remap_kg_m_s": summary[
                "momentum_remap_absolute_kg_m_s"
            ]["maximum_absolute"],
            "maximum_absolute_cls_phase_conservation_residual_m3": summary[
                "cls_phase_conservation_residual_m3"
            ]["maximum_absolute"],
            "maximum_absolute_cls_density_conservation_residual_kg": summary[
                "cls_density_conservation_residual_kg"
            ]["maximum_absolute"],
            "maximum_absolute_cls_momentum_remap_kg_m_s": summary[
                "cls_momentum_remap_absolute_kg_m_s"
            ]["maximum_absolute"],
            "maximum_single_step_phase_clip_absolute_volume_m3": summary[
                "phase_clip_absolute_volume_m3"
            ]["maximum_absolute"],
            "maximum_single_step_phase_repair_absolute_volume_m3": summary[
                "phase_repair_absolute_volume_m3"
            ]["maximum_absolute"],
        }
        failures = {
            key: {"actual": value, "limit": limits[key]}
            for key, value in checks.items()
            if value > limits[key]
        }
        print(json.dumps({"actual": checks, "limits": limits}, indent=2))
        if failures:
            raise AssertionError(f"numeric diagnostic threshold failures: {failures}")

    record(
        run_callable("numeric_archive_thresholds", numeric_thresholds)
    )

    comparison_output = output / "comparison_report"
    record(
        run_command(
            "comparison_report_generation",
            [
                sys.executable,
                "run_stage1_cli.py",
                "compare",
                str(forward_archive),
                str(hf_archive),
                "--output",
                str(comparison_output),
                "--quiet",
            ],
        )
    )

    actual_trees: dict[str, str] = {}
    boundary_details: dict[str, Any] = {}

    def source_boundary() -> None:
        nonlocal actual_trees, boundary_details
        actual_trees, boundary_details = _source_boundary_check()
        print(json.dumps(boundary_details, ensure_ascii=False, indent=2))

    record(run_callable("source_boundary", source_boundary))

    git_identity = {
        "branch": _git("branch", "--show-current"),
        "head": _git("rev-parse", "HEAD"),
        "origin_main": _git("rev-parse", "origin/main"),
        "working_tree_status": _git("status", "--short", check=False).splitlines(),
        **boundary_details,
    }
    acceptance = compose_acceptance(
        gates,
        environment=_environment(),
        source_identity=source_identity,
        git_identity=git_identity,
        original_trees=actual_trees or ORIGINAL_TREES,
    )
    evidence_files: dict[str, dict[str, Any]] = {}
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name not in {"acceptance.json", "acceptance.sha256"}:
            relative = path.relative_to(output).as_posix()
            if (
                relative.startswith("logs/")
                or relative.startswith("inputs/")
                or path.name in {"manifest.json", "comparison.json"}
            ):
                evidence_files[relative] = {
                    "bytes": path.stat().st_size,
                    "sha256": _sha256_file(path),
                }
    acceptance["evidence_files"] = evidence_files
    acceptance_path = output / "acceptance.json"
    acceptance_path.write_text(
        json.dumps(acceptance, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    acceptance_sha = _sha256_file(acceptance_path)
    (output / "acceptance.sha256").write_text(
        f"{acceptance_sha}  acceptance.json\n", encoding="utf-8"
    )
    print(acceptance["overall_status"])
    print(f"acceptance={acceptance_path}")
    print(f"acceptance_sha256={acceptance_sha}")
    return output, acceptance


def _self_test_fail_closed() -> int:
    gates = {
        "synthetic_failure": {
            "name": "synthetic_failure",
            "required": True,
            "passed": False,
            "returncode": 1,
        }
    }
    acceptance = compose_acceptance(
        gates,
        environment={},
        source_identity={},
        git_identity={},
        original_trees={},
    )
    assert acceptance["implementation_pass"] is False
    assert acceptance["physical_prediction_go"] is False
    assert acceptance["overall_status"] == (
        "FAIL_STAGE1_IMPLEMENTATION__NO_GO_PHYSICAL_PREDICTION"
    )
    print("validator fail-closed self-test: PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-baseline-tests", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test-fail-closed", action="store_true")
    args = parser.parse_args()
    if args.self_test_fail_closed:
        return _self_test_fail_closed()
    _output, acceptance = _run_validation(args)
    return 0 if acceptance["implementation_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
