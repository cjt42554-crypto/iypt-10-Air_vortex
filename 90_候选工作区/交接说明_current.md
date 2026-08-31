# 当前交接

当前状态：

```text
D3R1_CURRENT_S2_G9_DT2_CONTINUE__DO_NOT_INTERRUPT_FOR_PERFORMANCE
HF3D_SINGLE_CORE_HOST_BOTTLENECK_CONFIRMED
CURRENT_D3R1_AUTHORIZATION_VALID_ONLY_FOR_ALREADY_RUNNING_LOCAL_SEQUENCE
NO_NEW_DYNAMIC_EXECUTION_AUTHORIZED_ON_UPDATED_REMOTE_MAIN
HF3D_PERFORMANCE_OPTIMIZATION_MANDATORY_AFTER_CURRENT_D3R1_EVIDENCE_FREEZE
```

## 当前唯一在途任务

用户机器上的 D3R1 仍在旧授权本地 HEAD：

```text
be349aec2f1bdcdb232e282a11175921eda7976b
```

最近用户确认 S2 正在执行 `G9_DT2`，模拟进度约 `0.006 s / 0.010 s`。本裁决提交到远端不会改变该本地进程的 HEAD。

详细裁决：

```text
90_候选工作区/current/17_D3R1运行中性能裁决与HF3D优化门.md
```

## 立即执行规则

**不要为了性能主动中断当前 G9_DT2。**

现有实测确认程序存在稳定的主机串行瓶颈：16 核/32 线程机器上进程约占一个逻辑 CPU，GPU 多数样本约 16%--22%；正式热循环又调用每步全场主机/设备传输的压力 host wrapper。因此性能优化是后续强制任务。

但当前已经完成 G6 三算例及 G9_DT0/G9_DT1，最后一个 G9_DT2 已约完成 60%。修改实现或正式源码后，R1 合同禁止复用任何旧算例，必须六个全新重跑。按 G9_DT0/DT1 已测墙钟推算，当前剩余约 1.74 h；仅重跑 G9 三算例就需要优化实现实测超过约 4.39x 才可能在不计 G6、开发、验证和再授权的情况下更早完成。目前没有达到该门的优化候选或端到端基准。

同时，当前 runner 只捕获 `Exception`，外部 Ctrl+C 产生的 `KeyboardInterrupt` 不保证进入 partial/ledger 事务。主动中断存在额外证据风险。

因此当前本地进程继续自然运行。

## 当前进程结束前禁止

```text
git pull / checkout / reset / commit
修改工作树或授权对象
改变线程环境变量、CPU affinity、GPU power 或 Python/CuPy 环境
启动第二个 D3R1/HF3D 写入进程
附加会改变进程状态的 profiler
删除、移动、覆盖 V1 或 R1 raw
```

允许只读 `pidstat`、`ps`、`nvidia-smi dmon` 和日志尾部采样。

## S2/S3 分支

G9_DT2/S2 命令退出后：

### S2 成功

只有同时满足以下条件才运行当前授权 S3：

```text
exit code = 0
S2 = EXECUTED / PASS
G9_DT0, G9_DT1, G9_DT2 summaries and archives all PASS
execution_failure.json absent
HEAD/worktree/source/authorization unchanged
```

随后在同一旧本地 HEAD 上显式运行 S3。S3 完成并停止后，先只读验证全部证据，再拉取远端新 `main`，更新交接并提交跟踪报告、manifest 和 compact evidence，然后立即交 Pro 做结果复核。

### S2 或 S3 失败

任一非零退出、异常、硬门失败、CUDA 回退、身份漂移、未知输出或失败标记出现，立即停止；不得重跑、修补、拼接或继续后续阶段。原样保留证据并更新交接。

## 远端授权边界

本裁决提交后，远端 `main` 已不再是 `d3_r1_execution_authorization.json` 唯一子提交，因此**不得在远端新 main 上重新启动 D3R1**。当前许可只覆盖已经在本地旧 HEAD 上运行的同一序列。

## 不可避免外部中断

只有硬件安全、系统稳定性或用户新的明确停止指令才能覆盖“不主动中断”裁决。若必须终止，应把事件登记为：

```text
D3R1_EXTERNAL_INTERRUPTION_DURING_S2_G9_DT2
NOT_A_NORMAL_RUNNER_FAILURE_TRANSACTION
NOT_ADMISSIBLE_FOR_CLASSIFICATION
```

中断前捕获 HEAD、工作树、源码树、授权、PID/命令、日志、最后确认时刻和 raw 树；按 SIGINT → 等待 → SIGTERM 的顺序处理，SIGKILL 仅限安全。退出后不得伪造旧 stage ledger 或 case summary；在新的性能候选目录中创建独立 interruption forensic manifest，并禁止后续新协议读取这些轨迹作为分类输入。

## 当前 D3R1 冻结后的下一项任务

当前 D3R1 无论成功或自然失败，完成证据冻结与 Pro 结果复核后，下一项工程任务固定为：

```text
90_候选工作区/current/17_HF3D性能优化/**
```

必须按裁决完成：

1. P0 生产等价 profiler 与端到端基线；
2. P1 结果盲性能候选；
3. P2 E0 严格逐位等价，或明确转为 E1 新后端身份；
4. P3 B1--B4 成对端到端基准；
5. 达到 `geometric mean >= 1.75x`、每个短基准 `>= 1.50x`、完整 G9 `>= 1.75x`；
6. 证明已消除稳定单核/GPU 饥饿模式；
7. 条件性正式提升和新的 Pro 精确快照授权。

线程环境变量、核心数或利用率本身不是速度证据。必须包含 GPU 同步、完整求解、诊断、归档和严格重载的墙钟基准。

## 条件性允许的正式修改范围

只有基线 profiler 已提交后，允许按裁决修改：

```text
02_模拟程序/src/airvortex/_hardware.py
02_模拟程序/src/airvortex/backends/high_fidelity_3d.py
02_模拟程序/src/airvortex/hf3d/pressure.py
02_模拟程序/src/airvortex/hf3d/stepper.py
02_模拟程序/src/airvortex/hf3d/recording.py
02_模拟程序/src/airvortex/hf3d/types.py
02_模拟程序/src/airvortex/hf3d/timestep.py
02_模拟程序/src/airvortex/numerics/projection3d_gpu.py
02_模拟程序/src/airvortex/hf3d/accelerated/**
02_模拟程序/src/airvortex/numerics/accelerated/**
相关正式测试
pyproject.toml 中仅限可选性能依赖
```

CPU 参考 `projection3d.py`、`cartesian3d.py`、`interface3d.py`、`momentum3d.py` 默认保持只读；确需修改必须先提交热点证据并停交 Pro 追加裁决。

禁止改变物理方程、精度、网格、时间步、输出、质量门、CG/预条件语义、诊断或分类合同。

## 当前结论上限

```text
D3_V1_INCONCLUSIVE_FAIL_CLOSED_AT_S1_G6_DT0_NATIVE_OUTPUT_TIMES_EXACT_GATE
D3_V1_FAILURE_CAUSE_OUTPUT_SCHEDULE_IMPLEMENTATION_DEFECT
D3_V1_SCIENTIFIC_CLASSIFICATION = NOT_FORMED

D3R1_CURRENT_S2_G9_DT2_CONTINUE_UNDER_EXISTING_LOCAL_AUTHORIZATION
HF3D_PERFORMANCE_DEFECT_CONFIRMED__OPTIMIZATION_REQUIRED_AFTER_FREEZE

NUMERICAL_QUALIFICATION = NOT_DEMONSTRATED
PHYSICAL_VALIDATION = NOT_PERFORMED
EXPERIMENTAL_VALIDATION = NOT_PERFORMED
APPARATUS_PREDICTION = DISALLOWED
```

本轮没有修改正式理论、正式程序、D3R1 合同或授权对象，没有运行新数值算例或实验。交接已更新。
