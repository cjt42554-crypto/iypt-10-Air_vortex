# 当前交接

## 当前唯一待续任务

Q1R3 已完成开边界质量语义拆分、冻结前细网格探针和正式冻结；冻结后的三层空间 CUDA 原始算例也已全部完整结束并通过归档完整性读取，但尚未执行空间组质量与收敛裁决。当前状态为 `Q1R3_FROZEN__SPATIAL_RUNS_COMPLETE__SPATIAL_DECISION_PENDING__TEMPORAL_NOT_STARTED`。

下一步不得直接启动时间组。必须先分析现有三层空间归档，并形成绑定冻结清单的空间阶段 PASS/FAIL 裁决；只有空间阶段全部门通过，才允许执行时间中、细层。正式理论 `01_最终理论/**` 和正式程序 `02_模拟程序/**` 本轮均未修改。

## 必要入口

- Q1R3 探针前预注册：`90_候选工作区/current/01_HF3D数值资格/reports/q1r3_preregistration/preregistration_report.md`、`preregistration_report.json`；
- 冻结前细网格探针：`reports/q1r3_probe/probe_report.md`、`probe_report.json`；
- Q1R3 冻结身份与早停协议：`reports/q1r3_freeze/freeze_manifest.md`、`freeze_manifest.json`；
- 冻结 campaign 与生成清单：`campaign_q1r3.json`、`generated_q1r3/cases_manifest.json`；
- 质量语义实现：`hfq_quality_contract.py`、`hfq_cli_common.py`、`hfq_cli_analysis.py`；
- 本地忽略的冻结后空间档案：`runs/q1r3/space_L0_4cells`、`space_L1_6cells`、`space_L2_9cells`；
- Q1R2 历史失败证据仍保留在 `reports/q1r2/`，不得用 Q1R3 结果改写。

## 冻结与证据身份

- 正式可执行源码树 SHA-256：`03ebc4dcd194d0ffadc7f24228720d775dc7d888ceb5a9737816368626482010`；
- Q1R3 campaign 逐字节 SHA-256：`e400a31bec6ae9f907093ec418e81d6ae03850ed27a13c6e5663759186e4aaf4`；
- Q1R3 生成清单逐字节 SHA-256：`d2d95a599b831e1741f0df38be6069a8ebd93301d981726429bf632cdb823da6`；
- 探针前预注册报告 SHA-256：`6397664427fcc86661e0688c0ea16865f62be74a139577961eb2c9a1bdfabe30`；
- 冻结前探针报告 SHA-256：`08b03271b95ea1ae0d9db24cf19b8ed62fe60f70994ea2edf7c3d7ef8c0e04fb`；
- Q1R3 冻结清单 SHA-256：`76df3985b63badd43f8517548c17f195e7388b2fcc6e197ec551ba60413829ef`；
- 冻结后空间归档清单 SHA-256：粗层 `44faf36211d88a6569daaf6e4285151ab52f7e604f8063b7fbf257b50a827d56`，中层 `794b0bc63f9bec688f32f0bc71e4ca05160d3b4ffce9d0af90636ef87f6705ae`，细层 `99b7cf26cd5e2c50959e09af1fcfa998a34df63778297e78ee6e27d9abd78b4d`；
- 冻结物理输入签名：`fb4e3a3b993fa5e569aed09d062239b0895f2fcaf40f642405edd502d6ad1059`；
- 质量契约：`OPEN_BOUNDARY_INVENTORY_CONSERVATION_SPLIT_V1`。

## 本轮已完成

1. 审核正式输出语义：`liquid_mass_error_relative` 实际是相对初始液体体积的开边界净存量变化，不是通量修正后的数值守恒残差。Q1R2 原始冻结失败保持不变；Q1R3 将 `liquid_volume_balance_error_relative` 保留为数值守恒硬门，并把前者重分类为必需、非绝对门控的空间/时间平滑观测量。
2. 生成 Q1R3 探针前设计和预注册裁决；8/8 门通过。旧 Q1R2 和 pilot 清单在新框架下仍可逐字节再生成，避免事后改写旧合同。
3. 完成 `136 × 136 × 120`、`0.02 s`、`5.5e-6 s` 正式 CUDA 冻结前探针；3650 步完整结束，登记步长命中率 0.957808 高于 0.95，CUDA 无回退，正式源码身份前后一致，11/11 探针门通过。通量修正守恒残差为 `9.66857e-12`；液体净存量变化为 `0.00140927`，只保留为非绝对门控平滑量。
4. 探针通过后冻结 Q1R3；12/12 冻结门通过，独立临时目录再生成逐字节一致。冻结协议明确禁止复用探针归档，并要求空间组三层先运行、先裁决，任一空间门失败即不执行时间新增算例。
5. 在全新的 `runs/q1r3/` 顺序执行冻结后空间粗、中、细三层。三层均达到 `0.02 s`，分别完成 3640、3640、3650 步；归档使用 `load_run_archive(..., verify=True)` 均可完整读取，稠密时步数量与完成步数一致，CUDA 无回退，正式源码树前后均匹配冻结身份。
6. 候选测试 32/32、相关 Python `compileall`、17 个 Q1R3 JSON 严格解析、Q1R3 与探针设计独立逐字节再生成均通过。顶层 `AGENTS.md`、两份交接及三份 Q1R3 报告通过 Pandoc `markdown` reader；三份含表格/公式报告已转为独立 HTML/MathML，并用本机 Chrome 实际渲染目视检查，未见截断、错表或公式异常。
7. 按用户要求调整并纳入顶层 `AGENTS.md` 的授权边界措辞，明确“继续任务”等措辞本身不授权修改正式成果；该治理修改不改变正式理论或正式程序内容。

## 尚未完成及结论边界

1. 冻结后空间组三层尚未运行 `hfq.py analyse`，因此实际登记步长命中率、数值质量门、六个平滑量的空间相对误差、观测阶、GCI 和二值事件一致性均未裁决。
2. 时间中层 `2.75e-6 s` 和时间细层 `1.375e-6 s` 未启动；时间粗层只能复用冻结后的 `space_L2_9cells`，不得复用冻结前探针。
3. 现有通用分析器在时间层缺失时，顶层会按设计报告 `INCOMPLETE_MISSING_OR_UNREADABLE_RUNS`，但仍会生成空间组结果。不得把该顶层“时间缺失”状态直接当作空间组失败；应另形成绑定冻结早停协议的空间阶段裁决。
4. 未执行更长时统计稳态、平滑区 Q2、近拓扑区 Q2、物理校准、盲法实验或装置级预测验证。
5. 当前结论上限为 `Q1R3_FROZEN__SPATIAL_RAW_RUNS_COMPLETE__NO_GO_NUMERICAL_QUALIFICATION__NO_GO_PHYSICAL_PREDICTION`。

## 下一次执行顺序

1. 保持 `runs/q1r3/**` 原样，先执行：

   ```bash
   cd '90_候选工作区/current/01_HF3D数值资格'
   .venv-gpu/bin/python hfq.py analyse \
     generated_q1r3/cases_manifest.json \
     runs/q1r3 \
     reports/q1r3_spatial_stage
   ```

   由于时间层尚缺失，该命令预期返回非零并写出顶层 `INCOMPLETE_MISSING_OR_UNREADABLE_RUNS`；这是预期的全清单状态，不得删除输出或误报为工具失败。
2. 只从该报告中读取并复核三个空间运行的来源/配置身份、实际时步门、数值质量门和 `groups.spatial`。新增一个绑定 `reports/q1r3_freeze/freeze_manifest.json` 的空间阶段裁决，明确列出每个空间门和是否授权时间阶段；不得修改冻结 campaign、生成清单或原始分析报告。
3. 若任一空间配置、质量、实际时步、平滑收敛或二值事件门失败，按冻结协议写出失败闭锁，停止且不运行时间层。
4. 只有空间阶段裁决全部通过，才顺序执行 `time_L1_2.750e-06s` 和 `time_L2_1.375e-06s`；随后才允许生成完整 Q1R3 总报告。
5. 无论数值筛查结果如何，物理与实验预测继续保持 `NO-GO`，不得把实现或数值门 PASS 提升为装置预测。
