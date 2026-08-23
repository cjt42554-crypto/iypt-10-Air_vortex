# F1.0 前向数值实现冻结验证报告

- 总结论：`PASS_NUMERICAL_IMPLEMENTATION_ONLY`
- 协议 SHA-256：`c2b4b494795722b2904ab2f862a925b51c18b40c2e36163dd8c5de73591285d9`
- 源码边界不变：`True`
- 声称边界：这只验证数值实现与离散趋势，**不验证物理理论，不给出实验临界转速**。

## 硬门

| 门 | 结果 | 数值 |
|---|---:|---|
| `unit_tests` | PASS | returncode=0 |
| `source_unchanged` | PASS | e53c26e363d0a731fab5c3434a09813dc00a5460ff799302fd8aa6a5d45d9b85 |
| `all_volume_energy` | PASS | {'max_volume_error_m3': 1.9072446746926097e-11, 'max_energy_increase_j': 1.1102230246251565e-16} |
| `subcritical_disconnected` | PASS | [False, False, False, False] |
| `subcritical_finest_depth` | PASS | 0.03589053572387552 |
| `topology_all_resolved` | PASS | [True, True, True, True] |
| `topology_event_monotone` | PASS | [1.8, 1.7490348515860958, 1.675, 1.625] |
| `topology_extrapolation_fit` | PASS | 0.00562563118942509 |
| `topology_extrapolation_stability` | PASS | 0.015405514940960131 |
| `topology_finest_throat` | PASS | 0.04545454545454533 |
| `time_finest_event` | PASS | 0.0033212889291676095 |
| `time_finest_throat` | PASS | 0.0 |
| `time_all_resolved` | PASS | [True, True, True] |

## 空间—界面联合外推

- 四级线性 (h\to0) 首次触及外推：`1.5301 s`。
- 归一化拟合 RMS：`0.00562563`。
- 最终三级与全四级外推相对差：`0.0154055`。

该外推是数值尖界面极限诊断，不是装置级预测；参数仍为 `FIXED_REFERENCE_NOT_EXPERIMENTALLY_CALIBRATED`。

## 案例摘要

| 案例 | nr×nz | dt / s | 深度 / m | 首次触及 / s | 颈半径 / m | 墙钟 / s |
|---|---:|---:|---:|---:|---:|---:|
| `space-pre-24x42` | 24×42 | 0.002 | 0.0133245 |  | 0 | 0.496 |
| `space-top-24x42` | 24×42 | 0.002 | 0.0577668 | 1.8 | 0.0075 | 1.447 |
| `space-pre-32x56` | 32×56 | 0.002 | 0.0143711 |  | 0 | 0.638 |
| `space-top-32x56` | 32×56 | 0.002 | 0.06 | 1.74903 | 0.0084375 | 2.727 |
| `space-pre-48x84` | 48×84 | 0.0015 | 0.0155183 |  | 0 | 1.296 |
| `space-top-48x84` | 48×84 | 0.0015 | 0.06 | 1.675 | 0.0103125 | 6.204 |
| `space-pre-64x112` | 64×112 | 0.001 | 0.016096 |  | 0 | 2.683 |
| `space-top-64x112` | 64×112 | 0.001 | 0.06 | 1.625 | 0.00984375 | 10.295 |
| `time-dt-0p004` | 32×56 | 0.004 | 0.06 | 1.74934 | 0.0084375 | 2.867 |
| `time-dt-0p002` | 32×56 | 0.002 | 0.06 | 1.74903 | 0.0084375 | 2.753 |
| `time-dt-0p001` | 32×56 | 0.001 | 0.06 | 1.74571 | 0.0084375 | 2.265 |

## 负结果与不允许的解读

- 单一网格的接触/连通时刻不是定量结果；它对界面厚度有可见依赖。
- 通过能量和体积门不等于旋流闭合、轴对称假设或常数迁移率已被实验验证。
- 一格连通只是前兆；必须通过颈宽—界面厚度—网格联合门。
- 高保真后端和结构性理论不在本报告中。
