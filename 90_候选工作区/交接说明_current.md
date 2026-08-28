# 当前交接

## 当前状态

Pro 已完成对 D1 静态证据、正式网格构造和 HF3D 运行路径的独立复核，裁决为：

```text
D1_CANDIDATE_ONLY_LOCKSTEP_DYNAMIC_SHADOW_AUTHORIZED__NOT_EXECUTED
```

授权的是候选区内的故障归因实验，不是数值资格或正式提升。动态步数仍为 `0`。

## 任务入口

下一任务由具备本地仓库和实际 CUDA 环境的执行者完成，入口为：

- 科学裁决与完整边界：`90_候选工作区/current/05_D1动态影子对照科学裁决与预注册.md`；
- 执行说明：`90_候选工作区/current/05_D1动态影子对照/README.md`；
- 机器可读合同：`90_候选工作区/current/05_D1动态影子对照/d1_dynamic_shadow_preregistration.json`。

## 关键复核结论

- D1 静态 PASS 与源码实现相符，未发现报告逻辑伪造；
- G1–G2 加权对称差仍未被阈值化接受；名义 `4/6/9/13.5` 层相对区间上界约为 `0.1910331/0.1684747/0.1304297/0.0811327`，全部相位最大值为 `0.214969991`；
- `6→13.5` 的缩放指标约 `0.90`，只支持“值得做动态诊断”，不支持 G1 正确或收敛；
- 禁止只在正式网格构造后替换 `bar_mask`。必须在投影与初态创建前重建 `active_mask`、开面、索引、最近固体距离/类型和壁面法向；
- G0 往返自同一性是任何 G1 动态步之前的硬门；
- G0/G1 必须由同一个成对锁步驱动器推进，禁止分别自适应运行后插值。

## 已授权工作

仅允许在新候选目录中：

1. 实现候选网格安装器、锁步驱动器、分析器和测试；
2. 完成名义 `4/6/9` 层 G0 静态自同一性；
3. 完成 `4` 格层 CPU/CUDA G0 八步同一性及 G0/G1 CUDA 八步烟雾测试；
4. 以 Q1R3 `6/9` 配置为基线，只把时长改为 `0.010 s`，依次运行锁步 G0/G1 对照；
5. 使用预注册四分类生成报告后停止，交 Pro 复核。

正式 `01_最终理论/**` 与 `02_模拟程序/**` 不得修改。

## 失败闭锁

以下任一情况发生即停止，不作几何物质性裁决：

- G0 自同一性失败；
- 证据、配置、掩膜或正式源码身份不匹配；
- CUDA 回退；
- 两侧接受时间不完全相同；
- 任一算例不完整或数值质量门失败；
- 使用未登记配置覆盖；
- 在 `6` 格层未通过全部门时启动 `9` 格层。

失败状态为：

```text
D1_DYNAMIC_SHADOW_INCONCLUSIVE_FAIL_CLOSED
```

## 禁止越过的边界

- 不得继续 `13.5/20.25` 格、D0-B、D0-C、Q1R3 时间组或完整 Q1R4；
- 不得撤销 Q1R3 既有失败闭锁；
- 不得把影子对照写成空间/时间收敛；
- 不得把 G1 提升到正式求解器；
- 不得宣称物理验证、实验验证、临界转速或装置预测。

当前结论上限仍为：

```text
Q1R3_SPATIAL_STAGE_FAIL_CLOSED__TEMPORAL_NOT_AUTHORIZED
Q1R4_D0_FAIL_CLOSED_AT_STATIC_GEOMETRY
D1_CANDIDATE_ONLY_LOCKSTEP_DYNAMIC_SHADOW_AUTHORIZED__NOT_EXECUTED
NUMERICAL_QUALIFICATION = NOT_DEMONSTRATED
PHYSICAL_VALIDATION = NOT_PERFORMED
EXPERIMENTAL_VALIDATION = NOT_PERFORMED
APPARATUS_PREDICTION = DISALLOWED
```
