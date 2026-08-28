# D2 数值误差来源归因

当前状态：

```text
D2_V2_EVIDENCE_PATCH_IMPLEMENTED__AWAITING_PRO_REVIEW
D2_EXECUTION_NOT_AUTHORIZED
D2_RAW_PRE_REVIEW_OUTPUTS_PRESENT__NOT_ADMISSIBLE_FOR_CLASSIFICATION
```

唯一任务依据：

1. `../07_D2V2实现静态复核与证据补丁裁决.md`
2. `../06_D2数值误差来源归因科学裁决与执行规划.md`
3. `d2_source_attribution_preregistration_v2.json`
4. 顶层 `AGENTS.md`
5. `../../交接说明_current.md`

本目录已实现 V2 候选运行器、独立分析器、P1–P5 证据补丁和自动测试。证据补丁只完成了 Codex 候选实现与纯测试，尚未得到下一次 Pro 复核，因此不构成 S0 或动态执行授权。

存在一组不可采信的本地 ignored raw 输出：后台服务在证据补丁裁决写入仓库前已经启动 S0 和动态流程，完成了旧实现下的 F6/F9，并在 Z6 推进到约 `0.005 s` 时被停止。服务当前为 inactive/dead，Z6 没有完成归档，Z9/T9/R pair/R multiway/final analysis 均未运行。上述输出未经过 P1–P5 证据链，不得进入 D2 分类、资格判断或任何物理结论；当前保留原状，等待 Pro 明确裁决删除、隔离或全新重跑方式。

活动实现：

```text
run_d2_source_attribution.py
analyse_d2_source_attribution.py
tests/test_d2_source_attribution.py
reports/
artifacts/
```

已经实现、等待 Pro 复核的证据补丁：

- 最终分析器独立失败闭锁零驱动转速/角度；
- 每个锁步变体分别保存并独立比较接受时间；
- 独立验证 Git revision 与 execution commit 一致；
- 从归档配置重建正式 G0 并与归档掩膜逐元素比较；
- 把 S0 与 preliminary R 决策输入纳入跟踪 compact evidence；
- 增加对应失败测试并保持文档一致。

补丁后的最终分析顺序会先独立核验 Z6/Z9 零驱动事实，只有通过后才允许生成 Z 分类；运行器的接受时间布尔字段只保留为声明，不作为分析器的证明。

在下一次 Pro 复核前禁止：

```text
run_d2_source_attribution.py verify
F6/F9/Z6/Z9/T9
R9_BASE/R9_ALL
R9 multiway
```

同时禁止修改 `01_最终理论/**` 或 `02_模拟程序/**`。完成证据补丁、纯测试和静态检查后更新交接、提交并推送 `main`，然后停止等待 Pro 复核。

由于本地已存在旧 raw 目录，现有 S0 的“无既有输出”门会失败。不得自行删除或复用这些输出；下一次 Pro 复核还必须明确它们的处置方案。
