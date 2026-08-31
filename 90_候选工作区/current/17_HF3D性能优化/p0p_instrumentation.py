"""Result-neutral P0-P semantic instrumentation and profile parsers.

The module is intentionally free of NumPy, SciPy, CuPy, and formal-airvortex
imports.  A worker may therefore validate authorization and apply the formal
early thread policy before constructing any numerical observer.
"""

from __future__ import annotations

import hashlib
import inspect
import itertools
import pstats
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Generic, ParamSpec, Protocol, TypeVar, cast

from p0_performance_contract import P0PerformanceError, relative, sha256_file

P = ParamSpec("P")
T = TypeVar("T")
_RANGE_RECORD_FIELDS = frozenset(
    {
        "call_id",
        "name",
        "parent_call_id",
        "thread_id",
        "start_ns",
        "end_ns",
        "inclusive_ns",
        "child_ns",
        "exclusive_ns",
        "attributed",
        "status",
        "binding",
    }
)


class NvtxBackend(Protocol):
    """Small interface implemented by the already-imported CuPy NVTX API."""

    def push(self, name: str) -> None: ...

    def pop(self) -> None: ...


@dataclass(frozen=True, slots=True)
class RangeRecord:
    call_id: int
    name: str
    parent_call_id: int | None
    thread_id: int
    start_ns: int
    end_ns: int
    inclusive_ns: int
    child_ns: int
    exclusive_ns: int
    attributed: bool
    status: str
    binding: Mapping[str, Any] | None


@dataclass(slots=True)
class _OpenRange:
    call_id: int
    name: str
    parent_call_id: int | None
    thread_id: int
    start_ns: int
    child_ns: int
    attributed: bool
    binding: Mapping[str, Any] | None


class IntervalRegistry:
    """Thread-safe nested interval registry with exclusive-time accounting."""

    def __init__(
        self,
        *,
        clock_ns: Callable[[], int] = time.perf_counter_ns,
        nvtx: NvtxBackend | None = None,
    ) -> None:
        self._clock_ns = clock_ns
        self._nvtx = nvtx
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._local = threading.local()
        self._records: list[RangeRecord] = []

    def _stack(self) -> list[_OpenRange]:
        stack = getattr(self._local, "stack", None)
        if stack is None:
            stack = []
            self._local.stack = stack
        return cast(list[_OpenRange], stack)

    @contextmanager
    def range(
        self,
        name: str,
        *,
        attributed: bool = True,
        binding: Mapping[str, Any] | None = None,
    ) -> Iterator[int]:
        if not name or "\n" in name:
            raise ValueError("semantic range name must be a non-empty single line")
        stack = self._stack()
        with self._lock:
            call_id = next(self._ids)
        parent = stack[-1] if stack else None
        opened = _OpenRange(
            call_id=call_id,
            name=name,
            parent_call_id=parent.call_id if parent else None,
            thread_id=threading.get_native_id(),
            start_ns=self._clock_ns(),
            child_ns=0,
            attributed=attributed,
            binding=dict(binding) if binding is not None else None,
        )
        stack.append(opened)
        if self._nvtx is not None:
            self._nvtx.push(f"{name}|call_id={call_id}")
        status = "returned"
        try:
            yield call_id
        except BaseException:
            status = "raised"
            raise
        finally:
            end_ns = self._clock_ns()
            if self._nvtx is not None:
                self._nvtx.pop()
            popped = stack.pop()
            if popped is not opened:
                raise RuntimeError("semantic interval stack corruption")
            inclusive_ns = end_ns - opened.start_ns
            if inclusive_ns < 0 or opened.child_ns > inclusive_ns:
                raise RuntimeError("semantic interval clock accounting is invalid")
            exclusive_ns = inclusive_ns - opened.child_ns
            if stack:
                stack[-1].child_ns += inclusive_ns
            record = RangeRecord(
                call_id=opened.call_id,
                name=opened.name,
                parent_call_id=opened.parent_call_id,
                thread_id=opened.thread_id,
                start_ns=opened.start_ns,
                end_ns=end_ns,
                inclusive_ns=inclusive_ns,
                child_ns=opened.child_ns,
                exclusive_ns=exclusive_ns,
                attributed=opened.attributed,
                status=status,
                binding=opened.binding,
            )
            with self._lock:
                self._records.append(record)

    def records(self) -> tuple[RangeRecord, ...]:
        with self._lock:
            return tuple(sorted(self._records, key=lambda item: item.call_id))

    def to_manifest(self) -> list[dict[str, Any]]:
        return [asdict(record) for record in self.records()]


def _interval_union_ns(records: Sequence[RangeRecord]) -> int:
    spans = sorted((record.start_ns, record.end_ns) for record in records)
    if not spans:
        return 0
    total = 0
    start, end = spans[0]
    for next_start, next_end in spans[1:]:
        if next_start <= end:
            end = max(end, next_end)
        else:
            total += end - start
            start, end = next_start, next_end
    return total + end - start


def coverage_report(
    records: Sequence[RangeRecord],
    *,
    child_wall_ns: int,
    solver_range_name: str,
) -> dict[str, Any]:
    """Calculate the frozen 98% top-level and 90% solver-exclusive gates."""

    if child_wall_ns <= 0:
        raise ValueError("child wall time must be positive")
    top_level = [record for record in records if record.parent_call_id is None]
    top_union = _interval_union_ns(top_level)
    solver_records = [record for record in records if record.name == solver_range_name]
    if len(solver_records) != 1:
        raise P0PerformanceError("exactly one solver phase range is required")
    solver = solver_records[0]
    by_id = {record.call_id: record for record in records}

    def inside_solver(record: RangeRecord) -> bool:
        parent_id = record.parent_call_id
        while parent_id is not None:
            if parent_id == solver.call_id:
                return True
            parent = by_id.get(parent_id)
            parent_id = parent.parent_call_id if parent is not None else None
        return False

    solver_children = [record for record in records if inside_solver(record)]
    named_exclusive_ns = sum(
        record.exclusive_ns for record in solver_children if record.attributed
    )
    unknown = [record for record in solver_children if not record.attributed]
    # The solver range's own exclusive time is control flow not covered by a
    # named child boundary and therefore remains explicitly unattributed.
    unknown_exclusive_ns = solver.exclusive_ns + sum(
        record.exclusive_ns for record in unknown
    )
    maximum_unknown_ns = max(
        [solver.exclusive_ns, *(record.exclusive_ns for record in unknown)]
    )
    solver_wall_ns = solver.inclusive_ns
    top_fraction = top_union / child_wall_ns
    named_fraction = named_exclusive_ns / solver_wall_ns if solver_wall_ns else 0.0
    unknown_fraction = unknown_exclusive_ns / solver_wall_ns if solver_wall_ns else 1.0
    maximum_unknown_fraction = (
        maximum_unknown_ns / solver_wall_ns if solver_wall_ns else 1.0
    )
    gates = {
        "top_level_phase_union_at_least_98_percent": top_fraction >= 0.98,
        "named_solver_exclusive_at_least_90_percent": named_fraction >= 0.90,
        "unknown_solver_total_at_most_10_percent": unknown_fraction <= 0.10,
        "no_single_unknown_region_at_least_5_percent": (
            maximum_unknown_fraction < 0.05
        ),
    }
    return {
        "gate_pass": all(gates.values()),
        "gates": gates,
        "child_wall_ns": child_wall_ns,
        "top_level_union_ns": top_union,
        "top_level_fraction": top_fraction,
        "solver_wall_ns": solver_wall_ns,
        "named_solver_exclusive_ns": named_exclusive_ns,
        "named_solver_exclusive_fraction": named_fraction,
        "unknown_solver_exclusive_ns": unknown_exclusive_ns,
        "unknown_solver_fraction": unknown_fraction,
        "maximum_single_unknown_fraction": maximum_unknown_fraction,
    }


def range_records_from_manifest(value: object) -> tuple[RangeRecord, ...]:
    """Strictly reconstruct and validate one serialized interval tree."""

    if not isinstance(value, list) or not value:
        raise P0PerformanceError("semantic range manifest must be a non-empty list")
    records: list[RangeRecord] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping) or set(item) != set(_RANGE_RECORD_FIELDS):
            raise P0PerformanceError(
                f"semantic range record {index} has a non-exact schema"
            )
        integer_fields = (
            "call_id",
            "thread_id",
            "start_ns",
            "end_ns",
            "inclusive_ns",
            "child_ns",
            "exclusive_ns",
        )
        if not all(type(item.get(field)) is int for field in integer_fields):
            raise P0PerformanceError(
                f"semantic range record {index} has a non-integer timing identity"
            )
        parent = item.get("parent_call_id")
        binding = item.get("binding")
        if not (
            (parent is None or type(parent) is int)
            and isinstance(item.get("name"), str)
            and bool(item["name"])
            and "\n" not in str(item["name"])
            and type(item.get("attributed")) is bool
            and item.get("status") in {"returned", "raised"}
            and (binding is None or isinstance(binding, Mapping))
        ):
            raise P0PerformanceError(
                f"semantic range record {index} has an invalid typed payload"
            )
        record = RangeRecord(
            call_id=int(item["call_id"]),
            name=str(item["name"]),
            parent_call_id=int(parent) if parent is not None else None,
            thread_id=int(item["thread_id"]),
            start_ns=int(item["start_ns"]),
            end_ns=int(item["end_ns"]),
            inclusive_ns=int(item["inclusive_ns"]),
            child_ns=int(item["child_ns"]),
            exclusive_ns=int(item["exclusive_ns"]),
            attributed=bool(item["attributed"]),
            status=str(item["status"]),
            binding=dict(binding) if isinstance(binding, Mapping) else None,
        )
        if not (
            record.call_id > 0
            and record.thread_id > 0
            and 0 <= record.start_ns <= record.end_ns
            and record.inclusive_ns == record.end_ns - record.start_ns
            and 0 <= record.child_ns <= record.inclusive_ns
            and record.exclusive_ns == record.inclusive_ns - record.child_ns
        ):
            raise P0PerformanceError(
                f"semantic range record {index} has inconsistent interval arithmetic"
            )
        records.append(record)
    records.sort(key=lambda item: item.call_id)
    identifiers = [record.call_id for record in records]
    if identifiers != list(range(1, len(records) + 1)):
        raise P0PerformanceError("semantic range call IDs are not exact and contiguous")
    by_id = {record.call_id: record for record in records}
    for record in records:
        if record.parent_call_id is None:
            continue
        parent = by_id.get(record.parent_call_id)
        if not (
            parent is not None
            and parent.call_id < record.call_id
            and parent.thread_id == record.thread_id
            and parent.start_ns <= record.start_ns
            and record.end_ns <= parent.end_ns
        ):
            raise P0PerformanceError(
                f"semantic range {record.call_id} has an invalid parent interval"
            )
    for parent in records:
        children = sorted(
            (
                record
                for record in records
                if record.parent_call_id == parent.call_id
            ),
            key=lambda record: (record.start_ns, record.end_ns, record.call_id),
        )
        if sum(child.inclusive_ns for child in children) != parent.child_ns:
            raise P0PerformanceError(
                f"semantic range {parent.call_id} child accounting is inconsistent"
            )
        if any(
            left.end_ns > right.start_ns
            for left, right in itertools.pairwise(children)
        ):
            raise P0PerformanceError(
                f"semantic range {parent.call_id} has overlapping direct children"
            )
    return tuple(records)


def audit_host_semantic_evidence(
    manifest: object,
    *,
    child_wall_ns: int,
) -> dict[str, Any]:
    """Independently recompute the frozen host attribution hard gates."""

    records = range_records_from_manifest(manifest)
    process_ranges = [
        record
        for record in records
        if record.name == "P0P_PROCESS_START" and record.parent_call_id is None
    ]
    structural_gates = {
        "unique_top_level_process_range": len(process_ranges) == 1,
        "all_ranges_returned": all(record.status == "returned" for record in records),
        "all_nonprocess_ranges_nested": len(process_ranges) == 1
        and all(
            record is process_ranges[0] or record.parent_call_id is not None
            for record in records
        ),
    }
    coverage = coverage_report(
        records,
        child_wall_ns=child_wall_ns,
        solver_range_name="solver_phase",
    )
    gates = {**structural_gates, **coverage["gates"]}
    return {
        **coverage,
        "gate_pass": all(gates.values()),
        "gates": gates,
        "range_count": len(records),
        "process_range_call_id": process_ranges[0].call_id
        if len(process_ranges) == 1
        else None,
    }


def callable_binding(function: Callable[..., Any]) -> dict[str, Any]:
    """Bind a wrapped callable without reading any numerical argument value."""

    target = inspect.unwrap(function)
    source = inspect.getsourcefile(target)
    source_path = Path(source).resolve() if source else None
    regular = bool(
        source_path and source_path.is_file() and not source_path.is_symlink()
    )
    source_label: str | None = None
    source_inside_repository = False
    if regular and source_path is not None:
        try:
            source_label = relative(source_path)
            source_inside_repository = True
        except ValueError:
            source_label = source_path.as_posix()
    return {
        "module": getattr(target, "__module__", type(target).__module__),
        "qualname": getattr(target, "__qualname__", type(target).__qualname__),
        "source_path": source_label,
        "source_sha256": sha256_file(source_path) if regular and source_path else None,
        "source_regular_file_not_symlink": regular,
        "source_inside_repository": source_inside_repository,
    }


class TransparentCallable(Generic[P, T]):
    """Call one original callable exactly once inside a semantic interval."""

    def __init__(
        self,
        original: Callable[P, T],
        *,
        name: str,
        registry: IntervalRegistry,
        attributed: bool = True,
    ) -> None:
        self.original = original
        self.name = name
        self.registry = registry
        self.attributed = attributed
        self.binding = callable_binding(original)
        self.call_count = 0

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> T:
        self.call_count += 1
        with self.registry.range(
            self.name,
            attributed=self.attributed,
            binding=self.binding,
        ):
            return self.original(*args, **kwargs)


def array_metadata(value: object) -> dict[str, Any]:
    """Read structural metadata only; never index or copy a numerical array."""

    shape = getattr(value, "shape", None)
    dtype = getattr(value, "dtype", None)
    nbytes = getattr(value, "nbytes", None)
    if shape is None or dtype is None or nbytes is None:
        raise TypeError("array-like shape, dtype, and nbytes are required")
    return {
        "object_id": id(value),
        "shape": [int(item) for item in shape],
        "dtype": str(dtype),
        "nbytes": int(nbytes),
    }


class PressureProjectProxy:
    """Transparent pressure proxy with structural transfer expectations."""

    def __init__(self, original: object, registry: IntervalRegistry) -> None:
        self._original = original
        self._project = cast(Callable[..., Any], cast(Any, original).project)
        self._registry = registry
        self.records: list[dict[str, Any]] = []
        self.binding = callable_binding(self._project)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)

    def project(self, *args: Any, **kwargs: Any) -> Any:
        inputs = [array_metadata(value) for value in args[:4]]
        initial = kwargs.get("initial_pressure_pa")
        if initial is not None:
            inputs.append(array_metadata(initial))
        with self._registry.range(
            "pressure.project",
            binding=self.binding,
        ) as call_id:
            result = self._project(*args, **kwargs)
        if not isinstance(result, tuple) or len(result) < 4:
            raise P0PerformanceError("pressure.project returned an invalid structure")
        outputs = [array_metadata(value) for value in result[:4]]
        self.records.append(
            {
                "call_id": call_id,
                "input_arrays": inputs,
                "output_arrays": outputs,
                "expected_h2d_size_multiset": sorted(item["nbytes"] for item in inputs),
                "expected_d2h_size_multiset": sorted(
                    item["nbytes"] for item in outputs
                ),
            }
        )
        return result


class DeviceAllocationRecorder:
    """CuPy hook state plus pool/device high-water observations."""

    def __init__(self, cupy_module: Any) -> None:
        self.cp = cupy_module
        self.pool_request_count = 0
        self.pool_requested_bytes = 0
        self.device_allocation_count = 0
        self.device_allocated_bytes = 0
        self.pool_free_count = 0
        self.pool_used_high_water = 0
        self.pool_total_high_water = 0
        self.device_used_high_water = 0
        self.boundaries: list[dict[str, Any]] = []
        recorder = self

        class Hook(cupy_module.cuda.MemoryHook):
            def malloc_postprocess(self, **kwargs: object) -> None:
                recorder.pool_request_count += 1
                recorder.pool_requested_bytes += int(cast(Any, kwargs.get("size", 0)))
                recorder.observe("malloc_postprocess")

            def alloc_postprocess(self, **kwargs: object) -> None:
                recorder.device_allocation_count += 1
                recorder.device_allocated_bytes += int(
                    cast(Any, kwargs.get("mem_size", 0))
                )
                recorder.observe("alloc_postprocess")

            def free_postprocess(self, **kwargs: object) -> None:
                recorder.pool_free_count += 1
                recorder.observe("free_postprocess")

        self.hook = Hook()

    def observe(self, label: str) -> None:
        pool = self.cp.get_default_memory_pool()
        free_bytes, total_bytes = self.cp.cuda.runtime.memGetInfo()
        used_bytes = int(total_bytes) - int(free_bytes)
        pool_used = int(pool.used_bytes())
        pool_total = int(pool.total_bytes())
        self.pool_used_high_water = max(self.pool_used_high_water, pool_used)
        self.pool_total_high_water = max(self.pool_total_high_water, pool_total)
        self.device_used_high_water = max(self.device_used_high_water, used_bytes)
        self.boundaries.append(
            {
                "label": label,
                "pool_used_bytes": pool_used,
                "pool_total_bytes": pool_total,
                "device_free_bytes": int(free_bytes),
                "device_total_bytes": int(total_bytes),
                "device_used_bytes": used_bytes,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "pool_request_count": self.pool_request_count,
            "pool_requested_bytes": self.pool_requested_bytes,
            "underlying_device_allocation_count": self.device_allocation_count,
            "underlying_device_allocated_bytes": self.device_allocated_bytes,
            "pool_free_count": self.pool_free_count,
            "pool_used_high_water_bytes": self.pool_used_high_water,
            "pool_total_high_water_bytes": self.pool_total_high_water,
            "device_used_high_water_bytes": self.device_used_high_water,
            "boundaries": list(self.boundaries),
        }


def parse_pstats(
    path: Path, *, limit: int = 200, logical_path: Path | None = None
) -> dict[str, Any]:
    """Convert a cProfile pstats file into a stable, bounded call table."""

    if not path.is_file() or path.is_symlink():
        raise P0PerformanceError("pstats input must be a regular file")
    stats = pstats.Stats(str(path))
    stats_any = cast(Any, stats)
    rows: list[dict[str, Any]] = []
    raw_stats = cast(dict[Any, Any], stats_any.stats)
    for function, raw in raw_stats.items():
        primitive_calls, total_calls, total_time, cumulative_time, callers = raw
        filename, line, name = function
        rows.append(
            {
                "filename": filename,
                "line": int(line),
                "function": name,
                "primitive_calls": int(primitive_calls),
                "total_calls": int(total_calls),
                "total_time_s": float(total_time),
                "cumulative_time_s": float(cumulative_time),
                "caller_count": len(callers),
            }
        )
    rows.sort(
        key=lambda row: (
            -float(row["cumulative_time_s"]),
            -float(row["total_time_s"]),
            str(row["filename"]),
            int(row["line"]),
        )
    )
    return {
        "source_path": str(logical_path if logical_path is not None else path),
        "source_sha256": sha256_file(path),
        "total_calls": int(stats_any.total_calls),
        "primitive_calls": int(stats_any.prim_calls),
        "total_profiled_time_s": float(stats_any.total_tt),
        "rows": rows[:limit],
        "row_count_total": len(rows),
        "claim_ceiling": (
            "Python and extension-call-boundary call graph only; not benchmark, "
            "native-symbol, CUDA, cache, SIMD, or memory-bandwidth evidence"
        ),
    }


def numerical_metadata_hash(records: Sequence[Mapping[str, Any]]) -> str:
    """Hash structural transfer expectations for an independent cross-check."""

    encoded = repr(
        [
            (
                int(record["call_id"]),
                tuple(record["expected_h2d_size_multiset"]),
                tuple(record["expected_d2h_size_multiset"]),
            )
            for record in records
        ]
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "DeviceAllocationRecorder",
    "IntervalRegistry",
    "NvtxBackend",
    "PressureProjectProxy",
    "RangeRecord",
    "TransparentCallable",
    "array_metadata",
    "audit_host_semantic_evidence",
    "callable_binding",
    "coverage_report",
    "numerical_metadata_hash",
    "parse_pstats",
    "range_records_from_manifest",
]
