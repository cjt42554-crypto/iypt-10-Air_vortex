# Stage1 实现修复报告

## 结论

2026-08-23 的实现级修复已关闭复审列出的 3 个 P0 和 6 个 P1 问题。指定 Stage1 测试、完整 CPU 回归、严格 warning 自检及六道门调试验收均已通过；正式整包验收与提交身份将在交付前写回本报告。

允许的最终状态只有：

```text
PASS_STAGE1_IMPLEMENTATION__NO_GO_PHYSICAL_PREDICTION
```

这不是装置级预测验证。HF3D 的物理预测状态保持 `NO_GO`。

## 身份与边界

- 仓库：`cjt42554-crypto/iypt-10-Air_vortex`
- 基线 `origin/main`：`e2a8215baa204b2a7f35c84aa0606c32e1018cc3`
- 独立审查证据：`codex/local-review-results-20260823`，提交 `a5ce324c82172f663dd553a9804bab2c43f00b8a`
- 修复分支：`codex/stage1-implementation-repair-20260823`
- 允许修改范围：`04_第一阶段预测改进版/**`
- 最终提交：待提交后固化
- 首轮整包 acceptance（提交前源码内容冻结）：`03_项目资料/03_验证协议与历次结果/04_历次结果/20260823T104453Z-predictive-stage1-repair-v2/acceptance.json`
- 首轮 acceptance SHA-256：`dde4287645c8236fb1f26deb4daaaa672a97a87d6ab4e909a146ee67f6184e0c`
- 首轮源码内容 SHA-256：`e73523ae933bd3dfba969fe77b67346c2645019e96f83f25c970004ad03b4749`（验证结果历史和缓存按协议排除）

受保护原目录 tree：

| 路径 | 要求与实测 tree SHA |
|---|---|
| `01_最终理论` | `238914477bfa15cf6d8dcd1a7e731b1559b6506d` |
| `02_模拟程序` | `2d65edcd2717ac262d028ab51d1feecc850b838f` |
| `03_项目资料` | `84f0e9e210b86b556935b9117310b5e668813bd7` |

审查分支的 `本地审查结果/` 没有合并到修复分支。根 README、`.gitignore` 与三个受保护原目录均未修改。

## 实现修复

1. 后端覆盖注册改为运行时要求的四元组，并验证安装幂等、别名无冲突、CLI/GUI 不回退。
2. 前向与 HF3D 先转换为 canonical surface，再调用同一 `apply_surface_observation_operator`；实际 ROI、外环、界面 level、参考面来源及聚合规则生成科学身份。
3. 完整科学身份门下沉到核心比较 API；GUI、CLI、存档、paired comparison 与报告导出共享该门。
4. 标准物理量、相机量、状态与数值诊断均进入正式 `observable_units`。
5. 事件名映射补齐，并拆分用户普通输出时间轴和稠密事件证据时间轴。
6. 相机投影、PSF、像素化、阈值分割和边界提取进入两个后端的逐帧结果链；相机 ID 覆盖所有有效参数，并拒绝非有限输入。
7. transport 与 CLS 的相、密度、三分量动量重映射和绝对量逐步进入结果与归档；非活动零密度除法改为安全 masked divide。
8. 验证器改为 fail closed：执行真实入口、12 个指定测试、可选完整回归、归档往返、身份反例、数值阈值及修改边界检查，并为每条命令保存返回码、日志和 SHA-256。

逐项复现、根因、测试与剩余风险见 `ISSUE_LEDGER.md`。

## 六道实现门的当前证据

| Gate | 调试验收 | 主要证据 |
|---|---|---|
| 1. 可运行 | PASS | CLI 五项自检、前向 smoke、16×16×24 HF3D smoke、无显示 GUI registry、strict warnings |
| 2. 观测合同真实统一 | PASS | 单一共享函数；同面跨表示一致；ROI/参考变化改变身份；基础量可独立复算 |
| 3. 比较不可绕过 | PASS | core compare 反例在 RMSE 前拒绝；存档、CLI、GUI、报告路径继承同一身份 |
| 4. 事件与相机链闭环 | PASS | 3 个普通帧/11 个证据帧反例；每帧相机量及完整 operator ID 可归档复读 |
| 5. 数值诊断可审计 | PASS | transport/CLS 残差与动量重映射逐步保存；协议阈值实数检查；严格 warning 通过 |
| 6. 验证器可信 | PASS | 无外部 `PYTHONPATH`、源码边界、受保护 tree、命令/返回码/日志/证据哈希、故障注入 fail closed |

首轮正式整包验收的六门均为 true，机器可读证据见上列 `acceptance.json`。提交后还会从干净工作树重新生成一份绑定被测提交的冻结 acceptance；不以本表替代机器证据。

## 已完成测试快照

- Python：3.12.3（CPython，GCC 13.3.0）
- 依赖：NumPy 1.26.4、SciPy 1.11.4、Matplotlib 3.6.3、pytest 9.1.1
- 12 个指定测试文件：`21 passed, 3 subtests passed`
- 完整回归：`197 passed, 3 skipped, 55 subtests passed`
- 3 个 skip：既有 CuPy/GPU 可选测试；未将 skip 记为通过的 GPU 证据
- CLI self-check：普通与 `PYTHONWARNINGS=error` 均返回 0，五项要求均 `ok=true`
- 修复包 `scripts/10_run_acceptance.sh`：返回 0；六门 acceptance 全部为 true

调试 smoke 中数值诊断的最大绝对量：

| 诊断 | 最大绝对量 |
|---|---:|
| phase conservation residual | `5.42e-20 m^3` |
| density conservation residual | `5.55e-17 kg` |
| transport momentum remap | `2.56e-26 kg*m/s` |
| CLS phase conservation residual | `5.42e-20 m^3` |
| CLS density conservation residual | `5.55e-17 kg` |
| CLS momentum remap | `4.16e-28 kg*m/s` |
| single-step clipping / repair | `3.98e-20 / 0.0 m^3` |

这些数字只证明当前小网格 smoke 的实现诊断落在预注册阈值内，不是收敛、精度或实验符合性证据。

## 尚未完成且不得越权声称的物理门

- 静滴 Laplace 压差；
- 寄生流网格收敛；
- 毛细波频率和阻尼；
- 网格—时间—界面联合收敛；
- 真实装置的实测磁子轨迹；
- 独立校准与盲验证实验；
- 圆柱壁折射、真实镜头畸变和相机参数标定。

因此，即使最终 implementation gate 全部通过，也不得将前向/HF3D 一致性解释为装置级预测正确。
