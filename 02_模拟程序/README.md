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
