"""Deterministically build or verify the P0-P2 V2 contract inputs."""

from __future__ import annotations

import argparse
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from p0p2v2_authorization import default_denied_authorization
from p0p2v2_contract import (
    AUTHORIZATION_PATH,
    CONTRACT_PATH,
    EFFECTIVE_CONFIG_ROOT,
    EXPECTED_BENCHMARKS,
    P0PerformanceError,
    effective_config,
    generated_contract_bytes,
    pretty_json_bytes,
    relative,
)


def generated_outputs() -> dict[Path, bytes]:
    outputs = {
        CONTRACT_PATH: generated_contract_bytes(),
        AUTHORIZATION_PATH: pretty_json_bytes(default_denied_authorization()),
    }
    for benchmark in EXPECTED_BENCHMARKS:
        payload, _identity = effective_config(benchmark)
        outputs[EFFECTIVE_CONFIG_ROOT / f"P0P2V2_{benchmark}.json"] = pretty_json_bytes(
            payload
        )
    return outputs


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def verify(outputs: Mapping[Path, bytes]) -> dict[str, object]:
    records: list[dict[str, object]] = []
    for path, expected in outputs.items():
        actual = path.read_bytes() if path.is_file() and not path.is_symlink() else None
        records.append(
            {
                "path": relative(path),
                "regular_file_not_symlink": actual is not None,
                "byte_identical": actual == expected,
            }
        )
    return {
        "gate_pass": all(record["byte_identical"] is True for record in records),
        "records": records,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    outputs = generated_outputs()
    if args.check:
        report = verify(outputs)
        if report["gate_pass"] is not True:
            raise P0PerformanceError(f"P0-P2 V2 generated outputs drifted: {report}")
        return 0
    for path, payload in outputs.items():
        _atomic_bytes(path, payload)
    report = verify(outputs)
    if report["gate_pass"] is not True:
        raise P0PerformanceError("P0-P2 V2 outputs failed post-write verification")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
