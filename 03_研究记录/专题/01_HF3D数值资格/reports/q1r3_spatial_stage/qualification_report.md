# HF3D 数值资格报告

## 总结论

- 状态：`INCOMPLETE_MISSING_OR_UNREADABLE_RUNS`。
- 结论上限始终是冻结连续模型内的数值资格；本报告不构成实验验证。
- 装置级临界转速、真实接触时刻与稳定气道半径仍须由独立实验检验。

## 细化设计门

- 设计门通过：True。
- `spatial`：登记步长=[5.5e-06, 5.5e-06, 5.5e-06]，静态有效步长=[5.5e-06, 5.5e-06, 5.5e-06]。
- `temporal`：登记步长=[5.5e-06, 2.75e-06, 1.375e-06]，静态有效步长=[5.5e-06, 2.75e-06, 1.375e-06]。

## 缺失算例

- `time_L1_2.750e-06s`
- `time_L2_1.375e-06s`

## 运行与配置审计

- `space_L0_4cells`：完成=True，源身份有效=True，最差方向磁子直径格数=4.0，诊断步数=3640，墙钟=388.645 s，峰值 RSS=6.80452e+08 byte，登记步长命中率=0.994505。
- `space_L1_6cells`：完成=True，源身份有效=True，最差方向磁子直径格数=6.0，诊断步数=3640，墙钟=1995.82 s，峰值 RSS=1.02508e+09 byte，登记步长命中率=0.994505。
- `space_L2_9cells`：完成=True，源身份有效=True，最差方向磁子直径格数=9.0，诊断步数=3650，墙钟=7707.02 s，峰值 RSS=2.20803e+09 byte，登记步长命中率=0.957808。
- `time_L0_5.500e-06s`：完成=True，源身份有效=True，最差方向磁子直径格数=9.0，诊断步数=3650，墙钟=7707.02 s，峰值 RSS=2.20803e+09 byte，登记步长命中率=0.957808。

## 执行环境身份

- 可读完整/终端运行证据的源码边界身份一致：True；逻辑证据数=4。
- 已加载完整运行之间的环境身份一致：True。
- 已加载逻辑算例覆盖：4/6；全部覆盖=False。
- 已加载完整运行均满足压力加速器要求：True。
- 整个清单已完整证明压力加速器要求：False。

## 开边界质量合同

- 合同：`OPEN_BOUNDARY_INVENTORY_CONSERVATION_SPLIT_V1`；审计通过=True。
- 数值守恒门使用按规定开边界通量修正后的 `liquid_volume_balance_error_relative`。
- `liquid_mass_error_relative` 在本合同中重分类为液体净存量变化观测；不设无物理标定依据的绝对通过阈值，但必须有有限值并参与平滑量收敛。
- 库存观测已登记为平滑量：True。

## 数值质量门

- `space_L0_4cells`：通过=True。
  - `liquid_inventory_change_relative`：值=1.04503e-12，绝对门=False，角色=`MODEL_DOMAIN_AND_BOUNDARY_INVENTORY_OBSERVABLE`。
- `space_L1_6cells`：通过=True。
  - `liquid_inventory_change_relative`：值=0.000442873，绝对门=False，角色=`MODEL_DOMAIN_AND_BOUNDARY_INVENTORY_OBSERVABLE`。
- `space_L2_9cells`：通过=True。
  - `liquid_inventory_change_relative`：值=0.00140927，绝对门=False，角色=`MODEL_DOMAIN_AND_BOUNDARY_INVENTORY_OBSERVABLE`。
- `time_L0_5.500e-06s`：通过=True。
  - `liquid_inventory_change_relative`：值=0.00140927，绝对门=False，角色=`MODEL_DOMAIN_AND_BOUNDARY_INVENTORY_OBSERVABLE`。

## temporal 收敛

- 组状态：`INCOMPLETE`。

## spatial 收敛

- 组状态：`FAIL`。
- 实际时间步身份门：通过=True，登记结构有效=True，实际结构有效=True。
- `funnel_depth_m`：通过=False，两最细层相对 $L^2$ 误差=1.48397，观测阶=0.712928，细网格 GCI=5.53421。
- `min_gap_to_bar_m`：通过=False，两最细层相对 $L^2$ 误差=0.00419548，观测阶=n/a，细网格 GCI=n/a。
- `kinetic_energy_lab_j`：通过=False，两最细层相对 $L^2$ 误差=0.446063，观测阶=0.190592，细网格 GCI=6.93997。
- `angular_momentum_z_kg_m2_s`：通过=False，两最细层相对 $L^2$ 误差=0.329424，观测阶=n/a，细网格 GCI=n/a。
- `axisymmetry_error`：通过=False，两最细层相对 $L^2$ 误差=0.401473，观测阶=0.215812，细网格 GCI=5.48778。
- `liquid_mass_error_relative`：通过=False，两最细层相对 $L^2$ 误差=0.654378，观测阶=n/a，细网格 GCI=n/a。
- `first_contact_proxy` 事件一致性：通过=True，首次开启时间差=n/a s。
- `connection_candidate` 事件一致性：通过=True，首次开启时间差=n/a s。
- `finite_channel_candidate` 事件一致性：通过=True，首次开启时间差=n/a s。

## 证据边界

`NUMERICALLY_QUALIFIED_WITHIN_FROZEN_MODEL` 只表示在预注册输入、网格路径、时间步路径和误差门下，离散结果显示稳定收敛。它不证明连续模型遗漏项可以忽略，也不证明模型参数对应真实装置，更不等于实验预测通过。
