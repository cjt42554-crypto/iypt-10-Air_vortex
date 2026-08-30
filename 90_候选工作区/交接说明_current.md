# 当前交接

当前状态：

```text
D2_V2_EXECUTION_REVIEW_ACCEPTED_WITH_NONBLOCKING_LEDGER_CLARIFICATION
D3_V2_FINAL_STATIC_REVIEW_ACCEPTED
D3_S3_PUBLICATION_TRANSACTION_ACCEPTED
D3_PREEXECUTION_MACHINE_GATE_FAILED_CLOSED_BEFORE_S0
D3_AUTHORIZATION_PATH_QUOTING_AUDIT_PATCH_READY_FOR_PRO_REVIEW
D3_DYNAMIC_EXECUTION_NOT_AUTHORIZED_FOR_PATCHED_SNAPSHOT
D3_FRESH_S0_S1_S2_S3_EXECUTION_NOT_YET_PERFORMED
```

## 最近裁决与本轮执行前结果

Pro 已完成对 D3 S3 发布事务补丁、共享状态机、运行器、独立分析器、故障注入测试和零时间步证据的结果盲静态复核，并曾对精确快照签发执行授权。裁决正文：

```text
90_候选工作区/current/13_D3V2最终静态复核与全新执行授权.md
```

本地拉取授权提交 `a0271e0` 后，执行前机器门在创建 D3 raw 和推进时间步之前拒绝 S0。唯一失败项最初为 `authorization_file_is_only_change`：Git 默认把中文授权路径输出为带引号的八进制转义串，而审计把它与真实 Unicode 路径直接比较。人工父子提交差异与修复后的机器审计都确认唯一变更确为授权文件；这是授权审计实现缺陷，不是动态算例失败。

候选实现提交 `8d39a0e` 同时修复运行器与独立分析器的 Git 中文路径读取，并把测试/静态审计从“授权对象必须永远保持默认拒绝”改为“待复核实现快照不得已具备精确绑定授权”，从而允许合法授权子提交存在而不削弱实现漂移闭锁。61/61 测试、Ruff、格式、限定 mypy、`compileall`、9 份严格 JSON、Pandoc/MathML/Chrome 和 `git diff --check` 通过；在清洁 `8d39a0e` 上生成的零步静态证据全门通过，RTX 5060 Laptop GPU CuPy FP64 探针通过，`time_steps_advanced = 0`，动态输出不存在。

## 机器授权的判定方式

本交接与裁决所在提交只是被复核父提交。紧随其后的唯一子提交必须只修改：

```text
90_候选工作区/current/07_D3跨网格时间步响应诊断/d3_execution_authorization.json
```

执行许可不以自然语言交接为唯一依据，而以当前 HEAD 上运行器和分析器的机器授权审计为准。只有同时满足以下条件，才视为 D3 动态执行已授权：

```text
status = AUTHORIZED_BY_PRO_FOR_BOUND_IMPLEMENTATION
real_cuda_dynamic_execution = true
authorized_stages = [S0, S1, S2, S3]
automatic_follow_on = PROHIBITED
current HEAD = authorization-only child of reviewed parent
all implementation/blob/hash/source/preregistration/ruling bindings = PASS
```

若当前 HEAD 不是该唯一授权子提交，或任一授权门失败，则不得执行任何动态命令。

旧授权绑定的是修复前实现，已因实现哈希漂移自动失效；不得通过环境配置、手工解释或修改授权对象绕过。修复后的实现必须重新接受 Pro 独立复核并获得新的精确快照裁决。

## 下一项唯一任务

由 Pro 独立复核当前远端 `main` 的中文路径审计修复、状态中立回归测试和零步静态证据，并裁决是否对该精确实现快照重新签发执行授权。在新的机器授权审计全门通过前，Codex 不得运行 S0、S1、S2 或 S3。

如果 Pro 重新授权，Codex 拉取后仍须先做非推进的授权、身份、CUDA、空输出和单写入者检查；只有全部通过，才可按既有裁决逐阶段显式执行并逐阶段检查，禁止后台无检查串联。

## 执行前冻结条件

1. `main` 工作树清洁，当前 HEAD 为有效授权提交；S0–S3 之间不得产生任何 commit 或实现修改。
2. D3 raw、动态 reports、最终 manifest、compact evidence、S3 staging 和 raw ledger 临时文件全部不存在；未知残留不得删除后继续。
3. 没有 D3/HF3D 后台或并发写入进程，整个阶段维持单写入者。
4. 使用零步静态证据记录的 GPU Python/CuPy 环境；真实 CUDA 且无 CPU 回退。
5. 不复用 D2 raw、旧轨迹、隔离目录、checkpoint、陈旧 staging 或部分输出。
6. 正式源码树必须保持：

```text
dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8
```

## 失败停止条件

任一命令非零退出、异常、硬门失败、CUDA 回退、`execution_failure.json` 出现、台账/归档/manifest/报告不一致、身份漂移、未知输出或外部中断，立即停止。禁止删除、修补、拼接、选择性保留或重跑同一阶段；保留原始证据并更新交接后交 Pro 复核。

## 成功交付

仅当 S3 `EXECUTED/PASS`，tracked/raw ledger 一致，报告、manifest、compact evidence 和事务记录的独立重载审计全部通过时：

1. 提交跟踪的 D3 报告、manifest、compact evidence 与更新后的交接；
2. 明确记录唯一执行提交和六算例状态；
3. 立即停止交 Pro 做结果复核，无论分类标签是什么；
4. 不自动运行更小时间步、更细网格、长期窗口、Q1R4 或 D4。

## 当前结论上限

```text
Q1R3_SPATIAL_STAGE_FAIL_CLOSED__TEMPORAL_NOT_AUTHORIZED
Q1R4_D0_FAIL_CLOSED_AT_STATIC_GEOMETRY
D1_GEOMETRY_EFFECT_SMALL_IN_REGISTERED_WINDOW
D2_Z_SMALL_NO_CLEAR_DIRECTION
D2_T_MATERIAL_REDUCTION_SIGNAL
D2_R_MIXED_OR_INDETERMINATE_NO_CLEAR_DIRECTION
D3_PATCHED_SNAPSHOT_REAL_CUDA_EXECUTION_NOT_AUTHORIZED_PENDING_PRO_REVIEW
NUMERICAL_QUALIFICATION = NOT_DEMONSTRATED
PHYSICAL_VALIDATION = NOT_PERFORMED
EXPERIMENTAL_VALIDATION = NOT_PERFORMED
APPARATUS_PREDICTION = DISALLOWED
```

正式理论 `01_最终理论/**`、正式程序 `02_模拟程序/**`、顶层 `AGENTS.md`、冻结预注册、科学阈值、六算例矩阵、输出时刻和标签优先级本轮均未修改。本轮没有运行 D3 动态算例或实验，也没有提高数值资格、物理验证、实验验证或装置预测结论。
