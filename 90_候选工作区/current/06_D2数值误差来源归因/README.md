# D2 数值误差来源归因

当前状态：

```text
D2_V2_EVIDENCE_PATCH_REVIEW_ACCEPTED
D2_PRE_REVIEW_RAW_QUARANTINE_REQUIRED
D2_FRESH_EXECUTION_AUTHORIZED_AFTER_SANITATION_COMMIT
```

唯一任务依据：

1. `../08_D2V2证据补丁复核与全新执行授权.md`
2. `../07_D2V2实现静态复核与证据补丁裁决.md`
3. `../06_D2数值误差来源归因科学裁决与执行规划.md`
4. `d2_source_attribution_preregistration_v2.json`
5. 顶层 `AGENTS.md`
6. `../../交接说明_current.md`

P1–P5 证据补丁已经通过 Pro 静态复核。正式 G0 重建、逐变体接受时间、零驱动、执行提交绑定及 S0/preliminary-R 跟踪证据主链可用于全新 D2 执行。

旧提交 `75a34f9` 产生的 S0、F6、F9 和未完成 Z6 raw 输出仍为不可采信证据，禁止复用。任何新执行前必须：

1. 确认服务与 D2 进程全部停止；
2. 生成并提交 `artifacts/pre_review_raw_quarantine_manifest.json`；
3. 把整个旧 `artifacts/runs` 移到仓库同级 `../iypt10_d2_quarantine_75a34f9_pre_review_20260829/`；
4. 核验活动 raw、报告、evidence、manifest 和 failure 路径均不存在；
5. 保持 `main`、清洁工作树和单一卫生提交 HEAD。

卫生提交之后不得再提交代码或文档，直至最终分析完成。必须在同一 HEAD 上顺序运行：

```text
run_d2_source_attribution.py verify
F6
F9
Z6
Z9
T9
R9_BASE/R9_ALL
analyse_d2_source_attribution.py preliminary-r
条件性 R9 multiway
analyse_d2_source_attribution.py final
```

任一阶段失败立即停止，不得形成部分分类。旧 raw 隔离目录不得被任何新命令读取。

禁止修改 `01_最终理论/**` 或 `02_模拟程序/**`。最终报告、compact evidence、阶段台账和交接提交并推送后停止，等待 Pro 复核。
