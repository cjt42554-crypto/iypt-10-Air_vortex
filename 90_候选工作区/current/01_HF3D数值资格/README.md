# HF3D 数值资格候选工具包

本目录用于 IYPT 2027 第 10 题“空气涡流”的无实验阶段数值资格工作。它不修改正式理论或正式模拟程序，不引入新的物理闭合，也不把计算收敛解释为实验验证。

## 文件

- `hfq_core.py`：配置审计、算例生成、输入身份、误差范数、三层观测阶、GCI 和拓扑事件一致性。
- `hfq.py`：`audit-config`、`generate`、`run`、`analyse` 四个命令。
- `campaign_screening.json`：已冻结的 Q1 早期瞬态筛查计划。
- `generated_screening/`：由 Q1 计划生成的配置和机读清单；六个逻辑层级对应五次唯一运行。
- `evidence/hf_preview_quick_resolution_audit.*`：对正式快速配置的配置级审计。
- `tests/test_hfq_core.py`：不依赖 HF3D 长时运行的合成单元测试。

## 关键原则

资格流程只允许空间层和时间层改变以下字段：

- `high_fidelity.radial_cells`；
- `high_fidelity.azimuthal_cells`；
- `high_fidelity.axial_cells`；
- `high_fidelity.time_step_s`。

其余几何、物性、实际磁子转速历程、运行时长、接触角和数值本构控制必须完全相同。工具会对去除上述四个字段后的输入生成规范化 SHA-256；不一致时失败闭锁。

正式后端已有的 `bar_diameter_cells_min_spacing` 是最佳方向分辨率。工具另外使用

$$
N_{D,\mathrm{worst}}
=\frac{D_b}{\max(\Delta x,\Delta y,\Delta z)}
$$

作为保守几何门，并检查网格各向异性。

## 使用

以下命令从仓库根目录执行。

### 1. 审计一个配置

```bash
python "90_候选工作区/current/01_HF3D数值资格/hfq.py" \
  audit-config \
  "02_模拟程序/configs/hf_preview_quick.json" \
  --json "90_候选工作区/current/01_HF3D数值资格/evidence/hf_preview_quick_resolution_audit.json" \
  --markdown "90_候选工作区/current/01_HF3D数值资格/evidence/hf_preview_quick_resolution_audit.md"
```

### 2. 重新生成冻结算例

```bash
python "90_候选工作区/current/01_HF3D数值资格/hfq.py" \
  generate \
  "90_候选工作区/current/01_HF3D数值资格/campaign_screening.json" \
  "90_候选工作区/current/01_HF3D数值资格/generated_screening_new"
```

生成器拒绝写入非空目录。确认新旧清单一致后再替换冻结目录。

### 3. 安装正式程序并执行算例

```bash
cd "02_模拟程序"
python -m pip install -e .
cd ..

python "90_候选工作区/current/01_HF3D数值资格/hfq.py" \
  run \
  "90_候选工作区/current/01_HF3D数值资格/generated_screening/cases_manifest.json" \
  "90_候选工作区/current/01_HF3D数值资格/runs/q1_screening" \
  --resume
```

运行器按顺序执行，避免多个大网格同时争用内存。每个唯一算例保存独立日志。若只需检查命令，可加 `--dry-run`。

### 4. 生成资格报告

```bash
python "90_候选工作区/current/01_HF3D数值资格/hfq.py" \
  analyse \
  "90_候选工作区/current/01_HF3D数值资格/generated_screening/cases_manifest.json" \
  "90_候选工作区/current/01_HF3D数值资格/runs/q1_screening" \
  "90_候选工作区/current/01_HF3D数值资格/reports/q1_screening"
```

报告状态可能为：

- `INCOMPLETE_MISSING_OR_UNREADABLE_RUNS`；
- `FAIL_PROVENANCE_INPUT_OR_COMPLETION_GATE`；
- `FAIL_NUMERICAL_QUALITY_GATE`；
- `FAIL_REFINEMENT_CONVERGENCE_GATE`；
- `NUMERICAL_SCREENING_PASSED`；
- 对允许更高声称的后续计划，最多为 `NUMERICALLY_QUALIFIED_WITHIN_FROZEN_MODEL`。

无论状态为何，报告都保持 `experimental_validation = NOT_PERFORMED` 和 `apparatus_prediction_allowed = false`。

## Q1 的严格边界

Q1 的时长和分辨率只用于早期瞬态筛查。它不覆盖统计稳态，不足以给出漏斗稳态深度、首次触杆转速、稳定气道半径或结构性分岔。Q1 通过后应另行冻结平滑区 Q2 和近拓扑区 Q2，不得直接把 Q1 状态升级为物理预测通过。

## 测试

```bash
cd "90_候选工作区/current/01_HF3D数值资格"
python -Werror -m unittest discover -s tests -v
```

单元测试只验证资格算法和配置冻结逻辑；它不替代 HF3D 多层运行。
