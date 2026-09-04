#!/usr/bin/env python3
"""Check local links and fences in active entry docs, not archived scientific evidence.

Supports ordinary inline Markdown links used by these entry documents. It does
not claim to validate external URLs, heading anchors, or full Pandoc semantics.
"""
from __future__ import annotations
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
ENTRY_DOCS = ("AGENTS.md", "README.md", "90_候选工作区/README.md",
              "90_候选工作区/交接说明_current.md")
FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})(.*)$")
LINK = re.compile(r"\[[^\]\n]*\]\((<[^>\n]+>|[^\s)]+)(?:\s+\"[^\"]*\")?\)")
INLINE_CODE = re.compile(r"(`+).*?\1")

def check_document(root: Path, relative: str) -> list[str]:
    root = root.resolve()
    path = root / relative
    if not path.is_file():
        return [f"{relative}: missing entry document"]
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeError as exc:
        return [f"{relative}: not UTF-8: {exc}"]
    errors: list[str] = []
    fence: tuple[str, int] | None = None
    for number, line in enumerate(text.splitlines(), 1):
        where = f"{relative}:{number}"
        if "\x00" in line or "\ufeff" in line:
            errors.append(f"{where}: unexpected NUL or BOM")
        marker = FENCE.match(line)
        if marker:
            token, suffix = marker.groups()
            if fence is None:
                fence = (token[0], len(token))
            elif token[0] == fence[0] and len(token) >= fence[1] and not suffix.strip():
                fence = None
            continue
        if fence is not None:
            continue
        # Match links before dropping inline-code spans: link labels may be code.
        scrubbed = INLINE_CODE.sub(lambda m: " " * len(m.group(0)), line)
        for match in LINK.finditer(line):
            if scrubbed[match.start():match.start()+1] != "[":
                continue
            destination = match.group(1).strip("<>")
            try:
                parts = urlsplit(destination)
            except ValueError:
                errors.append(f"{where}: malformed link {destination!r}")
                continue
            if parts.scheme or parts.netloc or not parts.path:
                continue
            target = (path.parent / unquote(parts.path)).resolve()
            if not target.is_relative_to(root):
                errors.append(f"{where}: link escapes repository: {destination}")
            elif not target.exists():
                errors.append(f"{where}: missing local target: {destination}")
    if fence is not None:
        errors.append(f"{relative}: unclosed code fence")
    if not text.endswith("\n"):
        errors.append(f"{relative}: missing final newline")
    return errors

def main() -> int:
    errors = [error for item in ENTRY_DOCS for error in check_document(ROOT, item)]
    if errors:
        print("ENTRY DOCS FAIL\n" + "\n".join(errors))
        return 1
    print(f"ENTRY DOCS PASS: {len(ENTRY_DOCS)} documents; local targets and fences only")
    return 0

if __name__ == "__main__":
    sys.exit(main())
