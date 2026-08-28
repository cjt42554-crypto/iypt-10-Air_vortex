# D2 数值误差来源归因

当前状态：

```text
D2_V2_IMPLEMENTATION_STATIC_REVIEW_CONDITIONAL_PASS
D2_EXECUTION_NOT_AUTHORIZED__EVIDENCE_PATCH_REQUIRED
```

唯一任务依据：

1. `../07_D2V2实现静态复核与证据补丁裁决.md`
2. `../06_D2数值误差来源归因科学裁决与执行规划.md`
3. `d2_source_attribution_preregistration_v2.json`
4. 顶层 `AGENTS.md`
5. `../../交接说明_current.md`

本目录已实现 V2 候选运行器、独立分析器和自动测试；尚未执行 S0 或任何 D2 动态算例。Pro 静态复核维持主体实现，但要求在开跑前补齐远端独立证据链。

活动实现：

```text
run_d2_source_attribution.py
analyse_d2_source_attribution.py
tests/test_d2_source_attribution.py
reports/
artifacts/
```

本轮 Codex 只允许实现证据补丁：

- 最终分析器独立失败闭锁零驱动转速/角度；
- 每个锁步变体分别保存并独立比较接受时间；
- 独立验证 Git revision 与 execution commit 一致；
- 从归档配置重建正式 G0 并与归档掩膜逐元素比较；
- 把 S0 与 preliminary R 决策输入纳入跟踪 compact evidence；
- 增加对应失败测试并保持文档一致。

在下一次 Pro 复核前禁止：

```text
run_d2_source_attribution.py verify
F6/F9/Z6/Z9/T9
R9_BASE/R9_ALL
R9 multiway
```

同时禁止修改 `01_最终理论/**` 或 `02_模拟程序/**`。完成证据补丁、纯测试和静态检查后更新交接、提交并推送 `main`，然后停止等待 Pro 复核。
