# 理论—程序对齐与实验前收口

源基线：`3d974e554a34cadfa95a3978c69218e107277512`，开始时 `main` 与远端一致且工作树干净。本轮代码与配置进入同一正式包；未修改前向物理方程、结构性理论、冻结历史证据或 HF3D 生产源码。数值理论新增实现说明，history 保持空白。

状态：**CP 与统一有效区间可独立交付；TF 局部模式有合成数值证据；SH 只完成固定参考域子阶段，移动面能量资格失败；全域耦合与装置预测未建立。** 这些证据足以为当前轻量工作设置明确的暂停点，不是“全部 10-00—10-05 通过”。

验证的未提交程序差异保存在 [压缩 patch](validated-program.patch.gz)，基线、补丁哈希与实际源树标识见 [源绑定](validated-source.json)。补丁涵盖程序、配置和测试；最终提交保存相同实现。

该原始补丁精确绑定实现提交 `2d251f6`。随后发现的 CI 测试兼容性修正只改变 TF 时间验收测试，生产源码哈希仍相同；最终测试身份和日志另见 [CI 兼容性证据](ci-compatibility.json)、[最终本机回归](solver-regression-ci-fix.txt)、[相同 CI 依赖的本机无显示复现](solver-regression-ci-env.txt)。最终分别为 250 项中 246 通过/4 CUDA 跳过，以及 245 通过/4 CUDA 加 1 Tk 跳过。

## 实际证据

| 范围 | 当前证据 | 结论上限 |
| --- | --- | --- |
| 基线 | [源身份](10-00/baseline-source.json)、[200 项基线回归](10-00/baseline-regression.txt) | 196 通过，4 CUDA 跳过；旧源码 |
| 完整程序 | [最终回归](solver-regression.txt)、[环境](environment.json) | 250 项：246 通过，4 CUDA 跳过；35.440 s |
| 10-00 / 10-01 | `test_forward_validity.py`、`test_gas_pressure.py`；[压差与实际后端退出](10-01/pressure-and-exit.json) | 两个局部流量反例、首样本缺证据/封闭气体、内部步首次退出、归档/GUI/扫描消费 |
| 10-02 固定域 | `test_stokes_branch.py`，最终回归内的 SH_MANUFACTURED、SH_PLANAR 数值表 | 四档网格速度/压力/应变约二阶；三条平面层谱路线；静水、压力零模态 |
| 10-02 移动面 | [可重跑小试验](10-02/verify_moving_reference.py)、[结果](10-02/moving_reference.json) | 能量目标 **FAIL**；完整非线性移动域 NOT_IMPLEMENTED；不得称 10-C3 完成 |
| 10-03 局部膜 | `test_thin_film.py`，最终回归内 TF_NONLINEAR_SPACE/TIME 与 TF_OPEN_DRAINAGE | 固定物性空间细化、独立 Radau 时间参考、开闭能量/体积、正厚度及显式诊断尾段 |
| 10-04 | [匹配研究](10-04/README.md)、[9 项代数与 7 个失配反例](10-04/results.json) | 合成端口研究；无 SH–TF 生产耦合、无真实重叠环带证据 |
| 10-05 | [定向复核](10-05/diagnosis.json)、[CLS 最小反例](10-05/cls_pairing.json) | 旧 D3 停止准则缺陷有支持证据；CPU 回归通过；旧动态轨迹与 GPU 未重跑 |
| 实验准备 | [最小输入与测量顺序](../../../04_实验与数据/README.md)、`test_observation_preparation.py` | 合成坐标/曝光/泄漏检查；无真实校准或留出评分 |

最终回归包括原有全部回归，没有删除有效物理门或放宽物理阈值。旧比较测试改为显式合成信号身份；真实 HF 没有新定量合同，比较应拒绝生成定量指标。

## 有意义的失败与修正

SH 制造解首先发现顶面切应力中 `dw/dr` 符号错误。修正前 8/16/32/64 网格的压力归一误差约为 1.9671/1.9143/1.8924/1.8858，未收敛；修正后 16/32/64/128 为 0.02273/0.005783/0.001454/0.0003643，速度和四个应变分量也收敛。静水背景直接参与强求解会激发抵消误差，改为相对静水压力求解，使静止例保持零速度。BVP 采用无量纲坐标以避免量纲导数吞噬绝对容差。保留首次分支验收日志 [branches-first](10-00/branches-first.txt) 和 [二次集成](10-00/branches-consumers-second.txt)。

SH 移动面另有未修复问题：固定物性合成试验的 8/16/32/64 网格能量相对误差约为 `7.49e-4 / 9.60e-4 / 4.47e-4 / 1.50e-4`，均不满足 `1e-6`。空间表面趋近和 RK4 时间误差不能代替能量验收。该问题与完整非线性移动域均保留为研究积压，GUI 明示限制。试验的 100 Pa s 是单独声明的合成黏性问题，不是将真实水的黏度调高以取得资格。

TF 开放排液的 24 格粗网格误差 `3.07e-5`，未满足 `1e-6`；保持物性、容差不变，24/48/96/192 格误差依次约 `3.07e-5 / 7.68e-6 / 1.92e-6 / 4.80e-7`，空间二阶。不是放宽目标，也没有用标量 ODE 结果替代 PDE。

最初的若干集成失败来自旧测试把缺资格结果当全程有效；完整中间日志保留在 10-00，最后结果以上述最终回归为准。导入拼写和尚未完成代码的短暂错误不作为科学失败。

首次远端 CPU 回归的 [原始日志](ci-first-failure-33985096105.log.gz) 保留了 TF 时间测试的严格单调断言失败。自适应 BDF 会改变阶数与实际步长，`max_step` 不是固定步长；本机把步长上限继续减至 0.000625 s 也出现误差回升。修正后同时报告四档步长上限与四档容差对独立 Radau 的误差，保留每档 `1e-6` 目标并要求粗细改善，不再从任意相邻步长上限强报固定方法阶数。本机最终容差细化误差约 `1.19e-10`，参考自细化误差约 `8.30e-17`；没有改物理参数、生产算法、能量/质量门或固定域 PDE 的空间收敛要求。

## 重跑入口

仓库根目录：

```bash
python3 -m unittest discover -s tools -p 'test_repository_docs.py' -v
python3 tools/check_repository_docs.py
PYTHONPATH='02_模拟程序/src:02_模拟程序/tests' OPENBLAS_NUM_THREADS=1 python3 -m unittest discover -s '02_模拟程序/tests' -v
PYTHONPATH='02_模拟程序/src' OPENBLAS_NUM_THREADS=1 python3 '03_研究记录/维护/2026-09-06_理论程序对齐/10-02/verify_moving_reference.py'
python3 '03_研究记录/维护/2026-09-06_理论程序对齐/10-04/verify_matching_contract.py'
PYTHONPATH='02_模拟程序/src' OPENBLAS_NUM_THREADS=1 python3 '03_研究记录/维护/2026-09-06_理论程序对齐/10-05/quantify_cls_pairing.py'
PYTHONPATH='02_模拟程序/src' OPENBLAS_NUM_THREADS=1 python3 '03_研究记录/维护/2026-09-06_理论程序对齐/10-05/run_targeted_review.py'
```

重算应在新副本/新输出路径保留结果，不覆盖已经作为证据引用的 JSON。这些脚本不推进大型三维动态轨迹、不扫描整台机器，也不调用 GPU。
