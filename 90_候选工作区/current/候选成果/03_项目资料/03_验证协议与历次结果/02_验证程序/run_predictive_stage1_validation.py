#!/usr/bin/env python3
"""Layout-v2 entrypoint for the preserved Stage1 validator implementation."""

from __future__ import annotations

import run_predictive_stage1_validation_impl as _impl

# The implementation and its scientific gates are preserved byte-for-byte.
# Only repository-governance path prefixes are adapted to layout v2.
_impl.ALLOWED_REPOSITORY_PREFIXES = (
    "90_候选工作区/",
    "00_项目控制/",
)

main = _impl.main
compose_acceptance = _impl.compose_acceptance


def __getattr__(name: str):
    return getattr(_impl, name)


if __name__ == "__main__":
    raise SystemExit(main())
