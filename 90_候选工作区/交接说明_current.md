# 当前交接

## 当前状态

Pro 已完成对 `main@63c155b787e428495dc36e725b2d415d18ce2624` 的 D2 V2 P1–P5 证据补丁静态复核。补丁通过，旧 raw 必须先隔离，随后允许在单一新提交上从 S0 全新执行完整 D2。

```text
D2_V2_EVIDENCE_PATCH_REVIEW_ACCEPTED
D2_PRE_REVIEW_RAW_QUARANTINE_REQUIRED
D2_FRESH_EXECUTION_AUTHORIZED_AFTER_SANITATION_COMMIT
```

正式理论 `01_最终理论/**`、正式程序 `02_模拟程序/**`、V2 科学阈值、算例集合与分类定义均未改变。

## Pro 复核结论

以下证据链已接受：

1. Z6/Z9 在形成 Z 分类前由最终分析器独立失败闭锁精确零转速与零角度；
2. 每个锁步变体分别保存接受时间计数、完整序列和 SHA-256，分析器重新编码 `float64` 字节并逐位比较；
3. 摘要 execution commit 与 Git revision 起点、终点必须完全一致；
4. 每份归档均从配置重建正式 G0，并与归档 `stir_bar_mask_3d` 逐元素比较；
5. S0 与 preliminary-R 进入跟踪 compact evidence，阶段台账只引用远端存在的路径；
6. 对应规定失败用例已经纳入测试。

裁决正文：

```text
90_候选工作区/current/08_D2V2证据补丁复核与全新执行授权.md
```

## 旧 raw 输出的处置

提交 `75a34f9bab660d692b60e4d1e51cb1160d90d14d` 上提前产生的 S0、完整 F6/F9 和未完成 Z6 一律不可采信：

```text
INADMISSIBLE_PRE_REVIEW_RAW
REUSE_PROHIBITED
CLASSIFICATION_PROHIBITED
```

即使 F6/F9 已完成，也必须重新运行。

Codex 在任何 S0 之前必须：

1. 确认 `iypt10-d2-v2.service` 为 inactive/dead、`MainPID=0`，且无 D2 Python 进程；
2. 对旧 `artifacts/runs` 全部文件生成相对路径、大小和 SHA-256 清单；
3. 把清单写入并提交：

```text
90_候选工作区/current/06_D2数值误差来源归因/artifacts/pre_review_raw_quarantine_manifest.json
```

4. 该清单提交并推送后，将整个旧 `artifacts/runs` 移到仓库同级：

```text
../iypt10_d2_quarantine_75a34f9_pre_review_20260829/
```

5. 若跨文件系统，先复制、逐文件核验清单、再删除源；任一失败立即停止；
6. 隔离目录不得被新 D2 命令读取，不记录用户绝对路径。

生成不可采信清单的提交是唯一 D2 执行提交。该提交之后直到最终分析完成，不得再产生 Git 提交。

## 全新执行顺序

确认活动 `artifacts/runs`、旧报告、`artifacts/evidence`、D2 manifest 和 `execution_failure.json` 均不存在，且 `main` 工作树清洁后，依次执行：

```text
python run_d2_source_attribution.py verify
python run_d2_source_attribution.py run-case --case F6
python run_d2_source_attribution.py run-case --case F9
python run_d2_source_attribution.py run-case --case Z6
python run_d2_source_attribution.py run-case --case Z9
python run_d2_source_attribution.py run-case --case T9
python run_d2_source_attribution.py run-r-pair
python analyse_d2_source_attribution.py preliminary-r
```

若 preliminary-R 给出 `S5_required = true`：

```text
python run_d2_source_attribution.py run-r-multiway
```

最后执行：

```text
python analyse_d2_source_attribution.py final
```

所有阶段必须在同一 HEAD 上顺序执行。禁止后台自动重启、跨提交续跑、并行依赖阶段或复用旧结果。

## 失败闭锁

任一命令非零、任一硬门失败、HEAD 改变、工作树不清洁、服务意外重启或旧隔离目录被读取时：

```text
D2_SOURCE_ATTRIBUTION_INCONCLUSIVE_FAIL_CLOSED
```

立即停止，保留失败证据并更新交接；不得跳过失败阶段形成部分分类。

## 完成条件

最终分析成功后：

1. 跟踪 compact evidence、总报告、阶段台账和原始大文件清单；
2. 保留旧 raw 不可采信清单；
3. 区分已执行、条件跳过、失败和未执行阶段；
4. 更新本文件与 `交接说明_history.md`；
5. 提交并推送 `main` 后停止等待 Pro 结果复核。

不得修改 `01_最终理论/**` 或 `02_模拟程序/**`。

## 当前结论上限

```text
Q1R3_SPATIAL_STAGE_FAIL_CLOSED__TEMPORAL_NOT_AUTHORIZED
Q1R4_D0_FAIL_CLOSED_AT_STATIC_GEOMETRY
D1_DYNAMIC_SHADOW_COMPLETE__GEOMETRY_EFFECT_SMALL_IN_REGISTERED_WINDOW
D2_V2_EVIDENCE_PATCH_REVIEW_ACCEPTED
D2_NUMERICAL_CLASSIFICATION = NOT_YET_AVAILABLE
NUMERICAL_QUALIFICATION = NOT_DEMONSTRATED
PHYSICAL_VALIDATION = NOT_PERFORMED
EXPERIMENTAL_VALIDATION = NOT_PERFORMED
APPARATUS_PREDICTION = DISALLOWED
```
