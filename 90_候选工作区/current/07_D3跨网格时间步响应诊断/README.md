# D3 跨网格时间步响应诊断

状态：

```text
PREREGISTERED_IMPLEMENTATION_ONLY__EXECUTION_NOT_AUTHORIZED
```

## 目标

D3 检验正式 G0、`6/9` 格和 `0–0.010 s` 短窗内，两个网格对共同时间步减半的响应是否不同，以及这种差分时间步响应是否实质污染了原 Q1R3 的 `6→9` 空间差。

D3 不建立数值资格，不修改正式 HF3D，不预测长期稳态或实验装置。

## 冻结入口

科学裁决：

```text
90_候选工作区/current/09_D2V2执行结果独立复核与D3科学裁决.md
```

机器可读合同：

```text
90_候选工作区/current/07_D3跨网格时间步响应诊断/d3_cross_grid_temporal_preregistration.json
```

基线配置：

```text
90_候选工作区/current/01_HF3D数值资格/generated_q1r3/space_L1_6cells.json
90_候选工作区/current/01_HF3D数值资格/generated_q1r3/space_L2_9cells.json
```

正式源码树 SHA-256：

```text
dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8
```

## 六算例设计

```text
G6_DT0  5.5e-6 s
G6_DT1  2.75e-6 s
G6_DT2  1.375e-6 s

G9_DT0  5.5e-6 s
G9_DT1  2.75e-6 s
G9_DT2  1.375e-6 s
```

六个确认性算例必须在 D3 预注册后、同一清洁执行提交上从零运行。D2 的 F6/F9/T9 只能作为提出 D3 的既有证据，不得进入 D3 分类输入。

## 当前允许的工作

只允许在本目录实现并静态验证：

```text
run_d3_cross_grid_temporal.py
analyse_d3_cross_grid_temporal.py
tests/test_d3_cross_grid_temporal.py
```

可以生成候选配置、执行 dry-run、运行不推进真实 HF3D 时间步的单元测试与身份审计，并检查严格 JSON、Markdown、失败闭锁和证据清单逻辑。

当前禁止：

- 运行任何真实 D3 CUDA 动态算例；
- 创建声称 S1、S2 或 S3 已执行的报告；
- 复用 D2 动态轨迹；
- 修改 `01_最终理论/**` 或 `02_模拟程序/**`；
- 自动进入更小时间步、更细网格、长期窗口、Q1R4 或 D4。

## 实现停止点

候选实现和静态测试完成后，状态应为：

```text
D3_IMPLEMENTATION_READY_FOR_PRO_STATIC_REVIEW__DYNAMIC_EXECUTION_NOT_AUTHORIZED
```

随后停止并更新交接；只有新的 Pro 明确裁决才能启动真实 CUDA 阶段。
