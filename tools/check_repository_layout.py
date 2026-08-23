#!/usr/bin/env python3
"""Validate the shared IYPT research-repository layout."""

from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_DIRS = (
    "00_项目控制",
    "01_最终理论",
    "02_模拟程序",
    "03_项目资料",
    "90_候选工作区/current",
    "90_候选工作区/archive",
)
REQUIRED_FILES = (
    "README.md",
    "AGENTS.md",
    "00_项目控制/README.md",
    "00_项目控制/STATUS.md",
    "00_项目控制/LAYOUT.md",
    "00_项目控制/WORKFLOW.md",
    "00_项目控制/LAYOUT_VERSION.txt",
    "01_最终理论/README.md",
    "02_模拟程序/README.md",
    "03_项目资料/README.md",
    "90_候选工作区/README.md",
    "90_候选工作区/current/README.md",
    "90_候选工作区/archive/README.md",
)
LEGACY_TOP_LEVEL = (
    "每次任务前阅读",
    "未提交修改",
    "02_最终模拟程序",
    "01-最终理论",
    "02-模拟程序",
    "03-项目资料",
)
ACTIVE_DOCS = (
    "README.md",
    "AGENTS.md",
    "00_项目控制/README.md",
    "00_项目控制/STATUS.md",
    "00_项目控制/WORKFLOW.md",
    "01_最终理论/README.md",
    "02_模拟程序/README.md",
    "03_项目资料/README.md",
    "90_候选工作区/README.md",
    "90_候选工作区/current/README.md",
)
FORBIDDEN_ACTIVE_TEXT = (
    "每次任务前阅读/",
    "未提交修改/当前任务/",
    "/home/cjt/",
)
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def main() -> int:
    errors: list[str] = []

    for relative in REQUIRED_DIRS:
        if not (ROOT / relative).is_dir():
            errors.append(f"missing directory: {relative}")
    for relative in REQUIRED_FILES:
        if not (ROOT / relative).is_file():
            errors.append(f"missing file: {relative}")
    for relative in LEGACY_TOP_LEVEL:
        if (ROOT / relative).exists():
            errors.append(f"legacy top-level path still exists: {relative}")

    version_path = ROOT / "00_项目控制/LAYOUT_VERSION.txt"
    if version_path.is_file() and version_path.read_text(encoding="utf-8").strip() != "2":
        errors.append("LAYOUT_VERSION.txt must contain 2")

    status_path = ROOT / "00_项目控制/STATUS.md"
    if status_path.is_file() and "90_候选工作区/current/" not in status_path.read_text(encoding="utf-8"):
        errors.append("STATUS.md must identify the current candidate path")

    for relative in ACTIVE_DOCS:
        path = ROOT / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_ACTIVE_TEXT:
            if token in text:
                errors.append(f"legacy active reference {token!r} in {relative}")
        for raw_target in LINK_RE.findall(text):
            target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
            if not target or target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            target = target.split("#", 1)[0]
            resolved = (path.parent / target).resolve()
            try:
                resolved.relative_to(ROOT.resolve())
            except ValueError:
                errors.append(f"link escapes repository in {relative}: {raw_target}")
                continue
            if not resolved.exists():
                errors.append(f"broken link in {relative}: {raw_target}")

    if errors:
        print("repository layout validation: FAIL")
        for error in errors:
            print(f"- {error}")
        return 1

    print("repository layout validation: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
