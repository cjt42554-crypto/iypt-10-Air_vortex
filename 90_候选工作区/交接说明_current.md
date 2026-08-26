# 当前交接

## 当前唯一待续任务

正式事务性 CFL 控制器的本机 CUDA 重放缺口已经关闭；Q1R2 已冻结并执行完三层空间算例，但触发两个预注册门后失败闭锁。当前状态为 `FORMAL_PROMOTION_AND_CUDA_FORMAL_REPLAY_PASS__Q1R2_FAIL_CLOSED_AFTER_COMPLETED_SPATIAL_GATES__TEMPORAL_NOT_COMPLETED`。

下一步不是直接恢复 Q1R2 时间层或进入 Q2，而是先完成 Q1R3 的预注册裁决：修正“液体净存量变化”和“开边界守恒残差”的门禁语义，并用完整 `0.02 s` 正式轨迹验证新的共同登记步长。正式理论 `01_最终理论/**` 和正式程序 `02_模拟程序/**` 本轮均未修改。

## 必要入口

- 正式路径 CUDA 重放：`90_候选工作区/current/01_HF3D数值资格/reports/formal_replay/formal_replay_report.md`、`formal_replay_report.json`；
- Q1R2 冻结身份：`reports/q1r2_freeze/freeze_manifest.md`、`freeze_manifest.json`；
- Q1R2 原始资格报告：`reports/q1r2/qualification_report.md`、`qualification_report.json`；
- 失败闭锁裁决：`reports/q1r2/early_stop_decision.md`、`early_stop_decision.json`；
- 下一共同步长只读诊断：`reports/q1r2/next_dt_diagnostic.md`、`next_dt_diagnostic.json`；
- 轨迹步长工具：`analyse_q1r2_dt_pilot.py`；
- 失败闭锁裁决器：`analyse_q1r2_early_stop.py`；
- 本地忽略的完整空间档案：`runs/q1r2/space_L0_4cells`、`space_L1_6cells`、`space_L2_9cells`。

## 冻结与证据身份

- 正式程序最近变更提交：`462da97293d86747100c29b6194d8313fdbb7bde`；
- 正式可执行源码树 SHA-256：`03ebc4dcd194d0ffadc7f24228720d775dc7d888ceb5a9737816368626482010`；
- 冻结补丁 SHA-256：`ee978d7bc9e1b27278b6f89a44c03f789780ba1cb11d648989ca8a3ee29ef4be`；
- Q1R2 campaign SHA-256：`afca1dd03214120e978f90f8401d188e8cf41cc2c7d27556af7713298922029d`；
- Q1R2 生成清单逐字节 SHA-256：`33fffa8b96a69ced47bbf2dbf291acfd9899237963912637e7cc4b968ce7932a`；
- Q1R2 资格报告 SHA-256：`896d3675d019a82aa4d832c910bae3bc248d1cc75929d860f1f655a002b35b94`；
- Q1R2 失败闭锁裁决 SHA-256：`2c2401523ceb2da8fa3044d5bd6065c0a0b8107aaa5dabf5e9012aecb41c3e57`；
- 下一共同步长诊断 SHA-256：`cd9cddb12aa25ca0d644540482250932919407caafcf461e5710bee73d0e5dc5`；
- 三层空间归档清单 SHA-256：粗层 `b798708a582440b66d000d4aaba81d1f4925a7b9dfecf0570fb3b8bb5fdb3583`，中层 `855d86162972fcebad00f998b17fbf1191ce54638aa1298202459851b6f2ceec`，细层 `6c02cc45df7f2a78d75de96807e0c497d2bde9d18b2aa7ff79c009747d56a16e`。

## 本轮已完成

1. 在正式路径执行隔离 CUDA 测试，5/5 通过，覆盖投影 CPU/CUDA 对等、完整 HF3D 单步对等和 GPU 压力历史回滚。
2. 从正式源码重跑 `136 × 136 × 120`、`0.013 s` CUDA 窗口，1757 步完整结束；冻结分析器 10/10 门通过，最大接受 CFL 为 0.336906，最大投影散度为 $2.490918\times10^{-5}\ \mathrm{s}^{-1}$。
3. 执行三层 `0.013 s` 正式轨迹试点；按 `0.1 µs` 量化、实际验收 0.95、设计门 0.97 选择 `6.0e-6 s`，并冻结 Q1R2 空间共同步长及 `6.0e-6→3.0e-6→1.5e-6 s` 时间层。冻结输入独立再生成逐字节一致。
4. Q1R2 三层空间均完整到 `0.02 s`，实际正式源码身份一致，CUDA 无回退。粗、中、细层登记步长命中率分别为 0.994012、0.994012、0.938264；细层低于冻结门 0.95，空间实际时间步身份门失败。
5. 细层冻结 `liquid_mass_error_relative` 门的值为 0.00132108，超过 0.001；但该正式量是相对初始体积的净存量变化，包含规定开边界通量。真正按通量修正的 `liquid_volume_balance_error_relative` 为 $8.60575\times10^{-12}$，因此不能把前者的门失败解释成求解器数值不守恒，也不能在看到结果后从 Q1R2 合同中删除。
6. 两个空间门已足以使 Q1R2 失败；时间中层在 110 步主动中断，进程已退出，留下 `state=cancelling`、非完整但载荷哈希和配置身份有效的诊断目录；时间细层未启动。裁决器确认失败证据在时间层完成前已经成立。
7. 当前候选测试 29/29、正式全量测试 181/181、正式 self-check 6/6 通过；正式全量测试在本机 CUDA 环境为 0 跳过、0 失败。Q1R2 原始分析顶层状态为 `FAIL_PROVENANCE_INPUT_OR_COMPLETION_GATE`，补充裁决状态为 `Q1R2_FAIL_CLOSED_AFTER_COMPLETED_SPATIAL_GATES__TEMPORAL_NOT_COMPLETED`。

## 尚未完成及结论边界

1. Q1R2 时间中、细层没有完整运行；时间收敛未评估。空间组因实际步长身份门先失败，平滑量空间收敛也未评估。
2. 完整 `0.02 s` 三层轨迹的只读投影暂选 `5.5e-6 s`，三层最小预测命中率为 0.973755；相邻更大值 `5.6e-6 s` 只有 0.965404。该数值没有冻结为 Q1R3，改变登记步长后的真实轨迹仍需重跑验证。
3. Q1R3 必须在运行前区分：数值守恒门使用开边界通量修正后的平衡残差；若仍关心容器内液体净存量，应把它另列为模型域/边界充分性或物理库存门，不能继续称作数值质量误差。
4. 未执行更长时统计稳态、平滑区 Q2、近拓扑区 Q2、物理校准、盲法实验或装置级预测验证。
5. 当前结论上限为 `FORMAL_IMPLEMENTATION_PROMOTED_AND_CPU_CUDA_REVALIDATED__Q1R2_FAIL_CLOSED__NO_GO_NUMERICAL_QUALIFICATION__NO_GO_PHYSICAL_PREDICTION`。

## 下一次执行顺序

1. 先在候选区形成 Q1R3 门禁语义审查，明确保留、重命名或拆分液体净存量门；不得改写 Q1R2 冻结清单和原始报告。
2. 用现有三层 `0.02 s` 归档重新生成可追踪的 Q1R3 步长诊断；把 `5.5e-6 s` 只作为待验证候选，不直接冻结。
3. 先运行一个细网格 `0.02 s`、候选 `5.5e-6 s` 的正式 CUDA 探针，重新检查实际命中率至少 0.95、开边界守恒残差、液体净存量、CFL、散度和压力残差。
4. 只有探针与门禁语义审查都通过，才冻结并执行 Q1R3；不得用删除 `runs/q1r2/time_L1_3.000e-06s` 或原样续跑来把 Q1R2 改写为通过。
5. 只有新的早期瞬态多网格/多时间步筛查通过后，才讨论更长时和平滑区/近拓扑区资格；物理与实验预测继续保持 `NO-GO`。
