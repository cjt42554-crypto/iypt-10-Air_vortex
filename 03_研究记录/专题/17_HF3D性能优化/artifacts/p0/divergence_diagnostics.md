# HF3D P0-D 结果盲散度诊断

状态：`P0_D_RESULT_BLIND_DIVERGENCE_DIAGNOSTICS_PASS`

本报告只描述冻结的逐接受步数值诊断。没有读取状态场、网格数组值、物理观测量或 D3 分类向量，不能形成 D3 科学分类、数值资格、物理验证或装置预测。

## 身份与访问边界

- 预注册提交：`246a4d5c365b022ad4bd2e930393bacdd59fef61`
- R1 执行提交：`be349aec2f1bdcdb232e282a11175921eda7976b`
- 正式源码树：`dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8`
- raw 文件：`40` 个，`359454291` 字节
- raw 规范 SHA-256：`906a32f71f3e095afc7cba6a3eddf7a83e4d639d36dc0b3014278b3e0001b032`
- `physical_observables_read = false`；`prohibited_numeric_values_loaded = false`。

## 六案例散度上界

| 案例 | 接受步 | 终止时刻 s | 最大散度 s^-1 | 门比 | 最大步 |
|---|---:|---:|---:|---:|---:|
| G6_DT0 | 1820 | 0.01 | 7.56280062397e-06 | 0.07562801 | 1801 |
| G6_DT1 | 3640 | 0.01 | 1.37050733995e-06 | 0.01370507 | 1542 |
| G6_DT2 | 7280 | 0.01 | 1.50924509068e-06 | 0.01509245 | 3236 |
| G9_DT0 | 1820 | 0.01 | 1.25912862083e-05 | 0.12591286 | 1476 |
| G9_DT1 | 3640 | 0.01 | 1.78754339224e-05 | 0.17875434 | 3003 |
| G9_DT2 | 6695 | 0.009196625 | 0.000102491858996 | 1.02491859 | 6695 |

## 冻结门比例计数

| 案例 | >= 0.80 | >= 0.90 | >= 0.95 | >= 1.00 |
|---|---:|---:|---:|---:|
| G6_DT0 | 0 | 0 | 0 | 0 |
| G6_DT1 | 0 | 0 | 0 | 0 |
| G6_DT2 | 0 | 0 | 0 | 0 |
| G9_DT0 | 0 | 0 | 0 | 0 |
| G9_DT1 | 0 | 0 | 0 | 0 |
| G9_DT2 | 3 | 2 | 1 | 1 |

## G9_DT2 失败步描述

- 失败步是本序列最大散度步：`true`
- 失败步是登记输出对齐步：`false`
- 压力残差/冻结门：`0.983083835608`；接近门：`true`
- 投影后 CFL/冻结门：`0.671900872197`；接近门：`false`
- 失败步存在 retry：`false`
- 压力迭代稳健异常标志：`false`
- 散度稳健异常标志：`true`

最后 64 步、散度最高 32 步、三个固定窗口、逐序列 dtype/shape/hash 和原生输出时刻跨层对照完整保存在同目录 JSON；Markdown 不重复宽表，以避免窄页截断。

## 结论边界

```text
D3R1_S2 = FAILED_STOPPED
D3R1_DIRECT_TRIGGER = PROJECTED_DIVERGENCE
D3R1_FAILURE_MECHANISM = NOT_YET_ESTABLISHED
D3R1_S3 = NOT_EXECUTED
D3R1_SCIENTIFIC_CLASSIFICATION = NOT_FORMED
HF3D_P0_D = COMPLETED
HF3D_P0_P = NOT_EXECUTED_BY_THIS_TOOL
HF3D_P1_IMPLEMENTATION = NOT_AUTHORIZED
NUMERICAL_QUALIFICATION = NOT_DEMONSTRATED
PHYSICAL_VALIDATION = NOT_PERFORMED
EXPERIMENTAL_VALIDATION = NOT_PERFORMED
APPARATUS_PREDICTION = DISALLOWED
```

这些描述性结构不能单独区分孤立尖峰、持续逼近、压力耦合、输出对齐效应或混合机制，也不能支持放宽阈值或修改正式数值合同。
