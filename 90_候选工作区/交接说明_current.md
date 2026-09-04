# 当前交接：2026-09-05 数值审查修复

## 当前状态

在 `main` 上以 `0a4d41b05ed91af2ec566f515250d942bc97042a` 的科学代码为基线继续。本轮是用户明确授权的正式源码修复，不再受旧交接中“未授权修改正式程序”的限制。未开展真实实验，没有把软件回归测试写成装置验证或空间/时间收敛资格。

本轮数值身份为 `HF3D-20260905-consistent-CLS-clearance-divergence`。旧冻结结果不能不经重算直接继承到新算法。项目继续按短期个人科研工具处理，没有新增工作流框架或扩大物理自由度。

`交接说明_history.md` 按要求清空为零字节；Git 历史、Q1R3/D3R1 原始结果、失败审计及 E0 测量接口材料保持保留。临时源码获取/提交工作流在最终发布提交中移除。

## 实际修改

文件位于 `02_模拟程序/`。

1. `numerics/phase_transfer3d.py` 与 `numerics/interface3d.py`：CLS 压缩/扩散改成逐面有界通量限制，不再先裁剪再全局补质量；同时将同一质量交换应用于三个动量分量。限流量另行记录，不伪装成裁剪量。测试覆盖密度比直到 $10^6$、密度跳变反号、多次交换、均匀速度保持、总质量和动量守恒及混合动能不增。
2. `hf3d/stepper.py`：物理动量对流先使用压缩前的密度，之后才配对执行 CLS。增加整步接线测试和开启压缩的零驱动、正反转短轨迹，不再只在关闭压缩的辅助配置上证明组合正确。
3. 物理对流仍存在有限压力残差引起的微小相分数裁剪/修复。新增上界为舍入量加 $2\Delta t\int|D\mathbf u|\,\mathrm dV$；超出即在提交状态前拒绝。**该残差级修复不是严格的配对动量通量**，不能宣称整个有限精度分裂步精确保持全部能量或自由流；已有逐步裁剪/修复数组继续记录它。初版只允许舍入量的门会误拒正常短轨迹，已按实测离散散度预算修正，不靠随意放宽原有质量预算通过。
4. `topology_diagnostics_3d`：等面积半径保留为描述量，但不再决定解析资格。采用考虑三维体素体积的保守净空和侵蚀后连通路径，要求从顶部达到体素磁子上方。终端截面的向下延拓只用于净空计算，连通路径不能走该虚拟区域。检验范围是磁子上方的主体气道，不是接触线、薄膜、侧面润湿或装置有效性。未接触时跳过距离变换，避免给每个正常前期步骤增加无用开销。
5. `projection3d.py` / `projection3d_gpu.py`：加入最大投影散度目标。根据离散恒等式 $D\mathbf u^{n+1}=\Delta t(Ap-b)/V$ 收紧 PCG 相对容差，并独立检查实际最大散度；另记录残差预报散度和恒等式缺口。未放宽原相对残差门。新配置 `high_fidelity.maximum_projected_divergence_s_inv` 默认 $10^{-4}\,\mathrm{s}^{-1}$，是可调整的数值控制，不是装置精度阈值。
6. `hf3d/types.py` / `recording.py`：保存逐接受步的 CLS 限流体积、CLS 动能变化、实际 PCG 容差、残差预报散度和投影恒等式缺口，并明确新的数值身份与解析门作用域。
7. 更新根 README、程序 README，新增 `04_20260905_数值一致性修订.md`。原高保真冻结文档只加当前版本提示，既有冻结数值数据不改写。

## 验证入口与证据

在 `02_模拟程序` 执行：

```bash
PYTHONPATH=src:tests OPENBLAS_NUM_THREADS=1 xvfb-run -a python -m unittest discover -s tests
```

新增回归覆盖薄缝（含各向异性网格）、大侧腔、薄水平桥、宽气道正例、压力右端尺度变化、伪造 CG 成功状态的拒绝、配置回读和整步数据记录。开启 CLS 的整步烟雾测试使用 $16\times16\times20$ 网格，零驱动及正反转各计算 0.02 秒；这是短数值检查，不是完整物理时长的收敛研究。

CUDA 路径使用同一停止准则，并增加了可在具备 CUDA 环境运行的对照断言；本次环境没有 CuPy/CUDA，设备测试跳过。不能将代码同步或 CPU PASS 表述为 GPU 硬件验证。

完整日志由实际验证工作流写入 `current/2026-09-05_数值审查修复/validation.log`；提交后工作流记录附在本文末尾。

## 未被本轮改变的科学状态

Q1R3 六个必需平滑观测量未通过原空间收敛门。D3R1 的 `G9_DT2` 在接受步 6695、模拟时刻约 0.009196625 秒因最大投影散度失败；旧结果盲审计重算为 $1.0249185899624536\times10^{-4}\,\mathrm{s}^{-1}$，没有识别唯一根因。本轮没有重跑这些 campaign，也没有把压力停止条件修复追认为历史失败的唯一解释。

没有新增动态接触角滞后、惯性界面 ROM、三维湍流闭合或实验标定。前向与 HF3D 的湿润闭合仍不同；对照应先限制在共同适用工况。真实高密度比流动仍需检查界面厚度、网格、步长、壁面和 CSF 平衡误差。

E0-R1A 测量准备继续参考 `current/31_实验前可证伪预测与测量接口.md`。共同二维观测、容器坐标/快门算子、实测初态映射及本装置单相机 FS-SS 路线仍待实现或校准。物理验证、实验验证和装置定量预测资格均未获得。

## 接续优先级

先复核新增质量—动量和投影诊断在目标短工况中的尺度，再做独立网格/时间细化。需要 CUDA 时先运行真实设备对照。新模型或更大流场计算只能依据明确的失配证据推进，不应把回归测试通过作为启动无边界大规模扫描的理由。

## 本轮提交验证记录

实际远端验证：在 `02_模拟程序` 执行 `PYTHONPATH=src:tests OPENBLAS_NUM_THREADS=1 xvfb-run -a python -m unittest discover -s tests -v`，退出码 `0`。NumPy 2.3.5、SciPy 1.17.0、Matplotlib 3.10.8。

工作流运行编号：`33924890990`；测试对象是基于 `a313fb0b5e7ce0a0e71a450878ee00967abb7572` 应用本轮补丁后的工作树。

```text
Python 3.13.15
test_device_matrix_and_face_coefficients_match_cpu_operator (test_gpu_projection.CuPyProjectionParityTests.test_device_matrix_and_face_coefficients_match_cpu_operator) ... skipped 'GPUProjectionUnavailable: CuPy CUDA runtime is unavailable; install the optional gpu dependencies in an isolated environment'
test_device_pressure_history_checkpoint_restores_rejected_trial (test_gpu_projection.CuPyProjectionParityTests.test_device_pressure_history_checkpoint_restores_rejected_trial) ... skipped 'GPUProjectionUnavailable: CuPy CUDA runtime is unavailable; install the optional gpu dependencies in an isolated environment'
test_fp64_projection_reaches_same_discrete_residual_and_divergence_gate (test_gpu_projection.CuPyProjectionParityTests.test_fp64_projection_reaches_same_discrete_residual_and_divergence_gate) ... skipped 'GPUProjectionUnavailable: CuPy CUDA runtime is unavailable; install the optional gpu dependencies in an isolated environment'
test_one_complete_hf3d_step_matches_explicit_cpu_path (test_gpu_projection.CuPyProjectionParityTests.test_one_complete_hf3d_step_matches_explicit_cpu_path) ... skipped 'GPUProjectionUnavailable: CuPy CUDA runtime is unavailable; install the optional gpu dependencies in an isolated environment'
Ran 200 tests in 36.888s
OK (skipped=4)
```
