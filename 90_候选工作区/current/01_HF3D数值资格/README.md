# HF3D 数值资格候选工具包

本目录用于 IYPT 2027 第 10 题“空气涡流”的无实验阶段数值资格工作。它不修改正式理论或正式模拟程序，不引入新的物理闭合，也不把计算收敛解释为实验验证。

## 文件

- `hfq_core.py`：配置审计、细化设计门、算例生成、输入身份、误差范数、三层观测阶、GCI 和拓扑事件一致性。
- `hfq.py`：`audit-config`、`generate`、`run`、`analyse` 和 `diagnose-q1r` 五个命令。
- `hfq_failure_diagnostics.py`：Q1R 稠密散度轨迹、终端 CFL 完整性、CUDA 单步压力重放和候选控制设计诊断。
- `campaign_screening.json` 与 `generated_screening/`：原冻结 Q1；真实运行证明其时间步设计不可识别，已失败闭锁，仅保留为反例证据。
- `reports/q1_screening/`：原 Q1 的 `FAIL_REFINEMENT_DESIGN_GATE` 机读与 Markdown 报告。
- `campaign_screening_q1r.json` 与 `generated_screening_q1r/`：修复时间步身份后的 Q1R；六个逻辑层级对应五次唯一运行。
- `reports/q1r_screening/`：Q1R 的真实失败证据；中网格违反候选投影散度门，细网格由源项后 CFL 门终止。
- `reports/q1r_diagnosis/`：两个失败的定位与候选修复边界；当前状态为 `DIAGNOSED_PRESSURE_REPLAY_PASS__CFL_OPEN__Q1R2_NOT_FROZEN`。
- `targeted_replay/medium_pressure_1e-7.json`：只收紧压力相对容差的中网格完整重放配置；该单例通过不等于 Q1R2。
- `formal_candidate/`：尚未提升的正式 CFL 控制器补丁、细网格 0.013 s CUDA 窗口配置、归档核验器和提升审查证据。正式程序仍未修改。
- `evidence/hf_preview_quick_resolution_audit.*`：对正式快速配置的配置级审计。
- `tests/`：资格算法、归档失败诊断和候选数值控制的短时单元测试。

## 关键原则

资格流程只允许空间层和时间层改变以下字段：

- `high_fidelity.radial_cells`；
- `high_fidelity.azimuthal_cells`；
- `high_fidelity.axial_cells`；
- `high_fidelity.time_step_s`。

其余几何、物性、实际磁子转速历程、运行时长、接触角和数值本构控制必须完全相同。工具会对去除上述四个字段后的输入生成规范化 SHA-256；不一致时失败闭锁。

登记的 `time_step_s` 是最大允许步长，不保证就是实际步长。正式 HF3D 还施加毛细稳定上限

$$
\Delta t_\sigma
=0.20\sqrt{\frac{\rho_g h_{\min}^3}{\pi\sigma}}.
$$

因此，空间细化组的共同登记步长必须在所有网格上都低于该上限；时间细化组的每一层也必须低于细网格上的该上限，否则空间误差会与时间误差混合，或多个时间层会坍缩为相同实际步长。`run` 在启动前执行静态设计门，`analyse` 还会读取归档的 `diagnostic_dt_s`，要求至少 95% 的步实际命中各层登记上限，并检查空间组实际最大步长相同、时间组实际最大步长严格递减。

归档配置允许正式加载器补齐未在生成 JSON 中显式写出的源码版本默认值，但生成 JSON 中每一条显式路径都必须与归档值一致。报告还要求各运行的正式源码边界哈希、Python/NumPy/SciPy、压力加速器和线程策略身份一致；Q1R 额外要求实际选择 `cuda`，不接受静默 CPU 回退。

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
python3 "90_候选工作区/current/01_HF3D数值资格/hfq.py" \
  audit-config \
  "02_模拟程序/configs/hf_preview_quick.json" \
  --json "90_候选工作区/current/01_HF3D数值资格/evidence/hf_preview_quick_resolution_audit.json" \
  --markdown "90_候选工作区/current/01_HF3D数值资格/evidence/hf_preview_quick_resolution_audit.md"
```

### 2. 重新生成冻结算例

```bash
python3 "90_候选工作区/current/01_HF3D数值资格/hfq.py" \
  generate \
  "90_候选工作区/current/01_HF3D数值资格/campaign_screening_q1r.json" \
  "90_候选工作区/current/01_HF3D数值资格/generated_screening_q1r_new"
```

生成器拒绝写入非空目录。确认新旧清单一致后再替换冻结目录。

### 3. 安装正式程序并执行算例

Q1R 已经失败闭锁。以下命令仅作为在全新输出目录中复现实证路径的说明；默认后续工作应先诊断失败并冻结新的 Q1R2，不应删除现有本地失败档案后原样重跑 Q1R。

```bash
python3 -m venv "90_候选工作区/current/01_HF3D数值资格/.venv-gpu"
"90_候选工作区/current/01_HF3D数值资格/.venv-gpu/bin/python" \
  -m pip install -e "./02_模拟程序[gpu]"

"90_候选工作区/current/01_HF3D数值资格/.venv-gpu/bin/python" \
  "90_候选工作区/current/01_HF3D数值资格/hfq.py" \
  run \
  "90_候选工作区/current/01_HF3D数值资格/generated_screening_q1r/cases_manifest.json" \
  "90_候选工作区/current/01_HF3D数值资格/runs/q1r_screening" \
  --resume
```

运行器按顺序执行，避免多个大网格同时争用内存。每个唯一算例保存独立日志。若只需检查命令，可加 `--dry-run`；若要先做一个可恢复的真实资源探针，可加 `--only space_L0_4cells`。`--only` 可重复指定，未选算例不会运行。`--resume` 不会把终端失败档案当作完成结果；出现失败后必须先诊断原因，不能以续跑规避失败门。

### 4. 重放 Q1R 失败诊断

该命令读取本地忽略的 Q1R 中网格完整档案与细网格终端档案，生成已跟踪机读诊断；默认还会在 CUDA 上用归档终态重建一个中网格单步，比较 $10^{-6}$、$10^{-7}$ 和 $10^{-8}$ 三个压力容差。重建单步不是稠密峰值步的精确恢复。提供 `--completed-pressure-replay-archive` 时，命令还会校验只改变压力容差的完整重放档案及其全部 Q1R 单例质量门。

```bash
"90_候选工作区/current/01_HF3D数值资格/.venv-gpu/bin/python" \
  "90_候选工作区/current/01_HF3D数值资格/hfq.py" \
  diagnose-q1r \
  "90_候选工作区/current/01_HF3D数值资格/runs/q1r_screening/space_L1_6cells" \
  "90_候选工作区/current/01_HF3D数值资格/runs/q1r_screening/space_L2_9cells" \
  "90_候选工作区/current/01_HF3D数值资格/reports/q1r_diagnosis/diagnosis_report.json" \
  --completed-pressure-replay-archive \
  "90_候选工作区/current/01_HF3D数值资格/runs/q1r_diagnosis/medium_pressure_1e-7"
```

没有可用 CUDA 时可加 `--skip-projection-replay`，但这种输出只能重算归档轨迹、终端完整性和已有完整重放档案，不能声称本机重新执行了压力机制单步。完整重放原始档案同样位于本地忽略目录；已跟踪 JSON 报告保存其载荷哈希、执行源码身份、CUDA 身份、稠密峰值和质量门结果。

### 5. 生成资格报告

```bash
"90_候选工作区/current/01_HF3D数值资格/.venv-gpu/bin/python" \
  "90_候选工作区/current/01_HF3D数值资格/hfq.py" \
  analyse \
  "90_候选工作区/current/01_HF3D数值资格/generated_screening_q1r/cases_manifest.json" \
  "90_候选工作区/current/01_HF3D数值资格/runs/q1r_screening" \
  "90_候选工作区/current/01_HF3D数值资格/reports/q1r_screening"
```

报告状态可能为：

- `INCOMPLETE_MISSING_OR_UNREADABLE_RUNS`；
- `FAIL_REFINEMENT_DESIGN_GATE`；
- `FAIL_PROVENANCE_INPUT_OR_COMPLETION_GATE`；
- `FAIL_NUMERICAL_QUALITY_GATE`；
- `FAIL_REFINEMENT_CONVERGENCE_GATE`；
- `NUMERICAL_SCREENING_PASSED`；
- 对允许更高声称的后续计划，最多为 `NUMERICALLY_QUALIFIED_WITHIN_FROZEN_MODEL`。

无论状态为何，报告都保持 `experimental_validation = NOT_PERFORMED` 和 `apparatus_prediction_allowed = false`。

## Q1/Q1R 的严格边界

原 Q1 已因细化设计门失败而作废，不能把其中任何层间差异解释为空间或时间收敛。Q1R 的静态设计门通过，但真实运行没有通过：粗层完整结束且单例质量门通过；中层完整结束但最大投影散度为 $5.807218\times10^{-4}\ \mathrm{s}^{-1}$，超过候选上限 $1.0\times10^{-4}\ \mathrm{s}^{-1}$；细层在约 60.7% 处因源项后对流 CFL 为 0.3504、超过冻结上限 0.3500 而失败闭锁；两个更细时间层未运行。因此 Q1R 当前状态是 `FAIL_PROVENANCE_INPUT_OR_COMPLETION_GATE`，并同时存在独立数值质量失败，不能据此声称空间或时间收敛。

Q1R 失败已经完成候选层定位：中网格共有 212 个稠密步超过绝对散度门，峰值为第 2228 步；正式相对压力残差门不保证候选绝对散度门。只把压力相对容差从 $10^{-6}$ 收紧为 $10^{-7}$ 的中网格完整 CUDA 重放在同一峰值步把最大散度降至 $5.018070\times10^{-5}\ \mathrm{s}^{-1}$，零步超门，并通过全部 Q1R 单例质量门。细网格则证明正式时间步控制没有把选步目标与源项后硬门分开，且失败档案缺少前 CFL 与逐算子增量，不能唯一归因。详细证据见 `reports/q1r_diagnosis/diagnosis_report.*`。

Q1R2 当前仍为 `NOT_FROZEN`。中网格压力容差路径已有完整重放支持，候选 CFL 控制器替换稿和细网格失败窗口也已完成，但补丁尚未获准提升到正式程序，新的共同/递减步长也未冻结；不得把候选首轮缩步值、单个中网格通过或候选细网格窗口通过伪装成 Q1R2 通过。Q1R 的时长和分辨率本来也只用于早期瞬态筛查，不覆盖统计稳态，不足以给出漏斗稳态深度、首次触杆转速、稳定气道半径或结构性分岔。Q1R2 完整通过后仍应另行冻结平滑区 Q2 和近拓扑区 Q2，不得把任何数值状态直接升级为物理预测通过。

此后已在 `formal_candidate/` 形成可应用的事务性 CFL 控制器补丁，并用隔离候选源码完成真实细网格 0.013 s CUDA 窗口：1757 个接受步，投影后最大 CFL 0.336906，最大投影散度 $2.490918\times10^{-5}\ \mathrm{s}^{-1}$，全部目标门通过。该窗口没有触发失败重试，而是由联合已知源项控制器预限步 112 次越过旧故障时刻。旧登记步长 $7.5\times10^{-6}\ \mathrm{s}$ 的命中比例只有 92.8856%，低于资格门 95%，因此不得直接沿用；正式提升和共同步长重新冻结仍需人工授权，Q1R2 继续保持 `NOT_FROZEN`。

## 测试

```bash
cd "90_候选工作区/current/01_HF3D数值资格"
python3 -Werror -m unittest discover -s tests -v
```

单元测试只验证资格算法、归档诊断和候选控制关系；CUDA 终态单步重放也只验证压力容差机制。本轮完整中网格重放验证了该单例的真实峰值窗口，但这些证据都不替代 Q1R2 多网格、多时间步完整运行。
