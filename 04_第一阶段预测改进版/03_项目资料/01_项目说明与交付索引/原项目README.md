# IYPT 2027 第 10 题：空气涡流统一研究平台

本项目围绕磁力搅拌器驱动棒状磁子时形成的自由液面漏斗与贯通空气通道，提供一套可复现的研究工程：严格 SI 配置、共享数据契约、两个计算后端、图形界面、命令行运行、结果归档、实验导入与模型比较。软件服务于理论推导、数值探索和实验设计，不把计算输出自动当作实验事实。

## 规范交付顺序

本项目的规范成果只认 `deliverables/` 中以下四份文档，并严格按用户指定顺序阅读：

1. `deliverables/01_前向预测理论.md`：闭合的 F1.0 前向预测理论；
2. `deliverables/02_前向预测理论的数值计算理论.md`：只讨论 F1.0 的离散、误差、事件与程序验收；
3. `deliverables/03_第一性原理高保真模拟.md`：独立 HF3D 局部守恒律模型及程序证据；
4. `deliverables/04_结构性理论.md`：在前三阶段冻结后建立的双临界、模态、分岔候选与可证伪预测。

`docs/`、`research/` 中的旧 M2/H1 文稿和探索记录保留用于追溯，**不得覆盖或替代上述四份规范交付物**。四层文档的身份哈希、程序入口和验证活动见 `deliverables/README.md`。

## 计算后端与证据边界

| 后端 | 适合回答 | 不能据此单独主张 |
|---|---|---|
| `forward` / F1.0 前向理论 | 轴对称平均旋流、精确曲率自由界面、润湿事件、接触/连通/断裂观测以及快速扫描 | 不能证明棒状磁子附近三维流动或绝对临界转速已被实验验证 |
| `high_fidelity` / HF3D | 三维变密度不可压一流体方程、局部牛顿应力、守恒色函数、CSF、真实水平胶囊磁子、开口大气边界及接触后拓扑演化 | 当前动态网格欠分辨且未收敛、没有封存实验；不能给出装置级临界转速、稳定气道半径或 Floquet 谱 |

注册表中的 `high_fidelity` 现在直接映射到 `high_fidelity_3d.py`，不是旧 H1 轴对称预览。旧 `high_fidelity.py` 只为历史回归保留，不是统一 GUI/CLI 的默认高保真后端。HF3D 已通过实现级投影、静水、开口体积收支、时间细化趋势和旋向反演门，但动态最细网格仍只有 1.875 格跨越磁子直径，低于预注册的 6 格要求。因此当前总边界是：**程序实现通过，物理预测 NO-GO**。

## 环境要求

- Python 3.11 或更新版本；
- NumPy、SciPy、Matplotlib；
- 启动图形界面还需要系统 Python 的 Tk 支持和可用显示环境。
- 可选 CUDA 压力加速需要 NVIDIA GPU、可用驱动和隔离安装的
  `cupy-cuda13x[ctk]`；没有这些条件时 `auto` 会明确回退 CPU。

在已具备这些依赖的环境中，无需安装即可从源码运行：

```bash
cd "/home/cjt/Desktop/iypt/10 空气涡流"
PYTHONPATH=src python3 -m airvortex --help
./run_gui.sh
```

`run_gui.sh` 只设置项目自身的 `PYTHONPATH` 并启动 GUI；若项目内已有且通过
FP64 实算探测的 `.venv-gpu`，它会优先使用该隔离环境，否则使用系统
`python3`。它不会在启动时安装软件、访问网络或更改系统配置。可用
`AIRVORTEX_PYTHON=/path/to/python ./run_gui.sh` 明确指定解释器。若希望安装为
当前 Python 环境中的命令，可由用户主动执行：

```bash
python3 -m pip install -e .
airvortex --help
airvortex-gui
```

## GUI

普通启动：

```bash
./run_gui.sh
```

界面入口也可直接写成：

```bash
PYTHONPATH=src python3 -m airvortex.gui
```

GUI 提供两后端选择、参数校验、开始/暂停/继续/软停止、进度与日志、时序和场可视化、扫描、实验 CSV 导入及结果比较。HF3D 会把只读径向平均自由表面及三维方位最小–最大包络送到装置截面图：正式输出时刻一定刷新；若两个输出时刻相隔很久，运行期间还会约每 1 秒墙钟时间从一个已完成求解步提取真实快照。中间快照不写入正式结果、不反馈求解器，也不会伪造插值帧。运行过程中物理参数会锁定；“停止”是协作式软停止，已完成的数据可被保留。

HF3D 参数中的“压力求解设备”有三种可复现取值：`auto` 在 CUDA FP64
实算探测通过时使用 GPU、否则记录原因并回退 CPU；`cuda` 要求 GPU，失败时
直接停止；`cpu` 固定参考 CPU 路径。CPU 与 CUDA 使用同一个变系数 MAC
压力矩阵、同一个垂向线块 Jacobi 预条件结构、同一个 PCG 容差与迭代上限；
差别只在浮点归约顺序。当前 GPU 路径只加速占主要耗时的压力投影，其余局部
守恒律仍在 CPU 上执行，结果元数据会记录实际选择和每步主机/设备传输状态。

CPU 路径会在 NumPy/SciPy 首次导入前限制小规模 BLAS 线程，避免稀疏 PCG
被全逻辑核线程调度开销拖慢。默认最多 2 线程；可在启动前设置
`AIRVORTEX_BLAS_THREADS=N` 明确覆盖，或设置为 `inherit` 完全继承外部线程环境。

可复现地创建独立 GPU 环境：

```bash
python3 -m venv .venv-gpu
.venv-gpu/bin/python -m pip install -e '.[gpu]'
.venv-gpu/bin/python - <<'PY'
from airvortex.numerics import probe_gpu_projection
print(probe_gpu_projection().to_dict())
PY
```

不要把 CUDA 依赖装进系统 Python。`run_gui.sh` 会在每次启动时做轻量实算探测，
探测失败就使用系统 Python；计算归档中的 `pressure_accelerator` 才是该次运行
实际使用 CPU 还是 CUDA 的最终证据。

## 严格配置

所有核心配置都采用 `schema_version = 2`、`problem_id = "iypt-2027-10-air-vortex"`，物理量使用 SI 单位。解析器会拒绝未知字段、重复 JSON 键、`NaN`/无穷值、错误类型和不满足几何/时间约束的输入。`drive.actual_speed_schedule_*` 可给出实际磁子的带符号分段线性转速日程；若发生磁耦合失步，不能继续用电机旋钮值代替磁子转速。

示例：

- `configs/forward_default.json`：前向理论基准算例；
- `configs/hf_preview_quick.json`：文件名为历史遗留，但现在通过注册表运行 HF3D；其 `12×12×16`、5 ms 算例只作低分辨率烟雾测试，不能作为收敛结果；
- `configs/forward_threshold_scan.json`：以约 900 r/min 为中心开展阈值扫描的基准配置。扫描时必须使用实际磁子转速，而不是搅拌器旋钮标称值。

可以先独立校验配置能否被严格加载：

```bash
PYTHONPATH=src python3 - <<'PY'
from airvortex.io import load_config_json

for path in (
    "configs/forward_default.json",
    "configs/hf_preview_quick.json",
    "configs/forward_threshold_scan.json",
):
    config = load_config_json(path)
    print(path, config.backend.value, config.drive.target_rpm)
PY
```

## 命令行工作流

先以实际程序帮助为唯一参数真源：

```bash
PYTHONPATH=src python3 -m airvortex --help
PYTHONPATH=src python3 -m airvortex validate-config --help
PYTHONPATH=src python3 -m airvortex run --help
PYTHONPATH=src python3 -m airvortex sweep --help
PYTHONPATH=src python3 -m airvortex compare --help
```

严格校验与离线自检：

```bash
PYTHONPATH=src python3 -m airvortex validate-config \
  configs/forward_default.json --backend forward
PYTHONPATH=src python3 -m airvortex self-check --pretty
```

运行一个前向算例；输出目录必须尚不存在，以免覆盖既有证据：

```bash
PYTHONPATH=src python3 -m airvortex run \
  --backend forward \
  --config configs/forward_default.json \
  --output runs/forward-demo
```

运行 HF3D 烟雾算例；程序会在启动前打印成本和证据边界警告：

```bash
PYTHONPATH=src python3 -m airvortex run \
  --backend high_fidelity \
  --config configs/hf_preview_quick.json \
  --output runs/hf3d-quick
```

该配置仅验证三维路径、归档和可视化能运行。它没有解析磁子直径、边界层或毫米气道，任何形态图都不得当作真实装置预测。

以实际磁子转速为输入，从 700 到 1100 r/min 做 9 点线性扫描：

```bash
PYTHONPATH=src python3 -m airvortex sweep \
  --config configs/forward_threshold_scan.json \
  --backend forward \
  --parameter drive.target_rpm \
  --start 700 --stop 1100 --num 9 --scale linear \
  --workers 1 \
  --output runs/threshold-sweep
```

`sweep` 还可读取带 `base_config`、`parameter`、`values`（或 `start`/`stop`/`count`）和 `scale` 的扫描说明 JSON；命令行缺省参数由说明文件补齐。扫描以完整配置为基线，每一点生成独立归档。不要把相邻时间帧当作独立实验重复。

对两个已有归档比较共同的标量观测量：

```bash
PYTHONPATH=src python3 -m airvortex compare \
  runs/forward-demo runs/hf-demo \
  --output runs/forward-vs-hf
```

也可以用同一份配置依次运行两后端再比较；这只比较两种计算路径，并不构成实验验证：

```bash
PYTHONPATH=src python3 -m airvortex compare \
  --config configs/hf_preview_quick.json \
  --output runs/paired-preview
```

单次运行会将配置、时序、数组、事件、元数据、运行溯源和哈希清单保存为结果归档。全部选项及退出码以对应子命令的 `--help` 为准。

## 最小 Python 示例

配置层不依赖 GUI，可直接用于脚本和单元测试：

```python
from airvortex.io import load_config_json

config = load_config_json("configs/forward_default.json")
print(config.backend.value)
print(config.drive.target_rpm)
print(config.to_dict()["fluid"]["surface_tension_n_m"])
```

运行完成后，应从归档中的 `metadata` 与 `events` 同时判断能力标签、停止状态、首次接触候选和数值警告，不能只截取一张形状图作为结论。

## 目录结构

```text
.
├── configs/                 # 严格 SI 示例配置
├── deliverables/            # 按 01→04 排列的四份规范交付物与身份索引
├── docs/                    # 旧阶段文稿；保留追溯但不是规范交付
├── examples/                # 实验 CSV 与元数据模板
├── validation/              # 冻结协议、不可覆盖结果和负结果
├── src/airvortex/
│   ├── core/                # 配置、控制状态和共享结果契约
│   ├── backends/            # F1.0 前向后端、HF3D 默认后端和历史 H1
│   ├── numerics/            # 前向变分界面与 HF3D 三维输运/投影工具
│   ├── runtime/             # 后端发现、任务编排与扫描
│   ├── analysis/            # 无量纲数、校准、比较和实验导入
│   ├── io/                  # 严格 JSON 与可校验结果归档
│   ├── gui/                 # Tk/Matplotlib 统一界面
│   └── cli.py               # 命令行入口
├── tests/                   # 标准库 unittest 验证
├── pyproject.toml
└── run_gui.sh
```

## 验证

项目测试使用标准库 `unittest`，不要求额外安装测试框架：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

完整交付还应分别记录：单元测试、零转速/体积守恒等物理基准、网格与时间步研究、后端独立性检查、GUI 烟雾测试，以及冻结后盲实验验证。测试通过只证明相应软件契约成立，不等价于真实装置预测已被验证。

两条规范冻结验证命令为：

```bash
PYTHONPATH=src python3 validation/run_forward_f1_validation.py
PYTHONPATH=src python3 validation/run_hf3d_validation.py
```

它们都创建不可覆盖的 `validation/results/<UTC时间>-.../`。当前 HF3D 正式活动明确返回 `PASS_IMPLEMENTATION__NO_GO_PHYSICAL_PREDICTION`；详见 `validation/README.md`。在完成至少三层空间/时间收敛、磁子和局部气道分辨率门、以及按完整运行/完整几何留出的封存实验盲验以前，项目不得升级为装置级物理 GO。

## 许可

代码以 MIT License 发布；文献、实验数据和第三方材料仍受各自许可约束。
