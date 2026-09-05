# D3 跨网格时间步响应诊断与 D3R1 修复

状态：

- D3 V1：`D3_V1_INCONCLUSIVE_FAIL_CLOSED_AT_S1_G6_DT0_NATIVE_OUTPUT_TIMES_EXACT_GATE`；
- V1 失败取证：`PASS`，旧运行不得追认、复用或重跑；
- D3R1：仅允许结果盲候选实现、测试和零时间步静态审计；
- D3R1 动态执行：`NOT_AUTHORIZED`，S0、S1、S2、S3 均不得运行。

静态复核状态不缓存于本 README，以以下机器证据中的 `gate_pass` 为准：

```text
artifacts/r1/d3_implementation_static_review.json
```

## 目标与结论边界

D3 检验正式 G0、`6/9` 格和 `0--0.010 s` 短窗内，两个网格对共同时间步减半的响应是否不同，以及这种差分时间步响应是否实质污染了原 Q1R3 的 `6→9` 空间差。

D3R1 只修复候选输出时刻调度身份并隔离新旧证据根，不改变科学问题、物理输入、六算例矩阵、三个时间步、冻结输出时刻、观测量、公式、阈值、分类优先级或结论上限。

D3 和 D3R1 都不是数值资格 campaign，不撤销 Q1R3 空间阶段失败闭锁，不修改正式 HF3D，不证明物理或实验正确，也不预测长期稳态、临界转速或实验装置。

## 冻结裁决与合同

Pro 裁决：

```text
90_候选工作区/current/15_D3S1输出时刻硬门失败独立复核与D3R1重跑裁决.md
```

V1 合同与授权对象保持逐字节不变：

```text
d3_cross_grid_temporal_preregistration.json
d3_execution_authorization.json
```

D3R1 自包含合同与默认拒绝授权对象：

```text
d3_cross_grid_temporal_preregistration_r1.json
d3_r1_execution_authorization.json
```

R1 运行器和独立分析器会机器深比较 Pro 冻结的全部 V1 科学字段。任一字段漂移、旧授权误用、V1 冻结 hash 漂移、实现身份漂移、正式源码漂移或证据根混用都会失败闭锁。

正式源码树 SHA-256：

```text
dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8
```

## V1 失败与取证边界

V1 在 S1 的首个算例 `G6_DT0` 完整推进后触发冻结硬门。原生输出时刻索引 9 为 `0.009000000000000001`，冻结字面值为 `0.009`，两者相差 1 ULP；其他冻结时刻逐位相同。该失败维持有效，未形成 D3 科学分类。

跟踪取证包：

```text
audit_d3_v1_s1_failure.py
artifacts/v1_failure/d3_v1_s1_failure_forensic.json
artifacts/v1_failure/d3_v1_s1_failure_forensic.md
```

取证只读取 `partial.npz` 的成员名和 `frame_time_s`，并核对五个冻结 V1 raw 的 SHA-256、阶段语义和后继输出缺失。V1 raw 永久保留在以下只读位置，只能做 hash 身份审计，不能作为 R1 输入：

```text
artifacts/runs/**
```

## D3R1 结果盲修复

输出目标只来自合同中的冻结 `native_output_times_s` 向量：

```text
registered_times = exact float64 parsing of native_output_times_s
next_index = 1
next_target = registered_times[next_index]
```

运行器只推进至当前索引目标，到达现有对齐容差后把状态时刻设为该冻结向量元素，精确记录一帧，再递增整数索引。重复浮点累加、插值、跳帧和重复帧均被禁止。完成门要求 11 帧的每个 `uint64` 位模式逐项等于冻结向量。

R1 与 V1 使用互不重叠的证据位置：

```text
artifacts/r1/runs/**
artifacts/r1/evidence/**
artifacts/r1/d3_implementation_static_review.json
artifacts/r1/d3_cross_grid_temporal_manifest.json
reports/r1/d3_cross_grid_temporal_report.json
reports/r1/d3_cross_grid_temporal_report.md
```

创建任何 R1 raw 之前，运行器必须重新核对五个冻结 V1 raw hash。R1 只接受新 R1 根内、同一清洁执行提交上从零生成的六份证据。

## 已实现文件

```text
README.md
audit_d3_v1_s1_failure.py
d3_cross_grid_temporal_preregistration.json
d3_execution_authorization.json
d3_cross_grid_temporal_preregistration_r1.json
d3_r1_execution_authorization.json
d3_r1_contract.py
d3_stage_machine.py
run_d3_cross_grid_temporal.py
analyse_d3_cross_grid_temporal.py
tests/test_d3_cross_grid_temporal.py
generated_configs/G6_DT0.json
generated_configs/G6_DT1.json
generated_configs/G6_DT2.json
generated_configs/G9_DT0.json
generated_configs/G9_DT1.json
generated_configs/G9_DT2.json
```

运行器直接导入 `02_模拟程序/src/airvortex/**` 的正式 HF3D、时间步控制器、记录器和归档器，没有复制或修改正式求解器。独立分析器不导入候选运行器，而是重新读取合同、正式归档与来源身份，独立重建配置、硬门、算术、标签和发布事务。

## 六算例设计

```text
G6_DT0  5.5e-6 s
G6_DT1  2.75e-6 s
G6_DT2  1.375e-6 s

G9_DT0  5.5e-6 s
G9_DT1  2.75e-6 s
G9_DT2  1.375e-6 s
```

六个算例及其科学字段与 V1 逐值相同。未来只有在新的 Pro 精确快照授权后，才可在同一清洁执行提交上全部从零生成。D2 轨迹和 V1 raw 都不能进入 R1 分类输入。

## 当前允许的静态命令

以下命令不推进真实 HF3D 时间步。GPU 静态审计必须使用项目既有的隔离 GPU 解释器，不能以缺少 CuPy 的系统解释器代替。

```bash
D3_GPU_PYTHON="90_候选工作区/current/01_HF3D数值资格/.venv-gpu/bin/python"

"$D3_GPU_PYTHON" \
  "90_候选工作区/current/07_D3跨网格时间步响应诊断/run_d3_cross_grid_temporal.py" \
  generate-configs

"$D3_GPU_PYTHON" -Werror -m unittest discover \
  -s "90_候选工作区/current/07_D3跨网格时间步响应诊断/tests" \
  -p "test_d3_cross_grid_temporal.py" -v

"$D3_GPU_PYTHON" \
  "90_候选工作区/current/07_D3跨网格时间步响应诊断/run_d3_cross_grid_temporal.py" \
  static-audit --write --require-clean
```

零时间步静态审计包括：

1. V1 forensic bundle 与五个冻结 raw hash；
2. V1/R1 科学字段逐值身份；
3. R1 默认拒绝授权和旧 V1 授权失效；
4. 六配置 allowlist 差异、正式 G0 网格、掩膜和初态身份；
5. 冻结输出向量的位模式、索引调度规则和六配置共同时间身份；
6. R1/V1 证据根隔离及 R1 动态输出缺失；
7. 正式源码树、Git 实现快照、实际 CUDA FP64 探测；
8. 全部 D3 候选测试和 S0--S3 状态机、失败事务、发布事务回归；
9. `time_steps_advanced_by_this_audit = 0`。

## 动态命令的机器闭锁

`prepare-execution`、`run-stage` 和独立分析器的 `final` 入口都已切换到 R1 合同、R1 授权对象和 R1 证据根。当前默认拒绝授权会在创建 R1 raw 或推进时间步前无副作用拒绝这些入口。

未来授权只有在 Pro 复核唯一清洁 `main` 实现快照后才可能成立。授权提交必须是该实现提交的唯一直接子提交，并且只能修改 `d3_r1_execution_authorization.json`；授权对象必须精确绑定预注册 blob、正式源码树、全部候选实现文件和裁决文档。任何漂移都会重新闭锁执行。

若未来获得精确授权，阶段仍只能按 `S0 → S1 → S2 → S3` 顺序执行。任一身份门、质量门、阶段门或事务门失败都立即停止后继阶段；完成 S3 后也必须停止等待新的 Pro 复核，不能自动进入更小时间步、更细网格、长期窗口、D4、Q1R4 或正式代码修改。

## 独立分析边界

未来 S3 只接受 R1 raw 根中的六份规范归档，并独立重新计算 `q`、`s`、`u`、`c`、`eta`、`rho`、`gamma`、余弦和观测阶。它拒绝 D2、V1、隔离目录、checkpoint、符号链接和跨提交轨迹，并要求逐算例摘要的归档载荷文件集、大小和 SHA-256 与实际归档精确一致。

所有窗口先在完整轨迹计算：

$$
q_{L,k}(t)=Q_{L,k}(t)-Q_{L,k}(0),
$$

再切取 `0--0.005 s` 或 `0.005--0.010 s` 的原生输出样本。诊断分窗不能改变完整窗口顶层标签；余弦零范数保持未定义；二值事件只记录，不作为 PASS 门。

## 当前禁止与停止点

- 不得运行 D3R1 S0 或任何真实 CUDA 动态算例；
- 不得签发允许态授权或创建声称 S1、S2、S3 已执行的证据；
- 不得修改、移动、覆盖或复用 V1 raw；
- 不得放宽科学阈值、质量阈值、分类优先级、CUDA 要求或来源隔离；
- 不得修改 `01_最终理论/**`、`02_模拟程序/**` 或根目录治理文件；
- 不得自动进入后继科学阶段。

D3R1 完成实现和零时间步静态证据后，必须停止并交 Pro 独立复核。新的详细裁决到达前，动态执行继续保持未授权。
