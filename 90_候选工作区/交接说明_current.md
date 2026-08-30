# 当前交接

当前状态：

```text
D2_V2_EXECUTION_REVIEW_ACCEPTED_WITH_NONBLOCKING_LEDGER_CLARIFICATION
D3_V1_S1_FAIL_CLOSED_UPHELD
D3_V1_FAILURE_CAUSE_OUTPUT_SCHEDULE_IMPLEMENTATION_DEFECT
D3_V1_G6_DT0_NOT_ADMISSIBLE_FOR_CLASSIFICATION_OR_REUSE
D3R1_RESULT_BLIND_REMEDIATION_IMPLEMENTATION_AUTHORIZED
D3R1_DYNAMIC_EXECUTION_NOT_AUTHORIZED
```

## 本轮 Pro 复核结论

独立裁决正文：

```text
90_候选工作区/current/15_D3S1输出时刻硬门失败独立复核与D3R1重跑裁决.md
```

D3 V1 的失败维持，不追认 G6_DT0 为 PASS。冻结合同要求六个算例的原生输出时刻逐位等于：

```text
[0.000, 0.001, ..., 0.009, 0.010]
```

执行提交的候选运行器却按：

```python
next_output += output_interval
```

重复生成时刻，并把该值写入 `state.time_s`；正式 `FrameRecorder` 直接记录这个值。独立 IEEE-754 复算确认：

```text
frozen 0.009:
  float.hex = 0x1.26e978d4fdf3bp-7
  uint64    = 4576341768551784251

repeated-addition value:
  decimal   = 0.009000000000000001
  float.hex = 0x1.26e978d4fdf3cp-7
  uint64    = 4576341768551784252

ULP distance = 1
```

运行器又把最终时刻单独钳到 `end_time=0.01`，所以索引 9 是唯一必然不逐位相等的元素。该机制与 current 记录的失败完全一致。

裁决区分如下：

```text
V1 hard-gate failure = VALID AND UPHELD
root cause = candidate output-schedule implementation defect
physical model failure = not established
numerical instability = not established
D3 scientific classification = not formed
```

网页端无法读取被 `.gitignore` 忽略的本地 raw，因此没有独立重算 current 中的全部质量值或 raw hash；下一阶段必须先生成跟踪 forensic bundle 补齐长期证据链。

## V1 证据处置

以下 V1 本地 raw 必须保持原路径、原内容，不得删除、移动、覆盖或复用：

```text
artifacts/runs/s0_identity_config_fresh_evidence_gate.json
artifacts/runs/stage_ledger.json
artifacts/runs/execution_failure.json
artifacts/runs/cases/G6_DT0/case_summary.json
artifacts/runs/cases/G6_DT0/partial.npz
```

完整根：

```text
90_候选工作区/current/07_D3跨网格时间步响应诊断/
```

冻结 SHA-256：

```text
e50905260c9d7732ef68e2cf76b4ed661b19383b4f92ade409463e58b63244b2
46ff32a79a62dd6cb544a04a7377d55888dff75e313f650bd97c32cf2df62afa
d63bd745ee455bfaaba563b6b348bb7f7c822d5f6d33977c6f0f02daadb7def5
15b2e76a57ad00dd93eb7a6652f4adf697f48c9dc772f57f666a771a537ca27a
d1b5ae2723ec60619d20fe2ce7d8d8010941083167fe1d71554990b898d9f3ed
```

V1 的 G6_DT0 不得用于分类、调参、补跑或作为 D3R1 输入。

## 下一项唯一任务

把当前 `main` 交给 Codex，严格按裁决正文实现 **D3R1**，但不得运行动态阶段。

### 阶段 A：先冻结 V1 失败证据

在修改运行器前新增并实际运行只读审计：

```text
07_D3跨网格时间步响应诊断/audit_d3_v1_s1_failure.py
07_D3跨网格时间步响应诊断/artifacts/v1_failure/
  d3_v1_s1_failure_forensic.json
  d3_v1_s1_failure_forensic.md
```

必须核对五个冻结 hash、S0/S1/S2/S3 状态、执行提交、`subsequent_stages_executed=false`，并从 `partial.npz` 只读取文件清单和 `frame_time_s`。记录每个时刻的 decimal、`float.hex()`、uint64 和 ULP 距离；不得读取或报告主要物理观测量。任一 hash 或语义不符立即停止。

### 阶段 B：建立 D3R1

新增：

```text
d3_cross_grid_temporal_preregistration_r1.json
d3_r1_execution_authorization.json
```

原 V1 合同和授权对象保持逐字节不变。R1 协议 ID：

```text
D3_HF3D_CROSS_GRID_TEMPORAL_RESPONSE_R1
```

R1 必须机器证明所有科学字段与 V1 逐值相同；只允许协议版本、证据路径、默认拒绝授权、失败绑定和输出调度构造规则不同。

调度器必须直接按冻结 `native_output_times_s` 的索引取目标值，禁止 `next_output += output_interval`。bitwise 原生时刻硬门保持不变，不改为容差门。

### 阶段 C：隔离新旧证据根

V1 继续位于：

```text
artifacts/runs/**
```

R1 使用：

```text
artifacts/r1/runs/**
artifacts/r1/evidence/**
artifacts/r1/d3_implementation_static_review.json
artifacts/r1/d3_cross_grid_temporal_manifest.json
reports/r1/d3_cross_grid_temporal_report.json
reports/r1/d3_cross_grid_temporal_report.md
```

R1 在创建新 raw 前还必须验证 V1 五个冻结 hash 未变化，且禁止读取 V1 partial/case summary/S0 作为 R1 输入。

### 阶段 D：测试和停止

必须新增并通过：

- V1 重复累加在索引 9 产生 1 ULP 的回归测试；
- R1 全部 11 个时刻逐位等于冻结向量；
- 六配置共享同一 uint64 时刻向量；
- 无跳帧、重复帧或插值；
- R1/V1 科学字段深比较；
- R1/V1 证据根不重叠；
- V1 hash 漂移、V1 输入复用和旧授权均失败闭锁；
- 既有状态机、失败事务、S3 事务和 Unicode 路径测试继续通过。

重新运行全部候选测试、Ruff、格式、限定 mypy、`compileall`、严格 JSON、Pandoc、可用的 Markdown 数学检查、必要渲染和 `git diff --check`，再生成零时间步 R1 静态证据。

完成后立即停止交 Pro 复核：

```text
R1 authorization = NOT_AUTHORIZED
R1 time_steps_advanced = 0
R1 dynamic outputs = absent
```

不得运行 R1 S0、S1、S2 或 S3，不得自行签发允许态授权。

## 精确允许范围

仅允许修改或新增：

```text
90_候选工作区/current/07_D3跨网格时间步响应诊断/**
90_候选工作区/交接说明_current.md
90_候选工作区/交接说明_history.md
```

禁止修改：

```text
01_最终理论/**
02_模拟程序/**
AGENTS.md
V1 d3_cross_grid_temporal_preregistration.json
V1 d3_execution_authorization.json
V1 ignored raw 内容或路径
既有 D1/D2/Q1R 证据
```

禁止放宽科学阈值、质量阈值、六算例矩阵、输出时刻、CUDA 要求或分类优先级。

## 当前结论上限

```text
Q1R3_SPATIAL_STAGE_FAIL_CLOSED__TEMPORAL_NOT_AUTHORIZED
Q1R4_D0_FAIL_CLOSED_AT_STATIC_GEOMETRY
D1_GEOMETRY_EFFECT_SMALL_IN_REGISTERED_WINDOW
D2_Z_SMALL_NO_CLEAR_DIRECTION
D2_T_MATERIAL_REDUCTION_SIGNAL
D2_R_MIXED_OR_INDETERMINATE_NO_CLEAR_DIRECTION
D3_V1_INCONCLUSIVE_FAIL_CLOSED_AT_S1_G6_DT0_NATIVE_OUTPUT_TIMES_EXACT_GATE
D3_V1_FAILURE_CAUSE_OUTPUT_SCHEDULE_IMPLEMENTATION_DEFECT
D3_V1_SCIENTIFIC_CLASSIFICATION = NOT_FORMED
D3R1_REMEDIATION_IMPLEMENTATION_AUTHORIZED
D3R1_DYNAMIC_EXECUTION = NOT_AUTHORIZED
NUMERICAL_QUALIFICATION = NOT_DEMONSTRATED
PHYSICAL_VALIDATION = NOT_PERFORMED
EXPERIMENTAL_VALIDATION = NOT_PERFORMED
APPARATUS_PREDICTION = DISALLOWED
```

本轮没有修改正式理论、正式程序、V1 合同、V1 授权对象或动态 raw，没有运行任何新数值算例或实验。
