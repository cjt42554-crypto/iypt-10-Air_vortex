"""Freeze Q1R3 only after the preregistered fine-grid probe passes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Mapping

from hfq_campaign import generate_campaign
from hfq_json import canonical_sha256, physical_signature
from hfq_quality_contract import OPEN_BOUNDARY_QUALITY_CONTRACT_ID


SPATIAL_CASE_IDS = (
    "space_L0_4cells",
    "space_L1_6cells",
    "space_L2_9cells",
)
TEMPORAL_NEW_CASE_IDS = (
    "time_L1_2.750e-06s",
    "time_L2_1.375e-06s",
)
DESCRIPTIVE_SPEC_KEYS = frozenset({"study_name", "scope"})


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"missing mapping: {label}")
    return value


def _without_descriptions(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): item
        for key, item in value.items()
        if key not in DESCRIPTIVE_SPEC_KEYS
    }


def _directory_file_hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): _sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def analyse(
    *,
    campaign_path: Path,
    frozen_manifest_path: Path,
    design_campaign_path: Path,
    design_manifest_path: Path,
    preregistration_path: Path,
    probe_report_path: Path,
    expected_source_sha256: str,
) -> dict[str, Any]:
    campaign = _read_json(campaign_path)
    frozen_manifest = _read_json(frozen_manifest_path)
    design_campaign = _read_json(design_campaign_path)
    design_manifest = _read_json(design_manifest_path)
    preregistration = _read_json(preregistration_path)
    probe = _read_json(probe_report_path)

    frozen_root = frozen_manifest_path.parent
    design_root = design_manifest_path.parent
    frozen_cases = {
        str(item.get("id")): item
        for item in frozen_manifest.get("cases", [])
        if isinstance(item, Mapping)
    }
    expected_case_ids = set(SPATIAL_CASE_IDS) | {
        "time_L0_5.500e-06s",
        *TEMPORAL_NEW_CASE_IDS,
    }
    fine_case = _mapping(
        frozen_cases.get("space_L2_9cells"), "frozen fine case"
    )
    fine_config_path = frozen_root / str(fine_case.get("config_file"))
    fine_config = _read_json(fine_config_path)

    frozen_files = _directory_file_hashes(frozen_root)
    with TemporaryDirectory(prefix="q1r3-freeze-regeneration-") as temporary:
        regeneration_root = Path(temporary)
        generate_campaign(campaign_path, regeneration_root)
        regeneration_files = _directory_file_hashes(regeneration_root)

    design_config_names = {
        str(item.get("config_file"))
        for item in design_manifest.get("cases", [])
        if isinstance(item, Mapping) and isinstance(item.get("config_file"), str)
    }
    frozen_config_names = {
        str(item.get("config_file"))
        for item in frozen_manifest.get("cases", [])
        if isinstance(item, Mapping) and isinstance(item.get("config_file"), str)
    }
    shared_config_bytes_identical = bool(
        frozen_config_names == design_config_names
        and all(
            (frozen_root / name).read_bytes()
            == (design_root / name).read_bytes()
            for name in frozen_config_names
        )
    )

    preregistration_inputs = _mapping(
        preregistration.get("inputs"), "preregistration inputs"
    )
    preregistered_probe = _mapping(
        preregistration.get("probe"), "preregistered probe"
    )
    probe_inputs = _mapping(probe.get("inputs"), "probe inputs")
    probe_execution = _mapping(probe.get("execution"), "probe execution")
    probe_decision = _mapping(
        probe.get("decision_boundary"), "probe decision boundary"
    )
    quality_contract = _mapping(
        frozen_manifest.get("quality_contract_audit"),
        "frozen quality contract audit",
    )
    refinement_audit = _mapping(
        frozen_manifest.get("refinement_design_audit"),
        "frozen refinement design audit",
    )

    gates = {
        "preregistration_passed_and_bound": bool(
            preregistration.get("status")
            == "Q1R3_PREREGISTRATION_PASS__FINE_PROBE_REQUIRED"
            and preregistration.get("gate_pass") is True
            and preregistration_inputs.get(
                "q1r3_probe_design_manifest_sha256"
            )
            == _sha256(design_manifest_path)
            and preregistered_probe.get("must_execute_before_q1r3_freeze")
            is True
        ),
        "fine_probe_passed_and_bound": bool(
            probe.get("status")
            == "Q1R3_FINE_PROBE_PASS__FREEZE_DECISION_READY"
            and probe.get("gate_pass") is True
            and probe_inputs.get("preregistration_sha256")
            == _sha256(preregistration_path)
            and probe_inputs.get("design_manifest_sha256")
            == _sha256(design_manifest_path)
            and probe_decision.get("q1r3_freeze_allowed") is True
            and probe_decision.get("q1r3_frozen") is False
        ),
        "formal_source_identity_bound": bool(
            probe_execution.get("source_tree_sha256_start")
            == expected_source_sha256
            and probe_execution.get("source_tree_sha256_end")
            == expected_source_sha256
        ),
        "frozen_spec_changes_only_descriptions": bool(
            _without_descriptions(campaign)
            == _without_descriptions(design_campaign)
        ),
        "frozen_generated_configs_match_probe_design": (
            shared_config_bytes_identical
        ),
        "frozen_fine_config_is_exact_probe_input": bool(
            _sha256(fine_config_path)
            == preregistered_probe.get("config_file_sha256")
            == probe_inputs.get("config_file_sha256")
            and canonical_sha256(fine_config)
            == preregistered_probe.get("config_canonical_sha256")
            == fine_case.get("config_sha256")
        ),
        "frozen_case_inventory_exact": bool(
            set(frozen_cases) == expected_case_ids
            and frozen_manifest.get("unique_run_count") == 5
        ),
        "physical_signature_unchanged": bool(
            frozen_manifest.get("physical_signature_sha256")
            == design_manifest.get("physical_signature_sha256")
            == canonical_sha256(physical_signature(fine_config))
        ),
        "open_boundary_quality_contract_frozen": bool(
            quality_contract.get("pass") is True
            and quality_contract.get("contract_id")
            == OPEN_BOUNDARY_QUALITY_CONTRACT_ID
            and quality_contract
            == design_manifest.get("quality_contract_audit")
        ),
        "static_refinement_design_passed": bool(
            refinement_audit.get("pass") is True
            and _mapping(
                refinement_audit.get("groups"), "refinement groups"
            ).get("spatial", {}).get("pass")
            is True
            and _mapping(
                refinement_audit.get("groups"), "refinement groups"
            ).get("temporal", {}).get("pass")
            is True
        ),
        "independent_regeneration_byte_identical": bool(
            frozen_files == regeneration_files
        ),
        "claim_boundary_remains_screening_only": bool(
            _mapping(
                frozen_manifest.get("claim_boundary"), "claim boundary"
            ).get("maximum_possible_status")
            == "NUMERICAL_SCREENING_ONLY"
            and _mapping(
                frozen_manifest.get("claim_boundary"), "claim boundary"
            ).get("apparatus_prediction_allowed")
            is False
        ),
    }
    gate_pass = all(gates.values())
    return {
        "schema_version": 1,
        "status": (
            "Q1R3_FROZEN__SPATIAL_SCREENING_AUTHORIZED"
            if gate_pass
            else "Q1R3_FREEZE_FAILED__EXECUTION_NOT_AUTHORIZED"
        ),
        "gate_pass": gate_pass,
        "gates": gates,
        "expected_formal_source_tree_sha256": expected_source_sha256,
        "inputs": {
            "campaign_path": str(campaign_path),
            "campaign_file_sha256": _sha256(campaign_path),
            "frozen_manifest_path": str(frozen_manifest_path),
            "frozen_manifest_file_sha256": _sha256(frozen_manifest_path),
            "design_campaign_file_sha256": _sha256(design_campaign_path),
            "design_manifest_file_sha256": _sha256(design_manifest_path),
            "preregistration_file_sha256": _sha256(preregistration_path),
            "probe_report_file_sha256": _sha256(probe_report_path),
            "frozen_generated_file_sha256": frozen_files,
        },
        "frozen_identity": {
            "physical_signature_sha256": frozen_manifest.get(
                "physical_signature_sha256"
            ),
            "quality_contract_id": quality_contract.get("contract_id"),
            "spatial_case_ids": list(SPATIAL_CASE_IDS),
            "temporal_reuse_case_id": "space_L2_9cells",
            "temporal_new_case_ids": list(TEMPORAL_NEW_CASE_IDS),
        },
        "execution_protocol": {
            "probe_archive_reuse_forbidden": True,
            "fresh_run_root_required": True,
            "stage_1": {
                "name": "spatial",
                "case_ids": list(SPATIAL_CASE_IDS),
                "run_sequentially": True,
                "analyse_before_stage_2": True,
            },
            "early_stop": {
                "condition": (
                    "ANY_SPATIAL_CONFIGURATION_QUALITY_EFFECTIVE_STEP_"
                    "OR_CONVERGENCE_GATE_FAILS"
                ),
                "action": "DO_NOT_EXECUTE_TEMPORAL_NEW_CASES",
            },
            "stage_2": {
                "name": "temporal",
                "authorized_only_if_stage_1_passes": True,
                "reuse_case_id": "space_L2_9cells",
                "new_case_ids": list(TEMPORAL_NEW_CASE_IDS),
                "run_sequentially": True,
            },
        },
        "decision_boundary": {
            "q1r3_frozen": gate_pass,
            "spatial_screening_authorized": gate_pass,
            "temporal_screening_authorized_now": False,
            "numerical_qualification_demonstrated": False,
            "physical_validation_performed": False,
            "experimental_validation_performed": False,
            "apparatus_prediction_allowed": False,
        },
    }


def markdown(report: Mapping[str, Any]) -> str:
    inputs = _mapping(report.get("inputs"), "inputs")
    identity = _mapping(report.get("frozen_identity"), "frozen identity")
    protocol = _mapping(report.get("execution_protocol"), "execution protocol")
    stage_1 = _mapping(protocol.get("stage_1"), "stage 1")
    early_stop = _mapping(protocol.get("early_stop"), "early stop")
    stage_2 = _mapping(protocol.get("stage_2"), "stage 2")
    gate_rows = "\n".join(
        f"| `{name}` | {'PASS' if passed else 'FAIL'} |"
        for name, passed in _mapping(report.get("gates"), "gates").items()
    )
    return f"""# HF3D Q1R3 冻结清单

状态：`{report['status']}`。

Q1R3 已在冻结前细网格探针全部门通过后绑定。该冻结只授权离散化数值筛查，不证明空间收敛、时间收敛、物理有效性、实验有效性或装置预测。

## 冻结身份

- campaign 逐字节 SHA-256：`{inputs['campaign_file_sha256']}`。
- 生成清单逐字节 SHA-256：`{inputs['frozen_manifest_file_sha256']}`。
- 探针报告逐字节 SHA-256：`{inputs['probe_report_file_sha256']}`。
- 物理输入签名：`{identity['physical_signature_sha256']}`。
- 质量契约：`{identity['quality_contract_id']}`。
- 独立临时目录重新生成与冻结目录逐字节一致。

## 冻结门

| 门 | 结果 |
|---|---:|
{gate_rows}

## 执行协议

1. 探针归档不得复用为 Q1R3 筛查结果，必须使用全新的运行根目录。
2. 第一阶段依次执行空间组：`{', '.join(stage_1['case_ids'])}`。
3. 空间组三层完成后先分析。若发生 `{early_stop['condition']}`，动作固定为 `{early_stop['action']}`。
4. 只有空间组全部门通过，才授权时间组新增算例：`{', '.join(stage_2['new_case_ids'])}`；时间粗层复用冻结后的 `space_L2_9cells`，不复用冻结前探针。

## 结论边界

当前仅为 `Q1R3_FROZEN__SPATIAL_SCREENING_AUTHORIZED`。数值资格尚未建立，物理验证和实验验证未执行，装置级预测继续为 `NO-GO`。
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--frozen-manifest", type=Path, required=True)
    parser.add_argument("--design-campaign", type=Path, required=True)
    parser.add_argument("--design-manifest", type=Path, required=True)
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--probe-report", type=Path, required=True)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args()
    report = analyse(
        campaign_path=args.campaign.resolve(),
        frozen_manifest_path=args.frozen_manifest.resolve(),
        design_campaign_path=args.design_campaign.resolve(),
        design_manifest_path=args.design_manifest.resolve(),
        preregistration_path=args.preregistration.resolve(),
        probe_report_path=args.probe_report.resolve(),
        expected_source_sha256=args.expected_source_sha256,
    )
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    args.markdown_output.write_text(markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {"status": report["status"], "gate_pass": report["gate_pass"]}
        )
    )
    return 0 if report["gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
