# D2 数值误差来源归因

当前状态：

```text
D2_SOURCE_ATTRIBUTION_V2_AUTHORIZED_NOT_EXECUTED
```

唯一执行依据：

1. `../06_D2数值误差来源归因科学裁决与执行规划.md`
2. `d2_source_attribution_preregistration_v2.json`
3. 顶层 `AGENTS.md`
4. `../../../交接说明_current.md`

本目录当前只包含执行规划、V2 机器合同和任务入口；D2 尚未实现或运行。

Codex 应在本目录新增：

```text
run_d2_source_attribution.py
analyse_d2_source_attribution.py
tests/test_d2_source_attribution.py
reports/
artifacts/
```

禁止修改 `01_最终理论/**` 或 `02_模拟程序/**`。总报告生成后更新交接、提交并推送 `main`，然后停止等待 Pro 复核。
