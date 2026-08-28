# D1 候选区 G0/G1 锁步动态影子对照

## 状态

```text
D1_DYNAMIC_SHADOW_COMPLETE__GEOMETRY_EFFECT_SMALL_IN_REGISTERED_WINDOW__STOP_FOR_PRO_REVIEW
```

本目录已经在不修改正式程序的条件下，完成 G0/G1 成对锁步动态影子对照。冻结几何故障归因标签为：

```text
GEOMETRY_EFFECT_SMALL_IN_REGISTERED_WINDOW
```

该标签只适用于登记的 `6/9` 格层和 `0.010 s` 短窗口，不证明 G1 更接近连续方程或实验，不撤销 Q1R3 或 Q1R4 的失败闭锁，也不授权继续动态计算或正式提升。

上位裁决与完整阈值见：

- `../05_D1动态影子对照科学裁决与预注册.md`；
- `d1_dynamic_shadow_preregistration.json`。

结果入口为：

- `reports/d1_dynamic_shadow_report.json`：机器可读裁决、逐观测量指标和结论边界；
- `reports/d1_dynamic_shadow_report.md`：人类可读摘要；
- `artifacts/d1_dynamic_shadow_manifest.json`：本地忽略原始产物的 SHA-256 清单。

执行代码绑定提交为 `68ef0dcef63a652eda348b8439ef62c13a812642`，正式源码树在完整窗口前后均为 `dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8`。

## 活动实现与产物

本目录保持以下单一活动实现：

```text
controlled_shadow_grid.py
run_d1_dynamic_shadow.py
analyse_d1_dynamic_shadow.py
tests/test_d1_dynamic_shadow.py
reports/
artifacts/
```

不得复制或分叉正式 HF3D 求解器。候选代码应导入正式网格、投影、时间步和单步推进实现，只在投影构造前安装候选几何，并在候选驱动器中实施成对锁步。

## 已执行的冻结顺序

1. 实现可重建全部掩膜依赖数组的候选网格安装器；
2. 完成 G0 往返自同一性、初态同一性和 CPU/CUDA 八步同一性测试；
3. 完成 `4` 格层 G0/G1 CUDA 八步烟雾测试；
4. 完成 `6` 格层 `0.010 s` 锁步对照；
5. 仅在全部硬门通过后完成 `9` 格层；
6. 独立分析、生成 JSON/Markdown 报告并停止。

实际 CLI 接口为：

```bash
python run_d1_dynamic_shadow.py verify-g0
python run_d1_dynamic_shadow.py smoke
python run_d1_dynamic_shadow.py run --level 6
python run_d1_dynamic_shadow.py run --level 9
python analyse_d1_dynamic_shadow.py
```

阶段边界、先后顺序和失败闭锁语义未改变。G0 自同一性、`4` 格层烟雾测试、`6` 格层完整窗口和 `9` 格层完整窗口依次通过后，分析器才生成报告。

## 禁止实现

- 不得只后赋值 `grid.bar_mask`；
- 不得在重建几何前创建压力投影或初态；
- 不得分别运行 G0/G1 后插值成共同时间网格；
- 不得修改 `01_最终理论/**` 或 `02_模拟程序/**`；
- 不得继续 `13.5/20.25` 格、Q1R3 时间组、D0-B、D0-C 或完整 Q1R4；
- 不得把影子对照报告标记为 qualification、validation 或 apparatus prediction。

## 交付物

报告必须至少给出：

- 执行提交、工作树、正式源码树和输入文件身份；
- G0 自同一性逐门结果；
- G0/G1 掩膜哈希、单元数和全部重建字段身份；
- 每对运行的共同接受时间、重试和数值质量轨迹；
- 六个登记观测量的原始与去初值轨迹；
- `N_L`、`E_0`、`E_1`、`eta_L`、`rho`；
- 固定四分类标签和完整结论边界；
- 未执行项目的明确清单。

`90_候选工作区/交接说明_current.md` 已更新为“等待 Pro 复核”。不得自行正式提升或越过报告后停止点。
