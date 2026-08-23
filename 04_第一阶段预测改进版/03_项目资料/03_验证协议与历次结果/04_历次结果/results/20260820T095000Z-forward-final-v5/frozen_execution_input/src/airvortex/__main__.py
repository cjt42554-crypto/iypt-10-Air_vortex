"""Application entry: no arguments opens the GUI; arguments use the CLI."""

from __future__ import annotations

import sys
from typing import Sequence


def _launch_gui() -> int:
    try:
        from airvortex.gui import launch
    except (ImportError, AttributeError) as exc:
        print(f"airvortex: GUI is unavailable: {exc}", file=sys.stderr)
        return 2
    result = launch()
    return 0 if result is None else int(result)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments:
        from airvortex.cli import main as cli_main

        return cli_main(arguments)
    return _launch_gui()


if __name__ == "__main__":
    raise SystemExit(main())
