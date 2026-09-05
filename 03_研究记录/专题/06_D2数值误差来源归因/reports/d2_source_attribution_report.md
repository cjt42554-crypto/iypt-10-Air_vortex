# D2 HF3D 数值误差来源归因 V2 报告

## 裁决

~~~text
D2_SOURCE_ATTRIBUTION_COMPLETE__Z_SMALL_NO_CLEAR_DIRECTION__T_MATERIAL_REDUCTION_SIGNAL__R_MIXED_OR_INDETERMINATE_NO_CLEAR_DIRECTION__STOP_FOR_PRO_REVIEW
~~~

执行提交：0f7f720b0494c9976d6dbc1c594e24f4a9cc1f5a。

D2 的结论只用于当前正式源码、正式 G0、6/9 格层和 0–0.010 s 窗口内的失败机制筛查。

## 三类因素标签

| 因素 | 大小标签 | 方向标签 | 可使用来源语言 |
|---|---|---|---|
| Z | SMALL | NO_CLEAR_DIRECTION | 否 |
| T | MATERIAL | REDUCTION_SIGNAL | 是 |
| R | MIXED_OR_INDETERMINATE | NO_CLEAR_DIRECTION | 否 |

MATERIAL 但没有 REDUCTION_SIGNAL 只表示结果敏感，不构成空间失败来源归因。

## 主要量 eta 与 rho

| 因素 | 观测量 | eta | rho |
|---|---|---:|---:|
| Z | funnel_depth_m | 2.19459e-12 | 1 |
| Z | kinetic_energy_lab_j | 3.17125e-25 | 1 |
| Z | angular_momentum_z_kg_m2_s | 7.18785e-18 | 1 |
| Z | axisymmetry_error | 6.0828e-14 | 1 |
| T | funnel_depth_m | 0.781693 | 0.220843 |
| T | kinetic_energy_lab_j | 0.865306 | 0.139267 |
| T | angular_momentum_z_kg_m2_s | 0.816485 | 0.183602 |
| T | axisymmetry_error | 0.160972 | 1.12545 |
| R | funnel_depth_m | 0 | 1 |
| R | kinetic_energy_lab_j | 0.0295113 | 1.0287 |
| R | angular_momentum_z_kg_m2_s | 0.00588662 | 1.00588 |
| R | axisymmetry_error | 0.27205 | 0.728767 |

## 条件阶段与存量预算

S5：EXECUTED。

开放顶边界预算 r_flux = 2.68462e-07；标签：INVENTORY_DIFFERENCE_TOP_BOUNDARY_MEDIATED。

该存量标签不证明开放边界条件错误，也不等价于数值不守恒。

## 硬门

| 证据组 | 结果 |
|---|---|
| F6 | PASS |
| F9 | PASS |
| Z6 | PASS |
| Z9 | PASS |
| T9 | PASS |
| r_pair | PASS |
| r_multiway | PASS |

## 未解决混杂与结论上限

- 正式接触角作用带仍为 1.5 倍最大网格间距，R 不覆盖这一路径；
- D2 不撤销 Q1R3 空间失败，不证明空间或时间收敛；
- 未修改或提升正式 HF3D；
- 未执行物理验证或实验验证；
- 不允许长期稳态、临界转速或装置级预测；
- 报告后停止，等待 Pro 复核。

## 远端可复核证据

跟踪清单：90_候选工作区/current/06_D2数值误差来源归因/artifacts/d2_source_attribution_manifest.json。

阶段台账：90_候选工作区/current/06_D2数值误差来源归因/artifacts/evidence/stage_ledger.json。
