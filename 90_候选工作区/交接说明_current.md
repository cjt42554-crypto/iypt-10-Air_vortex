# 当前交接

当前状态：

```text
HF3D_P0P_V1_CLAIM = CONSUMED
HF3D_P0P_V1_SCHEDULE = TERMINAL_FAILED_STOPPED_NO_RESUME
HF3D_P0P_V1_MEASURED_PERFORMANCE_RESULT = NOT_AVAILABLE

HF3D_P0P_V1_E0_FAILURE_EVIDENCE_REGISTRATION = ACCEPTED
HF3D_P0P2_I2_EFFECTIVE_CONFIG_AND_ZERO_STEP_QUALIFICATION = ACCEPTED
HF3D_P0P2_I2_RUNTIME_GATE = FALSE_DEFAULT_DENIAL
HF3D_P0P2_DYNAMIC_EXECUTABILITY = NOT_ESTABLISHED
HF3D_P0P2_PARENT_TICKET = NOT_ISSUED
HF3D_P0P2_DYNAMIC_EXECUTION = NOT_AUTHORIZED
HF3D_P0P2_V2_DYNAMIC_BASELINE_IMPLEMENTATION = AUTHORIZED
HF3D_P1 = PROHIBITED

PROJECT_REFACTOR = TARGETED_CROSS_MODULE_REFACTOR_AFTER_BASELINE
PROJECT_WIDE_REWRITE = REJECTED
HF3D_HARDWARE_UTILIZATION = INSUFFICIENT
PRIMARY_ACCELERATION_ROUTE = EXPLICIT_GPU_RESIDENT_E1_BACKEND_AFTER_P0P2
REFERENCE_BACKEND = RETAINED

D3R1_S2 = FAILED_STOPPED
D3R1_SCIENTIFIC_CLASSIFICATION = NOT_FORMED
```

## 最新 Pro 裁决

```text
90_候选工作区/current/25_P0P2零步终审与动态基线实现门裁决.md
```

## 已接受内容

### E0

V1 失败证据登记接受：

```text
E0 commit = 379ae8dfb425c2f57e25d2779525fc843592fc98
regular files = 15
total bytes = 52509
source/snapshot canonical tree SHA-256
= 21ef41a6acc791c01da67b751cbaeaabfdaa94ee1d3a860948f0700b7dd4e541
```

原 ignored raw 在复制前后保持不变；跟踪 snapshot 与原目录 path/Git mode/size/SHA-256/canonical tree 一致。Claim consumed、WARMUP_B1 `FAILED_STOPPED/start_count=1`、剩余 17 项 `NOT_STARTED`、`0.002/0.005` 配置冲突、精确 ConfigError、空 stdout、null partial 和无 completion 均已独立登记。

E0 只能作为失败证据和负回归，禁止作为新运行输入或完成前缀。

### I2 零步资格

三份配置已正式通过：

```text
run.duration_s = 0.005
drive.ramp_duration_s = 0.005
output_interval_s = 0.001
registered times = [0, 0.001, 0.002, 0.003, 0.004, 0.005]
only source change = run.duration_s
```

```text
B1 effective canonical SHA-256
= e694ea2b4c3b1a686e066cb7f6a919367e7bd1a340f0738964ce4e157389f00b
B2
= d51aed9bb2e90af300d980b00324214cf991aa483c15f4a46a8b2b669d8731c4
B3
= 2c3ac702a5e85762eecf2f15bb908f538c697058604d1e5edddad051564618e3
```

B1/B2/B3 均在独立 fresh process 中完成正式配置 round-trip、实际 CUDA pressure、网格、初态、零步诊断、初始帧和 CuPy pool teardown，HF3D 时间步为 0。统一 preflight 的 static qualification PASS；exact denial 使唯一 runtime gate 保持 false。

## 为什么没有签发 P2/C2

当前目录只有零步合同、配置、授权审计轮廓、环境门、zero-step probe 和 static audit。尚不存在：

```text
dynamic campaign orchestrator
production-equivalent one-sample worker
claim/ledger/worker-token runtime
sample success/failure transaction
external telemetry monitors
cProfile / semantic attribution
nsys launch/export/audit
host/device allocation observers
cross-mode canonical numerical equivalence
archive/reload dynamic evidence audit
final analyser/report/manifest transaction
```

因此当前 ticket 最多只能绑定零步资格代码，不能证明 18 项动态日程如何执行、测量、失败闭锁或发布。P2 ticket 和 C2 grant 均未签发，任何动态执行继续禁止。

## 下一项唯一任务：P0-P2 V2 完整动态基线实现

继续只在：

```text
90_候选工作区/current/24_P0P失败证据登记与P0P2零步实现/**
```

实现完整的生产等价动态 baseline，但保持 default denial，且不得启动样本。

为避免把当前零步轮廓误作执行合同，新版本身份冻结为：

```text
contract ID = HF3D_P0P2_PRODUCTION_EQUIVALENT_PERFORMANCE_BASELINE_V2
schedule ID = HF3D_P0P2_FIXED_18_V2
authorization ID = HF3D_P0P2_DYNAMIC_EXECUTION_AUTHORIZATION_V2
claim namespace = HF3D_P0P2_SINGLE_SCHEDULE_CLAIM_V2
ledger schema = HF3D_P0P2_FIXED_18_LEDGER_V2
sample prefix = P0P2V2_
```

短窗和三份 source config 不变：

```text
run.duration_s = 0.005
registered times = [0, 0.001, 0.002, 0.003, 0.004, 0.005]
```

### 必须实现的动态链

```text
single-writer orchestrator
fresh-process worker
non-consuming preflight
O_EXCL single claim
fixed 18-entry no-resume ledger
one-time worker launch token
staging + atomic no-replace sample transaction
terminal execution failure/complete
external monitors
HOST / CUDA / MEMORY profile modes
canonical numerical payload equality
independent analyser and final publication transaction
```

固定逻辑日程：

```text
3 warm-up
9 scored timed（B1/B2/B3 各三次，原交错顺序）
B2 HOST/CUDA/MEMORY
B3 HOST/CUDA/MEMORY
```

所有样本使用新的 `P0P2V2_` ID。不得提供 skip/repeat/only/start-at/resume 或补样入口。

### 端到端计时边界

TIMED 墙钟必须覆盖：

```text
process launch/import/thread policy
identity/config gates
grid/pressure/initial state
all accepted/rejected trials and hard gates
registered frames
result assembly
archive write
strict reload
canonical payload extraction
final CUDA sync
normal process exit
```

Scored 模式禁止 cProfile、nsys、allocator interposer 和语义包装，只允许外部只读 telemetry。

### 数值门

冻结 D3R1 的适用硬门，并记录：

```text
accepted/rejected sequence
dt / limiter / retry
pressure residual / iterations
projected divergence
CFL
volume balance
clip / repair
native output times exact
archive state/diagnostic/observable arrays
```

同一 benchmark 的 TIMED reference、HOST、CUDA、MEMORY 必须在排除时间和 profiler 元数据后逐位相同，canonical numerical payload SHA-256 必须一致。

### HOST/CUDA/MEMORY

HOST：cProfile + transparent operator ranges + pidstat/nvidia-smi，要求 top-level coverage >=98%、solver named exclusive >=90%、unknown total <=10%、无单个 unknown >=5%。

CUDA：只使用已验证的：

```text
nsys 2026.2.1
--trace=cuda,nvtx
--sample=none
--cpuctxsw=none
```

要求 `.nsys-rep`、SQLite、四份 stats、PID/context、NVTX 首尾、kernel/API/memcpy、H2D/D2H 方向/次数/字节/时间完整交叉。未经新能力复核禁止 `osrt`、`--cuda-memory-usage`、sampling、backtrace、GPU metrics 和 Unified Memory tracing。

MEMORY：validated native allocator observer、CuPy allocation recorder、pool high-water、CUDA allocation API、RSS/VmHWM 和显存时间线。

### 授权拓扑补丁

未来审计必须增加：

```text
P2 exactly one parent
symbolic HEAD = refs/heads/main
git fetch --no-tags origin main before audit
NUL-safe raw diff with status/mode/old-new blob
regular 100644 ticket/ruling/current/history
complete synthetic Git positive/negative tests
```

负例必须覆盖 merge P2/C2、intermediate parent、stale origin、wrong branch、extra paths、rename/copy/mode/symlink/submodule、wrong blobs、missing/drifted reviewed file 和中文路径。

### 工具链和环境

Claim 前与 worker 内绑定：

```text
.venv-gpu executable
Python/NumPy/SciPy/CuPy exact versions
nsys version
GPU name + UUID + compute capability
CUDA driver/runtime
VRAM
CPU model / 32 logical CPUs
OS/kernel
affinity/governor/AC
formal selected BLAS threads = 2
OMP_DYNAMIC = FALSE
library thread variables exact
```

Preflight 顺序：

```text
initial instantaneous environment gate
→ B1/B2/B3 fresh-process zero-step probes
→ full 30 s post-probe environment gate
```

环境不合法时不得先做大规模 device allocation。

## 零步返回 Pro 前的验收条件

必须实际通过并提交：

```text
P0-P2 V2专项测试
Git topology synthetic tests
transaction/failure-injection tests
configuration/hash/namespace negative tests
profile separation and coverage tests
nsys/memcpy/parser negative tests
allocator probes
cross-mode payload mismatch tests
strict JSON and deterministic regeneration
Ruff / mypy / compile
```

最终静态审计必须明确：

```text
HF3D dynamic time_steps = 0
warmup/timed/profile started = 0
P0-P2 V2 claims consumed = 0
new raw/lock/claim/ledger/sample = absent
authorization = exact NOT_AUTHORIZED
P1 = NOT_IMPLEMENTED
formal source unchanged
```

完成后立即提交、推送并停止交 Pro。不得自行生成 P2 ticket、C2 grant 或运行任何样本。

## 允许修改

```text
90_候选工作区/current/24_P0P失败证据登记与P0P2零步实现/**
90_候选工作区/交接说明_current.md
90_候选工作区/交接说明_history.md
```

可以只读参考旧 V1 harness，但新 runtime 必须自包含，执行时不得 import 旧 V1 candidate modules，也不得读取 V1 raw/E0 snapshot 作为数值输入。

## 禁止修改或执行

```text
01_最终理论/**
02_模拟程序/**
90_候选工作区/current/07_D3跨网格时间步响应诊断/**
90_候选工作区/current/17_HF3D性能优化/**
90_候选工作区/current/23_P0P一次性执行失败独立复核与P0P2协议裁决.md
AGENTS.md
V1 raw/claim/ledger/failure
E0 snapshot bytes
```

```text
P0-P2 dynamic samples = PROHIBITED
P2/C2 issuance = PROHIBITED
P1/formal optimization = PROHIBITED
threshold relaxation = PROHIBITED
```

## 技术债与性能路线

全项目继续采用局部跨模块重构，不做整体重写。当前先完成 P0-P2 baseline；随后优先建立显式 GPU-resident E1 backend，保留 reference backend，再处理 recording/archive device boundary 与未来 campaign shared evidence core。

既有真实证据仍表明 CPU/GPU 利用不足：单主线程约满载、总进程约一个逻辑核、GPU 多数约 16%--22%、pressure 每步存在 host-device transfer。零步构造 PASS 不是性能结果。

正式 E1 提升仍需结果盲数值等价合同、真实 CPU/GPU 利用率和端到端速度门；利用率和线程变量均不能替代墙钟。

## 结论上限

```text
E0 failure evidence = ACCEPTED
P0-P2 zero-step qualification = ACCEPTED
P0-P2 dynamic harness = NOT YET COMPLETE
P0-P2 dynamic execution = NOT AUTHORIZED
P1 = PROHIBITED

numerical qualification = NOT DEMONSTRATED
physical validation = NOT PERFORMED
experimental validation = NOT PERFORMED
apparatus prediction = DISALLOWED
```
