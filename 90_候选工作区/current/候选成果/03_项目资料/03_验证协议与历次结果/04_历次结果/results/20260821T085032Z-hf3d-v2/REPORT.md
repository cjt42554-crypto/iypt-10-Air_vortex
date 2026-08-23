# HF3D v1 冻结验证报告

- 实现结论：`PASS_NUMERICAL_IMPLEMENTATION_ONLY`
- 物理预测结论：`NO_GO_UNDERRESOLVED_UNCONVERGED_UNVALIDATED`
- 总结论：`PASS_IMPLEMENTATION__NO_GO_PHYSICAL_PREDICTION`
- 协议 SHA-256：`e409d613063f6b20cc4e197026f63269f9aec6b15b5461b91f2d953cc587acbf`
- 源码边界不变：`True`
- 声称边界：通过只证明独立 HF3D 程序满足预注册实现门；不证明装置临界转速、稳定气道半径或实验真实性。

## 实现硬门

| 门 | 结果 | 观测值 |
|---|---:|---|
| `unit_tests` | PASS | {'returncode': 0, 'count': 160} |
| `source_unchanged` | PASS | 514eaf4d6be66e79292b62a2d48b28c87f24d037a66879535572fcb2d3ccded6 |
| `variable_density_projection` | PASS | {'seed': 20270821, 'divergence_before_s_inv': 26.729343298375525, 'divergence_after_s_inv': 4.058348057611738e-08, 'divergence_ratio': 1.518311921212963e-09, 'pressure_relative_residual': 7.76464590373194e-10, 'iterations': 67, 'active_unknowns': 1789, 'matrix_nonzeros': 11523, 'pressure_sha256': 'e414c33af986dece1f4106265db061496f1b42d78fc874b5abc13eec05724129'} |
| `hydrostatic_rest` | PASS | {'maximum_speed_m_s': 7.327124268188657e-07, 'maximum_funnel_depth_m': 4.0963785835623767e-10} |
| `open_boundary_volume_balance` | PASS | 1.8842729184987848e-13 |
| `phase_boundedness_and_repair` | PASS | {'maximum_cumulative_clip_relative': 6.515550601163828e-13, 'maximum_cumulative_repair_relative': 4.626810590790386e-13} |
| `time_refinement_trend` | PASS | {'relative_l2_against_finest': [0.3761954170953796, 0.17477580615911095, 0.0], 'medium_to_coarse_error_ratio': 0.46458781318646086, 'maximum_dt_s': [0.00015, 7.5e-05, 3.75e-05]} |
| `rotation_reversal_symmetry` | PASS | {'phase_mirror_relative_l2': 0.04067549419847815, 'velocity_mirror_relative_l2': 0.005200400946783432, 'kinetic_energy_relative_difference': 2.8880464448150414e-06, 'angular_momentum_antisymmetry_relative_difference': 1.3993378983348655e-06} |

## 物理 GO 审计（允许诚实 NO-GO）

| 门 | 结果 | 观测值 |
|---|---:|---|
| `dynamic_grid_observables` | NO-GO | {'kinetic_energy_relative_difference': 0.17869183613202047, 'angular_momentum_relative_difference': 0.14869899155295457, 'limit': 0.05} |
| `capsule_resolution` | NO-GO | {'finest_bar_diameter_cells': 1.875, 'minimum': 6} |
| `sealed_experiment_validation` | NO-GO | NOT_PROVIDED_AND_NOT_FABRICATED |

## 时间细化

- 粗/中时间步相对最细三维速度误差：`[0.3761954170953796, 0.17477580615911095, 0.0]`。
- 中/粗误差比：`0.464588`（实现趋势门通过不等于已到渐近收敛区）。

## 动态网格审计

- 最细两级动能相对差：`0.178692`。
- 最细两级角动量相对差：`0.148699`。
- 动态序列最细网格磁子直径仅 `1.875` 个最小网格，低于协议所需 6 格。
- 因此该序列的角色是暴露欠分辨和防止过度声称，不是产生装置级预测。

## 旋向反演

- 相场镜像相对 L2：`0.0406755`。
- 实验室速度镜像相对 L2：`0.0052004`。
- 动能相对差：`2.88805e-06`。
- 角动量反对称误差：`1.39934e-06`。

## 案例摘要

| 案例 | 网格 | max dt / s | rpm | 步数 | KE / J | Lz / kg m2 s | vmax / m s-1 | 体积收支误差 | 墙钟 / s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `hydrostatic-8` | 8×8×10 | 0.0001 | 0 | 20 | 1.10091e-14 | -1.02173e-12 | 7.32712e-07 | -1.423e-16 | 0.055 |
| `hydrostatic-12` | 12×12×15 | 0.0001 | 0 | 20 | 1.02448e-25 | -3.76298e-17 | 7.2094e-12 | -2.846e-16 | 0.066 |
| `hydrostatic-16` | 16×16×20 | 0.0001 | 0 | 20 | 7.54021e-26 | 4.40171e-22 | 1.13579e-11 | -1.423e-16 | 0.098 |
| `time-0p00015` | 12×12×15 | 0.00015 | 200 | 28 | 0.0400945 | 0.00512754 | 0.887651 | -1.423e-16 | 0.128 |
| `time-7p5e-05` | 12×12×15 | 7.5e-05 | 200 | 56 | 0.0550111 | 0.0063466 | 0.879813 | 2.846e-16 | 0.222 |
| `time-3p75e-05` | 12×12×15 | 3.75e-05 | 200 | 108 | 0.0705891 | 0.00731708 | 0.886948 | 2.846e-16 | 0.415 |
| `grid-8` | 8×8×10 | 7.5e-05 | 200 | 56 | 0.0749577 | 0.00768279 | 0.877064 | 4.270e-16 | 0.150 |
| `grid-12` | 12×12×15 | 7.5e-05 | 200 | 56 | 0.0550111 | 0.0063466 | 0.879813 | 2.846e-16 | 0.218 |
| `grid-16` | 16×16×20 | 7.5e-05 | 200 | 56 | 0.0419927 | 0.00540352 | 0.953183 | 0.000e+00 | 0.346 |
| `grid-20` | 20×20×25 | 7.5e-05 | 200 | 56 | 0.034489 | 0.00460002 | 0.993714 | -1.884e-13 | 0.547 |
| `reversal-negative` | 12×12×15 | 7.5e-05 | -200 | 56 | 0.0550109 | -0.00634659 | 0.879804 | 0.000e+00 | 0.219 |

## 明确不允许的解读

- `PASS_NUMERICAL_IMPLEMENTATION_ONLY` 不是物理模型验证，也不是实验验证。
- 本动态网格序列没有解析磁子直径和边界层；任何绝对临界 rpm、接触时刻、气道半径均为 NO-GO。
- 体积守恒、静水保持和压力投影通过，不能替代界面曲率、润湿和湍流分辨率研究。
- 本报告不评价前向理论，也不提出结构性理论。
