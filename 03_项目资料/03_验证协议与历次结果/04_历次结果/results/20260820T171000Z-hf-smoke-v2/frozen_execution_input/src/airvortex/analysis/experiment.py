"""Strict CSV + JSON experiment interchange with explicit units and roles."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np

from ._common import DataValidationError


CALIBRATION = "calibration"
SEALED_VALIDATION = "sealed_validation"
VALID_DATASET_ROLES = frozenset({CALIBRATION, SEALED_VALIDATION})


def _reject_json_constant(token: str) -> None:
    """Reject Python's non-standard ``NaN``/``Infinity`` JSON extensions."""

    raise DataValidationError(
        f"non-standard JSON numeric constant is forbidden in metadata: {token}"
    )


def _parse_finite_json_float(token: str) -> float:
    """Parse a JSON number without permitting exponent overflow to infinity."""

    value = float(token)
    if not math.isfinite(value):
        raise DataValidationError(
            f"non-finite JSON number is forbidden in metadata: {token}"
        )
    return value


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build an object while rejecting duplicate keys at every nesting level."""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DataValidationError(f"duplicate JSON object key in metadata: {key}")
        result[key] = value
    return result


def _loads_metadata_json_strict(text: str) -> Any:
    """Decode exactly one standards-compliant metadata JSON value."""

    try:
        # ``json.loads`` itself rejects any non-whitespace trailing content.
        return json.loads(
            text,
            parse_constant=_reject_json_constant,
            parse_float=_parse_finite_json_float,
            object_pairs_hook=_unique_json_object,
        )
    except DataValidationError:
        raise
    except json.JSONDecodeError as exc:
        raise DataValidationError(f"invalid metadata JSON: {exc}") from exc


def _dumps_metadata_json_strict(value: object) -> str:
    """Encode metadata as standard JSON and reject non-finite nested values."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            # Preserve dataset column order: it is also the required CSV order.
            sort_keys=False,
        )
    except (TypeError, ValueError) as exc:
        raise DataValidationError(f"metadata is not strict JSON: {exc}") from exc


# unit -> (physical dimension, multiplier to the corresponding SI unit)
_UNIT_TABLE: dict[str, tuple[str, float]] = {
    "1": ("dimensionless", 1.0),
    "%": ("dimensionless", 0.01),
    "s": ("time", 1.0),
    "ms": ("time", 1.0e-3),
    "min": ("time", 60.0),
    "m": ("length", 1.0),
    "cm": ("length", 1.0e-2),
    "mm": ("length", 1.0e-3),
    "rad/s": ("angular_speed", 1.0),
    "rpm": ("angular_speed", 2.0 * np.pi / 60.0),
    "N*m": ("torque", 1.0),
    "mN*m": ("torque", 1.0e-3),
    "Pa": ("pressure", 1.0),
    "kPa": ("pressure", 1.0e3),
    "m/s": ("speed", 1.0),
    "mm/s": ("speed", 1.0e-3),
    "kg/m^3": ("density", 1.0),
    "Pa*s": ("dynamic_viscosity", 1.0),
    "mPa*s": ("dynamic_viscosity", 1.0e-3),
    "N/m": ("surface_tension", 1.0),
    "mN/m": ("surface_tension", 1.0e-3),
}


@dataclass(frozen=True)
class ColumnSpec:
    """Mapping from a CSV column to a canonical observable in SI."""

    source: str
    canonical: str
    unit: str
    role: str = "observable"

    def __post_init__(self) -> None:
        if not self.source.strip() or not self.canonical.strip():
            raise DataValidationError("column source and canonical names cannot be empty")
        if self.role not in {"time", "observable"}:
            raise DataValidationError(
                f"column {self.source!r} has invalid role {self.role!r}"
            )
        if self.unit not in _UNIT_TABLE:
            raise DataValidationError(
                f"column {self.source!r} uses unsupported unit {self.unit!r}"
            )
        dimension = _UNIT_TABLE[self.unit][0]
        if self.role == "time" and dimension != "time":
            raise DataValidationError("the time column must use a time unit")
        if self.role == "time" and self.canonical != "time":
            raise DataValidationError("the time column canonical name must be 'time'")

    @property
    def dimension(self) -> str:
        return _UNIT_TABLE[self.unit][0]

    @property
    def to_si_factor(self) -> float:
        return _UNIT_TABLE[self.unit][1]


@dataclass(frozen=True)
class ExperimentDataset:
    """Validated experimental data stored internally in SI units."""

    time_s: np.ndarray
    observables: Mapping[str, np.ndarray]
    role: str
    columns: tuple[ColumnSpec, ...]
    metadata: Mapping[str, Any]
    source_csv: str | None = None
    source_metadata: str | None = None

    def __post_init__(self) -> None:
        if self.role not in VALID_DATASET_ROLES:
            raise DataValidationError(
                f"dataset role must be one of {sorted(VALID_DATASET_ROLES)}"
            )
        time = np.asarray(self.time_s, dtype=float)
        if time.ndim != 1 or len(time) < 1:
            raise DataValidationError("experiment time must be a non-empty 1D array")
        if not np.all(np.isfinite(time)):
            raise DataValidationError("experiment time contains a missing/non-finite value")
        if len(time) > 1 and not np.all(np.diff(time) > 0.0):
            raise DataValidationError("experiment time must be strictly increasing")
        time = time.copy()
        time.setflags(write=False)

        checked: dict[str, np.ndarray] = {}
        for name, raw in self.observables.items():
            values = np.asarray(raw, dtype=float)
            if values.ndim != 1 or len(values) != len(time):
                raise DataValidationError(
                    f"observable {name!r} must be 1D with the same length as time"
                )
            if not np.all(np.isfinite(values)):
                raise DataValidationError(
                    f"observable {name!r} contains a missing/non-finite value"
                )
            values = values.copy()
            values.setflags(write=False)
            checked[str(name)] = values
        if not checked:
            raise DataValidationError("experiment must contain at least one observable")

        time_specs = [spec for spec in self.columns if spec.role == "time"]
        observable_specs = [spec for spec in self.columns if spec.role == "observable"]
        if len(time_specs) != 1:
            raise DataValidationError("metadata must define exactly one time column")
        canonical = [spec.canonical for spec in observable_specs]
        if len(canonical) != len(set(canonical)):
            raise DataValidationError("observable canonical names must be unique")
        if set(canonical) != set(checked):
            raise DataValidationError(
                "observable data keys do not match the metadata column mapping"
            )

        object.__setattr__(self, "time_s", time)
        object.__setattr__(self, "observables", MappingProxyType(checked))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def units(self) -> Mapping[str, str]:
        """Return canonical SI units by observable name."""

        si_units: dict[str, str] = {}
        for spec in self.columns:
            if spec.role == "observable":
                si_units[spec.canonical] = _si_unit_for_dimension(spec.dimension)
        return MappingProxyType(si_units)


def _si_unit_for_dimension(dimension: str) -> str:
    return {
        "dimensionless": "1",
        "time": "s",
        "length": "m",
        "angular_speed": "rad/s",
        "torque": "N*m",
        "pressure": "Pa",
        "speed": "m/s",
        "density": "kg/m^3",
        "dynamic_viscosity": "Pa*s",
        "surface_tension": "N/m",
    }[dimension]


def _metadata_columns(document: Mapping[str, Any]) -> tuple[ColumnSpec, ...]:
    raw_columns = document.get("columns")
    if not isinstance(raw_columns, Mapping) or not raw_columns:
        raise DataValidationError("metadata 'columns' must be a non-empty object")
    specs: list[ColumnSpec] = []
    for source, raw_spec in raw_columns.items():
        if not isinstance(source, str) or not isinstance(raw_spec, Mapping):
            raise DataValidationError("each column mapping must be an object")
        canonical = raw_spec.get("canonical", raw_spec.get("observable"))
        unit = raw_spec.get("unit")
        role = raw_spec.get("role", "observable")
        if not all(isinstance(item, str) for item in (canonical, unit, role)):
            raise DataValidationError(
                f"column {source!r} requires string canonical, unit, and role fields"
            )
        specs.append(ColumnSpec(source, canonical, unit, role))
    if len({spec.source for spec in specs}) != len(specs):
        raise DataValidationError("CSV source column names must be unique")
    if sum(spec.role == "time" for spec in specs) != 1:
        raise DataValidationError("metadata must define exactly one time column")
    if not any(spec.role == "observable" for spec in specs):
        raise DataValidationError("metadata must define at least one observable column")
    return tuple(specs)


def load_experiment(
    csv_path: str | Path,
    metadata_path: str | Path | None = None,
) -> ExperimentDataset:
    """Load and strictly validate a CSV plus its adjacent ``.meta.json`` file."""

    csv_file = Path(csv_path)
    if metadata_path is None:
        metadata_file = csv_file.with_suffix(".meta.json")
    else:
        metadata_file = Path(metadata_path)
    try:
        metadata_text = metadata_file.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise DataValidationError(f"metadata file not found: {metadata_file}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise DataValidationError(f"cannot read metadata file: {exc}") from exc
    document = _loads_metadata_json_strict(metadata_text)
    if not isinstance(document, Mapping):
        raise DataValidationError("metadata JSON root must be an object")
    if document.get("schema_version") != 1:
        raise DataValidationError("unsupported or missing experiment schema_version")
    role = document.get("role")
    if role not in VALID_DATASET_ROLES:
        raise DataValidationError(
            f"metadata role must be one of {sorted(VALID_DATASET_ROLES)}"
        )
    specs = _metadata_columns(document)

    try:
        handle = csv_file.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise DataValidationError(f"cannot read experiment CSV: {exc}") from exc
    with handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise DataValidationError("experiment CSV has no header")
        expected = [spec.source for spec in specs]
        if reader.fieldnames != expected:
            raise DataValidationError(
                f"CSV columns {reader.fieldnames!r} do not exactly match metadata {expected!r}"
            )
        raw: dict[str, list[float]] = {name: [] for name in expected}
        for row_number, row in enumerate(reader, start=2):
            if None in row:
                raise DataValidationError(f"row {row_number} has extra fields")
            for name in expected:
                cell = row.get(name)
                if cell is None or not cell.strip():
                    raise DataValidationError(
                        f"row {row_number}, column {name!r} is missing"
                    )
                try:
                    value = float(cell)
                except ValueError as exc:
                    raise DataValidationError(
                        f"row {row_number}, column {name!r} is not numeric"
                    ) from exc
                if not np.isfinite(value):
                    raise DataValidationError(
                        f"row {row_number}, column {name!r} is non-finite"
                    )
                raw[name].append(value)
    if not raw or not next(iter(raw.values())):
        raise DataValidationError("experiment CSV contains no data rows")

    time_spec = next(spec for spec in specs if spec.role == "time")
    time_s = np.asarray(raw[time_spec.source], dtype=float) * time_spec.to_si_factor
    observables = {
        spec.canonical: np.asarray(raw[spec.source], dtype=float) * spec.to_si_factor
        for spec in specs
        if spec.role == "observable"
    }
    user_metadata = {
        key: value
        for key, value in document.items()
        if key not in {"schema_version", "role", "columns"}
    }
    return ExperimentDataset(
        time_s=time_s,
        observables=observables,
        role=role,
        columns=specs,
        metadata=user_metadata,
        source_csv=str(csv_file.resolve()),
        source_metadata=str(metadata_file.resolve()),
    )


def save_experiment(
    dataset: ExperimentDataset,
    csv_path: str | Path,
    metadata_path: str | Path | None = None,
) -> tuple[Path, Path]:
    """Write a validated dataset using its original unit-column mapping."""

    csv_file = Path(csv_path)
    metadata_file = (
        csv_file.with_suffix(".meta.json") if metadata_path is None else Path(metadata_path)
    )
    csv_file.parent.mkdir(parents=True, exist_ok=True)
    metadata_file.parent.mkdir(parents=True, exist_ok=True)

    document: dict[str, Any] = dict(dataset.metadata)
    document.update({
        "schema_version": 1,
        "role": dataset.role,
        "columns": {
            spec.source: {
                "canonical": spec.canonical,
                "unit": spec.unit,
                "role": spec.role,
            }
            for spec in dataset.columns
        },
    })
    metadata_file.write_text(
        _dumps_metadata_json_strict(document) + "\n",
        encoding="utf-8",
    )

    with csv_file.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow([spec.source for spec in dataset.columns])
        for row_index in range(len(dataset.time_s)):
            row: list[str] = []
            for spec in dataset.columns:
                if spec.role == "time":
                    si_value = float(dataset.time_s[row_index])
                else:
                    si_value = float(dataset.observables[spec.canonical][row_index])
                row.append(format(si_value / spec.to_si_factor, ".17g"))
            writer.writerow(row)
    return csv_file, metadata_file


# Clear import/export aliases for CLI callers.
import_experiment = load_experiment
export_experiment = save_experiment
