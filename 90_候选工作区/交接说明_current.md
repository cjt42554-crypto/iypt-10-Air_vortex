# 当前交接

## 当前状态

D1 候选区 G0/G1 成对锁步动态影子对照已经按 Pro 预注册完成，冻结状态为：

```text
D1_DYNAMIC_SHADOW_COMPLETE__GEOMETRY_EFFECT_SMALL_IN_REGISTERED_WINDOW__STOP_FOR_PRO_REVIEW
```

当前等待 Pro 复核。报告后停止点已经到达，不得自行继续动态算例、改变协议或正式提升 G1。

## 复核入口

- 人类可读报告：`90_候选工作区/current/05_D1动态影子对照/reports/d1_dynamic_shadow_report.md`；
- 机器可读报告：`90_候选工作区/current/05_D1动态影子对照/reports/d1_dynamic_shadow_report.json`；
- 本地原始证据清单：`90_候选工作区/current/05_D1动态影子对照/artifacts/d1_dynamic_shadow_manifest.json`；
- 冻结科学裁决：`90_候选工作区/current/05_D1动态影子对照科学裁决与预注册.md`；
- 机器可读合同：`90_候选工作区/current/05_D1动态影子对照/d1_dynamic_shadow_preregistration.json`。

## 执行身份与阶段门

- Pro 裁决由远端提交 `8829b5f` 拉入；候选实现和所有结果承载运行绑定提交 `68ef0dcef63a652eda348b8439ef62c13a812642`；
- 正式源码树在各完整窗口前后均为 `dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8`；
- S0/S1：名义 `4/6/9` 层 G0 静态往返、初态及 `4` 格层 CPU/CUDA 八步同一性通过；
- S2：`4` 格层 G0/G1 CUDA 八步烟雾测试通过；
- S3：`6` 格层 G0/G1 完成 `0.010 s`、`1820` 个共同接受步、`0` 次重试，两侧全部质量门通过；
- S4：`9` 格层 G0/G1 完成 `0.010 s`、`1820` 个共同接受步、`0` 次重试，两侧全部质量门通过；
- `6/9` 两层的共同接受时间 SHA-256 均为 `469efa2a729a3cfeda808f1aed082b7e17f3b33d4c43b0cbf87551f8a23b931a`；
- CUDA 设备为 NVIDIA GeForce RTX 5060 Laptop GPU，未回退到 CPU；
- S5 独立分析器重新加载并校验归档后，全部九项阶段门通过。

第一次 `9` 格层尝试在 `t=0.007 s` 被外部会话中断，没有生成 `level_9` 结果文件，也未进入分析。随后以相同提交和配置从零完整重跑；清单只纳入完整重跑产物。

## 冻结几何归因结论

四个主要观测量的直接物质性、极端变化和误差削减信号计数均为 `0`，全部满足预注册的小窗口效应判据。全窗口主要指标为：

| 观测量 | max D | max eta | rho |
|---|---:|---:|---:|
| funnel_depth_m | 0.0258418 | 0.0258418 | 0.976337 |
| kinetic_energy_lab_j | 5.16091e-05 | 0.000153855 | 0.999846 |
| angular_momentum_z_kg_m2_s | 2.04859e-05 | 7.25894e-05 | 0.999931 |
| axisymmetry_error | 1.17405e-05 | 2.0974e-05 | 1.00002 |

因此冻结标签为：

```text
GEOMETRY_EFFECT_SMALL_IN_REGISTERED_WINDOW
```

它只支持“在登记短窗口内，G0/G1 布尔几何差异对既有失败形态的影响较小”。它不证明 G1 正确、收敛或更接近实验，也不证明 Q1R3 失败的唯一原因。

## 原始证据边界

本地忽略的原始产物共 `34` 个文件、`300460363` 字节；跟踪清单记录路径、大小和 SHA-256。该清单只能检测相对当前清单的意外变更，不是外部签名。原始大文件未纳入 Git。

## 禁止越过的边界

- 不得继续 `13.5/20.25` 格、D0-B、D0-C、Q1R3 时间组或完整 Q1R4；
- 不得撤销 Q1R3 既有失败闭锁；
- 不得把影子对照写成空间或时间收敛；
- 不得把 G1 提升到正式求解器；
- 不得宣称物理验证、实验验证、临界转速、长期稳态或装置预测。

当前结论上限为：

```text
Q1R3_SPATIAL_STAGE_FAIL_CLOSED__TEMPORAL_NOT_AUTHORIZED
Q1R4_D0_FAIL_CLOSED_AT_STATIC_GEOMETRY
D1_DYNAMIC_SHADOW_COMPLETE__GEOMETRY_EFFECT_SMALL_IN_REGISTERED_WINDOW__STOP_FOR_PRO_REVIEW
NUMERICAL_QUALIFICATION = NOT_DEMONSTRATED
PHYSICAL_VALIDATION = NOT_PERFORMED
EXPERIMENTAL_VALIDATION = NOT_PERFORMED
APPARATUS_PREDICTION = DISALLOWED
```
