# 当前交接

项目是个人短期科研工具，优先级是科学预测准确性；现有性能没有明显妨碍使用。P0-Lite 性能路线已经终止，不再重跑、补齐基线、profiling 或选择性能重构。

    PROJECT_MODE = PERSONAL_SHORT_TERM_RESEARCH_TOOL
    PRIMARY_OBJECTIVE = ACCURATE_EXPERIMENTAL_PHENOMENON_PREDICTION

    P0LITE_EXECUTION = TERMINATED
    P0LITE_REFERENCE_BASELINE = INCOMPLETE
    P0LITE_VALID_SAMPLES = PARTIAL
    P0LITE_DECISION_NEED = SUPERSEDED_BY_USER_OPERATIONAL_JUDGMENT
    TIMED_R1_B3 = NO_VALID_SAMPLE
    ATTEMPT_LIMIT = UNCHANGED
    FURTHER_PERFORMANCE_EXECUTION = NO_GO

## 实际科学状态

- HF3D reference backend 已实现且具有较丰富诊断，但尚不是经过数值资格与物理验证的预测求解器。
- Q1R3 三层空间归档完整；六个必需平滑观测量全部没有通过空间收敛，状态仍为 `Q1R3_SPATIAL_STAGE_FAIL_CLOSED__TEMPORAL_NOT_AUTHORIZED`。
- D3R1 六个冻结算例中五个完成；`G9_DT2` 在接受步 `6695`、模拟时刻 `0.009196624999999948 s` 因投影后最大散度门失败，冻结 partial 的 SHA-256 为 `8f1e0973eda48c2ecc54f10001f28b988d353e85a728a16ec24f74582527cbcc`。
- 数值资格未建立；物理验证和实验验证未执行；装置级预测仍被禁止。

## 唯一活动任务

对冻结 `G9_DT2/partial.npz` 做一次结果盲、只读的失败步离散投影闭合与空间热点审计。预注册合同位于：

    90_候选工作区/current/07_D3跨网格时间步响应诊断/diagnostics/g9_dt2_projection_closure/projection_closure_preregistration.json

必须在加载合同白名单中的任何数值数组之前提交并 push 该合同。审计只允许读取：

    state_u_x_face
    state_u_y_face
    state_u_z_face
    state_pressure_pa
    state_phase
    grid_active_mask
    grid_bar_mask
    diagnostic_step_time
    diagnostic_step_dt
    diagnostic_step_divergence
    diagnostic_step_pressure_residual
    diagnostic_step_pressure_iterations

所有未列出的 NPZ 成员均不得访问，尤其禁止读取任何 `observable_*`。空间场只有失败步 `6695` 的单份末态；步级标量上下文只使用 `6683--6695`，不得声称存在 13 份空间帧。

## 冻结判别

候选审计必须独立重建 $D_h$、面密度系数、$A=-V D_h\beta G$、$r_A=Ap-b$、代数散度分量和算子缺陷，并检查全局通量闭合。热点固定为满足 $\lvert D_hu\rvert\geq 0.5\max\lvert D_hu\rvert$ 且包含最大值格点的六邻域连通分量，再向各轴扩展两格；同时标记开顶边界、闭合容器或磁子边界及界面带。

审计只能结束于以下三种状态之一：

    ALGEBRAIC_RESIDUAL_LOCALIZATION_HYPOTHESIS_SUPPORTED
    DISCRETE_OPERATOR_OR_BOUNDARY_MISMATCH_HYPOTHESIS_SUPPORTED
    MECHANISM_NOT_IDENTIFIED__NO_FIX_AUTHORIZED

无论结果为何，本轮都不得自动修求解器。结果形成后停止并交独立复核；只有后续明确裁决才可讨论候选修正。

## 当前禁止

- 不修改 `01_最终理论/**`、`02_模拟程序/**`、顶层 `AGENTS.md` 或既有冻结证据。
- 不运行 HF3D 动态步，不恢复 partial，不生成 D3 物理量或分类值。
- 不修改 P0-Lite attempt 上限，不继续任何性能样本或 profiling。
- 不把实现、归档、Pro 裁决或离散闭合诊断写成数值资格、物理验证或实验验证。

## 长期完成点

项目持续推进到真正开展实验之前可以安全暂停的阶段：数值可信度缺口按最小充分原则关闭或明确保持 `NO-GO`，并冻结可测输入、预测量、参数不确定性、实验对照指标、测量与数据记录方案、停止条件和可复现交接。未经用户明确授权不得实际开始实验。

## 当前结论上限

    numerical qualification = NOT DEMONSTRATED
    physical validation = NOT PERFORMED
    experimental validation = NOT PERFORMED
    apparatus prediction = DISALLOWED
