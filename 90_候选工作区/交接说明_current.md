# 当前交接

## 当前唯一待续任务

按最新 Pro 裁决，在候选工作区实现并执行 D1 可控磁子几何离散的静态阶段：

```text
Q1R4_D0_V1_FAIL_UPHELD__D1_CONTROLLED_BAR_DISCRETISATION_SELECTED
D1_STATIC_ONLY__FORMAL_PROMOTION_NOT_AUTHORIZED
```

任务入口：

- `90_候选工作区/current/04_D0A后续科学裁决与D1设计边界.md`；
- `90_候选工作区/current/04_D1可控磁子离散/README.md`；
- `90_候选工作区/current/04_D1可控磁子离散/d1_geometry_preregistration.json`。

## 为什么选择 D1

D0-A 的 `6` 格层质心门确实失败，不能事后放宽或追认通过。进一步源码审查表明，当前磁子掩膜由“解析体积四舍五入到固定单元数，再按单元中心符号距离稳定排序截断”生成。该规则控制体积，却把并列项数组顺序和浮点表示转化为有效几何。

按当前源码独立静态复算，`90 × 90 × 80` 层的最终距离截断处有 `48` 个精确并列单元而只剩 `8` 个名额；磁子中心高度只改变一个 IEEE-754 ULP，`792` 个磁子单元中预计有 `80` 个发生对称差，轴向质心偏移符号翻转。该病态性足以阻止继续昂贵的 Q1R4，但尚不能证明它是 Q1R3 动态不收敛的唯一原因。

## Codex 执行顺序

### A0：只读复现

直接导入正式 `ImmersedCartesianGrid`，不得复制正式算法。复现：

1. 既有 D0-A 四层报告；
2. `6` 格层 `792` 单元、质心向量和截止并列数；
3. $z_b$ 上下各一 ULP 的掩膜哈希、对称差、体积、质心和二阶矩；
4. C 顺序、反向顺序及固定随机排列下的并列选择敏感性。

若与既有报告或上位裁决不一致，失败闭锁并停止。

### A1：候选 G1

只在本候选目录实现：

```text
G1_SYMMETRY_MOMENT_CONSTRAINED_BOOLEAN_MASK
```

必须遵守机器合同中的解析边界带、$x/y$ 镜像共享变量、硬几何门、字典序目标、确定性和禁止项。不得修改正式程序。

### A2：静态参考 G2

实现：

```text
G2_ADAPTIVE_SUBCELL_CAPSULE_REFERENCE
```

它只提供自带误差区间的静态体积分数、质心和二阶矩参考，不得接入动态求解器或冒充正式 cut-cell。

### A3：审计并停止

执行 `4/6/9/13.5` 名义层及合同登记的 `6/9` 相位扫描，输出严格 JSON、Markdown、掩膜哈希、连通性、G1/G2 差异和全部门。

静态报告完成后立即停止并更新本交接；不得自行进入动态影子对照、正式提升、D0-B、D0-C 或完整 Q1R4。

## 正式目录与历史证据

本次 Pro 裁决未修改 `01_最终理论/**` 或 `02_模拟程序/**`，未修改 Q1 至 Q1R3 冻结证据，未执行新测试或动态算例。

旧结论保持：

```text
Q1R3_SPATIAL_STAGE_FAIL_CLOSED__TEMPORAL_NOT_AUTHORIZED
Q1R4_D0_FAIL_CLOSED_AT_STATIC_GEOMETRY
NUMERICAL_QUALIFICATION = NOT_DEMONSTRATED
PHYSICAL_VALIDATION = NOT_PERFORMED
EXPERIMENTAL_VALIDATION = NOT_PERFORMED
APPARATUS_PREDICTION = DISALLOWED
```
