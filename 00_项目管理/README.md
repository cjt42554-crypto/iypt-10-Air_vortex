# 目录与接续

[当前交接](交接说明_current.md) 是唯一活动状态入口，[AGENTS](../AGENTS.md) 是工作规范。[任务单与验收](理论程序对齐_任务单与验收.md) 保留尚未全部完成的理论—程序对齐要求。

| 目录 | 保留内容 |
| --- | --- |
| [01_最终理论](../01_最终理论/README.md) | 当前采用的物理、数值和结构理论 |
| [02_模拟程序](../02_模拟程序/README.md) | 唯一正式源码、配置、测试、界面及技术说明 |
| [03_研究记录](../03_研究记录/README.md) | 关键结果、失败证据、必要输入与独立核查 |
| [04_实验与数据](../04_实验与数据/README.md) | 测量来源、原始/派生数据、标定与留出 |
| [tools](../tools/) 与 `.github/workflows/` | 持续使用的轻量检查和只读 CI |

开始时先读 current，再按问题读取理论、源码和证据。普通维护直接修改采用版本；确有竞争模型时再建立探索目录，不预建空目录。临时输出放 `/tmp` 或受忽略的运行目录；支撑结论的最小输入、日志和结果需进入研究记录。

研究记录按专题或维护日期定位，但不维护第二份 current。冻结结果绑定原源码、输入、命令和环境；复算另存，不把历史 PASS 套给当前实现。

旧候选路径映射保留在 [migration.json](../03_研究记录/维护/2026-09-05_目录重组/migration.json)。`python3 tools/resolve_legacy_path.py '<旧相对路径>'` 可查仍在当前目录中的材料；已精简材料按 [历史恢复说明](../03_研究记录/README.md#历史恢复) 读取原 Git 版本。旧报告中的审批或暂停文字仅是当时记录。

`交接说明_history.md` 按要求保持空白。历史用 Git 查询：

```bash
git log --oneline -- '00_项目管理/交接说明_current.md'
git show <commit>:'00_项目管理/交接说明_current.md'
```

入口检查：

```bash
python3 -m unittest discover -s tools -p 'test_repository_docs.py' -v
python3 tools/check_repository_docs.py
```

程序运行与回归命令见程序 README。软件、数值、标定和实验结论分别报告。
