# 当前交接

当前状态：

```text
D3R1_S2 = FAILED_STOPPED
D3R1_DIRECT_TRIGGER = PROJECTED_DIVERGENCE
D3R1_FAILURE_MECHANISM = NOT_YET_ESTABLISHED
D3R1_S3 = NOT_EXECUTED
D3R1_SCIENTIFIC_CLASSIFICATION = NOT_FORMED

HF3D_P0_D = COMPLETED
HF3D_P0_P_ZERO_STEP_IMPLEMENTATION = STATIC_REVIEW_ACCEPTED
HF3D_P0_P_REVIEW_BINDING_MATERIAL = REQUIRED
HF3D_P0_P_DYNAMIC_SAMPLES = NOT_AUTHORIZED
HF3D_P1_IMPLEMENTATION = NOT_AUTHORIZED

PROJECT_REFACTOR = TARGETED_CROSS_MODULE_REFACTOR
PROJECT_WIDE_REWRITE = REJECTED
HF3D_HARDWARE_UTILIZATION = INSUFFICIENT
PRIMARY_ACCELERATION_ROUTE = EXPLICIT_GPU_RESIDENT_E1_BACKEND
REFERENCE_BACKEND = RETAINED
```

## 最新 Pro 裁决

详细裁决：

```text
90_候选工作区/current/21_P0P零步实现终审与全项目技术债性能路线裁决.md
```

复核绑定：

```text
reviewed main: 8a0c2fa564d9d6abfd84e5620379af11e5f858d3
reviewed parent: 2ec92aa0646f403bfd813a7721c52073f83dc93f
reviewed tree: 57dc939af0bc923ef3076d1d14ea889573041cf7
P0 contract blob: 62f256574f67654d13f588e0505f63505d065907
formal source tree: dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8
```

## P0-P 零步实现复核结论

提交 `8a0c2fa...` 的 runtime implementation 可以保留。Pro 接受：

- 父票据 exact-blob 授权与无自引用 schema；
- NUL-safe Git topology/path/blob/mode 审计；
- 默认拒绝、非消费 preflight、O_EXCL claim 和固定 18 项 schedule；
- 独占 writer、worker launch token、无 resume/补样/替换；
- 环境门、当前约 96.875 °C Tctl 的零步拒绝；
- fresh config/state、正式 HF3D stepper、硬门、archive/reload/sync 的端到端样本语义；
- cProfile/operator-boundary、轻量 nsys、pidstat、nvidia-smi/turbostat、host/device allocator 证据组合；
- 跨 TIMED/HOST/CUDA/MEMORY 的逐位 numerical payload 门；
- atomic sample/failure/complete 事务和零步静态证据。

当前 harness 的性能结论范围严格限于：

```text
D3R1 production-equivalent HF3D execution path
```

不能扩大为整个 GUI/CLI 应用的全端到端性能。

## 为什么仍未开跑

当前未签发 parent ticket 或 authorized child。阻塞是 review-binding material 尚未机器化集中提交，而不是 runtime 实现失败。

票据需要精确绑定全部 implementation、static audit、P0 contract、P0-D、capability、denied/authorized blob 和 fixed schedule。不得由 Pro 手工转录长列表后直接开跑，以免一个 blob/hash 录入错误使昂贵 campaign 作废。

## 下一项唯一任务：最小 review-binding material 补充

仅允许新增或修改：

```text
90_候选工作区/current/17_HF3D性能优化/review_tools/**
90_候选工作区/current/17_HF3D性能优化/artifacts/p0/p0p_static/p0p_review_binding_material.json
90_候选工作区/current/17_HF3D性能优化/artifacts/p0/p0p_static/p0p_review_binding_material.md
90_候选工作区/current/17_HF3D性能优化/tests/** 中仅与 review material 生成相关的测试
90_候选工作区/交接说明_current.md
90_候选工作区/交接说明_history.md
```

不得修改：

```text
任何现有 P0-P runtime implementation
p0p_execution_authorization.json
P0 contract / P0-D
02_模拟程序/**
01_最终理论/**
07_D3跨网格时间步响应诊断/**
AGENTS.md
```

binding material 必须确定性输出：

```text
全部 REQUIRED_IMPLEMENTATION_FILES 的 path/mode/Git blob/SHA-256/byte length
static audit / contract / P0-D / capability / denied authorization 绑定
formal source identity
固定 18 项 schedule canonical object/hash
ticket schema、review-only paths、protected prefixes
non-authorizing / ticket absent / grant absent / dynamic denied 声明
```

不得包含当前补充提交自身或未来 P/C 的 commit/tree hash。

必须证明：

```text
runtime blobs 与 8a0c2fa... 完全相同
formal source unchanged
self-reference absent
regeneration byte-identical
dynamic authorization denied
HF3D time_steps = 0
warmup/scored/profile = 0
```

完成后立即停止交 Pro。若 runtime 有任何变化，需重新完整静态复核。

## 全项目技术债裁决

三选一：

```text
不重构：拒绝
局部、跨模块、限界重构：接受
全项目推倒重写：拒绝
```

正式 HF3D 已按 backend/stepper/pressure/timestep/recording/numerics/archive 分层，且有冻结证据和大量测试；没有全量重写依据。技术债集中在：

1. 主机 NumPy 主路径 + GPU pressure 脉冲 + 每步完整场传输；
2. D1/D2/D3/P0 campaign 的授权、台账、事务和证据逻辑重复；
3. GPU 驻留所需的 recording/archive/diagnostic 同步边界；
4. profiler/native build 的可复现构建身份。

优先级：

```text
P0: 完成 P0-P baseline
P1A: explicit accelerated backend
P1B: 未来 campaign/evidence 共享核心
P2: recording/archive/device boundary
P3: GUI/forward 等仅在有独立缺陷证据时处理
```

旧冻结脚本和证据保持不可变，不迁移回写。

## 硬件利用与主要路线

当前 16 核/32 线程机器上的实测约为：

```text
process CPU ~101%
main thread ~99.9%
GPU mostly 16%--22%
```

CPU 和 GPU 利用均不充分。线程环境变量本身不能解决该架构。

主要路线：

```text
显式新 GPU-resident HF3D backend
默认按 E1 新数值身份管理
保留现有 reference backend
```

GPU 路径应长期驻留 phase、velocity、pressure、properties 和 workspace，只在登记输出和最终归档边界回传；迁移 phase/interface、momentum/viscosity/body force、boundary/topology reduction，并预分配复用。

允许同时做小型 E0 cache/preallocation 原型，或在 P0 证明适合时做 CPU parallel 原型，但只能由实测选择提升路线。

## 性能与利用率验收

### E0

```text
所有状态、步序列、dt/limiter/retry、pressure iterations、events、archive 逐位相同
F0 精确复现 G9_DT2 第 6695 步失败
B5 完整 G9_DT2 配对
B1/B2/B3 各 >= 1.50x
B1--B3 几何中位 >= 1.75x
B4、B5、预计六案例 >= 1.75x
无回退 > 5%
```

### E1

```text
新 backend ID、新 formal source identity、新结果盲合同、至少 D3R2
FP64，无 TF32/FP32/mixed/fastmath
同方程/边界/G0/材料/驱动/网格/输出
初态逐位同一，守恒/散度/残差/CFL 不超门
冻结 checkpoint 和观测量差异合同
B1--B3 各 >= 1.50x
B1--B3 几何中位、B4、预计六案例 >= 2.00x
无回退 > 5%
```

### GPU 利用率

```text
median compute >= 45%
p90 utilization >= 70%
full-field H2D/D2H bytes/step reduction >= 75%
peak device memory <= 80% VRAM
无稳定单主线程饥饿模式
```

### CPU 路径

```text
aggregate CPU >= 400%
至少 4 个逻辑 CPU 持续工作
任一线程 <= 总线程 CPU time 的 50%
```

利用率不替代端到端速度门。

## 当前禁止

```text
任何 P0-P dynamic sample
任何 parent ticket / authorized child
任何 P1 实现
任何 02_模拟程序/** 修改
任何 D3R1 重跑或 S3
放宽散度、压力、CFL 或其他科学门
删除或复用 V1/R1 raw
```

## 当前结论上限

```text
HF3D_P0_P_ZERO_STEP_IMPLEMENTATION = STATIC_REVIEW_ACCEPTED
HF3D_P0_P_REVIEW_BINDING_MATERIAL = REQUIRED
HF3D_P0_P_DYNAMIC_SAMPLES = NOT_AUTHORIZED
HF3D_P1_IMPLEMENTATION = NOT_AUTHORIZED

PROJECT_REFACTOR = TARGETED_CROSS_MODULE_REFACTOR
PROJECT_WIDE_REWRITE = REJECTED
HF3D_HARDWARE_UTILIZATION = INSUFFICIENT
PRIMARY_ACCELERATION_ROUTE = EXPLICIT_GPU_RESIDENT_E1_BACKEND
REFERENCE_BACKEND = RETAINED

NUMERICAL_QUALIFICATION = NOT_DEMONSTRATED
PHYSICAL_VALIDATION = NOT_PERFORMED
EXPERIMENTAL_VALIDATION = NOT_PERFORMED
APPARATUS_PREDICTION = DISALLOWED
```
