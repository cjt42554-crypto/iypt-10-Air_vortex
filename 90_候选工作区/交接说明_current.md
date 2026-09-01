# 当前交接

当前状态：

```text
HF3D_P0P_V1_CLAIM = CONSUMED
HF3D_P0P_V1_SCHEDULE = TERMINAL_FAILED_STOPPED_NO_RESUME
HF3D_P0P_V1_MEASURED_PERFORMANCE_RESULT = NOT_AVAILABLE

HF3D_P0P_V1_E0_FAILURE_EVIDENCE_REGISTRATION = ACCEPTED
HF3D_P0P2_I2_EFFECTIVE_CONFIG_AND_ZERO_STEP_QUALIFICATION = ACCEPTED

HF3D_P0P2_V2_PRECLAIM_MACHINE_IDENTITY_PATCH = ACCEPTED
HF3D_P0P2_V2_PER_SAMPLE_ENVIRONMENT_AND_BACKGROUND_PATCH = ACCEPTED
HF3D_P0P2_V2_CUDA_WORKER_LAUNCHER_LIFETIME_PATCH = ACCEPTED
HF3D_P0P2_V2_EXECUTION_TERMINAL_TRANSACTION_PATCH = ACCEPTED
HF3D_P0P2_V2_ANALYSIS_PATCH = PARTIAL_PASS_WITH_BLOCKING_FINDINGS

HF3D_P0P2_V2_NONCONSUMING_PREFLIGHT_RETRY_PATCH = REQUIRED
HF3D_P0P2_V2_ANALYSIS_AUTHORIZATION_AND_TOOLCHAIN_REBINDING = REQUIRED
HF3D_P0P2_V2_ANALYSIS_UNIQUE_TERMINAL_TRANSACTION = REQUIRED

HF3D_P0P2_V2_PARENT_TICKET = NOT_ISSUED
HF3D_P0P2_V2_AUTHORIZATION_CHILD = NOT_ISSUED
HF3D_P0P2_V2_DYNAMIC_EXECUTION = NOT_AUTHORIZED
HF3D_P0P2_V2_FINAL_PATCH_IMPLEMENTATION = AUTHORIZED
HF3D_P1 = PROHIBITED

PROJECT_REFACTOR = TARGETED_CROSS_MODULE_REFACTOR_AFTER_BASELINE
PROJECT_WIDE_REWRITE = REJECTED
HF3D_HARDWARE_UTILIZATION = INSUFFICIENT
TRUSTWORTHY_P0P2_BASELINE = NOT_AVAILABLE

D3R1_S2 = FAILED_STOPPED
D3R1_SCIENTIFIC_CLASSIFICATION = NOT_FORMED
```

## 最新 Pro 裁决

```text
90_候选工作区/current/27_P0P2V2补丁复核与非消费预检及分析唯一终态裁决.md
```

复核对象：

```text
Codex patch commit = d8b73846c33a028616fef2ede74ce21052e71555
patch tree = 8484e40231c1140769ffecb4c515538583adfacd
formal source SHA-256 = dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8
```

## 已接受并保留

以下部分不要求回退：

- E0 的 V1 失败 snapshot 和语义登记；
- 三份 `0.005 s` 正式配置和真实 CUDA 零步资格；
- claim 前 ticket-bound machine identity 比较；
- 每样本 30 秒完整环境门；
- worker 生命周期内 background CPU/other-compute monitor；
- worker 与 outer Nsight finalization 生命周期分离；
- 第 18 项后的 `ALL_SAMPLES_PASS_PENDING_TERMINAL`；
- completion/failure 隔离和唯一 execution terminal 组合；
- 固定 18 项顺序、无 retry/resume/replacement；
- fresh worker、正式 HF3D、D3R1 适用硬门、archive/reload 和数值 payload；
- HOST/CUDA/MEMORY 框架；
- parent-ticket Git topology 主体；
- sample staging 和 no-replace publication 主体。

## 仍然阻塞 P2/C2 的三项问题

### 1. 非消费 preflight 实际会使 C2 工作树变脏

Preflight 失败记录当前写到：

```text
artifacts/p0p2v2/static/preclaim_failures/
```

但该目录没有被 `.gitignore` 忽略。一次温度、背景任务或 machine identity 的 claim 前失败会生成未跟踪文件；下一次授权审计因 dirty worktree 拒绝，从而在 `claim=0` 时使授权不可重用。

必须把 failure root 移到精确 ignored runtime-evidence 目录，或增加精确 ignore 规则，并把 `.gitignore` 加入 future ticket 的 reviewed implementation binding。

规定门：

```text
preclaim failure retained
worktree remains clean
claim/raw/ledger/lock absent
same C2 can later pass a valid preflight
```

### 2. Analysis lock 的失败者可以污染成功终态

当前任何 analyser 异常都会写全局 `analysis_failure.json`，包括：

```text
没有取得 analysis lock
成功后重复调用
compact output 已存在
```

所以两个并发 analyser 可能产生：

```text
compact success + analysis_failure
```

成功后第二次调用也可能污染已成功终态。

必须改为：

- 只有持有 analysis lock 的 owner 可以写 global failure；
- lock/precondition rejection 无副作用；
- success 和 failure 全程互斥；
- second invocation after success 无副作用；
- pre-existing failure 禁止 success；
- terminal/rename/reload 故障最终只能形成一种 terminal class。

### 3. Analyzer 没有独立重建 P2/C2，也没有比较当前 parser toolchain

`analysis_git_snapshot()` 当前只比较 HEAD、implementation files、formal source 和 claim 内两个历史 capability 字段。它没有：

```text
重新 audit 当前 authorized grant / P2 ticket / C2 topology
把 claim 的 ticket/parent/snapshot/blob 与 Git objects 逐项比较
观察当前 analyser Python/NumPy/SciPy/platform identity
```

必须在 analysis lock和任何输出以前：

- 重新执行完整 parent-ticket authorization audit；
- 把 C2、P2、reviewed snapshot、ticket blob/hash、authorized blob 与 claim 精确比较；
- 使用精确受审查 Python 或重建当前 parser toolchain，并与 ticket/claim 精确比较；
- wrong Python/package、fake claim parent、wrong ticket/authorized blob 必须在输出前失败。

## 下一项唯一任务

Codex 只在：

```text
90_候选工作区/current/24_P0P失败证据登记与P0P2零步实现/**
90_候选工作区/交接说明_current.md
90_候选工作区/交接说明_history.md
```

完成上述三项最小补丁。

不建立新 contract/schedule ID，不复制另一套 harness。完成后重新生成零步静态证据并停止交 Pro。

## 规定测试

至少新增：

```text
authorized preclaim failure → evidence retained + git clean + zero claim
same C2 second valid preflight → PASS

concurrent two analyzers → loser no global failure
second analyser after success → side-effect-free refusal
pre-existing analysis failure → no success
stale staging / terminal / rename / reload fault → exactly one terminal class

valid current C2/P2/claim/parser → PASS
HEAD matches claim but P2/C2 invalid → FAIL before output
claim ticket/blob/snapshot drift → FAIL before output
wrong Python/NumPy parser identity → FAIL before output
```

## 返回复核前必须保持

```text
HF3D time_steps_advanced = 0
claims consumed = 0
warmup/timed/profile started = 0
V2 performance raw absent
compact evidence absent
analysis failure absent
P2 ticket absent
C2 grant absent
P1 not implemented
formal source unchanged
```

## 禁止

```text
自行签发 P2/C2
authorization=true
--execute
任何 warm-up/timed/profile
动态 analyser
P1
02_模拟程序/** 修改
放宽环境、质量、数值同一、覆盖率或事务门
删除或重写 V1/E0 证据
```

## 结论上限

```text
P0-P V1 measured result = NOT AVAILABLE
E0 = ACCEPTED
P0-P2 zero-step = ACCEPTED
P0-P2 V2 original patch = PARTIAL_PASS
P0-P2 V2 final patch = AUTHORIZED_NOT_YET_IMPLEMENTED
P0-P2 V2 dynamic execution = NOT_AUTHORIZED
P1 = PROHIBITED
hardware utilization baseline = NOT DEMONSTRATED
numerical qualification = NOT DEMONSTRATED
physical validation = NOT PERFORMED
experimental validation = NOT PERFORMED
apparatus prediction = DISALLOWED
```
