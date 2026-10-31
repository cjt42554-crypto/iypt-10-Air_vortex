# 研究证据

这里保留支撑当前模型、未关闭问题和后续实验的必要记录。活动状态见 [current](../00_项目管理/交接说明_current.md)；唯一正式实现见 [程序入口](../02_模拟程序/README.md)。CP、SH、TF 和 HF3D 对应不同物理近似与用途，不是按版本号递增的替代关系。

## 当前使用

- [理论—程序对齐](维护/2026-09-06_理论程序对齐/README.md)：CP 气体/有效区间、SH 固定域与移动面能量失败、TF 局部膜、匹配反例、HF 定向复核及最终回归。
- [压力与薄膜独立参考](维护/2026-09-05_压力与薄膜理论修订/README.md)：独立代数、边值与薄膜子问题核查，保留脚本、结果和源身份。
- [早期前向代数检查](维护/2026-09-05_前向理论修订/check_theory.py) 及 [当时结果](维护/2026-09-05_前向理论修订/final-validation.txt)：与后续压力/薄膜检查覆盖不同，保留独立参考。
- [实验前可证伪预测与测量接口](专题/31_实验前可证伪预测与测量接口.md)：观测问题的详细研究依据；实际准备顺序见 [实验入口](../04_实验与数据/README.md)。

## 高保真历史证据与结论边界

每项保留原始 JSON、配置、来源清单及仍需使用的轨迹。旧报告的重复 Markdown 展示合并为下表；所有保留的冻结文件维持原字节，详细数值和阈值以链接的原始报告为准。

| 主题 | 原始证据 | 必须保留的结论与限制 |
| --- | --- | --- |
| Q1R3 冻结与空间资格 | [冻结身份](专题/01_HF3D数值资格/reports/q1r3_freeze/freeze_manifest.json)、[资格报告](专题/01_HF3D数值资格/reports/q1r3_spatial_stage/qualification_report.json)、[空间裁决](专题/01_HF3D数值资格/reports/q1r3_spatial_stage/spatial_stage_decision.json)、[配置](专题/01_HF3D数值资格/generated_q1r3/) | 4/6/9 格的六个平滑观测量空间资格全部失败；各算例质量门通过不能抵消空间失败。时间中、细层未执行，因此顶层报告的 INCOMPLETE 不应掩盖已确定的空间 FAIL。三个拓扑事件均未激发，不能据此宣称接触或成道获得验证。 |
| Q1R4 / D0 | [静态几何](专题/03_Q1R4诊断门/reports/static_geometry_audit.json)、[早停裁决](专题/03_Q1R4诊断门/reports/d0_diagnostic_decision.json)、[原预注册](专题/03_Q1R4诊断门/q1r4_diagnostic_preregistration.json) | 6 格层质心门失败；层间跳变门通过。没有动态求解，资源探针与来源解耦未运行；尚不能把体素几何认定为 Q1R3 失败原因，不能事后放宽阈值。 |
| D1 静态几何 | [G0 条件数](专题/04_D1可控磁子离散/reports/a0_g0_conditioning.json)、[G1 静态分析](专题/04_D1可控磁子离散/reports/d1_static_analysis.json)、[原预注册](专题/04_D1可控磁子离散/d1_geometry_preregistration.json) | G0 的 6 格层对排序和 1 ULP 位移敏感；G1 静态门通过。G1–G2 加权对称差没有预登记通过阈值，不参与静态总门；静态通过不证明动态收敛。 |
| D1 动态影子对照 | [报告](专题/05_D1动态影子对照/reports/d1_dynamic_shadow_report.json)、[归档身份](专题/05_D1动态影子对照/artifacts/d1_dynamic_shadow_manifest.json) | 登记短窗口内 G0/G1 几何效应小，不证明 G1 更接近连续方程或实验。G1 未替代正式模型，Q1R3 空间失败仍成立。 |
| D2 来源归因 | [报告](专题/06_D2数值误差来源归因/reports/d2_source_attribution_report.json)、[原始标量轨迹与归档身份](专题/06_D2数值误差来源归因/artifacts/evidence/runs/)、[阶段记录](专题/06_D2数值误差来源归因/artifacts/evidence/stage_ledger.json) | 在 6/9 格、0–0.010 s 窗口中，Z 小且无方向信号，T 有显著减弱信号，R 混合或不确定且无明确方向。敏感性本身不是来源归因；R 未覆盖 `1.5 * grid.maximum_spacing_m` 接触角作用带。开放顶边界存量响应不等于数值不守恒。 |
| D3 V1 | [失败取证](专题/07_D3跨网格时间步响应诊断/artifacts/v1_failure/d3_v1_s1_failure_forensic.json) | G6_DT0 在原生输出索引 9 的时刻相差 1 ULP，原因是候选输出调度浮点累加；V1 失败不被后续 R1 结果覆盖。只检查了 partial 的 `frame_time_s`，没有形成科学分类。 |
| D3 R1 | [失败取证](专题/07_D3跨网格时间步响应诊断/artifacts/r1_failure/d3_r1_s2_failure_forensic.json)、[原配置](专题/07_D3跨网格时间步响应诊断/generated_configs/) | G9_DT2 在接受步 6695、`t≈0.009196625 s` 的散度 `1.02491859e-4 s^-1` 超过 `1e-4`；未到终止时刻及输出不完整是后果，不能算第二个独立根因。S3 未执行，不能从失败 partial 推断装置预测。 |
| D3 投影闭合 | [离散审计](专题/07_D3跨网格时间步响应诊断/diagnostics/g9_dt2_projection_closure/artifacts/projection_closure_audit.json) | 独立复现失败步散度，代数残差解释率接近 1；但原 H-A/H-B/H-C 门均未通过，因此原审计终态仍为机制未确定。后续压力停止准则诊断与修复见当前对齐记录，不能倒改原报告结论。 |
| D3 跨工况诊断 | [散度诊断](专题/17_HF3D性能优化/artifacts/p0/divergence_diagnostics.json)、[访问记录](专题/17_HF3D性能优化/artifacts/p0/divergence_access_ledger.json) | 逐接受步的描述性诊断不读取物理观测量，不形成 D3 科学分类，也不单独证明压力耦合、孤立尖峰或其他机制。 |

Q1R3 的启动段及三个恒速子窗均未通过六观测量门，失败不能仅归因于加速启动。开边界存量变化与通量修正后的守恒残差是不同量。更细网格需要重新匹配共同时间步；原资源估计只是线性指标，不是显存实测或可行性保证。详细诊断、数值表与来源身份保留在空间裁决 JSON 中。

上述历史计算均绑定各自源码、输入、环境和覆盖范围，不构成当前程序的空间/时间收敛或装置验证。旧文件中的审批、暂停和阶段口令仅为历史背景，当前维护按根 AGENTS 与 current 执行。所有真实实验、标定和留出验证仍按实验入口单独判断。

同字节的 D2 配置、结果或轨迹仍保留在各阶段原路径：各自 manifest/provenance 记录的运行身份不同，不能仅凭文件哈希相同合并实验身份。大数组及 D3 原 `partial.npz` 原先就不在当前 Git 跟踪内容中，目录整理不能使这些缺失对象自动恢复。

## 历史恢复

2026-10-31 整理前的完整版本为 `13c49b1033f1623d1a9eb2e5a83a3110dc51f824`。本轮只将上述 13 份同名 JSON 的派生 Markdown 展示合并到本索引，并移除已完成工作的重复交接流水账；正式源码、理论、测试、配置、实验输入和冻结数据未改变。原摘要和旧交接可由该提交逐字节恢复：

```bash
git show '13c49b1033f1623d1a9eb2e5a83a3110dc51f824:03_研究记录/专题/07_D3跨网格时间步响应诊断/artifacts/r1_failure/d3_r1_s2_failure_forensic.md'
git archive --format=tar --output=/tmp/airvortex-before-20261031-cleanup.tar 13c49b1033f1623d1a9eb2e5a83a3110dc51f824
```

更早的执行器、候选补丁、逐轮审批记录、重复分析输出和中间日志已在 2026-09-06 精简，恢复基线为 `ede9802d686eea72b592a0c6059f18119e2f515d`：

```bash
git show 'ede9802d686eea72b592a0c6059f18119e2f515d:03_研究记录/专题/04_D1可控磁子离散/audit_d1_geometry.py'
```

浅克隆须先获取相应历史提交。历史计算在对应源版本的独立副本中运行，不能把恢复到的旧 PASS 当作当前资格；Git 不备份从未入库的大数据。旧路径关系及哈希见保留原字节的 [迁移清单](维护/2026-09-05_目录重组/migration.json)。
