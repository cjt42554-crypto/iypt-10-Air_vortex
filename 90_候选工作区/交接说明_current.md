# 当前交接

当前状态：

```text
D3R1_S2 = FAILED_STOPPED
D3R1_DIRECT_TRIGGER = PROJECTED_DIVERGENCE
D3R1_FAILURE_MECHANISM = NOT_YET_ESTABLISHED
D3R1_S3 = NOT_EXECUTED
D3R1_SCIENTIFIC_CLASSIFICATION = NOT_FORMED

HF3D_P0_D = COMPLETED
HF3D_P0_P_PLAN = FROZEN
HF3D_P0_P_AUTHORIZATION_SELF_REFERENCE_DEFECT = CONFIRMED
HF3D_P0_P_AUTHORIZATION_STRUCTURE = PARENT_ANCHORED_EXACT_BLOB_GRANT
HF3D_P0_P_ZERO_STEP_AUTHORIZATION_REFACTOR = AUTHORIZED
HF3D_P0_P_DYNAMIC_SAMPLES = NOT_AUTHORIZED_PENDING_NEW_STATIC_REVIEW
HF3D_P1_IMPLEMENTATION_AND_FORMAL_SOURCE_MODIFICATION = NOT_AUTHORIZED
```

## 最新 Pro 裁决

详细裁决：

```text
90_候选工作区/current/20_HF3DP0P无自引用精确授权结构裁决.md
```

复核基线：

```text
reviewed main: d12078f996092c82f46936b594c7d6d645ccdda0
P0 contract blob: 62f256574f67654d13f588e0505f63505d065907
formal source tree: dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8
P0-D: completed
P0-P samples: not started
```

## 自引用问题的裁决

`p0p_execution_authorization.json` 不得要求预先写入或校验其所在授权子提交的最终 commit hash。该文件一旦包含候选 child hash，就会改变自身 blob、tree 和 child commit hash，形成不可用的 Git 固定点问题。

以下字段和方案明确禁止：

```text
authorization_child_commit
self_commit_hash
self_blob_hash
先写 placeholder/null 后回填
循环 amend 求稳定 hash
运行时修改授权 JSON
用 grandchild receipt 代替直接授权 child
```

实际授权 child commit hash只能在 schedule 开始后写入动态 `execution_claim.json`、sample manifests、完成/失败记录和后续交接，不能先验写入 tracked 授权文件。

## 选定的无自引用结构

未来授权采用：

```text
I = Codex 零步实现和静态证据提交
P = Pro 已复核父提交
C = 唯一授权子提交
Q = 运行时一次性 execution claim

I -- P -- C
```

### I：零步实现

包含：

```text
完整 P0-P harness/worker/monitors/parsers/allocator observers
静态审计和合成测试
默认拒绝 p0p_execution_authorization.json
```

不得包含允许态票据或动态样本。

### P：父提交票据

Pro 独立复核 I 后，在单父直接子提交 P 中加入：

```text
20/后续最终授权裁决
p0p_authorization_ticket.json
current/history
```

P 中授权文件仍保持精确 denied blob。票据必须预登记：

```text
I commit/tree/implementation manifest
P0 contract and P0-D identity
formal source tree
exact denied authorization blob
exact future authorized authorization blob
fixed 18-item schedule and canonical hash
single-child/single-file/mode policy
one-claim/no-resume/no-replacement policy
toolchain and capability manifest
```

票据不能包含其所在 P 的 hash。运行器从 C 的唯一 parent 得到 P，并从 Git 对象 `P:<ticket_path>` 读取票据。

### C：唯一授权 child

C 必须：

```text
恰有一个 parent P
当前 local main 和 origin/main 均指向 C
P..C 恰一条 modified path
path = p0p_execution_authorization.json
mode = 100644 -> 100644
old blob = ticket.denied_parent_blob
new blob = ticket.authorized_child_blob
```

允许态授权 JSON 的完整字节必须在 P 创建前确定并由票据预登记，因此不得含 P、C 或自身 object hash。

### Q：一次性 claim

全部 Git、实现、工具和环境门通过后，首个 `WARMUP_B1` 启动前原子创建：

```text
artifacts/p0/performance_raw/execution_claim.json
```

Q 记录实际：

```text
C child commit
P reviewed parent
I reviewed snapshot
ticket/blob/schedule/formal source/toolchain identity
grant nonce
single-writer lock identity
```

Claim 一旦创建即消费授权；任何失败、异常、中断或崩溃均禁止 resume、补样、替换或重新 claim。

## 下一项唯一任务：零步授权结构重构

Codex 只能在：

```text
90_候选工作区/current/17_HF3D性能优化/**
90_候选工作区/交接说明_current.md
90_候选工作区/交接说明_history.md
```

完成：

1. 默认拒绝授权 JSON 移除所有 child/self hash 语义；
2. 实现未来 `p0p_authorization_ticket.json` 的严格 parser/audit；
3. 使用 Git plumbing 和 NUL 分隔路径验证 parent、blob、mode 和 exact one-file delta；
4. 实现 `origin/main == HEAD`、单父 child、P 为 I 单父 review commit 和 review-only delta 门；
5. 实现非消费 preflight；
6. 实现 `O_CREAT|O_EXCL` execution claim、独占 writer lock 和固定 18-item schedule ledger；
7. 禁止 `--only/--skip/--repeat/--start-at/--resume`；
8. worker 必须要求有效 claim、锁所有权、expected sequence index 和一次性 launch token；
9. 增加合成 Git 仓库正负测试；
10. 生成零时间步静态证据后立即停止交 Pro。

## 必须通过的合成 Git 测试

至少覆盖：

```text
I -> P -> C 正路径
C merge parent
C parent 错误
P parent 不是 I
I..P 出现未批准实现变化
P..C 多一个文件
rename/copy/mode/symlink
parent denied blob 错
child authorized blob 错
从工作树而非 P Git object 读取 ticket
中文路径和 NUL-safe parsing
local HEAD != origin/main
授权 schema 出现 child/self hash 字段
允许态 exact bytes 在 P 创建前可稳定 hash
```

## 一次性消费测试

必须覆盖：

```text
非法环境 preflight 不消费 claim
preflight 不创建 sample raw
合法环境只允许一次 O_EXCL claim
第二次 claim 拒绝
claim 后崩溃禁止 resume
固定顺序不可跳步/重复/替换/补样
直接 worker 调用拒绝
并发 orchestrator/stale lock/PID reuse
claim/ledger atomic write failure injection
完成或失败后授权不可再次使用
```

## 零步返回 Pro 前的硬门

静态证据必须明确：

```text
HF3D time_steps_advanced = 0
warmup_samples_started = 0
scored_samples_started = 0
profile_samples_started = 0
formal source unchanged = true
P1 implemented = false
p0p_execution_authorization = NOT_AUTHORIZED
actual Pro ticket = absent or NOT_ISSUED
self-referential authorization fields = absent
synthetic parent-ticket authorization tests = PASS
```

当前环境仍存在仓库外计算进程且温度门不满足。不得启动任何 warm-up、timed 或 profiled 样本。

## 禁止修改

```text
02_模拟程序/**
01_最终理论/**
90_候选工作区/current/07_D3跨网格时间步响应诊断/**
00_P0性能与散度诊断预注册.json
P0-D 已提交输出
AGENTS.md
```

不得创建真实允许态票据、允许态授权 child，不得进入 P1。

## 后续正式签发流程

零步实现推送后：

1. Pro 复核 I；
2. Pro 先生成不含 P/C/self hash 的 exact allow JSON A1；
3. 计算 A1 Git blob 和 SHA-256；
4. 创建父票据和裁决提交 P，P 中授权仍 denied；
5. 创建唯一 child C，只把授权文件替换为 A1；
6. 推送 C 到 origin/main；
7. Codex 先运行非消费 preflight；
8. 环境合法后原子 claim 并执行一次固定 schedule。

任何额外 commit、实现漂移、ticket/blob 不一致、dirty worktree 或环境失败均不能执行。

## 当前结论上限

```text
D3R1_S2 = FAILED_STOPPED
D3R1_DIRECT_TRIGGER = PROJECTED_DIVERGENCE
D3R1_FAILURE_MECHANISM = NOT_YET_ESTABLISHED
D3R1_S3 = NOT_EXECUTED
D3R1_SCIENTIFIC_CLASSIFICATION = NOT_FORMED

HF3D_P0_D = COMPLETED
HF3D_P0_P_ZERO_STEP_AUTHORIZATION_REFACTOR = AUTHORIZED
HF3D_P0_P_DYNAMIC_SAMPLES = NOT_AUTHORIZED_PENDING_NEW_STATIC_REVIEW
HF3D_P1_IMPLEMENTATION = NOT_AUTHORIZED

NUMERICAL_QUALIFICATION = NOT_DEMONSTRATED
PHYSICAL_VALIDATION = NOT_PERFORMED
EXPERIMENTAL_VALIDATION = NOT_PERFORMED
APPARATUS_PREDICTION = DISALLOWED
```
