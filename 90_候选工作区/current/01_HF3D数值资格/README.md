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
- `formal_candidate/`：已经提升的事务性 CFL 控制器冻结补丁、细网格 `0.013 s` CUDA 窗口配置、归档核验器和提升审查历史证据。
- `reports/formal_replay/`：从提升后正式路径重新执行的 5 项 CUDA 测试和细网格窗口证据，状态为 `FORMAL_CUDA_TESTS_AND_FINE_WINDOW_REPLAY_PASS`。
- `campaign_q1r2_dt_pilot.json`、`generated_q1r2_dt_pilot/` 与 `reports/q1r2_dt_design/`：三层正式 `0.013 s` 轨迹和带余量的共同登记步长设计。
- `campaign_q1r2.json`、`generated_q1r2/` 与 `reports/q1r2_freeze/`：冻结的 Q1R2 六逻辑层、五唯一运行输入及运行前身份。
- `reports/q1r2/`：Q1R2 原始资格报告与空间门失败闭锁裁决；三层空间完整，时间中层主动停止，时间细层未启动。
- `analyse_q1r2_dt_pilot.py`：从三层稠密步长轨迹选择带设计余量的量化共同登记步长。
- `analyse_q1r2_early_stop.py`：验证完成的空间证据是否已足以失败闭锁，并区分主动停止与数值故障。
- `campaign_q1r3.json`、`generated_q1r3/` 与 `reports/q1r3_freeze/`：开边界质量语义拆分后冻结的 Q1R3 输入、冻结身份和“空间先裁决”的早停协议。
- `reports/q1r3_spatial_stage/`：Q1R3 三层空间归档的原始通用分析与绑定冻结身份的空间阶段失败闭锁裁决；时间中、细层未启动。
- `analyse_q1r3_spatial_stage.py`：核验冻结报告、生成清单、空间归档载荷和原始资格报告，形成空间阶段 PASS/FAIL，并在失败后执行不改变冻结结论的窗口与资源诊断。
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

Q1R2 也已失败闭锁。其冻结输入仍可用 `generate` 逐字节再生，但本地 `runs/q1r2/time_L1_3.000e-06s` 是主动中断后的非完整诊断目录，不能被 `--resume` 当作完成结果；不得删除或绕过它后声称原冻结 Q1R2 连续通过。若未来明确批准额外时间诊断，应使用独立输出目录并保持与本次失败证据分离。

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

Q1R2 的原始报告和失败闭锁裁决可分别重建为：

```bash
"90_候选工作区/current/01_HF3D数值资格/.venv-gpu/bin/python" \
  "90_候选工作区/current/01_HF3D数值资格/hfq.py" \
  analyse \
  "90_候选工作区/current/01_HF3D数值资格/generated_q1r2/cases_manifest.json" \
  "90_候选工作区/current/01_HF3D数值资格/runs/q1r2" \
  "90_候选工作区/current/01_HF3D数值资格/reports/q1r2"

python3 \
  "90_候选工作区/current/01_HF3D数值资格/analyse_q1r2_early_stop.py" \
  "90_候选工作区/current/01_HF3D数值资格/reports/q1r2/qualification_report.json" \
  --expected-source-sha256 \
  03ebc4dcd194d0ffadc7f24228720d775dc7d888ceb5a9737816368626482010 \
  --json-output \
  "90_候选工作区/current/01_HF3D数值资格/reports/q1r2/early_stop_decision.json" \
  --markdown-output \
  "90_候选工作区/current/01_HF3D数值资格/reports/q1r2/early_stop_decision.md"
```

Q1R3 已按冻结协议先完成三层空间运行。以下第一条通用分析命令会因为时间中、细层按协议尚未运行而返回非零，并写出顶层 `INCOMPLETE_MISSING_OR_UNREADABLE_RUNS`；这不等于空间组尚未形成结果。第二条命令只读取已完整形成的空间组并核验全部绑定证据，最终状态为 `Q1R3_SPATIAL_STAGE_FAIL_CLOSED__TEMPORAL_NOT_AUTHORIZED`。

```bash
"90_候选工作区/current/01_HF3D数值资格/.venv-gpu/bin/python" \
  "90_候选工作区/current/01_HF3D数值资格/hfq.py" \
  analyse \
  "90_候选工作区/current/01_HF3D数值资格/generated_q1r3/cases_manifest.json" \
  "90_候选工作区/current/01_HF3D数值资格/runs/q1r3" \
  "90_候选工作区/current/01_HF3D数值资格/reports/q1r3_spatial_stage"

"90_候选工作区/current/01_HF3D数值资格/.venv-gpu/bin/python" \
  "90_候选工作区/current/01_HF3D数值资格/analyse_q1r3_spatial_stage.py" \
  --freeze-report \
  "90_候选工作区/current/01_HF3D数值资格/reports/q1r3_freeze/freeze_manifest.json" \
  --campaign-manifest \
  "90_候选工作区/current/01_HF3D数值资格/generated_q1r3/cases_manifest.json" \
  --qualification-report \
  "90_候选工作区/current/01_HF3D数值资格/reports/q1r3_spatial_stage/qualification_report.json" \
  --run-root \
  "90_候选工作区/current/01_HF3D数值资格/runs/q1r3" \
  --expected-source-sha256 \
  03ebc4dcd194d0ffadc7f24228720d775dc7d888ceb5a9737816368626482010 \
  --json-output \
  "90_候选工作区/current/01_HF3D数值资格/reports/q1r3_spatial_stage/spatial_stage_decision.json" \
  --markdown-output \
  "90_候选工作区/current/01_HF3D数值资格/reports/q1r3_spatial_stage/spatial_stage_decision.md"
```

## Q1/Q1R/Q1R2/Q1R3 的严格边界

原 Q1 已因细化设计门失败而作废，不能把其中任何层间差异解释为空间或时间收敛。Q1R 的静态设计门通过，但真实运行没有通过：粗层完整结束且单例质量门通过；中层完整结束但最大投影散度为 $5.807218\times10^{-4}\ \mathrm{s}^{-1}$，超过候选上限 $1.0\times10^{-4}\ \mathrm{s}^{-1}$；细层在约 60.7% 处因源项后对流 CFL 为 0.3504、超过冻结上限 0.3500 而失败闭锁；两个更细时间层未运行。因此 Q1R 当前状态是 `FAIL_PROVENANCE_INPUT_OR_COMPLETION_GATE`，并同时存在独立数值质量失败，不能据此声称空间或时间收敛。

Q1R 失败已经完成候选层定位：中网格共有 212 个稠密步超过绝对散度门，峰值为第 2228 步；正式相对压力残差门不保证候选绝对散度门。只把压力相对容差从 $10^{-6}$ 收紧为 $10^{-7}$ 的中网格完整 CUDA 重放在同一峰值步把最大散度降至 $5.018070\times10^{-5}\ \mathrm{s}^{-1}$，零步超门，并通过全部 Q1R 单例质量门。细网格则证明正式时间步控制没有把选步目标与源项后硬门分开，且失败档案缺少前 CFL 与逐算子增量，不能唯一归因。详细证据见 `reports/q1r_diagnosis/diagnosis_report.*`。

事务性 CFL 控制器已经提升到正式程序。随后从提升后正式路径重新执行 5 项 CUDA 测试和真实细网格 `0.013 s` 窗口：1757 个接受步，投影后最大 CFL 为 0.336906，最大投影散度为 $2.490918\times10^{-5}\ \mathrm{s}^{-1}$，冻结分析器 10/10 门通过。该证据关闭了“只在隔离候选源码执行”的实现级缺口，但仍不是收敛或物理验证。

三层正式 `0.013 s` 轨迹在 `0.1 µs` 量化网格上给出带 `0.02` 比例余量的共同登记步长 `6.0e-6 s`；Q1R2 因而冻结为空间组共同 `6.0e-6 s`、时间组 `6.0e-6→3.0e-6→1.5e-6 s`、压力相对容差 `1e-7`、实际 CUDA 无回退和统一 `0.02 s`。冻结输入可以独立再生成且物理签名一致。

Q1R2 三层空间算例均已完整执行。粗、中层登记步长命中率均为 0.994012；精细层为 0.938264，低于冻结门 0.95，所以空间实际时间步身份门失败。精细层登记的 `liquid_mass_error_relative` 为 0.00132108，也超过冻结上限 0.001；但正式实现把该量定义为相对初始液体体积的净变化，包含规定的开边界通量，而按开边界通量修正的 `liquid_volume_balance_error_relative` 只有 $8.60575\times10^{-12}$。因此这项仍是不能事后删除的冻结门失败，却不能单独解释为求解器数值不守恒。

在上述两个空间门已经不可补救地失败后，时间中层在 110 步主动中断并留下 `state=cancelling`、非完整但载荷哈希和配置身份有效的诊断目录，时间细层未启动。冻结分析器原始顶层状态为 `FAIL_PROVENANCE_INPUT_OR_COMPLETION_GATE`；补充裁决状态为 `Q1R2_FAIL_CLOSED_AFTER_COMPLETED_SPATIAL_GATES__TEMPORAL_NOT_COMPLETED`。完整 `0.02 s` 轨迹对下一轮步长的只读投影暂给 `5.5e-6 s`，相邻 `5.6e-6 s` 未达到 0.97 设计比例；该结果不是 Q1R3 冻结值，改变步长后的真实轨迹与质量门语义仍须另行预注册和验证。

Q1R3 先把 `liquid_volume_balance_error_relative` 保留为开边界通量修正后的数值守恒硬门，并把旧归档名 `liquid_mass_error_relative` 明确重分类为规范语义 `liquid_inventory_change_relative`：后者没有未经物理标定的绝对硬门，但仍是必需空间/时间平滑量。冻结前细网格 CUDA 探针通过后，Q1R3 绑定共同空间登记步长 `5.5e-6 s` 和 `4/6/9` 格/磁子直径路径，并规定空间任一门失败即不得运行时间新增算例。

冻结后三层空间归档的配置门、实际时间步身份门、逐算例数值质量门和二值事件一致性门均通过；登记步长命中率依次为 0.994505、0.994505 和 0.957808，三个二值事件均为 `NOT_EXERCISED`。但六个必需平滑量全部未通过空间收敛门：两最细层相对 $L^2$ 误差从 `min_gap_to_bar_m` 的 0.00420 到 `funnel_depth_m` 的 1.48397 不等，多项没有正观测阶，其余 GCI 远超 0.05。空间阶段因而失败闭锁，时间中层 `2.75e-6 s` 和细层 `1.375e-6 s` 均未启动，时间收敛状态为 `NOT_EVALUATED`。

失败后的只读窗口诊断把加速段和三个恒定 `300 rpm` 子窗分别重算，四个窗口均为 0/6 平滑量达到冻结阈值，因此失败并不只来自加速启动段。液体净存量变化在粗层未达到 $10^{-6}$，在中、细层分别于 `0.011 s` 和 `0.009 s` 达到该量级，而三层通量修正守恒残差仍约为 $10^{-11}$；这支持开边界存量响应存在强网格依赖，不支持数值不守恒。现有三层仍不能唯一分离磁子/Brinkman 分辨率、弥散界面与边界层正则化、开边界域响应、短时协议或连续模型缺项。

若延续 1.5 细化比，向上平移一级的 `6/9/13.5` 路径至少要运行 7490880 单元的 `13.5` 格层，完全更细的 `9/13.5/20.25` 路径还要运行 24952320 单元的 `20.25` 格层。两层的静态毛细步上限都低于 Q1R3 的共同 `5.5e-6 s`，所以旧 `6/9` 归档不能与新增 `13.5` 层直接组成有效空间组；选择同模型路径也必须冻结更小共同步长并整组重跑。资源可行性和科学路径必须先由人工裁决，再另行预注册 Q1R4。

Q1R、Q1R2 的时长和分辨率都只用于早期瞬态筛查，不覆盖统计稳态，不足以给出漏斗稳态深度、首次触杆转速、稳定气道半径或结构性分岔。只有后续早期筛查先通过，才可另行冻结平滑区 Q2 和近拓扑区 Q2；不得把任何数值状态直接升级为物理预测通过。

## 测试

```bash
cd "90_候选工作区/current/01_HF3D数值资格"
python3 -Werror -m unittest discover -s tests -v
```

当前候选测试共 35 项；本机 CUDA 环境的正式全量测试为 181/181 通过，正式 self-check 为 6/6 通过。候选测试只验证资格算法、归档诊断、步长设计和失败闭锁关系；CUDA 终态单步重放也只验证压力容差机制。正式路径窗口与 Q1R3 三层空间运行提供了更强的实现级和单例证据，但 Q1R3 已因六个平滑量空间不收敛而失败，仍未取得多网格、多时间步收敛或数值资格。
