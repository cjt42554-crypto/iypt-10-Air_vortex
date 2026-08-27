# 当前交接

## 当前唯一待续任务

HF3D 纯结构限界重构已经完成并通过 CPU/CUDA 严格等价验收。当前没有待续的重构实现工作。

下一项仍是 Q1R4 的人工科学与资源裁决；在当前用户明确选择路径并批准新预注册前，不得自动冻结、启动、补跑或拼接任何 Q1R4 算例。较低成本且保持当前模型身份的优先候选仍是整组重跑 6/9/13.5 格/磁子直径，但必须先独立完成资源探针和更小共同登记步长设计，旧 Q1R3 的 6/9 层不得复用为新三层中的前两层。

## 本轮完成状态

结论为：

`PASS_HF3D_BOUNDED_STRUCTURAL_REFACTOR__CPU_CUDA_STRICT_EQUIVALENCE`

正式兼容入口 `02_模拟程序/src/airvortex/backends/high_fidelity_3d.py` 从 2054 行降为 657 行，继续保留旧导入和 `HighFidelityBackend`。唯一活动实现按职责拆入 `02_模拟程序/src/airvortex/hf3d/`：

- `types.py`：状态、诊断、异常和 limiter 编码；
- `drive.py`：实际转速、右斜率和角度积分；
- `pressure.py`：CPU/CUDA 选择和禁止中途静默回退；
- `timestep.py`：有类型的时间步提案和 retry；
- `stepper.py`：事务式 trial step；
- `events.py`：拓扑事件状态机；
- `recording.py`：帧、逐步诊断、alias、单位和结果组装。

没有保留旧、新两套活动 stepper、recorder 或 event tracker；没有 monkey patch、运行时 fallback 或临时功能开关。

## 身份与等价证据

- 重构前捕获 HEAD：`ce96932b5a122fa284922bebc84db9df92ef6c36`；
- 重构前正式源码树：`03ebc4dcd194d0ffadc7f24228720d775dc7d888ceb5a9737816368626482010`；
- 重构实现捕获 HEAD：`f9ba2fc648b3495c8144ec292ecf35e18d36201b`；
- 重构后正式源码树：`dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8`；
- CPU 严格比较：88/88 数组逐元素相同；
- CUDA 严格比较：88/88 数组逐元素相同；
- CPU 重构前后 NPZ SHA-256 同为 `0fd77a759b064d16cb229b437568e3a669a3fd5383b88812fa2bd24a6da90baf`；
- CUDA 重构前后 NPZ SHA-256 同为 `3902967478be3e2586b386fe6d92db2887d5b6c323f54575781d0328f78e1250`。

允许差异只有捕获标签、数组归档文件名、Git HEAD 和正式源码树身份。status、事件及 payload、键与顺序、shape、dtype、全部数组、单位、数值 metadata、Progress、逐步诊断和最终完整三维场均相同。

必要入口：

- `90_候选工作区/current/02_HF3D纯结构限界重构/completion_report.json`；
- `90_候选工作区/current/02_HF3D纯结构限界重构/completion_report.md`；
- `90_候选工作区/current/02_HF3D纯结构限界重构/evidence/equivalence_cpu.*`；
- `90_候选工作区/current/02_HF3D纯结构限界重构/evidence/equivalence_cuda.*`。

## 最终验证

- 正式完整测试 187/187 通过，其中 5 项 CUDA 测试实际执行；
- 正式 self-check 6/6 通过；
- Q1 至 Q1R3 候选资格测试 35/35 通过；
- 比较器失败闭锁测试 5/5 通过；
- `compileall`、严格 JSON、Pandoc Markdown、未用导入和 Git 空白检查通过。

## 科学与保护边界

`01_最终理论/**` 未修改。Q1、Q1R、Q1R2、Q1R3 的冻结配置、报告、裁决器、历史补丁、证据身份和本地大型归档均未修改。旧 Q1R3 证据继续绑定重构前源码树，不能改绑到新源码。

Q1R3 仍为：

`Q1R3_SPATIAL_STAGE_FAIL_CLOSED__TEMPORAL_NOT_AUTHORIZED`

完整数值资格、物理校准、实验验证和装置级预测仍为 `NO-GO` 或 `NOT_EXECUTED`。本轮没有冻结或运行 Q1R4。
