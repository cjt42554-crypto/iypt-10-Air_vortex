# HF3D 数值资格报告

## 总结论

- 状态：`FAIL_REFINEMENT_DESIGN_GATE`。
- 结论上限始终是冻结连续模型内的数值资格；本报告不构成实验验证。
- 装置级临界转速、真实接触时刻与稳定气道半径仍须由独立实验检验。

## 细化设计门

- 设计门通过：False。
- `space_L1_6cells: configured 2.5e-05 s exceeds static capillary ceiling 1.45915711e-05 s`
- `space_L2_9cells: configured 2.5e-05 s exceeds static capillary ceiling 7.85520378e-06 s`
- `time_L0_1.000e-04s: configured 0.0001 s exceeds static capillary ceiling 7.85520378e-06 s`
- `time_L1_5.000e-05s: configured 5e-05 s exceeds static capillary ceiling 7.85520378e-06 s`
- `time_L2_2.500e-05s: configured 2.5e-05 s exceeds static capillary ceiling 7.85520378e-06 s`
- `spatial: static effective time steps differ across grids`
- `temporal: static effective time steps collapse under stability limits`
- `spatial`：登记步长=[2.5e-05, 2.5e-05, 2.5e-05]，静态有效步长=[2.5e-05, 1.4591571063179744e-05, 7.85520378288456e-06]。
- `temporal`：登记步长=[0.0001, 5e-05, 2.5e-05]，静态有效步长=[7.85520378288456e-06, 7.85520378288456e-06, 7.85520378288456e-06]。

## 缺失算例

- `space_L1_6cells`
- `space_L2_9cells`
- `time_L0_1.000e-04s`
- `time_L1_5.000e-05s`
- `time_L2_2.500e-05s`

## 运行与配置审计

- `space_L0_4cells`：完成=True，源身份有效=True，最差方向磁子直径格数=4.0，诊断步数=800，墙钟=122.485 s，峰值 RSS=2.18718e+08 byte，登记步长命中率=1。

## 执行环境身份

- 可读完整/终端运行证据的源码边界身份一致：True；逻辑证据数=1。
- 已加载完整运行之间的环境身份一致：True。
- 已加载逻辑算例覆盖：1/6；全部覆盖=False。
- 本清单未登记特定压力加速器要求。

## 数值质量门

- `space_L0_4cells`：通过=True。

## temporal 收敛

- 组状态：`INCOMPLETE`。

## spatial 收敛

- 组状态：`INCOMPLETE`。

## 证据边界

`NUMERICALLY_QUALIFIED_WITHIN_FROZEN_MODEL` 只表示在预注册输入、网格路径、时间步路径和误差门下，离散结果显示稳定收敛。它不证明连续模型遗漏项可以忽略，也不证明模型参数对应真实装置，更不等于实验预测通过。
