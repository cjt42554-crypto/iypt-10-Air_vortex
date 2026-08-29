# D3 跨网格时间步响应诊断

状态：

```text
D3_IMPLEMENTATION_READY_FOR_PRO_STATIC_REVIEW__DYNAMIC_EXECUTION_NOT_AUTHORIZED
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

## 已实现文件

```text
run_d3_cross_grid_temporal.py
analyse_d3_cross_grid_temporal.py
tests/test_d3_cross_grid_temporal.py
generated_configs/G6_DT0.json
generated_configs/G6_DT1.json
generated_configs/G6_DT2.json
generated_configs/G9_DT0.json
generated_configs/G9_DT1.json
generated_configs/G9_DT2.json
artifacts/d3_implementation_static_review.json
```

运行器直接导入 `02_模拟程序/src/airvortex/**` 的正式 HF3D、时间步控制器、记录器和归档器，没有复制或修改正式求解器。独立分析器不导入候选运行器，而是从预注册和正式归档读取器重新构造配置、硬门、算术与标签。

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

## 当前允许的复核命令

以下命令均不推进真实 HF3D 时间步。GPU 静态审计必须使用项目既有的隔离 GPU 解释器，不能以缺少 CuPy 的系统 `python3` 代替。

```bash
D3_GPU_PYTHON="90_候选工作区/current/01_HF3D数值资格/.venv-gpu/bin/python"

"$D3_GPU_PYTHON" \
  "90_候选工作区/current/07_D3跨网格时间步响应诊断/run_d3_cross_grid_temporal.py" \
  generate-configs

"$D3_GPU_PYTHON" -Werror -m unittest discover \
  -s "90_候选工作区/current/07_D3跨网格时间步响应诊断/tests" \
  -p "test_d3_cross_grid_temporal.py" -v

"$D3_GPU_PYTHON" \
  "90_候选工作区/current/07_D3跨网格时间步响应诊断/run_d3_cross_grid_temporal.py" \
  static-audit --write
```

静态审计会实际完成以下非推进检查：

1. 逐算例证明配置差异不越过登记 allowlist；
2. 重新构造六组正式网格和初态；
3. 对同网格三层配置逐 dtype、shape 和 SHA-256 比较网格、正式 G0 掩膜与初态；
4. 核对正式源码树哈希；
5. 探测实际 CUDA FP64 后端；
6. 核对 D3 raw、动态报告和最终清单尚不存在；
7. 核对 S0–S3 的阶段编号、动作名、证据字段和人类说明逐项同义；
8. 明确记录 `time_steps_advanced = 0`。

## 动态命令的当前闭锁

运行器已经实现未来执行入口，但当前预注册中的：

```text
real_cuda_dynamic_execution = false
execution_requires_new_pro_review = true
```

会在创建 D3 raw 或推进任何时间步之前拒绝以下命令：

```bash
"$D3_GPU_PYTHON" \
  "90_候选工作区/current/07_D3跨网格时间步响应诊断/run_d3_cross_grid_temporal.py" \
  prepare-execution

"$D3_GPU_PYTHON" \
  "90_候选工作区/current/07_D3跨网格时间步响应诊断/run_d3_cross_grid_temporal.py" \
  run-stage --stage S1

"$D3_GPU_PYTHON" \
  "90_候选工作区/current/07_D3跨网格时间步响应诊断/run_d3_cross_grid_temporal.py" \
  run-stage --stage S2

"$D3_GPU_PYTHON" \
  "90_候选工作区/current/07_D3跨网格时间步响应诊断/analyse_d3_cross_grid_temporal.py" \
  final
```

只有新的 Pro 裁决明确改变执行授权后，才允许按 `S0 → S1 → S2 → S3` 顺序使用这些入口。S1 或 S2 任一算例硬门失败都会写入失败诊断、停止后续算例，并把顶层状态闭锁为 `D3_INCONCLUSIVE_FAIL_CLOSED`。

## 独立分析边界

未来 S3 会独立重载六份正式归档并重新计算：

```text
q, s, u, c
eta_delta_1
rho_common_1, rho_common_2
gamma_G6, gamma_G9, gamma_delta
cosine_G6, cosine_G9, cosine_delta
observed_order_G6, observed_order_G9, observed_order_delta
```

分析器只接受 D3 自身 raw 根目录下的规范归档，拒绝 D2、隔离目录、checkpoint、符号链接和跨提交轨迹。余弦零范数保持为未定义；诊断分窗不能改变完整窗口顶层标签；二值事件只记录，不作为 PASS 门。

## 当前静态证据

```text
artifacts/d3_implementation_static_review.json
```

该文件只证明候选实现与静态门在所记录环境中通过，不是动态执行证据，也不包含 D3 科学分类。

## 当前禁止

- 运行任何真实 D3 CUDA 动态算例；
- 创建声称 S1、S2 或 S3 已执行的报告；
- 复用 D2 动态轨迹；
- 修改 `01_最终理论/**` 或 `02_模拟程序/**`；
- 自动进入更小时间步、更细网格、长期窗口、Q1R4 或 D4。

## 停止点

候选实现和静态测试完成后停在：

```text
D3_IMPLEMENTATION_READY_FOR_PRO_STATIC_REVIEW__DYNAMIC_EXECUTION_NOT_AUTHORIZED
```

只有新的 Pro 明确裁决才能启动真实 CUDA 阶段。
