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
HF3D_P0_P_REVIEW_BINDING_MATERIAL = ACCEPTED
HF3D_P0_P_EXACT_AUTHORIZATION = ISSUED
HF3D_P0_P_DYNAMIC_SCHEDULE = AUTHORIZED_ONCE_NOT_STARTED
HF3D_P0_P_EXECUTION_CLAIM = NOT_CREATED
HF3D_P0_P_DYNAMIC_OUTPUT = ABSENT
HF3D_P1_IMPLEMENTATION = PROHIBITED

PROJECT_REFACTOR = TARGETED_CROSS_MODULE_REFACTOR
PROJECT_WIDE_REWRITE = REJECTED
HF3D_HARDWARE_UTILIZATION = INSUFFICIENT
PRIMARY_ACCELERATION_ROUTE = EXPLICIT_GPU_RESIDENT_E1_BACKEND
REFERENCE_BACKEND = RETAINED
```

## 最新 Pro 裁决

```text
90_候选工作区/current/22_P0P绑定材料复核与动态执行裁决.md
```

绑定实现快照：

```text
I commit: e3b2a1b794b1bd46113d496550e0ce2970ba0a5d
I tree: 2e24c41acf7ce71ee3c8979aaad888bbfe126d53
review material blob: c91c95e7229a98c48ad2d5511772515321110101
review material SHA-256: 1977454018e11010f0afd6d50d91b688cf71b8c93b06c340527b450e8ec64133
runtime reference: 8a0c2fa564d9d6abfd84e5620379af11e5f858d3
formal source SHA-256: dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8
```

## 签名事务

当前远端 `main` 必须保持为父票据 `P` 的唯一授权子提交 `C`：

```text
I -> P -> C
```

父票据：

```text
ticket_id: HF3D_P0P_PRO_TICKET_E3B2A1B7_20260901_V1
ticket_path: 90_候选工作区/current/17_HF3D性能优化/p0p_authorization_ticket.json
fixed schedule: HF3D_P0P_FIXED_18_V1
fixed schedule SHA-256: 2138aea65041ff322a0cc2ce22945544d51d1e39db1929079f9f6265e494dceb
```

授权子 blob：

```text
Git blob: 703c38e1d40883028e94e23bd5f90c7f0b083a69
SHA-256: a8ad3f747171245772d57ebf245c98eabb72d76ec2c9aabb9ee5390998336965
byte length: 860
grant_nonce: 0aaf03ac79d9004f9b659c9f0e56883eb59aaafbbd7c3613befb0b0963ce872c
```

`P..C` 只能修改：

```text
90_候选工作区/current/17_HF3D性能优化/p0p_execution_authorization.json
```

不得在执行前再提交任何文件。任何后续提交都会使授权拓扑审计失败。

## 已完成复核

Pro 已独立确认：

- Codex 提交只涉及 review-binding 生成器、材料、专项测试和交接；
- 24 个 runtime 文件在当前实现快照与 `8a0c2fa...` 上 mode/blob 完全一致；
- static audit、P0 contract、三份 P0-D、capability、denied authorization 和 formal source 均已精确绑定；
- 固定 18 项 schedule 的 canonical SHA-256 可独立重算；
- ticket/grant/claim/raw 在材料生成阶段不存在；
- HF3D 时间步、warm-up、scored、profile 计数均为 0；
- review material 无当前提交、当前树或未来 `P/C` 自引用；
- 现有 P0-P runtime 无需再次修改。

本次签名事务没有启动动态样本，也没有修改正式理论、正式程序、D3、P0 合同、P0-D 或既有 raw。

## 下一项唯一任务：本机非消费 preflight，然后一次性执行

先同步且不得产生本地修改：

```bash
git pull --ff-only
git status --short
git rev-parse HEAD
git rev-parse origin/main
```

必须确认：

```text
HEAD == origin/main
worktree/index clean
main remains exact authorization child C
no P0-P claim/raw/ledger/complete/failure output exists
```

运行非消费 preflight：

```bash
python3 90_候选工作区/current/17_HF3D性能优化/run_p0_performance_campaign.py --zero-step-preflight
```

若 preflight 失败：

```text
原样停止
不得修改授权文件
不得提交新 commit
不得创建 claim
不得启动任何样本
将完整失败输出交 Pro
```

若 preflight 通过，且用户明确要求执行，则只允许一次：

```bash
python3 90_候选工作区/current/17_HF3D性能优化/run_p0_performance_campaign.py --execute
```

执行器会在首次样本环境门通过后原子创建唯一 claim。Claim 后任何失败均为永久 `FAILED_STOPPED_NO_RESUME`，禁止重试、恢复、补样、替换或删除失败证据。

## 环境硬门

必须继续满足：

```text
AC power online
32 logical CPUs online
governor records complete and equal
no competing GPU compute process
background CPU load within frozen threshold
CPU Tctl <= 90 C
GPU temperature <= 75 C
toolchain and GPU identity exact
```

此前约 `96.875 C` 的 Tctl 必须继续被拒绝。线程环境变量不能替代这些门。

## 当前禁止

```text
执行前任何新 commit
任何 P1 实现
任何 02_模拟程序/** 修改
任何 D3R1 重跑或 S3
任何 schedule 改动
任何阈值或科学门放宽
任何旧 raw 复用
任何未通过 preflight 的动态执行
```

## 结论上限

```text
HF3D_P0_P_DYNAMIC_SCHEDULE = AUTHORIZED_ONCE_NOT_STARTED
HF3D_P0_P_RESULT = NOT_AVAILABLE
NUMERICAL_QUALIFICATION = NOT_DEMONSTRATED
PHYSICAL_VALIDATION = NOT_PERFORMED
EXPERIMENTAL_VALIDATION = NOT_PERFORMED
APPARATUS_PREDICTION = DISALLOWED
```
