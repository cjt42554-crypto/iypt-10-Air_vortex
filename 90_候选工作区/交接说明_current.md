# 当前交接

当前没有已授权的待续任务。项目已经推进到“真正开展实验之前可以安全暂停”的阶段；未经用户明确授权，不得搭建、校准或执行实际实验。

    PROJECT_MODE = PERSONAL_SHORT_TERM_RESEARCH_TOOL
    PRIMARY_OBJECTIVE = ACCURATE_EXPERIMENTAL_PHENOMENON_PREDICTION

    PROJECT_STATE = READY_TO_PAUSE_BEFORE_EXPERIMENT
    READY_FOR_QUANTITATIVE_APPARATUS_PREDICTION = false
    DYNAMIC_STEPS_EXECUTED_THIS_STAGE = 0
    experiment_started = false

## 科学状态

- Q1R3 六个必需平滑观测量全部未通过空间收敛，保持 `Q1R3_SPATIAL_STAGE_FAIL_CLOSED__TEMPORAL_NOT_AUTHORIZED`。
- D3R1 的 `G9_DT2` 在接受步 `6695`、模拟时刻 `0.009196624999999948 s` 因投影后最大散度门失败。
- 结果盲、只读的失败步审计已完成；最大投影散度独立重算为 `1.0249185899624536e-4 s^-1`，与归档逐位相同。压力代数残差携带几乎全部失败散度，但冻结 H-A/H-B/H-C 门没有识别唯一因果机制。
- 冻结终态保持：

      MECHANISM_NOT_IDENTIFIED__NO_FIX_AUTHORIZED

- Pro 独立复核接受该审计为合同忠实，并明确拒绝事后把结果追认为 H-A。当前关闭投影取证，不开展第二轮审计、求解器修复、动态重跑、D3 S3 或更细网格 campaign。
- P0-Lite 已按用户“短期个人使用性能足够、科学准确性优先”的判断终止；reference baseline 保持 `INCOMPLETE/PARTIAL`，不得重跑、补齐或转入性能重构。

## 实验前准备包

唯一 E0 交付为：

    90_候选工作区/current/31_实验前可证伪预测与测量接口.md

E0-V1 冻结了：

- 第一条可反驳链：实测磁子本体转速到贯通前的方位平均自由液面轮廓，再到派生漏斗深度；
- HF3D 的 $\alpha=0.5$ 列高度、径向分箱、实验全场重建与同一坐标/时间算子；
- 不加染料或粒子的扩展视角自由液面合成纹影（FS-SS）路线，以及遮挡、折射、焦散、近壁和中心覆盖硬门；
- 所有几何、物性、湿润、实际转速、光学与时基输入的单位、测量方法和不确定度合同；
- 只允许静态仪器数据进入 `calibration`，所有磁力驱动液面按完整 run 进入 `sealed_validation`；
- 原始数据、哈希、二维时间—半径载荷、主误差指标、参数/数值/测量/模型四类不确定度和失败闭锁。

正式分析接口目前只支持一维标量时序，不能自动承载完整时间—半径轮廓；这一点已在 E0 中明示。若以后授权实验，任何桥接实现都必须留在候选区、先用合成场验证，并在解封 holdout 前冻结。

## 恢复条件

只有用户明确授权进入实验阶段后才继续。恢复时第一项动作是按 E0-V1 搭建并校准测量系统、实测输入，不直接采集或查看磁力驱动 holdout。

真正形成装置定量预测之前还必须另行满足：

- 同一模型身份、参数区间、观测量和运行时长的数值资格；
- 本装置 FS-SS、磁子本体转速和共同触发的留出校准；
- 在液面数据仍密封时提交不可追溯修改的 prediction manifest；
- 明确且有限的模型适用域/结构差异处理。

若用户只授权搭建或校准，不得据此扩大到磁力驱动实验。若用户授权动态实验，也不得把未资格预测或已解封数据写成盲验证。

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
