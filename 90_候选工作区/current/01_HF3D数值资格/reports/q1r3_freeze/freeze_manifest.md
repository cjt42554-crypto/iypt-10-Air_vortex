# HF3D Q1R3 冻结清单

状态：`Q1R3_FROZEN__SPATIAL_SCREENING_AUTHORIZED`。

Q1R3 已在冻结前细网格探针全部门通过后绑定。该冻结只授权离散化数值筛查，不证明空间收敛、时间收敛、物理有效性、实验有效性或装置预测。

## 冻结身份

- campaign 逐字节 SHA-256：`e400a31bec6ae9f907093ec418e81d6ae03850ed27a13c6e5663759186e4aaf4`。
- 生成清单逐字节 SHA-256：`d2d95a599b831e1741f0df38be6069a8ebd93301d981726429bf632cdb823da6`。
- 探针报告逐字节 SHA-256：`08b03271b95ea1ae0d9db24cf19b8ed62fe60f70994ea2edf7c3d7ef8c0e04fb`。
- 物理输入签名：`fb4e3a3b993fa5e569aed09d062239b0895f2fcaf40f642405edd502d6ad1059`。
- 质量契约：`OPEN_BOUNDARY_INVENTORY_CONSERVATION_SPLIT_V1`。
- 独立临时目录重新生成与冻结目录逐字节一致。

## 冻结门

| 门 | 结果 |
|---|---:|
| `preregistration_passed_and_bound` | PASS |
| `fine_probe_passed_and_bound` | PASS |
| `formal_source_identity_bound` | PASS |
| `frozen_spec_changes_only_descriptions` | PASS |
| `frozen_generated_configs_match_probe_design` | PASS |
| `frozen_fine_config_is_exact_probe_input` | PASS |
| `frozen_case_inventory_exact` | PASS |
| `physical_signature_unchanged` | PASS |
| `open_boundary_quality_contract_frozen` | PASS |
| `static_refinement_design_passed` | PASS |
| `independent_regeneration_byte_identical` | PASS |
| `claim_boundary_remains_screening_only` | PASS |

## 执行协议

1. 探针归档不得复用为 Q1R3 筛查结果，必须使用全新的运行根目录。
2. 第一阶段依次执行空间组：`space_L0_4cells, space_L1_6cells, space_L2_9cells`。
3. 空间组三层完成后先分析。若发生 `ANY_SPATIAL_CONFIGURATION_QUALITY_EFFECTIVE_STEP_OR_CONVERGENCE_GATE_FAILS`，动作固定为 `DO_NOT_EXECUTE_TEMPORAL_NEW_CASES`。
4. 只有空间组全部门通过，才授权时间组新增算例：`time_L1_2.750e-06s, time_L2_1.375e-06s`；时间粗层复用冻结后的 `space_L2_9cells`，不复用冻结前探针。

## 结论边界

当前仅为 `Q1R3_FROZEN__SPATIAL_SCREENING_AUTHORIZED`。数值资格尚未建立，物理验证和实验验证未执行，装置级预测继续为 `NO-GO`。
