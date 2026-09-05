# 模拟程序

本目录包含空气涡流研究平台的源码、测试、配置、示例和高保真模型说明。

## 当前实现与证据

2026-09-05 已修复 CLS 相分数—动量同步、通道净空判据和散度关联的压力停止条件。完整算法说明见 [数值一致性修订](04_20260905_数值一致性修订.md)，实际测试和未解决项见 [当前交接](../00_项目管理/交接说明_current.md)。原冻结文档不是新版本的收敛证据；物理闭合、装置标定和 Q1R3/D3R1 历史失败状态没有因此取得资格。

## 目录与入口

- [`src/`](src/)：程序源码。
- [`tests/`](tests/)：自动化测试。
- [`configs/`](configs/)：运行配置。
- [`examples/`](examples/)：示例输入。
- [`pyproject.toml`](pyproject.toml)：Python 版本、依赖、包和命令行入口。
- [`run.py`](run.py)：直接运行入口。
- [`run_gui.sh`](run_gui.sh)：图形界面启动脚本。
- [`03_第一性原理高保真模拟.md`](03_第一性原理高保真模拟.md)：高保真模型说明。

## 运行

需要 Python 3.11 或更高版本：

```bash
python -m pip install -e .
airvortex --help
airvortex-gui
```

完整 unittest 测试（无需额外 pytest 依赖）：

```bash
PYTHONPATH=src:tests OPENBLAS_NUM_THREADS=1 python -m unittest discover -s tests
```

无显示环境下可用 `xvfb-run -a` 包装命令执行 Tk 布局测试；没有显示器时该项跳过。CUDA 测试需要另行安装并具备可用 CuPy/CUDA 设备，未执行的测试不能记为 PASS。

源码、配置、测试和示例共同定义可复现的计算条件。运行结果应记录模型身份、配置和环境；通过软件测试不自动构成数值资格、物理验证或实验验证。

维护权限与交接方式统一见 [根工作规范](../AGENTS.md) 和 [目录指南](../00_项目管理/README.md)。本文件只描述科学/技术接口，不另设审批要求。

## CP / SH / TF 与实验前使用

`forward.model_branch` 选择 `AV-FWD-CP-v2`、`AV-FWD-SH-v1` 或 `AV-FWD-TF-v1`。GUI 可载入同一 JSON，模型栏与运行用途栏保留分支选择；TF 图显示局部膜厚，SH 输出速度/压力与自由面。新字段可省略以使用默认值，未知字段/分支拒绝，不默默回退。

- [CP 合成开放储库](configs/cp_synthetic_reservoir.json)：小网格短时演化，气压来源明确标为合成假设。
- [SH 固定参考域静止例](configs/sh_fixed_reference_static.json)：独立压力—速度算子，未取得完整移动面资格。SH 的能量目标失败与非线性移动域缺口见 current，不能用于全装置成道定量预测。
- [TF 局部非均匀薄膜](configs/tf_local_synthetic.json)：给定局部初边值、物理滑移/润湿输入，不与全容器自动耦合。

从本目录运行（当前主机使用 `python3`，无需重复安装已具备的依赖）：

```bash
PYTHONPATH=src python3 -m airvortex run --backend forward --config configs/cp_synthetic_reservoir.json --output /tmp/airvortex-cp-example
PYTHONPATH=src python3 -m airvortex run --backend forward --config configs/tf_local_synthetic.json --output /tmp/airvortex-tf-example
```

输出目录须为新路径；再次运行换一个目录。归档包含输入配置、模型/实现/理论身份、实际时序、诊断和 `quantitative_valid_mask`。默认 `strict` 在首次物理失效处停止；明确设 `diagnostic` 才续跑。气压证据不足的原默认 CP 在初始状态停止，这是有意的失败闭锁。

合成储库来源不能直接复制成实测资格。`gas` 要给压力误差预算、来源和时间/几何适用域；受限气道还需要端点、局部截面、导数和近似误差信息。旧归档缺有效性信息会保留旧身份并标为未核定，不能拿失效尾段、旧 HF 结果直接生成定量比较或扫描临界值。

[实验输入与采集准备](../04_实验与数据/README.md) 给出最小准备顺序。[数值理论](../01_最终理论/02_前向预测理论的数值计算理论.md) 第 11、16 节说明算法与覆盖范围。新增小规模验收可用：

```bash
PYTHONPATH=src:tests OPENBLAS_NUM_THREADS=1 python3 -m unittest test_gas_pressure test_forward_validity test_stokes_branch test_thin_film test_observation_preparation test_hf3d_10_05 -v
```

这组软件回归和局部数值检查不含大型 CFD、CUDA 等价性、实际标定或留出实验。
