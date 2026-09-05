#!/usr/bin/env python3
"""Resolve an exact pre-migration repository path without creating aliases."""
from __future__ import annotations
import argparse
import json
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "03_研究记录/维护/2026-09-05_目录重组/migration.json"

def resolve_legacy(path: str, mappings: list[dict]) -> str:
    normalized = path.replace("\\", "/").removeprefix("./").rstrip("/")
    if PurePosixPath(normalized).is_absolute() or ".." in PurePosixPath(normalized).parts:
        raise ValueError("use a repository-relative path without '..'")
    for item in sorted(mappings, key=lambda x: len(x["old"]), reverse=True):
        old, new = item["old"], item["new"]
        if normalized == old or normalized.startswith(old + "/"):
            return new + normalized[len(old):]
    raise ValueError("path is not covered by this migration")

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    args = parser.parse_args()
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        target = resolve_legacy(args.path, manifest["prefixes"])
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(2, f"{exc}\n")
    if not (ROOT / target).exists():
        parser.exit(2, f"mapped target is not present: {target}\n")
    print(target)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
