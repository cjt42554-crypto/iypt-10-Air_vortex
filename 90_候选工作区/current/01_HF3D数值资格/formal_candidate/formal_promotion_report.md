# HF3D 事务性 CFL 控制器正式提升报告

状态：`FORMAL_PROMOTION_CPU_PASS__CUDA_FORMAL_REPLAY_PENDING`。

用户已授权按当前交接文件完成唯一待续任务。冻结补丁已原样应用到正式程序，采用控制目标比例 `0.95`、最多 `4` 次事务重试和新增稠密诊断字段；CFL 硬门未放宽。正式理论未修改。

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

## 证据边界

候选阶段已有隔离 CUDA 测试和真实 `136 × 136 × 120`、`0.013 s` 细网格窗口通过；其可执行源码树身份与本次提升后的正式源码逐字节一致。因此这些证据仍能支持“该实现曾在 CUDA 上通过候选门”，但不能写成“已从提升后的正式目录重新执行”。本轮 GitHub runner 没有 CuPy/CUDA，也没有图形显示，故相应测试按条件跳过。

Q1R2 仍为 `NOT_FROZEN`。旧登记步长 `7.5e-6 s` 的候选命中比例为 `0.928856`，低于 `0.95`；候选窗口中的最小联合限步值 `5.82815205187083e-6 s` 也不是冻结共同步长。不得直接采用任一数值冻结 Q1R2。

## 当前结论上限

`FORMAL_IMPLEMENTATION_PROMOTED_AND_CPU_REVALIDATED__CUDA_FORMAL_REPLAY_PENDING__Q1R2_NOT_FROZEN__NO_GO_PHYSICAL_PREDICTION`

这不是多网格或多时间步收敛通过，不是物理验证，也不是实验验证；当前仍不得给出装置级定量预测。
