# 研究证据

这里保留支撑现有模型、未关闭问题和后续实验的必要记录。活动状态见 [current](../00_项目管理/交接说明_current.md)；正式实现见 [程序入口](../02_模拟程序/README.md)。

## 当前使用

- [理论—程序对齐](维护/2026-09-06_理论程序对齐/README.md)：CP 气体/有效区间、SH 固定域与移动面能量失败、TF 局部膜、匹配反例、HF 定向复核和最终回归。
- [压力与薄膜独立参考](维护/2026-09-05_压力与薄膜理论修订/README.md)：独立代数、边值与薄膜子问题核查，保留脚本、结果和源身份。
- [早期前向代数检查](维护/2026-09-05_前向理论修订/check_theory.py) 及 [当时结果](维护/2026-09-05_前向理论修订/final-validation.txt)。
- [实验前可证伪预测与测量接口](专题/31_实验前可证伪预测与测量接口.md)：观测问题的详细研究依据；实际准备顺序见 [实验入口](../04_实验与数据/README.md)。

## 高保真未关闭问题的证据

| 主题 | 保留结果与输入 | 用途 |
| --- | --- | --- |
| Q1R3 | [空间资格](专题/01_HF3D数值资格/reports/q1r3_spatial_stage/qualification_report.json)、[裁决](专题/01_HF3D数值资格/reports/q1r3_spatial_stage/spatial_stage_decision.md)、[冻结身份](专题/01_HF3D数值资格/reports/q1r3_freeze/freeze_manifest.json)、[配置](专题/01_HF3D数值资格/generated_q1r3/) | 六观测量空间资格失败；时间细化未完成 |
| Q1R4/D0 | [几何审计](专题/03_Q1R4诊断门/reports/static_geometry_audit.md)、[裁决](专题/03_Q1R4诊断门/reports/d0_diagnostic_decision.json)、同目录预注册与配置 | 记录几何离散差异，不能跳过直接推断动力学 |
| D1 | [静态分析](专题/04_D1可控磁子离散/reports/d1_static_analysis.md)、[G0 条件数](专题/04_D1可控磁子离散/reports/a0_g0_conditioning.json)、[动态对照](专题/05_D1动态影子对照/reports/d1_dynamic_shadow_report.md) | 保留几何候选与实际动态差异，未采用为正式磁子模型 |
| D2 | [来源归因报告](专题/06_D2数值误差来源归因/reports/d2_source_attribution_report.md)、[原始标量轨迹与归档身份](专题/06_D2数值误差来源归因/artifacts/evidence/runs/) | 有限窗口的失败机制筛查；重复派生汇总已移出当前目录 |
| D3 | [R1 失败取证](专题/07_D3跨网格时间步响应诊断/artifacts/r1_failure/d3_r1_s2_failure_forensic.md)、[V1 失败取证](专题/07_D3跨网格时间步响应诊断/artifacts/v1_failure/d3_v1_s1_failure_forensic.md)、[投影闭合](专题/07_D3跨网格时间步响应诊断/diagnostics/g9_dt2_projection_closure/artifacts/projection_closure_audit.json)、同目录预注册与配置 | 保留接受步 6695 的散度失败与源/输入身份 |
| D3 跨工况诊断 | [散度诊断](专题/17_HF3D性能优化/artifacts/p0/divergence_diagnostics.json)、[访问账本](专题/17_HF3D性能优化/artifacts/p0/divergence_access_ledger.json) | 区分压力残差、时间步及历史读取范围 |

这些冻结结果仍绑定原版本，未改写字节或阈值。旧报告中的路径、审批标签和命令按其原 Git 版本解释；不能作为当前权限或资格。大数组与 `partial.npz` 不在当前受版本管理的文件中，本次精简没有把原先缺失的数据变成可恢复数据。

## 历史恢复

2026-09-06 精简前的完整、已推送版本为 `ede9802d686eea72b592a0c6059f18119e2f515d`。旧执行器、候选补丁、逐轮审批记录、重复分析输出与中间测试日志从当前目录删除；所有原受 Git 管理的内容仍在该提交中。

查看单个旧文件，或把所需材料导出到新位置：

```bash
git show 'ede9802:03_研究记录/专题/04_D1可控磁子离散/audit_d1_geometry.py'
git archive --format=tar --output=/tmp/airvortex-before-cleanup.tar ede9802
```

历史计算应在对应源 commit 的独立副本中运行。上述快照用于找回材料，不证明当前源码与旧运行等价；Git 不备份从未入库的大数据。精确的旧候选路径关系见 [迁移清单](维护/2026-09-05_目录重组/migration.json)。
