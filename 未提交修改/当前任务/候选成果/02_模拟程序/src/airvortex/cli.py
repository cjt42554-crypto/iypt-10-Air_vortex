"""Offline command-line interface for validation, runs, comparisons, and sweeps."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
import math
from pathlib import Path
import sys
import traceback
from typing import Any, Sequence

from airvortex.core import (
    BackendMode,
    Control,
    DriveConfig,
    RunConfig,
    RunStatus,
    SimulationConfig,
    SimulationResult,
)
from airvortex.io import config_sha256, load_config_json, read_json_strict
from airvortex.runtime import (
    JobMessage,
    compare_archives,
    discover_backends,
    estimate_high_fidelity_work,
    linear_values,
    logarithmic_values,
    run_once,
    run_paired_compare,
    run_sweep,
)


COMMANDS = ("validate-config", "run", "compare", "sweep", "self-check")


class CLIError(RuntimeError):
    """A concise user-facing command error."""


def _backend(value: str) -> BackendMode:
    try:
        return BackendMode(value.replace("-", "_"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "backend must be 'forward' or 'high_fidelity'"
        ) from exc


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return parsed


def _finite_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(parsed):
        raise argparse.ArgumentTypeError("must be finite")
    return parsed


class _ProgressPrinter:
    def __init__(self, *, quiet: bool = False) -> None:
        self.quiet = quiet
        self._last_step = -1
        self._last_fraction = -1.0

    def __call__(self, message: JobMessage) -> None:
        if self.quiet:
            return
        if message.kind == "state":
            print(
                f"[state] {message.payload.get('previous')} -> "
                f"{message.payload.get('current')}",
                file=sys.stderr,
            )
            return
        if message.kind == "error":
            print(
                f"[error] {message.payload.get('type')}: "
                f"{message.payload.get('message')}",
                file=sys.stderr,
            )
            return
        if message.kind != "progress":
            return
        fraction = float(message.payload.get("fraction", 0.0))
        step = int(message.payload.get("step", 0))
        if (
            fraction < 1.0
            and step <= self._last_step
            and fraction < self._last_fraction + 0.01
        ):
            return
        self._last_step = step
        self._last_fraction = fraction
        detail = str(message.payload.get("message", "")).strip()
        print(
            f"[progress] {100.0 * fraction:6.2f}% step={step}"
            + (f" {detail}" if detail else ""),
            file=sys.stderr,
        )


def _apply_backend(config: SimulationConfig, backend: BackendMode | None) -> SimulationConfig:
    return config if backend is None or config.backend is backend else replace(config, backend=backend)


def _warn_hf_cost(config: SimulationConfig) -> None:
    if config.backend is not BackendMode.HIGH_FIDELITY:
        return
    estimate = estimate_high_fidelity_work(config)
    print(
        "WARNING: high_fidelity is an independent research-grade HF3D solver; "
        "a single run is not a validated critical-rpm or stable-channel prediction.",
        file=sys.stderr,
    )
    print(
        "WARNING: current HF3D box upper bound is "
        f"{estimate['steps_upper_bound']:,} steps, "
        f"{estimate['active_3d_box_cells_upper_bound']:,} x-y-z box cells, about "
        f"{estimate['active_3d_box_cell_steps_upper_bound']:,} cell-steps before "
        "adaptive sub-stepping. All three configured dimensions are used.",
        file=sys.stderr,
    )


def _read_sweep(
    path: Path,
    *,
    backend_override: BackendMode | None,
    parameter_override: str | None,
    start_override: float | None,
    stop_override: float | None,
    count_override: int | None,
    scale_override: str | None,
) -> tuple[SimulationConfig, str, list[int | float]]:
    raw = read_json_strict(path)
    if not isinstance(raw, dict):
        raise CLIError("sweep config must be a JSON object")
    sweep_keys = {"base_config", "parameter", "values", "start", "stop", "count", "scale", "backend"}
    is_sweep_spec = "base_config" in raw or bool(set(raw) & {"parameter", "values", "start", "stop", "count", "scale"})
    if is_sweep_spec:
        unknown = set(raw) - sweep_keys
        if unknown:
            raise CLIError(f"sweep config contains unknown fields: {', '.join(sorted(unknown))}")
        base_raw = raw.get("base_config")
        if isinstance(base_raw, str):
            base = load_config_json(path.parent / base_raw)
        elif isinstance(base_raw, dict):
            base = SimulationConfig.from_dict(base_raw)
        else:
            raise CLIError("sweep config requires base_config as a path or config object")
        spec_backend = raw.get("backend")
        if spec_backend is not None:
            if not isinstance(spec_backend, str):
                raise CLIError("sweep backend must be a string")
            try:
                base = replace(base, backend=BackendMode(spec_backend.replace("-", "_")))
            except ValueError as exc:
                raise CLIError(f"invalid sweep backend: {spec_backend}") from exc
        parameter = parameter_override or raw.get("parameter")
        if not isinstance(parameter, str) or not parameter:
            raise CLIError("sweep parameter is required")
        if "values" in raw and all(
            value is None
            for value in (start_override, stop_override, count_override, scale_override)
        ):
            values_raw = raw["values"]
            if not isinstance(values_raw, list) or not values_raw:
                raise CLIError("sweep values must be a non-empty array")
            values: list[int | float] = []
            for value in values_raw:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise CLIError("sweep values must contain only numbers")
                values.append(value)
        else:
            start = start_override if start_override is not None else raw.get("start")
            stop = stop_override if stop_override is not None else raw.get("stop")
            count = count_override if count_override is not None else raw.get("count")
            scale = scale_override or raw.get("scale", "linear")
            values = _range_values(start, stop, count, scale)
    else:
        base = SimulationConfig.from_dict(raw)
        if parameter_override is None:
            raise CLIError("--parameter is required when --config is a normal run config")
        parameter = parameter_override
        values = _range_values(start_override, stop_override, count_override, scale_override or "linear")
    return _apply_backend(base, backend_override), parameter, values


def _range_values(
    start: object, stop: object, count: object, scale: object
) -> list[float]:
    if isinstance(start, bool) or not isinstance(start, (int, float)):
        raise CLIError("sweep start must be a number")
    if isinstance(stop, bool) or not isinstance(stop, (int, float)):
        raise CLIError("sweep stop must be a number")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise CLIError("sweep count must be a positive integer")
    if scale == "linear":
        return linear_values(float(start), float(stop), count)
    if scale == "log":
        return logarithmic_values(float(start), float(stop), count)
    raise CLIError("sweep scale must be 'linear' or 'log'")


def _validate_command(args: argparse.Namespace) -> int:
    config = _apply_backend(load_config_json(args.config), args.backend)
    registry = discover_backends(probe=True)
    registry.create(config.backend)
    print(
        json.dumps(
            {
                "valid": True,
                "backend": config.backend.value,
                "config_sha256": config_sha256(config),
                "problem_id": config.problem_id,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _run_command(args: argparse.Namespace) -> int:
    config = _apply_backend(load_config_json(args.config), args.backend)
    _warn_hf_cost(config)
    outcome = run_once(
        config,
        args.output,
        on_message=_ProgressPrinter(quiet=args.quiet),
    )
    print(
        json.dumps(
            {
                "state": outcome.state.value,
                "output": str(outcome.output.resolve()),
                "result_status": None
                if outcome.result is None
                else outcome.result.status.value,
                "termination_reason": outcome.termination_reason,
                "requested_end_s": outcome.requested_end_s,
                "actual_end_s": outcome.actual_end_s,
                "domain_limited": outcome.domain_limited,
                "full_duration": outcome.full_duration,
                "completion_class": outcome.completion_class,
                "domain_status": outcome.domain_status,
                "out_of_model_domain": outcome.out_of_model_domain,
                "first_out_of_domain_s": outcome.first_out_of_domain_s,
                "scientific_completion_class": (
                    outcome.scientific_completion_class
                ),
                "execution_source_identity_valid": (
                    outcome.execution_source_identity_valid
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if outcome.succeeded else 1


def _compare_command(args: argparse.Namespace) -> int:
    if args.config is not None:
        if args.left is not None or args.right is not None:
            raise CLIError("compare accepts either --config or two run directories, not both")
        config = load_config_json(args.config)
        _warn_hf_cost(replace(config, backend=BackendMode.HIGH_FIDELITY))
        comparison = run_paired_compare(
            config,
            args.output,
            on_message=_ProgressPrinter(quiet=args.quiet),
        )
    else:
        if args.left is None or args.right is None:
            raise CLIError("compare requires two run directories or --config")
        comparison = compare_archives(args.left, args.right, args.output)
    response: dict[str, Any] = {
        "status": "completed",
        "output": str(comparison.resolve()),
    }
    comparison_manifest = read_json_strict(comparison / "manifest.json")
    if isinstance(comparison_manifest, dict):
        response.update(
            {
                "status": comparison_manifest.get("status", "completed"),
                "completion_class": comparison_manifest.get("completion_class"),
                "valid_time_end": comparison_manifest.get("valid_time_end"),
                "truncated_to_common_valid_domain": comparison_manifest.get(
                    "truncated_to_common_valid_domain", False
                ),
                "domains": comparison_manifest.get("domains"),
            }
        )
    if args.config is not None:
        paired = read_json_strict(Path(args.output) / "paired_manifest.json")
        if isinstance(paired, dict):
            response.update(
                {
                    "status": paired.get("status", "completed"),
                    "completion_class": paired.get("completion_class"),
                    "domain_limited": paired.get("domain_limited", False),
                    "out_of_model_domain": paired.get(
                        "out_of_model_domain", False
                    ),
                    "valid_time_end": paired.get("valid_time_end"),
                    "truncated_to_common_valid_domain": paired.get(
                        "truncated_to_common_valid_domain", False
                    ),
                    "backend_domains": paired.get("backend_domains"),
                    "backend_terminations": paired.get("backend_terminations"),
                }
            )
    print(json.dumps(response, ensure_ascii=False, sort_keys=True))
    return 0


def _sweep_command(args: argparse.Namespace) -> int:
    config, parameter, values = _read_sweep(
        args.config,
        backend_override=args.backend,
        parameter_override=args.parameter,
        start_override=args.start,
        stop_override=args.stop,
        count_override=args.num,
        scale_override=args.scale,
    )
    _warn_hf_cost(config)
    outcome = run_sweep(
        config,
        parameter,
        values,
        args.output,
        workers=args.workers,
        on_message=_ProgressPrinter(quiet=args.quiet),
    )
    print(
        json.dumps(
            {
                "status": (
                    "completed_with_out_of_model_domain"
                    if outcome.succeeded and outcome.out_of_model_domain
                    else "completed_with_domain_limit"
                    if outcome.succeeded
                    and (
                        outcome.completed_domain_limited
                        or outcome.skipped_domain_limited
                    )
                    else "completed"
                    if outcome.succeeded
                    else "failed"
                ),
                "output": str(outcome.output.resolve()),
                "total": outcome.total,
                "completed": outcome.completed,
                "completed_full": outcome.completed_full,
                "completed_domain_limited": outcome.completed_domain_limited,
                "completed_early_unclassified": (
                    outcome.completed_early_unclassified
                ),
                "stopped": outcome.stopped,
                "skipped": outcome.skipped,
                "skipped_full": outcome.skipped_full,
                "skipped_domain_limited": outcome.skipped_domain_limited,
                "completed_with_out_of_model_domain": (
                    outcome.completed_with_out_of_model_domain
                ),
                "skipped_with_out_of_model_domain": (
                    outcome.skipped_with_out_of_model_domain
                ),
                "out_of_model_domain": outcome.out_of_model_domain,
                "failed": outcome.failed,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if outcome.succeeded else 1


def _self_check_command(args: argparse.Namespace) -> int:
    checks: dict[str, dict[str, Any]] = {}

    def record(name: str, action: Any) -> Any:
        try:
            value = action()
        except Exception as exc:
            checks[name] = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
            return None
        checks[name] = {"ok": True}
        return value

    registry = record("backend_discovery", lambda: discover_backends(probe=True))
    if registry is not None:
        for backend_id in ("forward", "high_fidelity"):
            record(f"backend_{backend_id}", lambda key=backend_id: registry.create(key))

    default = SimulationConfig()
    record(
        "config_round_trip",
        lambda: SimulationConfig.from_dict(default.to_dict()),
    )

    def forward_smoke() -> SimulationResult:
        assert registry is not None
        backend = registry.create("forward")
        config = replace(
            default,
            backend=BackendMode.FORWARD,
            drive=DriveConfig(initial_rpm=0.0, target_rpm=0.0, ramp_duration_s=0.0),
            run=RunConfig(duration_s=0.01, output_interval_s=0.005),
            forward=replace(
                default.forward,
                integration_time_step_s=0.001,
                surface_radial_points=128,
            ),
        )
        result = backend.run(config, control=Control(), progress=None)
        if not isinstance(result, SimulationResult) or result.status is not RunStatus.COMPLETED:
            raise RuntimeError("forward smoke did not return a completed SimulationResult")
        return result

    def high_fidelity_smoke() -> SimulationResult:
        assert registry is not None
        backend = registry.create("high_fidelity")
        config = replace(
            default,
            backend=BackendMode.HIGH_FIDELITY,
            drive=DriveConfig(initial_rpm=0.0, target_rpm=0.0, ramp_duration_s=0.0),
            run=RunConfig(duration_s=0.0002, output_interval_s=0.0001),
            forward=replace(default.forward, integration_time_step_s=0.0001),
            high_fidelity=replace(
                default.high_fidelity,
                radial_cells=16,
                azimuthal_cells=16,
                axial_cells=24,
                time_step_s=0.0001,
                pressure_relative_tolerance=1.0e-6,
                pressure_max_iterations=800,
            ),
        )
        result = backend.run(config, control=Control(), progress=None)
        if not isinstance(result, SimulationResult) or result.status is not RunStatus.COMPLETED:
            raise RuntimeError("high-fidelity smoke did not return a completed SimulationResult")
        if result.metadata.get("capabilities", {}).get("quantitative_post_contact_air_channel") is not False:
            raise RuntimeError("high-fidelity smoke is missing its post-contact capability boundary")
        return result

    if registry is not None and checks.get("backend_forward", {}).get("ok"):
        record("forward_minimal_smoke", forward_smoke)
    if registry is not None and checks.get("backend_high_fidelity", {}).get("ok"):
        record("high_fidelity_16x16x24_smoke", high_fidelity_smoke)

    passed = all(item["ok"] for item in checks.values())
    payload = {"ok": passed, "offline": True, "checks": checks}
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if passed else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="airvortex",
        description="IYPT 10 空气涡流离线双后端计算平台",
    )
    parser.add_argument("--debug", action="store_true", help="show full traceback on command failure")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate-config", help="strictly validate one run config")
    validate.add_argument("config", type=Path)
    validate.add_argument("--backend", type=_backend)
    validate.set_defaults(handler=_validate_command)

    run = subparsers.add_parser("run", help="execute one backend and save an archive")
    run.add_argument("--backend", required=True, type=_backend)
    run.add_argument("--config", required=True, type=Path)
    run.add_argument("--output", required=True, type=Path)
    run.add_argument("--quiet", action="store_true")
    run.set_defaults(handler=_run_command)

    compare = subparsers.add_parser(
        "compare", help="compare two archives or run a paired forward/HF comparison"
    )
    compare.add_argument("left", nargs="?", type=Path)
    compare.add_argument("right", nargs="?", type=Path)
    compare.add_argument("--config", type=Path, help="run both backends from this shared config")
    compare.add_argument("--output", required=True, type=Path)
    compare.add_argument("--quiet", action="store_true")
    compare.set_defaults(handler=_compare_command)

    sweep = subparsers.add_parser("sweep", help="run/resume a linear or logarithmic sweep")
    sweep.add_argument("--config", required=True, type=Path)
    sweep.add_argument("--backend", type=_backend)
    sweep.add_argument("--parameter", help="dotted numeric config path")
    sweep.add_argument("--start", type=_finite_float)
    sweep.add_argument("--stop", type=_finite_float)
    sweep.add_argument("--num", type=_positive_int)
    sweep.add_argument("--scale", choices=("linear", "log"))
    sweep.add_argument("--output", required=True, type=Path)
    sweep.add_argument("--workers", type=_positive_int, default=1)
    sweep.add_argument("--quiet", action="store_true")
    sweep.set_defaults(handler=_sweep_command)

    self_check = subparsers.add_parser("self-check", help="offline dependency and forward smoke check")
    self_check.add_argument("--pretty", action="store_true")
    self_check.set_defaults(handler=_self_check_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except Exception as exc:
        if args.debug:
            traceback.print_exc()
        else:
            print(f"airvortex: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
