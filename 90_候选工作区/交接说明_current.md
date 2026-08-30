# 当前交接

当前状态：

```text
D3_V1_S1_FAIL_CLOSED_UPHELD
D3_V1_FAILURE_CAUSE_OUTPUT_SCHEDULE_IMPLEMENTATION_DEFECT
D3_V1_G6_DT0_NOT_ADMISSIBLE_FOR_CLASSIFICATION_OR_REUSE
D3R1_FINAL_STATIC_REVIEW_ACCEPTED
D3R1_V1_FORENSIC_BUNDLE_ACCEPTED
D3R1_INDEXED_NATIVE_TIME_SCHEDULE_ACCEPTED
D3R1_EXECUTION_AUTHORIZED_IFF_CURRENT_HEAD_PASSES_MACHINE_AUTHORIZATION_AUDIT
D3R1_FRESH_S0_S1_S2_S3_EXECUTION_NOT_YET_PERFORMED
```

## 本轮 Pro 复核与裁决

裁决正文：

```text
90_候选工作区/current/16_D3R1最终静态复核与全新执行授权.md
```

Pro 已独立复核：

```text
reviewed main: d57a6b797343417229439e01e8cbec63d44ba38b
R1 implementation commit: 80b42121c9a67e3ed80368e6eeeb10c48ccc59f1
V1 forensic commit: 73c3b54183d0304116bedfc9b955253d64a2b568
R1 preregistration blob: a692d4c81684925cb4276a73a195dca4f5175ef7
formal source tree: dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8
```

结论：

1. V1 forensic bundle 可接受。五个 ignored raw 的冻结 hash、S0/S1/S2/S3 状态、失败标记和后续阶段未执行均通过；`partial.npz` 只读取成员清单和 `frame_time_s`，没有读取或报告主要物理观测量。
2. V1 失败维持，G6_DT0 不得分类或复用。
3. R1 与 V1 的 19 个科学字段逐值相同；六算例矩阵、正式 G0、输出时刻、质量门、公式、阈值和分类优先级未改变。
4. 输出目标改为直接索引冻结 `native_output_times_s`，不再重复累加；11 个目标的位模式和逐位硬门保持原样。
5. V1 raw 与 R1 raw/evidence/reports 完全隔离。运行器和独立分析器拒绝 V1 partial、V1 归档、D2 输出和 checkpoint 作为 R1 输入。
6. S0–S2 失败事务、共享阶段门、S3 staging/完整验证/raw-ledger-last 发布继续成立。
7. 提交的零步静态证据记录 D3R1 测试 72/72、CUDA FP64、科学字段同一性、V1 hash、证据根隔离和索引式时刻门全部通过；R1 时间步推进为 0，R1 动态输出不存在，授权仍为默认拒绝。网页端没有独立重跑这些命令。

未发现继续阻塞一次全新 D3R1 短窗执行的实现或科学合同问题。

## 机器授权判定

本裁决、current 和 history 所在提交是受复核父提交。紧随其后的唯一直接子提交只能修改：

```text
90_候选工作区/current/07_D3跨网格时间步响应诊断/d3_r1_execution_authorization.json
```

执行许可不以自然语言交接为唯一依据。只有当前 HEAD 上运行器与独立分析器的机器审计同时满足：

```text
status = AUTHORIZED_BY_PRO_FOR_BOUND_IMPLEMENTATION
real_cuda_dynamic_execution = true
authorized_stages = [S0, S1, S2, S3]
automatic_follow_on = PROHIBITED
current HEAD = authorization-only child of reviewed parent
all implementation/blob/hash/source/R1-preregistration/ruling bindings = PASS
V1 frozen raw and forensic hashes = PASS
R1/V1 scientific identity and evidence-root isolation = PASS
```

才视为 D3R1 动态执行已授权。V1 授权对象不能授权 R1；旧授权、额外文件变化、实现漂移、V1 hash 漂移或任一机器门失败均不得执行。

## 下一项唯一任务

Codex 拉取当前远端 `main` 后，先运行不推进时间步的授权、身份、V1 hash、科学字段同一性、证据隔离、CUDA、空 R1 输出和单写入者检查。全部通过后，才可在同一清洁授权 HEAD 上逐阶段完成一次全新 D3R1：

```text
S0 → 检查精确 PASS
S1: G6_DT0 → G6_DT1 → G6_DT2 → 检查精确 PASS
S2: G9_DT0 → G9_DT1 → G9_DT2 → 检查精确 PASS
S3: 独立分析、事务发布、完整重载审计后停止
```

命令：

```bash
"$D3_GPU_PYTHON" \
  "90_候选工作区/current/07_D3跨网格时间步响应诊断/run_d3_cross_grid_temporal.py" \
  prepare-execution

"$D3_GPU_PYTHON" \
  "90_候选工作区/current/07_D3跨网格时间步响应诊断/run_d3_cross_grid_temporal.py" \
  run-stage --stage S1

"$D3_GPU_PYTHON" \
  "90_候选工作区/current/07_D3跨网格时间步响应诊断/run_d3_cross_grid_temporal.py" \
  run-stage --stage S2

"$D3_GPU_PYTHON" \
  "90_候选工作区/current/07_D3跨网格时间步响应诊断/analyse_d3_cross_grid_temporal.py" \
  final
```

`automatic_follow_on = PROHIBITED`。每个阶段结束后必须显式读取台账、失败标记和阶段证据，只有精确 `EXECUTED/PASS` 才可启动下一阶段；禁止后台无检查串联。

## 执行前冻结条件

1. `main` 工作树清洁，HEAD 为有效 R1 授权提交；S0–S3 之间不得产生 commit 或文件修改。
2. R1 授权子提交相对受复核父提交的唯一变化是 `d3_r1_execution_authorization.json`。
3. 五个 V1 ignored raw 和三份 forensic 文件必须存在且 hash 精确；不得删除、移动、覆盖或复用 V1 raw。
4. R1 `artifacts/r1/runs/`、动态 reports、manifest、compact evidence、S3 staging 和 raw-ledger 临时文件全部不存在；未知残留不得删除后继续。
5. 没有 D3/D3R1/HF3D 后台或并发写入进程，整个序列维持单一前台写入者。
6. 使用零步静态证据记录的 GPU Python/CuPy 环境；实际 CUDA 且无 CPU 回退。
7. 正式源码树保持：

```text
dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8
```

8. 不把 V1 partial、case summary、S0 或其他 V1 动态内容作为 R1 初态或分类输入；V1 只做 hash 身份检查。

## 失败停止条件

任一命令非零退出、异常、硬门失败、输出时刻非逐位相等、CUDA 回退、`execution_failure.json` 出现、V1 hash 漂移、身份漂移、未知输出、并发写入、外部中断或台账/归档/manifest/报告不一致，立即停止。

失败后禁止删除、修补、拼接、选择性保留或重跑同一阶段；必须原样保留 V1 和 R1 证据并更新交接后交 Pro 复核。

## 成功交付

仅当 S3 `EXECUTED/PASS`、tracked/raw ledger 一致、六算例硬门通过、原生输出时刻逐位精确，且报告、manifest、compact evidence 和事务记录的独立重载审计全部通过时：

1. 提交跟踪的 R1 报告、manifest、compact evidence 与更新后的交接；
2. 明确记录唯一执行提交和六算例状态；
3. 立即停止交 Pro 做结果复核，无论分类标签是什么；
4. 不自动运行更小时间步、更细网格、长期窗口、Q1R4 或 D4。

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
D3R1_EXACT_SNAPSHOT_REAL_CUDA_EXECUTION_AUTHORIZED_NOT_YET_EXECUTED
NUMERICAL_QUALIFICATION = NOT_DEMONSTRATED
PHYSICAL_VALIDATION = NOT_PERFORMED
EXPERIMENTAL_VALIDATION = NOT_PERFORMED
APPARATUS_PREDICTION = DISALLOWED
```

正式理论 `01_最终理论/**`、正式程序 `02_模拟程序/**`、顶层 `AGENTS.md`、V1 合同/授权、V1 raw、R1 科学字段、六算例矩阵、输出时刻和分类优先级本轮均未修改。本轮没有运行 R1 S0、动态算例或实验。
