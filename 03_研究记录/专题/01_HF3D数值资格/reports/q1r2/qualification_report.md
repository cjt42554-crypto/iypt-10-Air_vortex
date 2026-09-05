# HF3D 数值资格报告

## 总结论

- 状态：`FAIL_PROVENANCE_INPUT_OR_COMPLETION_GATE`。
- 结论上限始终是冻结连续模型内的数值资格；本报告不构成实验验证。
- 装置级临界转速、真实接触时刻与稳定气道半径仍须由独立实验检验。

## 细化设计门

- 设计门通过：True。
- `spatial`：登记步长=[6e-06, 6e-06, 6e-06]，静态有效步长=[6e-06, 6e-06, 6e-06]。
- `temporal`：登记步长=[6e-06, 3e-06, 1.5e-06]，静态有效步长=[6e-06, 3e-06, 1.5e-06]。

## 缺失算例

- `time_L2_1.500e-06s`

## 终端失败档案

- `time_L1_3.000e-06s`：状态=`cancelling`，完整运行档案=False，终端载荷完整性=True，显式配置匹配=True，源身份有效=True，墙钟=224.461 s，峰值 RSS=2.11976e+09 byte，错误=None。

## 运行与配置审计

- `space_L0_4cells`：完成=True，源身份有效=True，最差方向磁子直径格数=4.0，诊断步数=3340，墙钟=348.105 s，峰值 RSS=6.95615e+08 byte，登记步长命中率=0.994012。
- `space_L1_6cells`：完成=True，源身份有效=True，最差方向磁子直径格数=6.0，诊断步数=3340，墙钟=1324.9 s，峰值 RSS=1.0315e+09 byte，登记步长命中率=0.994012。
- `space_L2_9cells`：完成=True，源身份有效=True，最差方向磁子直径格数=9.0，诊断步数=3353，墙钟=6510.5 s，峰值 RSS=2.20811e+09 byte，登记步长命中率=0.938264。
- `time_L0_6.000e-06s`：完成=True，源身份有效=True，最差方向磁子直径格数=9.0，诊断步数=3353，墙钟=6510.5 s，峰值 RSS=2.20811e+09 byte，登记步长命中率=0.938264。

## 执行环境身份

- 可读完整/终端运行证据的源码边界身份一致：True；逻辑证据数=5。
- 已加载完整运行之间的环境身份一致：True。
- 已加载逻辑算例覆盖：4/6；全部覆盖=False。
- 已加载完整运行均满足压力加速器要求：True。
- 整个清单已完整证明压力加速器要求：False。

## 数值质量门

- `space_L0_4cells`：通过=True。
- `space_L1_6cells`：通过=True。
- `space_L2_9cells`：通过=False。
  - `liquid_mass_error_relative`：值=0.00132108，上限=0.001。
- `time_L0_6.000e-06s`：通过=False。
  - `liquid_mass_error_relative`：值=0.00132108，上限=0.001。

## temporal 收敛

- 组状态：`INCOMPLETE`。

## spatial 收敛

- 组状态：`FAIL_EFFECTIVE_TIME_STEP_GATE`。
- 实际时间步身份门：通过=False，登记结构有效=True，实际结构有效=True。

## 证据边界

`NUMERICALLY_QUALIFIED_WITHIN_FROZEN_MODEL` 只表示在预注册输入、网格路径、时间步路径和误差门下，离散结果显示稳定收敛。它不证明连续模型遗漏项可以忽略，也不证明模型参数对应真实装置，更不等于实验预测通过。
