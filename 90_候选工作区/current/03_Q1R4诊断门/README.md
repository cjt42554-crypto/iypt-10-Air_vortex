# Q1R4 D0 诊断门接续任务

目标仓库：`cjt42554-crypto/iypt-10-Air_vortex`

工作分支：仅 `main`

基准提交：先确认 `HEAD` 不早于 `9e39944c1e12052dafcfc6a2c48f2034ef37e576`，并读取仓库顶层 `AGENTS.md` 与 `90_候选工作区/交接说明_current.md`。

## 任务结论

不要启动完整 Q1R4。只实现并执行候选工作区内的 D0 诊断门：

```text
Q1R4_NOT_FROZEN__D0_DIAGNOSTIC_GATE_SELECTED
```

正式目录 `01_最终理论/**` 和 `02_模拟程序/**` 只读。除非为了修复候选工具无法调用的明确正式缺陷，否则不得修改；若发现此类缺陷，先停止并在报告中说明，不自行修改。

## 必须新增

```text
90_候选工作区/current/03_Q1R4科学与资源裁决.md
90_候选工作区/current/03_Q1R4诊断门/README.md
90_候选工作区/current/03_Q1R4诊断门/q1r4_diagnostic_preregistration.json
90_候选工作区/current/03_Q1R4诊断门/audit_q1r4_geometry.py
90_候选工作区/current/03_Q1R4诊断门/probe_q1r4_resource.py
90_候选工作区/current/03_Q1R4诊断门/analyse_q1r4_diagnostics.py
90_候选工作区/current/03_Q1R4诊断门/tests/test_q1r4_diagnostic_gate.py
```

裁决正文位于 `../03_Q1R4科学与资源裁决.md`，机器可读合同为本目录的 `q1r4_diagnostic_preregistration.json`。实现和报告必须保持与这两份已提交输入的一致性。

## A. 静态几何审计

使用正式 `HighFidelityBackend.make_grid()`，对以下网格只做构造和几何计算，不运行流体步骤：

```text
60 × 60 × 54
90 × 90 × 80
136 × 136 × 120
204 × 204 × 180
```

输出严格 JSON 与 Markdown。至少包含三向间距、活动单元数、精确和体素磁子体积、相对体积误差、体素质心、质心误差、关于真实磁子中心的二阶矩和活动流体体积误差。

不得把几何误差门写成物理预测门。

## B. `13.5` 格 CUDA 资源探针

只允许：

```text
grid = 204 × 204 × 180
candidate registered dt = 3.75e-6 s
accepted steps = 8
pressure_solver_device = cuda
```

`3.75e-6 s` 只是探针候选步长，不是 Q1R4 冻结值。

必须直接调用当前正式网格、状态初始化、CUDA 压力投影、`propose_time_step()`、事务式
`advance_one_step()` 和正式 CFL 重试函数。禁止复制一套简化求解器。禁止 CUDA 回退。

持续采样并输出：

- 进程 RSS 基线与峰值；
- 主机可用内存基线与最低值；
- CUDA 总量、空闲量和本进程运行期间的峰值增量；
- CuPy 默认内存池的 `used_bytes()` 与 `total_bytes()`；
- 网格、状态、压力投影各阶段构造时间；
- 八个已接受步骤的墙钟；
- 压力迭代、残差、投影散度、CFL、重试；
- `git HEAD`、工作树清洁状态、正式源码内容树身份；
- 实际压力加速器身份及 fallback 字段。

任一身份、资源可审计性、完成性或数值质量门失败时，报告必须失败闭锁。

探针通过的最高结论：

```text
Q1R4_D0_RESOURCE_PROBE_PASS__SOURCE_SEPARATION_ALLOWED
```

不得写成 Q1R4 可运行、数值收敛或物理通过。

## C. 来源解耦

先生成预注册和分析器测试，再运行任何诊断。

最小诊断包含：

1. `6/9` 零驱动相关分辨率诊断；
2. `9` 格固定物理宽度对照，物理宽度取 `6` 格基准：
   - `interface_thickness_cells = 3.7777777777777777`
   - `boundary_layer_cells = 2.2666666666666666`
   - `surface_tension_smoothing_cells = 1.1333333333333333`
3. 分开报告液体净存量变化与开放边界通量校正守恒；
4. 对核心平滑量计算 `D_cell`、`D_reg` 和 `eta_reg`；
5. 将静态体素几何变化与动态量差异并列，不强行做因果声称。

固定物理宽度对照必须标记 `DIAGNOSTIC_ONLY`，不得进入资格组。

运行窗口和输出频率应在预注册中给定并保持最小化。不要为了“看出趋势”事后延长窗口或挑选子区间。

本次可执行协议已经在机器合同中固定为：

- 公共窗口 `0–0.005 s`；
- 输出间隔 `0.001 s`；
- 六个核心平滑量沿用 Q1R3 的登记集合；
- `eta_reg ≥ 0.5` 或 `eta_zero_drive ≥ 0.5` 记为单量“同阶”；
- 至少两个核心量达到同阶即判为来源影响具有实质性；
- 旧 Q1R3 `6/9` 强迫归档只作为 `DIAGNOSTIC_ONLY` 固定格数路径参照，不能成为 Q1R4 资格证据。

执行顺序为：先运行合成失败闭锁测试并生成 `generated/` 中的四份候选配置；将实现、合同和配置提交后，在清洁 `main` 工作树上依次运行 D0-A、D0-B。只有 D0-A/B 均通过才执行 D0-C 的两个零驱动算例和一个 `9` 格固定物理宽度算例。原始运行与连续资源采样写入 Git 忽略的 `runs/`，分析完成后再把严格报告发布到 `reports/`。

## D. 停止条件

以下任一情况出现就停止，不创建 `campaign_q1r4.json`：

- `13.5` 构造或八步资源探针失败；
- CUDA 回退；
- 数值质量失败；
- 正则化影响对多个核心量达到与 `6→9` 差异同阶；
- 静态体素几何跳变足以使当前层间差异无法归因；
- 零驱动出现相关分辨率下的显著寄生流、自由面变形或液体净存量异常；
- 数据或源码身份不完整。

只有 D0 全部完成且分析器给出“资源通过、来源已受限”时，才起草一个新的 `6/9/13.5` 预注册。仍不得自动运行。

## 测试与检查

至少执行：

```bash
python -m unittest discover -s "90_候选工作区/current/03_Q1R4诊断门/tests" -p "test_*.py"
python -m compileall -q "90_候选工作区/current/03_Q1R4诊断门"
python -m json.tool "90_候选工作区/current/03_Q1R4诊断门/q1r4_diagnostic_preregistration.json" >/dev/null
pandoc -f markdown -t html "90_候选工作区/current/03_Q1R4科学与资源裁决.md" -o /tmp/q1r4_decision.html
git diff --check
```

若运行候选诊断依赖正式程序测试，再执行完整正式测试；不要用少量测试替代已有全量门。

## 交接与提交

完成后更新：

```text
90_候选工作区/交接说明_history.md
90_候选工作区/交接说明_current.md
```

`current` 只保留最新状态、结论上限和下一步，不复制全过程。所有修改在 `main` 提交并推送，不新建分支。

最终汇报必须列出：

- 修改路径；
- 正式目录是否改动；
- 实际运行了哪些诊断、哪些未运行；
- 测试结果；
- 资源探针结果；
- D0 的失败闭锁或下一步；
- Q1R4 是否仍未冻结；
- 装置预测为何仍不允许。
