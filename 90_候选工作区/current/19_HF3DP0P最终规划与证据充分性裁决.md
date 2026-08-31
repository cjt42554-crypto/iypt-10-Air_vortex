# HF3D P0-P 最终规划与证据充分性裁决

日期：2026-08-31（Asia/Tokyo）

裁决状态：

```text
HF3D_P0_D_COMPLETED_AND_RETAINED
HF3D_P0_P_FINAL_EVIDENCE_PLAN_FROZEN
HF3D_P0_P_ZERO_STEP_HARNESS_IMPLEMENTATION_AUTHORIZED
HF3D_P0_P_DYNAMIC_SAMPLES_NOT_AUTHORIZED_PENDING_PRO_STATIC_REVIEW
HF3D_P1_IMPLEMENTATION_AND_FORMAL_SOURCE_MODIFICATION_NOT_AUTHORIZED
```

## 1. 绑定对象与本轮边界

本裁决绑定：

```text
repository: cjt42554-crypto/iypt-10-Air_vortex
reviewed main: e1e72f95548b9456d94cb91d7dc64a6b925dd2b8
P0 preregistration Git blob: 62f256574f67654d13f588e0505f63505d065907
P0-D evidence commit: e1e72f95548b9456d94cb91d7dc64a6b925dd2b8
formal source tree SHA-256: dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8
D3R1 raw canonical SHA-256: 906a32f71f3e095afc7cba6a3eddf7a83e4d639d36dc0b3014278b3e0001b032
```

当前 P0-D 已提交并保持结果盲边界；本裁决不修改其数值、解释上限或访问白名单。正式源码相对 P0 授权提交未变化。

用户已明确提供并要求纳入的本机能力事实：

```text
GPU Python: project .venv-gpu
Python 3.12.3
NumPy 2.5.2
SciPy 1.17.1
CuPy 14.2.0
GPU: NVIDIA GeForce RTX 5060
available: nsys 2026.2.1, pidstat, nvidia-smi, turbostat
CPU sampling / perf native profiling: unavailable
py-spy: unavailable
```

独立最小 CuPy 探针已经实际证明以下 Nsight Systems 配置可用：

```text
--trace=cuda,nvtx --sample=none --cpuctxsw=none
```

它能够正常生成 `.nsys-rep` 与 SQLite，并导出：

```text
cuda_api_sum
cuda_gpu_kern_sum
cuda_gpu_mem_time_sum
cuda_gpu_mem_size_sum
```

已实测可见 kernel 名称、实例数和时长，CUDA API（包括 `cudaMemcpyAsync`、`cudaMalloc` 等），以及 H2D/D2H 的次数、字节和时间。加入 `osrt` 或 `--cuda-memory-usage` 的更重合成配置曾在目标退出后的收尾阶段异常，因此本裁决禁止在正式 P0-P 中未经另一次独立能力验证启用这些选项。

当前环境仍不满足开跑门：存在仓库外计算进程，CPU Tctl 约 96 °C，高于冻结的 90 °C 上限。本轮只授权规划和零时间步实现；不得启动 warm-up、timed 或 profiled HF3D 样本。

## 2. 总体裁决

P0-P 的证据目标可以在不修改 `02_模拟程序/**`、不使用 `perf`/`py-spy`、不启用重型 Nsight 选项的条件下完成，但不能依赖单一工具。

充分证据必须由以下互补链条共同组成：

```text
A. 未插桩的 scored 端到端基线
B. cProfile + 候选侧透明语义包装的主机热点证据
C. nsys 轻量 CUDA/NVTX trace 的 kernel/API/memcpy 时间线
D. pidstat 的进程/线程 CPU 与调度时间线
E. nvidia-smi/turbostat 的 GPU/热/频率/功耗/内存环境时间线
F. 经合成探针验证的主机原生分配观察器
G. 经合成探针验证的 CuPy/设备分配和内存高水位观察器
H. 所有模式之间的数值与证据身份交叉门
```

任何一个工具都不能单独完成 P0-P。特别是：

```text
线程环境变量 ≠ 性能证据
GPU utilization ≠ CUDA 时间线
cProfile ≠ 原生符号采样
pidstat ≠ 热点归因
cuda_api_sum ≠ 分配字节或峰值内存
RSS/显存采样 ≠ 分配计数
```

## 3. 冻结执行结构

P0 预注册中的 scored 顺序保持不变：

```text
WARMUP_B1
WARMUP_B2
WARMUP_B3
TIMED_R1_B1
TIMED_R1_B2
TIMED_R1_B3
TIMED_R2_B2
TIMED_R2_B3
TIMED_R2_B1
TIMED_R3_B3
TIMED_R3_B1
TIMED_R3_B2
PROFILED_B2
PROFILED_B3
```

其中 `PROFILED_B2` 与 `PROFILED_B3` 在本裁决中冻结为三个独立新进程组成的 bundle，顺序不可改变：

```text
PROFILED_B2_HOST
PROFILED_B2_CUDA
PROFILED_B2_MEMORY
PROFILED_B3_HOST
PROFILED_B3_CUDA
PROFILED_B3_MEMORY
```

这些 profile 子运行全部不计入 scored 时间，不得替代、补齐或重算任一 timed 样本。

### 3.1 Scored 样本

Warm-up 和 timed 样本必须：

- 使用未修改的正式 HF3D 生产路径；
- 不启用 cProfile、nsys、原生分配 interposer、CuPy 分配 recorder 或 NVTX 语义包装；
- 只允许外部只读 `pidstat`、`nvidia-smi` 和温度/环境监测；
- 端到端墙钟覆盖进程创建、导入、配置、构网格、初态、全部步进与硬门、帧记录、归档、严格重载和最终 GPU 同步；
- 每个样本使用全新进程和全新目录；
- 任一失败即停止剩余计划，不重试、不替换、不重排。

### 3.2 Profile bundle

`HOST`、`CUDA`、`MEMORY` 三类 profile 必须使用同一 benchmark 配置、同一正式源码、同一线程策略和全新初态，但相互独立，避免一种 profiler 的开销污染另一种证据：

- `HOST`：cProfile、透明语义包装、候选计时区间、pidstat、nvidia-smi；
- `CUDA`：已实测的轻量 nsys CUDA/NVTX trace、透明 NVTX 包装、pidstat、nvidia-smi；不得同时启用 cProfile；
- `MEMORY`：主机原生分配观察器、CuPy/设备分配观察器、`/proc`/ru_maxrss、nvidia-smi；不得同时启用 cProfile 或 nsys。

如任何 profile 子运行改变数值身份、硬门、接受/拒绝序列或归档载荷，则整个 P0-P 失败闭锁。

## 4. 候选侧包装与插桩边界

允许在：

```text
90_候选工作区/current/17_HF3D性能优化/**
```

实现进程内包装、监测、解析器和原生观察器。禁止修改正式 Python 文件或把补丁写入 site-packages。

### 4.1 透明包装硬门

每个包装器必须：

1. 绑定并记录原对象的 module、qualname、源文件和 Git/source identity；
2. 原参数对象原样传入，原函数恰调用一次；
3. 返回同一个结果对象或保持返回结构逐对象身份；
4. 异常类型、异常消息和 traceback 语义不变，包装器只在 `finally` 中关闭计时/NVTX 范围；
5. 不复制、转换、读取或修改数值数组；只可读取 shape、dtype、nbytes、对象 ID、调用次数和已存在的诊断标量；
6. 不缓存结果，不改变线程、affinity、环境、随机性、CUDA stream 或 allocator；
7. 不在算子包装器内部调用 GPU synchronize；同步只能发生在预注册的端到端边界；
8. 不吞掉或延迟任何质量门、事务或失败。

### 4.2 必须覆盖的语义边界

至少应对下列阶段或正式调用边界建立计时/NVTX 区间：

```text
process/bootstrap and formal imports
config load and identity gates
grid construction
initial-state construction
pressure-engine construction
zero-step diagnostics
HF3D run
accepted trial step
phase advection
interface compression
mixture-property construction
momentum advection
viscous acceleration
surface-tension acceleration
boundary relaxation
rotating-frame acceleration
cell/face conversions
pressure.project host wrapper
projection.project_device
post-projection diagnostics
topology diagnostics
frame recording
result assembly
archive write
strict archive reload and verification
final GPU synchronization
```

可以按 profiler 结果增加更细的候选区包装，但不得读取状态场或改写正式模块。

## 5. Python/native 主机热点证据

### 5.1 cProfile 能证明什么

cProfile 用于：

- Python 函数与内建/C 扩展调用边界的调用次数；
- `tottime` 和 `cumtime` 的调用图；
- 高频调用与高层算法路径；
- 与透明包装调用次数的交叉核验。

cProfile 不得用于：

- scored 基准时间；
- 证明 C/Fortran 内部符号、SIMD、缓存或内存带宽热点；
- 证明 CUDA kernel 执行时间、并行度或 memcpy 字节；
- 将 Python 与 C 代码的 profiler 时间直接解释为真实性能比例。

### 5.2 无 perf 时可接受的 native 覆盖定义

CPU native sampling 当前不可用，因此 P0-P 接受“正式算子/库调用边界级”的 native 归因，而不声称 native 符号级或微架构级归因。

`PROFILED_*_HOST` 必须同时满足：

```text
top_level_phase_union / child_process_wall >= 0.98
named_solver_exclusive_time / solver_phase_wall >= 0.90
```

其中：

- top-level phase 使用不重叠区间覆盖 worker 入口至最终同步和报告提交；
- solver 内部使用嵌套栈计算 exclusive 时间，禁止把 inclusive 区间相加制造大于 100% 的覆盖；
- 每个占 solver wall >= 5% 的区间必须落到具体正式函数或明确的 native library boundary；
- 未归因区间总计 > 10%，或任一 >= 5% 的区间只能写成“unknown native/C”，即失败闭锁；
- cProfile 用于解释调用图和核对次数，不用于计算上述 90% 墙钟覆盖率。

若 dominant region 已明确为一个正式 NumPy/SciPy/CuPy 算子边界，即使不能展开其内部原生符号，P0-P 可以报告“operator-level hotspot”；不得升级为 native symbol、缓存或带宽结论。

### 5.3 perf 的地位

当前 `perf record`/CPU sampling 不可用，不能把空报告或权限失败写成 native 证据。若未来能力探针成功，perf 只能作为额外的独立、非 scored profile：

- `perf record/report` 可补充 native sampled call stacks；
- `perf stat` 可补充 cycles、instructions、cache 等聚合计数；
- 两者都不能提供 CUDA kernel/memcpy 时间线。

在本裁决规定的语义覆盖门通过时，perf 不是 P0-P 完成的必需条件；若语义覆盖门未通过，则 perf 缺失不能被其他利用率图替代，P0-P 必须失败闭锁。

## 6. CUDA kernel/API/memcpy 证据

### 6.1 唯一允许的正式 nsys 配置

只允许使用已经通过独立合成探针的轻量配置：

```text
nsys 2026.2.1
--trace=cuda,nvtx
--sample=none
--cpuctxsw=none
```

禁止未经新能力探针和 Pro 复核加入：

```text
osrt
--cuda-memory-usage
CPU sampling
Python sampling
CUDA/Python backtrace
GPU metrics
Unified Memory page-fault tracing
```

### 6.2 必须产生和核验的对象

每个 `PROFILED_*_CUDA` 必须产生：

```text
.nsys-rep
SQLite export
cuda_api_sum
cuda_gpu_kern_sum
cuda_gpu_mem_time_sum
cuda_gpu_mem_size_sum
NVTX range manifest
parsed CUDA timeline report
```

硬门：

1. nsys 与目标进程均正常退出，不能有 post-target finalization error；
2. `.nsys-rep` 和 SQLite 为常规非空文件；
3. SQLite `PRAGMA integrity_check` 为 `ok`；
4. 导出的实际 schema 被保存，解析器不得假定未来版本固定 schema；
5. 四个统计报告均存在且内容非空；
6. kernel、CUDA API 和 memcpy 活动均绑定到正确 target PID/context；
7. 从首个 `P0P_PROCESS_START` NVTX 到 `P0P_FINAL_SYNC_END` 没有 trace 缺口或丢失尾部；
8. SQLite 与 `nsys stats` 对 kernel/API/memcpy 的计数、方向和总字节交叉一致；时间只允许报告格式舍入差；
9. 所有原始和解析文件均记录大小和 SHA-256；
10. 任一 export、schema、范围关联或尾部完整性不能证明，P0-P 失败闭锁。

### 6.3 H2D/D2H 次数与字节

精确 H2D/D2H 证据必须来自 CUDA activity/SQLite 和 `cuda_gpu_mem_size_sum`，不能从 GPU utilization 推断。

透明 `pressure.project` 包装器还必须只读取输入/输出的 shape、dtype、nbytes，按每次压力求解生成预期 full-field 传输多重集，并以 NVTX 唯一 call ID 标记每次调用。

必须报告：

```text
total H2D count/bytes/time
total D2H count/bytes/time
per accepted step H2D/D2H count and bytes
inside pressure.project NVTX ranges的 count/bytes
按传输尺寸的 histogram
candidate metadata 预期 full-field bytes
observed vs expected cross-check
```

要求 SQLite 与 summary 的方向、计数和字节完全一致；压力范围内观察到的 full-field 大小多重集必须覆盖包装器按 nbytes 计算的预期多重集。额外小传输可以存在，但必须列明。若只能看到 API 名而没有 direction/bytes，或无法把传输关联到 pressure range，则失败闭锁。

## 7. GPU 和系统采样工具的证据上限

### 7.1 nvidia-smi

nvidia-smi 只用于采样：

```text
GPU utilization
memory utilization
process/device memory
SM/memory clocks
pstate
power
GPU temperature
throttle reasons
other compute processes
```

它能够证明采样时刻的设备状态和近似利用率/显存高水位，但不能证明 kernel 名称、API 调用、memcpy 次数/字节、短脉冲完整性或精确事件重叠。正式 CUDA 归因必须来自 nsys。

### 7.2 pidstat

pidstat 用于：

```text
process CPU utilization
per-thread CPU utilization
RSS and faults
context switches
CPU migration / scheduling timeline when supported
```

它不能给出 Python/native 函数名、调用栈或 GPU 证据。目标 PID/TID、采样间隔、开始/结束时间和丢失样本必须记录；对 B1/B2/B3 这种长样本，目标存活期间的有效时间覆盖必须 >= 95%，任何连续 telemetry gap > 2 s 必须失败闭锁。

### 7.3 turbostat

允许使用 turbostat 或可审计的 `/sys/class/hwmon` Tctl 读取完成环境开跑门和 CPU 频率/温度/功耗记录。若没有可靠的 Tctl 数据源，不得把其他温度近似替代 90 °C 冻结门。

## 8. 分配计数与峰值内存

### 8.1 主机侧

以下证据均不够单独满足“host allocation count”：

```text
tracemalloc
RSS / VmHWM
ru_maxrss
cProfile call counts
pidstat memory columns
```

原因是它们不能完整计数 NumPy/SciPy/CuPy 进程中的原生 malloc/calloc/realloc/aligned allocation/mmap 路径。

`PROFILED_*_MEMORY` 必须使用一个经合成探针验证的候选侧原生分配观察器。技术路线不预设，可以是：

- 透明 `LD_PRELOAD` allocator interposer；
- 等价的 glibc/native allocation audit；
- 其他能够覆盖目标进程原生分配 API 的实现。

观察器必须：

1. 位于候选工作区，不修改正式源码或系统库；
2. 只转发到原 allocator，不替换分配策略；
3. 覆盖并分类 `malloc/calloc/realloc/free`、aligned/posix allocation，并在实际使用时覆盖 `mmap/munmap`；
4. 不在 hook 内递归分配或使用会死锁的普通日志路径；
5. 报告调用次数、请求/实际字节、当前 live bytes 和 peak live bytes；
6. 输出目标进程 PID、API 分类、完整性和溢出标志；
7. 在单线程、多线程、异常退出和已知分配序列合成测试中给出精确预期计数；
8. 在最小 NumPy+CuPy 数值探针中，启用/禁用观察器的数值 hash 相同且正常退出。

同时记录 `/proc/<pid>/status` 或 `smaps_rollup`、`VmHWM`、`ru_maxrss` 和外部 RSS 时间线，以交叉核验 peak host memory。若原生分配观察器无法可靠运行，或存在未计数/溢出且无法界定，则 P0-P 不完整并失败闭锁。

### 8.2 设备侧

设备分配证据必须组合：

```text
validated CuPy allocator request recorder
CuPy default memory-pool used_bytes/total_bytes high-water
cudaMemGetInfo or等价 phase/step boundary high-water
nsys cudaMalloc/cudaFree/cudaMallocAsync 等 API count
nvidia-smi process/device memory timeline
```

CuPy recorder 必须透明委托给原默认 memory pool，不改变 allocator 或清理策略，并在合成分配中准确报告 request count/bytes 和 pool high-water。

nsys API count用于发现不经过 CuPy recorder 的 driver/runtime allocations。若存在 unmatched allocation API，且不能取得其大小或证明它不影响峰值，则不得声称完整 device allocation/peak evidence，P0-P 必须失败闭锁。禁止用未验证的 `--cuda-memory-usage` 绕过该门。

## 9. 跨运行数值身份门

每个 benchmark 必须生成一个排除时间戳、墙钟和 profiler 元数据后的 canonical numerical payload SHA-256。

同一 B1/B2/B3 的：

```text
warm-up
timed R1/R2/R3
HOST profile
CUDA profile
MEMORY profile
```

必须同时满足：

- 配置 canonical hash 相同，除 `run.duration_s=0.002` 外无变化；
- 初态、网格、G0 和正式源码身份相同；
- status、接受步数、拒绝/重试序列、每步 dt、limiter、压力迭代和硬门相同；
- 登记输出时刻逐 uint64 相同；
- 数值 arrays/diagnostics/events 的 canonical payload hash 相同；
- CUDA 真实且无回退；
- 归档严格重载通过。

任一 instrumentation mode 改变数值位模式、失败语义或步骤序列，立即停止，不得把该 profiler 输出用于 P0 结论。

## 10. 何种证据组合足以完成 P0-P

只有以下全部成立，P0-P 才可写为完成：

1. 环境门在每个样本前通过；
2. 3 个 warm-up 和 9 个 scored 样本按冻结顺序完成；
3. 每个 benchmark 恰有 3 个 scored 样本，无替换和剔除；
4. B2/B3 的 HOST/CUDA/MEMORY 三个 profile 子运行全部完成；
5. 主机语义热点覆盖达到 98% top-level 和 90% solver attribution 门；
6. cProfile 调用图、包装调用次数和 pidstat 线程时间线相互一致；
7. nsys kernel/API/memcpy trace 完整，四个报告和 SQLite 交叉一致；
8. H2D/D2H 方向、次数、字节、时间和每步值可直接计算；
9. 主机原生分配 count/peak 和设备 allocation/peak 证据完整；
10. nvidia-smi/turbostat 环境与热时间线无缺口；
11. 所有模式数值 payload 逐位相同；
12. raw manifest、compact 报告和解析器复算一致。

P0 只形成 baseline 与热点/传输/分配归因，不形成 candidate speedup 或 P1 通过结论。

## 11. 必须失败闭锁的缺口

出现下列任一项即停止 P0-P：

```text
current environment gate invalid
formal source/config/preregistration/raw identity drift
benchmark hard gate failure or CUDA fallback
sample nonzero exit, timeout, external interruption
sample directory或manifest不完整
sample被重跑、替换、重排或剔除
profile instrumentation改变数值payload
host attribution < 90% 或 unknown >= 5% region
nsys finalization/export/schema/trace tail不完整
缺少 kernel/API/memcpy 任一类数据
H2D/D2H 无 direction/count/bytes 或无法范围关联
host native allocation count 不完整
unmatched device allocation 无法界定 size/peak
pidstat/nvidia-smi/temperature telemetry gap超门
无法可靠读取 Tctl
需要修改 02_模拟程序/**
需要改变数值合同、门值、压力算法或线程策略
```

失败后不得补样、换工具后继续剩余顺序或挑选已有样本；必须保留证据并交 Pro 裁决。

## 12. Harness、监测器与解析器机器门

零步实现至少包含：

```text
P0-P orchestrator
single-sample worker
strict config generator/auditor
environment gate
exclusive output-root lock
pidstat monitor/parser
nvidia-smi monitor/parser
turbostat/hwmon gate/parser
cProfile launcher/pstats parser
transparent wrapper/NVTX registry
nsys launcher/export/schema/stats parser
host allocation observer and parser
device allocation recorder and parser
archive/numerical-payload verifier
atomic manifest/report publisher
failure transaction
machine-readable static audit
```

### 12.1 启动机器门

在创建 sample raw 前必须验证：

- HEAD、P0 contract blob、正式源码树和 P0-D 输出 hash；
- `.venv-gpu` 的解释器 realpath、版本和关键包版本；
- RTX 5060 的设备名、UUID、driver/runtime 和 FP64 probe；
- nsys 恰为已验证版本/配置，最小 probe 及四报告 export 通过；
- cProfile、pidstat、nvidia-smi、温度读取、分配观察器能力 probe 通过；
- 32 个在线逻辑 CPU、AC、governor、线程策略；
- 无其他 CPU/GPU compute writer，30 s 背景 CPU <= 25%；
- CPU Tctl <= 90 °C，GPU <= 75 °C；
- output root 不存在或为空、无 symlink、无并发 lock；
- 当前失败 R1 raw 和 P0-D hash 未变化。

### 12.2 样本事务

每个样本必须：

1. 创建唯一 staging 根与锁；
2. 保存 preflight/environment/tool identity；
3. 启动外部监测器并记录 PID；
4. 启动唯一 worker；
5. 保存 stdout/stderr、退出码、进程和监测时间线；
6. 严格重载 archive 并验证 numerical payload；
7. 最终 GPU sync 后关闭监测器；
8. 验证所有证据后原子发布 canonical sample manifest；
9. 失败时发布唯一 failure record，不伪造成功 manifest。

不得把监测器自身 CPU 计入目标进程 CPU；必须同时记录系统总负载和 monitor PID。

## 13. 零时间步实现的规定测试

Codex 本轮至少必须实现并通过：

1. 三个 benchmark 配置只能改变 `run.duration_s`；
2. 固定 schedule 不能重排、跳过、替换或重复；
3. scored 模式拒绝 profiler/NVTX/interposer；
4. wrapper 参数、返回对象、异常、单调用和 nested exclusive 时间测试；
5. cProfile 合成 call graph 与 parser 覆盖计算；
6. host attribution 89.999% 失败、90% 通过及 unknown >= 5% 失败；
7. 已验证 nsys 最小 CuPy probe、SQLite integrity、四报告和 schema parser；
8. nsys 缺表、空 kernel、缺 memcpy、stats/SQLite 数量不一致、尾部缺失失败；
9. NVTX range 嵌套、PID/context 和 transfer range 关联测试；
10. H2D/D2H size multiset 和每步归一化测试；
11. pidstat/nvidia-smi/turbostat 正常、截断、PID 重用和 telemetry gap 测试；
12. 主机 allocator observer 的已知分配、多线程、realloc、aligned、mmap 和 overflow 测试；
13. CuPy allocator recorder 与 nsys allocation count cross-check 测试；
14. host/device peak memory 交叉和 unmatched allocation 失败；
15. instrumented/uninstrumented 最小 NumPy+CuPy 数值 hash 同一；
16. archive numerical payload 的时间/性能元数据排除规则；
17. output root symlink、并发 writer、atomic publish 和 failure injection；
18. 当前 96 °C/外部 compute 环境被正确拒绝，且不创建 sample raw；
19. static audit 明确 `time_steps_advanced = 0`、`performance_samples_started = 0`；
20. `git diff --check`、Ruff、格式、限定 mypy、`compileall`、严格 JSON 和文档解析。

## 14. 当前授权范围与停止点

本裁决只授权：

```text
90_候选工作区/current/17_HF3D性能优化/**
90_候选工作区/交接说明_current.md
90_候选工作区/交接说明_history.md
```

用于实现上述零步 harness、监测器、解析器、分配观察器、合成 probe、测试和默认拒绝的机器授权对象。

只读且禁止修改：

```text
02_模拟程序/**
01_最终理论/**
90_候选工作区/current/07_D3跨网格时间步响应诊断/**
AGENTS.md
P0 冻结预注册
P0-D 已提交输出
```

Codex 完成零步实现后必须立即停止并提交：

```text
clean implementation commit
all source/blob/hash identities
capability probes
synthetic tests
static audit
current/history handoff
```

且必须证明：

```text
HF3D time steps advanced = 0
warm-up samples started = 0
scored samples started = 0
profile samples started = 0
formal source unchanged
P1 not implemented
P0-P authorization = NOT_AUTHORIZED
```

不得等待环境冷却后自行开跑。

## 15. 未来 P0-P 开跑授权

零步实现经 Pro 独立静态复核后，才可签发 P0-P 精确快照机器授权。结构要求：

1. Pro 裁决、implementation hashes 和 static evidence 位于 reviewed parent；
2. 唯一直接子提交只修改新的 `p0p_execution_authorization.json`；
3. 授权绑定 harness、worker、wrapper registry、所有 monitor/parser、allocator observer 源码和构建产物 hash、P0 contract、正式源码树、工具版本与 GPU 环境；
4. 授权只允许冻结 schedule 的一次执行；
5. 每个样本前仍须实时通过环境门；
6. 任一失败即终止全部剩余样本；
7. P0-P 完成后立即停止交 Pro，不得进入 P1。

## 16. 结论上限

```text
D3R1_S2 = FAILED_STOPPED
D3R1_DIRECT_TRIGGER = PROJECTED_DIVERGENCE
D3R1_FAILURE_MECHANISM = NOT_YET_ESTABLISHED
D3R1_S3 = NOT_EXECUTED
D3R1_SCIENTIFIC_CLASSIFICATION = NOT_FORMED

HF3D_P0_D = COMPLETED
HF3D_P0_P_PLAN = FROZEN
HF3D_P0_P_ZERO_STEP_IMPLEMENTATION = AUTHORIZED
HF3D_P0_P_DYNAMIC_SAMPLES = NOT_AUTHORIZED_PENDING_PRO_STATIC_REVIEW
HF3D_P1_IMPLEMENTATION = NOT_AUTHORIZED

NUMERICAL_QUALIFICATION = NOT_DEMONSTRATED
PHYSICAL_VALIDATION = NOT_PERFORMED
EXPERIMENTAL_VALIDATION = NOT_PERFORMED
APPARATUS_PREDICTION = DISALLOWED
```

本轮没有修改正式源码、P0 预注册或 P0-D 证据，没有运行性能样本、HF3D 时间步或实验。
