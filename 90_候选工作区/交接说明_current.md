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
HF3D_P0_P_REVIEW_BINDING_MATERIAL = IMPLEMENTED_PENDING_PRO_REVIEW
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
reviewed runtime commit: 8a0c2fa564d9d6abfd84e5620379af11e5f858d3
reviewed runtime tree: 57dc939af0bc923ef3076d1d14ea889573041cf7
Pro ruling commit: 06d7fd5e49b398acd668ca43a392f621864d956d
P0 contract blob: 62f256574f67654d13f588e0505f63505d065907
formal source identity: dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8
```

## 本轮完成的 review-binding material

本轮只新增 review-only 工具、绑定材料和相关测试，没有修改任何既有 P0-P runtime 文件。

生成器：

```text
90_候选工作区/current/17_HF3D性能优化/review_tools/generate_p0p_review_binding.py
```

规范输出：

```text
90_候选工作区/current/17_HF3D性能优化/artifacts/p0/p0p_static/p0p_review_binding_material.json
90_候选工作区/current/17_HF3D性能优化/artifacts/p0/p0p_static/p0p_review_binding_material.md
```

相关测试：

```text
90_候选工作区/current/17_HF3D性能优化/tests/test_p0p_review_binding_material.py
```

JSON 由 Git object 和现有冻结证据确定性生成，集中包含：

- `REQUIRED_IMPLEMENTATION_RELATIVE_PATHS` 的 24 个精确、有序、无重复记录；
- 每个 runtime 文件的 path、mode、Git blob、SHA-256 和 byte length；
- static audit、P0 contract、三份 P0-D、capability manifest 和默认拒绝授权的同类记录；
- formal source identity；
- 固定 18 项 schedule 完整 canonical object 与 SHA-256；
- future ticket schema、child/claim policy、review-only path template、protected prefix 和 immutable path policy；
- non-authorizing、ticket absent、grant absent、dynamic denied、P1 denied 和零执行计数声明。

材料只记录已经存在且已复核的 runtime 参考提交 `8a0c2fa...`。它有意不记录包含材料的新提交自身 commit/tree，也不记录未来父提交 `P` 或授权子提交 `C` 的身份。新提交形成后，Pro 必须从 Git 拓扑独立观察 snapshot `I` 的 commit/tree。

## 核心验证结果

```text
required implementation file count = 24
all runtime records match 8a0c2fa = true
fixed schedule item count = 18
fixed schedule canonical SHA-256 = 2138aea65041ff322a0cc2ce22945544d51d1e39db1929079f9f6265e494dceb
formal source identity unchanged = true
actual Pro ticket absent = true
authorized grant absent = true
dynamic samples authorized = false
HF3D time steps = 0
warm-up samples = 0
scored samples = 0
profile samples = 0
all performance samples = 0
```

已执行：

- review material 专项正负测试 `17/17`；
- 含既有 P0-D、P0-P runtime 与新增 review material 的完整候选回归 `53/53`；
- Ruff lint 与 format check；
- 新生成器和测试的限定 mypy；
- 生成后 `--check` 逐字节一致；
- 严格 JSON 解析；
- current 的 Pandoc Markdown 验证。

未执行：

- 任何 P0-P dynamic sample；
- 任何 HF3D 时间步、warm-up、timed 或 profile 样本；
- 新一轮 allocator、nsys 或 CUDA capability probe；
- 数值资格、物理验证或实验验证；
- 正式程序性能重构。

## 复核负例覆盖

新增测试明确覆盖：

- required implementation 缺一项、重复一项或多一项；
- path、mode、Git blob、SHA-256 或 byte length 漂移；
- symlink 与 gitlink/submodule；
- fixed schedule 顺序或字段漂移；
- static audit、contract、capability 或 denied authorization 记录漂移；
- 自引用字段或包含材料的当前 commit/tree 值出现；
- formal source identity 漂移；
- dynamic authorization 或零执行计数漂移；
- 两次构造、两次写出与 `--check` 的逐字节确定性。

## 下一项唯一任务：Pro review 与票据签发裁决

当前必须停止交 Pro 独立复核本轮 review-only 补充。Pro 应先确认：

```text
supplement commit/tree 可从 Git 独立观察
runtime blobs 与 8a0c2fa 完全相同
binding material 完整且逐字节可再生
self/future reference absent
formal source unchanged
ticket absent
grant absent
dynamic denied
all execution counters zero
```

若复核通过，只有 Pro 可以按裁决生成 exact authorized grant bytes `A1`，计算其 Git blob 与 SHA-256，创建仍保持 denied authorization 的父票据提交 `P`，再创建仅修改授权文件的唯一子提交 `C`。Codex 不得自行预建 ticket、grant、`P` 或 `C`。

即使 Pro 后续签发授权，运行前仍必须通过实时环境门。当前或未来出现高温、仓库外计算进程、工作树/远端漂移或任何绑定失败时，preflight 必须拒绝且不得消费 claim。

## 技术债与性能路线裁决保持不变

Pro 已拒绝全项目推倒重写，接受有边界的跨模块定向重构。顺序仍为：

```text
P0: 完成 P0-P baseline
P1A: explicit GPU-resident accelerated backend
P1B: 面向未来 campaign 的共享 evidence core
P2: recording/archive/device boundary
P3: GUI/forward 等仅在独立问题证据出现时处理
```

当前 CPU 和 GPU 利用均不充分。主要加速路线仍是显式 GPU-resident HF3D 新后端，默认作为 E1 新数值身份，保留现有 reference backend。不得在 P0-P baseline 冻结前修改 `02_模拟程序/**`。

## 当前禁止

```text
任何 P0-P dynamic sample
任何未经 Pro 签发的 parent ticket / authorized grant / child
任何 P1 实现
任何 02_模拟程序/** 修改
任何 01_最终理论/** 修改
任何 D3R1 重跑或 S3
放宽散度、压力、CFL 或其他科学门
删除、替换或复用既有 V1/R1 raw
```

## 当前结论上限

```text
HF3D_P0_P_ZERO_STEP_IMPLEMENTATION = STATIC_REVIEW_ACCEPTED
HF3D_P0_P_REVIEW_BINDING_MATERIAL = IMPLEMENTED_PENDING_PRO_REVIEW
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
