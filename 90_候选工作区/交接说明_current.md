# 当前交接

当前状态：

```text
HF3D_P0P_V1_CLAIM = CONSUMED
HF3D_P0P_V1_SCHEDULE = TERMINAL_FAILED_STOPPED_NO_RESUME
HF3D_P0P_V1_HF3D_TIME_STEPS = 0
HF3D_P0P_V1_MEASURED_PERFORMANCE_RESULT = NOT_AVAILABLE

HF3D_P0P2_E0_FAILURE_EVIDENCE_REGISTRATION = COMPLETE
HF3D_P0P2_I2_ZERO_STEP_IMPLEMENTATION = STATIC_COMPLETE_AWAITING_PRO_REVIEW
HF3D_P0P2_STATIC_QUALIFICATION_GATE = PASS
HF3D_P0P2_RUNTIME_GATE = FALSE_DEFAULT_DENIAL
HF3D_P0P2_DYNAMIC_EXECUTION = NOT_AUTHORIZED
HF3D_P1 = PROHIBITED

D3R1_S2 = FAILED_STOPPED
D3R1_SCIENTIFIC_CLASSIFICATION = NOT_FORMED
```

I2 已在候选区完成实现、真实 CUDA 三算例零步构造、静态证据固化和独立重算。当前必须停止交 Pro 独立复核。没有父票据、授权 grant、dynamic raw、lock、claim、ledger、sample、warm-up、timed 或 profile 运行；不得把静态 PASS 写成动态执行 PASS 或性能结果。

## 上一份 Pro 裁决与 V1 终态

上一份有效裁决为：

```text
commit = 0350fc6653988af6dded2bbc2c0cfce79de7631a
90_候选工作区/current/23_P0P一次性执行失败独立复核与P0P2协议裁决.md
```

Pro 将 V1 失败裁定为候选性能协议在 claim 前本可静态判定、但被旧门集漏检的派生配置错误：

```text
run.duration_s = 0.002
drive.ramp_duration_s = 0.005
ConfigError: drive ramp duration cannot exceed run duration
```

该失败发生在 HF3D 构造前，不是 HF3D 数值、物理、CUDA、压力、环境或性能结果。旧 claim 已消费，旧日程永久终止；旧 ledger、失败目录、首项失败和剩余 17 个 `NOT_STARTED` 项禁止恢复、重跑、补样、替换、删除或改写。

## E0：V1 失败证据登记已完成

E0 位于：

```text
90_候选工作区/current/24_P0P失败证据登记与P0P2零步实现/evidence/
90_候选工作区/current/24_P0P失败证据登记与P0P2零步实现/register_v1_failure_evidence.py
90_候选工作区/current/24_P0P失败证据登记与P0P2零步实现/tests/test_v1_failure_evidence_registration.py
```

登记器把旧 ignored raw 的完整常规文件树逐字节复制为新的跟踪 snapshot，并在复制前后独立只读扫描原目录。它拒绝 symlink、hardlink、device、socket、FIFO、路径逃逸和规范化重复路径。

冻结身份：

```text
E0 commit = 379ae8dfb425c2f57e25d2779525fc843592fc98
regular files = 15
directories below root = 1
total file bytes = 52509
source canonical tree SHA-256
= 21ef41a6acc791c01da67b751cbaeaabfdaa94ee1d3a860948f0700b7dd4e541
snapshot canonical tree SHA-256
= 21ef41a6acc791c01da67b751cbaeaabfdaa94ee1d3a860948f0700b7dd4e541
manifest SHA-256
= 0c2e84e117970cc34ad9ae0793bb5f6edf17090d1da3d064e42ed0abfd3a3e6a
audit SHA-256
= 2ffb6cc8e2286585604e3be5885f403ef6783fa13b00f1c924251b5b9a1a5860
```

E0 snapshot 只能作为失败证据和 P0-P2 负例，不能作为运行输入、完成前缀、warm-up、计时结果或 claim 配额。

## I2：P0-P2 零步实现已完成

实现与规范静态证据均位于：

```text
90_候选工作区/current/24_P0P失败证据登记与P0P2零步实现/**
```

核心对象：

```text
p0p2_contract.py
p0p2_contract.json
p0p2_execution_authorization.json
generated_effective_configs/P0P2_B1.json
generated_effective_configs/P0P2_B2.json
generated_effective_configs/P0P2_B3.json
build_p0p2_contract.py
p0p2_authorization.py
p0p2_environment.py
p0p2_zero_step_probe.py
run_p0p2_preflight.py
audit_p0p2_zero_step.py
tests/test_p0p2_authorization.py
tests/test_p0p2_contract_and_preflight.py
tests/test_p0p2_environment.py
artifacts/p0p2/static/**
```

新身份与固定日程：

```text
contract ID = HF3D_P0P2_ZERO_STEP_AND_FUTURE_PERFORMANCE_BASELINE_V1
schedule ID = HF3D_P0P2_FIXED_18_V1
schedule canonical SHA-256
= 638dfb0b884ec08cd68faca3948d71bd1180c39081449c8ed1157d19d2a70a14
authorization ID = HF3D_P0P2_DYNAMIC_EXECUTION_AUTHORIZATION_V1
claim namespace = HF3D_P0P2_SINGLE_SCHEDULE_CLAIM_V1
ledger schema = HF3D_P0P2_FIXED_18_LEDGER_V1
nonce prefix = HF3D_P0P2_GRANT_NONCE_
sample prefix = P0P2_
```

冻结有效配置：

```text
run.duration_s = 0.005
drive.ramp_duration_s = 0.005
output_interval_s = 0.001
registered times = [0, 0.001, 0.002, 0.003, 0.004, 0.005]
only allowed source-config change = run.duration_s

P0P2_B1 effective canonical SHA-256
= e694ea2b4c3b1a686e066cb7f6a919367e7bd1a340f0738964ce4e157389f00b
P0P2_B2 effective canonical SHA-256
= d51aed9bb2e90af300d980b00324214cf991aa483c15f4a46a8b2b669d8731c4
P0P2_B3 effective canonical SHA-256
= 2c3ac702a5e85762eecf2f15bb908f538c697058604d1e5edddad051564618e3
```

合同生成文件与默认拒绝授权对象的文件 SHA-256：

```text
p0p2_contract.json
= 0b89cd355ff7d8087f306903ca7acef8f2df242d47550f74aaf4e30cf207cbef
p0p2_execution_authorization.json
= 4a5ef77216179bdf6f973890efacefbfd5e24faa38beef22c130c100b31f66a7
```

运行输入审计不仅拒绝旧 V1/E0 路径，也拒绝把旧文件复制或改名后以相同字节内容输入。新授权严格实现默认拒绝和未来 `I2 -> P2 -> C2` 顺序：P2 父票据须绑定完整 E0、I2、三份来源/有效配置、正式 round-trip、fresh-process probe、统一 preflight、能力身份和静态审计；C2 才可在唯一子提交中修改授权对象。P2 和 C2 均尚未发生。

统一 runtime preflight 只有一个顶层 `gate_pass`。当前 exact denial 下的预期且实测状态为：

```text
status = HF3D_P0P2_ZERO_STEP_STATIC_PASS_DYNAMIC_EXECUTION_DENIED
gate_pass = false
static_qualification.gate_pass = true
hf3d_time_steps_advanced = 0
```

任何 runtime 门失败都必须在创建新 raw、lock、claim、ledger 或 sample 目录之前停止。

## 已固化的零步与环境证据

独立静态审计状态：

```text
HF3D_P0P2_ZERO_STEP_IMPLEMENTATION_STATIC_AUDIT_PASS
```

30 秒环境观察通过：

```text
elapsed = 30.000241199 s
background CPU = 15.199877793489202 %
background CPU gate = 25 %
other compute process = none
CPU Tctl = 77.875 -> 55.625 C
GPU temperature = 52 -> 48 C
```

三个探针均为 fresh process、精确有效配置、实际 CUDA、无 CPU fallback，并完成网格、压力、初态和零步诊断构造后显式清空 CuPy device/pinned memory pools：

```text
P0P2_B1:
  grid = 90 x 90 x 80
  elapsed = 0.914137654 s
  projection allocation lower bound = 136314880 bytes
  hf3d_time_steps_advanced = 0

P0P2_B2:
  grid = 136 x 136 x 120
  elapsed = 4.827688032 s
  projection allocation lower bound = 493158400 bytes
  hf3d_time_steps_advanced = 0

P0P2_B3:
  grid = 136 x 136 x 120
  elapsed = 4.777108330 s
  projection allocation lower bound = 467533824 bytes
  hf3d_time_steps_advanced = 0
```

以上只是构造能力与零步身份门，不是端到端性能基线，不证明 CPU/GPU 利用充分，也不形成数值或物理资格。

规范静态证据 SHA-256：

```text
p0p2_static_audit.json
= 01dc77c44e2d127b0a4cf2f71f8b4bd09ac742c9aae2bf02c0c25202121461c9
p0p2_preflight_report.json
= 7353939c78957404c48a1d9af33595d5489733ef2eb8ec2814111b5e25807ca9
p0p2_capability_identity.json
= 112420d1d0b3c9a2fc99a7a1703b0417854b4bae0015220678e89147b4e59819
```

三份正式配置往返报告与三份零步报告分别固化在：

```text
artifacts/p0p2/static/formal_round_trip_reports/
artifacts/p0p2/static/probe_reports/
```

## 已执行验证

- E0 与 I2 新增专项测试：`38/38` 通过。
- Ruff format check 与 lint：通过。
- strict mypy：10 个 I2/E0 Python 文件通过。
- Python bytecode 编译：通过。
- P0-P2 合同确定性重建 `--check`：通过。
- E0 失败证据独立重算 `--check`：通过。
- P0-P2 零步静态证据独立重建 `--check`：通过。
- 24 个跟踪 JSON 严格解析：通过。
- 当前交接的 Pandoc Markdown 解析：提交前执行并记录；其他候选 Markdown 按用户最新要求不检查格式。

补充回归中，旧只读 `17_HF3D性能优化/tests` 共收集 38 项，其中 4 项在旧 fixture/setup 处报错。它们要求旧 V1 授权对象仍是 pre-authorization/default-denial 且绑定旧 `8a0c2fa` 快照；仓库当前冻结的旧 V1 对象已经是 Pro 授权后、claim 已消费的终态。旧 `17_HF3D性能优化/**` 与当前 HEAD 逐字节一致，本轮未修改。该结果必须保留为历史测试与终态证据不兼容，不能伪报为 PASS，也不构成 I2 新实现的专项测试失败。

## 未执行与禁止事项

- 未签发 P2 父票据或 C2 grant，未把授权改为 true。
- 未创建 P0-P2 dynamic raw、lock、claim、ledger 或 sample。
- 未运行任何 warm-up、timed、profile 或 Nsight 性能样本。
- 未恢复、重跑或修改 V1 失败证据。
- 未运行 P1，未推进 D3R1 科学分类。
- 未修改 `01_最终理论/**`、`02_模拟程序/**`、D3、旧 P0-P 或 Pro 裁决。
- 未形成端到端加速比、CPU/GPU 常驻利用率、数值等价性、数值资格、物理验证、实验验证或装置预测结论。

## 下一步：只交 Pro 独立复核与裁决

Pro 应从 GitHub `main` 读取本次 I2 实现提交和本交接，独立重算 E0/I2 身份、静态证据、负例、default denial、零步实际 CUDA 构造、环境门和 `I2 -> P2 -> C2` 拓扑，再决定是否签发父票据。Codex 当前不得自行签发 P2/C2，也不得开始动态性能日程。

请 Pro 同时基于项目现状独立判断以下两项，不预设审查范围或实现路径：

1. 全项目技术债是否已经需要局部重构或较大范围重构，以及当前最合理的先后顺序。
2. 当前模拟程序是否充分利用本机硬件；若没有，应如何在真实瓶颈、数值等价性、CPU/GPU 实测利用率和端到端加速比约束下规划优化。若停止正在运行的任务并优化能显著提速，应明确安全停机、未完成证据登记和恢复计算的合法门槛。

只有 Pro 形成完整最终裁决并实际写入 GitHub 后，Codex 才可拉取并按新授权继续。思考摘要、流式中间文字或回答控件暂时消失均不视为完成；必须按内容语义确认裁决完整。

## 当前允许修改范围

在新 Pro 裁决前，没有新的实现修改权限。现有 I2 交付只允许提交并推送以下路径：

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
P0-P2 implementation = STATIC_COMPLETE_AWAITING_PRO_REVIEW
P0-P2 static qualification = PASS
P0-P2 runtime gate = FALSE_DEFAULT_DENIAL
P0-P2 dynamic execution = NOT_AUTHORIZED
P1 = PROHIBITED
numerical qualification = NOT DEMONSTRATED
physical validation = NOT PERFORMED
experimental validation = NOT PERFORMED
apparatus prediction = DISALLOWED
```
