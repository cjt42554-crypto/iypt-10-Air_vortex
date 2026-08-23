# Stage1 修复 Issue Ledger

本台账对应 2026-08-23 本地复审的全部 P0/P1 项。`CLOSED_IMPLEMENTATION` 只表示实现级复现已关闭；不表示 HF3D 已通过装置级物理验证。

| ID | 严重度 | 状态 | 复现命令/反例 | 根因 | 主要修改 | 新增硬测试 | 验证结果 | 剩余风险 |
|---|---|---|---|---|---|---|---|---|
| P0-1 | P0 | CLOSED_IMPLEMENTATION | `./run_stage1_cli.sh self-check --pretty`；无显示 GUI registry probe | Stage1 覆盖层把后端注册项写成三元组，而运行时要求 `(backend_id, module_name, class_names, aliases)` 四元组 | `stage1/bootstrap.py` 改为合法、无冲突、幂等的四元组注册；CLI 与 GUI 均探测真实 Stage1 类 | `test_stage1_registry_and_entrypoints.py` | CLI 五项自检全部 `ok=true`；前向与 16×16×24 HF3D smoke 均完成；GUI 可构造两个 Stage1 后端 | 自检只证明最小入口可运行，不证明长时或大网格稳定性 |
| P0-2 | P0 | CLOSED_IMPLEMENTATION | 同一合成液面分别按径向/3D 表示观测；改变 ROI 后再次比较 | 两后端此前使用不同中央 ROI 和参考面语义，却在结果后处理阶段被赋予同一固定身份 | `stage1/observables.py` 建立唯一 canonical surface 与共享观测算子；两后端在结果生成链中调用；实际 ROI、外环、界面水平、静态参考来源与聚合规则进入身份 digest 和 provenance | `test_stage1_shared_observable_operator.py`、`test_stage1_units.py`、`test_stage1_archive_roundtrip.py` | 同面跨表示的五个标准量逐项一致；改变 ROI/参考语义会改变身份并被拒绝；H0 明示为 nominal configured liquid depth | 当前共享算子仍是模型选择；nominal H0 未替代实验实测 H0，装置校准尚未完成 |
| P0-3 | P0 | CLOSED_IMPLEMENTATION | 使用旧反例直接调用 core compare；再从 CLI/存档/报告路径比较 | 身份检查只在 GUI 边界，核心 `compare_*` 可绕过并错误计算 RMSE | `analysis/comparison.py` 将完整科学身份门下沉到 `compare_series`、`compare_results` 与 common-observables 路径；GUI 只转交同一身份；一侧缺失合同也拒绝 | `test_stage1_core_comparison_contract.py`、`test_stage1_archive_roundtrip.py` | 参考面、ROI、界面 level、相机参数或拓扑参数不一致均在计算指标前抛出 `DataValidationError`/`ContractError`；旧 `0.5773502691896257` 反例不再返回 RMSE | 两侧都没有科学合同的历史通用数据仍可走 legacy 数值比较；Stage1 结果与归档均强制携带合同，单边剥离会 fail closed |
| P1-1 | P1 | CLOSED_IMPLEMENTATION | 查询每个新增量的 `metadata["observable_units"]` | 标准量只靠名称后缀暗示单位，正式元数据链缺失 | 后端/canonicalizer 为物理量、相机量、状态量和全部数值诊断显式写入 `m`、`m^3`、`kg`、`kg*m/s` 或 `1` | `test_stage1_units.py` | 所有 Stage1 标准量和诊断的单位键存在且与合同单位一致 | 单位元数据不能替代实验仪器标定和量值溯源 |
| P1-2 | P1 | CLOSED_IMPLEMENTATION | 真实事件名映射；`t_end=0.01`、普通输出 0.005 s、证据输出 0.001 s 的帧数反例 | 事件别名不完整，事件判断直接受 GUI/用户输出间隔控制 | 补齐 touch/channel/reconnect/break/exit 的真实名称映射；前向后端在稠密证据网格求事件并将普通结果插值回用户时间轴；证据时间单独归档 | `test_stage1_events_and_sampling.py` | 反例得到 3 个普通帧与 11 个事件证据帧；全部要求事件名进入统一状态机 | 前向事件判据仍需以真实高速摄影校准；稠密证据不等于连续时间严格定位 |
| P1-3 | P1 | CLOSED_IMPLEMENTATION | 后端运行到 archive，再从 GUI/实验比较读取相机可见量；改变像素/PSF/阈值 | 相机算子未进入后端链，operator ID 未覆盖有效参数，NaN 参数未拒绝 | `stage1/measurement.py` 实现界面投影、PSF、像素化、分割与边界提取；两后端每个输出帧执行；完整相机参数生成 operator ID；NaN/非法参数 fail closed；明确记录折射 `NOT_APPLIED` | `test_stage1_camera_integration.py`、`test_stage1_archive_roundtrip.py` | 相机深度/气道宽度、边界与末帧分割图可归档复读；物理量与相机量身份不同；参数变化改变身份 | 当前为理想正交轮廓相机；圆柱壁折射、真实镜头畸变、曝光和实验阈值尚未标定，不能冒充实测算子 |
| P1-4 | P1 | CLOSED_IMPLEMENTATION | 从仓库根目录、未预置 `PYTHONPATH` 原样执行 README 验证命令；触发 validator fail-closed 自测 | 旧命令依赖调用者手工 `PYTHONPATH`，验证器只覆盖少量测试且 acceptance 证据不足 | 验证器自行解析绝对源码路径并为子进程设置环境；执行 12 个指定测试、可选全量回归、CLI/GUI/归档/反例/数值阈值/源码边界；acceptance 记录环境、Git、命令、返回码、日志与哈希 | `test_stage1_validator_end_to_end.py` | 干净环境变量下验证器返回实现 PASS；故意失败时 `implementation_pass=false` 且退出非零 | 运行 Python 仍必须事先具有项目依赖；环境记录不是完整可重建锁文件 |
| P1-5 | P1 | CLOSED_IMPLEMENTATION | 运行 HF3D smoke，检查内存结果、NPZ/JSON 归档与 summary | transport/CLS 的相、密度及三分量动量重映射诊断在后端聚合时被丢弃 | `transport3d.py`、`cls3d.py` 与 HF3D 后端贯通每步 phase/density/momentum x/y/z/absolute 诊断，并记录最大值、累计绝对值、修复/裁剪量与步数 | `test_stage1_transport_conservation.py`、`test_stage1_cls_conservation.py`、`test_stage1_archive_roundtrip.py` | 所有诊断可在结果与归档复读；正式验证器按协议单位与阈值检查数值，而非只检查字段存在 | 当前阈值仅服务实现 smoke；尚未完成网格—时间—界面联合收敛，不能外推装置精度 |
| P1-6 | P1 | CLOSED_IMPLEMENTATION | `PYTHONWARNINGS=error ./run_stage1_cli.sh self-check --pretty` | 非活动区零密度仍在 `np.where` 求值阶段发生除零 | `numerics/momentum3d.py`、transport/CLS 改为带 `where=active_density` 的安全 `np.divide`，只在活动正密度单元计算 | `test_stage1_hf3d_strict_warnings.py` | 严格 warning 模式下前向和 HF3D 自检返回 0，无 invalid/divide warning | 更大网格和更长积分仍可能暴露尚未覆盖的浮点极端值，需后续压力测试 |

## 状态约束

- 实现状态目标：`PASS_STAGE1_IMPLEMENTATION__NO_GO_PHYSICAL_PREDICTION`。
- 物理预测状态：始终为 `NO_GO`。
- 未完成的物理门：静滴 Laplace 压差、寄生流网格收敛、毛细波频率与阻尼、网格—时间—界面联合收敛、实测磁子轨迹、独立校准与盲验证。
