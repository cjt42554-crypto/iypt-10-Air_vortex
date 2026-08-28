# 当前交接

## 当前状态

D1 可控磁子离散的已授权静态阶段已经完成，独立复核结论为：

```text
D1_STATIC_G1_PASS_CONFIRMED__STOP_FOR_PRO_REVIEW
```

当前没有获授权的继续计算任务。下一步只能把现有静态证据交给 Pro，裁决是否发出：

```text
REQUEST_PRO_REVIEW_FOR_CANDIDATE_ONLY_G0_VS_G1_DYNAMIC_SHADOW
```

该请求字符串本身不授权动态影子对照或正式提升。

## 任务入口与证据

- 上位科学裁决：`90_候选工作区/current/04_D0A后续科学裁决与D1设计边界.md`；
- D1 状态、复现命令和结论边界：`90_候选工作区/current/04_D1可控磁子离散/README.md`；
- 机器可读合同：`90_候选工作区/current/04_D1可控磁子离散/d1_geometry_preregistration.json`；
- A0 正式 G0 复现：`reports/a0_g0_conditioning.json` 和 `reports/a0_g0_conditioning.md`；
- 完整静态审计：`reports/d1_static_audit.json` 和 `reports/d1_static_audit.md`；
- 独立合同复核：`reports/d1_static_analysis.json` 和 `reports/d1_static_analysis.md`。

最后三个 `reports/` 相对路径均相对于 D1 目录。最终审计从清洁 `main` 提交 `242bb3da6a303b4c69b11bc42b1790b27dd618d6` 启动，审计前后正式源码树身份均为 `dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8`。

## 已完成结果

- A0 直接导入正式 `ImmersedCartesianGrid`，复现 D0-A 四层结果和 `6` 格层并列截断病态；
- G1 名义 `4/6/9/13.5` 层全部通过静态硬门；
- `6/9` 每层 27 个、合计 54 个登记相位算例全部通过，最坏质心误差分别为 `0.04972436` 和 `0.02629424`；
- 全部 58 个静态算例均为单一六连通分量，枚举顺序和 ULP 不变性门通过；
- G2 误差区间门通过；
- `6→9` 与 `9→13.5` 的登记误差跳变门通过；
- 全部动态步数为 `0`。

必须保留的未裁决风险：G1–G2 加权对称差没有预注册 PASS 阈值，全部算例最大相对上界为 `0.214969991`，四个名义层的相对区间上界依次约为 `0.1910331`、`0.1684747`、`0.1304297` 和 `0.0811327`。静态总门通过不表示 Pro 已接受该差异。

## 禁止越过的边界

- 不得自行运行候选 G0/G1 动态影子对照；
- 不得继续 D0-B、D0-C、完整 Q1R4 或 Q1R3 时间组；
- 不得修改或提升到 `01_最终理论/**`、`02_模拟程序/**`；
- 不得把候选静态门通过写成数值收敛、物理验证、实验验证或装置预测。

当前结论上限仍为：

```text
Q1R3_SPATIAL_STAGE_FAIL_CLOSED__TEMPORAL_NOT_AUTHORIZED
Q1R4_D0_FAIL_CLOSED_AT_STATIC_GEOMETRY
D1_STATIC_G1_PASS_CONFIRMED__STOP_FOR_PRO_REVIEW
NUMERICAL_QUALIFICATION = NOT_DEMONSTRATED
PHYSICAL_VALIDATION = NOT_PERFORMED
EXPERIMENTAL_VALIDATION = NOT_PERFORMED
APPARATUS_PREDICTION = DISALLOWED
```
