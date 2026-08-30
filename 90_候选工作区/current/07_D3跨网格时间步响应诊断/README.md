# D3 跨网格时间步响应诊断

状态：

- 静态实现：`D3_V2_STATE_MACHINE_PATCH_READY_FOR_PRO_REVIEW`；
- 动态执行：`NOT_AUTHORIZED`。

## 目标

D3 检验正式 G0、`6/9` 格和 `0–0.010 s` 短窗内，两个网格对共同时间步减半的响应是否不同，以及这种差分时间步响应是否实质污染了原 Q1R3 的 `6→9` 空间差。

D3 不建立数值资格，不修改正式 HF3D，不预测长期稳态或实验装置。

## 冻结入口

科学裁决：

```text
90_候选工作区/current/09_D2V2执行结果独立复核与D3科学裁决.md
```

机器可读合同：

```text
90_候选工作区/current/07_D3跨网格时间步响应诊断/d3_cross_grid_temporal_preregistration.json
```

独立执行授权对象：

```text
90_候选工作区/current/07_D3跨网格时间步响应诊断/d3_execution_authorization.json
```

冻结预注册继续保存科学设计与“未授权”历史状态；未来执行许可只能写入独立授权对象，不能通过翻转预注册布尔值开门。

基线配置：

```text
90_候选工作区/current/01_HF3D数值资格/generated_q1r3/space_L1_6cells.json
90_候选工作区/current/01_HF3D数值资格/generated_q1r3/space_L2_9cells.json
```

正式源码树 SHA-256：

```text
dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8
```

## 已实现文件

```text
run_d3_cross_grid_temporal.py
analyse_d3_cross_grid_temporal.py
d3_stage_machine.py
d3_execution_authorization.json
tests/test_d3_cross_grid_temporal.py
generated_configs/G6_DT0.json
generated_configs/G6_DT1.json
generated_configs/G6_DT2.json
generated_configs/G9_DT0.json
generated_configs/G9_DT1.json
generated_configs/G9_DT2.json
artifacts/d3_implementation_static_review.json
```

运行器直接导入 `02_模拟程序/src/airvortex/**` 的正式 HF3D、时间步控制器、记录器和归档器，没有复制或修改正式求解器。独立分析器不导入候选运行器，而是从预注册和正式归档读取器重新构造配置、硬门、算术与标签。

## 六算例设计

```text
G6_DT0  5.5e-6 s
G6_DT1  2.75e-6 s
G6_DT2  1.375e-6 s

G9_DT0  5.5e-6 s
G9_DT1  2.75e-6 s
G9_DT2  1.375e-6 s
```

六个确认性算例必须在 D3 预注册后、同一清洁执行提交上从零运行。D2 的 F6/F9/T9 只能作为提出 D3 的既有证据，不得进入 D3 分类输入。

## 当前允许的复核命令

以下命令均不推进真实 HF3D 时间步。GPU 静态审计必须使用项目既有的隔离 GPU 解释器，不能以缺少 CuPy 的系统 `python3` 代替。

```bash
D3_GPU_PYTHON="90_候选工作区/current/01_HF3D数值资格/.venv-gpu/bin/python"

"$D3_GPU_PYTHON" \
  "90_候选工作区/current/07_D3跨网格时间步响应诊断/run_d3_cross_grid_temporal.py" \
  generate-configs

"$D3_GPU_PYTHON" -Werror -m unittest discover \
  -s "90_候选工作区/current/07_D3跨网格时间步响应诊断/tests" \
  -p "test_d3_cross_grid_temporal.py" -v

"$D3_GPU_PYTHON" \
  "90_候选工作区/current/07_D3跨网格时间步响应诊断/run_d3_cross_grid_temporal.py" \
  static-audit --write
```

静态审计会实际完成以下非推进检查：

1. 逐算例证明配置差异不越过登记 allowlist；
2. 重新构造六组正式网格和初态；
3. 对同网格三层配置逐 dtype、shape 和 SHA-256 比较网格、正式 G0 掩膜与初态；
4. 核对正式源码树哈希；
5. 探测实际 CUDA FP64 后端；
6. 核对 D3 raw、动态报告和最终清单尚不存在；
7. 核对 S0–S3 唯一有序、状态组合合法、失败后不再推进，并要求每一阶段使用精确登记的证据路径；
8. 明确记录 `time_steps_advanced = 0`。

V2 还增加以下结果盲静态加固：

1. 执行授权必须绑定受复核提交、预注册 blob、正式源码树和全部候选实现文件的 Git blob 与内容 SHA-256；
2. 授权提交必须是受复核快照的唯一直接子提交，且该提交只能修改授权对象，从而避开授权文件自哈希递归；
3. 独立分析器自行重建并逐字段比较 S0、逐算例摘要、正式 G0 归档掩膜和同网格三个时间层的初态身份，不采信运行器给出的结论布尔值；
4. 所有可捕获的动态异常统一写入一个失败事务出口，使失败标记、阶段台账和可恢复诊断保持一致；
5. 诊断分窗先在完整轨迹上计算 `Q(t)-Q(0)`，再切取登记窗口；后半窗口不会重新减去 `Q(0.005 s)`。

补充状态机补丁还固定了以下执行前语义：

1. S1、S2 和 S3 共用 `d3_stage_machine.py` 中无文件系统副作用的前置门；目标阶段前必须形成精确 `EXECUTED/PASS` 前缀，目标及后继阶段必须全部保持 `NOT_EXECUTED`；
2. S0 报告必须完整绑定同一执行提交、起止实现身份、六算例零步初态审计和精确 fresh-evidence 策略，并与台账 S0 的 `EXECUTED/PASS` 及登记证据路径交叉一致；
3. 乱序、重复、前驱缺失、证据错误、授权或参数前置门失败均作为无副作用拒绝，不写失败标记、不改台账、不创建算例目录；
4. 只有前置门通过后的阶段内异常才进入失败事务；失败台账候选先在内存中通过语义门，随后才写失败标记和原子替换台账，任一步写入失败都会撤回新标记并保留原台账；
5. S3 在分析起止锁定同一 HEAD、完整授权对象、授权文件 blob/hash、裁决身份及裁决文件 blob/hash，并继续要求清洁工作树。

## 动态命令的当前闭锁

运行器已经实现未来执行入口，但当前独立授权对象明确保存：

```text
status = NOT_AUTHORIZED
real_cuda_dynamic_execution = false
authorized_stages = []
```

会在创建 D3 raw 或推进任何时间步之前拒绝以下命令：

```bash
"$D3_GPU_PYTHON" \
  "90_候选工作区/current/07_D3跨网格时间步响应诊断/run_d3_cross_grid_temporal.py" \
  prepare-execution

"$D3_GPU_PYTHON" \
  "90_候选工作区/current/07_D3跨网格时间步响应诊断/run_d3_cross_grid_temporal.py" \
  run-stage --stage S1

"$D3_GPU_PYTHON" \
  "90_候选工作区/current/07_D3跨网格时间步响应诊断/run_d3_cross_grid_temporal.py" \
  run-stage --stage S2

"$D3_GPU_PYTHON" \
  "90_候选工作区/current/07_D3跨网格时间步响应诊断/analyse_d3_cross_grid_temporal.py" \
  final
```

只有新的 Pro 裁决明确授权同一受复核快照后，才允许按 `S0 → S1 → S2 → S3` 顺序使用这些入口。合法授权需要满足：

1. Pro 先完成对唯一清洁 `main` 快照的复核；如需新增详细裁决文档，可先单独提交该文档；
2. 随后的授权提交只能修改 `d3_execution_authorization.json`；
3. 授权对象把其唯一父提交、预注册 blob、正式源码树、候选实现文件集合及每个 blob/hash 全部写实；
4. 缺失、格式错误、文件集合变化、hash 漂移、父提交漂移、配置漂移或正式源码漂移均在创建 raw 前失败。

授权文件本身不写入自身提交 SHA 或自身 blob，因此不存在自哈希递归。任何实现文件在复核后发生变化，即使已经提交且工作树清洁，也会使旧授权失效。

S1 或 S2 任一算例硬门失败，或在完整阶段前置门通过后，算例准备、推进、归档、摘要和阶段提交发生可捕获异常，都会进入同一失败事务，停止后续算例，并把顶层状态闭锁为 `D3_INCONCLUSIVE_FAIL_CLOSED`。前置条件拒绝不会生成动态失败证据。

## 独立分析边界

未来 S3 会独立重载六份正式归档并重新计算：

```text
q, s, u, c
eta_delta_1
rho_common_1, rho_common_2
gamma_G6, gamma_G9, gamma_delta
cosine_G6, cosine_G9, cosine_delta
observed_order_G6, observed_order_G9, observed_order_delta
```

分析器只接受 D3 自身 raw 根目录下的规范归档，拒绝 D2、隔离目录、checkpoint、符号链接和跨提交轨迹，并要求逐算例摘要中的归档载荷文件集、大小和 SHA-256 与实际归档精确一致。余弦零范数保持为未定义；诊断分窗不能改变完整窗口顶层标签；二值事件只记录，不作为 PASS 门。

所有窗口的去初值语义统一为先在完整轨迹计算：

$$
q_{L,k}(t)=Q_{L,k}(t)-Q_{L,k}(0),
$$

再切取 `0–0.005 s` 或 `0.005–0.010 s` 的原生输出样本。该选择在任何 D3 动态结果出现前完成并由测试锁定。

## 当前静态证据

```text
artifacts/d3_implementation_static_review.json
```

该文件只证明候选实现与静态门在所记录环境中通过，不是动态执行证据，也不包含 D3 科学分类。

## 当前禁止

- 运行任何真实 D3 CUDA 动态算例；
- 创建声称 S1、S2 或 S3 已执行的报告；
- 复用 D2 动态轨迹；
- 修改 `01_最终理论/**` 或 `02_模拟程序/**`；
- 自动进入更小时间步、更细网格、长期窗口、Q1R4 或 D4。

## 停止点

候选状态机补丁和零步静态测试完成后停在“等待 Pro 独立复核与裁决，动态执行未授权”。

只有新的 Pro 明确裁决才能启动真实 CUDA 阶段。
