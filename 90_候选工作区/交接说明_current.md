# 当前交接

## 当前状态

D2 V2 已按 Pro 授权完成旧 raw 隔离、同一 HEAD 全新执行、条件性 S5 和最终独立分析。

```text
D2_SOURCE_ATTRIBUTION_COMPLETE__Z_SMALL_NO_CLEAR_DIRECTION__T_MATERIAL_REDUCTION_SIGNAL__R_MIXED_OR_INDETERMINATE_NO_CLEAR_DIRECTION__STOP_FOR_PRO_REVIEW
```

下一步仅为 Pro 结果与证据复核；禁止自动进入新的 D2、Q1R4、长期稳态或装置预测阶段。

## 执行与来源身份

- 唯一执行提交：`0f7f720b0494c9976d6dbc1c594e24f4a9cc1f5a`；
- 执行分支：`main`；
- 正式源码树起止 SHA-256：`dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8`；
- 正式理论 `01_最终理论/**` 与正式程序 `02_模拟程序/**` 本轮均未修改。

旧提交 `75a34f9bab660d692b60e4d1e51cb1160d90d14d` 的预复核 raw 已按跟踪清单
`current/06_D2数值误差来源归因/artifacts/pre_review_raw_quarantine_manifest.json`
完整移至仓库同级 `../iypt10_d2_quarantine_75a34f9_pre_review_20260829/`。隔离目录未被
新执行读取，仍不得用于分类或资格结论。

## 已完成阶段

S0–S7 全部 PASS，没有失败阶段或未执行阶段：

1. S0 身份与实现验证；
2. S1 F6、F9；
3. S2 Z6、Z9；
4. S3 T9；
5. S4 R9_BASE/R9_ALL 与 preliminary-R；
6. preliminary-R 给出 `S5_required = true`，因此执行 S5 五路共同锁步；
7. S6 最终分析与 S7 完整性清单。

F6、F9、Z6、Z9、R 双路组与 R 五路组各有 1820 个接受步、0 次重试；T9
有 3640 个接受步、0 次重试。所有归档质量门通过，CUDA 已选中且无回退。

## D2 分类

| 因素 | 大小标签 | 方向标签 | 来源语言 |
|---|---|---|---|
| Z | SMALL | NO_CLEAR_DIRECTION | 不允许 |
| T | MATERIAL | REDUCTION_SIGNAL | 仅允许受限机制筛查语言 |
| R | MIXED_OR_INDETERMINATE | NO_CLEAR_DIRECTION | 不允许 |

开放顶边界存量预算为 `INVENTORY_DIFFERENCE_TOP_BOUNDARY_MEDIATED`。该标签不证明
开放边界条件错误，也不等价于数值不守恒。正式接触角作用带仍是 R 未覆盖的混杂项。

## 远端复核入口

1. `current/06_D2数值误差来源归因/reports/d2_source_attribution_report.json`；
2. `current/06_D2数值误差来源归因/reports/d2_source_attribution_report.md`；
3. `current/06_D2数值误差来源归因/artifacts/evidence/stage_ledger.json`；
4. `current/06_D2数值误差来源归因/artifacts/d2_source_attribution_manifest.json`；
5. `current/06_D2数值误差来源归因/artifacts/pre_review_raw_quarantine_manifest.json`。

最终清单记录 71 个跟踪紧凑证据文件，共 1,970,714 字节；同时记录 84 个本地
ignored raw 文件，共 1,095,143,236 字节。两组文件的大小与 SHA-256 已逐文件重算匹配。
raw 清单只提供完整性核对，不是外部签名或独立重跑。

## 当前结论上限

```text
Q1R3_SPATIAL_STAGE_FAIL_CLOSED__TEMPORAL_NOT_AUTHORIZED
Q1R4_D0_FAIL_CLOSED_AT_STATIC_GEOMETRY
D1_DYNAMIC_SHADOW_COMPLETE__GEOMETRY_EFFECT_SMALL_IN_REGISTERED_WINDOW
D2_SOURCE_ATTRIBUTION_COMPLETE__STOP_FOR_PRO_REVIEW
NUMERICAL_QUALIFICATION = NOT_DEMONSTRATED
SPATIAL_CONVERGENCE = NOT_ESTABLISHED
TEMPORAL_CONVERGENCE = NOT_ESTABLISHED
PHYSICAL_VALIDATION = NOT_PERFORMED
EXPERIMENTAL_VALIDATION = NOT_PERFORMED
APPARATUS_PREDICTION = DISALLOWED
```

D2 的有效范围仅是当前正式源码、正式 G0、6/9 格层及 0–0.010 s 窗口内的失败
机制筛查；不得据此撤销 Q1R3、提升正式 HF3D 或外推长期/装置行为。

## 接续规则

等待 Pro 复核本轮分类、证据链与结论措辞。收到新裁决前，不运行新的结果性算例，
不修改冻结阈值、种子、算法或正式源码，不把 T 的材料性减弱信号写成数值资格证明。
