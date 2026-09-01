# P0-P V1 失败证据与 P0-P2 零步实现

本目录包含两个彼此分离的对象：

- E0 把已消费且终止失败的 P0-P V1 raw 完整登记为跟踪的只读失败证据。
- I2 实现全新身份的 P0-P2 有效配置、默认拒绝授权、三个独立零步构造探针和统一 preflight。

V1 的 claim 已永久消费，日程已永久终止。其 raw、claim、ledger、sample 或 snapshot 只能用作失败证据和负例，不得作为 P0-P2 运行输入、完成前缀、warm-up、性能结果或 claim 配额。

P0-P2 的冻结有效短窗为：

```text
run.duration_s = 0.005
drive.ramp_duration_s = 0.005
output_interval_s = 0.001
registered times = [0, 0.001, 0.002, 0.003, 0.004, 0.005]
only source change = run.duration_s
```

## 安全边界

`p0p2_execution_authorization.json` 必须保持 exact default denial，直到新的 Pro 父票据和唯一授权子提交另行完成。当前不得创建新 raw root、lock、claim、ledger 或 sample 目录，不得运行 warm-up、timed 或 profile 样本，不得开始 P1。

P0-P2 runtime preflight 只有一个顶层 `gate_pass`。它合并合同、E0 证据、授权、来源与有效配置可构造性、三个 fresh-process 零步探针、新 raw 卫生和环境。默认拒绝状态下，即使零步静态资格通过，该顶层值仍必须为 `false`。

## 只读核验命令

以仓库已绑定的 GPU Python 执行：

```bash
python build_p0p2_contract.py --check
python register_v1_failure_evidence.py --check
python audit_p0p2_zero_step.py --check
```

`run_p0p2_preflight.py --zero-step-preflight` 在当前 exact denial 下预期返回非零退出码，并报告：

```text
status = HF3D_P0P2_ZERO_STEP_STATIC_PASS_DYNAMIC_EXECUTION_DENIED
gate_pass = false
static_qualification.gate_pass = true
hf3d_time_steps_advanced = 0
```

这不是动态执行 PASS，也不是性能、数值资格、物理验证、实验验证或装置预测结论。

跟踪静态证据位于 `artifacts/p0p2/static/`，包括统一 preflight 报告、三份单独的正式配置往返报告、三份单独的 fresh-process 零步报告、新工具链/硬件/环境能力身份和静态审计。它们只为后续 Pro 静态复核与可能的新父票据提供绑定材料。
