# 当前交接

当前状态：

```text
HF3D_P0P_V1_AUTHORIZATION = HISTORICAL_CONSUMED_AND_NO_LONGER_CURRENT_HEAD
HF3D_P0P_V1_CLAIM = CONSUMED
HF3D_P0P_V1_SCHEDULE = TERMINAL_FAILED_STOPPED_NO_RESUME
HF3D_P0P_V1_WARMUP_B1 = FAILED_STOPPED__SEQUENCE_0__START_COUNT_1
HF3D_P0P_V1_REMAINING_17 = PERMANENTLY_NOT_STARTED
HF3D_P0P_V1_HF3D_TIME_STEPS = 0
HF3D_P0P_V1_FAILURE_CLASS = PRE_NUMERICAL_PROTOCOL_CONFIGURATION_INVALID
HF3D_P0P_V1_MEASURED_PERFORMANCE_RESULT = NOT_AVAILABLE

HF3D_P0P2_EVIDENCE_REGISTRATION_AND_ZERO_STEP_IMPLEMENTATION = AUTHORIZED
HF3D_P0P2_DYNAMIC_EXECUTION = NOT_AUTHORIZED
HF3D_P1 = PROHIBITED

D3R1_S2 = FAILED_STOPPED
D3R1_SCIENTIFIC_CLASSIFICATION = NOT_FORMED
```

## 最新 Pro 裁决

```text
90_候选工作区/current/23_P0P一次性执行失败独立复核与P0P2协议裁决.md
```

## V1 已消费终态

执行身份：

```text
execution HEAD = origin/main at execution time
               = 18cd1984fea41b0b1983136e28c4d5a453795e32
preflight = ALL GATES PASS
```

证据锚点：

```text
claim SHA-256
= ecd7cf0e9dfa4e3d609f22e205c95261cc1505a255776dc56a29b3532ad94d80

schedule ledger SHA-256
= 089d91578f89d083f3552f4dfdfaac464bc32842a4f7945f13e9e00903817948

execution failure SHA-256
= 749ecd452c3958e6320de4e39fe9ea04d2cf4aadd8e4488f105a1cbdc5ab5262

sample failure SHA-256
= 81145152ab792a763596cbdc4859040fee47c31cc0546f033d7805cdfa2cee1b

worker stderr SHA-256
= 944c1cdd60c47f9315d20453e27a541c07f6cf98c5f8419d2b6d79d28d21e894
```

Ledger：

```text
WARMUP_B1:
  sequence_index = 0
  start_count = 1
  state = FAILED_STOPPED

other 17 entries = NOT_STARTED
completed_samples = []
```

失败：

```text
ConfigError: drive ramp duration cannot exceed run duration
run.duration_s = 0.002
drive.ramp_duration_s = 0.005
partial_evidence = null
worker stdout = empty
HF3D accepted steps = 0
```

这是一项三 benchmark 共有的、claim 前本可静态判定的候选协议配置错误，不是 HF3D 数值、物理、CUDA、压力、环境或性能结果。正式配置校验正确，禁止放宽。

## V1 永久禁止

```text
不得恢复、重跑、补样、替换、重排或复制 completed prefix
不得修改或删除原 performance_raw
不得改写 claim、ledger、execution_failure 或 WARMUP_B1 失败目录
不得把 WARMUP_B1 重解释为 NOT_STARTED
不得运行剩余 17 项
不得把 V1 raw/state/trajectory/archive 用作 P0-P2 输入
```

本裁决提交后，当前 `HEAD` 不再是旧授权唯一子提交，V1 授权同时失去 current-HEAD 拓扑资格；旧 claim 仍作为历史消费事实永久成立。

## 下一项唯一任务

在本地先 `git pull --ff-only`，不得触碰旧 raw。只允许新建：

```text
90_候选工作区/current/24_P0P失败证据登记与P0P2零步实现/
```

按两个提交阶段执行。

### E0：跟踪登记 V1 失败证据

- 对旧 ignored `performance_raw/` 做复制前、复制后只读 canonical 快照；
- 把完整常规文件树逐字节复制到新目录的跟踪 evidence snapshot；
- 记录 path/mode/size/SHA-256/canonical tree SHA-256；
- 核对本交接列出的五个 hash、ledger 终态、ConfigError、空 stdout、null partial 和 completion 缺失；
- 原 raw 任何字节发生变化或任一核验不一致，立即失败停止。

禁止移动或强制跟踪原运行目录。

### I2：实现 P0-P2 零步协议

新的 P0-P2 必须与 V1 使用不同的：

```text
contract ID
schedule ID/hash
authorization ID
ticket/grant/nonce
raw root
claim/ledger
sample IDs
effective-config hashes
```

冻结有效短窗：

```text
run.duration_s = 0.005
drive.ramp_duration_s = 0.005  # 不修改
output_interval_s = 0.001
registered times = [0, 0.001, 0.002, 0.003, 0.004, 0.005]
only allowed source-config change = run.duration_s
```

Claim 前必须对 B1/B2/B3 全部执行正式：

```text
SimulationConfig.from_dict()
exact to_dict round-trip
canonical effective-config hash binding
exact-case fresh-process zero-step runtime construction
actual CUDA/no fallback
grid + pressure + initial state + zero-step diagnostics
hf3d_time_steps_advanced = 0
```

新 preflight 的顶层 `gate_pass` 必须合并配置、zero-step、raw、授权、来源和环境全部门；任一失败都必须在 lock/raw/claim/ledger/sample 产生前停止。

完成 E0、I2、测试、静态证据和 handoff 后提交、推送并停止交 Pro。**当前不授权 P0-P2 动态执行或父票据签发。**

## 允许修改

```text
90_候选工作区/current/24_P0P失败证据登记与P0P2零步实现/**
90_候选工作区/交接说明_current.md
90_候选工作区/交接说明_history.md
```

其余路径只读，尤其：

```text
90_候选工作区/current/17_HF3D性能优化/**
01_最终理论/**
02_模拟程序/**
90_候选工作区/current/07_D3跨网格时间步响应诊断/**
AGENTS.md
```

## 结论上限

```text
P0-P V1 measured result = NOT AVAILABLE
P0-P2 dynamic execution = NOT AUTHORIZED
P1 = PROHIBITED
numerical qualification = NOT DEMONSTRATED
physical validation = NOT PERFORMED
experimental validation = NOT PERFORMED
apparatus prediction = DISALLOWED
```
