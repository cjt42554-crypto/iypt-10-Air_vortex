# HF3D 细网格候选 CFL 窗口重放

状态：`FINE_WINDOW_CANDIDATE_PASS__PROMOTION_DECISION_REQUIRED`。

本报告只验证隔离候选补丁能否越过原 Q1R 细网格 CFL 失败窗口。正式程序没有修改；这不是 Q1R2、空间/时间收敛、物理验证或实验验证。

## 门禁

| 门禁 | 结果 |
|---|---:|
| `archive_integrity` | PASS |
| `completed_targeted_duration` | PASS |
| `crossed_old_failure_state_time` | PASS |
| `input_change_scope` | PASS |
| `candidate_disk_source_boundary_identity` | PASS |
| `cuda_selected_without_fallback` | PASS |
| `accepted_post_projection_cfl` | PASS |
| `absolute_projected_divergence` | PASS |
| `pressure_relative_residual` | PASS |
| `formal_numerical_quality_gate` | PASS |

## 核心结果

- 模拟时长：`0.013 s`；完成步数：`1757`。
- 墙钟：`3998.59 s`；最小接受步长：`6.76465690243e-07 s`；登记步长命中比例：`0.928856`。
- 联合已知源项限步的最小步长：`5.82815205187e-06 s`。登记步长命中比例低于资格工具要求的 0.95，因此原 `7.5e-6 s` 不得直接沿用为 Q1R2 空间共同步长。
- 投影后最大接受 CFL：`0.336906177612`；硬门：`0.35`。
- 被拒绝试步最大 CFL：`0`；总重试次数：`0`。
- 最大投影散度：`2.4909184404e-05 s^-1`；候选绝对门：`0.0001 s^-1`。
- 最大压力相对残差：`9.99996829527e-08`；登记容差：`1e-07`。
- 实际压力加速器：`cuda`；回退：`False`。
- 执行源码 SHA-256：`03ebc4dcd194d0ffadc7f24228720d775dc7d888ceb5a9737816368626482010`；前后边界一致：`True`。
- 已加载模块字节核验：`False`。正式归档模型固定记录为 `false`；本报告只声称磁盘白名单源码树执行前后边界一致，不把它扩大为已导入模块字节证明。

## 结论边界

若全部门禁通过，只能说明该候选补丁及目标压力容差在一条 0.013 s 细网格窗口轨迹上通过实现级和单例数值门，足以提交“是否提升到正式程序”的人工裁决。它不冻结共同时间步，不替代完整 Q1R2，也不允许装置级预测。
