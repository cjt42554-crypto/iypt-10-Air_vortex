# D3R1 S2 失败取证包

状态：`D3R1_S2_G9_DT2_PROJECTED_DIVERGENCE_FAILURE_FORENSIC_GATE_PASS`

本取证包冻结 R1 自然失败的身份、硬门和证据边界。它只从失败 partial 读取 `frame_time_s`，不读取或报告状态数组或主要物理观测量，不能支持 D3 科学分类。

## 绑定

- 协议：`D3_HF3D_CROSS_GRID_TEMPORAL_RESPONSE_R1`
- 执行提交：`be349aec2f1bdcdb232e282a11175921eda7976b`
- 性能与失败处置裁决提交：`d1aff0b6f46783096ecfcc8c820b68145b78aa55`
- 正式源码树：`dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8`
- 审计脚本 SHA-256：`72060a5794812a072e92483fe7217b5a92e69c40263531345c30a1bfebd8a8f6`

## 原始证据快照

- 文件数：`40`
- 总字节数：`359454291`
- 规范快照 SHA-256：`906a32f71f3e095afc7cba6a3eddf7a83e4d639d36dc0b3014278b3e0001b032`

40 个文件的逐文件路径、字节数和 SHA-256 完整冻结在同目录 JSON 的 `raw_snapshot.files`；本 Markdown 只重复列出直接界定失败状态的关键文件，避免窄页渲染截断。

### 关键原始文件

- 路径：`cases/G9_DT2/case_summary.json`
    - 字节数：`95925`
    - SHA-256：`3eb6d14e11fd45d944f213177cb5cec4eb92e2c333ba7c696f803839c2df73c6`
- 路径：`cases/G9_DT2/partial.npz`
    - 字节数：`60848588`
    - SHA-256：`8f1e0973eda48c2ecc54f10001f28b988d353e85a728a16ec24f74582527cbcc`
- 路径：`execution_failure.json`
    - 字节数：`1050`
    - SHA-256：`bc9233adabb138d576344c71197d2dd537652cafdea7c397e428c0129fafc104`
- 路径：`stage_ledger.json`
    - 字节数：`2452`
    - SHA-256：`6c0125cd64417c0e381269cdc4ff5832d352e3c43e4bb337f9777ecb41508e30`

## 阶段状态

| 阶段 | disposition | status |
|---|---|---|
| S0 | `EXECUTED` | `PASS` |
| S1 | `EXECUTED` | `PASS` |
| S2 | `FAILED_STOPPED` | `D3_INCONCLUSIVE_FAIL_CLOSED` |
| S3 | `NOT_EXECUTED` | `NOT_EXECUTED` |

S2 为自然硬门失败，S3 未执行，`subsequent_stages_executed = false`。

## 已完成案例

下列五个案例的状态均为 `D3_CASE_COMPLETE_ALL_HARD_GATES_PASS`，归档载荷身份均已复核。

| 案例 | 阶段 | 重试 | 墙钟 s | CPU s |
|---|---|---:|---:|---:|
| G6_DT0 | S1 | 0 | 790.561558 | 800.113809 |
| G6_DT1 | S1 | 0 | 1542.255561 | 1561.067467 |
| G6_DT2 | S1 | 0 | 2977.589639 | 3014.482555 |
| G9_DT0 | S2 | 0 | 3982.075571 | 4059.093851 |
| G9_DT1 | S2 | 0 | 7821.489456 | 7942.530704 |

## G9_DT2 失败硬门

- 失败：`FloatingPointError: G9_DT2 hard gate failed: projected_divergence`
- 接受步数：`6695`
- 失败时模拟时刻：`0.0091966249999999479 s`
- 最大投影散度：`0.000102491858996245355 s^-1`
- 冻结上限：`0.000100000000000000005 s^-1`
- 超限比：`1.02491858996`；绝对超量：`2.49185899624535044e-06 s^-1`
- 其他导致完整性为假的门是 `complete_to_registered_end_time` 和 `native_output_times_exact`；它们是失败后未到 `0.010 s` 的结果，不是第二个独立数值根因。
- CUDA 仍为真实设备且无回退；执行提交、正式源码树和授权身份在失败前后稳定。

## Partial 边界

- 路径：`90_候选工作区/current/07_D3跨网格时间步响应诊断/artifacts/r1/runs/cases/G9_DT2/partial.npz`
- 字节数：`60848588`
- SHA-256：`8f1e0973eda48c2ecc54f10001f28b988d353e85a728a16ec24f74582527cbcc`
- 只读取了 `frame_time_s`；其值逐位等于冻结输出时刻 `0.000` 到 `0.009 s` 的前十项。
- 其他 NPZ 成员只枚举名称，未读取数组值。

## 结论边界

```text
D3R1_S2 = FAILED_STOPPED
D3R1_FAILURE_CASE = G9_DT2
D3R1_FAILURE_GATE = PROJECTED_DIVERGENCE
D3R1_S3 = NOT_EXECUTED
D3R1_SCIENTIFIC_CLASSIFICATION = NOT_FORMED
NUMERICAL_QUALIFICATION = NOT_DEMONSTRATED
PHYSICAL_VALIDATION = NOT_PERFORMED
EXPERIMENTAL_VALIDATION = NOT_PERFORMED
APPARATUS_PREDICTION = DISALLOWED
```

不得删除、修补、拼接、选择性保留或重跑本次 D3R1，也不得将 partial 用作后续科学运行初态。
