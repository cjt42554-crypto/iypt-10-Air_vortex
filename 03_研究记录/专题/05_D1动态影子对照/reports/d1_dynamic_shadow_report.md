# D1 G0/G1 锁步动态影子对照报告

## 裁决

```text
D1_DYNAMIC_SHADOW_COMPLETE__GEOMETRY_EFFECT_SMALL_IN_REGISTERED_WINDOW__STOP_FOR_PRO_REVIEW
```

执行提交：`68ef0dcef63a652eda348b8439ef62c13a812642`。

几何故障归因标签为：

```text
GEOMETRY_EFFECT_SMALL_IN_REGISTERED_WINDOW
```

该标签只解释登记短窗口内 G0/G1 布尔几何差异对既有 Q1R3 失败形态的贡献，不证明 G1 更接近连续方程或实验。

## 主要观测量指标

| 观测量 | max D | max eta | rho |
|---|---:|---:|---:|
| funnel_depth_m | 0.0258418 | 0.0258418 | 0.976337 |
| kinetic_energy_lab_j | 5.16091e-05 | 0.000153855 | 0.999846 |
| angular_momentum_z_kg_m2_s | 2.04859e-05 | 7.25894e-05 | 0.999931 |
| axisymmetry_error | 1.17405e-05 | 2.0974e-05 | 1.00002 |

## 阶段门

| 阶段 | 结果 |
|---|---|
| clean_main_at_analysis_start | PASS |
| formal_source_identity | PASS |
| S1_G0_self_identity | PASS |
| S2_CUDA_smoke | PASS |
| S3_level_6 | PASS |
| S4_level_9 | PASS |
| all_execution_commits_match | PASS |
| source_stable_across_full_windows | PASS |
| independent_materiality_analysis | PASS |

## 结论边界

- Q1R3 空间阶段既有失败闭锁没有被撤销；
- 本轮不是空间或时间收敛研究；
- G1 没有提升到正式求解器；
- 没有执行物理验证或实验验证；
- 不允许临界转速、长期稳态、空气通道稳定性或装置级预测；
- 报告完成后停止，等待 Pro 复核。

## 原始证据

本地原始证据清单：`90_候选工作区/current/05_D1动态影子对照/artifacts/d1_dynamic_shadow_manifest.json`。
