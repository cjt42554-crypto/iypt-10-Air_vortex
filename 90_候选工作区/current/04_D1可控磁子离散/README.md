# D1 可控磁子几何离散

状态：`D1_STATIC_ONLY__FORMAL_PROMOTION_NOT_AUTHORIZED`

上位裁决：`../04_D0A后续科学裁决与D1设计边界.md`

机器可读合同：`d1_geometry_preregistration.json`

## 当前唯一允许的工作

1. 只读复现当前 `ImmersedCartesianGrid` 的 D0-A 几何量及一 ULP/枚举顺序敏感性；
2. 在本目录实现候选 `G1_SYMMETRY_MOMENT_CONSTRAINED_BOOLEAN_MASK`；
3. 实现只用于静态参考的 `G2_ADAPTIVE_SUBCELL_CAPSULE_REFERENCE`；
4. 执行名义层和登记相位扫描，输出严格 JSON、Markdown、测试和源码身份；
5. 静态报告完成后停止，交由 Pro 裁决。

## 禁止

- 修改 `01_最终理论/**` 或 `02_模拟程序/**`；
- 追认 D0-V1 通过或修改其阈值；
- 继续 D0-B、D0-C；
- 运行动态影子对照、完整 Q1R4 或 Q1R3 时间组；
- 为 `6/9/13.5` 硬编码掩膜；
- 用动态可观测量挑选静态几何；
- 把 G2 称为已进入正式求解器的 cut-cell；
- 把静态几何通过解释为数值收敛、物理验证或实验验证。

## 预期目录

```text
04_D1可控磁子离散/
├── README.md
├── d1_geometry_preregistration.json
├── reproduce_g0_conditioning.py
├── controlled_bar_geometry.py
├── audit_d1_geometry.py
├── analyse_d1_geometry.py
├── tests/
│   └── test_d1_geometry.py
└── reports/
```

文件名可在实现时小幅调整，但合同、失败闭锁和停止点不得弱化。
