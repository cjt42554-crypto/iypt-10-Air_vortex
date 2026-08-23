# 程序成熟基线

本目录是空气涡流研究平台的当前程序基线，采用 Python 项目结构。

## 目录与入口

- [`src/`](src/)：程序源码。
- [`tests/`](tests/)：自动化测试。
- [`configs/`](configs/)：可追踪配置。
- [`examples/`](examples/)：示例输入与用法。
- [`pyproject.toml`](pyproject.toml)：Python 版本、依赖、包和命令行入口。
- [`run.py`](run.py)：直接运行入口。
- [`run_gui.sh`](run_gui.sh)：图形界面启动脚本。
- [`03_第一性原理高保真模拟.md`](03_第一性原理高保真模拟.md)：高保真模型说明。

## 基本运行

需要 Python 3.11 或更高版本：

```bash
python -m pip install -e .
airvortex --help
airvortex-gui
```

在测试环境安装 `pytest` 后执行：

```bash
python -m pytest tests
```

## 程序契约

- `src`、配置、测试和示例共同定义一个可复现版本；运行结果必须记录模型身份、配置和环境。
- 临时输出、大体积结果和候选修订不直接写回成熟源码；新实现默认进入 [`../90_候选工作区/current/`](../90_候选工作区/current/)。
- 被接受的验证材料按类别归档到 [`../03_项目资料/`](../03_项目资料/)。
- 当前资格和下一步见 [`../00_项目控制/STATUS.md`](../00_项目控制/STATUS.md)。
