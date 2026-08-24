#!/usr/bin/env python3
"""Validate the repository's single-source AI work policy and active layout."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_DIRS = (
    "01_最终理论",
    "02_模拟程序",
    "90_候选工作区/current",
    ".github/workflows",
    "tools",
)

REQUIRED_FILES = (
    "AGENTS.md",
    "README.md",
    "01_最终理论/README.md",
    "02_模拟程序/README.md",
    "90_候选工作区/current/.gitkeep",
    "90_候选工作区/交接说明_current.md",
    "90_候选工作区/交接说明_history.md",
    ".github/workflows/repository-policy.yml",
    "tools/check_repository_policy.py",
)

ALLOWED_TOP_LEVEL = {
    ".gitattributes",
    ".github",
    ".gitignore",
    "01_最终理论",
    "02_模拟程序",
    "90_候选工作区",
    "AGENTS.md",
    "README.md",
    "tools",
}

REMOVED_PATHS = (
    "00_" + "项目控制",
    "03_" + "项目资料",
    "00-" + "项目控制",
    "03-" + "项目资料",
    "90_候选工作区/" + "archive",
)

REMOVED_REFERENCES = REMOVED_PATHS + (
    "repository-" + "layout.yml",
    "check_repository_" + "layout.py",
    "WORK" + "FLOW.md",
    "LAY" + "OUT.md",
    "archive/pre-" + "03-90-consolidation",
    "01-" + "最终理论",
    "02-" + "模拟程序",
)

REQUIRED_AGENT_TEXT = (
    "本文件是本仓库唯一有效的 AI 自主工作规范",
    "01_最终理论/**",
    "02_模拟程序/**",
    "90_候选工作区/current/**",
    "交接说明_current.md",
    "交接说明_history.md",
    "最多保留最近 12 条",
)

LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
HISTORY_ENTRY_RE = re.compile(r"(?m)^##\s+(?!说明\b|格式\b).+")


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(message or "git ls-files failed")
    return [
        Path(item)
        for item in result.stdout.decode("utf-8", "surrogateescape").split("\0")
        if item
    ]


def readable_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def check_link(source: Path, raw_target: str, errors: list[str]) -> None:
    target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
    if not target or target.startswith(("#", "http://", "https://", "mailto:")):
        return
    target = target.split("#", 1)[0]
    resolved = (source.parent / target).resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError:
        errors.append(
            f"link escapes repository in {source.relative_to(ROOT)}: {raw_target}"
        )
        return
    if not resolved.exists():
        errors.append(
            f"broken link in {source.relative_to(ROOT)}: {raw_target}"
        )


def main() -> int:
    errors: list[str] = []

    for relative in REQUIRED_DIRS:
        if not (ROOT / relative).is_dir():
            errors.append(f"missing directory: {relative}")
    for relative in REQUIRED_FILES:
        if not (ROOT / relative).is_file():
            errors.append(f"missing file: {relative}")

    for relative in REMOVED_PATHS:
        if (ROOT / relative).exists():
            errors.append(f"removed path still exists: {relative}")

    root_readme = ROOT / "README.md"
    if root_readme.is_file() and root_readme.read_bytes() != b"":
        errors.append("root README.md must be exactly zero bytes")

    agents_files = sorted(ROOT.rglob("AGENTS.md"))
    if agents_files != [ROOT / "AGENTS.md"]:
        displayed = ", ".join(
            str(path.relative_to(ROOT)) for path in agents_files
        ) or "<none>"
        errors.append(f"AGENTS.md must exist only at repository root: {displayed}")

    agents_path = ROOT / "AGENTS.md"
    agents_text = readable_text(agents_path) if agents_path.is_file() else None
    if agents_text is not None:
        for required in REQUIRED_AGENT_TEXT:
            if required not in agents_text:
                errors.append(f"AGENTS.md is missing required rule: {required}")

    current_path = ROOT / "90_候选工作区/交接说明_current.md"
    history_path = ROOT / "90_候选工作区/交接说明_history.md"
    if current_path.is_file() and current_path.stat().st_size > 12_000:
        errors.append("current handoff is too large; keep only next-task context")
    if history_path.is_file():
        history_text = history_path.read_text(encoding="utf-8")
        if len(HISTORY_ENTRY_RE.findall(history_text)) > 12:
            errors.append("handoff history contains more than 12 entries")
        if history_path.stat().st_size > 30_000:
            errors.append("handoff history is too large")

    try:
        tracked = tracked_files()
    except RuntimeError as exc:
        errors.append(str(exc))
        tracked = []

    for relative in tracked:
        if len(relative.parts) > 1:
            top_level = relative.parts[0]
        else:
            top_level = relative.name
        if top_level not in ALLOWED_TOP_LEVEL:
            errors.append(f"unexpected tracked top-level path: {top_level}")

        normalized = relative.as_posix()
        for removed in REMOVED_PATHS:
            if normalized == removed or normalized.startswith(removed + "/"):
                errors.append(f"removed tracked path remains: {normalized}")

        text = readable_text(ROOT / relative)
        if text is None:
            continue
        for removed in REMOVED_REFERENCES:
            if removed in text:
                errors.append(
                    f"obsolete repository reference {removed!r} in {normalized}"
                )

    link_sources = (
        ROOT / "AGENTS.md",
        ROOT / "01_最终理论/README.md",
        ROOT / "02_模拟程序/README.md",
        ROOT / "90_候选工作区/交接说明_current.md",
        ROOT / "90_候选工作区/交接说明_history.md",
    )
    for source in link_sources:
        text = readable_text(source)
        if text is None:
            continue
        for raw_target in LINK_RE.findall(text):
            check_link(source, raw_target, errors)

    if errors:
        print("repository policy validation: FAIL")
        for error in sorted(set(errors)):
            print(f"- {error}")
        return 1

    print("repository policy validation: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
