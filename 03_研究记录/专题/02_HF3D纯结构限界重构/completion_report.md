# HF3D 纯结构限界重构完成报告

结论：`PASS_HF3D_BOUNDED_STRUCTURAL_REFACTOR__CPU_CUDA_STRICT_EQUIVALENCE`。

本次获准的结构重构已经完成。CPU 与 CUDA 固定短重放相对重构前基线均达到 88/88 数组逐元素完全相同；status、事件及 payload、键与顺序、shape、dtype、单位、数值 metadata、Progress 序列、逐步诊断和最终完整三维场也完全相同。没有用容差放宽、门槛修改或整段 metadata 豁免取得通过。

## 实际结构变化

正式兼容入口 `02_模拟程序/src/airvortex/backends/high_fidelity_3d.py` 从 2054 行降为 657 行，继续提供原有 `HighFidelityBackend`、类型和私有兼容导入。内部职责拆分为：

- `hf3d/types.py`：状态、诊断、异常和 limiter 稳定编码；
- `hf3d/drive.py`：实际转速、右斜率与角度精确积分；
- `hf3d/pressure.py`：CPU/CUDA 选择及禁止中途静默回退；
- `hf3d/timestep.py`：有类型的时间步提案与 CFL retry；
- `hf3d/stepper.py`：唯一活动的事务式 trial step；
- `hf3d/events.py`：拓扑事件状态机；
- `hf3d/recording.py`：帧、逐步诊断、alias、单位和完整结果组装。

兼容入口只保留配置验证、网格与初态构造、运行编排、Control、Progress、retry 编排和兼容转发。结构测试明确禁止入口内出现第二套活动 stepper，并检查旧导入仍指向同一合同对象。

## 身份与严格等价

- 重构前捕获 HEAD：`ce96932b5a122fa284922bebc84db9df92ef6c36`；
- 重构前正式源码树：`03ebc4dcd194d0ffadc7f24228720d775dc7d888ceb5a9737816368626482010`；
- 重构实现捕获 HEAD：`f9ba2fc648b3495c8144ec292ecf35e18d36201b`；
- 重构后正式源码树：`dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8`。

CPU 的重构前后数组归档 SHA-256 均为 `0fd77a759b064d16cb229b437568e3a669a3fd5383b88812fa2bd24a6da90baf`。CUDA 的重构前后数组归档 SHA-256 均为 `3902967478be3e2586b386fe6d92db2887d5b6c323f54575781d0328f78e1250`。

允许差异白名单只有：捕获标签、数组归档文件名、Git HEAD 和正式源码树身份。两份比较的 `first_difference` 均为 `null`。机读和人读证据见 `evidence/equivalence_cpu.*` 与 `evidence/equivalence_cuda.*`。

## 最终验证

- 正式完整测试：187/187 通过，其中 5 项 CUDA 测试实际执行；
- 正式 self-check：6/6 通过；
- 正式源码与测试 `compileall`：通过；
- Q1 至 Q1R3 候选资格测试：35/35 通过；
- 比较器缺键、shape、dtype、事件和单元素差异失败闭锁测试：5/5 通过；
- CPU 与 CUDA 严格比较：分别 88/88 数组通过；
- 未用导入检查、Git 空白检查、严格 JSON 和 Pandoc Markdown 解析：通过。

完整命令与退出码见 `completion_report.json`。

## 保护边界与结论上限

`01_最终理论/**` 未修改。Q1、Q1R、Q1R2、Q1R3 的冻结配置、报告、裁决器、历史补丁、归档登记身份和本地大型归档均未修改；旧 Q1R3 证据继续绑定重构前源码树，不得重绑定到新源码。

本次没有重算 Q1R3，没有冻结、启动或补跑 Q1R4，也没有执行新的网格/时间收敛、物理校准或实验验证。因此：

- Q1R3 仍为 `Q1R3_SPATIAL_STAGE_FAIL_CLOSED__TEMPORAL_NOT_AUTHORIZED`；
- 完整数值资格仍为 `NO-GO`；
- 装置级物理预测仍为 `NO-GO`。

下一步仍需对 Q1R4 的科学路径和资源协议作单独裁决；本报告不授权自动运行。
