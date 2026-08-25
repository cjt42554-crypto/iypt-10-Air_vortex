# 当前交接

## 当前唯一待续任务

HF3D 事务性 CFL 控制器候选补丁及真实细网格窗口已经通过候选门，当前状态为 `FINE_WINDOW_CANDIDATE_PASS__PROMOTION_DECISION_REQUIRED`。正式理论 `01_最终理论/**` 与正式程序 `02_模拟程序/**` 均未修改。下一步必须由用户明确裁决：是否把已冻结补丁按原样提升到正式程序，并接受默认控制目标比例 0.95、最多 4 次事务重试及新增稠密诊断字段。

未经明确批准，不应用补丁、不修改正式目录、不冻结 Q1R2，也不直接采用任何候选步长。

## 必要入口

- 提升审查：`90_候选工作区/current/01_HF3D数值资格/formal_candidate/promotion_review.md`；
- 候选清单：`90_候选工作区/current/01_HF3D数值资格/formal_candidate/candidate_manifest.json`；
- 冻结补丁：`90_候选工作区/current/01_HF3D数值资格/formal_candidate/0001-Add-transactional-HF3D-CFL-controller-candidate.patch`；
- 真实窗口报告：`90_候选工作区/current/01_HF3D数值资格/formal_candidate/fine_window_report.md`、`fine_window_report.json`；
- 窗口配置与核验器：`formal_candidate/fine_window_pressure_1e-7.json`、`formal_candidate/analyse_fine_window.py`；
- 先前 Q1R 诊断：`90_候选工作区/current/01_HF3D数值资格/reports/q1r_diagnosis/diagnosis_report.md`。

## 冻结身份

- 仓库基线提交：`9b79b1ca39461ada176c6e7de5855a935b191983`；
- 正式可执行源码树 SHA-256：`4858dc42121c1bc5dfd926aafef194e4225c8a0c1100118efb7d8ab184dd3c8a`；
- 候选补丁 SHA-256：`ee978d7bc9e1b27278b6f89a44c03f789780ba1cb11d648989ca8a3ee29ef4be`；
- 应用补丁后的可执行源码树 SHA-256：`03ebc4dcd194d0ffadc7f24228720d775dc7d888ceb5a9737816368626482010`。

## 已确认事实

1. 候选控制器把控制目标 $C_t=0.95C_h$ 与投影后硬门 $C_h$ 分开，对当前/壁面 CFL 率和可解析的重力/旋转系 CFL 率加速度使用联合正根；完整步仍以投影后 CFL 失败闭锁。硬门和物理参数均未放宽。
2. 被拒绝试步不会提交流场；压力求解计数、初猜外推计数/因子和两级历史以 $O(1)$ 引用检查点回滚。合成完整步回归逐字节确认时间、步号、角度、速度、相场、压力和压力历史不变；重试耗尽错误包含机器可读前后 CFL、逐算子加速度、重试数和实际加速器。
3. 最终补丁在全新正式副本上无冲突应用，181 项正式测试中 177 项通过、系统 Python 的 4 项 CUDA 测试按预期跳过；隔离 CUDA 测试 5/5、正式 `self-check` 6/6、候选测试 24/24、`compileall` 和文本检查通过。
4. 真实候选窗口使用 136 × 136 × 120 网格、压力容差 $10^{-7}$、显式 CUDA 和原登记步长上限运行到 0.013 s：1757 个接受步、墙钟 3998.592 s、CUDA 无回退、候选磁盘源码树前后 SHA 一致。投影后最大接受 CFL 0.336906，最大投影散度 $2.490918\times10^{-5}\ \mathrm{s}^{-1}$，全部窗口门通过。
5. 原故障状态时间约 0.0121425 s 后的候选首个接受步为第 1628 步，$t=0.0121483342\ \mathrm{s}$、步长 $7.1290163\times10^{-6}\ \mathrm{s}$、投影后 CFL 0.333940。窗口没有触发事务重试；联合控制器预限步 112 次。
6. 原登记步长 $7.5\times10^{-6}\ \mathrm{s}$ 只在 92.8856% 的接受步命中，低于资格工具要求的 95%；联合控制器最小非输出对齐步长为 $5.828152\times10^{-6}\ \mathrm{s}$。后者也不是冻结步长，必须在正式提升后用正式源码轨迹重新设计空间共同步长和时间递减层。
7. 正式归档模型固定记录 `loaded_module_bytes_verified=false`；本轮只声称白名单磁盘源码树执行前后 SHA 边界一致，不扩大为已导入模块字节证明。
8. Q1R2 仍为 `NOT_FROZEN`；没有取得多网格/多时间步收敛、数值资格、物理验证或实验验证通过。物理预测状态仍为 `NO-GO_UNDERRESOLVED_UNCONVERGED_UNVALIDATED`。

## 获得批准后的顺序

1. 重新确认 `main`、工作树和基线身份，执行补丁 `git apply --check` 后应用到 `02_模拟程序/`；正式理论保持只读。
2. 重新执行最终补丁绑定的针对性、全量、CUDA 和 `self-check` 验证；失败则停止，不继续 Q1R2。
3. 从提升后的正式源码重跑 0.013 s 细网格窗口，确认正式源码身份与候选源码 SHA 一致。
4. 依据提升后各空间层真实轨迹冻结新的共同登记步长；不得直接采用 $5.828152\times10^{-6}\ \mathrm{s}$。
5. 冻结全新 Q1R2：所有层统一压力容差 $10^{-7}$，空间组共同实际步长，时间组实际步长严格递减，实际 CUDA 无回退。
6. 按仓库规范在 `main` 提交并推送；即使 Q1R2 通过，物理/实验预测仍保持 `NO-GO`。

## 原始证据边界

真实窗口原始归档位于忽略目录 `90_候选工作区/current/01_HF3D数值资格/runs/cfl_candidate/`，远程不能直接读取；`fine_window_report.json` 和 `candidate_manifest.json` 已冻结归档 manifest 及全部载荷 SHA-256。正式归档模型的内部哈希能检出意外损坏或不一致写入，但不构成对可同时改写 manifest 与载荷的对抗性真实性证明。
