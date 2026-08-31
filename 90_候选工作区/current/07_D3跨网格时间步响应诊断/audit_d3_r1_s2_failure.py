#!/usr/bin/env python3
"""Freeze the local D3R1 S2 fail-closed evidence without reading observables.

This audit is independent of the D3 runner and analyser.  It hashes every
regular file in the ignored R1 raw tree, validates the committed stage/failure
semantics, verifies all five completed case archives, and opens the failed
``partial.npz`` only to enumerate members and read ``frame_time_s``.  It never
loads or reports physical observable or state arrays.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import struct
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parents[2]
RAW_ROOT = HERE / "artifacts" / "r1" / "runs"
OUTPUT_ROOT = HERE / "artifacts" / "r1_failure"
OUTPUT_JSON = OUTPUT_ROOT / "d3_r1_s2_failure_forensic.json"
OUTPUT_MD = OUTPUT_ROOT / "d3_r1_s2_failure_forensic.md"
PREREGISTRATION_PATH = HERE / "d3_cross_grid_temporal_preregistration_r1.json"

PROTOCOL_ID = "D3_HF3D_CROSS_GRID_TEMPORAL_RESPONSE_R1"
EXECUTION_COMMIT = "be349aec2f1bdcdb232e282a11175921eda7976b"
PRO_RULING_COMMIT = "d1aff0b6f46783096ecfcc8c820b68145b78aa55"
FORMAL_SOURCE_TREE_SHA256 = (
    "dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8"
)

EXPECTED_RAW_FILE_COUNT = 40
EXPECTED_RAW_TOTAL_SIZE_BYTES = 359_454_291
EXPECTED_RAW_SNAPSHOT_SHA256 = (
    "906a32f71f3e095afc7cba6a3eddf7a83e4d639d36dc0b3014278b3e0001b032"
)
EXPECTED_PARTIAL_SHA256 = (
    "8f1e0973eda48c2ecc54f10001f28b988d353e85a728a16ec24f74582527cbcc"
)
EXPECTED_PARTIAL_SIZE_BYTES = 60_848_588

PASS_CASES = ("G6_DT0", "G6_DT1", "G6_DT2", "G9_DT0", "G9_DT1")
FAILED_CASE = "G9_DT2"
EXPECTED_STAGE_STATE = {
    "S0": ("EXECUTED", "PASS"),
    "S1": ("EXECUTED", "PASS"),
    "S2": ("FAILED_STOPPED", "D3_INCONCLUSIVE_FAIL_CLOSED"),
    "S3": ("NOT_EXECUTED", "NOT_EXECUTED"),
}

ABSENT_S3_PATHS = (
    HERE / "reports" / "r1" / "d3_cross_grid_temporal_report.json",
    HERE / "reports" / "r1" / "d3_cross_grid_temporal_report.md",
    HERE / "artifacts" / "r1" / "d3_cross_grid_temporal_manifest.json",
    HERE / "artifacts" / "r1" / "evidence",
    RAW_ROOT / ".s3_publication_transaction",
    RAW_ROOT / ".stage_ledger.s3-commit.tmp",
)


class ForensicAuditError(RuntimeError):
    """Raised before bundle publication when frozen evidence does not match."""


def _relative(path: Path) -> str:
    return path.resolve().relative_to(REPOSITORY_ROOT.resolve()).as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is prohibited: {value}")


def _object_no_duplicates(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _json_object(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ForensicAuditError(f"not a regular file: {_relative(path)}")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_object_no_duplicates,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ForensicAuditError(
            f"strict JSON reload failed: {_relative(path)}"
        ) from exc
    if not isinstance(value, dict):
        raise ForensicAuditError(f"top-level JSON object required: {_relative(path)}")
    return value


def _uint64(value: float | np.float64) -> int:
    return struct.unpack(">Q", struct.pack(">d", float(value)))[0]


def _require(gates: dict[str, bool], name: str, condition: object) -> None:
    passed = bool(condition)
    gates[name] = passed
    if not passed:
        raise ForensicAuditError(f"forensic gate failed: {name}")


def _stage_map(ledger: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    stages = ledger.get("stages")
    if not isinstance(stages, list):
        raise ForensicAuditError("stage ledger does not contain a stage list")
    result: dict[str, Mapping[str, Any]] = {}
    for item in stages:
        if not isinstance(item, Mapping) or not isinstance(item.get("stage"), str):
            raise ForensicAuditError("malformed stage ledger entry")
        stage = str(item["stage"])
        if stage in result:
            raise ForensicAuditError(f"duplicate stage ledger entry: {stage}")
        result[stage] = item
    return result


def _raw_snapshot() -> dict[str, Any]:
    if not RAW_ROOT.is_dir() or RAW_ROOT.is_symlink():
        raise ForensicAuditError("R1 raw root is not a regular directory")
    symlinks = sorted(
        path.relative_to(RAW_ROOT).as_posix()
        for path in RAW_ROOT.rglob("*")
        if path.is_symlink()
    )
    if symlinks:
        raise ForensicAuditError(f"R1 raw contains symlinks: {symlinks}")
    rows: list[dict[str, Any]] = []
    for path in sorted(item for item in RAW_ROOT.rglob("*") if item.is_file()):
        rows.append(
            {
                "path": path.relative_to(RAW_ROOT).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    canonical = json.dumps(
        rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return {
        "file_count": len(rows),
        "total_size_bytes": sum(int(item["size_bytes"]) for item in rows),
        "canonical_snapshot_sha256": hashlib.sha256(canonical).hexdigest(),
        "files": rows,
    }


def _archive_audit(case_id: str, summary: Mapping[str, Any]) -> dict[str, Any]:
    archive = summary.get("archive")
    if not isinstance(archive, Mapping):
        raise ForensicAuditError(f"{case_id} does not contain archive metadata")
    payloads = archive.get("payloads")
    if not isinstance(payloads, Mapping) or not payloads:
        raise ForensicAuditError(f"{case_id} archive payload map is missing")
    root = RAW_ROOT / "cases" / case_id / "archive"
    observed: list[dict[str, Any]] = []
    for name, metadata in sorted(payloads.items()):
        if not isinstance(name, str) or not isinstance(metadata, Mapping):
            raise ForensicAuditError(f"{case_id} archive payload metadata is malformed")
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise ForensicAuditError(
                f"{case_id} archive payload is not regular: {name}"
            )
        size = path.stat().st_size
        digest = _sha256(path)
        if size != metadata.get("size_bytes") or digest != metadata.get("sha256"):
            raise ForensicAuditError(
                f"{case_id} archive payload identity failed: {name}"
            )
        observed.append({"name": name, "size_bytes": size, "sha256": digest})
    if archive.get("result_status") != "completed":
        raise ForensicAuditError(f"{case_id} archive result is not completed")
    return {
        "path": archive.get("path"),
        "result_status": archive.get("result_status"),
        "frame_time_sha256": archive.get("frame_time_sha256"),
        "payloads": observed,
    }


def _completed_case_audit(case_id: str, gates: dict[str, bool]) -> dict[str, Any]:
    path = RAW_ROOT / "cases" / case_id / "case_summary.json"
    summary = _json_object(path)
    prefix = f"completed_case_{case_id}"
    _require(gates, f"{prefix}_identity", summary.get("case_id") == case_id)
    _require(
        gates,
        f"{prefix}_pass",
        summary.get("status") == "D3_CASE_COMPLETE_ALL_HARD_GATES_PASS"
        and summary.get("gate_pass") is True
        and all(bool(value) for value in summary.get("gates", {}).values())
        and summary.get("quality", {}).get("gate_pass") is True
        and all(
            bool(value)
            for value in summary.get("quality", {}).get("gates", {}).values()
        ),
    )
    _require(
        gates,
        f"{prefix}_execution_commit",
        summary.get("execution_commit") == EXECUTION_COMMIT
        and summary.get("git_revision_execution_start", {}).get("value")
        == EXECUTION_COMMIT
        and summary.get("git_revision_execution_end", {}).get("value")
        == EXECUTION_COMMIT,
    )
    _require(
        gates,
        f"{prefix}_formal_source",
        summary.get("source_tree_execution_start", {}).get("value")
        == FORMAL_SOURCE_TREE_SHA256
        and summary.get("source_tree_execution_end", {}).get("value")
        == FORMAL_SOURCE_TREE_SHA256,
    )
    _require(
        gates,
        f"{prefix}_cuda",
        summary.get("pressure_accelerator", {}).get("selected") == "cuda"
        and summary.get("pressure_accelerator", {}).get("fallback") is False,
    )
    archive = _archive_audit(case_id, summary)
    return {
        "case_id": case_id,
        "stage": summary.get("stage"),
        "status": summary.get("status"),
        "summary_path": _relative(path),
        "summary_sha256": _sha256(path),
        "wall_time_s": summary.get("wall_time_s"),
        "process_cpu_time_s": summary.get("process_cpu_time_s"),
        "retry_attempts": summary.get("retry_attempts"),
        "archive": archive,
    }


def _partial_audit(
    preregistration: Mapping[str, Any], gates: dict[str, bool]
) -> dict[str, Any]:
    path = RAW_ROOT / "cases" / FAILED_CASE / "partial.npz"
    _require(
        gates,
        "partial_identity",
        path.is_file()
        and not path.is_symlink()
        and path.stat().st_size == EXPECTED_PARTIAL_SIZE_BYTES
        and _sha256(path) == EXPECTED_PARTIAL_SHA256,
    )
    with np.load(path, allow_pickle=False) as archive:
        members = sorted(archive.files)
        if "frame_time_s" not in members:
            raise ForensicAuditError("failed partial is missing frame_time_s")
        frame_time = np.asarray(archive["frame_time_s"], dtype=np.float64).copy()
    registered = np.asarray(
        preregistration.get("native_output_times_s"), dtype=np.float64
    )
    expected = registered[: frame_time.size]
    _require(
        gates,
        "partial_frame_times_are_registered_prefix",
        frame_time.dtype == np.float64
        and frame_time.shape == (10,)
        and np.array_equal(frame_time, expected),
    )
    rows = []
    for index, (observed, frozen) in enumerate(zip(frame_time, expected, strict=True)):
        observed_bits = _uint64(observed)
        frozen_bits = _uint64(frozen)
        rows.append(
            {
                "index": index,
                "observed_decimal": repr(float(observed)),
                "registered_decimal": repr(float(frozen)),
                "observed_float_hex": float(observed).hex(),
                "registered_float_hex": float(frozen).hex(),
                "observed_uint64": observed_bits,
                "registered_uint64": frozen_bits,
                "bitwise_equal": observed_bits == frozen_bits,
            }
        )
    return {
        "path": _relative(path),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "members": members,
        "members_inspected_only_by_name": [
            name for name in members if name != "frame_time_s"
        ],
        "only_array_value_loaded": "frame_time_s",
        "frame_time_identity": rows,
    }


def _failed_case_audit(
    preregistration: Mapping[str, Any], gates: dict[str, bool]
) -> dict[str, Any]:
    path = RAW_ROOT / "cases" / FAILED_CASE / "case_summary.json"
    summary = _json_object(path)
    failure = summary.get("failure")
    quality = summary.get("quality")
    if not isinstance(failure, Mapping) or not isinstance(quality, Mapping):
        raise ForensicAuditError("G9_DT2 failure summary is malformed")
    quality_gates = quality.get("gates")
    metrics = quality.get("metrics")
    if not isinstance(quality_gates, Mapping) or not isinstance(metrics, Mapping):
        raise ForensicAuditError("G9_DT2 quality evidence is malformed")
    expected_false = {
        "complete_to_registered_end_time",
        "native_output_times_exact",
        "projected_divergence",
    }
    observed_false = {name for name, value in quality_gates.items() if not bool(value)}
    threshold = float(
        preregistration["numerical_quality_hard_gates"][
            "projected_divergence_max_s_inv"
        ]
    )
    maximum = float(metrics["maximum_projected_divergence_s_inv"])
    _require(
        gates,
        "failed_case_status",
        summary.get("case_id") == FAILED_CASE
        and summary.get("stage") == "S2"
        and summary.get("status") == "D3_INCONCLUSIVE_FAIL_CLOSED"
        and summary.get("gate_pass") is False
        and summary.get("gates", {}).get("run_completed") is False
        and summary.get("gates", {}).get("all_numerical_quality_gates") is False,
    )
    _require(
        gates,
        "failed_case_identity_stable",
        summary.get("execution_commit") == EXECUTION_COMMIT
        and summary.get("git_revision_execution_start", {}).get("value")
        == EXECUTION_COMMIT
        and summary.get("git_revision_execution_end", {}).get("value")
        == EXECUTION_COMMIT
        and summary.get("source_tree_execution_start", {}).get("value")
        == FORMAL_SOURCE_TREE_SHA256
        and summary.get("source_tree_execution_end", {}).get("value")
        == FORMAL_SOURCE_TREE_SHA256,
    )
    _require(
        gates,
        "failed_case_cuda_without_fallback",
        summary.get("pressure_accelerator", {}).get("selected") == "cuda"
        and summary.get("pressure_accelerator", {}).get("fallback") is False,
    )
    _require(
        gates,
        "failed_case_exception_exact",
        failure.get("type") == "FloatingPointError"
        and failure.get("message") == "G9_DT2 hard gate failed: projected_divergence"
        and failure.get("accepted_steps") == 6695
        and math.isclose(
            float(failure["simulated_time_s"]),
            0.009196624999999948,
            rel_tol=0.0,
            abs_tol=0.0,
        ),
    )
    _require(gates, "failed_quality_gate_set_exact", observed_false == expected_false)
    _require(
        gates,
        "projected_divergence_threshold_exceeded",
        math.isfinite(maximum)
        and math.isfinite(threshold)
        and threshold == 1.0e-4
        and maximum == 0.00010249185899624536
        and maximum > threshold,
    )
    _require(
        gates,
        "failed_case_partial_metadata",
        summary.get("partial_evidence", {}).get("sha256") == EXPECTED_PARTIAL_SHA256
        and summary.get("partial_evidence", {}).get("size_bytes")
        == EXPECTED_PARTIAL_SIZE_BYTES,
    )
    return {
        "case_id": FAILED_CASE,
        "status": summary.get("status"),
        "summary_path": _relative(path),
        "summary_sha256": _sha256(path),
        "failure": dict(failure),
        "retry_attempts": summary.get("retry_attempts"),
        "wall_time_s": summary.get("wall_time_s"),
        "process_cpu_time_s": summary.get("process_cpu_time_s"),
        "quality_false_gates": sorted(observed_false),
        "maximum_projected_divergence_s_inv": maximum,
        "projected_divergence_gate_s_inv": threshold,
        "gate_ratio": maximum / threshold,
        "gate_excess_s_inv": maximum - threshold,
        "archive_created": False,
    }


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# D3R1 S2 失败取证包",
        "",
        "状态：`D3R1_S2_G9_DT2_PROJECTED_DIVERGENCE_FAILURE_FORENSIC_GATE_PASS`",
        "",
        "本取证包冻结 R1 自然失败的身份、硬门和证据边界。它只从失败 partial 读取 `frame_time_s`，不读取或报告状态数组或主要物理观测量，不能支持 D3 科学分类。",
        "",
        "## 绑定",
        "",
        f"- 协议：`{report['protocol_id']}`",
        f"- 执行提交：`{report['execution_commit']}`",
        f"- 性能与失败处置裁决提交：`{report['pro_ruling_commit']}`",
        f"- 正式源码树：`{report['formal_source_tree_sha256']}`",
        f"- 审计脚本 SHA-256：`{report['audit_script_sha256']}`",
        "",
        "## 原始证据快照",
        "",
        f"- 文件数：`{report['raw_snapshot']['file_count']}`",
        f"- 总字节数：`{report['raw_snapshot']['total_size_bytes']}`",
        f"- 规范快照 SHA-256：`{report['raw_snapshot']['canonical_snapshot_sha256']}`",
        "",
        "40 个文件的逐文件路径、字节数和 SHA-256 完整冻结在同目录 JSON 的 `raw_snapshot.files`；本 Markdown 只重复列出直接界定失败状态的关键文件，避免窄页渲染截断。",
        "",
        "### 关键原始文件",
        "",
    ]
    key_paths = {
        "cases/G9_DT2/case_summary.json",
        "cases/G9_DT2/partial.npz",
        "execution_failure.json",
        "stage_ledger.json",
    }
    for item in report["raw_snapshot"]["files"]:
        if item["path"] not in key_paths:
            continue
        lines.extend(
            [
                f"- 路径：`{item['path']}`",
                f"    - 字节数：`{item['size_bytes']}`",
                f"    - SHA-256：`{item['sha256']}`",
            ]
        )
    lines.extend(
        [
            "",
            "## 阶段状态",
            "",
            "| 阶段 | disposition | status |",
            "|---|---|---|",
        ]
    )
    for stage in ("S0", "S1", "S2", "S3"):
        item = report["stage_state"][stage]
        lines.append(f"| {stage} | `{item['disposition']}` | `{item['status']}` |")
    lines.extend(
        [
            "",
            "S2 为自然硬门失败，S3 未执行，`subsequent_stages_executed = false`。",
            "",
            "## 已完成案例",
            "",
            "下列五个案例的状态均为 `D3_CASE_COMPLETE_ALL_HARD_GATES_PASS`，归档载荷身份均已复核。",
            "",
            "| 案例 | 阶段 | 重试 | 墙钟 s | CPU s |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for item in report["completed_cases"]:
        lines.append(
            f"| {item['case_id']} | {item['stage']} | {item['retry_attempts']} | "
            f"{item['wall_time_s']:.6f} | "
            f"{item['process_cpu_time_s']:.6f} |"
        )
    failed = report["failed_case"]
    lines.extend(
        [
            "",
            "## G9_DT2 失败硬门",
            "",
            f"- 失败：`{failed['failure']['type']}: {failed['failure']['message']}`",
            f"- 接受步数：`{failed['failure']['accepted_steps']}`",
            f"- 失败时模拟时刻：`{failed['failure']['simulated_time_s']:.18g} s`",
            f"- 最大投影散度：`{failed['maximum_projected_divergence_s_inv']:.18g} s^-1`",
            f"- 冻结上限：`{failed['projected_divergence_gate_s_inv']:.18g} s^-1`",
            f"- 超限比：`{failed['gate_ratio']:.12g}`；绝对超量：`{failed['gate_excess_s_inv']:.18g} s^-1`",
            "- 其他导致完整性为假的门是 `complete_to_registered_end_time` 和 `native_output_times_exact`；它们是失败后未到 `0.010 s` 的结果，不是第二个独立数值根因。",
            "- CUDA 仍为真实设备且无回退；执行提交、正式源码树和授权身份在失败前后稳定。",
            "",
            "## Partial 边界",
            "",
            f"- 路径：`{report['partial']['path']}`",
            f"- 字节数：`{report['partial']['size_bytes']}`",
            f"- SHA-256：`{report['partial']['sha256']}`",
            "- 只读取了 `frame_time_s`；其值逐位等于冻结输出时刻 `0.000` 到 `0.009 s` 的前十项。",
            "- 其他 NPZ 成员只枚举名称，未读取数组值。",
            "",
            "## 结论边界",
            "",
            "```text",
            "D3R1_S2 = FAILED_STOPPED",
            "D3R1_FAILURE_CASE = G9_DT2",
            "D3R1_FAILURE_GATE = PROJECTED_DIVERGENCE",
            "D3R1_S3 = NOT_EXECUTED",
            "D3R1_SCIENTIFIC_CLASSIFICATION = NOT_FORMED",
            "NUMERICAL_QUALIFICATION = NOT_DEMONSTRATED",
            "PHYSICAL_VALIDATION = NOT_PERFORMED",
            "EXPERIMENTAL_VALIDATION = NOT_PERFORMED",
            "APPARATUS_PREDICTION = DISALLOWED",
            "```",
            "",
            "不得删除、修补、拼接、选择性保留或重跑本次 D3R1，也不得将 partial 用作后续科学运行初态。",
            "",
        ]
    )
    return "\n".join(lines)


def run_audit() -> dict[str, Any]:
    gates: dict[str, bool] = {}
    script_hash = _sha256(Path(__file__).resolve())
    preregistration = _json_object(PREREGISTRATION_PATH)
    before = _raw_snapshot()
    _require(
        gates,
        "raw_snapshot_identity",
        before["file_count"] == EXPECTED_RAW_FILE_COUNT
        and before["total_size_bytes"] == EXPECTED_RAW_TOTAL_SIZE_BYTES
        and before["canonical_snapshot_sha256"] == EXPECTED_RAW_SNAPSHOT_SHA256,
    )
    _require(
        gates,
        "preregistration_identity",
        preregistration.get("protocol_id") == PROTOCOL_ID
        and preregistration.get("formal_source_contract", {}).get(
            "required_tree_sha256"
        )
        == FORMAL_SOURCE_TREE_SHA256,
    )

    ledger_path = RAW_ROOT / "stage_ledger.json"
    failure_path = RAW_ROOT / "execution_failure.json"
    ledger = _json_object(ledger_path)
    failure_record = _json_object(failure_path)
    stages = _stage_map(ledger)
    stage_state: dict[str, dict[str, Any]] = {}
    _require(gates, "stage_set_exact", set(stages) == set(EXPECTED_STAGE_STATE))
    for stage, expected in EXPECTED_STAGE_STATE.items():
        entry = stages[stage]
        _require(
            gates,
            f"stage_{stage}_exact",
            (entry.get("disposition"), entry.get("status")) == expected,
        )
        stage_state[stage] = {
            "disposition": entry.get("disposition"),
            "status": entry.get("status"),
            "evidence": entry.get("evidence"),
        }
    _require(
        gates,
        "ledger_terminal_semantics",
        ledger.get("execution_commit") == EXECUTION_COMMIT
        and ledger.get("failed_stages") == ["S2"]
        and ledger.get("unexecuted_stages") == ["S3"]
        and ledger.get("terminal_state") == "D3_INCONCLUSIVE_FAIL_CLOSED"
        and ledger.get("failure_record") == _relative(failure_path),
    )
    _require(
        gates,
        "failure_record_semantics",
        failure_record.get("protocol_id") == PROTOCOL_ID
        and failure_record.get("execution_commit") == EXECUTION_COMMIT
        and failure_record.get("stage") == "S2"
        and failure_record.get("status") == "D3_INCONCLUSIVE_FAIL_CLOSED"
        and failure_record.get("command") == ["run-stage", "--stage", "S2"]
        and failure_record.get("failure", {}).get("type") == "D3ExecutionError"
        and failure_record.get("failure", {}).get("message")
        == "G9_DT2 completed its evidence transaction but failed hard gates"
        and failure_record.get("subsequent_stages_executed") is False,
    )

    completed = [_completed_case_audit(case_id, gates) for case_id in PASS_CASES]
    failed = _failed_case_audit(preregistration, gates)
    partial = _partial_audit(preregistration, gates)
    absent = [_relative(path) for path in ABSENT_S3_PATHS]
    _require(
        gates, "s3_outputs_absent", all(not path.exists() for path in ABSENT_S3_PATHS)
    )

    after = _raw_snapshot()
    _require(gates, "raw_hashes_unchanged_during_audit", after == before)
    report: dict[str, Any] = {
        "schema_version": 1,
        "audit_id": "D3R1_S2_G9_DT2_PROJECTED_DIVERGENCE_FAILURE_FORENSIC_V1",
        "status": "D3R1_S2_G9_DT2_PROJECTED_DIVERGENCE_FAILURE_FORENSIC_GATE_PASS",
        "gate_pass": all(gates.values()),
        "gates": gates,
        "protocol_id": PROTOCOL_ID,
        "execution_commit": EXECUTION_COMMIT,
        "pro_ruling_commit": PRO_RULING_COMMIT,
        "formal_source_tree_sha256": FORMAL_SOURCE_TREE_SHA256,
        "audit_script_sha256": script_hash,
        "scope": {
            "raw_tree_fully_hashed": True,
            "completed_case_summaries_and_archive_payloads_verified": True,
            "failed_case_summary_and_hard_gate_verified": True,
            "partial_member_names_enumerated": True,
            "partial_array_values_loaded": ["frame_time_s"],
            "state_arrays_read": False,
            "physical_observable_arrays_read": False,
            "scientific_classification_allowed": False,
        },
        "raw_snapshot": before,
        "stage_state": stage_state,
        "failure_record": {
            "path": _relative(failure_path),
            "size_bytes": failure_path.stat().st_size,
            "sha256": _sha256(failure_path),
            "subsequent_stages_executed": False,
        },
        "completed_cases": completed,
        "failed_case": failed,
        "partial": partial,
        "confirmed_absent_s3_paths": absent,
        "conclusion": {
            "D3R1_S2": "FAILED_STOPPED",
            "D3R1_FAILURE_CASE": "G9_DT2",
            "D3R1_FAILURE_GATE": "PROJECTED_DIVERGENCE",
            "D3R1_S3": "NOT_EXECUTED",
            "D3R1_SCIENTIFIC_CLASSIFICATION": "NOT_FORMED",
            "NUMERICAL_QUALIFICATION": "NOT_DEMONSTRATED",
            "PHYSICAL_VALIDATION": "NOT_PERFORMED",
            "EXPERIMENTAL_VALIDATION": "NOT_PERFORMED",
            "APPARATUS_PREDICTION": "DISALLOWED",
        },
    }
    if not report["gate_pass"]:
        raise ForensicAuditError("forensic report contains a failed gate")
    _write_atomic(
        OUTPUT_JSON,
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )
    _write_atomic(OUTPUT_MD, _markdown(report))
    final_snapshot = _raw_snapshot()
    if final_snapshot != before:
        OUTPUT_JSON.unlink(missing_ok=True)
        OUTPUT_MD.unlink(missing_ok=True)
        raise ForensicAuditError("R1 raw changed while forensic outputs were published")
    return report


def main() -> int:
    report = run_audit()
    print(
        json.dumps(
            {
                "status": report["status"],
                "gate_pass": report["gate_pass"],
                "raw_snapshot_sha256": report["raw_snapshot"][
                    "canonical_snapshot_sha256"
                ],
                "failed_case": report["failed_case"]["case_id"],
                "failed_gate": report["conclusion"]["D3R1_FAILURE_GATE"],
                "output_json": _relative(OUTPUT_JSON),
                "output_markdown": _relative(OUTPUT_MD),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
