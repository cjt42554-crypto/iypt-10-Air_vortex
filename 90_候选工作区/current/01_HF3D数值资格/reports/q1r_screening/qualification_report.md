# HF3D 数值资格报告

## 总结论

- 状态：`FAIL_PROVENANCE_INPUT_OR_COMPLETION_GATE`。
- 结论上限始终是冻结连续模型内的数值资格；本报告不构成实验验证。
- 装置级临界转速、真实接触时刻与稳定气道半径仍须由独立实验检验。

## 细化设计门

- 设计门通过：True。
- `spatial`：登记步长=[7.5e-06, 7.5e-06, 7.5e-06]，静态有效步长=[7.5e-06, 7.5e-06, 7.5e-06]。
- `temporal`：登记步长=[7.5e-06, 3.75e-06, 1.875e-06]，静态有效步长=[7.5e-06, 3.75e-06, 1.875e-06]。

## 缺失算例

- `time_L1_3.750e-06s`
- `time_L2_1.875e-06s`

## 终端失败档案

- `space_L2_9cells`：状态=`failed`，完整运行档案=False，终端载荷完整性=True，显式配置匹配=True，源身份有效=True，墙钟=2787.78 s，峰值 RSS=2.13756e+09 byte，错误=HF3D post-source advective CFL 3.504e-01 exceeds configured limit 3.500e-01; reduce time_step_s。
- `time_L0_7.500e-06s`：状态=`failed`，完整运行档案=False，终端载荷完整性=True，显式配置匹配=True，源身份有效=True，墙钟=2787.78 s，峰值 RSS=2.13756e+09 byte，错误=HF3D post-source advective CFL 3.504e-01 exceeds configured limit 3.500e-01; reduce time_step_s。

## 运行与配置审计

- `space_L0_4cells`：完成=True，源身份有效=True，最差方向磁子直径格数=4.0，诊断步数=2680，墙钟=252.055 s，峰值 RSS=7.14945e+08 byte，登记步长命中率=0.992537。
- `space_L1_6cells`：完成=True，源身份有效=True，最差方向磁子直径格数=6.0，诊断步数=2680，墙钟=990.789 s，峰值 RSS=1.08418e+09 byte，登记步长命中率=0.992537。

## 执行环境身份

- 可读完整/终端运行证据的源码边界身份一致：True；逻辑证据数=4。
- 已加载完整运行之间的环境身份一致：True。
- 已加载逻辑算例覆盖：2/6；全部覆盖=False。
- 已加载完整运行均满足压力加速器要求：True。
- 整个清单已完整证明压力加速器要求：False。

## 数值质量门

- `space_L0_4cells`：通过=True。
- `space_L1_6cells`：通过=False。
  - `projected_divergence_s_inv`：值=0.000580722，上限=0.0001。

## temporal 收敛

- 组状态：`INCOMPLETE`。

## spatial 收敛

- 组状态：`INCOMPLETE`。

## 证据边界

`NUMERICALLY_QUALIFIED_WITHIN_FROZEN_MODEL` 只表示在预注册输入、网格路径、时间步路径和误差门下，离散结果显示稳定收敛。它不证明连续模型遗漏项可以忽略，也不证明模型参数对应真实装置，更不等于实验预测通过。
