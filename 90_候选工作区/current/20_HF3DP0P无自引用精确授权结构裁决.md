# HF3D P0-P 无自引用精确授权结构裁决

日期：2026-08-31（Asia/Tokyo）

裁决状态：

```text
HF3D_P0P_AUTHORIZATION_SELF_REFERENCE_DEFECT_CONFIRMED
HF3D_P0P_PARENT_ANCHORED_EXACT_BLOB_GRANT_SELECTED
HF3D_P0P_ZERO_STEP_AUTHORIZATION_REFACTOR_AUTHORIZED
HF3D_P0P_DYNAMIC_SAMPLES_NOT_AUTHORIZED_PENDING_NEW_STATIC_REVIEW
HF3D_P1_IMPLEMENTATION_AND_FORMAL_SOURCE_MODIFICATION_NOT_AUTHORIZED
```

## 1. 绑定对象与本轮边界

本裁决绑定：

```text
repository: cjt42554-crypto/iypt-10-Air_vortex
reviewed main: d12078f996092c82f46936b594c7d6d645ccdda0
P0 contract Git blob: 62f256574f67654d13f588e0505f63505d065907
formal source tree SHA-256: dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8
P0-D: completed and retained
P0-P dynamic samples: not authorized
P1: not authorized
```

本轮只裁决未来 P0-P 精确快照授权的 Git 对象结构、机器门和一次性消费语义，不修改 P0 合同、P0-D 证据、正式源码或性能基准。当前仍只允许零时间步实现和合成测试，不允许启动 warm-up、timed 或 profiled HF3D 样本。

## 2. 自引用矛盾成立

设：

```text
P = 已复核父提交
A = p0p_execution_authorization.json 的字节内容
C = 唯一授权子提交
```

Git 子提交身份满足：

```text
C = Hash(commit_header(tree(A), parent=P, author, committer, message, ...))
```

若 `A` 内要求包含并校验 `C` 自身最终 commit hash，则变成：

```text
C = Hash(... tree(A(C)) ...)
```

这是密码散列固定点问题。每次把候选 `C` 写入 JSON 都会改变授权文件 blob、tree 和 commit hash，从而再次改变 `C`。通过反复 amend、占位符、先写 null 再回填或迭代求值都不能构成可验证的正常 Git 工作流。

同理：

- 位于提交 `P` 内的票据不能包含并校验 `P` 自身 commit hash；
- 位于某 blob 内的普通字段不能直接包含并校验该 blob 的完整 Git object ID；
- 子提交哈希不能靠 commit message、JSON 自哈希或事后回填消除递归。

因此，之前“授权文件预先写入最终授权子提交 hash”的要求撤销。**授权子提交 hash 必须作为运行时观测值记录，而不能作为授权文件内部的先验字段。**

## 3. 四项需求的精确定义

### 3.1 精确绑定已复核父快照

精确绑定由 Git 拓扑和父提交内的授权票据共同完成，而不是由授权 JSON 自报自身子提交哈希：

```text
C 的唯一父提交 = P
授权票据 T 必须从 Git 对象 P 中读取
T 必须绑定已复核实现快照 I、P0 合同、工具能力、正式源码树和固定 schedule
C 中授权文件的 Git blob 必须等于 T 预先登记的精确允许 blob
```

### 3.2 唯一直接子提交

“唯一”在 Git 中定义为可本地机器验证的操作语义：

```text
当前执行 HEAD C 恰有一个 parent
该 parent 恰为包含有效票据 T 的 P
当前 main/origin-main 指向 C
```

Git 是由子指向父的数据结构；本地仓库不能证明整个 Git 世界中不存在另一个未获取或不可见的 sibling child。因此不得把“全局没有任何其他 child”设为无法完成的机器门。真正需要验证的是：**本次执行 HEAD 是受复核父提交的单父直接子提交，并且只有授权文件发生变化。**

### 3.3 仅授权文件变化

必须按 NUL 分隔的 Git plumbing 输出验证：

```text
diff(P, C) = exactly one modified path
path = p0p_execution_authorization.json
old mode = 100644
new mode = 100644
old blob = T.denied_parent_blob
new blob = T.authorized_child_blob
```

不得接受 rename、copy、symlink、submodule、mode change、额外文档变化或未提交工作树漂移。

### 3.4 一次性固定 schedule

一次性不是靠授权 JSON 存储自身 commit hash实现，而由：

```text
不可变授权票据 + 精确授权 blob + 原子 execution claim + 严格 schedule ledger
```

共同实现。授权子提交只允许取得一次 schedule claim；每个样本只能按冻结顺序启动一次，失败、异常或外部中断均消费本次 claim，不得补样、替换或恢复。

## 4. 被拒绝的结构

以下方案均禁止：

1. **授权 JSON 写最终 child commit hash。** 存在直接自引用。
2. **循环 amend 直到 hash“稳定”。** 密码散列固定点不可作为工程协议。
3. **先以 null/placeholder 提交，再在同一文件回填 hash。** 回填后 commit hash改变。
4. **运行时自动修改授权 JSON 写入 HEAD。** 会使工作树变脏并破坏精确 blob 门。
5. **增加第二个“确认 grandchild”记录 child hash。** 使执行 HEAD 不再是授权父提交的直接授权子提交，并扩大提交结构。
6. **只使用 Git note。** note 位于独立 ref，默认 clone/fetch 不保证取得，不能作为唯一门。
7. **只使用事后 tag/receipt 而没有父票据。** 可以做附加认证，但不能替代父快照、单文件差异和一次性 claim。
8. **授权文件只写 reviewed parent hash，且父提交没有预登记精确允许 grant 内容。** 在非对抗个人工作流中可勉强工作，但无法阻止同一父提交下构造语义不同的另一个“授权”文件；本项目选择更严格的父票据精确 blob 结构。
9. **票据写其所在父提交 hash。** 只是把自引用从 child 移到 parent。
10. **把字符串 `HEAD`、`SELF` 或空字段当作 commit 绑定。** 不是密码学或 Git 对象绑定。

## 5. 选定结构：父提交票据 + 精确授权 blob + 运行时消费凭据

选定四对象结构：

```text
I = Codex 零步实现与静态证据提交
P = Pro 已复核父提交
C = 唯一授权子提交
Q = 运行开始后原子生成的 execution claim
```

Git 历史结构：

```text
I -- P -- C
```

其中：

- `I` 包含完整 harness、worker、监测器、解析器、分配观察器、静态证据以及默认拒绝的 `p0p_execution_authorization.json`；
- `P` 是 `I` 的单父直接子提交，只增加 Pro 裁决、current/history 和机器可读授权票据 `p0p_authorization_ticket.json`；父提交中的授权文件仍保持默认拒绝；
- `C` 是 `P` 的单父直接子提交，只把授权文件从精确拒绝 blob 改为票据预登记的精确允许 blob；
- `Q` 不属于 Git 授权文件，而是在全部执行前门和环境门通过后、首个 warm-up 启动前原子创建，用于记录实际 `C`、`P`、票据、schedule 和一次性消费状态。

## 6. 跟踪文件与路径

未来零步实现必须使用：

```text
90_候选工作区/current/17_HF3D性能优化/p0p_execution_authorization.json
90_候选工作区/current/17_HF3D性能优化/p0p_authorization_ticket.json
```

动态消费证据位于 P0 预注册的独立 raw 根，例如：

```text
90_候选工作区/current/17_HF3D性能优化/artifacts/p0/performance_raw/
  execution_claim.json
  schedule_ledger.json
  execution_failure.json
  execution_complete.json
  samples/**
```

`p0p_authorization_ticket.json` 只在 Pro 复核父提交 `P` 中创建。Codex 零步实现阶段不得伪造票据或允许态文件；它只实现严格解析、机器门、合成 Git 仓库测试和默认拒绝状态。

## 7. 父提交授权票据 T

票据必须是严格 JSON、无重复 key、无 NaN/Infinity、无未知顶层字段，并至少包含：

```text
schema_version
ticket_id
status = READY_FOR_SINGLE_AUTHORIZATION_CHILD
authorization_path
ruling_path
reviewed_snapshot
review_parent_policy
denied_parent_blob
authorized_child_blob
fixed_schedule
child_commit_policy
claim_policy
toolchain_binding
formal_source_tree_sha256
```

### 7.1 reviewed_snapshot

必须绑定：

```text
I 的 commit ID
I 的 tree ID
零步静态审计路径、Git blob 和 SHA-256
全部 harness/worker/monitor/parser/allocator 源码与构建物的 Git blob 和 SHA-256
P0 合同 Git blob 和 SHA-256
P0-D 证据身份
正式源码树 SHA-256
Python/NumPy/SciPy/CuPy/nsys/GPU capability manifest
```

### 7.2 review_parent_policy

必须规定：

```text
P 恰有一个 parent，且为 I
I..P 的变化路径精确等于已批准的 review-only 路径集合
I..P 不允许修改 01_最终理论/**、02_模拟程序/**、P0 合同、P0-D 输出或零步实现文件
P 中 p0p_execution_authorization.json 的 blob 恰为 denied_parent_blob
P 中 ticket 和 ruling 均为 regular file mode 100644
```

票据不能包含 `P` 自身 hash；运行器从 `C` 的唯一 parent 得到 `P`，再从 Git 对象 `P:<ticket_path>` 读取票据。票据由其所在的内容寻址父提交保护，无需自报所在 commit。

### 7.3 denied_parent_blob / authorized_child_blob

二者均必须记录：

```text
Git blob object ID
git_object_algorithm
content SHA-256
byte length
UTF-8 + LF encoding policy
```

允许态授权 JSON 的**完整精确字节**必须在创建 `P` 前就确定，并计算出 `authorized_child_blob`。因此允许态 JSON 不得包含未来的 `P` 或 `C` hash。

### 7.4 fixed_schedule

票据必须冻结完整 schedule，而不只冻结一个自然语言名称。至少记录：

```text
schedule_id
canonical_schedule_sha256
ordered item list
每项 sequence_index
benchmark_id
mode = WARMUP / TIMED / PROFILE_HOST / PROFILE_CUDA / PROFILE_MEMORY
scored boolean
config hash
duration/output schedule
profile tool policy
no replacement / no reorder / no resume
```

本轮冻结的执行序列必须与 P0 合同和上一裁决一致：

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
PROFILED_B2_HOST
PROFILED_B2_CUDA
PROFILED_B2_MEMORY
PROFILED_B3_HOST
PROFILED_B3_CUDA
PROFILED_B3_MEMORY
```

## 8. 允许态授权文件 A1

允许态 `p0p_execution_authorization.json` 必须是一个在创建父提交前即可完全确定的静态 grant。它至少包含：

```text
schema_version
authorization_id
ticket_id
ticket_path
status = AUTHORIZED_BY_PRO_FOR_BOUND_PARENT_TICKET
grant_nonce
reviewed_snapshot_commit = I
fixed_schedule_id
fixed_schedule_sha256
maximum_schedule_claims = 1
preflight_consumes_claim = false
claim_before_first_sample = true
sample_replacement = PROHIBITED
schedule_resume_after_claim = PROHIBITED
P1 = PROHIBITED
```

允许态文件明确禁止包含：

```text
authorization_child_commit
current_head_commit
self_commit_hash
self_blob_hash
review_parent_commit（如其值依赖尚未创建的 P）
任何运行时填写字段
```

父快照绑定由 `C -> P` 的唯一父关系和 `P` 内票据完成。允许态文件只需引用 `ticket_id` 和固定 grant 内容，其精确 blob 已预登记在票据中。

## 9. 创建授权提交的无递归流程

### 阶段 A：Codex 零步实现

1. 提交清洁实现 `I`；
2. `p0p_execution_authorization.json` 保持默认拒绝；
3. 静态审计证明动态样本为零；
4. 停止交 Pro。

### 阶段 B：Pro 构造允许态 blob 与父票据

1. 先构造完整允许态 JSON `A1`，其中不含 `P`、`C` 或自身 blob hash；
2. 计算：

   ```text
   B1_git = git hash-object(A1 exact bytes)
   B1_sha256 = SHA-256(A1 exact bytes)
   ```

3. 读取 `I` 中拒绝文件得到 `B0_git/B0_sha256`；
4. 构造票据 `T`，登记 `I`、`B0`、`B1`、固定 schedule、正式源码和工具能力；
5. 创建父提交 `P`，只加入裁决、票据和交接，授权文件仍为 `B0`。

### 阶段 C：创建唯一授权子提交

1. 从 `P` 创建单父 child；
2. 只把授权文件替换为精确 `A1`；
3. 创建 `C` 并推进远端 `main`；
4. 不在 `A1` 内回填 `C`；
5. `C` 的实际 commit ID 在运行时机器审计和 `Q` 中记录。

此流程不存在 Git 对象自引用。

## 10. 运行前机器审计

机器必须使用 Git plumbing 和 NUL 分隔输出，禁止依赖人类格式化的 `git log` 或易受中文 quoting 影响的普通文本解析。

### 10.1 远端和工作树

启动动态 schedule 前必须：

```text
git fetch --no-tags origin main 成功
symbolic HEAD = refs/heads/main
HEAD C = refs/remotes/origin/main
git diff --quiet
git diff --cached --quiet
无未跟踪非 raw 文件
正式源码树精确
无并发 writer
```

若无法确认远端 `main`、工作树清洁或分支身份，失败闭锁。

### 10.2 child 拓扑

必须验证：

```text
C 是 commit
C 恰有一个 parent P
C 不是 merge commit
```

### 10.3 父票据

必须从 Git 对象读取而不是信任工作树字符串：

```text
T = git cat-file blob P:<ticket_path>
```

随后验证严格 JSON、ticket status、reviewed snapshot、I..P review-only delta、正式源码、P0 合同和静态证据。

### 10.4 精确单文件变化

以 `git diff-tree --raw -r -z P C` 验证：

```text
恰一条 M 记录
path = authorization_path
old/new mode = 100644/100644
old blob = B0
new blob = B1
```

再验证：

```text
git ls-tree P authorization_path = B0
git ls-tree C authorization_path = B1
git hash-object(worktree authorization file) = B1
content SHA-256 = ticket B1_sha256
```

任何额外 path、mode change、symlink、rename、copy 或 blob 不一致均拒绝。

### 10.5 grant 与 schedule

解析 `A1` 后必须：

```text
A1 ticket_id = T ticket_id
A1 authorization_id / nonce 精确
A1 fixed_schedule_sha256 = T fixed_schedule_sha256
A1 maximum_schedule_claims = 1
A1 P1 = PROHIBITED
T authorized_child_blob = observed B1
```

Orchestrator 不得接收可改变 schedule 的 `--only`、`--skip`、`--repeat`、`--start-at`、`--resume` 或任意样本列表参数。

## 11. 一次性 schedule 消费协议

### 11.1 非消费 preflight

`preflight` 可以多次运行，但必须：

```text
不推进 HF3D 时间步
不创建 sample raw
不创建 execution claim
不改变授权文件或工作树
只输出环境拒绝/通过证据
```

当前环境温度或外部计算进程不满足门时，preflight 拒绝不消费授权。

### 11.2 原子 claim

所有静态授权门和实时环境门通过后、`WARMUP_B1` 启动前，orchestrator 必须：

1. 取得独占单写入者锁；
2. 以 `O_CREAT|O_EXCL` 创建 `execution_claim.json`；
3. 写入并 fsync 文件和父目录；
4. 只有 claim 持久化成功后才可启动第一个样本。

Claim 必须记录：

```text
observed authorization child C
observed reviewed parent P
reviewed snapshot I
ticket blob ID and SHA-256
authorized blob ID and SHA-256
grant nonce
fixed schedule ID/hash/完整序列
formal source tree
toolchain/GPU identity
claim UTC/time monotonic anchor
single-writer lock identity
```

**实际 child commit hash 应在这里记录，而不是写入授权 JSON。**

### 11.3 claim 后的终态

Claim 一旦创建即消耗授权。此后任一：

```text
样本失败
监测缺口
环境门失效
硬门失败
CUDA 回退
外部中断
orchestrator 崩溃
worker 异常
机器重启
```

都必须形成终止失败状态。不得重新 claim、补跑、替换样本或从中间继续。

### 11.4 固定 schedule ledger

`schedule_ledger.json` 必须预先含 18 个固定条目并维持唯一前缀状态：

```text
NOT_STARTED* -> exactly one RUNNING -> PASS prefix
```

启动样本前先原子提交该条目 `RUNNING`；样本结束后再提交 `PASS` 或全 schedule `FAILED_STOPPED`。不得出现：

```text
两个 RUNNING
跳号
PASS 后回退
失败后继续
同一 sample 两次 start
scored sample 被替换
profile 子运行重排
```

Worker 必须要求 orchestrator 当前 claim、锁所有权、expected sequence index 和一次性 sample launch token；直接手工调用 worker 应失败。

## 12. 一次性保证的威胁模型

该结构可以机器防止：

```text
误用旧授权
额外提交变化
错误 parent
错误 grant blob
schedule 漂移
重复 orchestrator 启动
同一 evidence root 内补样/替换/重跑
异常后继续
```

它不声称能抵抗一个有权限删除全部本地 raw、伪造 Git 历史并强推远端的恶意操作者。若需要对抗性不可回滚的一次性保证，必须增加独立写入一次存储、远端受保护服务或密码学签名/透明日志；这超出当前单用户科研工具的信任模型。

在当前模型中，强制要求：

```text
origin/main = C
旧 evidence roots 原样保留
claim/ledger 不得删除
运行后立即提交 compact evidence
```

足以满足可审计的一次性科研执行。

## 13. 子提交 hash 的合法记录位置

允许记录实际 `C` 的位置：

```text
execution_claim.json
每个 sample manifest
execution_failure.json / execution_complete.json
compact performance manifest
后续结果提交和交接文档
```

禁止记录实际 `C` 的先验位置：

```text
P 内票据
C 内授权 JSON
C 的任意 tracked file
```

后验记录不会影响 `C` 的 Git 身份，因此没有自引用。

## 14. 零步实现必须增加的测试

至少覆盖：

### 14.1 Git 拓扑与 blob

- 合成仓库 `I -> P -> C` 正路径；
- `C` 为 merge commit；
- `C` parent 错误；
- `P` parent 不是 `I`；
- `I..P` 出现未批准实现路径；
- `P..C` 多一个文件；
- auth path rename/copy/mode change/symlink；
- parent denied blob 错；
- child authorized blob 错；
- ticket 从工作树读取而非 `P` object 的负例；
- 中文路径与 `core.quotePath`/NUL 解析；
- local HEAD 与 `origin/main` 不一致。

### 14.2 无自引用 schema

- schema 明确拒绝 `authorization_child_commit`、`self_commit_hash`、`self_blob_hash`；
- 允许态文件可在 `P` 创建前确定并计算稳定 blob；
- 同一 exact bytes 始终得到同一 Git blob；
- ticket 登记 blob 与 child 实际 blob 精确一致。

### 14.3 一次性消费

- 非消费 preflight 不创建 claim；
- 非法环境不消费授权；
- 合法环境原子创建一次 claim；
- 第二次 claim 被 `O_EXCL` 拒绝；
- claim 后崩溃禁止 resume；
- sample 启动前 ledger 先进入 RUNNING；
- 固定顺序、跳步、重复、替换、补样负例；
- 直接 worker 调用被拒绝；
- stale lock、PID reuse、并发 orchestrator；
- claim/ledger 原子写入和故障注入；
- 完成或失败后授权均不能再次使用。

### 14.4 静态审计

必须证明：

```text
HF3D time_steps_advanced = 0
warmup_samples_started = 0
scored_samples_started = 0
profile_samples_started = 0
formal source unchanged = true
P1 implemented = false
actual parent ticket absent or NOT_ISSUED in zero-step phase
p0p_execution_authorization = NOT_AUTHORIZED
self-referential fields absent = true
synthetic Git authorization tests = PASS
```

## 15. 允许与禁止的零步修改范围

仅允许修改或新增：

```text
90_候选工作区/current/17_HF3D性能优化/**
90_候选工作区/交接说明_current.md
90_候选工作区/交接说明_history.md
```

明确禁止：

```text
02_模拟程序/**
01_最终理论/**
90_候选工作区/current/07_D3跨网格时间步响应诊断/**
P0 预注册
P0-D 输出
AGENTS.md
任何 warm-up/timed/profile 样本
任何允许态票据或允许态授权子提交
P1 accelerated implementation
```

Codex 此轮只能把授权审计实现重构为上述父票据结构、补充合成 Git 测试和零步静态证据。完成后立即停止交 Pro。

## 16. Pro 后续签发流程

只有零步实现独立复核通过后：

1. Pro 先生成 exact `A1` bytes 和其 Git blob；
2. Pro 创建父票据和裁决提交 `P`；
3. 验证 `P` 为 `I` 的单父直接子提交且授权文件仍 denied；
4. 创建唯一授权 child `C`，只替换授权文件为 `A1`；
5. 推送 `C` 到远端 `main`；
6. Codex 拉取并执行非消费 preflight；
7. 环境合法后原子 claim 并执行一次固定 schedule。

任何额外 commit、实现漂移、ticket/blob 不一致或环境失败都不能通过旧授权继续。

## 17. 当前结论上限

```text
D3R1_S2 = FAILED_STOPPED
D3R1_DIRECT_TRIGGER = PROJECTED_DIVERGENCE
D3R1_FAILURE_MECHANISM = NOT_YET_ESTABLISHED
D3R1_S3 = NOT_EXECUTED
D3R1_SCIENTIFIC_CLASSIFICATION = NOT_FORMED

HF3D_P0_D = COMPLETED
HF3D_P0_P_PLAN = FROZEN
HF3D_P0_P_AUTHORIZATION_STRUCTURE = PARENT_ANCHORED_EXACT_BLOB_GRANT
HF3D_P0_P_ZERO_STEP_AUTHORIZATION_REFACTOR = AUTHORIZED
HF3D_P0_P_DYNAMIC_SAMPLES = NOT_AUTHORIZED_PENDING_NEW_STATIC_REVIEW
HF3D_P1_IMPLEMENTATION = NOT_AUTHORIZED

NUMERICAL_QUALIFICATION = NOT_DEMONSTRATED
PHYSICAL_VALIDATION = NOT_PERFORMED
EXPERIMENTAL_VALIDATION = NOT_PERFORMED
APPARATUS_PREDICTION = DISALLOWED
```

本裁决不修改正式程序、P0 合同、P0-D 证据或性能样本计划，只修正未来授权的 Git 身份设计。