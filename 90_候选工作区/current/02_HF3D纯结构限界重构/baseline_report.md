# HF3D 纯结构重构前基线

结论：`PASS_BASELINE_READY_FOR_REFACTOR`。正式源码尚未修改，可以进入限界重构。

## 身份与环境

- 重构前 Git HEAD：`ce96932b5a122fa284922bebc84db9df92ef6c36`；
- 正式源码树 SHA-256：`03ebc4dcd194d0ffadc7f24228720d775dc7d888ceb5a9737816368626482010`；
- Python 3.12.3，NumPy 2.5.2，SciPy 1.17.1，CuPy 14.2.0；
- CUDA 设备：NVIDIA GeForce RTX 5060 Laptop GPU，驱动 580.173.02，总显存 8151 MiB。

## 重构前门

- 正式完整测试：181/181 通过，其中 5 项 CUDA 测试实际执行并通过；
- 正式 self-check：6/6 通过；
- `compileall`：通过；
- Q1 至 Q1R3 候选资格测试：35/35 通过；
- 新增严格比较器失败闭锁测试：5/5 通过。

完整命令、退出码和结构化结果见 `baseline_report.json`。

## 固定短重放

`baseline_config.json` 是候选区专用的低成本 characterization 配置，不承载科学结论。它使用 8 × 8 × 10 网格、非零 100 rpm 加速历程、0.002 s 时长、0.001 s 输出间隔和 0.0001 s 登记步长。

CPU 与 CUDA 各完成 20 个接受步、3 个归档帧、20 次压力求解，分别明确选择 CPU 与 CUDA，无回退。每份捕获包含：

- `time_s`、status、events 和 Progress 序列；
- observable 与 field 的有序键、shape、dtype 和全部数组值；
- units、数值 metadata、逐步诊断数组和最终完整三维场；
- 88 个数组的独立内容哈希。

CPU 与 CUDA 捕获分别对自身执行严格比较，均得到 `PASS_STRICT_ELEMENTWISE_EQUIVALENCE`。预重构数组及 manifest 位于 `evidence/pre_cpu.*` 和 `evidence/pre_cuda.*`。

首次 CPU 计算结束后的草稿 manifest 序列化曾因 `MappingProxyType` 被证据工具拒绝。捕获器随后改为复用正式程序的 `json_ready` 规范化，并重新生成最终 CPU 与 CUDA 证据；这没有修改正式源码，也不是求解器基线失败。

## 结论边界

此基线仅用于证明重构前后实现级严格等价。它不改变 Q1R3 的失败闭锁结论，不提供网格或时间收敛、物理校准、实验验证或装置级预测证据。
