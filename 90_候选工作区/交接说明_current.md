# 当前交接

当前状态：

```text
D2_V2_EXECUTION_REVIEW_ACCEPTED_WITH_NONBLOCKING_LEDGER_CLARIFICATION
D3_AUTHORIZATION_UNICODE_PATH_AUDIT_PATCH_ACCEPTED
D3_PATCHED_SNAPSHOT_AUTHORIZATION_PRECHECK_PASS
D3_S0_EXECUTED_PASS
D3_S1_FAILED_STOPPED_AT_G6_DT0_NATIVE_OUTPUT_TIMES_EXACT_GATE
D3_S2_S3_NOT_EXECUTED
D3_SCIENTIFIC_CLASSIFICATION_NOT_FORMED
```

## 本轮执行结论

Pro 裁决与再授权正文：

```text
90_候选工作区/current/14_D3授权审计中文路径修复复核与全新执行再授权.md
```

本地已快进到唯一授权子提交：

```text
execution commit = 90ed5c1019d8265f6e9730ff8a2d36be61deec8b
reviewed parent = a5fc929b64b70aca16354817cb4ba620c32affb5
```

运行器与独立分析器的授权审计、真实 Unicode 唯一路径、父提交、实现 blob/hash、预注册、正式源码树、裁决文档、六配置、清洁工作树、空输出和 RTX 5060 Laptop GPU CuPy FP64 门在 raw 创建前全部通过。

S0 随后成功提交，时间步推进数为 0。S1 只完成第一个算例 `G6_DT0`；该算例完整推进到 `0.010 s` 后，冻结质量门 `native_output_times_exact` 失败，命令以退出码 2 结束并进入唯一失败事务。运行器没有启动 `G6_DT1`、`G6_DT2`、S2 或 S3，Codex 也没有重跑、删除、修补或继续。

最终台账状态：

```text
S0 = EXECUTED / PASS
S1 = FAILED_STOPPED / D3_INCONCLUSIVE_FAIL_CLOSED
S2 = NOT_EXECUTED
S3 = NOT_EXECUTED
terminal_state = D3_INCONCLUSIVE_FAIL_CLOSED
automatic_follow_on = PROHIBITED
```

台账语义、证据路径和失败记录交叉审计全部通过。`execution_failure.json` 明确记录 `subsequent_stages_executed = false`。

## G6_DT0 失败证据

`G6_DT0` 实际完成：

```text
simulated_time_s = 0.010
accepted_steps = 1820
retry_attempts = 0
wall_time_s = 818.6739202680001
CUDA device = NVIDIA GeForce RTX 5060 Laptop GPU
compute capability = 12.0
requested/selected = cuda/cuda
fallback = false
execution commit start/end = 90ed5c1019d8265f6e9730ff8a2d36be61deec8b
formal source tree start/end = dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8
```

除 `native_output_times_exact` 外，其余质量门全部通过。主要记录值：

```text
maximum_advective_cfl = 0.07135371797767952
maximum_cfl_retry_count = 0
maximum_pressure_relative_residual = 9.999623167132062e-08
maximum_projected_divergence_s_inv = 7.454811945528661e-06
maximum_liquid_volume_balance_error_relative = 3.1520299546005352e-12
cumulative_phase_clip_relative = 3.152340439859344e-12
cumulative_phase_repair_relative = 0.0
configured_time_step_active_fraction = 0.9945054945054945
all_state_and_diagnostic_values_finite = true
```

输出时刻只有索引 9 不逐位相等：

```text
observed[9] = 0.009000000000000001
expected[9] = 0.009
delta = 1.734723475976807e-18 s
spacing(expected[9]) = 1.734723475976807e-18 s
delta = 1 ULP
all other registered output times = bitwise equal
```

只读复算确认，从 `0.001` 开始重复执行八次 `next_output += 0.001` 得到的正是归档值 `0.009000000000000001`；冻结 `EXPECTED_OUTPUT_TIME` 的字面量 `0.009` 是相邻的低一位浮点值。这个事实不撤销本轮失败：合同要求逐位精确，本轮授权明确禁止失败后重跑或现场修改门。

## 必须保留的本地证据

以下 raw 被 `.gitignore` 忽略，只存在于当前用户机器；不得删除、移动、覆盖或复用：

```text
e50905260c9d7732ef68e2cf76b4ed661b19383b4f92ade409463e58b63244b2  artifacts/runs/s0_identity_config_fresh_evidence_gate.json
46ff32a79a62dd6cb544a04a7377d55888dff75e313f650bd97c32cf2df62afa  artifacts/runs/stage_ledger.json
d63bd745ee455bfaaba563b6b348bb7f7c822d5f6d33977c6f0f02daadb7def5  artifacts/runs/execution_failure.json
15b2e76a57ad00dd93eb7a6652f4adf697f48c9dc772f57f666a771a537ca27a  artifacts/runs/cases/G6_DT0/case_summary.json
d1b5ae2723ec60619d20fe2ce7d8d8010941083167fe1d71554990b898d9f3ed  artifacts/runs/cases/G6_DT0/partial.npz
```

完整相对根为：

```text
90_候选工作区/current/07_D3跨网格时间步响应诊断/
```

`G6_DT1`、`G6_DT2`、全部 G9 算例目录、动态报告、最终 manifest、compact evidence 和 S3 staging 均不存在。Git 工作树仍清洁，当前无 D3/HF3D 写入进程。

## 下一项唯一任务

由 Pro 独立复核本次 S1 失败、冻结精确门语义、重复累加产生的 1 ULP 差异和本地证据摘要，并裁决后续。Pro 的详细最终分析等价于人工裁决。

在新裁决到来前，Codex 不得：

1. 重跑 S0、S1 或 `G6_DT0`；
2. 启动 `G6_DT1`、`G6_DT2`、S2 或 S3；
3. 删除、移动、改写或选择性保留上述 raw；
4. 修改冻结时刻、容差、质量门、矩阵、阈值、正式源码或授权对象；
5. 把 `G6_DT0` 的部分证据用于 D3 分类或数值资格结论。

## 当前结论上限

```text
Q1R3_SPATIAL_STAGE_FAIL_CLOSED__TEMPORAL_NOT_AUTHORIZED
Q1R4_D0_FAIL_CLOSED_AT_STATIC_GEOMETRY
D1_GEOMETRY_EFFECT_SMALL_IN_REGISTERED_WINDOW
D2_Z_SMALL_NO_CLEAR_DIRECTION
D2_T_MATERIAL_REDUCTION_SIGNAL
D2_R_MIXED_OR_INDETERMINATE_NO_CLEAR_DIRECTION
D3_INCONCLUSIVE_FAIL_CLOSED_AT_S1_G6_DT0_NATIVE_OUTPUT_TIMES_EXACT_GATE
D3_SCIENTIFIC_CLASSIFICATION = NOT_FORMED
NUMERICAL_QUALIFICATION = NOT_DEMONSTRATED
PHYSICAL_VALIDATION = NOT_PERFORMED
EXPERIMENTAL_VALIDATION = NOT_PERFORMED
APPARATUS_PREDICTION = DISALLOWED
```

正式理论 `01_最终理论/**`、正式程序 `02_模拟程序/**`、顶层 `AGENTS.md`、冻结预注册、科学阈值、六算例矩阵、输出时刻和标签优先级均未修改。本轮实际运行了 S0 和一个完整 `G6_DT0` 动态算例，但没有形成 D3 分类、数值资格、物理验证、实验验证或装置预测结论。
