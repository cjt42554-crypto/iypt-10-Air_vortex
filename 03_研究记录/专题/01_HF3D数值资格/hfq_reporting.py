"""Human-readable numerical qualification report rendering."""
from __future__ import annotations
import math
from typing import Any, Mapping

def report_markdown(report: Mapping[str, Any]) -> str:
    status = str(report.get("status", "UNKNOWN"))
    lines = [
        "# HF3D 数值资格报告",
        "",
        "## 总结论",
        "",
        f"- 状态：`{status}`。",
        "- 结论上限始终是冻结连续模型内的数值资格；本报告不构成实验验证。",
        "- 装置级临界转速、真实接触时刻与稳定气道半径仍须由独立实验检验。",
        "",
    ]
    missing = report.get("missing_cases", [])
    design = report.get("refinement_design_audit", {})
    if isinstance(design, Mapping):
        lines.extend(
            [
                "## 细化设计门",
                "",
                f"- 设计门通过：{design.get('pass')}。",
            ]
        )
        reasons = design.get("reasons", [])
        if isinstance(reasons, list):
            lines.extend(f"- `{reason}`" for reason in reasons)
        groups = design.get("groups", {})
        if isinstance(groups, Mapping):
            for group_name, group in groups.items():
                if not isinstance(group, Mapping):
                    continue
                lines.append(
                    f"- `{group_name}`：登记步长={group.get('configured_maximum_time_step_s')}，"
                    f"静态有效步长={group.get('static_effective_maximum_time_step_s')}。"
                )
        lines.append("")
    if missing:
        lines.extend(
            [
                "## 缺失算例",
                "",
                *[f"- `{case}`" for case in missing],
                "",
            ]
        )
    terminal_failures = report.get("terminal_failures", {})
    if isinstance(terminal_failures, Mapping) and terminal_failures:
        lines.extend(["## 终端失败档案", ""])
        for case_id, item in terminal_failures.items():
            if not isinstance(item, Mapping):
                continue
            error = item.get("error", {})
            message = error.get("message") if isinstance(error, Mapping) else None
            metrics = item.get("execution_metrics", {})
            if not isinstance(metrics, Mapping):
                metrics = {}
            lines.append(
                f"- `{case_id}`：状态=`{item.get('state')}`，完整运行档案="
                f"{item.get('complete_run_archive')}，终端载荷完整性="
                f"{item.get('terminal_archive_payload_integrity_valid')}，显式配置匹配="
                f"{item.get('generated_config_explicit_paths_match')}，源身份有效="
                f"{item.get('execution_source_identity_valid')}，墙钟="
                f"{_fmt_value(metrics.get('wall_clock_duration_s'))} s，峰值 RSS="
                f"{_fmt_value(metrics.get('peak_resident_set_size_bytes'))} byte，"
                f"错误={message}。"
            )
        lines.append("")
    audits = report.get("run_audits", {})
    if isinstance(audits, Mapping) and audits:
        lines.extend(["## 运行与配置审计", ""])
        for case_id, item in audits.items():
            if not isinstance(item, Mapping):
                continue
            grid = item.get("grid", {})
            metrics = item.get("execution_metrics", {})
            if not isinstance(metrics, Mapping):
                metrics = {}
            lines.append(
                f"- `{case_id}`：完成={item.get('completed')}，源身份有效={item.get('execution_source_identity_valid')}，"
                f"最差方向磁子直径格数={grid.get('bar_diameter_cells_worst_axis', 'n/a')}，"
                f"诊断步数={metrics.get('diagnostic_time_step_count', 'n/a')}，墙钟="
                f"{_fmt_value(metrics.get('wall_clock_duration_s'))} s，峰值 RSS="
                f"{_fmt_value(metrics.get('peak_resident_set_size_bytes'))} byte，"
                f"登记步长命中率="
                f"{_fmt_value(metrics.get('configured_step_active_fraction'))}。"
            )
        lines.append("")
    coverage = report.get("execution_identity_coverage", {})
    if not isinstance(coverage, Mapping):
        coverage = {}
    lines.extend(
        [
            "## 执行环境身份",
            "",
            "- 可读完整/终端运行证据的源码边界身份一致："
            f"{report.get('source_tree_consistent_across_runs')}；逻辑证据数="
            f"{report.get('source_identity_evidence_logical_case_count', 0)}。",
            "- 已加载完整运行之间的环境身份一致："
            f"{report.get('execution_runtime_consistent_across_runs')}。",
            "- 已加载逻辑算例覆盖："
            f"{coverage.get('loaded_logical_case_count', 0)}/"
            f"{coverage.get('manifest_logical_case_count', 0)}；"
            f"全部覆盖={coverage.get('complete', False)}。",
        ]
    )
    requirements = report.get("execution_requirements", {})
    if isinstance(requirements, Mapping) and requirements.get(
        "pressure_accelerator_selected"
    ) is not None:
        lines.extend(
            [
                "- 已加载完整运行均满足压力加速器要求："
                f"{report.get('pressure_accelerator_requirement_met_for_loaded_runs')}。",
                "- 整个清单已完整证明压力加速器要求："
                f"{report.get('pressure_accelerator_requirement_met')}。",
            ]
        )
    else:
        lines.append("- 本清单未登记特定压力加速器要求。")
    lines.append("")
    numerical_quality = report.get("numerical_quality", {})
    quality_contract = report.get("quality_contract_audit")
    if isinstance(quality_contract, Mapping):
        inventory = quality_contract.get("inventory_observation", {})
        if not isinstance(inventory, Mapping):
            inventory = {}
        lines.extend(
            [
                "## 开边界质量合同",
                "",
                f"- 合同：`{quality_contract.get('contract_id')}`；审计通过="
                f"{quality_contract.get('pass')}。",
                "- 数值守恒门使用按规定开边界通量修正后的 "
                "`liquid_volume_balance_error_relative`。",
                "- `liquid_mass_error_relative` 在本合同中重分类为液体净存量变化观测；"
                "不设无物理标定依据的绝对通过阈值，但必须有有限值并参与平滑量收敛。",
                f"- 库存观测已登记为平滑量："
                f"{inventory.get('smooth_observable_registered')}。",
                "",
            ]
        )
    if isinstance(numerical_quality, Mapping) and numerical_quality:
        lines.extend(["## 数值质量门", ""])
        for case_id, result in numerical_quality.items():
            if not isinstance(result, Mapping):
                continue
            lines.append(f"- `{case_id}`：通过={result.get('pass')}。")
            inventory = result.get("inventory_observation")
            if isinstance(inventory, Mapping):
                lines.append(
                    "  - `liquid_inventory_change_relative`：值="
                    f"{_fmt_value(inventory.get('value'))}，绝对门="
                    f"{inventory.get('absolute_gate_enabled')}，角色="
                    f"`{inventory.get('role')}`。"
                )
            checks = result.get("checks", {})
            if not isinstance(checks, Mapping):
                continue
            for name, check in checks.items():
                if not isinstance(check, Mapping) or check.get("pass") is not False:
                    continue
                lines.append(
                    f"  - `{name}`：值={_fmt_value(check.get('value'))}，"
                    f"上限={_fmt_value(check.get('maximum'))}。"
                )
        lines.append("")
    groups = report.get("groups", {})
    if isinstance(groups, Mapping):
        for group_name, group in groups.items():
            lines.extend([f"## {group_name} 收敛", ""])
            if not isinstance(group, Mapping):
                lines.extend(["无有效结果。", ""])
                continue
            lines.append(f"- 组状态：`{group.get('status', 'UNKNOWN')}`。")
            effective_step = group.get("effective_time_step_gate", {})
            if isinstance(effective_step, Mapping) and effective_step:
                lines.append(
                    f"- 实际时间步身份门：通过={effective_step.get('pass')}，"
                    f"登记结构有效={effective_step.get('registered_structure_valid')}，"
                    f"实际结构有效={effective_step.get('effective_structure_valid')}。"
                )
            smooth = group.get("smooth_observables", {})
            if isinstance(smooth, Mapping):
                for name, result in smooth.items():
                    if not isinstance(result, Mapping):
                        continue
                    estimate = result.get("estimate", {})
                    lines.append(
                        f"- `{name}`：通过={result.get('pass')}，两最细层相对 $L^2$ 误差="
                        f"{_fmt_nested(estimate, 'pair_fine_medium', 'relative_l2')}，"
                        f"观测阶={_fmt_value(estimate.get('observed_order'))}，"
                        f"细网格 GCI={_fmt_value(estimate.get('fine_grid_gci_l2'))}。"
                    )
            binary = group.get("binary_observables", {})
            if isinstance(binary, Mapping):
                for name, result in binary.items():
                    if not isinstance(result, Mapping):
                        continue
                    lines.append(
                        f"- `{name}` 事件一致性：通过={result.get('pass')}，"
                        f"首次开启时间差={_fmt_value(result.get('first_on_time_difference_s'))} s。"
                    )
            lines.append("")
    lines.extend(
        [
            "## 证据边界",
            "",
            "`NUMERICALLY_QUALIFIED_WITHIN_FROZEN_MODEL` 只表示在预注册输入、网格路径、时间步路径和误差门下，离散结果显示稳定收敛。它不证明连续模型遗漏项可以忽略，也不证明模型参数对应真实装置，更不等于实验预测通过。",
            "",
        ]
    )
    return "\n".join(lines)

def _fmt_value(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return f"{float(value):.6g}"
    return str(value)

def _fmt_nested(mapping: Any, *path: str) -> str:
    current = mapping
    for part in path:
        if not isinstance(current, Mapping):
            return "n/a"
        current = current.get(part)
    return _fmt_value(current)
