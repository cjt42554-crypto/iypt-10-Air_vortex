# 当前交接

当前结论：Codex 在 `bbe5531ee4cdcc393f84d3309e79183bd904c415` 实现的 ledger transition 补丁，已经关闭普通 `OSError` 在原子替换前后造成的提交不确定性；但 Pro 独立复核又发现两个执行边界阻断，因此未签发 P2 父票据，也未生成 C2 授权子提交。

```text
HF3D_P0P_V1_CLAIM = CONSUMED
HF3D_P0P_V1_SCHEDULE = TERMINAL_FAILED_STOPPED_NO_RESUME
HF3D_P0P_V1_MEASURED_PERFORMANCE_RESULT = NOT_AVAILABLE

HF3D_P0P_V1_E0_FAILURE_EVIDENCE_REGISTRATION = ACCEPTED
HF3D_P0P2_I2_EFFECTIVE_CONFIG_AND_ZERO_STEP_QUALIFICATION = ACCEPTED

HF3D_P0P2_V2_NONCONSUMING_PREFLIGHT_RETRY_PATCH = ACCEPTED
HF3D_P0P2_V2_ANALYSIS_AUTHORIZATION_AND_TOOLCHAIN_REBINDING = ACCEPTED
HF3D_P0P2_V2_ANALYSIS_UNIQUE_TERMINAL_TRANSACTION = ACCEPTED
HF3D_P0P2_V2_ORDINARY_EXCEPTION_LEDGER_TRANSITION_PATCH = ACCEPTED

HF3D_P0P2_V2_CONTROL_FLOW_INTERRUPT_SEMANTICS = BLOCKING
HF3D_P0P2_V2_DURABLE_CLAIM_OUTER_GATE = BLOCKING
HF3D_P0P2_V2_INTERRUPT_AND_CLAIM_BOUNDARY_PATCH = REQUIRED

HF3D_P0P2_V2_PARENT_TICKET = NOT_ISSUED
HF3D_P0P2_V2_AUTHORIZATION_CHILD = NOT_ISSUED
HF3D_P0P2_V2_DYNAMIC_EXECUTION = NOT_AUTHORIZED
HF3D_P1 = PROHIBITED

PROJECT_REFACTOR = TARGETED_CROSS_MODULE_REFACTOR_AFTER_BASELINE
PROJECT_WIDE_REWRITE = REJECTED
HF3D_HARDWARE_UTILIZATION = INSUFFICIENT
TRUSTWORTHY_P0P2_BASELINE = NOT_AVAILABLE
```

## 当前裁决入口

```text
90_候选工作区/current/29_P0P2V2账本补丁复核与控制流中断门裁决.md
```

该裁决绑定：

```text
reviewed implementation commit = bbe5531ee4cdcc393f84d3309e79183bd904c415
reviewed tree = 24e65718982fc2652a61efd0c5c1cca42012aaf0
previous ruling commit = a18aa007c6be7bf2a7a9216bcd3d45939403a7bc
formal source SHA-256 = dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8
submitted static audit SHA-256 = 23b801e0a330472490af4287323427cd5f600fa4dc5d01d90f56417fa6577a07
submitted contract SHA-256 = f0b172a1eaed05ce365b56ab7862ab9f7472f94c0647f5066c41404aafb88561
```

## 已接受并保留的实现

`_commit_ledger_transition()` 对普通 `Exception`/存储故障的主体设计可以保留：

```text
candidate 在写前只构造一次
写前要求 durable ledger == expected_before
每次发布后严格重载
observed candidate -> 按已提交恢复
observed exact old -> 同一候选最多重试一次
observed third state -> 失败闭锁
RUNNING token/timestamp/start_count 不重复生成
ordinary PASS 与 final pending-terminal 共用提交判定
outer handler 可从 durable ledger 重建 PASS 前缀和 RUNNING identity
```

现有普通 OSError 矩阵、同一候选、唯一 token、`start_count = 1`、无重复 worker、completion XOR failure 等测试继续作为回归门。

## 剩余阻断一：不得吞掉控制流中断

当前 transition helper 使用：

```python
except BaseException:
```

这会把 `KeyboardInterrupt`、`SystemExit`、`GeneratorExit` 等控制流信号当成可重试存储故障。

后果包括：

```text
RUNNING pre-commit interrupt -> helper 可能重试并在中断后启动 worker
RUNNING post-commit interrupt -> 中断被转成成功返回并启动 worker
ordinary PASS post-commit interrupt -> 继续下一样本
final pending post-commit interrupt -> 继续发布 completion
```

必须把可重试异常收窄为普通 `Exception` 或更窄的存储 allowlist。非 `Exception` 的控制流 `BaseException` 必须立即向 outer handler 传播；outer handler 再读取 durable ledger 并形成 FAILED_STOPPED，不得启动 worker、下一样本或 completion。

## 剩余阻断二：durable claim 不能由本地 `claimed` 布尔值决定

当前顺序为：

```python
claimed = False
create_claim_and_ledger(...)
claimed = True
```

若真实 claim 和 ledger 已持久化，但异常发生在函数返回边界或 `claimed = True` 之前，outer handler 会因为本地 `claimed == False` 跳过终态化，留下：

```text
valid claim
valid CLAIMED_NOT_STARTED ledger
no execution_complete
no execution_failure
```

必须以持久对象为权威：异常边界只要能严格加载合法绑定的 claim+ledger，且没有既有合法 terminal，就必须调用 consumed-claim terminalization；本地 `claimed` 最多只能作为诊断字段。

## 下一项唯一任务

只在现有 P0-P2 V2 身份内完成一次最小控制流补丁，不复制 V3，不改变协议 ID、固定 18 项日程、配置、质量门、profiler、authorization/claim namespace 或 raw root。

允许修改：

```text
90_候选工作区/current/24_P0P失败证据登记与P0P2零步实现/p0p2v2_consumption.py
90_候选工作区/current/24_P0P失败证据登记与P0P2零步实现/run_p0p2v2_campaign.py
90_候选工作区/current/24_P0P失败证据登记与P0P2零步实现/p0p2v2_contract.py
90_候选工作区/current/24_P0P失败证据登记与P0P2零步实现/p0p2v2_contract.json
90_候选工作区/current/24_P0P失败证据登记与P0P2零步实现/tests/test_p0p2v2_authorization_and_consumption.py
90_候选工作区/current/24_P0P失败证据登记与P0P2零步实现/audit_p0p2v2_harness.py
90_候选工作区/current/24_P0P失败证据登记与P0P2零步实现/artifacts/p0p2v2/static/**
90_候选工作区/交接说明_current.md
90_候选工作区/交接说明_history.md
```

必须新增 orchestrator 级故障注入：

```text
KeyboardInterrupt + SystemExit
x RUNNING pre/post commit
x ordinary PASS pre/post commit
x final pending-terminal pre/post commit
```

硬门：

```text
control-flow exception publication attempt count = 1
RUNNING interrupt -> zero worker launch
ordinary PASS interrupt -> no next sample
final pending interrupt -> no completion
control exception not converted to success return
unique coherent FAILED_STOPPED + execution_failure
```

还必须覆盖：

```text
real create_claim_and_ledger succeeds, then wrapper raises OSError before caller flag assignment
real create_claim_and_ledger succeeds, then wrapper raises KeyboardInterrupt before caller flag assignment
no durable claim/ledger -> 不伪造 consumed-claim failure
existing initialization failure -> 不覆盖
```

前两个案例必须形成有效 claim、FAILED_STOPPED ledger、唯一 execution failure，且样本启动数为 0。

## 返回 Pro 前必须重跑

从空 V2 static 根执行：

```text
完整 authorization/consumption tests
完整 harness tests
完整 Git topology tests
Ruff
strict mypy
Python compile
contract deterministic regeneration
allocator probes
qualified Nsight probe/export/parser
B1/B2/B3 fresh-process real CUDA zero-step probes
post-probe 30 s environment gate
最终 static audit rebuild
```

随后立即停止交 Pro。

## 必须保持的零执行状态

```text
HF3D time_steps_advanced = 0
claims consumed = 0
warmup started = 0
scored/timed started = 0
profile started = 0
V2 performance raw = absent
campaign lock = absent
compact evidence = absent
analysis failure = absent
P2 ticket = absent
C2 grant = absent
P1 implementation = absent
formal source = unchanged
```

## 禁止事项

```text
签发或伪造 P2 ticket
签发或伪造 C2 grant
authorization=true
--execute
任何 warm-up / timed / profile
动态 analyser
P1
修改 01_最终理论/**
修改 02_模拟程序/**
修改 D3、V1 raw 或 E0 snapshot
放宽环境、质量、数值同一、覆盖率或事务门
```

## 技术债与硬件利用结论

项目继续采用定向跨模块重构，不整体推倒重写。当前缺口位于候选性能 harness 控制流，不证明正式 HF3D 架构需要大范围重构。

既有真实瓶颈结论不变：CPU 长期约一个逻辑核，GPU 多数采样约 `16%-22%`，压力求解存在逐步 host-device transfer。当前零步证据不构成可信性能 baseline。P0-P2 合法完成后，优先规划保留 reference backend 的 GPU-resident E1 候选；验收必须同时依赖数值等价、同机端到端墙钟、CPU/GPU 利用率、传输时间线和实际加速比，不能把线程环境变量或瞬时峰值当作性能证明。

## 当前结论上限

```text
P0-P V1 measured result = NOT AVAILABLE
E0 failure evidence = ACCEPTED
P0-P2 zero-step qualification = ACCEPTED
P0-P2 ordinary storage-fault ledger patch = ACCEPTED
P0-P2 control-flow/claim boundary = BLOCKING
P0-P2 dynamic execution = NOT AUTHORIZED
P1 = PROHIBITED
hardware utilization baseline = NOT DEMONSTRATED
numerical qualification = NOT DEMONSTRATED
physical validation = NOT PERFORMED
experimental validation = NOT PERFORMED
apparatus prediction = DISALLOWED
```
