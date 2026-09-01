# 当前交接

当前没有已授权的待续任务。E0-R1 已关闭实验前文档接口的已知数学与适定性缺口，项目达到“真正搭建、校准或执行实验之前可以安全暂停”的阶段。未经用户明确授权，不得进入实验阶段。

    PROJECT_MODE = PERSONAL_SHORT_TERM_RESEARCH_TOOL
    PRIMARY_OBJECTIVE = ACCURATE_EXPERIMENTAL_PHENOMENON_PREDICTION

    PROJECT_STATE = READY_TO_PAUSE_BEFORE_EXPERIMENT
    READY_FOR_QUANTITATIVE_APPARATUS_PREDICTION = false
    DYNAMIC_STEPS_EXECUTED_THIS_STAGE = 0
    experiment_started = false

## 科学状态

- Q1R3 六个必需平滑观测量全部未通过空间收敛，保持 `Q1R3_SPATIAL_STAGE_FAIL_CLOSED__TEMPORAL_NOT_AUTHORIZED`。
- D3R1 的 `G9_DT2` 在接受步 `6695`、模拟时刻 `0.009196624999999948 s` 因投影后最大散度门失败。
- 结果盲、只读失败步审计独立重算的最大投影散度为 `1.0249185899624536e-4 s^-1`，与归档逐位相同。压力代数残差携带几乎全部失败散度，但冻结 H-A/H-B/H-C 门没有识别唯一因果机制。
- 冻结终态保持：

      MECHANISM_NOT_IDENTIFIED__NO_FIX_AUTHORIZED

- Pro 接受投影审计为合同忠实并关闭当前取证；不得事后追认 H-A，不开展第二轮审计、求解器修复、动态重跑、D3 S3 或更细网格 campaign。
- P0-Lite 已按用户“短期个人使用性能足够、科学准确性优先”的判断终止；reference baseline 保持 `INCOMPLETE/PARTIAL`，不得重跑、补齐或转入性能重构。

## 实验前准备包

活动 E0 交付为：

    90_候选工作区/current/31_实验前可证伪预测与测量接口.md

Pro 对 E0-V1 的对抗性终审判定“状态分离和主预测对象正确，但尚不能宣称接口已完整冻结”，并要求唯一一次结果盲、纯文档 E0-R1 修订。E0-R1 已按正式代码独立核对后一次性关闭：

- 用物理区间交叠与公共有效面积定义 $D_{E0}$，消除一般网格上的空集合/部分箱缺陷；中心量改为面积平均，不再采用箱间最小值；
- 冻结双方共同的物理面积、固定支持 mask、空间 PSF/传递和曝光积分算子；正式 `surface_profile_m` 在二维桥接前只作原生诊断；
- 补齐正式 $\alpha=0.5$ 列高度的三种 fallback 与空环带插值语义，并要求主域单一合法交叉；
- 明示正式 HF3D 只有平坦初态，实测 $h_0(x,y)$ 到相场/初态的映射尚未实现，分别减初值不能消除动力学初态差异；
- 以容器轴和容器坐标重力向量闭合坐标，加入磁子中心、升降、偏心、倾角与跳动的测量/适用域；
- 把单顶部偏轴 FS-SS 降为本装置尚未证明的条件光学路线，加入有效梯度图连通、法方程满秩、逆问题条件数、独立非平面校准和包含磁子排液的体积约束；
- 划清触发前基线输入与触发后密封液面，补齐 mask、原因码、有效面积、算子身份、联合协方差/Monte Carlo 载荷和完整 prediction manifest；
- 把几何、物性、转速、姿态和初态统一放入 $u_{input}$，输出光学误差放入 $u_{meas,out}$，保留共享时基交叉协方差，消除转速双计数；
- 废止没有本装置误差预算依据的普适固定阈值，改由独立于 holdout 的 $A_{MRD}$、共同时间带宽和预注册决策风险产生资格门。

这些是数学合同和未来失败门，不是实现/校准 PASS。当前仍明确：

    COMMON_2D_OBSERVATION_OPERATOR = NOT_IMPLEMENTED
    MEASURED_H0_TO_HF3D_INITIAL_STATE_MAPPING = NOT_IMPLEMENTED
    FS_SS_SINGLE_OFF_AXIS_CAMERA = CONDITIONAL_OPTICAL_ROUTE__FEASIBILITY_NOT_DEMONSTRATED
    PROFILE_BRIDGE_REQUIRED_BEFORE_PAUSE = false
    PROFILE_BRIDGE_REQUIRED_BEFORE_SEALED_VALIDATION = true

正式分析接口只支持一维标量，不能自动承载时间—半径轮廓；暂停前不要求实现桥接，但任何磁力驱动 sealed validation 解封前，候选二维共同观测、初态路径和 profile 分析桥必须合成验证并冻结。

## 恢复条件

只有用户明确授权进入实验阶段后才继续。恢复后的第一项动作是按 E0-R1 做静态测量系统/输入校准，并在候选区结果盲实现共同二维观测与初态桥；不得直接采集、查看或解封磁力驱动 holdout。

形成装置定量预测之前还必须满足：

- 同一模型身份、参数区间、观测量和运行时长的数值资格；
- 本装置单相机 FS-SS 光路、磁子完整运动、共同触发和时空传递的静态资格；
- 实测初态到 HF3D 的合法映射，或结果盲零驱动平衡兼容门；
- 共同二维物理面积/支持/PSF/曝光算子及联合不确定度载荷；
- 在触发后液面仍密封时提交不可追溯修改的完整 prediction manifest；
- 明确且有限的模型适用域和结构差异处理。

若用户只授权搭建或校准，不得扩大到磁力驱动实验。若用户授权动态实验，也不得把未资格预测、失效测量或已解封数据写成盲验证。

## 保护范围与当前结论上限

未获新的文件级授权时，继续保持只读：

    01_最终理论/**
    02_模拟程序/**
    AGENTS.md
    既有 Q1R3、D3、投影审计和 P0-Lite 冻结证据

当前结论上限：

    numerical qualification = NOT DEMONSTRATED
    physical validation = NOT PERFORMED
    experimental validation = NOT PERFORMED
    apparatus prediction = DISALLOWED
