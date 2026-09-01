# 当前交接

当前唯一活动路线已经切换为个人短期科研工具模式。Pro 对项目用途重新裁决后，明确停止继续加固旧 P0-P2 V2 治理控制面；29 号控制流补丁裁决不再执行。正式物理理论、HF3D reference backend、数值质量门和正式模块化架构继续保留。

    PROJECT_MODE = PERSONAL_SHORT_TERM_RESEARCH_TOOL
    PRIMARY_OBJECTIVE = ACCURATE_EXPERIMENTAL_PHENOMENON_PREDICTION

    PROJECT_OVERENGINEERING = YES
    OVERENGINEERING_LOCATION = GOVERNANCE_AND_EVIDENCE_CONTROL_PLANE
    FORMAL_PHYSICS_AND_SOLVER_ARCHITECTURE = RETAIN
    PROJECT_WIDE_REWRITE = PROHIBITED

    P0P2_V2_GOVERNANCE_HARDENING = STOP
    RULING_29_CONTROL_FLOW_PATCH = SUPERSEDED
    P0P2_V2_PARENT_TICKET = NOT_ISSUED_AND_RETIRED
    P0P2_V2_AUTHORIZATION_CHILD = NOT_ISSUED_AND_RETIRED
    P0P2_V2_DYNAMIC_EXECUTION = RETIRED_NOT_ACTIVE

    P0LITE_STAGE_0_GOVERNANCE_CONVERGENCE = COMPLETE
    P0LITE_STAGE_1_IMPLEMENTATION_AND_ZERO_STEP_GATES = COMPLETE
    P0LITE_STAGE_2_REFERENCE_BASELINE = NEXT_AND_AUTHORIZED_NOT_STARTED

## 唯一活动入口

    90_候选工作区/current/30_个人科研模式收敛与P0Lite性能基线/

旧治理原型：

    90_候选工作区/current/24_P0P失败证据登记与P0P2零步实现/

旧目录已经由 ARCHIVED.md 标记为 ARCHIVED_NOT_ACTIVE。其代码、测试、裁决和证据保留在原位，不删除、不移动、不改写；新 P0-Lite 不得 import 其中任何 Python 模块。

## 已完成的治理收敛

阶段 0 已完成以下仓库状态变更：

- 建立新的唯一活动目录；
- 冻结 16-slot P0-Lite 日程；
- 明确个人科研工具的测量、验证和停止边界；
- 归档旧 P0-P2 V2，不执行 29 号补丁；
- 停止 P2/C2、一次性 claim、全局 ledger、analysis 唯一终态、Git topology 合成测试和穷举故障注入；
- 正式理论与正式模拟程序保持未修改；
- 没有运行任何动态样本。

## 已完成的 P0-Lite 实现

轻量 P0-Lite 已限制在五个活动 Python 文件内：

    p0lite_common.py
    run_p0lite.py
    p0lite_worker.py
    analyse_p0lite.py
    tests/test_p0lite.py

实现总量约 2080 个非空非注释行，包含 14 个高价值测试，没有新增第六个模块，也没有 import 旧 P0-P2 V2。

当前实现只包含：

- 三份有效配置生成与正式 SimulationConfig round-trip；
- fresh-process worker；
- cProfile、NVTX 和稳定 Nsight CUDA/NVTX 包装；
- pidstat、nvidia-smi 和 Tctl 采样；
- Nsight CSV 与 SQLite 中 CUDA kernel、API、allocation、H2D/D2H 次数、精确字节和时间解析；
- canonical numerical payload；
- archive strict reload。

不得重新实现：

- 父票据或授权子提交；
- grant nonce 或 reviewed blob 表；
- 一次性 claim；
- 全局 mutable ledger；
- no-resume/no-retry 权限系统；
- analysis lock 或唯一 terminal；
- Git topology 合成仓库；
- fsync、rename 或控制信号组合故障矩阵；
- 高可靠事务恢复框架。

## 下一项唯一任务

先把阶段 1 实现按 Pro 指定消息提交并 push：

    perf: add lightweight reproducible HF3D baseline harness

该提交的完整 40 位对象 ID 是阶段 2 baseline commit。随后在 clean main 上按 p0lite_schedule.json 的既定顺序执行 16 个 slot；不等待 P2、C2 或新的授权票据，不运行旧 P0-P2 V2。

## 固定 benchmark 与日程

    B1 = G6_DT0, 0.000--0.005 s
    B2 = G9_DT0, 0.000--0.005 s
    B3 = G9_DT2, 0.000--0.005 s

派生配置只能把 run.duration_s 从 0.010 改为 0.005。drive.ramp_duration_s、output_interval_s、登记时刻、物理输入、网格、时间步和质量门必须保持不变。

p0lite_schedule.json 已冻结为：

    WARMUP_B1
    WARMUP_B2
    WARMUP_B3
    TIMED_R1_B1
    TIMED_R1_B2
    TIMED_R1_B3
    TIMED_R2_B2
    TIMED_R2_B3
    TIMED_R2_B1
    TIMED_R3_B3
    TIMED_R3_B1
    TIMED_R3_B2
    PROFILE_HOST_B2
    PROFILE_CUDA_B2
    PROFILE_HOST_B3
    PROFILE_CUDA_B3

## attempt 模型

每次尝试使用新的不可覆盖目录：

    runs/<slot_id>/<attempt_id>/

状态只允许 RUNNING、SUCCESS、FAILED、INTERRUPTED 和 INVALID_ENVIRONMENT。异常终止遗留的 RUNNING 由 analyser 解释为 INTERRUPTED，不做复杂恢复。

任何失败和中断证据不得删除。允许环境无效、中断或 profiler 失败后创建新 attempt；每个 slot 最多三个 attempt。数值质量、配置身份、CUDA fallback、archive 重载或 canonical payload 一致性失败时停止整个阶段，不直接重试。

## 阶段 1 已完成验证

已实际完成：

- 14/14 高价值测试通过；
- Ruff 通过；
- 四个核心文件 strict mypy 通过；
- 五个活动 Python 文件 compile 通过；
- B1/B2/B3 在三个独立 PID 中完成正式 CUDA 零步构造，网格分别为 90 x 90 x 80、136 x 136 x 120、136 x 136 x 120；三者均 CUDA selected、fallback false、time_steps_advanced = 0；
- CUDA runtime 13020、driver 13000；
- Nsight Systems 2026.2.1 最小 CUDA/NVTX 探针通过，捕获实际 kernel/API；SQLite 精确重算 H2D 与 D2H 各 1 次、各 32768 字节；
- git diff --check 通过；
- 正式理论、正式程序和 AGENTS.md 差异为空。

这些检查没有启动 warm-up、timed、HOST profile、CUDA profile 或任何 HF3D 动态时间步。阶段 2 尚未开始，不能把零步 PASS 写成性能或数值资格结论。

阶段 1 通过后直接提交并进入阶段 2，不等待 P2/C2 或新的授权票据。

## 阶段 2 执行边界

16 个 slot 按冻结顺序执行。端到端墙钟必须覆盖进程启动、imports、配置构造、网格和初态、全部时间步、质量门、登记帧、结果组装、archive 写入与严格重载、canonical payload、最终 CUDA synchronize 和进程退出。

每个 scored timed attempt 前要求连续 30 秒背景 CPU 不超过 25%、无其他 GPU compute process、CPU Tctl 不超过 90 摄氏度、GPU 不超过 75 摄氏度、AC online、32 个逻辑 CPU 在线、governor 一致、工作树清洁且 commit 与 baseline 一致。

必须保留全部有效和无效 attempt，并生成：

    reports/p0lite_baseline.json
    reports/p0lite_baseline.md
    reports/p0lite_profile.json
    reports/p0lite_profile.md

## 之后的固定方向

阶段 2 报告完成后，只按 Pro 冻结的实测规则选择一个性能候选；候选最多一次修正。预计合理加速低于 1.30 倍时停止性能重构。性能候选无论通过或拒绝，随后必须返回 D3R1 投影散度、空间/时间资格、参数不确定性和实验验证。

## 当前禁止

阶段 0–2 期间禁止：

- 修改 01_最终理论；
- 修改 02_模拟程序；
- 修改 AGENTS.md；
- 删除或改写旧 P0-P、P0-P2、D3、V1 或 E0 证据；
- 执行旧 P0-P2 V2；
- 签发旧授权；
- 扩张治理文档或事务状态机；
- 在基线数据出现前选择优化技术路线。

## 当前结论上限

    P0-P V1 measured result = NOT AVAILABLE
    P0-P2 V2 governance prototype = ARCHIVED_NOT_ACTIVE
    P0-Lite harness = IMPLEMENTED_AND_ZERO_STEP_QUALIFIED
    P0-Lite reference baseline = NOT_YET_RUN
    hardware utilization baseline = NOT DEMONSTRATED
    numerical qualification = NOT DEMONSTRATED
    physical validation = NOT PERFORMED
    experimental validation = NOT PERFORMED
    apparatus prediction = DISALLOWED
