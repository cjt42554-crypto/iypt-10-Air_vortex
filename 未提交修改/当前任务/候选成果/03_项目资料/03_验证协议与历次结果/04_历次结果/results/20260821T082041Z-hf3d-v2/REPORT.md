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
| `unit_tests` | PASS | {'returncode': 0, 'count': 159} |
| `source_unchanged` | PASS | 13cbd71fabd2cdf4b1e16667011e20f6ea1d2bdfa6e8024972d570b9fe4aff5d |
| `variable_density_projection` | PASS | {'seed': 20270821, 'divergence_before_s_inv': 26.729343298375525, 'divergence_after_s_inv': 4.042298407114231e-08, 'divergence_ratio': 1.5123074151095591e-09, 'pressure_relative_residual': 9.375886698404798e-10, 'iterations': 99, 'active_unknowns': 1789, 'matrix_nonzeros': 11523, 'pressure_sha256': '23cb654429de0e3a9beb98e3c4ff254fd9e627f395cf5a78cf9cddb09f8a8a4b'} |
| `hydrostatic_rest` | PASS | {'maximum_speed_m_s': 7.327138112373261e-07, 'maximum_funnel_depth_m': 4.0963980124653077e-10} |
| `open_boundary_volume_balance` | PASS | 2.104863781313975e-13 |
| `phase_boundedness_and_repair` | PASS | {'maximum_cumulative_clip_relative': 2.1042957729695167e-13, 'maximum_cumulative_repair_relative': 0.0} |
| `time_refinement_trend` | PASS | {'relative_l2_against_finest': [0.3761954170958064, 0.17477580615797003, 0.0], 'medium_to_coarse_error_ratio': 0.46458781318290104, 'maximum_dt_s': [0.00015, 7.5e-05, 3.75e-05]} |
| `rotation_reversal_symmetry` | PASS | {'phase_mirror_relative_l2': 0.040675494198434116, 'velocity_mirror_relative_l2': 0.0052004009467090774, 'kinetic_energy_relative_difference': 2.888046525918364e-06, 'angular_momentum_antisymmetry_relative_difference': 1.399337958603767e-06} |

## 物理 GO 审计（允许诚实 NO-GO）

| 门 | 结果 | 观测值 |
|---|---:|---|
| `dynamic_grid_observables` | NO-GO | {'kinetic_energy_relative_difference': 0.1786918361322631, 'angular_momentum_relative_difference': 0.148698991553274, 'limit': 0.05} |
| `capsule_resolution` | NO-GO | {'finest_bar_diameter_cells': 1.875, 'minimum': 6} |
| `sealed_experiment_validation` | NO-GO | NOT_PROVIDED_AND_NOT_FABRICATED |

## 时间细化

- 粗/中时间步相对最细三维速度误差：`[0.3761954170958064, 0.17477580615797003, 0.0]`。
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
| `hydrostatic-8` | 8×8×10 | 0.0001 | 0 | 20 | 1.10091e-14 | -1.02177e-12 | 7.32714e-07 | 1.423e-16 | 0.052 |
| `hydrostatic-12` | 12×12×15 | 0.0001 | 0 | 20 | 4.81291e-25 | 3.679e-17 | 8.9119e-12 | -2.846e-16 | 0.065 |
| `hydrostatic-16` | 16×16×20 | 0.0001 | 0 | 20 | 1.62208e-25 | -8.54064e-22 | 1.62124e-11 | 0.000e+00 | 0.099 |
| `time-0p00015` | 12×12×15 | 0.00015 | 200 | 28 | 0.0400945 | 0.00512754 | 0.887651 | -7.116e-16 | 0.129 |
| `time-7p5e-05` | 12×12×15 | 7.5e-05 | 200 | 56 | 0.0550111 | 0.0063466 | 0.879813 | -1.423e-16 | 0.242 |
| `time-3p75e-05` | 12×12×15 | 3.75e-05 | 200 | 108 | 0.0705891 | 0.00731708 | 0.886948 | 1.139e-15 | 0.455 |
| `grid-8` | 8×8×10 | 7.5e-05 | 200 | 56 | 0.0749577 | 0.00768279 | 0.877064 | -2.846e-16 | 0.159 |
| `grid-12` | 12×12×15 | 7.5e-05 | 200 | 56 | 0.0550111 | 0.0063466 | 0.879813 | -1.423e-16 | 0.235 |
| `grid-16` | 16×16×20 | 7.5e-05 | 200 | 56 | 0.0419927 | 0.00540352 | 0.953183 | 2.846e-16 | 0.356 |
| `grid-20` | 20×20×25 | 7.5e-05 | 200 | 56 | 0.034489 | 0.00460002 | 0.993714 | -2.105e-13 | 0.567 |
| `reversal-negative` | 12×12×15 | 7.5e-05 | -200 | 56 | 0.0550109 | -0.00634659 | 0.879804 | -2.846e-16 | 0.235 |

## 明确不允许的解读

- `PASS_NUMERICAL_IMPLEMENTATION_ONLY` 不是物理模型验证，也不是实验验证。
- 本动态网格序列没有解析磁子直径和边界层；任何绝对临界 rpm、接触时刻、气道半径均为 NO-GO。
- 体积守恒、静水保持和压力投影通过，不能替代界面曲率、润湿和湍流分辨率研究。
- 本报告不评价前向理论，也不提出结构性理论。
