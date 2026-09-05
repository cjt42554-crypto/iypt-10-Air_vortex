# D2 数值误差来源归因

当前状态：

```text
D2_SOURCE_ATTRIBUTION_COMPLETE__Z_SMALL_NO_CLEAR_DIRECTION__T_MATERIAL_REDUCTION_SIGNAL__R_MIXED_OR_INDETERMINATE_NO_CLEAR_DIRECTION__STOP_FOR_PRO_REVIEW
```

## 执行身份与旧证据隔离

旧提交 `75a34f9bab660d692b60e4d1e51cb1160d90d14d` 产生的预复核 raw 已按
`artifacts/pre_review_raw_quarantine_manifest.json` 完整移至仓库同级
`../iypt10_d2_quarantine_75a34f9_pre_review_20260829/`，未被本轮执行复用。

全新 D2 从 S0 到最终分析均绑定同一执行提交
`0f7f720b0494c9976d6dbc1c594e24f4a9cc1f5a`。正式源码树起止哈希均为
`dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8`。

## 阶段结果

S0–S7 全部执行并通过；没有失败阶段或未执行阶段。条件阶段 S5 因 preliminary-R
给出 `MIXED_OR_INDETERMINATE` 而按冻结合同执行，五个变体共同锁步完成。

| 阶段 | 内容 | 结果 |
|---|---|---|
| S0 | 身份与实现验证 | PASS |
| S1 | F6、F9 | PASS |
| S2 | Z6、Z9 | PASS |
| S3 | T9 | PASS |
| S4 | R9_BASE、R9_ALL 与 preliminary-R | PASS |
| S5 | R9_BASE、R9_ALL、R9_IF、R9_BL、R9_ST | PASS |
| S6 | 最终独立分析 | PASS |
| S7 | 证据清单 | PASS |

F6、F9、Z6、Z9、R 双路组和 R 五路组均有 1820 个接受步、0 次重试；T9
有 3640 个接受步、0 次重试。最终分析器独立复核了逐变体接受时间、CUDA
无回退、归档完整性、正式 G0 掩膜、零驱动门及 execution commit 绑定。

## 因素分类

| 因素 | 大小标签 | 方向标签 | 来源语言 |
|---|---|---|---|
| Z | SMALL | NO_CLEAR_DIRECTION | 不允许 |
| T | MATERIAL | REDUCTION_SIGNAL | 仅允许受限机制筛查语言 |
| R | MIXED_OR_INDETERMINATE | NO_CLEAR_DIRECTION | 不允许 |

开放顶边界存量预算标签为 `INVENTORY_DIFFERENCE_TOP_BOUNDARY_MEDIATED`；它不证明
开放边界条件错误，也不证明数值不守恒。正式接触角作用带仍是 R 未覆盖的混杂路径。

## 证据入口

1. `reports/d2_source_attribution_report.json`
2. `reports/d2_source_attribution_report.md`
3. `artifacts/evidence/stage_ledger.json`
4. `artifacts/d2_source_attribution_manifest.json`
5. `artifacts/pre_review_raw_quarantine_manifest.json`

最终清单记录 71 个跟踪紧凑证据文件，共 1,970,714 字节；另记录 84 个本地
ignored raw 文件，共 1,095,143,236 字节。清单提供完整性核对，不是外部签名或独立重跑。

## 结论上限与停止点

D2 只支持当前正式源码、正式 G0、6/9 格层及 0–0.010 s 窗口内的数值失败机制
筛查。它不撤销 Q1R3 空间失败，不建立空间或时间收敛，不建立数值资格，也未执行
物理或实验验证；长期稳态、临界转速和装置级预测仍禁止。

未修改 `01_最终理论/**` 或 `02_模拟程序/**`。当前停止，等待 Pro 对结果与证据的复核；
禁止自动进入后续阶段。
