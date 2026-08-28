# 当前交接

## 当前状态

Codex 已在 `main@75a34f9bab660d692b60e4d1e51cb1160d90d14d` 实现 D2 V2 候选运行器、独立分析器和测试。Pro 已完成实现静态复核：主体事务式推进、配置约束、三项正则化尺度和 `eta/rho/beta_R/kappa` 算术可保留，但远端独立证据链尚不完整。

当前状态：

```text
D2_V2_IMPLEMENTATION_STATIC_REVIEW_CONDITIONAL_PASS
D2_EXECUTION_NOT_AUTHORIZED__EVIDENCE_PATCH_REQUIRED
```

D2 尚未执行 S0，也没有运行 F6/F9/Z6/Z9/T9、R pair 或 R multiway。

## Codex 唯一任务入口

依次读取：

1. 顶层 `AGENTS.md`；
2. 本文件；
3. `90_候选工作区/current/07_D2V2实现静态复核与证据补丁裁决.md`；
4. `90_候选工作区/current/06_D2数值误差来源归因科学裁决与执行规划.md`；
5. `90_候选工作区/current/06_D2数值误差来源归因/d2_source_attribution_preregistration_v2.json`；
6. `90_候选工作区/current/06_D2数值误差来源归因/README.md`。

本轮只修候选 D2 证据链，不运行真实 D2 动态算例。

## 必须完成的证据补丁

1. 最终分析器直接从 Z6/Z9 归档重算 `actual_bar_rpm` 与 `bar_angle_rad`，任一非零即失败闭锁；
2. 对每个锁步变体分别保存 `accepted_time_count / accepted_time_sha256 / accepted_times_s`，最终分析器逐位独立比较；
3. 最终分析器验证每个阶段 Git revision 起止均等于记录的 `execution_commit`；
4. 最终分析器从归档配置重新调用正式 `HighFidelityBackend.make_grid()`，将正式 G0 `bar_mask` 与归档 `stir_bar_mask_3d` 逐元素比较；
5. 将 `s0_identity_implementation.json` 与 `preliminary_r.json` 复制到跟踪 compact evidence，并确保阶段台账引用远端存在的证据；
6. 增加相应失败闭锁测试；
7. 修正 D2 README 的交接相对路径并保持 current/history 一致。

完整细节以 `07_D2V2实现静态复核与证据补丁裁决.md` 为准。V2 科学阈值、算例集合和分类规则保持不变。

## 禁止越过的边界

在下一次 Pro 复核确认证据补丁通过前：

```text
S0_EXECUTION = PROHIBITED
D2_DYNAMIC_EXECUTION = PROHIBITED
```

因此不得运行：

- `run_d2_source_attribution.py verify`；
- F6/F9/Z6/Z9/T9；
- R9-BASE/R9-ALL；
- 条件五变体 R multiway；
- Q1R3 时间组；
- `13.5/20.25` 格动态算例；
- 完整 Q1R4。

不得修改 `01_最终理论/**` 或 `02_模拟程序/**`。

## 当前结论上限

```text
Q1R3_SPATIAL_STAGE_FAIL_CLOSED__TEMPORAL_NOT_AUTHORIZED
Q1R4_D0_FAIL_CLOSED_AT_STATIC_GEOMETRY
D1_DYNAMIC_SHADOW_COMPLETE__GEOMETRY_EFFECT_SMALL_IN_REGISTERED_WINDOW
D2_V2_IMPLEMENTED__NOT_EXECUTED__EVIDENCE_PATCH_REQUIRED
NUMERICAL_QUALIFICATION = NOT_DEMONSTRATED
PHYSICAL_VALIDATION = NOT_PERFORMED
EXPERIMENTAL_VALIDATION = NOT_PERFORMED
APPARATUS_PREDICTION = DISALLOWED
```

Codex 完成证据补丁、纯测试和静态检查后，应更新交接、提交并推送 `main`，随后停止等待 Pro 复核；不得自动开始 S0。
