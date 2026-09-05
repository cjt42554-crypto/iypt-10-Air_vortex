# HF3D 配置分辨率审计：02_模拟程序/configs/hf_preview_quick.json

## 结论

- 结论上限：`PREVIEW_ONLY`。
- 本审计只检查配置所对应的离散尺度与时间窗，不是一次 HF3D 运行，也不是实验验证。
- 装置级临界转速、通道形成时刻和稳定通道半径仍不得据此声称。

## 关键量

- 网格：`12 × 12 × 16`，盒状网格单元数 `2,304`。
- 网格间距：$\Delta x=0.0075\,\mathrm{m}$，$\Delta y=0.0075\,\mathrm{m}$，$\Delta z=0.005\,\mathrm{m}$。
- 各向异性比 $h_{\max}/h_{\min}=1.5$。
- 磁子直径按最佳方向计 `1.2` 格，按最差方向计 `0.8` 格。
- 磁子精确体积只相当于约 `2.815` 个网格单元。
- 弥散界面物理宽度 `0.0125 m`，在最差方向仅跨 `1.667` 格。
- 配置时长 `0.005 s`，最大步长 `0.0002 s`，带符号磁子总转数 `0.02`。

## 发现的问题

- `CRITICAL` / `BAR_SUBCELL_WORST_AXIS`：磁子直径在最粗网格方向上不足一个单元。
- `MAJOR` / `BAR_TOO_FEW_VOXELS`：磁子精确体积对应的 Cartesian 单元少于 50 个。
- `MAJOR` / `GRID_ANISOTROPY`：最大与最小网格间距之比超过 1.25。
- `MAJOR` / `INTERFACE_UNDERRESOLVED_WORST_AXIS`：实现中的弥散界面宽度在最差方向上不足两格。
- `MAJOR` / `SUB_ROTATION_WINDOW`：配置时长覆盖的带符号磁子转数不足一圈。

## 指标解释

正式后端当前记录的 `bar_diameter_cells_min_spacing` 使用 $D/h_{\min}$，它表示最佳分辨方向。若网格各向异性，真正保守的几何门应使用 $D/h_{\max}$。本审计同时报告两者，并把最差方向作为资格判断依据。
