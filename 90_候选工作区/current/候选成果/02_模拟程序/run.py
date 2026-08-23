#!/usr/bin/env python3
"""Source-tree launcher; equivalent to ``python -m airvortex``."""

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from airvortex.__main__ import main  # noqa: E402 - source-tree bootstrap precedes import


if __name__ == "__main__":
    raise SystemExit(main())
