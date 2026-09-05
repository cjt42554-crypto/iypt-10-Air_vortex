"""Strict JSON, canonical hashing, and dotted-path configuration helpers."""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping
from hfq_types import ALLOWED_DISCRETIZATION_PATHS, JsonObject, QualificationError

def read_json(path: str | Path) -> JsonObject:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise QualificationError(f"{path} must contain a JSON object")
    return value

def write_json(path: str | Path, value: object) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    target.write_text(payload + "\n", encoding="utf-8")

def canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()

def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise QualificationError(f"{label} must be an object")
    return value

def _finite_positive(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise QualificationError(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise QualificationError(f"{label} must be finite and > 0")
    return number

def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise QualificationError(f"{label} must be an integer >= 1")
    return value

def deep_get(mapping: Mapping[str, Any], dotted_path: str) -> Any:
    current: Any = mapping
    for part in dotted_path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise QualificationError(f"missing configuration path: {dotted_path}")
        current = current[part]
    return current

def deep_set(mapping: MutableMapping[str, Any], dotted_path: str, value: Any) -> None:
    parts = dotted_path.split(".")
    current: MutableMapping[str, Any] = mapping
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, MutableMapping):
            raise QualificationError(f"missing or non-object configuration path: {dotted_path}")
        current = child
    if parts[-1] not in current:
        raise QualificationError(f"missing configuration path: {dotted_path}")
    current[parts[-1]] = value

def remove_dotted_paths(mapping: Mapping[str, Any], paths: Iterable[str]) -> JsonObject:
    result = deepcopy(dict(mapping))
    for dotted_path in paths:
        parts = dotted_path.split(".")
        current: Any = result
        for part in parts[:-1]:
            if not isinstance(current, MutableMapping) or part not in current:
                current = None
                break
            current = current[part]
        if isinstance(current, MutableMapping):
            current.pop(parts[-1], None)
    return result

def physical_signature(config: Mapping[str, Any]) -> JsonObject:
    """Return the input identity after removing only registered discretization controls."""

    return remove_dotted_paths(config, ALLOWED_DISCRETIZATION_PATHS)
