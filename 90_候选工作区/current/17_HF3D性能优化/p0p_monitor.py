#!/usr/bin/env python3
"""Timestamped read-only monitor process for one authorized P0-P worker."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO

from p0p_telemetry import gpu_snapshot, process_identity, tctl_sources


def emit(stream: TextIO, payload: Mapping[str, Any]) -> None:
    stream.write(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
    )
    stream.flush()


def target_alive(pid: int, start_ticks: int) -> bool:
    try:
        current = process_identity(pid)
    except (OSError, RuntimeError, ValueError):
        return False
    return current.start_ticks == start_ticks


def monitor_periodic(
    *,
    kind: str,
    pid: int,
    start_ticks: int,
    interval_s: float,
    stop_file: Path,
    stream: TextIO,
) -> int:
    sample_count = 0
    first_ns = time.monotonic_ns()
    while target_alive(pid, start_ticks) and not stop_file.exists():
        captured = time.monotonic_ns()
        try:
            if kind == "gpu":
                value: Any = gpu_snapshot()
            elif kind == "tctl":
                value = tctl_sources()
            elif kind == "proc":
                status = Path("/proc") / str(pid) / "status"
                rollup = Path("/proc") / str(pid) / "smaps_rollup"
                value = {
                    "status": status.read_text(encoding="utf-8"),
                    "smaps_rollup": rollup.read_text(encoding="utf-8")
                    if rollup.is_file()
                    else None,
                }
            else:
                raise ValueError(f"unknown periodic monitor kind: {kind}")
            emit(
                stream,
                {
                    "record_type": "sample",
                    "kind": kind,
                    "monotonic_ns": captured,
                    "target_pid": pid,
                    "target_start_ticks": start_ticks,
                    "value": value,
                },
            )
            sample_count += 1
        except Exception as exc:  # noqa: BLE001 - preserve monitor failure evidence
            emit(
                stream,
                {
                    "record_type": "monitor_error",
                    "kind": kind,
                    "monotonic_ns": captured,
                    "target_pid": pid,
                    "target_start_ticks": start_ticks,
                    "exception_type": type(exc).__name__,
                    "message": str(exc),
                },
            )
            return 2
        time.sleep(interval_s)
    emit(
        stream,
        {
            "record_type": "monitor_summary",
            "kind": kind,
            "monitor_pid": os.getpid(),
            "target_pid": pid,
            "target_start_ticks": start_ticks,
            "first_monotonic_ns": first_ns,
            "last_monotonic_ns": time.monotonic_ns(),
            "sample_count": sample_count,
            "stop_file_seen": stop_file.exists(),
            "target_alive_at_stop": target_alive(pid, start_ticks),
        },
    )
    return 0


def monitor_pidstat(
    *,
    pid: int,
    start_ticks: int,
    stop_file: Path,
    stream: TextIO,
) -> int:
    first_ns = time.monotonic_ns()
    last_ns = first_ns
    command = [
        "pidstat",
        "-h",
        "-H",
        "-t",
        "-u",
        "-r",
        "-w",
        "-p",
        str(pid),
        "1",
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    line_count = 0
    try:
        assert process.stdout is not None
        while target_alive(pid, start_ticks) and not stop_file.exists():
            line = process.stdout.readline()
            if not line:
                if process.poll() is not None:
                    break
                time.sleep(0.05)
                continue
            emit(
                stream,
                {
                    "record_type": "pidstat_line",
                    "kind": "pidstat",
                    "monotonic_ns": time.monotonic_ns(),
                    "target_pid": pid,
                    "target_start_ticks": start_ticks,
                    "raw": line.rstrip("\n"),
                },
            )
            last_ns = time.monotonic_ns()
            line_count += 1
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            stdout_tail, stderr = process.communicate(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout_tail, stderr = process.communicate()
        for line in stdout_tail.splitlines():
            captured_ns = time.monotonic_ns()
            emit(
                stream,
                {
                    "record_type": "pidstat_line",
                    "kind": "pidstat",
                    "monotonic_ns": captured_ns,
                    "target_pid": pid,
                    "target_start_ticks": start_ticks,
                    "raw": line,
                },
            )
            last_ns = captured_ns
            line_count += 1
        emit(
            stream,
            {
                "record_type": "monitor_summary",
                "kind": "pidstat",
                "monitor_pid": os.getpid(),
                "target_pid": pid,
                "target_start_ticks": start_ticks,
                "first_monotonic_ns": first_ns,
                "last_monotonic_ns": last_ns,
                "pidstat_exit_code": process.returncode,
                "pidstat_stderr": stderr,
                "line_count": line_count,
                "stop_file_seen": stop_file.exists(),
                "target_alive_at_stop": target_alive(pid, start_ticks),
            },
        )
    return 0 if line_count else 2


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--kind", choices=("gpu", "tctl", "proc", "pidstat"), required=True
    )
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--start-ticks", type=int, required=True)
    parser.add_argument("--interval-s", type=float, default=0.5)
    parser.add_argument("--stop-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.output.exists() or args.output.is_symlink():
        print("monitor output already exists", file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        args.output,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        emit(
            stream,
            {
                "record_type": "monitor_start",
                "kind": args.kind,
                "monitor_pid": os.getpid(),
                "target_pid": args.pid,
                "target_start_ticks": args.start_ticks,
                "monotonic_ns": time.monotonic_ns(),
            },
        )
        if not target_alive(args.pid, args.start_ticks):
            emit(
                stream,
                {
                    "record_type": "monitor_error",
                    "kind": args.kind,
                    "target_pid": args.pid,
                    "target_start_ticks": args.start_ticks,
                    "message": "target PID/start identity mismatch at monitor start",
                    "monotonic_ns": time.monotonic_ns(),
                },
            )
            result = 2
        elif args.kind == "pidstat":
            result = monitor_pidstat(
                pid=args.pid,
                start_ticks=args.start_ticks,
                stop_file=args.stop_file,
                stream=stream,
            )
        else:
            result = monitor_periodic(
                kind=args.kind,
                pid=args.pid,
                start_ticks=args.start_ticks,
                interval_s=args.interval_s,
                stop_file=args.stop_file,
                stream=stream,
            )
        stream.flush()
        os.fsync(stream.fileno())
        return result


if __name__ == "__main__":
    raise SystemExit(main())
