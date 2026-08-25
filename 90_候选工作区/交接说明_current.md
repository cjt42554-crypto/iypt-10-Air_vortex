# 当前交接

## 当前唯一待续任务

HF3D 事务性 CFL 控制器已获用户授权，并按冻结补丁原样提升到正式程序。当前状态为 `FORMAL_PROMOTION_CPU_PASS__CUDA_FORMAL_REPLAY_PENDING`。正式理论 `01_最终理论/**` 未修改；正式程序的 5 个源码文件和 3 个测试文件已修改。

下一步必须在具备 CuPy/CUDA 的环境中，从提升后的正式目录重新执行隔离 GPU 测试和 `0.013 s` 细网格失败窗口。两项均通过后，才能依据正式源码各空间层的真实稠密步长轨迹重新设计空间共同登记步长和时间递减层；在此之前不得冻结或运行 Q1R2。

## 必要入口

- 正式提升报告：`90_候选工作区/current/01_HF3D数值资格/formal_candidate/formal_promotion_report.md`、`formal_promotion_report.json`；
- 冻结补丁：`90_候选工作区/current/01_HF3D数值资格/formal_candidate/0001-Add-transactional-HF3D-CFL-controller-candidate.patch`；
- 原提升审查：`90_候选工作区/current/01_HF3D数值资格/formal_candidate/promotion_review.md`；
- 真实候选窗口：`formal_candidate/fine_window_report.md`、`fine_window_report.json`；
- 正式窗口配置与核验器：`formal_candidate/fine_window_pressure_1e-7.json`、`formal_candidate/analyse_fine_window.py`。

## 冻结身份

- 本次正式提升触发提交：`7cddbafd26acacbdcfdbb292a0f16d33d0d60aa9`；
- 冻结补丁 SHA-256：`ee978d7bc9e1b27278b6f89a44c03f789780ba1cb11d648989ca8a3ee29ef4be`；
- 提升后正式可执行源码树 SHA-256：`03ebc4dcd194d0ffadc7f24228720d775dc7d888ceb5a9737816368626482010`；
- 控制目标比例：`0.95`；最多事务重试：`4`；CFL 硬门未放宽。

## 本轮已完成

1. 确认正式程序和正式理论自冻结基线以来未被其他提交修改，并核验补丁 SHA-256。
2. 在 `02_模拟程序/` 原样应用补丁；反向应用检查通过，变更范围精确为 5 个源码文件和 3 个测试文件。
3. 提升后正式可执行源码树身份与候选 CUDA 窗口绑定身份完全一致。
4. GitHub CPU runner 上正式全量测试：`176 passed, 5 skipped, 0 failed`，另有 `52` 个 subtests 通过。4 项因无 CuPy/CUDA 跳过，1 项因无 Tk 图形显示跳过。
5. 候选资格测试 `24/24`、正式 `self-check` `6/6`、`compileall`、`git diff --check` 和尾随空格/制表符扫描通过。
6. 冻结候选清单未改写；其中 `authorization_received=false` 是用户授权前的历史快照，不代表当前未授权。

## 尚未完成及结论边界

1. 本轮 runner 无 CuPy/CUDA，未从提升后的正式路径重跑隔离 CUDA 测试，也未重跑 `136 × 136 × 120`、`0.013 s` 真实细网格窗口。
2. 候选阶段上述 CUDA 证据绑定的可执行源码树与正式提升后逐字节身份相同，因此仍有实现级相关性；但不得表述为正式路径已重新执行。
3. 旧登记步长 `7.5e-6 s` 的候选命中比例为 `0.928856`，低于资格门 `0.95`；`5.82815205187083e-6 s` 只是候选窗口中的最小联合限步值，不是冻结步长。
4. Q1R2 仍为 `NOT_FROZEN`；没有取得多网格/多时间步收敛、数值资格、物理验证或实验验证通过。
5. 当前结论上限为 `FORMAL_IMPLEMENTATION_PROMOTED_AND_CPU_REVALIDATED__CUDA_FORMAL_REPLAY_PENDING__Q1R2_NOT_FROZEN__NO_GO_PHYSICAL_PREDICTION`。

## 下一次执行顺序

1. 在正式程序上运行隔离 CUDA 测试，确认 CUDA 投影、CPU/CUDA 对等和 GPU 压力历史回滚。
2. 用 `fine_window_pressure_1e-7.json` 从正式源码重跑到 `0.013 s`，并用 `analyse_fine_window.py` 核验正式源码身份、CUDA 无回退、旧故障时刻覆盖、CFL、散度、压力残差和全部质量门。
3. 依据提升后各空间层真实轨迹冻结新的空间共同登记步长；不得直接沿用候选窗口的单个最小步长。
4. 冻结并执行全新 Q1R2：统一压力容差 `1e-7`，空间组共同实际步长，时间组实际步长严格递减，实际 CUDA 无回退。
5. 即使 Q1R2 通过，物理预测与实验验证仍保持 `NO-GO`，之后另行设计更长时和平滑区/近拓扑区资格。
