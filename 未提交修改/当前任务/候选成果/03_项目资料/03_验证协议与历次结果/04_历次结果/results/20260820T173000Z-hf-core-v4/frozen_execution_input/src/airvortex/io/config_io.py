"""Strict JSON configuration I/O with deterministic formatting."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any

from airvortex.core import ConfigError, SimulationConfig, sha256_bytes


class StrictJSONError(ConfigError):
    """JSON syntax that would otherwise silently lose or invent information."""


def _reject_constant(token: str) -> None:
    raise StrictJSONError(f"non-standard JSON numeric constant is forbidden: {token}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StrictJSONError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def loads_json_strict(text: str) -> Any:
    if not isinstance(text, str):
        raise TypeError("JSON input must be text")
    try:
        return json.loads(
            text,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except StrictJSONError:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise StrictJSONError(f"invalid JSON: {exc}") from exc


def dumps_json_strict(value: object, *, indent: int | None = 2) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=indent,
            separators=(",", ":") if indent is None else None,
        )
    except (TypeError, ValueError) as exc:
        raise StrictJSONError(f"value is not strict JSON: {exc}") from exc


def canonical_json_bytes(value: object) -> bytes:
    return (dumps_json_strict(value, indent=None) + "\n").encode("utf-8")


def config_sha256(config: SimulationConfig) -> str:
    if not isinstance(config, SimulationConfig):
        raise TypeError("config must be a SimulationConfig")
    return sha256_bytes(canonical_json_bytes(config.to_dict()))


def read_json_strict(path: str | Path) -> Any:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise StrictJSONError(f"cannot read JSON file {path}: {exc}") from exc
    return loads_json_strict(text)


def _atomic_write_text(path: Path, text: str, *, overwrite: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temporary, path)
        else:
            # A same-directory hard link is an atomic no-clobber publish on POSIX.
            os.link(temporary, path)
            temporary.unlink()
    finally:
        if temporary.exists():
            temporary.unlink()


def save_config_json(
    path: str | Path, config: SimulationConfig, *, overwrite: bool = False
) -> Path:
    if not isinstance(config, SimulationConfig):
        raise TypeError("config must be a SimulationConfig")
    target = Path(path)
    _atomic_write_text(
        target,
        dumps_json_strict(config.to_dict(), indent=2) + "\n",
        overwrite=overwrite,
    )
    return target


def load_config_json(path: str | Path) -> SimulationConfig:
    return SimulationConfig.from_dict(read_json_strict(path))
