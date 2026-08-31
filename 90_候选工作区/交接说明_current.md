# 当前交接

当前状态：

```text
D3R1_S2 = FAILED_STOPPED
D3R1_DIRECT_TRIGGER = PROJECTED_DIVERGENCE
D3R1_FAILURE_MECHANISM = NOT_YET_ESTABLISHED
D3R1_S3 = NOT_EXECUTED
D3R1_SCIENTIFIC_CLASSIFICATION = NOT_FORMED
HF3D_P0_D = COMPLETED
HF3D_P0_P_FINAL_PLAN = FROZEN_BY_PRO
HF3D_P0_P_ZERO_STEP_HARNESS_IMPLEMENTATION = AUTHORIZED
HF3D_P0_P_DYNAMIC_SAMPLES = NOT_AUTHORIZED_PENDING_PRO_STATIC_REVIEW
HF3D_P1_IMPLEMENTATION_AND_FORMAL_SOURCE_MODIFICATION = NOT_AUTHORIZED
```

## 有效裁决与绑定

P0-P 最终规划裁决：

```text
90_候选工作区/current/19_HF3DP0P最终规划与证据充分性裁决.md
```

复核绑定：

```text
reviewed main: e1e72f95548b9456d94cb91d7dc64a6b925dd2b8
P0 contract blob: 62f256574f67654d13f588e0505f63505d065907
formal source tree: dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8
R1 raw canonical SHA-256: 906a32f71f3e095afc7cba6a3eddf7a83e4d639d36dc0b3014278b3e0001b032
```

P0-D 已完成并保持结果盲边界；P0 预注册、正式源码和 P0-D 输出本轮只读。

## 本机 profiler 能力裁决

已接受用户提供的独立实测：

```text
nsys 2026.2.1
--trace=cuda,nvtx --sample=none --cpuctxsw=none
```

可正常产生 `.nsys-rep`、SQLite，并导出：

```text
cuda_api_sum
cuda_gpu_kern_sum
cuda_gpu_mem_time_sum
cuda_gpu_mem_size_sum
```

该配置可用于 kernel/API/memcpy 时间线、H2D/D2H 次数/字节/时间。正式 P0-P 禁止未经新 probe 启用 `osrt`、`--cuda-memory-usage`、CPU/Python sampling 或 backtrace；这些更重配置曾出现收尾异常。

当前 CPU sampling/perf 和 py-spy 不可用。P0-P 允许用：

```text
cProfile + 透明正式算子包装
pidstat
the validated lightweight nsys trace
nvidia-smi / turbostat
validated host-native allocator observer
validated CuPy/device allocator recorder
```

形成互补证据。cProfile 不能作为 benchmark 或 native symbol sampling；pidstat/nvidia-smi 不能替代热点或 memcpy trace。

## 冻结 profile bundle

预注册的 `PROFILED_B2`、`PROFILED_B3` 分别冻结为三个独立新进程：

```text
PROFILED_B2_HOST
PROFILED_B2_CUDA
PROFILED_B2_MEMORY
PROFILED_B3_HOST
PROFILED_B3_CUDA
PROFILED_B3_MEMORY
```

- HOST：cProfile、透明语义包装、pidstat、nvidia-smi；
- CUDA：仅轻量 nsys CUDA/NVTX、透明 NVTX 包装、pidstat、nvidia-smi；
- MEMORY：host native allocation observer、CuPy/device recorder、RSS/ru_maxrss/nvidia-smi；不同时启用 cProfile 或 nsys。

全部 profile 子运行不计分，且必须与对应未插桩样本数值 payload 逐位相同。

## 下一项唯一任务：零时间步实现

只允许在：

```text
90_候选工作区/current/17_HF3D性能优化/**
90_候选工作区/交接说明_current.md
90_候选工作区/交接说明_history.md
```

实现：

```text
P0-P orchestrator
single-sample worker
strict benchmark config generator/auditor
environment gate and single-writer lock
pidstat/nvidia-smi/turbostat monitors and parsers
cProfile launcher/pstats parser
transparent semantic wrapper and NVTX registry
nsys launcher/export/schema/stats parser
host native allocation observer
device allocation recorder
archive/numerical-payload verifier
atomic sample manifest/failure transaction
machine-readable static audit
default NOT_AUTHORIZED p0p_execution_authorization.json
```

禁止修改：

```text
02_模拟程序/**
01_最终理论/**
90_候选工作区/current/07_D3跨网格时间步响应诊断/**
AGENTS.md
00_P0性能与散度诊断预注册.json
P0-D 已提交输出
```

## 关键证据门

### 主机热点

```text
top-level phase wall coverage >= 98%
solver named exclusive attribution >= 90%
any unknown region >= 5% => FAIL
```

cProfile 只解释 Python调用图、次数和 operator boundary；不能声称 native symbol 或微架构归因。perf 不是必需条件，但如果上述语义覆盖不达门，perf 缺失不能用利用率图替代。

### CUDA trace

正式 nsys profile 只能使用已验证轻量配置。必须生成并交叉核验 `.nsys-rep`、SQLite、四个 stats 报告、NVTX ranges、kernel/API/memcpy timeline。SQLite 和 summary 的 H2D/D2H direction/count/bytes 必须一致，并与 `pressure.project` 包装器按 shape/dtype/nbytes 计算的 full-field transfer 多重集交叉。

### 内存

`tracemalloc`、RSS、ru_maxrss、cProfile 或 pidstat 单独都不能满足 host native allocation count。必须实现并合成验证透明 native allocation observer；不能可靠计数即失败闭锁。

设备侧必须组合：

```text
CuPy allocator request recorder
pool used/total high-water
cudaMemGetInfo/phase boundary
nsys allocation API count
nvidia-smi process/device memory timeline
```

存在无法界定大小/峰值的 unmatched device allocation 即失败闭锁。

### 跨模式数值同一

同一 B1/B2/B3 的 warm-up、三次 timed、HOST/CUDA/MEMORY profile 必须在排除时间和性能元数据后得到相同 numerical payload SHA-256，并保持相同步数、dt、limiter、retry、压力迭代、硬门、登记时刻和真实 CUDA。

## 当前环境与禁止开跑

当前存在仓库外计算进程且 CPU Tctl 约 96 °C，不满足冻结的：

```text
no_other_compute_process
background CPU <= 25% for 30 s
CPU Tctl <= 90 °C
```

本轮不得启动：

```text
WARMUP_B1/B2/B3
任何 TIMED_* 样本
任何 PROFILED_* 子运行
任何 HF3D 时间步
```

零步 preflight 应正确拒绝当前环境，并且不得创建 performance sample raw。

## 零步返回 Pro 前最低验收

必须通过：

- config 只允许 `run.duration_s=0.002`；
- 固定 schedule 不可重排/补样；
- scored 模式拒绝 profiler/interposer；
- wrapper 参数/返回/异常/单调用/nested exclusive 测试；
- 90%/98% 覆盖边界负例；
- 已验证 nsys probe、SQLite integrity、四报告及缺表/缺 tail 负例；
- H2D/D2H direction/count/bytes/range 解析；
- pidstat/nvidia-smi/turbostat telemetry gap/PID reuse 负例；
- host allocator 已知 count/bytes/peak、多线程/realloc/aligned/mmap 测试；
- CuPy allocator 与 nsys allocation cross-check；
- instrumented/uninstrumented 最小 NumPy+CuPy hash 同一；
- archive payload identity、atomic publish、symlink/concurrency/failure injection；
- 当前非法环境零步拒绝；
- Ruff、格式、限定 mypy、`compileall`、严格 JSON、文档解析和 `git diff --check`。

返回时必须证明：

```text
HF3D time_steps_advanced = 0
warmup_samples_started = 0
scored_samples_started = 0
profile_samples_started = 0
formal source unchanged
P1 not implemented
P0-P authorization = NOT_AUTHORIZED
```

完成后立即停止交 Pro 静态复核，不得等待环境冷却后自行开跑。

## 未来开跑方式

Pro 接受零步实现后，才可采用：

```text
reviewed parent: ruling + implementation + static evidence
unique authorization child: only p0p_execution_authorization.json changes
```

授权必须绑定 harness、worker、wrapper、monitors/parsers、allocation observer 源码与构建物、P0 contract、正式源码树、工具版本和 GPU 环境，只允许冻结 schedule 的一次执行。每个样本前仍实时判环境门；任一失败终止剩余计划。P0-P 完成后停止，不得进入 P1。

## 当前结论上限

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
