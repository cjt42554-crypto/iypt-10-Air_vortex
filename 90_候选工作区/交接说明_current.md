# 当前交接

当前状态：

```text
D3R1_S2 = FAILED_STOPPED
D3R1_DIRECT_TRIGGER = PROJECTED_DIVERGENCE
D3R1_FAILURE_MECHANISM = NOT_YET_ESTABLISHED
D3R1_S3 = NOT_EXECUTED
D3R1_SCIENTIFIC_CLASSIFICATION = NOT_FORMED
HF3D_P0_D_RESULT_BLIND_DIVERGENCE_DIAGNOSTICS = COMPLETED
HF3D_P0_P_PRODUCTION_PATH_BASELINE_AND_PROFILER = NOT_STARTED_ENVIRONMENT_GATE_INVALID
HF3D_P1_IMPLEMENTATION_AND_FORMAL_SOURCE_MODIFICATION = NOT_AUTHORIZED
```

## 有效 Pro 裁决与冻结合同

详细裁决：

```text
90_候选工作区/current/18_D3R1S2散度失败独立复核与HF3D性能P0授权.md
```

关键提交：

```text
Pro P0 authorization: a1676fe2f81e984ce96b421a0e91d7413dfa5f0d
P0 preregistration: 246a4d5c365b022ad4bd2e930393bacdd59fef61
P0-D implementation: e00b4f40f3aff1fc0fe6d04fd97b0e31ef5c33dc
D3R1 execution: be349aec2f1bdcdb232e282a11175921eda7976b
formal source tree: dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8
```

冻结合同：

```text
90_候选工作区/current/17_HF3D性能优化/00_P0性能与散度诊断预注册.json
```

P0 只允许在 `90_候选工作区/current/17_HF3D性能优化/**` 建立诊断、基线和 profiler 证据。`01_最终理论/**`、`02_模拟程序/**`、D3R1 raw、合同、矩阵、阈值和时间步均只读。P0 完成后必须停止交 Pro；P1 和正式源码修改仍未授权。

## P0-D 已完成

执行绑定：

```text
execution commit: e00b4f40f3aff1fc0fe6d04fd97b0e31ef5c33dc
R1 raw files: 40
R1 raw bytes: 359454291
R1 raw canonical SHA-256: 906a32f71f3e095afc7cba6a3eddf7a83e4d639d36dc0b3014278b3e0001b032
G9_DT2 partial SHA-256: 8f1e0973eda48c2ecc54f10001f28b988d353e85a728a16ec24f74582527cbcc
```

输出：

```text
90_候选工作区/current/17_HF3D性能优化/artifacts/p0/divergence_diagnostics.json
90_候选工作区/current/17_HF3D性能优化/artifacts/p0/divergence_diagnostics.md
90_候选工作区/current/17_HF3D性能优化/artifacts/p0/divergence_access_ledger.json
```

输出 SHA-256：

```text
divergence_diagnostics.json = d83f3bd42b819cdfadea51dd2153f183eee7f7d29fbde6111722fb6abc0cffa5
divergence_diagnostics.md = ec0be83f071ed5b60c6dd84024e3414583a7e03d38ef0b60d15f360267ccec42
divergence_access_ledger.json = d6c380cc09e3eada938df9dfa9deab9106018511f935bbd196afe5d9e2ecbd0f
```

访问边界实际成立：只加载预注册的 10 条 `float64` 一维诊断序列；完整归档只经 `arrays.fields` 元数据解析到允许成员，失败 partial 只经显式映射读取。访问账本记录：

```text
physical_observables_read = false
state_fields_read = false
grid_array_values_read = false
prohibited_numeric_values_loaded = false
```

raw 规范哈希在数值读取前、分析后、发布后及独立复核时均为 `906a32f...1b032`。

六案例最大投影散度与冻结门比：

| 案例 | 接受步 | 终止时刻 s | 最大散度 s^-1 | 门比 |
|---|---:|---:|---:|---:|
| G6_DT0 | 1820 | 0.010000000 | 7.56280062397e-06 | 0.0756280062 |
| G6_DT1 | 3640 | 0.010000000 | 1.37050733995e-06 | 0.0137050734 |
| G6_DT2 | 7280 | 0.010000000 | 1.50924509068e-06 | 0.0150924509 |
| G9_DT0 | 1820 | 0.010000000 | 1.25912862083e-05 | 0.1259128621 |
| G9_DT1 | 3640 | 0.010000000 | 1.78754339224e-05 | 0.1787543392 |
| G9_DT2 | 6695 | 0.009196625 | 1.02491858996e-04 | 1.0249185900 |

`G9_DT2` 中达到 `0.80/0.90/0.95/1.00` 倍门的步数分别为 `3/2/1/1`。失败步是全序列最大散度步；不是登记输出对齐步；无 retry；投影后 CFL/门为 `0.6719008722`；压力残差/门为 `0.9830838356`；压力迭代不是冻结稳健异常，散度是冻结稳健异常。只能登记“末端散度异常并伴随高压力残差”的描述，不能据此唯一裁定压力耦合、孤立尖峰、持续逼近或其他机制，也不能放宽门。

已执行验证：6 个合成访问边界/统计负例测试、Ruff、格式、限定 mypy、`compileall`、严格 JSON 内部交叉、脚本哈希、raw 独立复核、Pandoc `markdown` reader，以及三页 Chrome/PDF 实际视觉检查。正式理论与正式程序未变化。

## 下一项唯一任务：P0-P

继续使用冻结合同中的 B1/B2/B3、交错顺序和环境门，不得重排、替换样本或改变除 `run.duration_s=0.002` 外的配置：

```text
B1 = G6_DT0
B2 = G9_DT0
B3 = G9_DT2
```

顺序固定为 3 次不计分 warm-up、每项 3 次新进程计时、再对 B2/B3 各做 1 次独立 profiled 运行。必须从零运行正式生产路径并包含初始化、步进、硬门、帧、归档、严格重载和 GPU 同步；不得使用 checkpoint 或旧轨迹。

2026-08-31 18:03 +0800 环境快照不满足开跑门：仓库外 Brinkman 进程约占 `114%` 单核 CPU，Bilibili/Codex 渲染负载活跃，CPU Tctl 约 `96.2 C`，高于冻结的 `90 C` 起始上限；GPU 约 `68 C`。未终止或修改任何仓库外进程，P0-P 尚未启动、没有样本或 raw。

在等待环境合法期间，只允许完成候选 harness、解析器、合成测试和零步检查。真正开跑前须重新完成合同要求的最长 1800 秒有界等待，并以 30 秒背景 CPU、CPU/GPU 温度、其他 CPU/GPU 计算进程、AC、32 个在线逻辑 CPU、governor、CUDA 环境和 profiler 覆盖能力逐项判门。任一门失败就停止，不得以线程环境变量替代端到端性能证据。

## P0 停止点与结论上限

P0-P 完成或失败闭锁后，提交全部实现、raw manifest、紧凑基线/profiler 报告和交接，push `main`，然后立即交 Pro 独立复核；不得在同一轮进入 P1 或修改正式程序。

```text
D3R1_S2 = FAILED_STOPPED
D3R1_DIRECT_TRIGGER = PROJECTED_DIVERGENCE
D3R1_FAILURE_MECHANISM = NOT_YET_ESTABLISHED
D3R1_S3 = NOT_EXECUTED
D3R1_SCIENTIFIC_CLASSIFICATION = NOT_FORMED
HF3D_P0_D = COMPLETED
HF3D_P0_P = NOT_STARTED_ENVIRONMENT_GATE_INVALID
HF3D_P1_IMPLEMENTATION = NOT_AUTHORIZED
NUMERICAL_QUALIFICATION = NOT_DEMONSTRATED
PHYSICAL_VALIDATION = NOT_PERFORMED
EXPERIMENTAL_VALIDATION = NOT_PERFORMED
APPARATUS_PREDICTION = DISALLOWED
```
