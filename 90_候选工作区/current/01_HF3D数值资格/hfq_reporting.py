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
    if missing:
        lines.extend(
            [
                "## 缺失算例",
                "",
                *[f"- `{case}`" for case in missing],
                "",
            ]
        )
    audits = report.get("run_audits", {})
    if isinstance(audits, Mapping) and audits:
        lines.extend(["## 运行与配置审计", ""])
        for case_id, item in audits.items():
            if not isinstance(item, Mapping):
                continue
            grid = item.get("grid", {})
            lines.append(
                f"- `{case_id}`：完成={item.get('completed')}，源身份有效={item.get('execution_source_identity_valid')}，"
                f"最差方向磁子直径格数={grid.get('bar_diameter_cells_worst_axis', 'n/a')}。"
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
