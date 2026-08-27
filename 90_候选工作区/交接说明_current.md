# 当前交接

## 当前唯一待续任务

Q1R4 D0 已在静态几何第一门失败闭锁：

```text
Q1R4_D0_FAIL_CLOSED_AT_STATIC_GEOMETRY
```

当前必须由人工或 Pro 裁决下一条科学路径。不得在现有协议下继续 D0-B、D0-C，不得事后放宽已登记几何阈值，也不得冻结或运行完整 Q1R4。

可供裁决的方向只有：

1. 接受当前停止结论，先设计更可控的磁子几何离散或渐近模型；这可能涉及正式程序身份变化，必须另行明确授权和预注册；
2. 认为当前绝对质心阈值不适合作为 D0-A 硬门，则只能建立一个前瞻的新诊断协议重新执行，不能把本次失败追认成通过；
3. 终止当前 HF3D 加密资格路径。

## D0-A 实际证据

静态审计在清洁 `main` 提交 `73d3b6cf485b969c2531966a29922a6f5029fe90` 上执行，正式源码树前后均为 `dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8`，动态求解步数为 `0`。

四层正式 `ImmersedCartesianGrid` 均成功构造。关键归一化几何量为：

- `6` 格层质心误差/磁子半径：`0.0606995164`，超过预注册上限 `0.05`；
- `9` 格层：`0.0173906578`；
- `13.5` 格层：`0.0122279384`；
- `6→9` 最大归一化误差跳变：`0.0433088586`，未超过 `0.05`；
- `9→13.5` 最大跳变：`0.00826571839`，未超过 `0.05`；
- 磁子体积和二阶矩门均通过。

因此只是“体素几何归因尚未被本协议限制住”，不是“已证明体素几何导致 Q1R3 失败”。

证据入口：

- `90_候选工作区/current/03_Q1R4诊断门/reports/static_geometry_audit.json`；
- `90_候选工作区/current/03_Q1R4诊断门/reports/static_geometry_audit.md`；
- `90_候选工作区/current/03_Q1R4诊断门/reports/d0_diagnostic_decision.json`；
- `90_候选工作区/current/03_Q1R4诊断门/reports/d0_diagnostic_decision.md`。

## 已执行与未执行

已执行：

- D0 工具、四份配置和失败闭锁分析器实现；
- D0 候选测试 18/18；
- 正式完整测试 187/187，其中 5 项 CUDA 测试实际执行；
- 既有 Q1 至 Q1R3 候选测试 35/35；
- D0-A 四层静态几何审计。

因 D0-A 先失败而未执行：

- `204 × 204 × 180` 八步 CUDA 资源探针；
- `6/9` 零驱动诊断；
- `9` 格固定物理宽度对照；
- 完整 Q1R4、Q1R3 时间组、物理校准和实验验证。

`campaign_q1r4.json` 和任何 Q1R4 冻结清单均未建立。

## 正式目录与结论上限

`01_最终理论/**` 与 `02_模拟程序/**` 未修改；Q1 至 Q1R3 的冻结证据和本地大型归档未修改。

当前状态继续为：

```text
Q1R3_SPATIAL_STAGE_FAIL_CLOSED__TEMPORAL_NOT_AUTHORIZED
Q1R4_D0_FAIL_CLOSED_AT_STATIC_GEOMETRY
NUMERICAL_QUALIFICATION = NOT_DEMONSTRATED
PHYSICAL_VALIDATION = NOT_PERFORMED
EXPERIMENTAL_VALIDATION = NOT_PERFORMED
APPARATUS_PREDICTION = DISALLOWED
```
