# IYPT 10 空气涡流：第一阶段预测改进版

本目录是当前活动候选，固定入口为 `90_候选工作区/current/候选成果/`。

仓库根目录 `01_最终理论/`、`02_模拟程序/`、`03_项目资料/` 是成熟基线。本目录包含候选理论、程序和资料；结构性理论只随候选保留。

本轮实现状态目标为：

```text
PASS_STAGE1_IMPLEMENTATION__NO_GO_PHYSICAL_PREDICTION
```

该状态只表示入口、观测合同、比较门、事件与相机链、数值诊断和验证器形成可执行闭环，不表示模型已经标定，也不表示 HF3D 已获得装置级预测效力。

## 启动

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

验证器会发现 Git 根、执行 Stage1 指定测试与完整回归，并把命令、返回码、日志哈希、Git 身份、成熟 tree 和源码内容 SHA-256 写入新建结果。活动入口负责定位当前候选，并调用 `run_predictive_stage1_validation_impl.py` 执行验证逻辑。

交付包总验收脚本应传入当前仓库根，而不是写死本机绝对路径：

```bash
bash scripts/10_run_acceptance.sh "$(git rev-parse --show-toplevel)"
```

仓库提交本身不等于测试已经运行。HF3D 的静滴、寄生流、毛细波、联合收敛、真实磁子轨迹、相机标定和独立盲验证仍是物理预测 GO 的后续硬门。

逐项根因与残余风险见 `ISSUE_LEDGER.md`；当前资格和未执行项以 `../管理/验证记录.md` 为准。
