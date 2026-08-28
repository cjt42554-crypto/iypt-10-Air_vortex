# 当前交接

## 当前状态

Codex 已在候选区实现 D2 V2 的 P1–P5 证据补丁并完成纯测试，当前等待下一次 Pro 静态复核。该实现尚未获得 S0 或真实动态执行授权。

```text
D2_V2_EVIDENCE_PATCH_IMPLEMENTED__AWAITING_PRO_REVIEW
D2_EXECUTION_NOT_AUTHORIZED
D2_RAW_PRE_REVIEW_OUTPUTS_PRESENT__NOT_ADMISSIBLE_FOR_CLASSIFICATION
```

正式理论 `01_最终理论/**` 与正式程序 `02_模拟程序/**` 本轮均未修改。V2 科学阈值、算例集合和分类定义也未改变。

## 证据补丁完成内容

活动目录：`90_候选工作区/current/06_D2数值误差来源归因/`。

1. 运行器为每个锁步变体分别保存 `accepted_time_count`、`accepted_time_sha256` 和 `accepted_times_s`；
2. 最终分析器把每个变体重新编码为 `float64` 字节，独立核验计数、摘要和逐位相等，不把运行器布尔字段当成证明；
3. 最终分析器在生成 Z 分类前，直接从 Z6/Z9 归档失败闭锁 `actual_bar_rpm == 0` 与 `bar_angle_rad == 0`；
4. 最终分析器独立要求摘要的 `execution_commit`、Git revision 起点和终点完全相等；
5. 每个归档都从归档配置重新调用正式 `HighFidelityBackend.make_grid()`，并将重建 G0 `bar_mask` 与归档 `stir_bar_mask_3d` 逐元素比较；
6. compact evidence 会跟踪复制 S0 和 preliminary-R，并由规范化阶段台账引用远端存在的跟踪路径；
7. 新增非零转速、非零角度、`nextafter` 时间、同计数异字节、错误提交、revision 边界不一致、翻转掩膜单元、缺失两类跟踪副本和 preliminary/final R 不一致等失败测试。

已执行并通过：D2 单元测试 22/22、Ruff 格式与规则检查、`compileall`、预注册 JSON 严格解析、Pandoc Markdown 解析及 `git diff --check`。这些只是实现级与文档级验证，不构成数值验证、物理验证或实验验证。

## 旧 raw 输出事件与处置边界

后台服务 `iypt10-d2-v2.service` 于 2026-08-29 00:34:58 +0800 在旧实现提交 `75a34f9` 上启动。新证据补丁裁决随后于 01:45:59 +0800 写入仓库；Codex 接续时发现服务仍在运行，于 01:49:58 +0800 停止。

只读核验确认：

- S0 raw 输出存在；
- F6 与 F9 在旧实现下完整结束并留下 raw 归档和组摘要；
- Z6 只推进到约 `0.005 s`，没有完成归档或摘要；
- Z9、T9、R pair、R multiway、preliminary-R 和 final analysis 未执行；
- 没有 `execution_failure.json`；
- 服务当前为 inactive/dead，`MainPID=0`，没有 D2 Python 进程。

这些输出不满足 P1–P5 跟踪证据要求，且与补丁后的实现提交不绑定，因此一律标记为不可采信：不得用于 D2 因素分类、数值资格、物理结论或装置预测。本轮未删除、移动或改写任何 raw 输出。

现有 S0 的“无既有 D2 输出”门将因这些文件而失败。下一次 Pro 复核必须同时明确：删除、隔离或另建全新执行根；在获得明确处置裁决前，Codex 不得自行清理或复用。

## 下一步唯一入口

下一位接续者依次读取：

1. 顶层 `AGENTS.md`；
2. 本文件；
3. `90_候选工作区/current/07_D2V2实现静态复核与证据补丁裁决.md`；
4. `90_候选工作区/current/06_D2数值误差来源归因/README.md`；
5. 候选运行器、独立分析器和测试的最新提交差异。

下一步是 Pro 对 P1–P5 的静态复核，并对旧 raw 输出作出明确处置裁决。除非 Pro 给出类似以下新状态，否则不得执行：

```text
D2_V2_EVIDENCE_PATCH_REVIEW_ACCEPTED__S0_EXECUTION_AUTHORIZED
```

## 禁止越过的边界

```text
S0_EXECUTION = PROHIBITED
D2_DYNAMIC_EXECUTION = PROHIBITED
OLD_RAW_EVIDENCE_REUSE = PROHIBITED
```

不得运行：

- `run_d2_source_attribution.py verify`；
- F6/F9/Z6/Z9/T9；
- R9-BASE/R9-ALL；
- 条件五变体 R multiway；
- preliminary-R 或 final analysis；
- Q1R3 时间组、`13.5/20.25` 格动态算例或完整 Q1R4。

不得修改 `01_最终理论/**` 或 `02_模拟程序/**`。

## 当前结论上限

```text
Q1R3_SPATIAL_STAGE_FAIL_CLOSED__TEMPORAL_NOT_AUTHORIZED
Q1R4_D0_FAIL_CLOSED_AT_STATIC_GEOMETRY
D1_DYNAMIC_SHADOW_COMPLETE__GEOMETRY_EFFECT_SMALL_IN_REGISTERED_WINDOW
D2_V2_EVIDENCE_PATCH_IMPLEMENTED__AWAITING_PRO_REVIEW
D2_NUMERICAL_CLASSIFICATION = NOT_ADMISSIBLE
NUMERICAL_QUALIFICATION = NOT_DEMONSTRATED
PHYSICAL_VALIDATION = NOT_PERFORMED
EXPERIMENTAL_VALIDATION = NOT_PERFORMED
APPARATUS_PREDICTION = DISALLOWED
```

完成提交并推送 `main` 后停止，等待 Pro 复核；不得自动开始 S0。
