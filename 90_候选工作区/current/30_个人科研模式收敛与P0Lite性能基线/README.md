# 个人科研模式收敛与 P0-Lite 性能基线

## 项目模式

本项目是个人短期科研工具，唯一目标是尽可能准确地预测实验现象。

后续工作只保留三项顶层目标：

1. 数值结果可信；
2. 计算速度足够支持科研迭代；
3. 结果最终能够与实验比较并提高预测准确度。

正式物理理论、HF3D reference backend、现有数值质量门和模块化求解器结构继续保留，不进行全项目重写。

## 旧治理体系

current/24_P0P失败证据登记与P0P2零步实现 保留为历史治理原型，但已经退出活动路径。

新实现不得 import 旧目录中的任何 p0p2v2 Python 模块，也不得重新建立：

- P2 父票据或 C2 授权子提交；
- grant nonce 或 reviewed snapshot blob 表；
- 一次性 claim；
- 全局 mutable ledger；
- no-resume、no-retry 或 no-supplement 权限系统；
- analysis lock 或唯一 success/failure terminal；
- Git topology 合成仓库；
- 穷举式 fsync、rename 或控制流异常矩阵；
- 自定义高可靠事务恢复框架。

旧代码、测试、裁决和证据不删除、不改写，也不得运行旧 execute 入口。

## P0-Lite 测量目标

P0-Lite 只负责获得可信的：

- 新进程端到端墙钟；
- CPU 总体和逐线程利用率；
- GPU 利用率、时钟、显存、温度和功耗时间线；
- CUDA kernel、API 和 memcpy 统计；
- H2D/D2H 次数、字节和时间；
- Python 与正式算子级热点；
- RSS、VmHWM、CuPy pool 与 CUDA 显存高水位；
- 数值结果身份；
- 不覆盖且可人工检查的固定输出结构。

利用率是解释性指标。只有数值可信且端到端墙钟实际下降，才能认定优化有效。

## 固定 benchmark

固定三个 reference benchmark：

    B1 = G6_DT0, 0.000--0.005 s
    B2 = G9_DT0, 0.000--0.005 s
    B3 = G9_DT2, 0.000--0.005 s

派生配置只能把 run.duration_s 从 0.010 改为 0.005。以下内容保持不变：

    drive.ramp_duration_s = 0.005
    output_interval_s = 0.001
    registered times = [0, 0.001, 0.002, 0.003, 0.004, 0.005]
    物理输入、网格、时间步和质量门

每次执行前都必须真实调用 SimulationConfig.from_dict(payload)，并核对精确 to_dict() 往返。

## 固定日程

p0lite_schedule.json 固定 16 个逻辑 slot：

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

端到端计时覆盖进程启动、imports、配置构造、网格和初态、全部接受或拒绝时间步、质量门、登记帧、结果组装、archive 写入与严格重载、canonical payload、最终 CUDA synchronize 和进程退出。

## 活动实现上限

活动目录原则上只包含：

    README.md
    p0lite_schedule.json
    p0lite_common.py
    run_p0lite.py
    p0lite_worker.py
    analyse_p0lite.py
    tests/test_p0lite.py
    runs/
    reports/

除非这五个 Python 文件无法完成任务，不得新增模块。活动 Python 代码总量原则上不超过约 2000 个非空非注释行；测试不超过约 20 个高价值用例。超过上限时先删减设计，不继续拆模块。

允许阅读旧实现并重新实现必要的少量逻辑，但不得 import 旧模块。可复用的思想仅包括：

- 三份有效配置生成与正式配置 round-trip；
- fresh-process worker；
- cProfile 和 NVTX；
- 稳定的 Nsight CUDA/NVTX 配置；
- pidstat、nvidia-smi 和 Tctl 采样；
- CUDA memcpy 解析；
- canonical numerical payload；
- archive strict reload。

## attempt 证据

每次尝试写入：

    runs/<slot_id>/<attempt_id>/

attempt_id 使用 UTC 时间、短 commit 和 PID。目录至少包含：

    status.json
    run_manifest.json
    effective_config.json
    stdout.log
    stderr.log
    quality.json
    telemetry/
    archive/

状态只允许 RUNNING、SUCCESS、FAILED、INTERRUPTED 和 INVALID_ENVIRONMENT。

异常终止后遗留的 RUNNING 由 analyser 解释为 INTERRUPTED。不做复杂自动终态恢复。

任何已有 attempt 目录不得覆盖。失败和中断证据不得删除；重试使用新的 attempt ID；最终报告列出全部 attempt。每个 slot 最多三个 attempt。

允许为环境无效、中断或 profiler 工具失败创建新 attempt。数值质量门、配置身份、CUDA fallback、archive 重载或数值 payload 一致性失败时，必须停止整个阶段并先修复原因。

## 环境门

每个 scored timed attempt 前要求：

    连续 30 s 背景 CPU <= 25%
    无其他 GPU compute process
    CPU Tctl <= 90 degC
    GPU temperature <= 75 degC
    AC online
    32 logical CPUs online
    all CPU governors identical
    clean worktree
    Git commit equals the baseline commit

运行中每 0.5--1.0 s 记录总 CPU、逐线程 CPU、GPU utilization、clocks、memory、temperature、power 和其他进程。明显背景污染只使该 attempt 成为 INVALID_ENVIRONMENT，证据保留后可创建新 attempt。

软件环境记录 Python、NumPy、SciPy、CuPy、CUDA runtime/driver、GPU UUID、kernel 和 thread environment。环境变化使用新的 environment_id 分组，不形成授权失效。

## 最小验证

动态基线前只执行：

- 不超过约 20 个高价值测试；
- Ruff；
- 核心文件 mypy 或 pyright；
- Python compile；
- B1/B2/B3 fresh-process CUDA 零步构造；
- Nsight 最小能力探针；
- git diff --check。

高价值测试集中在配置正式构造、只改变 duration、非法配置拒绝、attempt 不覆盖、环境失败不启动 worker、fresh process、SUCCESS 完整条件、残缺 attempt、canonical payload、最小 Nsight trace 和损坏文件拒绝。

不再测试所有 fsync、rename、control signal 或 Git parent 组合。

## 数据驱动优化

完成 reference baseline 后只按冻结规则选择一个性能候选：

- recording、archive 和 reload 占墙钟至少 15%：先优化记录与 I/O；
- H2D、D2H 和 pressure wrapper 占墙钟至少 20%，或每接受步存在完整三维场往返：选择 device-resident pressure/data boundary；
- 一至两个正式 CPU 算子合计至少 40%：只加速这些算子；
- host numerical operators 至少 60%、GPU 中位利用率低于 35% 且无单算子达到 40%：建立显式 hf3d_cuda_resident_v1；
- 合理候选预计最大端到端加速低于 1.30x：停止性能重构并返回科学验证。

只允许一个主候选和最多一次修正迭代。禁止为了加速改变物理、边界、材料、驱动、网格、时间步、质量门或输出定义；禁止 FP32、TF32、mixed precision、fastmath 或减少诊断。

## 返回科学主线

性能达到可用水平后停止继续追求利用率，依次进入：

1. D3R1 G9_DT2 末端投影散度根因；
2. 空间和时间数值资格；
3. 敏感且可测参数的不确定性与预测区间；
4. 轨迹、频率、幅值、增长或衰减、相位、事件时间和不确定区间的实验验证。

准确预测真实实验必须依赖模型与实验对照；任何 Git 授权、状态机或哈希都不能替代实验验证。
