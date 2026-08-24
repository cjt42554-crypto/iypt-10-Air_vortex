#!/usr/bin/env python3
"""Command-line entry point for the candidate HF3D qualification workflow."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
from typing import Sequence
from hfq_core import QualificationError
from hfq_cli_common import UserFacingError
from hfq_cli_run import _audit_command, _generate_command, _run_command
from hfq_cli_analysis import _analyse_command


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate, run and analyse a fail-closed HF3D grid/time refinement campaign."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("audit-config", help="audit one HF3D JSON configuration")
    audit.add_argument("config", type=Path)
    audit.add_argument("--json", dest="json_output", type=Path)
    audit.add_argument("--markdown", dest="markdown_output", type=Path)
    audit.add_argument("--label")
    generate = sub.add_parser("generate", help="generate frozen refinement cases")
    generate.add_argument("spec", type=Path)
    generate.add_argument("output", type=Path)
    run = sub.add_parser("run", help="run generated cases sequentially")
    run.add_argument("manifest", type=Path)
    run.add_argument("output", type=Path)
    run.add_argument("--python", default=sys.executable)
    run.add_argument("--resume", action="store_true")
    run.add_argument("--keep-going", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    analyse = sub.add_parser("analyse", help="analyse completed AirVortex archives")
    analyse.add_argument("manifest", type=Path)
    analyse.add_argument("runs", type=Path)
    analyse.add_argument("output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "audit-config":
            return _audit_command(args)
        if args.command == "generate":
            return _generate_command(args)
        if args.command == "run":
            return _run_command(args)
        if args.command == "analyse":
            return _analyse_command(args)
        raise AssertionError(args.command)
    except (QualificationError, UserFacingError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
