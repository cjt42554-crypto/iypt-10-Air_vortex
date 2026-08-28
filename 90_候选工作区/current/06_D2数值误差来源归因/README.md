# D2 数值误差来源归因

当前状态：

```text
D2_SOURCE_ATTRIBUTION_V2_IMPLEMENTED__EXECUTION_NOT_STARTED
```

唯一执行依据：

1. `../06_D2数值误差来源归因科学裁决与执行规划.md`
2. `d2_source_attribution_preregistration_v2.json`
3. 顶层 `AGENTS.md`
4. `../../../交接说明_current.md`

本目录已实现 V2 候选运行器、独立分析器和自动测试；尚未执行 S0
或任何 D2 动态算例。

活动实现：

```text
run_d2_source_attribution.py
analyse_d2_source_attribution.py
tests/test_d2_source_attribution.py
reports/
artifacts/
```

运行顺序由 V2 合同冻结为：

1. 先提交并推送本目录实现，保持执行开始时 `main` 工作树清洁；
2. 运行 `run_d2_source_attribution.py verify`；
3. 依次运行 F6、F9、Z6、Z9、T9；
4. 运行事务性 R9_BASE/R9_ALL 成对对照；
5. 用独立分析器生成 R 初判，只按冻结大小标签决定执行或跳过五路 S5；
6. 最终分析器重新加载所有归档，发布小型跟踪证据、总报告和阶段台账。

大型运行归档位于 `artifacts/runs/` 并由仓库忽略；最终分类轨迹、硬门极值、
配置差分和小型归档文件由分析器发布到跟踪路径。

禁止修改 `01_最终理论/**` 或 `02_模拟程序/**`。总报告生成后更新交接、
提交并推送 `main`，然后停止等待 Pro 复核。
