# 当前交接

当前状态：

```text
HF3D_P0P_V1_CLAIM = CONSUMED
HF3D_P0P_V1_SCHEDULE = TERMINAL_FAILED_STOPPED_NO_RESUME
HF3D_P0P_V1_HF3D_TIME_STEPS = 0
HF3D_P0P_V1_MEASURED_PERFORMANCE_RESULT = NOT_AVAILABLE

HF3D_P0P2_E0_FAILURE_EVIDENCE_REGISTRATION = COMPLETE
HF3D_P0P2_I2_ZERO_STEP_IMPLEMENTATION = AUTHORIZED_NOT_YET_PERFORMED
HF3D_P0P2_DYNAMIC_EXECUTION = NOT_AUTHORIZED
HF3D_P1 = PROHIBITED

D3R1_S2 = FAILED_STOPPED
D3R1_SCIENTIFIC_CLASSIFICATION = NOT_FORMED
```

## 最新 Pro 裁决

```text
commit = 0350fc6653988af6dded2bbc2c0cfce79de7631a
90_候选工作区/current/23_P0P一次性执行失败独立复核与P0P2协议裁决.md
```

Pro 将 V1 失败裁定为候选性能协议的 claim 前本可静态判定、但被旧门集漏检的派生配置错误：

```text
run.duration_s = 0.002
drive.ramp_duration_s = 0.005
ConfigError: drive ramp duration cannot exceed run duration
```

这不是 HF3D 数值、物理、CUDA、压力、环境或性能结果。旧 claim、ledger、失败目录和剩余 17 个 `NOT_STARTED` 项永久封存，禁止恢复、重跑、补样、替换、删除或改写。

## E0 已完成：V1 失败证据跟踪登记

实现与证据位于：

```text
90_候选工作区/current/24_P0P失败证据登记与P0P2零步实现/register_v1_failure_evidence.py
90_候选工作区/current/24_P0P失败证据登记与P0P2零步实现/tests/test_v1_failure_evidence_registration.py
90_候选工作区/current/24_P0P失败证据登记与P0P2零步实现/evidence/v1_claim_failure_snapshot/
90_候选工作区/current/24_P0P失败证据登记与P0P2零步实现/evidence/v1_failure_evidence_manifest.json
90_候选工作区/current/24_P0P失败证据登记与P0P2零步实现/evidence/v1_failure_evidence_audit.json
```

登记器在复制前和复制后分别以只读、无符号链接跟随的方式扫描旧 raw，拒绝 symlink、hardlink、device、socket、FIFO、路径逃逸与规范化重复路径，并把完整常规文件树逐字节复制到新的跟踪 snapshot。

规范身份：

```text
regular files = 15
directories below root = 1
total file bytes = 52509
source canonical tree SHA-256
= 21ef41a6acc791c01da67b751cbaeaabfdaa94ee1d3a860948f0700b7dd4e541
snapshot canonical tree SHA-256
= 21ef41a6acc791c01da67b751cbaeaabfdaa94ee1d3a860948f0700b7dd4e541
```

五个冻结锚点均由源与副本独立重算一致：

```text
execution_claim.json
= ecd7cf0e9dfa4e3d609f22e205c95261cc1505a255776dc56a29b3532ad94d80

schedule_ledger.json
= 089d91578f89d083f3552f4dfdfaac464bc32842a4f7945f13e9e00903817948

execution_failure.json
= 749ecd452c3958e6320de4e39fe9ea04d2cf4aadd8e4488f105a1cbdc5ab5262

WARMUP_B1/sample_failure.json
= 81145152ab792a763596cbdc4859040fee47c31cc0546f033d7805cdfa2cee1b

WARMUP_B1/worker.stderr.log
= 944c1cdd60c47f9315d20453e27a541c07f6cf98c5f8419d2b6d79d28d21e894
```

机器语义审计确认：

- `WARMUP_B1` 为 `sequence_index=0 / start_count=1 / FAILED_STOPPED`；
- 其余 17 项均为零启动的 `NOT_STARTED`；
- `completed_samples=[]`，completion、sample manifest、partial state 和 archive 均缺失；
- worker exit 2，严格单行错误为正式 `ConfigError`，`partial_evidence=null`，stdout 为零字节；
- 有效配置冲突精确为 `0.002/0.005`；
- 根据配置构造前失败路径、空 stdout、null partial 和动态结果文件缺失，HF3D 接受时间步为 0；
- 所有 `.json` 与非空 `.jsonl` 记录严格解析；
- 原 ignored raw 在登记前后 path、POSIX mode、size、SHA-256 与 canonical tree 均完全一致。

E0 专项测试为 `11/11` 通过。跟踪 snapshot 只是失败证据，不得作为 P0-P2 的运行输入、完成前缀、warm-up、计时结果或 claim 配额。

## 下一项唯一任务：I2 零步实现

只允许继续在下列目录实现新的 P0-P2：

```text
90_候选工作区/current/24_P0P失败证据登记与P0P2零步实现/**
```

新的机器身份必须与 V1 全部分离：

```text
new contract ID
new schedule ID and canonical hash
new default-denial authorization ID
new future ticket/grant/nonce namespace
new raw root
new claim/ledger schema and sample IDs
new effective-config hashes
```

冻结短窗：

```text
run.duration_s = 0.005
drive.ramp_duration_s = 0.005  # 保持源配置，不修改
output_interval_s = 0.001
registered times = [0, 0.001, 0.002, 0.003, 0.004, 0.005]
only allowed source-config change = run.duration_s
```

Claim 前必须对 B1/B2/B3 全部执行：

```text
source hash verification
only run.duration_s changed
SimulationConfig.from_dict(effective_payload)
exact config.to_dict() round-trip
canonical effective-config SHA-256 binding
drive/schedule/output compatibility
fresh-process exact-case zero-step construction
actual CUDA without fallback
grid + pressure + initial state + zero-step diagnostics
hf3d_time_steps_advanced = 0
```

新 preflight 的单一顶层 `gate_pass` 必须合并合同、E0 证据、默认拒绝授权、正式来源、有效配置、fresh-process 零步构造、新 raw 卫生和环境全部门。任何失败必须在 lock、raw、claim、ledger 或 sample 目录产生前停止。

I2 必须包含 V1 `0.002/0.005` 负例、P0-P2 `0.005/0.005` 正例、任一 benchmark 非法导致整项日程失败、额外字段修改、source/effective hash 漂移、新旧 raw 重叠、旧 V1 claim/ledger/sample 输入、preflight 副作用和 V1 ledger 恢复等拒绝测试。

完成 I2、静态证据、测试和交接后提交、推送并停止交 Pro。当前不授权签发父票据、不授权把授权改为 true、不授权任何 warm-up/timed/profile 样本。

## 允许修改

```text
90_候选工作区/current/24_P0P失败证据登记与P0P2零步实现/**
90_候选工作区/交接说明_current.md
90_候选工作区/交接说明_history.md
```

其余路径只读，尤其：

```text
90_候选工作区/current/17_HF3D性能优化/**
90_候选工作区/current/07_D3跨网格时间步响应诊断/**
01_最终理论/**
02_模拟程序/**
AGENTS.md
90_候选工作区/current/23_P0P一次性执行失败独立复核与P0P2协议裁决.md
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
