# 当前交接

## 当前状态

Pro 已完成对 D1 G0/G1 动态影子对照的独立复核。未发现足以推翻冻结分类的实现级或分析级错误，继续接受：

```text
GEOMETRY_EFFECT_SMALL_IN_REGISTERED_WINDOW
```

当前状态更新为：

```text
D1_DYNAMIC_SHADOW_REVIEW_ACCEPTED__D2_SOURCE_ATTRIBUTION_V2_AUTHORIZED_NOT_EXECUTED
```

D1 结论只适用于名义中心位置、`6/9` 格层和 `0–0.010 s`。它削弱了“正式 G0 布尔磁子掩膜是 Q1R3 空间失败主因”的假设，但不证明 G1 正确、长期几何效应很小、HF3D 已收敛或可以预测实验。

## Codex 唯一任务入口

依次读取：

1. 顶层 `AGENTS.md`；
2. 本文件；
3. `90_候选工作区/current/06_D2数值误差来源归因科学裁决与执行规划.md`；
4. `90_候选工作区/current/06_D2数值误差来源归因/d2_source_attribution_preregistration_v2.json`；
5. `90_候选工作区/current/06_D2数值误差来源归因/README.md`。

随后只在以下候选目录实现并执行 D2：

```text
90_候选工作区/current/06_D2数值误差来源归因/
```

D2 尚未实现或执行。不要从未被 `main` 引用的草稿提交中推断额外授权。

## D2 要回答的问题

D2 重新执行 `F6/F9`、`0–0.010 s` 强迫基线，依次量化：

- `Z`：零驱动背景、静止两相寄生流和开放顶边界存量响应；
- `T`：九格层把时间步从 `5.5e-6 s` 减半到 `2.75e-6 s` 的影响；
- `R`：三项显式正则化控制保持六格相同物理宽度的影响。

九格层正确目标值：

```text
interface_thickness_cells = 3.7777777777777777
boundary_layer_cells = 2.25
surface_tension_smoothing_cells = 1.125
```

其中界面厚度按 `minimum_spacing_m` 缩放；壁层和平滑宽度按 `maximum_spacing_m` 缩放。硬编码 `1.5 * maximum_spacing_m` 接触角作用带不在 R 覆盖范围内。

每项必须同时报告：

```text
eta：效应大小
rho：是否沿缩小 F6–F9 差异的方向
```

`MATERIAL` 而没有缩差方向，只能称为敏感性，不得称为失败来源。

`R` 不是 `SMALL` 时，必须运行：

```text
R9-BASE / R9-ALL / R9-IF / R9-BL / R9-ST
```

五变体共同锁步组，使用同一条基线和共同试步序列后再计算交互残差。

## 关键执行边界

- 正式 G0 几何保持不变，D2 不再使用 G1；
- 不修改 `01_最终理论/**` 或 `02_模拟程序/**`；
- 不继续 Q1R3 时间中、细层；
- 不把 D1 延长到 `0.020 s`；
- 不运行 `13.5/20.25` 格动态算例；
- 不建立或运行完整 Q1R4；
- 任一身份、配置、CUDA、数值质量或锁步硬门失败，D2 失败闭锁；
- 总报告必须跟踪完整分类轨迹和小型配置、来源、结果、时序、锁步摘要；
- 总报告后更新交接、提交并推送 `main`，然后停止等待 Pro 复核。

## 当前证据与结论上限

D1 两层均达到 `0.010 s`，各 `1820` 个共同接受步、`0` 次重试，质量门通过，实际 CUDA 无回退，正式源码树前后保持：

```text
dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8
```

当前结论上限：

```text
Q1R3_SPATIAL_STAGE_FAIL_CLOSED__TEMPORAL_NOT_AUTHORIZED
Q1R4_D0_FAIL_CLOSED_AT_STATIC_GEOMETRY
D1_DYNAMIC_SHADOW_COMPLETE__GEOMETRY_EFFECT_SMALL_IN_REGISTERED_WINDOW
D2_SOURCE_ATTRIBUTION_V2_AUTHORIZED_NOT_EXECUTED
NUMERICAL_QUALIFICATION = NOT_DEMONSTRATED
PHYSICAL_VALIDATION = NOT_PERFORMED
EXPERIMENTAL_VALIDATION = NOT_PERFORMED
APPARATUS_PREDICTION = DISALLOWED
```

本次只完成 D1 Pro 复核并写入 D2 V2 科学规划、机器合同和交接。正式理论与正式程序未变化，D2 动态步数为零。
