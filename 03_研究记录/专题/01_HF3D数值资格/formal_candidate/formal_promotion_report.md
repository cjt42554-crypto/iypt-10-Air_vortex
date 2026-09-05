# HF3D 事务性 CFL 控制器正式提升报告

状态：`FORMAL_PROMOTION_AND_CUDA_FORMAL_REPLAY_PASS__Q1R2_FAIL_CLOSED`。

用户已授权按当前交接文件完成唯一待续任务。冻结补丁已原样应用到正式程序，采用控制目标比例 `0.95`、最多 `4` 次事务重试和新增稠密诊断字段；CFL 硬门未放宽。正式理论未修改。

后续已在本机 CuPy/CUDA 环境从提升后的正式路径关闭原待办，并完成 Q1R2 三层空间执行与失败闭锁。新增执行只写入候选工作区；正式理论和正式程序在该后续轮次均未再次修改。

## 正式程序变更

- `02_模拟程序/src/airvortex/backends/high_fidelity_3d.py`
- `02_模拟程序/src/airvortex/core/config.py`
- `02_模拟程序/src/airvortex/numerics/__init__.py`
- `02_模拟程序/src/airvortex/numerics/projection3d.py`
- `02_模拟程序/src/airvortex/numerics/projection3d_gpu.py`
- `02_模拟程序/tests/test_config_io.py`
- `02_模拟程序/tests/test_gpu_projection.py`
- `02_模拟程序/tests/test_high_fidelity_3d.py`

提升后的正式可执行源码树 SHA-256 为 `03ebc4dcd194d0ffadc7f24228720d775dc7d888ceb5a9737816368626482010`，与冻结候选 CUDA 证据绑定的源码身份完全一致。

## 本轮验证

| 项目 | 结果 |
|---|---|
| 冻结补丁 SHA-256、应用检查和反向检查 | PASS |
| 正式变更范围 | 精确为 5 个源码文件和 3 个测试文件 |
| 正式可执行源码树身份 | PASS |
| 正式全量测试 | 176 passed，5 skipped，0 failed；另有 52 个 subtests passed |
| 跳过原因 | 4 项缺少 CuPy/CUDA；1 项缺少 Tk 图形显示 |
| 候选资格测试 | 24/24 passed |
| 正式 self-check | 6/6 passed |
| compileall、git diff --check、尾随空格/制表符扫描 | PASS |
| 提升后正式路径隔离 CUDA 测试 | 5/5 passed，0 skipped |
| 提升后正式路径 `0.013 s` 细网格 CUDA 窗口 | 10/10 冻结门通过 |
| 本机 CUDA 环境正式全量回归 | 181/181 passed，0 skipped，0 failed |
| 本机正式 self-check | 6/6 passed |
| Q1R2 三层空间执行 | 三层完整；精细层两个冻结门失败 |
| 当前候选资格测试 | 29/29 passed |

## 证据边界

原 GitHub runner 没有 CuPy/CUDA，也没有图形显示，所以提升时相应测试按条件跳过。该缺口现已由本机正式路径重放关闭：5 项 GPU 测试全部通过；`136 × 136 × 120`、`0.013 s` 窗口完成 1757 步，正式源码树前后身份一致，实际选择 CUDA 且无回退，投影后最大 CFL 0.336906，最大投影散度为 $2.490918\times10^{-5}\ \mathrm{s}^{-1}$。详细证据见 `../reports/formal_replay/formal_replay_report.*`。

Q1R2 已冻结并执行三层空间算例，但没有通过。精细层 `6.0e-6 s` 登记步长命中率为 0.938264，低于冻结门 0.95；冻结的液体质量净变化门也失败。后者包含规定的开边界通量，不能单独解释为数值不守恒，但同样不能在看到结果后从 Q1R2 验收合同中删除。时间中层因此在 110 步主动停止，时间细层未运行；空间和时间收敛均未建立。

## 当前结论上限

`FORMAL_IMPLEMENTATION_PROMOTED_AND_CPU_CUDA_REVALIDATED__Q1R2_FAIL_CLOSED__NO_GO_NUMERICAL_QUALIFICATION__NO_GO_PHYSICAL_PREDICTION`

正式实现级验证已经关闭，但 Q1R2 的失败意味着尚无多网格或多时间步收敛通过，也没有数值资格、物理验证或实验验证；当前仍不得给出装置级定量预测。
