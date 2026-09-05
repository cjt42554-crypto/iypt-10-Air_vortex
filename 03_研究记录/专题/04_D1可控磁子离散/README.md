# D1 可控磁子几何离散

状态：`D1_STATIC_G1_PASS_CONFIRMED__STOP_FOR_PRO_REVIEW`

上位裁决：`../04_D0A后续科学裁决与D1设计边界.md`

机器可读合同：`d1_geometry_preregistration.json`

## 本轮完成范围

- `reproduce_g0_conditioning.py` 直接导入正式 `ImmersedCartesianGrid`，复现 D0-A 四层结果以及 `6` 格层的 ULP 和枚举顺序病态；
- `controlled_bar_geometry.py` 实现候选 `G1_SYMMETRY_MOMENT_CONSTRAINED_BOOLEAN_MASK` 和静态诊断用 `G2_ADAPTIVE_SUBCELL_CAPSULE_REFERENCE`；
- `audit_d1_geometry.py` 执行 `4/6/9/13.5` 四个名义层和合同登记的 `6/9` 共 54 个相位算例；
- `analyse_d1_geometry.py` 从落盘 JSON 独立重算身份、算例集合、硬几何门、层间跳变和停止点；
- `tests/test_d1_geometry.py` 覆盖几何解析量、边界带、对称性、精确选择器、G2 区间和枚举/ULP 不变性。

全部实现和结果只位于本候选目录。`01_最终理论/**` 与 `02_模拟程序/**` 未修改，动态求解步数为 `0`。

## 静态结果

| 格/磁子直径 | 磁子单元数 | 体积相对误差 | 质心误差/半径 | 二阶矩相对误差 | 六连通分量 |
|---:|---:|---:|---:|---:|---:|
| 4 | 236 | 0.006334218 | 0.01914626 | 0.001813998 | 1 |
| 6 | 792 | 0.0004024994 | $2.313043\times10^{-15}$ | 0.000146345 | 1 |
| 9 | 2712 | 0.0001271449 | 0.01392986 | 0.0006245843 | 1 |
| 13.5 | 9152 | 0.00001787717 | 0.003561254 | 0.006970189 | 1 |

`6` 格层 27 个相位算例的最大质心误差为 `0.04972436`，最大二阶矩误差为 `0.03309877`；`9` 格层相应最坏值为 `0.02629424` 和 `0.02415944`。全部 54 个相位算例均满足预注册硬门，所有掩膜均为单一六连通分量，枚举顺序和 ULP 不变性门均通过。

G2 全局区间宽度门通过：体积相对区间宽度为 `9.99935518e-06`，质心/半径区间宽度为 `0`，二阶矩相对区间宽度为 `2.05406040e-05`。

## 未裁决的 G1–G2 差异

机器合同要求记录 G1–G2 加权对称差，但没有为其预注册 PASS 阈值，所以它不参与静态总门。全部 58 个静态算例中的最大相对上界为 `0.214969991`，出现在若干等值 `6` 格相位算例；四个名义层的相对区间如下。

| 格/磁子直径 | G1–G2 加权对称差相对区间 |
|---:|:---|
| 4 | `[0.1909331, 0.1910331]` |
| 6 | `[0.1683747, 0.1684747]` |
| 9 | `[0.1303297, 0.1304297]` |
| 13.5 | `[0.0810327, 0.0811327]` |

该诊断必须由 Pro 在决定是否授权候选 G0/G1 动态影子对照前复核；当前静态 PASS 不等于该差异已被接受。

## 证据文件

- `reports/a0_g0_conditioning.json` 与 `reports/a0_g0_conditioning.md`：正式 G0 只读复现；
- `reports/d1_static_audit.json` 与 `reports/d1_static_audit.md`：58 个静态算例的完整几何、掩膜哈希、G2 区间和不变性证据；
- `reports/d1_static_analysis.json` 与 `reports/d1_static_analysis.md`：独立合同复核和停止裁决。

最终审计绑定清洁 `main` 提交 `242bb3da6a303b4c69b11bc42b1790b27dd618d6`。审计前后正式源码树身份均为 `dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8`，共 58 个文件、1,004,894 字节。

## 复现命令

从仓库根目录执行：

```bash
python3 90_候选工作区/current/04_D1可控磁子离散/reproduce_g0_conditioning.py \
  --json-output 90_候选工作区/current/04_D1可控磁子离散/reports/a0_g0_conditioning.json \
  --markdown-output 90_候选工作区/current/04_D1可控磁子离散/reports/a0_g0_conditioning.md

python3 -m unittest discover \
  -s 90_候选工作区/current/04_D1可控磁子离散/tests -v

python3 90_候选工作区/current/04_D1可控磁子离散/audit_d1_geometry.py \
  --json-output 90_候选工作区/current/04_D1可控磁子离散/reports/d1_static_audit.json \
  --markdown-output 90_候选工作区/current/04_D1可控磁子离散/reports/d1_static_audit.md

python3 90_候选工作区/current/04_D1可控磁子离散/analyse_d1_geometry.py \
  --json-output 90_候选工作区/current/04_D1可控磁子离散/reports/d1_static_analysis.json \
  --markdown-output 90_候选工作区/current/04_D1可控磁子离散/reports/d1_static_analysis.md
```

审计器要求在 `main` 的清洁工作树上启动，并在结束时复核 Git HEAD 与正式源码树身份没有变化。

## 停止点与结论上限

当前必须停在 `STOP_FOR_PRO_REVIEW`。条件性下一请求为 `REQUEST_PRO_REVIEW_FOR_CANDIDATE_ONLY_G0_VS_G1_DYNAMIC_SHADOW`；该字符串只是供 Pro 裁决的请求，不构成动力学运行、正式提升、D0-B、D0-C、完整 Q1R4 或 Q1R3 时间组授权。

D1 目前只证明候选静态几何通过已登记门。它不证明数值收敛，不构成物理或实验验证，不允许装置预测。
