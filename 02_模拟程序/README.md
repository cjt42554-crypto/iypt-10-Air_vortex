# 模拟程序

本目录包含空气涡流研究平台的源码、测试、配置、示例和高保真模型说明。

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

安装测试依赖后执行：

```bash
python -m pytest tests
```

源码、配置、测试和示例共同定义可复现的计算条件。运行结果应记录模型身份、配置和环境；通过软件测试不自动构成数值资格、物理验证或实验验证。
