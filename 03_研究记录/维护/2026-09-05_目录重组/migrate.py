#!/usr/bin/env python3
"""One-off, hash-checked IYPT workspace migration; no physics changes."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

BASELINES = {
    '5': ('4ff0301bee4ed214c935a39ad53c9449f15ec474','5991cb6f09fa9de27627c978051a6a37c2a09cd2167edc0dfcafc3ac6fec8a5f'),
    '10': ('4c43b56f2fedb3197f7d8f0348a3c8a6d132d5f0','0f10682571b14f09b5a0cc627a231ac37dd75f42b74528fec6bf00620a071aa6'),
    '13': ('6ec60b084ab95cfb6ff6fba174a87ab5c4e7e98e','96173086a85b557aef463fc720670ca900129abdb13bd62cf7205b787f6d2646'),
}
OLD = '90_候选工作区'
MANAGEMENT = '00_项目管理'
RECORDS = '03_研究记录'
DATA = '04_实验与数据'
EXPLORATION = '90_探索工作区'
TASK = RECORDS + '/维护/2026-09-05_目录重组'
TEMP_WORKFLOW = '.github/workflows/repository-layout-task.yml'

GUIDE = '''# 工作目录与接续方式

[当前交接](交接说明_current.md) 是唯一动态状态入口；[工作规范](../AGENTS.md) 是唯一活动管理规范。本文只说明目录职责和使用路径，不维护第二份待办或测试台账。

## 目录职责

| 入口 | 放什么 | 不放什么 |
| --- | --- | --- |
| [01_最终理论](../01_最终理论/README.md) | 当前采用的前向物理、数值方法、结构理论及相关协议 | 按日期复制的平行“最终版” |
| [02_模拟程序](../02_模拟程序/README.md) | 实际维护的源码、配置、测试、界面与技术说明 | 无验证却冒充正式后端的候选副本 |
| [03_研究记录](../03_研究记录/README.md) | 专题推导、审查、数值证据、失败记录和冻结快照 | 当前待办的第二份副本 |
| [04_实验与数据](../04_实验与数据/README.md) | 测量来源、原始/派生数据、标定与留出划分、外部数据索引 | 把合成样例冒充真实测量 |
| [90_探索工作区](../90_探索工作区/README.md) | 真正未采用的替代模型和实验性代码 | 所有日常修复的强制中转站 |
| [tools](../tools/) 与 `.github/workflows/` | 持续使用的检查器和只读 CI | 长期遗留的自动改仓库控制器 |

保留 `01_最终理论`、`02_模拟程序` 的现有路径和各语言的包布局，避免破坏脚本与版本引用。“最终”表示当前采用的版本，不表示已经完成数值收敛或实验验证。四个研究层次是物理前向、数值实现、高保真参考、结构性研究；它们不必机械对应四个顶层文件夹。

## 日常工作

开始时阅读 current 的未完成项与验收条件，再按任务读取理论和程序。普通维护直接修改唯一采用版本；只有竞争方案需要探索目录。完成后运行受影响检查，把结果和未完成项写回 current，并提交、推送。

需要沉淀的计算在 `03_研究记录/专题/<主题>/<运行标识>/` 留下配置、源版本、命令、环境、结果与退出原因；维护/审查放在 `03_研究记录/维护/<日期>_<任务>/`。小任务不强制创建新目录，复用已有记录即可。尚未整理的临时结果放 `90_探索工作区/_scratch/`，该路径被 Git 忽略，不是证据备份。

已冻结记录保留原始内容；重新计算或修订结论新建有来源关系的记录。长期原始数据放数据区或可靠外部存储，不只留在会过期的 CI 附件。采用探索成果时更新唯一正式实现，旧候选仅在支撑证据或比较需要时保留，并明确其身份。

## 旧路径与旧规则

旧候选区的 current/history 已移到本目录；旧材料按“专题、维护、归档”移到研究记录区。精确路径与哈希见 [迁移清单](../03_研究记录/维护/2026-09-05_目录重组/migration.json)。旧报告、`ACTIVE.md`、审批口令和机器配置仅反映其原版本，不作为当前工作权限；其中仍有效的科学限制不能删去。

冻结脚本和报告中的旧路径不批量改写，以免破坏原始字节和校验关系。查旧位置可运行 `python tools/resolve_legacy_path.py '<旧仓库相对路径>'`；复现历史计算应检出记录所绑定的原 commit，而不是假定历史脚本自动适用于当前源码。未完成事项继续看 current，不从“归档”或目录名推断研究问题已解决。

`交接说明_history.md` 保持空文件。Git 追溯使用 `git log --follow -- 00_项目管理/交接说明_current.md`；源文件、配置、数据和实际日志优先于摘要。

## 检查入口

在仓库根目录运行：

```bash
python -m unittest discover -s tools -p 'test_repository_docs.py' -v
python tools/check_repository_docs.py
```

求解器安装和回归命令见 [程序入口](../02_模拟程序/README.md)。软件测试、数值收敛、物理标定和留出实验验证分别报告，不用一个 PASS 混为一谈。
'''

DATA_README = '''# 实验与数据

本目录是长期测量与数据管理入口。目前本次整理没有新增实测数据，也没有把既有合成配置或 CFD 结果改称实验数据。[当前能力与缺口](../00_项目管理/交接说明_current.md)、[物理理论与协议](../01_最终理论/README.md)、[程序入口](../02_模拟程序/README.md) 分别给出使用条件。

有实际材料时再按需建立 `原始/`、`派生/`、`标定/`、`留出/`，不预建空的全套目录。原始测量只追加，修正通过派生文件及转换脚本表达；记录单位、坐标系、时间基准、装置条件和来源。视频、图像或大数组保留原文件与校验值，不能只保存手工摘录的结果。

标定与留出按实验批次/工况划分，记录共享测量带来的相关性。合成、模拟和实测必须可区分；既有程序样例仍留在原包中，不复制成两套配置。盲预测前冻结训练范围和判据，留出调参后不再称盲验证。

大文件优先沿用项目已有 Git LFS 或明确的外部存储，并保存 URI/相对位置、SHA-256、大小、用途、获取及恢复方法。LFS 指针不等于数据已经下载或验证；外部数据无法取得时如实记录。不要把全部 CSV、JSON 或图像加入通配忽略规则，避免误丢研究输入。

推导、审核意见和计算报告进入 [研究记录](../03_研究记录/README.md)，正在尝试的替代模型进入 [探索区](../90_探索工作区/README.md)。管理规则统一见 [AGENTS](../AGENTS.md)。
'''

EXPLORATION_README = '''# 探索工作区

这里仅存放尚未采用的替代模型、算法试验和概念验证。普通修复直接维护 [理论](../01_最终理论/README.md) 和 [程序](../02_模拟程序/README.md)，不需要先复制到本目录。

本次整理没有在这里复制任何旧候选作为新的活动实现。既有候选及其原始失败/审查证据完整保存在 [研究记录](../03_研究记录/README.md)。它们是否需要继续研究，由 [当前交接](../00_项目管理/交接说明_current.md) 的任务与验收决定，而不由历史审批口令决定。

新探索按问题命名目录，短说明中写清问题、采用模型、与正式版本的差异、最小验证和当前状态。采用后合并到唯一正式实现；有复现价值的候选保留为有源版本的研究记录，重复的无价值构建物不进 Git。不要维护第二份全仓库待办或无限增长的“正式提升”流水账。

`_scratch/` 用于可删除、可重建的临时输出，默认不入 Git。重要结果应主动转存至研究记录或数据区并绑定源版本；忽略目录不能替代备份。工作规范统一见 [AGENTS](../AGENTS.md)。
'''

RESOLVER = '''#!/usr/bin/env python3
"""Resolve an exact pre-migration repository path without creating aliases."""
from __future__ import annotations
import argparse
import json
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "03_研究记录/维护/2026-09-05_目录重组/migration.json"

def resolve_legacy(path: str, mappings: list[dict]) -> str:
    normalized = path.replace("\\\\", "/").removeprefix("./").rstrip("/")
    if PurePosixPath(normalized).is_absolute() or ".." in PurePosixPath(normalized).parts:
        raise ValueError("use a repository-relative path without '..'")
    for item in sorted(mappings, key=lambda x: len(x["old"]), reverse=True):
        old, new = item["old"], item["new"]
        if normalized == old or normalized.startswith(old + "/"):
            return new + normalized[len(old):]
    raise ValueError("path is not covered by this migration")

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    args = parser.parse_args()
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        target = resolve_legacy(args.path, manifest["prefixes"])
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(2, f"{exc}\\n")
    if not (ROOT / target).exists():
        parser.exit(2, f"mapped target is not present: {target}\\n")
    print(target)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
'''

EXTRA_TESTS = '''
class LayoutAndMigrationTests(unittest.TestCase):
    def test_required_layout_and_empty_history(self):
        from check_repository_docs import check_layout, REQUIRED_DIRS
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in REQUIRED_DIRS:
                (root / name).mkdir()
            (root / "00_项目管理/交接说明_history.md").write_bytes(b"")
            self.assertEqual(check_layout(root), [])
            (root / "00_项目管理/交接说明_history.md").write_text("restored")
            self.assertTrue(check_layout(root))
    def test_missing_layout_is_reported(self):
        from check_repository_docs import check_layout
        with tempfile.TemporaryDirectory() as temp:
            self.assertTrue(check_layout(Path(temp)))
    def test_old_active_workspace_is_not_recreated(self):
        from check_repository_docs import check_layout, REQUIRED_DIRS
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in REQUIRED_DIRS:
                (root / name).mkdir()
            (root / "00_项目管理/交接说明_history.md").write_bytes(b"")
            (root / "90_候选工作区").mkdir()
            self.assertTrue(check_layout(root))
    def test_nested_program_readmes_are_checked_not_frozen_records(self):
        from check_repository_docs import entry_documents
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("02_模拟程序/pkg/README.md", "03_研究记录/归档/README.md"):
                p = root / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text("# Entry\\n")
            docs = entry_documents(root)
            self.assertIn("02_模拟程序/pkg/README.md", docs)
            self.assertNotIn("03_研究记录/归档/README.md", docs)
    def test_exact_and_longest_prefix_mapping(self):
        from resolve_legacy_path import resolve_legacy
        maps = [{"old":"old/current", "new":"records/topics"},
                {"old":"old/current/date", "new":"records/maintenance/date"}]
        self.assertEqual(resolve_legacy("old/current/a.txt",maps), "records/topics/a.txt")
        self.assertEqual(resolve_legacy("./old/current/date/a.txt",maps), "records/maintenance/date/a.txt")
    def test_mapping_boundary_is_not_substring_replacement(self):
        from resolve_legacy_path import resolve_legacy
        with self.assertRaises(ValueError):
            resolve_legacy("old/currently/a", [{"old":"old/current","new":"new"}])
    def test_mapping_rejects_escape(self):
        from resolve_legacy_path import resolve_legacy
        for bad in ("../old/current/a", "/old/current/a"):
            with self.assertRaises(ValueError):
                resolve_legacy(bad, [{"old":"old/current", "new":"new"}])
'''

def command(*args: str) -> str:
    return subprocess.check_output(args).decode().strip()

def write(path: str, text: str) -> None:
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text,encoding='utf-8')

def main() -> None:
    n = sys.argv[1]
    expected_ref, expected_digest = BASELINES[n]
    paths = subprocess.check_output(['git','ls-files','-z']).decode().split('\0')[:-1]
    digest = hashlib.sha256()
    for path in sorted(paths):
        if path == TEMP_WORKFLOW: continue
        digest.update(path.encode()+b'\0'+hashlib.sha256(Path(path).read_bytes()).digest())
    if digest.hexdigest() != expected_digest:
        raise RuntimeError('repository content changed since review; re-read before migration')
    for folder in [MANAGEMENT,RECORDS,DATA,EXPLORATION]:
        if Path(folder).exists():raise RuntimeError('destination already exists: '+folder)
    old_current=Path(OLD+'/交接说明_current.md').read_text()
    old_agents=Path('AGENTS.md').read_text()
    dated=sorted(p.name for p in Path(OLD+'/current').iterdir() if re.match(r'\d{4}-\d{2}-\d{2}_',p.name))
    prefixes=[{'old':OLD+'/交接说明_current.md','new':MANAGEMENT+'/交接说明_current.md'},
              {'old':OLD+'/交接说明_history.md','new':MANAGEMENT+'/交接说明_history.md'},
              {'old':OLD+'/README.md','new':TASK+'/旧候选区说明.md'}]
    prefixes += [{'old':OLD+'/current/'+d,'new':RECORDS+'/维护/'+d} for d in dated]
    prefixes += [{'old':OLD+'/current','new':RECORDS+'/专题'},
                 {'old':OLD+'/archive','new':RECORDS+'/归档'}]
    def mapped(p: str) -> str:
        for rule in sorted(prefixes,key=lambda x:len(x['old']),reverse=True):
            if p==rule['old'] or p.startswith(rule['old']+'/'):
                return rule['new']+p[len(rule['old']):]
        return p
    records=[]
    for p in sorted(x for x in paths if x.startswith(OLD+'/')):
        target=mapped(p)
        data=Path(p).read_bytes()
        Path(target).parent.mkdir(parents=True,exist_ok=True)
        shutil.move(p,target)
        if p.endswith('/交接说明_current.md'):continue
        records.append({'old':p,'new':target,'sha256':hashlib.sha256(data).hexdigest(),'size':len(data)})
    # All remaining directories are empty: never remove an unseen file.
    for p in sorted(Path(OLD).rglob('*'),key=lambda x:len(x.parts),reverse=True):
        if p.is_dir():p.rmdir()
        else:raise RuntimeError('unmapped file in legacy directory: '+str(p))
    Path(OLD).rmdir()
    for folder in [MANAGEMENT,RECORDS,DATA,EXPLORATION]:Path(folder).mkdir(exist_ok=True)
    assert Path(MANAGEMENT+'/交接说明_history.md').read_bytes()==b''

    # Stable activity rules: retain scientific constraints, not historical permissions.
    agents=old_agents.replace(OLD+'/交接说明_current.md',MANAGEMENT+'/交接说明_current.md')
    agents=agents.replace('[候选与证据目录]','[研究目录]')
    agents=agents.replace('候选区用于真正的替代模型、实验性实现与独立证据','探索区用于真正的替代模型与实验性实现，独立证据进入研究记录区')
    agents=agents.replace('采用后指向唯一正式实现，并标明候选状态。','采用后更新唯一正式实现，并标明旧候选的证据身份。')
    agents=re.sub(r'`90_候选工作区/current/`[^\n]*',
                  '目录归属与旧路径迁移统一见 [目录指南](00_项目管理/README.md)。研究记录中的历史规范不覆盖本文件；不会因目录搬迁而恢复旧的只读、审批或暂停口令。',agents)
    agents=agents.replace('先读本文件与','先读本文件、[目录指南](00_项目管理/README.md) 与')
    agents=agents.replace('候选区只','探索区只')
    write('AGENTS.md',agents)
    write(MANAGEMENT+'/README.md',GUIDE)
    write(DATA+'/README.md',DATA_README)
    write(EXPLORATION+'/README.md',EXPLORATION_README)

    # Root introduction retains all physical assumptions, with one dynamic state source.
    readme=Path('README.md').read_text()
    intro='''## 项目定位与阅读入口

这是长期个人科研项目，持续积累理论、实现、数值证据和实验预测能力。

[当前交接](00_项目管理/交接说明_current.md) 查看真实进度、未解决问题和下一步；[目录指南](00_项目管理/README.md) 定位材料；[工作规范](AGENTS.md) 规定维护方式。主页保留稳定研究介绍与物理假设，不重复维护动态测试计数、阶段口令或待办。

'''
    readme=re.sub(r'## 项目定位与阅读入口\n.*?(?=## 研究内容\n)',intro,readme,flags=re.S)
    structure='''## 仓库结构

```text
00_项目管理/       当前交接、空 history、目录指南
01_最终理论/       当前采用的物理、数值和结构理论
02_模拟程序/       稳定程序入口、配置、测试与技术文档
03_研究记录/       专题、维护记录与冻结归档
04_实验与数据/     测量、标定、留出数据与来源索引
90_探索工作区/     未采用的替代模型；临时输出用 _scratch/
tools/            轻量检查和维护工具
```

[理论入口](01_最终理论/README.md)、[程序入口](02_模拟程序/README.md)、[研究证据](03_研究记录/README.md)、[实验数据](04_实验与数据/README.md)、[探索区](90_探索工作区/README.md) 各自承担一种职责。旧目录的精确迁移关系见目录指南；研究记录不因搬迁而获得新的科学资格。
'''
    readme=re.sub(r'## 仓库结构\n.*',structure,readme,flags=re.S)
    write('README.md',readme)

    # Rebase live handoff links; keep earlier capabilities and backlog verbatim.
    current=old_current.split('## 本轮管理修改与验证')[0].rstrip()+'\n\n'
    current=current.replace(OLD+'/交接说明_current.md',MANAGEMENT+'/交接说明_current.md')
    current=re.sub(r'\]\(current/([^)]*)\)',lambda m:'](../'+mapped(OLD+'/current/'+m.group(1))+')',current)
    current=re.sub(r'\]\(archive/([^)]*)\)',lambda m:'](../'+mapped(OLD+'/archive/'+m.group(1))+')',current)
    current += '''## 本轮目录整理与验证

本轮实际移动交接与旧候选材料，拆分项目管理、研究记录、实验数据与探索工作区；不是只改规范。理论及程序主体路径保留。原研究证据逐文件保持字节内容，旧审批口令不再作为活动管理规则，科学失败与未完成项仍有效。没有重算历史物理结果、提高定量资格或引入新的物理模型。

旧路径、原始内容哈希和迁移范围见 [迁移清单](../03_研究记录/维护/2026-09-05_目录重组/migration.json)。迁移脚本和检查日志保存在同一记录目录；history 仍为零字节。5 号结构分析旧脚本的临时输出改到探索区 `_scratch/`；13 号冻结证据测试改读新归档位置。维护规则集中于根 AGENTS，目录指南只负责材料归属。

本轮实际执行及未执行项见 [验证日志](../03_研究记录/维护/2026-09-05_目录重组/validation.txt)。入口检查覆盖新目录、current、history 和程序导航；旧证据以原始源版本复现，不批量改写冻结脚本中的路径。LFS 指针校验不代表大文件本体已下载验证。上文研究任务继续有效，不因目录整理完成而关闭。
'''
    if n!='5':current=current.replace('5 号结构分析旧脚本的临时输出改到探索区 `_scratch/`；','')
    if n!='13':current=current.replace('13 号冻结证据测试改读新归档位置。','')
    current=current.replace('`_scratch/`；维护规则','`_scratch/`。维护规则')
    write(MANAGEMENT+'/交接说明_current.md',current)

    # Program and theoretical navigation: local technical docs do not invent permissions.
    for path in [Path('01_最终理论/README.md'),Path('02_模拟程序/README.md')]:
        text=path.read_text()
        text=text.replace('../'+OLD+'/交接说明_current.md','../'+MANAGEMENT+'/交接说明_current.md')
        text+='\n维护权限与交接方式统一见 [根工作规范](../AGENTS.md) 和 [目录指南](../00_项目管理/README.md)。本文件只描述科学/技术接口，不另设审批要求。\n'
        write(str(path),text)
    if n=='5':
        status=Path('02_模拟程序/02_第一性原理高保真模拟器/Y-Pendulum-Simulator-v1.0.0-rc1/STATUS.md')
        text=status.read_text().replace('- 不再向 RC1 直接追加新物理、接触功能或结果驱动修补。','- 旧 RC1 结果保持原版本绑定；后续修改不能继承旧版本未获得的数值或实验资格。')
        text=text.split('## 后续修改门')[0]+'''## 维护与版本身份

常规诊断、修复和测试按根 AGENTS 在用户任务范围内进行，不再另设“默认只读/逐次授权”的管理门。原 RC1 的历史结果和输入保持其原 commit 绑定；修改方程、接口或功能后更新相关版本/模型说明并重新验证，不能仍以旧证据覆盖新实现。没有模型语义变化的文档或路径修复不需要虚构新模型身份。

程序测试不能替代空间/时间收敛、连续守卫或装置验证。`HF_NUMERICAL_QUALIFICATION_NO_GO` 仍有效；这限制结论，不禁止开展改善它的工作。
'''
        write(str(status),text)
        script=Path('02_模拟程序/03_结构性理论分析程序/torus_structure_analysis.py')
        text=script.read_text().replace('/ "90_候选工作区"\n        / "current"\n        / "验证与复现"','/ "90_探索工作区"\n        / "_scratch"')
        write(str(script),text)
    if n=='10':
        p=Path('02_模拟程序/04_20260905_数值一致性修订.md')
        write(str(p),p.read_text().replace('../'+OLD+'/交接说明_current.md','../'+MANAGEMENT+'/交接说明_current.md'))
    if n=='13':
        p=Path('02_模拟程序/tests/a8s-archived-evidence.test.ts')
        write(str(p),p.read_text().replace('../'+OLD+'/archive/','../'+RECORDS+'/归档/'))
        p=Path('tools/check_markdown_math.py')
        write(str(p),p.read_text().replace('ROOT / "90_候选工作区" / "交接说明_current.md"','ROOT / "00_项目管理" / "交接说明_current.md"'))

    # One lightweight checker, expanded to all live entry documents.
    checker=Path('tools/check_repository_docs.py').read_text()
    start=checker.index('ENTRY_DOCS = '); end=checker.index('FENCE = ',start)
    checker=checker[:start]+'''REQUIRED_DIRS = ("00_项目管理", "01_最终理论", "02_模拟程序", "03_研究记录", "04_实验与数据", "90_探索工作区")
ENTRY_DOCS = ("AGENTS.md", "README.md", "00_项目管理/README.md", "00_项目管理/交接说明_current.md", "01_最终理论/README.md", "02_模拟程序/README.md", "03_研究记录/README.md", "04_实验与数据/README.md", "90_探索工作区/README.md")

def entry_documents(root: Path) -> tuple[str, ...]:
    docs = set(ENTRY_DOCS)
    for path in (root / "02_模拟程序").rglob("README.md"):
        if not any(part in {"node_modules", ".venv", "dist", "__pycache__"} for part in path.parts):
            docs.add(path.relative_to(root).as_posix())
    return tuple(sorted(docs))

def check_layout(root: Path) -> list[str]:
    errors = [f"missing directory: {p}" for p in REQUIRED_DIRS if not (root / p).is_dir()]
    history = root / "00_项目管理/交接说明_history.md"
    if not history.is_file() or history.stat().st_size != 0:
        errors.append("history must exist and remain empty")
    if (root / "90_候选工作区").exists():
        errors.append("legacy active workspace recreated; use the directory guide")
    return errors

'''+checker[end:]
    checker=checker.replace('errors = [error for item in ENTRY_DOCS for error in check_document(ROOT, item)]','docs = entry_documents(ROOT)\n    errors = check_layout(ROOT) + [error for item in docs for error in check_document(ROOT, item)]')
    checker=checker.replace('{len(ENTRY_DOCS)} documents; local targets and fences only','{len(docs)} documents; layout, local targets and fences only')
    write('tools/check_repository_docs.py',checker)
    write('tools/resolve_legacy_path.py',RESOLVER)
    tests=Path('tools/test_repository_docs.py').read_text().split('if __name__ == "__main__":')[0]
    write('tools/test_repository_docs.py',tests+EXTRA_TESTS+'\nif __name__ == "__main__":\n    unittest.main()\n')
    ignore=Path('.gitignore').read_text().rstrip()+'\n\n# Disposable exploration output; important evidence must be retained explicitly.\n/90_探索工作区/_scratch/\n'
    write('.gitignore',ignore)

    record_text='''# 研究记录与证据索引

这里保存专题研究、审查/维护和冻结证据，不维护当前待办。[当前交接](../00_项目管理/交接说明_current.md) 决定接续顺序，[目录指南](../00_项目管理/README.md) 说明使用方法。

旧候选代码、`ACTIVE.md` 和“唯一允许/待授权”等文字均保留为原始记录，不是当前管理限制，也不因搬迁而变成已完成或已验证成果。数值失败、模型适用边界和实验缺口仍有效。正式实现以 [程序入口](../02_模拟程序/README.md) 为准，不在这里维护一份平行正式包。

## 维护与审查

'''
    for p in sorted(Path(RECORDS+'/维护').iterdir()):
        if p.is_dir():record_text+=f'- [{p.name}](维护/{p.name}/)：该次修改、检查或审查的原始材料。\n'
    if Path(RECORDS+'/专题').exists():
        record_text+='\n## 既有专题材料\n\n保留原专题之间的历史顺序和内部组织；目录内的源码仅代表所绑定版本，不等于当前采用后端。\n\n'
        for p in sorted(Path(RECORDS+'/专题').iterdir()):
            if p.name=='.gitkeep':continue
            name=p.name;record_text+=f'- [{name}](专题/{name}{"/" if p.is_dir() else ""})\n'
    if Path(RECORDS+'/归档').exists():
        record_text+='\n## 冻结基线\n\n'
        for p in sorted(Path(RECORDS+'/归档').iterdir()):
            record_text+=f'- [{p.name}](归档/{p.name}{"/" if p.is_dir() else ""})：原始归档、字节及 LFS 指针保持不变。\n'
    record_text+='\n## 新记录与历史复现\n\n新专题使用 `专题/<主题>/<运行标识>/`，新维护使用 `维护/<日期>_<任务>/`，按实际需要创建。保存源版本、输入、环境、命令、结果与退出原因，不强制每个小任务生成一套新文件。历史脚本按原 commit 复现；[迁移清单](维护/2026-09-05_目录重组/migration.json) 只改变位置，不把旧证据重新认证为新版本结果。\n'
    write(RECORDS+'/README.md',record_text)

    manifest={'schema':'iypt-layout-migration-v1','date':'2026-09-05','repository_problem':n,
              'reviewed_snapshot_commit':expected_ref,'reviewed_content_sha256':expected_digest,
              'executed_from_commit':command('git','rev-parse','HEAD'),'prefixes':prefixes,
              'preserved_files':records,
              'note':'Hashes describe original tracked bytes, including LFS pointers, not hydrated LFS object validation. Current handoff was relocated and updated. Physics equations and numerical kernels were not modified.'}
    write(TASK+'/migration.json',json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    write(TASK+'/migrate.py',Path(__file__).read_text())
    write(TASK+'/validation.txt','Repository layout migration validation\nExecution results are recorded by the validation command before publication.\n')
    for record in records:
        data=Path(record['new']).read_bytes()
        assert len(data)==record['size'] and hashlib.sha256(data).hexdigest()==record['sha256'], record['new']
    # Force-stage only originally tracked relocated files: *.log/LFS are not lost to ignores.
    force_paths='\0'.join(record['new'] for record in records)+'\0'
    Path('/tmp/iypt-relocated-paths').write_bytes(force_paths.encode())
    print(f'MIGRATION PASS: {n}; {len(records)} relocated original files byte-identical; history empty')

if __name__=='__main__': main()
