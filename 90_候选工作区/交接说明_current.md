# 当前交接

当前状态：

```text
D3R1_S2_FAILED_STOPPED_AT_G9_DT2_PROJECTED_DIVERGENCE_HARD_GATE
D3R1_S3_NOT_EXECUTED
D3R1_SCIENTIFIC_CLASSIFICATION_NOT_FORMED
D3R1_FAILURE_EVIDENCE_FROZEN_FOR_PRO_REVIEW
HF3D_PERFORMANCE_OPTIMIZATION_MANDATORY_AFTER_FAILURE_REVIEW
```

## 本轮结果

D3R1 在执行提交：

```text
be349aec2f1bdcdb232e282a11175921eda7976b
```

上依次完成：

```text
S0 = EXECUTED / PASS
S1 = EXECUTED / PASS
S2 = FAILED_STOPPED / D3_INCONCLUSIVE_FAIL_CLOSED
S3 = NOT_EXECUTED
```

S1 的 `G6_DT0`、`G6_DT1`、`G6_DT2` 和 S2 的 `G9_DT0`、`G9_DT1` 均完整通过全部冻结硬门，真实 CUDA 无回退，执行提交和正式源码树起止一致。

`G9_DT2` 在最后确认登记输出 `0.009 s` 之后，于第 `6695` 个接受步自然触发冻结硬门：

```text
failure type = FloatingPointError
failure message = G9_DT2 hard gate failed: projected_divergence
failure simulated time = 0.009196624999999948 s
maximum projected divergence = 0.00010249185899624536 s^-1
frozen maximum = 0.0001 s^-1
gate ratio = 1.0249185899624536
retry attempts = 0
```

压力相对残差、接受 CFL、体积平衡、clip/repair、配置时间步命中、有限值和真实 CUDA 门仍通过。`complete_to_registered_end_time` 与 `native_output_times_exact` 为假，是因为投影散度硬门在 `0.010 s` 前停止；不能把它们解释成独立调度失败。

运行器正常完成失败事务：

```text
artifacts/r1/runs/cases/G9_DT2/case_summary.json
artifacts/r1/runs/cases/G9_DT2/partial.npz
artifacts/r1/runs/execution_failure.json
artifacts/r1/runs/stage_ledger.json
```

阶段台账已精确登记 S2 `FAILED_STOPPED`、S3 `NOT_EXECUTED` 和 `subsequent_stages_executed = false`。不得运行 S3，不得重跑、修补、拼接、选择性保留或删除本次 R1 证据。

## 跟踪取证包

新增独立取证工具和跟踪输出：

```text
90_候选工作区/current/07_D3跨网格时间步响应诊断/audit_d3_r1_s2_failure.py
90_候选工作区/current/07_D3跨网格时间步响应诊断/artifacts/r1_failure/d3_r1_s2_failure_forensic.json
90_候选工作区/current/07_D3跨网格时间步响应诊断/artifacts/r1_failure/d3_r1_s2_failure_forensic.md
```

运行：

```bash
/usr/bin/python3 90_候选工作区/current/07_D3跨网格时间步响应诊断/audit_d3_r1_s2_failure.py
```

应得到：

```text
D3R1_S2_G9_DT2_PROJECTED_DIVERGENCE_FAILURE_FORENSIC_GATE_PASS
```

取证结果：

```text
raw file count = 40
raw total size = 359454291 bytes
raw canonical snapshot SHA-256 = 906a32f71f3e095afc7cba6a3eddf7a83e4d639d36dc0b3014278b3e0001b032
failed partial SHA-256 = 8f1e0973eda48c2ecc54f10001f28b988d353e85a728a16ec24f74582527cbcc
failed partial size = 60848588 bytes
```

工具逐文件重算全部 ignored R1 raw 的路径、大小和 SHA-256；重新核验五个完整案例摘要和所有归档载荷；核验失败摘要、失败标记、台账和冻结阈值；发布前后 raw 快照完全一致。

对 `partial.npz` 只枚举成员名称并读取 `frame_time_s`。十个已记录时刻逐位等于冻结向量 `0.000` 至 `0.009 s`；未读取状态数组或主要物理观测数组。

## Pro 性能裁决仍然有效

已拉取 Pro 性能裁决提交：

```text
d1aff0b6f46783096ecfcc8c820b68145b78aa55
```

详细裁决：

```text
90_候选工作区/current/17_D3R1运行中性能裁决与HF3D优化门.md
```

该裁决确认正式 HF3D 存在稳定主机单核瓶颈和 GPU 饥饿，当前 D3R1 证据冻结并经 Pro 结果复核后，下一项工程任务必须是：

```text
90_候选工作区/current/17_HF3D性能优化/**
```

但本次 D3R1 已自然失败，旧授权立即失效。任何后续动态运行都必须在性能候选通过 P0--P3、Pro 独立静态复核、新裁决父提交和唯一授权子提交后，使用新协议与全新证据根从零执行六案例及 S3；不得拼入本次完整或 partial 轨迹。

## 下一项唯一任务

先由 Pro 读取当前 GitHub `main`、取证包和本交接，独立复核：

1. S2 的失败事务和原始快照是否成立；
2. `projected_divergence` 是否是本次唯一先触发的数值硬门；
3. S3 未执行、科学分类未形成和旧授权失效是否维持；
4. 是否允许进入性能裁决的 P0 生产等价基线与 profiler 阶段；
5. P0 是否需要针对本次散度失败增加额外的结果盲诊断边界。

在 Pro 完整最终裁决前：

```text
不得运行 S3
不得重跑 D3R1
不得修改正式理论或正式程序
不得启动新的 HF3D 动态基准
不得读取 partial 的状态或主要物理观测数组
不得删除、移动或覆盖 V1/R1 raw
```

## 当前结论上限

```text
D3_V1_INCONCLUSIVE_FAIL_CLOSED_AT_S1_G6_DT0_NATIVE_OUTPUT_TIMES_EXACT_GATE
D3_V1_FAILURE_CAUSE_OUTPUT_SCHEDULE_IMPLEMENTATION_DEFECT
D3_V1_SCIENTIFIC_CLASSIFICATION = NOT_FORMED

D3R1_S2 = FAILED_STOPPED
D3R1_FAILURE_CASE = G9_DT2
D3R1_FAILURE_GATE = PROJECTED_DIVERGENCE
D3R1_S3 = NOT_EXECUTED
D3R1_SCIENTIFIC_CLASSIFICATION = NOT_FORMED

HF3D_PERFORMANCE_DEFECT = CONFIRMED
NUMERICAL_QUALIFICATION = NOT_DEMONSTRATED
PHYSICAL_VALIDATION = NOT_PERFORMED
EXPERIMENTAL_VALIDATION = NOT_PERFORMED
APPARATUS_PREDICTION = DISALLOWED
```

本轮未修改 `01_最终理论/**`、`02_模拟程序/**`、顶层 `AGENTS.md`、D3R1 合同、授权对象、六案例矩阵、阈值、输出时刻或既有 raw。根目录 README 未读取或修改。
