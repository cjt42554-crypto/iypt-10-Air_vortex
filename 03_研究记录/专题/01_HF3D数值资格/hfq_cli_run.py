"""Configuration audit, campaign generation, and sequential execution commands."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
from typing import Any, Mapping
from hfq_core import (
    audit_config,
    audit_markdown,
    audit_refinement_design,
    generate_campaign,
    read_json,
    write_json,
)
from hfq_cli_common import UserFacingError

def _audit_command(args: argparse.Namespace) -> int:
    config = read_json(args.config)
    result = audit_config(config, label=args.label or args.config.name)
    if args.json_output:
        write_json(args.json_output, result)
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(audit_markdown(result), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0

def _generate_command(args: argparse.Namespace) -> int:
    manifest = generate_campaign(args.spec, args.output)
    print(
        json.dumps(
            {
                "status": "generated",
                "output": str(args.output.resolve()),
                "case_count": len(manifest["cases"]),
                "physical_signature_sha256": manifest["physical_signature_sha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0

def _load_archive_for_resume(path: Path) -> bool:
    try:
        from airvortex.io import load_run_archive

        archive = load_run_archive(path)
    except Exception:
        return False
    status = getattr(getattr(archive, "result", None), "status", None)
    return getattr(status, "value", str(status)) == "completed"

def _run_command(args: argparse.Namespace) -> int:
    manifest_path = args.manifest.resolve()
    manifest = read_json(manifest_path)
    design_audit = audit_refinement_design(manifest_path)
    if design_audit.get("pass") is not True:
        reasons = design_audit.get("reasons", [])
        detail = "; ".join(str(item) for item in reasons) or "unspecified design failure"
        raise UserFacingError(f"refinement design gate failed: {detail}")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise UserFacingError("manifest has no cases")
    config_root = manifest_path.parent
    output_root = args.output.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    failed: list[str] = []
    skipped: list[str] = []
    commands: list[list[str]] = []
    selected_ids = set(getattr(args, "only", []) or [])
    manifest_ids = {
        str(item.get("id")) for item in cases if isinstance(item, Mapping)
    }
    unknown_ids = sorted(selected_ids - manifest_ids)
    if unknown_ids:
        raise UserFacingError(
            "--only names unknown cases: " + ", ".join(unknown_ids)
        )

    for raw_case in cases:
        if not isinstance(raw_case, Mapping):
            raise UserFacingError("manifest contains a non-object case")
        case_id = str(raw_case.get("id", ""))
        config_file = raw_case.get("config_file")
        if not case_id or not isinstance(config_file, str):
            raise UserFacingError("case requires id and config_file")
        if selected_ids and case_id not in selected_ids:
            skipped.append(f"{case_id}->not-selected")
            continue
        config_path = (config_root / config_file).resolve()
        reuse_case_id = raw_case.get("reuse_case_id")
        if reuse_case_id is not None:
            if not isinstance(reuse_case_id, str) or not reuse_case_id:
                raise UserFacingError(f"{case_id}: reuse_case_id must be a non-empty string")
            skipped.append(f"{case_id}->reuse:{reuse_case_id}")
            continue
        archive_path = output_root / case_id
        log_path = output_root / f"{case_id}.log"
        if archive_path.exists():
            if args.resume and _load_archive_for_resume(archive_path):
                skipped.append(case_id)
                continue
            raise UserFacingError(
                f"output exists and is not an accepted resumable archive: {archive_path}"
            )
        command = [
            args.python,
            "-m",
            "airvortex",
            "run",
            "--backend",
            "high_fidelity",
            "--config",
            str(config_path),
            "--output",
            str(archive_path),
        ]
        commands.append(command)
        if args.dry_run:
            print(" ".join(command))
            continue
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.run(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
        if process.returncode != 0:
            failed.append(case_id)
            if not args.keep_going:
                break

    summary = {
        "status": "failed" if failed else "dry_run" if args.dry_run else "completed",
        "output": str(output_root),
        "commands": commands if args.dry_run else None,
        "failed": failed,
        "skipped": skipped,
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 1 if failed else 0
