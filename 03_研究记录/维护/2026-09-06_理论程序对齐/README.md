# 理论—程序对齐证据

生产实现提交为 `2d251f6bb21f88b74fa887b5e4a400f3a1479430`；最终测试修正为 `ede9802d686eea72b592a0c6059f18119e2f515d`，后者未改生产源码。源程序 SHA-256 为 `b422a7ea905f9dc31efacbfd9d7208632a92b9cf150dcd46c974b3ae1766b4f3`，算法见正式归档模块 `source_tree_identity()`。

CP 与统一有效区间已接入；TF 有局部合成数值证据；SH 固定域通过、移动面能量失败；全域耦合与装置预测未建立。精简仅删除重复过程记录，以下结果未改写。

## 保留证据

| 范围 | 文件 | 结论上限 |
| --- | --- | --- |
| 最终软件回归 | [本机完整日志](solver-regression-ci-fix.txt)、[环境](environment.json) | 250 项：246 通过，4 CUDA 跳过；Tk 本机通过 |
| 跨依赖复验 | [CI 兼容性记录](ci-compatibility.json)、[无显示复验](solver-regression-ci-env.txt)、[首次 CI 失败](ci-first-failure-33985096105.log.gz) | NumPy 2.5.2 / SciPy 1.18.1 本机复验：245 通过，4 CUDA 加 1 Tk 跳过 |
| 实际 CLI | [三个示例的运行、导出与回读](cli-examples.json) | 合成软件示例，不是装置数据 |
| 10-00 / 10-01 | [压差与正式退出](10-01/pressure-and-exit.json) | 局部流量反例、资料不足/封闭气体、内部步首次退出、归档/GUI/扫描消费 |
| 10-02 固定域 | 正式 `test_stokes_branch.py`；最终日志内 SH_MANUFACTURED、SH_PLANAR | 四档速度、压力、应变约二阶；独立平面谱核对 |
| 10-02 移动面 | [小试验](10-02/verify_moving_reference.py)、[结果](10-02/moving_reference.json) | 能量目标 FAIL；完整非线性移动域未实现 |
| 10-03 局部膜 | 正式 `test_thin_film.py`；最终日志内 TF_NONLINEAR_SPACE/TIME、TF_OPEN_DRAINAGE | 固定物性空间细化、独立 Radau 时间参考、开闭体积/能量、正厚度 |
| 10-04 | [匹配研究](10-04/README.md)、[代数与反例](10-04/results.json) | 9 项代数通过，7 个失配反例；无生产耦合 |
| 10-05 | [旧失败定向复核](10-05/diagnosis.json)、[CLS 反例](10-05/cls_pairing.json) | D3 停止准则缺陷有支持证据；旧动态轨迹与 GPU 未重跑 |

完整回归已保留全部有效程序测试。缺资格的旧 HF 结果不能生成定量比较。

## 数值边界

SH 固定域修复了顶面切应力符号和静水抵消误差；速度、压力和四个应变进入收敛区。移动面 8/16/32/64 格能量相对缺口约 `7.49e-4 / 9.60e-4 / 4.47e-4 / 1.50e-4`，未达到 `1e-6`。RK4 时间趋近不替代能量门。试验的 100 Pa s 是明示合成物性，不能当作真实水的资格。

TF 开放排液在固定物性下，24/48/96/192 格误差约 `3.07e-5 / 7.68e-6 / 1.92e-6 / 4.80e-7`。自适应 BDF 的步长上限不等于固定步长，不能强求每次上限减半时误差严格单调；最终测试分别细化步长上限和容差，保留每档 `1e-6` 精度目标并检查对独立 Radau 的粗细改善。没有放宽物理、质量或能量阈值。

当前失败状态和下一次验收详见 [current](../../../00_项目管理/交接说明_current.md)。

## 复跑与来源

完整回归从仓库根目录运行：

```bash
PYTHONPATH='02_模拟程序/src:02_模拟程序/tests' OPENBLAS_NUM_THREADS=1 python3 -m unittest discover -s '02_模拟程序/tests' -v
```

本目录的 `10-02/verify_moving_reference.py`、`10-04/verify_matching_contract.py`、`10-05/quantify_cls_pairing.py` 和 `10-05/run_targeted_review.py` 是保留的小规模核查入口。复算先读其参数，另存结果或使用新副本，不覆盖冻结 JSON；它们不推进大型三维轨迹。

原基线 `3d974e5`、未提交补丁及中间失败/通过日志可在精简前 Git 提交 `ede9802` 中查阅。相同程序已由上述实现提交完整保存，因此当前目录不再放一份重复源码补丁。恢复方法见 [研究记录](../../README.md#历史恢复)。
