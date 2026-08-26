# HF3D Q1R2 完整空间轨迹的下一步长诊断

状态：`DIAGNOSTIC_ONLY__Q1R3_COMMON_DT_NOT_FROZEN`。

本报告把 Q1R2 三层完整 `0.02 s` 空间轨迹投影到更小的共同登记步长，用于下一次 Q1R3 预注册决策。它不是新 campaign、不是运行结果，也不授权恢复或改写 Q1R2。

## 输入身份

- Q1R2 生成清单 SHA-256：`33fffa8b96a69ced47bbf2dbf291acfd9899237963912637e7cc4b968ce7932a`。
- 正式源码树 SHA-256：`03ebc4dcd194d0ffadc7f24228720d775dc7d888ceb5a9737816368626482010`。
- 粗、中、细空间归档清单 SHA-256 分别为 `b798708a…3583`、`855d8616…ceec`、`6c02cc45…a16e`。
- 三层分别包含 3340、3340、3353 个接受步；原登记步长均为 `6.0e-6 s`。

## 投影规则与结果

在 `0.1 µs` 量化网格上，选择三层预测命中率均至少 0.97 的最大共同值；最终实际验收门仍为 0.95，因此保留 0.02 的比例设计余量。

- 暂选投影值：`5.5e-6 s`；三层最小预测命中率为 0.973754846406。
- 相邻更大值：`5.6e-6 s`；三层最小预测命中率只有 0.965404115717，未达到设计门，因此拒绝。

## 重建命令

从 `90_候选工作区/current/01_HF3D数值资格/` 执行：

```bash
.venv-gpu/bin/python analyse_q1r2_dt_pilot.py \
  generated_q1r2/cases_manifest.json \
  --archive space_L0_4cells=runs/q1r2/space_L0_4cells \
  --archive space_L1_6cells=runs/q1r2/space_L1_6cells \
  --archive space_L2_9cells=runs/q1r2/space_L2_9cells \
  --expected-source-sha256 \
  03ebc4dcd194d0ffadc7f24228720d775dc7d888ceb5a9737816368626482010 \
  --json-output /tmp/airvortex-q1r3-dt-diagnostic.json \
  --markdown-output /tmp/airvortex-q1r3-dt-diagnostic.md
```

该命令复用原工具，因此临时原始输出中的名称仍含 `Q1R2`；本包装报告只抽取轨迹投影数据，不继承其“可冻结”措辞。

## 结论边界

降低登记上限会改变后续状态轨迹，所以 `5.5e-6 s` 不能仅凭投影冻结。下一步至少应先做一个相同细网格、相同 `0.02 s`、候选 `5.5e-6 s` 的正式 CUDA 探针，并在运行前修正液体净存量与开边界守恒残差的门禁语义。数值资格、物理验证和实验验证均未建立。
