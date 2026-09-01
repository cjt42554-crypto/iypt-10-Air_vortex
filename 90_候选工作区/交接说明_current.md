# 当前交接

当前状态：

```text
HF3D_P0P_V1_CLAIM = CONSUMED
HF3D_P0P_V1_SCHEDULE = TERMINAL_FAILED_STOPPED_NO_RESUME
HF3D_P0P_V1_MEASURED_PERFORMANCE_RESULT = NOT_AVAILABLE

HF3D_P0P_V1_E0_FAILURE_EVIDENCE_REGISTRATION = ACCEPTED
HF3D_P0P2_I2_EFFECTIVE_CONFIG_AND_ZERO_STEP_QUALIFICATION = ACCEPTED
HF3D_P0P2_V2_DYNAMIC_HARNESS_STATIC_REVIEW = CONDITIONAL_PASS_WITH_BLOCKING_FINDINGS
HF3D_P0P2_V2_PRECLAIM_TERMINAL_AND_ANALYSIS_PATCH = REQUIRED
HF3D_P0P2_V2_PARENT_TICKET = NOT_ISSUED
HF3D_P0P2_V2_AUTHORIZATION_CHILD = NOT_ISSUED
HF3D_P0P2_V2_DYNAMIC_EXECUTION = NOT_AUTHORIZED
HF3D_P0P2_V2_PATCH_IMPLEMENTATION = AUTHORIZED
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
90_候选工作区/current/26_P0P2V2动态基线静态复核与执行前补丁裁决.md
```

复核对象：

```text
Codex implementation commit = 6ce05eed96ce59a94d79dc2a49c9e012f3afb1f5
implementation tree = 24a867e53929bfb36d9da7b353737441504fe4bd
formal source SHA-256 = dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8
```

## 已接受并保留

以下主体无需回退或重新复制：

- E0 的 15 文件 V1 失败 snapshot、manifest 和语义审计；
- 三份 `0.005 s` 配置的正式 round-trip 和真实 CUDA 零步构造；
- V2 独立 contract/schedule/authorization/claim/ledger/sample namespace；
- 固定 18 项顺序和无 skip/retry/resume/replacement CLI；
- 精确父票据、NUL-safe raw diff、blob/mode/parent/branch/fetch 授权审计；
- fresh-process worker、正式 HF3D stepper、索引式登记时刻和 D3R1 硬门；
- archive write、strict reload 和 canonical numerical payload；
- HOST、CUDA、MEMORY 三类 profile 框架；
- sample staging、`RENAME_NOREPLACE` 和一次性 token/ledger 状态机主体。

当前配置和零步资格仍为 PASS；没有动态 raw、claim 或样本。

## 为什么没有签发父票据

本轮发现五项会在一次性 claim 后造成不可恢复失败、受污染基线或未授权分析的阻塞问题。

### P0-1：ticket-bound 当前机器身份未在 claim 前完整比较

Preflight 只验证票据绑定的 capability 文件本身，并检查当前环境满足一般门。当前 governor、affinity、driver、CPU model、AC record 或线程策略可以在仍满足一般门的情况下与 ticket 漂移；第一个 worker 才在 claim 后发现。

必须把当前 probe/toolchain/environment 与 ticket capability 的逐字段同一检查移到 lock/raw/claim/ledger 之前。Worker 保留重复检查。

### P0-2：后续样本和样本内部缺少 CPU 背景污染证明

Claim 前只有一次 30 秒完整环境门。Claim 后每个样本前只做瞬时门，样本内 monitor 只跟踪 target；数小时日程中后来出现的仓库外 CPU 作业可能污染 scored wall time而不被发现。

必须：

- 每个样本开始前重新执行完整 30 秒环境门；
- 样本生命周期内记录系统背景 CPU 和其他计算进程，排除 target/orchestrator/monitor PID；
- 任何污染使日程永久 FAILED_STOPPED；
- analyser 独立复算该门。

### P0-3：CUDA profile telemetry 的终点使用外层 nsys 退出时刻

Monitor target 是内部 Python worker，worker 退出时 monitor 停止；但 coverage 终点是外层 `nsys` 收尾后的退出时刻。只要 Nsight finalization 超过 2 秒，完整 worker telemetry 会被误判为缺口。

必须分离：

```text
worker target lifetime -> telemetry coverage
outer nsys lifetime -> profile end-to-end wall and finalization gate
```

并增加人工 finalization lag 正负测试。

### P0-4：第 18 项先把 ledger 设为 COMPLETE，再写 completion

当前最后一个 `mark_sample_pass()` 先令 ledger `COMPLETE`。若 `execution_complete.json` 的 open/write/fsync/reload 失败，ledger 已不可转为 FAILED，而 failure publication 又要求 FAILED ledger，可能留下：

```text
ledger COMPLETE
completion absent/corrupt
failure absent
```

必须增加 `ALL_SAMPLES_PASS_PENDING_TERMINAL` 或等价状态，使最终只能为：

```text
18 PASS + 唯一完整 completion
或
自洽 FAILED_STOPPED + 唯一 failure
```

并覆盖全部 terminal 写入故障。

### P0-5：analyser 未绑定 claim 快照且无完整分析事务

当前 analyser 可以在日程完成后于另一 Git 提交或脏工作树上读取旧 raw；它没有校验当前 analyser/parser blobs 等于 claim implementation files，也没有 analysis lock、staging recovery 或 analysis failure record。

必须：

- 从 claim 重建授权 child/review parent/reviewed snapshot；
- 要求 clean symbolic main 和 analyser 全依赖 blob 精确匹配 claim；
- 核验 formal source、contract、capability 和 raw controls；
- 建立 analysis 单写入者与可恢复 publication；
- compact terminal 记录 analyser commit/blob set、claim hash 和 raw manifest hash。

## 下一项唯一任务

只在：

```text
90_候选工作区/current/24_P0P失败证据登记与P0P2零步实现/**
```

修复上述五项，更新 current/history，保持 V2 默认拒绝。因为尚无 P2/C2/claim/dynamic output，不强制改成 V3，也不得复制另一套活动 harness。

### 必须新增的负例

至少覆盖：

```text
governor all-equal but differs from ticket
affinity / CPU model / driver / UUID / thread policy drift
second sample before a new 30 s environment gate
sample lifetime中出现其他 CPU compute process
worker完整 telemetry + 5 s nsys finalization lag
worker telemetry真正截断
18th PASS 后 completion open/write/fsync/reload failure
ledger terminal transition failure
HEAD/analyser blob changed before analysis
dirty worktree analysis
concurrent analyser
unknown stale analysis staging
analysis terminal write/rename failure
raw changed after completion
```

### 返回 Pro 前静态门

必须重新执行并固化：

```text
完整 V2 tests
Ruff
strict mypy
Python compile
allocator probes
qualified nsys probe/export/parser
三份 fresh-process CUDA zero-step probes
Git topology synthetic positive/negative tests
static audit and deterministic rebuild
```

并明确：

```text
HF3D time_steps_advanced = 0
warmup/timed/profile started = 0
claims consumed = 0
performance raw absent
compact evidence absent
parent ticket absent
authorized grant absent
P1 not implemented
```

完成后 commit、push `main` 并立即停止交 Pro。当前 `6ce05eed...` 不能作为父票据 reviewed snapshot。

## 允许修改

```text
90_候选工作区/current/24_P0P失败证据登记与P0P2零步实现/**
90_候选工作区/交接说明_current.md
90_候选工作区/交接说明_history.md
```

## 只读

```text
01_最终理论/**
02_模拟程序/**
90_候选工作区/current/07_D3跨网格时间步响应诊断/**
90_候选工作区/current/17_HF3D性能优化/**
V1 raw/claim/ledger/failure
E0 snapshot/manifest/audit
AGENTS.md
90_候选工作区/current/26_P0P2V2动态基线静态复核与执行前补丁裁决.md
```

## 当前禁止

```text
P2 ticket
C2 grant
authorization=true
--execute
warm-up/timed/profile
动态 analyser
P1
02_模拟程序/** 修改
环境、质量、覆盖率、数值同一或事务门放宽
```

## 技术债与性能路线

全项目仍采用定向跨模块重构，不做整体重写。正式 HF3D 的 backend/stepper/pressure/recording/archive 边界应保留。

旧真实运行证明 CPU/GPU 利用不足，但 P0-P V1 没有性能样本，P0-P2 V2 尚未合法执行。必须先完成本补丁并取得一次可信 baseline，再决定 E0 小优化或显式 GPU-resident E1 后端。任何正式提升都必须以数值等价、实测 CPU/GPU 利用率和端到端加速比共同验收。

## 结论上限

```text
P0-P V1 measured result = NOT AVAILABLE
E0 = ACCEPTED
P0-P2 zero-step = ACCEPTED
P0-P2 V2 harness = CONDITIONAL_STATIC_PASS
P0-P2 V2 dynamic execution = NOT AUTHORIZED
P0-P2 V2 patch implementation = AUTHORIZED
P1 = PROHIBITED
numerical qualification = NOT DEMONSTRATED
physical validation = NOT PERFORMED
experimental validation = NOT PERFORMED
apparatus prediction = DISALLOWED
```
