# IYPT 10 空气涡流：第一阶段预测改进版

本目录是从基线提交 `e7cff65b8bda38d0fd6cec848866a2a3d5a8caa6` 建立的隔离候选。2026-08-24 起，活动路径统一为 `90_候选工作区/current/候选成果/`；冻结历史证据中的旧路径仍按生成时原样保留。

仓库根目录 `01_最终理论/`、`02_模拟程序/`、`03_项目资料/` 是成熟基线。本目录内部复用基线副本并追加第一阶段内容；结构性理论只随副本保留。

本轮实现状态目标为：

```text
PASS_STAGE1_IMPLEMENTATION__NO_GO_PHYSICAL_PREDICTION
```

该状态只表示入口、观测合同、比较门、事件与相机链、数值诊断和验证器形成可执行闭环，不表示模型已经标定，也不表示 HF3D 已获得装置级预测效力。

## 启动隔离版

```bash
cd "90_候选工作区/current/候选成果/02_模拟程序"
./run_stage1_gui.sh
./run_stage1_cli.sh --help
```

## 验证

在包含 NumPy、SciPy、Matplotlib 和 pytest 的 Python 环境中，从仓库根目录执行：

```bash
cd "90_候选工作区/current/候选成果"
python3 "03_项目资料/03_验证协议与历次结果/02_验证程序/run_predictive_stage1_validation.py" \
  --include-baseline-tests
```

验证器会发现 Git 根、执行 Stage1 指定测试与完整回归，并把命令、返回码、日志哈希、Git 身份、成熟 tree 和源码内容 SHA-256 写入新建结果。布局 v2 使用一个薄包装器适配新工作区前缀；原验证器实现作为 `run_predictive_stage1_validation_impl.py` 保留，避免无必要改写冻结逻辑。

交付包总验收脚本应传入当前仓库根，而不是写死本机绝对路径：

```bash
bash scripts/10_run_acceptance.sh "$(git rev-parse --show-toplevel)"
```

仓库提交本身不等于测试已经运行。HF3D 的静滴、寄生流、毛细波、联合收敛、真实磁子轨迹、相机标定和独立盲验证仍是物理预测 GO 的后续硬门。

逐项根因与残余风险见 `ISSUE_LEDGER.md`，实现与验收摘要见 `REPAIR_REPORT.md`。
