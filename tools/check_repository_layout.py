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
    "01_最终理论/README.md",
    "02_模拟程序/README.md",
    "03_项目资料/README.md",
    "90_候选工作区/README.md",
    "90_候选工作区/current/README.md",
    "90_候选工作区/current/候选成果/README.md",
    "90_候选工作区/archive/README.md",
)
ALLOWED_TOP_LEVEL = (
    ".git",
    ".github",
    ".gitignore",
    "README.md",
    "AGENTS.md",
    "00_项目控制",
    "01_最终理论",
    "02_模拟程序",
    "03_项目资料",
    "90_候选工作区",
    "tools",
)
ACTIVE_DOCS = (
    "README.md",
    "AGENTS.md",
    "00_项目控制/README.md",
    "00_项目控制/STATUS.md",
    "00_项目控制/LAYOUT.md",
    "00_项目控制/WORKFLOW.md",
    "01_最终理论/README.md",
    "02_模拟程序/README.md",
    "03_项目资料/README.md",
    "90_候选工作区/README.md",
    "90_候选工作区/current/README.md",
    "90_候选工作区/current/候选成果/README.md",
    "90_候选工作区/archive/README.md",
    "90_候选工作区/current/管理/修改记录.md",
    "90_候选工作区/current/管理/变更映射.md",
    "90_候选工作区/current/管理/验收清单.md",
    "90_候选工作区/current/管理/验证记录.md",
    "90_候选工作区/current/审查与复现/README.md",
    "90_候选工作区/current/审查与复现/当前审查/README.md",
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
    allowed_top_level = set(ALLOWED_TOP_LEVEL)
    for path in ROOT.iterdir():
        if path.name not in allowed_top_level:
            errors.append(f"unexpected top-level path: {path.name}")

    status_path = ROOT / "00_项目控制/STATUS.md"
    if status_path.is_file() and "90_候选工作区/current/" not in status_path.read_text(encoding="utf-8"):
        errors.append("STATUS.md must identify the current candidate path")

    for relative in ACTIVE_DOCS:
        path = ROOT / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
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
