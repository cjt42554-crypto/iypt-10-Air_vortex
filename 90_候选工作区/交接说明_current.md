# 当前交接

项目是个人短期科研工具，科学预测准确性优先；现有性能没有明显妨碍使用。P0-Lite 已按用户判断终止，reference baseline 保持 `INCOMPLETE/PARTIAL`，不得重跑、补齐或转入性能重构。

    PROJECT_MODE = PERSONAL_SHORT_TERM_RESEARCH_TOOL
    PRIMARY_OBJECTIVE = ACCURATE_EXPERIMENTAL_PHENOMENON_PREDICTION

    P0LITE_EXECUTION = TERMINATED
    TIMED_R1_B3 = NO_VALID_SAMPLE
    FURTHER_PERFORMANCE_EXECUTION = NO_GO

## 实际科学状态

- HF3D reference backend 已实现且具有较丰富诊断，但尚不是经过数值资格与物理验证的预测求解器。
- Q1R3 三层空间归档完整；六个必需平滑观测量全部没有通过空间收敛，状态仍为 `Q1R3_SPATIAL_STAGE_FAIL_CLOSED__TEMPORAL_NOT_AUTHORIZED`。
- D3R1 六个冻结算例中五个完成；`G9_DT2` 在接受步 `6695`、模拟时刻 `0.009196624999999948 s` 因投影后最大散度门失败。
- 数值资格未建立；物理验证和实验验证未执行；装置级预测仍被禁止。

## G9_DT2 失败步审计结果

结果盲合同和独立实现已经分别在读取数值数组前提交并 push。审计实现提交为：

    08f67b950bba9b2820a4ff7b9c43acc1c78bcb28

证据入口：

    90_候选工作区/current/07_D3跨网格时间步响应诊断/diagnostics/g9_dt2_projection_closure/projection_closure_preregistration.json
    90_候选工作区/current/07_D3跨网格时间步响应诊断/diagnostics/g9_dt2_projection_closure/audit_projection_closure.py
    90_候选工作区/current/07_D3跨网格时间步响应诊断/diagnostics/g9_dt2_projection_closure/artifacts/projection_closure_audit.json
    90_候选工作区/current/07_D3跨网格时间步响应诊断/diagnostics/g9_dt2_projection_closure/artifacts/projection_closure_audit.md

审计只读取了预注册的 12 个状态、掩膜与步级压力诊断成员；`observable_*` 读取数为零，动态步数为零，partial 前后 SHA-256 均为 `8f1e0973eda48c2ecc54f10001f28b988d353e85a728a16ec24f74582527cbcc`。

冻结终态为：

    MECHANISM_NOT_IDENTIFIED__NO_FIX_AUTHORIZED

判别细节必须完整保留：

- 独立重算最大投影散度与冻结值逐位相同，均为 `1.0249185899624536e-4 s^-1`，故 H-C 归档对齐假设不成立。
- 独立重算压力相对残差 $L_2=9.830838356012649\times10^{-8}$，与归档诊断 `9.830838356083504\times10^{-8}` 一致；$L_\infty=7.789472723500224\times10^{-9}$。
- 代数残差分量在最大格点和全局 $L_\infty$ 上分别解释 `0.9999999988175134` 与 `0.9999998103085352`，且最大值共位。
- 算子缺陷最大值为 `1.9441830861506717e-11 s^-1`，远小于失败散度，但大于预注册舍入尺度 `1.0249185899624535e-12 s^-1`；因此 H-A 的全部冻结门没有同时通过。
- 算子缺陷既不与主热点共位，也不能达到 90% 解释门，故 H-B 不成立。
- 全局通量闭合相对误差为 `2.2690411167966486e-10`。
- 主热点位于索引 `[11, 100, 104]`，50% 热点连通分量只有一个格点；该格点不直接邻接开顶、闭合容器或磁子，也不在界面带内，距界面带一个横向网格间距。两格扩展区含 40 个界面带格点并邻接闭合边界。

上述终态不能事后改阈值、追认为 H-A 或授权修复。它只说明冻结 H-A/H-B/H-C 判别没有给出唯一机制。

## 下一项唯一任务

把预注册、实现和完整结果交 ChatGPT Pro 独立复核，请其从当前全局科学目标出发裁决下一项最小充分任务。复核完成前停止：不得修改正式或候选求解器、不得启动动态重算、不得建立第二轮诊断或进入实验预测准备。

若 Pro 认为需要新审计，必须在看到新结果前另立不可追溯修改的预注册；不得改写本轮合同或终态。若 Pro 裁决候选修正，仍须先明确候选范围、验证矩阵和从零重跑义务，正式树保持只读，除非用户另行给出文件级授权。

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
