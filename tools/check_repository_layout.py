#!/usr/bin/env python3
"""Validate the shared structure and active-path contracts of an IYPT repository."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
CURRENT = ROOT / "90_候选工作区/current"

REQUIRED_DIRS = (
    "00_项目控制",
    "01_最终理论",
    "02_模拟程序",
    "03_项目资料",
    "90_候选工作区/current",
    "90_候选工作区/current/管理",
    "90_候选工作区/current/审查与复现",
    "90_候选工作区/current/审查与复现/当前审查",
    "90_候选工作区/current/审查与复现/历史审查",
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
    "90_候选工作区/current/管理/变更映射.md",
    "90_候选工作区/current/管理/修改记录.md",
    "90_候选工作区/current/管理/验证记录.md",
    "90_候选工作区/current/审查与复现/README.md",
    "90_候选工作区/current/审查与复现/当前审查/README.md",
    "90_候选工作区/archive/README.md",
)
ALLOWED_TRACKED_TOP_LEVEL_DIRS = {
    ".github",
    "00_项目控制",
    "01_最终理论",
    "02_模拟程序",
    "03_项目资料",
    "90_候选工作区",
    "tools",
}
RESERVED_CURRENT_DIRS = {"管理", "审查与复现"}
CORE_ACTIVE_DOCS = (
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
    "90_候选工作区/current/管理/变更映射.md",
    "90_候选工作区/current/管理/修改记录.md",
    "90_候选工作区/current/审查与复现/README.md",
    "90_候选工作区/current/审查与复现/当前审查/README.md",
    "90_候选工作区/archive/README.md",
)
SINGLE_STATE_DOCS = (
    "README.md",
    "AGENTS.md",
    "00_项目控制/README.md",
    "00_项目控制/LAYOUT.md",
    "00_项目控制/WORKFLOW.md",
    "90_候选工作区/README.md",
    "90_候选工作区/current/README.md",
)
FORBIDDEN_ACTIVE_TEXT = (
    "每次任务前阅读/",
    "未提交修改/当前任务/",
    "02-模拟程序/",
    "03-项目资料/",
    "01-最终理论/",
    "/home/cjt/",
)
FORBIDDEN_EXECUTABLE_TEXT = FORBIDDEN_ACTIVE_TEXT
EXECUTABLE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".sh", ".bash"}
FROZEN_PATH_PARTS = {"历史审查", "archive", "原始同步包"}
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
STATE_ASSIGNMENT_RE = re.compile(r"(?m)^[A-Z][A-Z0-9_]{2,}\s*=\s*\S+")


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace").strip() or "git ls-files failed")
    return [Path(item) for item in result.stdout.decode("utf-8", "surrogateescape").split("\0") if item]


def is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def check_markdown_link(path: Path, raw_target: str, errors: list[str]) -> None:
    target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
    if not target or target.startswith(("#", "http://", "https://", "mailto:")):
        return
    target = target.split("#", 1)[0]
    resolved = (path.parent / target).resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError:
        errors.append(f"link escapes repository in {path.relative_to(ROOT)}: {raw_target}")
        return
    if not resolved.exists():
        errors.append(f"broken link in {path.relative_to(ROOT)}: {raw_target}")


def main() -> int:
    errors: list[str] = []

    for relative in REQUIRED_DIRS:
        if not (ROOT / relative).is_dir():
            errors.append(f"missing directory: {relative}")
    for relative in REQUIRED_FILES:
        if not (ROOT / relative).is_file():
            errors.append(f"missing file: {relative}")

    try:
        tracked = tracked_files()
    except RuntimeError as exc:
        errors.append(str(exc))
        tracked = []

    for relative in tracked:
        if len(relative.parts) > 1 and relative.parts[0] not in ALLOWED_TRACKED_TOP_LEVEL_DIRS:
            errors.append(f"unexpected tracked top-level directory: {relative.parts[0]}")

    payload_dirs = []
    if CURRENT.is_dir():
        payload_dirs = sorted(
            path for path in CURRENT.iterdir()
            if path.is_dir() and path.name not in RESERVED_CURRENT_DIRS
        )
    if len(payload_dirs) != 1:
        errors.append(
            "current must contain exactly one candidate payload directory in addition to 管理 and 审查与复现"
        )
        payload = None
    else:
        payload = payload_dirs[0]
        if not (payload / "README.md").is_file():
            errors.append(f"candidate payload is missing README.md: {payload.relative_to(ROOT)}")

    management = CURRENT / "管理"
    if management.is_dir() and not list(management.glob("*验收清单.md")):
        errors.append("current/管理 must contain at least one *验收清单.md")

    status_path = ROOT / "00_项目控制/STATUS.md"
    if status_path.is_file() and "90_候选工作区/current/" not in status_path.read_text(encoding="utf-8"):
        errors.append("STATUS.md must identify 90_候选工作区/current/")

    active_docs = [ROOT / relative for relative in CORE_ACTIVE_DOCS]
    if payload is not None:
        active_docs.append(payload / "README.md")
    if management.is_dir():
        active_docs.extend(sorted(management.glob("*验收清单.md")))

    for path in active_docs:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_ACTIVE_TEXT:
            if token in text:
                errors.append(f"non-current path reference {token!r} in {path.relative_to(ROOT)}")
        for raw_target in LINK_RE.findall(text):
            check_markdown_link(path, raw_target, errors)

    for relative in SINGLE_STATE_DOCS:
        path = ROOT / relative
        if path.is_file() and STATE_ASSIGNMENT_RE.search(path.read_text(encoding="utf-8")):
            errors.append(f"dynamic state assignment outside STATUS.md: {relative}")

    scan_roots = [ROOT / "02_模拟程序"]
    if payload is not None:
        scan_roots.append(payload)
    for relative in tracked:
        absolute = ROOT / relative
        if absolute.suffix.lower() not in EXECUTABLE_SUFFIXES:
            continue
        if relative.name.endswith("_impl.py"):
            continue
        if any(part in FROZEN_PATH_PARTS for part in relative.parts):
            continue
        if not any(is_under(absolute, scan_root) for scan_root in scan_roots):
            continue
        try:
            text = absolute.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for token in FORBIDDEN_EXECUTABLE_TEXT:
            if token in text:
                errors.append(f"stale executable path {token!r} in {relative}")

    if errors:
        print("repository layout validation: FAIL")
        for error in sorted(set(errors)):
            print(f"- {error}")
        return 1

    print("repository layout validation: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
