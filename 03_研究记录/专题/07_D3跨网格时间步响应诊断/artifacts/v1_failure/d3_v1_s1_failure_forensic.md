# D3 V1 S1 失败取证包

状态：`D3_V1_S1_FAILURE_FORENSIC_GATE_PASS`

本取证包只冻结 V1 失败身份与证据边界。它不读取或报告主要物理观测量，不能支持 D3 科学分类。

## 绑定

- 协议：`D3_HF3D_CROSS_GRID_TEMPORAL_RESPONSE_V1`
- 执行提交：`90ed5c1019d8265f6e9730ff8a2d36be61deec8b`
- Pro 裁决提交：`b3dcc73a8d57290d372f7d8c41dbd721a01dccaf`
- 审计脚本 SHA-256：`dae37bb3c4c5a75dbdbf9e2ec6099de0dd6170cbe9b80ed280b9630b4ae0c1db`

## 冻结文件

| 角色 | 路径 | SHA-256 | 字节数 |
|---|---|---|---:|
| s0 | `90_候选工作区/current/07_D3跨网格时间步响应诊断/artifacts/runs/s0_identity_config_fresh_evidence_gate.json` | `e50905260c9d7732ef68e2cf76b4ed661b19383b4f92ade409463e58b63244b2` | 90783 |
| stage_ledger | `90_候选工作区/current/07_D3跨网格时间步响应诊断/artifacts/runs/stage_ledger.json` | `46ff32a79a62dd6cb544a04a7377d55888dff75e313f650bd97c32cf2df62afa` | 2090 |
| failure | `90_候选工作区/current/07_D3跨网格时间步响应诊断/artifacts/runs/execution_failure.json` | `d63bd745ee455bfaaba563b6b348bb7f7c822d5f6d33977c6f0f02daadb7def5` | 806 |
| case_summary | `90_候选工作区/current/07_D3跨网格时间步响应诊断/artifacts/runs/cases/G6_DT0/case_summary.json` | `15b2e76a57ad00dd93eb7a6652f4adf697f48c9dc772f57f666a771a537ca27a` | 59565 |
| partial_npz | `90_候选工作区/current/07_D3跨网格时间步响应诊断/artifacts/runs/cases/G6_DT0/partial.npz` | `d1b5ae2723ec60619d20fe2ce7d8d8010941083167fe1d71554990b898d9f3ed` | 17987661 |

## 阶段状态

| 阶段 | disposition | status |
|---|---|---|
| S0 | `EXECUTED` | `PASS` |
| S1 | `FAILED_STOPPED` | `D3_INCONCLUSIVE_FAIL_CLOSED` |
| S2 | `NOT_EXECUTED` | `NOT_EXECUTED` |
| S3 | `NOT_EXECUTED` | `NOT_EXECUTED` |

`subsequent_stages_executed = false`，V1 G6_DT0 仍为硬门失败，不得分类或复用。

## 原生输出时刻位级身份

`partial.npz` 只读取了成员清单和 `frame_time_s`；其他数组均未访问。

| 索引 | observed decimal | registered decimal | ULP | 相等 |
|---:|---:|---:|---:|---|
| 0 | 0.0 | 0.0 | 0 | true |
| 1 | 0.001 | 0.001 | 0 | true |
| 2 | 0.002 | 0.002 | 0 | true |
| 3 | 0.003 | 0.003 | 0 | true |
| 4 | 0.004 | 0.004 | 0 | true |
| 5 | 0.005 | 0.005 | 0 | true |
| 6 | 0.006 | 0.006 | 0 | true |
| 7 | 0.007 | 0.007 | 0 | true |
| 8 | 0.008 | 0.008 | 0 | true |
| 9 | 0.009000000000000001 | 0.009 | 1 | false |
| 10 | 0.01 | 0.01 | 0 | true |

### Observed 位模式

| 索引 | float.hex | uint64 |
|---:|---|---:|
| 0 | `0x0.0p+0` | 0 |
| 1 | `0x1.0624dd2f1a9fcp-10` | 4562254508917369340 |
| 2 | `0x1.0624dd2f1a9fcp-9` | 4566758108544739836 |
| 3 | `0x1.89374bc6a7efap-9` | 4569063951553953530 |
| 4 | `0x1.0624dd2f1a9fcp-8` | 4571261708172110332 |
| 5 | `0x1.47ae147ae147bp-8` | 4572414629676717179 |
| 6 | `0x1.89374bc6a7efap-8` | 4573567551181324026 |
| 7 | `0x1.cac083126e979p-8` | 4574720472685930873 |
| 8 | `0x1.0624dd2f1a9fcp-7` | 4575765307799480828 |
| 9 | `0x1.26e978d4fdf3cp-7` | 4576341768551784252 |
| 10 | `0x1.47ae147ae147bp-7` | 4576918229304087675 |

### Registered 位模式

| 索引 | float.hex | uint64 |
|---:|---|---:|
| 0 | `0x0.0p+0` | 0 |
| 1 | `0x1.0624dd2f1a9fcp-10` | 4562254508917369340 |
| 2 | `0x1.0624dd2f1a9fcp-9` | 4566758108544739836 |
| 3 | `0x1.89374bc6a7efap-9` | 4569063951553953530 |
| 4 | `0x1.0624dd2f1a9fcp-8` | 4571261708172110332 |
| 5 | `0x1.47ae147ae147bp-8` | 4572414629676717179 |
| 6 | `0x1.89374bc6a7efap-8` | 4573567551181324026 |
| 7 | `0x1.cac083126e979p-8` | 4574720472685930873 |
| 8 | `0x1.0624dd2f1a9fcp-7` | 4575765307799480828 |
| 9 | `0x1.26e978d4fdf3bp-7` | 4576341768551784251 |
| 10 | `0x1.47ae147ae147bp-7` | 4576918229304087675 |

仅索引 9 相差 1 ULP；索引 0--8 和 10 逐位相同。

## 缺失的后续证据

- `90_候选工作区/current/07_D3跨网格时间步响应诊断/artifacts/runs/cases/G6_DT1`
- `90_候选工作区/current/07_D3跨网格时间步响应诊断/artifacts/runs/cases/G6_DT2`
- `90_候选工作区/current/07_D3跨网格时间步响应诊断/artifacts/runs/cases/G9_DT0`
- `90_候选工作区/current/07_D3跨网格时间步响应诊断/artifacts/runs/cases/G9_DT1`
- `90_候选工作区/current/07_D3跨网格时间步响应诊断/artifacts/runs/cases/G9_DT2`
- `90_候选工作区/current/07_D3跨网格时间步响应诊断/reports/d3_cross_grid_temporal_report.json`
- `90_候选工作区/current/07_D3跨网格时间步响应诊断/reports/d3_cross_grid_temporal_report.md`
- `90_候选工作区/current/07_D3跨网格时间步响应诊断/artifacts/d3_cross_grid_temporal_manifest.json`
- `90_候选工作区/current/07_D3跨网格时间步响应诊断/artifacts/evidence`
- `90_候选工作区/current/07_D3跨网格时间步响应诊断/artifacts/runs/.s3_publication_transaction`
- `90_候选工作区/current/07_D3跨网格时间步响应诊断/artifacts/runs/.stage_ledger.s3-commit.tmp`

## 结论边界

```text
D3_V1_S1_FAIL_CLOSED = UPHELD
D3_V1_FAILURE_CAUSE = CANDIDATE_OUTPUT_SCHEDULE_FLOAT_ACCUMULATION_DEFECT
D3_V1_SCIENTIFIC_CLASSIFICATION = NOT_FORMED
```
