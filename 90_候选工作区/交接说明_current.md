# 当前交接

当前状态：

```text
D3R1_S2_FAILURE_FORENSIC_ACCEPTED_AT_FROZEN_HASH_AUDIT_LEVEL
D3R1_DIRECT_TRIGGER_PROJECTED_DIVERGENCE_UPHELD
D3R1_FAILURE_MECHANISM_NOT_YET_ESTABLISHED
D3R1_S3_NOT_EXECUTED__SCIENTIFIC_CLASSIFICATION_NOT_FORMED
D3R1_OLD_EXECUTION_AUTHORIZATION_TERMINATED
HF3D_P0_PERFORMANCE_AND_RESULT_BLIND_DIVERGENCE_DIAGNOSTICS_AUTHORIZED
HF3D_P1_IMPLEMENTATION_AND_FORMAL_SOURCE_MODIFICATION_NOT_AUTHORIZED
```

## 本轮 Pro 复核

详细裁决：

```text
90_候选工作区/current/18_D3R1S2散度失败独立复核与HF3D性能P0授权.md
```

复核对象：

```text
reviewed main: fde3ad8c715034ef5bfa426d8e106810ae79e912
D3R1 execution commit: be349aec2f1bdcdb232e282a11175921eda7976b
formal source tree: dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8
R1 raw snapshot SHA-256: 906a32f71f3e095afc7cba6a3eddf7a83e4d639d36dc0b3014278b3e0001b032
failed partial SHA-256: 8f1e0973eda48c2ecc54f10001f28b988d353e85a728a16ec24f74582527cbcc
```

裁决：

1. S0、S1、S2 失败事务和 40 文件冻结取证链可以接受；GitHub 不能直接读取 ignored raw，本结论限于提交的确定性脚本、逐文件哈希和运行器语义。
2. S2 维持 `FAILED_STOPPED`，S3 维持未执行，D3R1 科学分类未形成，旧授权终止。
3. `projected_divergence` 是第 6695 接受步唯一直接触发的逐步硬门。`complete_to_registered_end_time` 和 `native_output_times_exact` 为提前停止的派生失败，不是第二个先行数值根因。
4. 当前仍不能裁定超限是孤立尖峰、持续逼近、压力残差耦合、输出对齐效应或其他机制；不得放宽 `1e-4 s^-1` 门或把性能瓶颈写成散度原因。
5. 允许进入 HF3D 性能 P0，但 P0 必须同时完成结果盲散度诊断与生产路径 profiler/基线；P0 后停止交 Pro，正式代码修改和 P1 仍未授权。

## 下一项唯一任务：P0

工作根：

```text
90_候选工作区/current/17_HF3D性能优化/**
```

仅允许修改或新增：

```text
90_候选工作区/current/17_HF3D性能优化/**
90_候选工作区/交接说明_current.md
90_候选工作区/交接说明_history.md
```

只读：

```text
90_候选工作区/current/07_D3跨网格时间步响应诊断/**
01_最终理论/**
02_模拟程序/**
AGENTS.md
```

不得修改 `02_模拟程序/**`，不得实现 P1 accelerated backend，不得运行任何 D3R1 S0/S1/S2/S3。

## P0-D：结果盲散度诊断

允许从冻结 R1 raw 读取：

```text
frame/time vectors
partial 中 diagnostic_step_* 数组
完整归档中 diagnostic_* 数值诊断数组
身份、质量门、计时和加速器元数据
```

禁止读取数值内容：

```text
state_*
observable_*
grid_* arrays
phase / velocity / pressure full fields
任何主要或次要 D3 科学观测量
```

必须输出：

- raw 与 partial 哈希前后不变；
- 六案例接受时间、dt 与散度诊断身份；
- 0.80/0.90/0.95/1.00 倍门的首次、末次和计数；
- `[0,0.005]`、`(0.005,0.009]`、`(0.009,failure]` 三窗；
- G9_DT2 最后 64 步和散度最高 32 步的 time/dt/limiter/retry/pressure residual/iterations/CFL/divergence；
- 原生输出时刻的 G6/G9 三时间层对照；
- 是否为输出对齐步、压力迭代或残差异常、CFL/retry 伴随；
- 实际读取数组白名单和 `physical_observables_read=false`。

只能给出描述性诊断与待检验假设；若必须读取状态或物理观测场，立即停止交 Pro。

## P0-P：性能基线与 profiler

固定短基准：

```text
B1 = G6_DT0, 0.000--0.002 s
B2 = G9_DT0, 0.000--0.002 s
B3 = G9_DT2, 0.000--0.002 s
```

除 duration 外全部配置、时间步、质量门和输出间隔与 D3R1 相同。每项：

```text
1 次不计分 warm-up
3 次独立新进程、未附加 profiler 的计时运行
B2/B3 各至少 1 次独立 profiled 运行
交错顺序
计时前后 GPU 同步
```

端到端计时必须包括启动、构网格/初态、全部步进、硬门、帧记录、归档、严格重载和同步。

必须记录：

```text
wall/CPU time
accepted steps/retries/steps per second
aggregate/per-thread CPU
GPU utilization/clocks/memory/power
H2D/D2H calls and bytes per step
host/device allocation and peak memory
pressure solve time/iterations
archive/reload time
thermal/background/environment identity
```

线程环境变量、affinity 或 GPU clocks 可作为隔离探索变量，但不能单独作为性能通过证据。

## P0 停止点

P0 完成后立即停止并提交：

```text
P0 preregistration
harness and parsers
raw manifests
compact divergence diagnostics
compact profiler/benchmark reports
clean implementation/evidence commit
updated handoff
```

不得在同一轮修改正式程序或开始 P1。

出现 raw/hash 漂移、读取禁止数组、使用 checkpoint/旧轨迹、正式源码变化、benchmark 硬门失败、CUDA 回退、环境污染、需要状态场或需要改变数值合同时，立即停止交 Pro。

## 未来性能候选新增强制门

既有性能裁决继续有效，并新增：

```text
F0: E0 候选必须从零逐位复现 G9_DT2 在第 6695 步、同一时刻、同一 projected_divergence 门和同一失败事务。
B5: 触及压力/步进/GPU 投影路径的候选，P3 必须完成完整 G9_DT2 自零至自然失败的基线/候选端到端配对。
```

若候选改变失败步、散度位模式、压力迭代/规约顺序或使原门通过，则不是 E0，必须按 E1 新后端、至少 D3R2 和新数值资格处理。不得通过放宽散度门、压力容差或改变预条件器/CG 语义来伪装纯性能优化。

## 当前结论上限

```text
D3_V1_INCONCLUSIVE_FAIL_CLOSED_AT_S1_G6_DT0_NATIVE_OUTPUT_TIMES_EXACT_GATE
D3_V1_FAILURE_CAUSE_OUTPUT_SCHEDULE_IMPLEMENTATION_DEFECT
D3_V1_SCIENTIFIC_CLASSIFICATION = NOT_FORMED

D3R1_S2 = FAILED_STOPPED
D3R1_FAILURE_CASE = G9_DT2
D3R1_DIRECT_TRIGGER = PROJECTED_DIVERGENCE
D3R1_FAILURE_MECHANISM = NOT_YET_ESTABLISHED
D3R1_S3 = NOT_EXECUTED
D3R1_SCIENTIFIC_CLASSIFICATION = NOT_FORMED

HF3D_PERFORMANCE_DEFECT = CONFIRMED
HF3D_P0_PERFORMANCE_AND_DIVERGENCE_DIAGNOSTICS = AUTHORIZED
HF3D_P1_IMPLEMENTATION = NOT_AUTHORIZED

NUMERICAL_QUALIFICATION = NOT_DEMONSTRATED
PHYSICAL_VALIDATION = NOT_PERFORMED
EXPERIMENTAL_VALIDATION = NOT_PERFORMED
APPARATUS_PREDICTION = DISALLOWED
```
