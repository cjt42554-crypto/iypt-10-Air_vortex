"""Fail-closed, one-shot promotion of the frozen HF3D CFL controller patch.

The runner validates the exact patch and the promoted formal source, writes the
formal promotion evidence and handoff, commits directly to main, and deletes all
transient promotion machinery in the successful commit.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from typing import Any
from zoneinfo import ZoneInfo


SELF = Path(__file__).resolve()
ROOT = SELF.parents[4]
FORMAL_DIR = ROOT / "02_模拟程序"
CANDIDATE_ROOT = ROOT / "90_候选工作区/current/01_HF3D数值资格"
FORMAL_CANDIDATE = CANDIDATE_ROOT / "formal_candidate"
PATCH = FORMAL_CANDIDATE / "0001-Add-transactional-HF3D-CFL-controller-candidate.patch"
OLD_HELPER = FORMAL_CANDIDATE / "promote_formal_once.py"
WORKFLOW = ROOT / ".github/workflows/one-shot-hf3d-formal-promotion.yml"
REPORT_JSON = FORMAL_CANDIDATE / "formal_promotion_report.json"
REPORT_MD = FORMAL_CANDIDATE / "formal_promotion_report.md"
HANDOFF_CURRENT = ROOT / "90_候选工作区/交接说明_current.md"
HANDOFF_HISTORY = ROOT / "90_候选工作区/交接说明_history.md"

FORMAL_BASELINE_COMMIT = "9b79b1ca39461ada176c6e7de5855a935b191983"
EXPECTED_PATCH_SHA256 = "ee978d7bc9e1b27278b6f89a44c03f789780ba1cb11d648989ca8a3ee29ef4be"
EXPECTED_SOURCE_SHA256 = "03ebc4dcd194d0ffadc7f24228720d775dc7d888ceb5a9737816368626482010"
EXPECTED_FORMAL_FILES = {
    "02_模拟程序/src/airvortex/backends/high_fidelity_3d.py",
    "02_模拟程序/src/airvortex/core/config.py",
    "02_模拟程序/src/airvortex/numerics/__init__.py",
    "02_模拟程序/src/airvortex/numerics/projection3d.py",
    "02_模拟程序/src/airvortex/numerics/projection3d_gpu.py",
    "02_模拟程序/tests/test_config_io.py",
    "02_模拟程序/tests/test_gpu_projection.py",
    "02_模拟程序/tests/test_high_fidelity_3d.py",
}
CLAIM_CEILING = (
    "FORMAL_IMPLEMENTATION_PROMOTED_AND_CPU_REVALIDATED__"
    "CUDA_FORMAL_REPLAY_PENDING__Q1R2_NOT_FROZEN__NO_GO_PHYSICAL_PREDICTION"
)


def run(
    *args: str,
    cwd: Path = ROOT,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(args), flush=True)
    completed = subprocess.run(
        args,
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )
    if capture and completed.stdout:
        print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    return completed


def output(*args: str, cwd: Path = ROOT) -> str:
    return run(*args, cwd=cwd, capture=True).stdout.strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def changed_names(*, cached: bool = False, scope: str | None = None) -> set[str]:
    command = ["git", "diff"]
    if cached:
        command.append("--cached")
    command.append("--name-only")
    if scope is not None:
        command.extend(["--", scope])
    return {line for line in output(*command).splitlines() if line}


def verify_text(paths: set[str]) -> None:
    errors: list[str] = []
    for raw in sorted(paths):
        path = ROOT / raw
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "\t" in line:
                errors.append(f"{raw}:{number}: tab")
            if line.rstrip(" ") != line:
                errors.append(f"{raw}:{number}: trailing space")
    if errors:
        raise RuntimeError("\n".join(errors))


def validate_formal_tests() -> dict[str, Any]:
    completed = run(
        sys.executable,
        "-m",
        "pytest",
        "02_模拟程序/tests",
        "-q",
        "-rs",
        capture=True,
    )
    text = completed.stdout
    summary = re.search(
        r"(?P<passed>\d+) passed, (?P<skipped>\d+) skipped, "
        r"(?P<subtests>\d+) subtests passed in",
        text,
    )
    if summary is None:
        raise RuntimeError("pytest terminal summary was not recognised")
    passed = int(summary.group("passed"))
    skipped = int(summary.group("skipped"))
    subtests_passed = int(summary.group("subtests"))
    if (passed, skipped, subtests_passed) != (176, 5, 52):
        raise RuntimeError(
            "unexpected formal test summary: "
            f"passed={passed}, skipped={skipped}, subtests={subtests_passed}"
        )
    gpu_skip_count = text.count(
        "GPUProjectionUnavailable: CuPy CUDA runtime is unavailable"
    )
    tk_skip_count = text.count("Tk display unavailable: no display name")
    if gpu_skip_count != 4 or tk_skip_count != 1:
        raise RuntimeError(
            "unexpected skip classification: "
            f"gpu={gpu_skip_count}, tk_display={tk_skip_count}"
        )
    return {
        "top_level_collected": passed + skipped,
        "passed": passed,
        "skipped": skipped,
        "failed": 0,
        "subtests_passed": subtests_passed,
        "skip_classification": {
            "cupy_cuda_runtime_unavailable": gpu_skip_count,
            "tk_display_unavailable": tk_skip_count,
        },
        "runner_exit_code": completed.returncode,
    }


def validate_candidate_tests() -> dict[str, int]:
    completed = run(
        sys.executable,
        "-Werror",
        "-m",
        "unittest",
        "discover",
        "-s",
        "tests",
        "-v",
        cwd=CANDIDATE_ROOT,
        capture=True,
    )
    match = re.search(r"Ran (\d+) tests? in", completed.stdout)
    if match is None or int(match.group(1)) != 24:
        raise RuntimeError("candidate qualification suite did not run exactly 24 tests")
    if not re.search(r"(?:^|\n)OK(?:\n|$)", completed.stdout):
        raise RuntimeError("candidate qualification suite did not end in OK")
    return {"run": 24, "passed": 24, "failed": 0}


def validate_self_check() -> tuple[dict[str, Any], dict[str, int]]:
    completed = run(
        sys.executable,
        "-m",
        "airvortex",
        "self-check",
        capture=True,
    )
    payload = json.loads(completed.stdout)
    checks = payload.get("checks")
    if payload.get("ok") is not True or not isinstance(checks, dict):
        raise RuntimeError(f"formal self-check failed: {payload}")
    if len(checks) != 6 or not all(item.get("ok") is True for item in checks.values()):
        raise RuntimeError(f"formal self-check scope/result changed: {payload}")
    return payload, {"run": 6, "passed": 6, "failed": 0}


def require_frozen_evidence() -> tuple[dict[str, Any], dict[str, Any]]:
    candidate_manifest = json.loads(
        (FORMAL_CANDIDATE / "candidate_manifest.json").read_text(encoding="utf-8")
    )
    fine_report = json.loads(
        (FORMAL_CANDIDATE / "fine_window_report.json").read_text(encoding="utf-8")
    )
    if candidate_manifest.get("candidate_patch", {}).get("sha256") != EXPECTED_PATCH_SHA256:
        raise RuntimeError("candidate manifest patch identity mismatch")
    if fine_report.get("candidate_patch", {}).get("sha256") != EXPECTED_PATCH_SHA256:
        raise RuntimeError("fine-window report patch identity mismatch")
    if (
        fine_report.get("candidate_patch", {}).get("expected_source_tree_sha256")
        != EXPECTED_SOURCE_SHA256
    ):
        raise RuntimeError("fine-window expected source identity mismatch")
    source = fine_report.get("archive", {}).get("source_identity", {})
    if source.get("source_tree_sha256_start") != EXPECTED_SOURCE_SHA256:
        raise RuntimeError("fine-window starting source identity mismatch")
    if source.get("source_tree_sha256_end") != EXPECTED_SOURCE_SHA256:
        raise RuntimeError("fine-window ending source identity mismatch")
    if fine_report.get("gate_pass") is not True:
        raise RuntimeError("frozen fine-window evidence is not a passing gate")
    return candidate_manifest, fine_report


def write_reports(
    *,
    trigger_commit: str,
    source_identity: dict[str, Any],
    formal_tests: dict[str, Any],
    candidate_tests: dict[str, int],
    self_check_payload: dict[str, Any],
    self_check_counts: dict[str, int],
    candidate_manifest: dict[str, Any],
    fine_report: dict[str, Any],
) -> None:
    now = datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")
    report: dict[str, Any] = {
        "schema_version": 1,
        "created_at": now,
        "status": "FORMAL_PROMOTION_CPU_PASS__CUDA_FORMAL_REPLAY_PENDING",
        "authorization": {
            "received": True,
            "instruction_summary": "按照交付文件完成任务",
            "accepted_candidate_defaults": {
                "cfl_controller_target_fraction": 0.95,
                "maximum_cfl_step_retries": 4,
                "dense_diagnostics": True,
            },
            "frozen_candidate_manifest_preserved": True,
            "historical_manifest_note": (
                "candidate_manifest.json was frozen before authorization; its historical "
                "authorization_received=false field was intentionally not rewritten"
            ),
        },
        "repository": {
            "branch": "main",
            "formal_baseline_commit": FORMAL_BASELINE_COMMIT,
            "workflow_trigger_commit": trigger_commit,
            "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
        },
        "candidate_patch": {
            "path": PATCH.relative_to(ROOT).as_posix(),
            "sha256": EXPECTED_PATCH_SHA256,
            "applied_exactly_once": True,
            "hard_cfl_gate_changed": False,
        },
        "changes": {
            "formal_theory_modified": False,
            "formal_program_modified": True,
            "formal_program_files": sorted(EXPECTED_FORMAL_FILES),
            "frozen_candidate_evidence_rewritten": False,
        },
        "source_identity": {
            "expected_sha256": EXPECTED_SOURCE_SHA256,
            "observed": source_identity,
            "match": source_identity.get("value") == EXPECTED_SOURCE_SHA256,
        },
        "validation": {
            "formal_baseline_unchanged_before_apply": "PASS",
            "formal_theory_unchanged": "PASS",
            "patch_sha256": "PASS",
            "git_apply_check": "PASS",
            "reverse_apply_check_after_apply": "PASS",
            "patch_scope_exact": "PASS",
            "compileall": "PASS",
            "formal_full_test_suite": formal_tests,
            "candidate_qualification_tests": candidate_tests,
            "formal_self_check": self_check_counts,
            "formal_self_check_names": sorted(self_check_payload["checks"]),
            "git_diff_check": "PASS",
            "whitespace_and_tab_scan": "PASS",
            "isolated_cuda_tests_on_promoted_formal_path": (
                "NOT_EXECUTED__CUPY_CUDA_RUNTIME_UNAVAILABLE"
            ),
            "promoted_formal_0_013s_cuda_window": (
                "NOT_EXECUTED__CUPY_CUDA_RUNTIME_UNAVAILABLE"
            ),
            "gui_layout_test": "SKIPPED__TK_DISPLAY_UNAVAILABLE",
        },
        "environment": {
            "python": platform.python_version(),
            "pytest": importlib.metadata.version("pytest"),
            "numpy": importlib.metadata.version("numpy"),
            "scipy": importlib.metadata.version("scipy"),
            "matplotlib": importlib.metadata.version("matplotlib"),
            "runner": "GitHub-hosted Ubuntu CPU runner without CUDA or display",
        },
        "prior_candidate_cuda_evidence": {
            "isolated_cuda_tests": candidate_manifest.get("validation", {}).get(
                "isolated_cuda_tests"
            ),
            "fine_window_status": fine_report.get("status"),
            "fine_window_gate_pass": fine_report.get("gate_pass"),
            "fine_window_source_sha256_start": fine_report["archive"][
                "source_identity"
            ]["source_tree_sha256_start"],
            "fine_window_source_sha256_end": fine_report["archive"][
                "source_identity"
            ]["source_tree_sha256_end"],
            "same_executable_source_identity_as_promoted_formal": True,
            "evidence_boundary": (
                "The byte-identical executable source preserves the relevance of the prior "
                "candidate CUDA evidence, but this run did not replay it from the promoted "
                "formal directory."
            ),
        },
        "not_performed": [
            "isolated CUDA tests from the promoted formal path",
            "0.013 s fine-grid CUDA replay from the promoted formal path",
            "Q1R2 common-time-step refreeze",
            "Q1R2 multi-grid and multi-time-step execution",
            "numerical convergence qualification",
            "physical validation",
            "experimental validation",
        ],
        "q1r2_status": "NOT_FROZEN",
        "claim_ceiling": CLAIM_CEILING,
    }
    REPORT_JSON.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    files = "\n".join(f"- `{path}`" for path in sorted(EXPECTED_FORMAL_FILES))
    REPORT_MD.write_text(
        f"""# HF3D 事务性 CFL 控制器正式提升报告

状态：`{report['status']}`。

用户已授权按当前交接文件完成唯一待续任务。冻结补丁已原样应用到正式程序，采用控制目标比例 `0.95`、最多 `4` 次事务重试和新增稠密诊断字段；CFL 硬门未放宽。正式理论未修改。

## 正式程序变更

{files}

提升后的正式可执行源码树 SHA-256 为 `{source_identity['value']}`，与冻结候选 CUDA 证据绑定的源码身份完全一致。

## 本轮验证

| 项目 | 结果 |
|---|---|
| 冻结补丁 SHA-256、应用检查和反向检查 | PASS |
| 正式变更范围 | 精确为 5 个源码文件和 3 个测试文件 |
| 正式可执行源码树身份 | PASS |
| 正式全量测试 | 176 passed，5 skipped，0 failed；另有 52 个 subtests passed |
| 跳过原因 | 4 项缺少 CuPy/CUDA；1 项缺少 Tk 图形显示 |
| 候选资格测试 | 24/24 passed |
| 正式 self-check | 6/6 passed |
| compileall、git diff --check、尾随空格/制表符扫描 | PASS |

## 证据边界

候选阶段已有隔离 CUDA 测试和真实 `136 × 136 × 120`、`0.013 s` 细网格窗口通过；其可执行源码树身份与本次提升后的正式源码逐字节一致。因此这些证据仍能支持“该实现曾在 CUDA 上通过候选门”，但不能写成“已从提升后的正式目录重新执行”。本轮 GitHub runner 没有 CuPy/CUDA，也没有图形显示，故相应测试按条件跳过。

Q1R2 仍为 `NOT_FROZEN`。旧登记步长 `7.5e-6 s` 的候选命中比例为 `0.928856`，低于 `0.95`；候选窗口中的最小联合限步值 `5.82815205187083e-6 s` 也不是冻结共同步长。不得直接采用任一数值冻结 Q1R2。

## 当前结论上限

`{CLAIM_CEILING}`

这不是多网格或多时间步收敛通过，不是物理验证，也不是实验验证；当前仍不得给出装置级定量预测。
""",
        encoding="utf-8",
    )

    HANDOFF_CURRENT.write_text(
        f"""# 当前交接

## 当前唯一待续任务

HF3D 事务性 CFL 控制器已获用户授权，并按冻结补丁原样提升到正式程序。当前状态为 `FORMAL_PROMOTION_CPU_PASS__CUDA_FORMAL_REPLAY_PENDING`。正式理论 `01_最终理论/**` 未修改；正式程序的 5 个源码文件和 3 个测试文件已修改。

下一步必须在具备 CuPy/CUDA 的环境中，从提升后的正式目录重新执行隔离 GPU 测试和 `0.013 s` 细网格失败窗口。两项均通过后，才能依据正式源码各空间层的真实稠密步长轨迹重新设计空间共同登记步长和时间递减层；在此之前不得冻结或运行 Q1R2。

## 必要入口

- 正式提升报告：`90_候选工作区/current/01_HF3D数值资格/formal_candidate/formal_promotion_report.md`、`formal_promotion_report.json`；
- 冻结补丁：`90_候选工作区/current/01_HF3D数值资格/formal_candidate/0001-Add-transactional-HF3D-CFL-controller-candidate.patch`；
- 原提升审查：`90_候选工作区/current/01_HF3D数值资格/formal_candidate/promotion_review.md`；
- 真实候选窗口：`formal_candidate/fine_window_report.md`、`fine_window_report.json`；
- 正式窗口配置与核验器：`formal_candidate/fine_window_pressure_1e-7.json`、`formal_candidate/analyse_fine_window.py`。

## 冻结身份

- 本次正式提升触发提交：`{trigger_commit}`；
- 冻结补丁 SHA-256：`{EXPECTED_PATCH_SHA256}`；
- 提升后正式可执行源码树 SHA-256：`{source_identity['value']}`；
- 控制目标比例：`0.95`；最多事务重试：`4`；CFL 硬门未放宽。

## 本轮已完成

1. 确认正式程序和正式理论自冻结基线以来未被其他提交修改，并核验补丁 SHA-256。
2. 在 `02_模拟程序/` 原样应用补丁；反向应用检查通过，变更范围精确为 5 个源码文件和 3 个测试文件。
3. 提升后正式可执行源码树身份与候选 CUDA 窗口绑定身份完全一致。
4. GitHub CPU runner 上正式全量测试：`176 passed, 5 skipped, 0 failed`，另有 `52` 个 subtests 通过。4 项因无 CuPy/CUDA 跳过，1 项因无 Tk 图形显示跳过。
5. 候选资格测试 `24/24`、正式 `self-check` `6/6`、`compileall`、`git diff --check` 和尾随空格/制表符扫描通过。
6. 冻结候选清单未改写；其中 `authorization_received=false` 是用户授权前的历史快照，不代表当前未授权。

## 尚未完成及结论边界

1. 本轮 runner 无 CuPy/CUDA，未从提升后的正式路径重跑隔离 CUDA 测试，也未重跑 `136 × 136 × 120`、`0.013 s` 真实细网格窗口。
2. 候选阶段上述 CUDA 证据绑定的可执行源码树与正式提升后逐字节身份相同，因此仍有实现级相关性；但不得表述为正式路径已重新执行。
3. 旧登记步长 `7.5e-6 s` 的候选命中比例为 `0.928856`，低于资格门 `0.95`；`5.82815205187083e-6 s` 只是候选窗口中的最小联合限步值，不是冻结步长。
4. Q1R2 仍为 `NOT_FROZEN`；没有取得多网格/多时间步收敛、数值资格、物理验证或实验验证通过。
5. 当前结论上限为 `{CLAIM_CEILING}`。

## 下一次执行顺序

1. 在正式程序上运行隔离 CUDA 测试，确认 CUDA 投影、CPU/CUDA 对等和 GPU 压力历史回滚。
2. 用 `fine_window_pressure_1e-7.json` 从正式源码重跑到 `0.013 s`，并用 `analyse_fine_window.py` 核验正式源码身份、CUDA 无回退、旧故障时刻覆盖、CFL、散度、压力残差和全部质量门。
3. 依据提升后各空间层真实轨迹冻结新的空间共同登记步长；不得直接沿用候选窗口的单个最小步长。
4. 冻结并执行全新 Q1R2：统一压力容差 `1e-7`，空间组共同实际步长，时间组实际步长严格递减，实际 CUDA 无回退。
5. 即使 Q1R2 通过，物理预测与实验验证仍保持 `NO-GO`，之后另行设计更长时和平滑区/近拓扑区资格。
""",
        encoding="utf-8",
    )

    with HANDOFF_HISTORY.open("a", encoding="utf-8") as handle:
        handle.write(
            f"\n- {now}：用户授权按当前交接完成 HF3D CFL 控制器正式提升；冻结补丁原样应用到 `02_模拟程序/`，正式源码树身份匹配。GitHub CPU runner 正式全量测试 176 通过、5 项按环境跳过、0 失败，另有 52 个 subtests 通过；候选测试 24/24、self-check 6/6。正式路径 CUDA 测试和 0.013 s 窗口待在 CUDA 环境重跑；Q1R2 仍未冻结。\n"
        )


def main() -> int:
    os.chdir(ROOT)
    run("git", "config", "core.quotepath", "false")
    trigger_commit = output("git", "rev-parse", "HEAD")

    if sha256(PATCH) != EXPECTED_PATCH_SHA256:
        raise RuntimeError("frozen patch SHA-256 mismatch")
    run("git", "diff", "--quiet", FORMAL_BASELINE_COMMIT, "HEAD", "--", "02_模拟程序")
    run("git", "diff", "--quiet", FORMAL_BASELINE_COMMIT, "HEAD", "--", "01_最终理论")
    candidate_manifest, fine_report = require_frozen_evidence()

    run("git", "apply", "--directory=02_模拟程序", "--check", str(PATCH))
    run("git", "apply", "--directory=02_模拟程序", str(PATCH))
    run(
        "git",
        "apply",
        "--directory=02_模拟程序",
        "--check",
        "--reverse",
        str(PATCH),
    )
    observed_formal = changed_names(scope="02_模拟程序")
    if observed_formal != EXPECTED_FORMAL_FILES:
        raise RuntimeError(
            "formal patch changed-file scope mismatch: "
            f"expected={sorted(EXPECTED_FORMAL_FILES)}, observed={sorted(observed_formal)}"
        )
    run("git", "diff", "--quiet", "--", "01_最终理论")

    from airvortex.io.archive import source_tree_identity

    source_identity = source_tree_identity(FORMAL_DIR)
    if not source_identity.get("available"):
        raise RuntimeError(f"formal source identity unavailable: {source_identity}")
    if source_identity.get("value") != EXPECTED_SOURCE_SHA256:
        raise RuntimeError(
            "promoted formal source identity mismatch: "
            f"expected={EXPECTED_SOURCE_SHA256}, observed={source_identity.get('value')}"
        )

    run(sys.executable, "-m", "compileall", "-q", "02_模拟程序/src", "02_模拟程序/tests")
    formal_tests = validate_formal_tests()
    candidate_tests = validate_candidate_tests()
    self_check_payload, self_check_counts = validate_self_check()
    run("git", "diff", "--check")
    verify_text(EXPECTED_FORMAL_FILES)

    write_reports(
        trigger_commit=trigger_commit,
        source_identity=source_identity,
        formal_tests=formal_tests,
        candidate_tests=candidate_tests,
        self_check_payload=self_check_payload,
        self_check_counts=self_check_counts,
        candidate_manifest=candidate_manifest,
        fine_report=fine_report,
    )
    run("git", "diff", "--check")
    verify_text(
        EXPECTED_FORMAL_FILES
        | {
            REPORT_JSON.relative_to(ROOT).as_posix(),
            REPORT_MD.relative_to(ROOT).as_posix(),
            HANDOFF_CURRENT.relative_to(ROOT).as_posix(),
            HANDOFF_HISTORY.relative_to(ROOT).as_posix(),
        }
    )

    for transient in (OLD_HELPER, SELF, WORKFLOW):
        transient.unlink(missing_ok=False)

    stage_paths = [
        "02_模拟程序",
        REPORT_JSON.relative_to(ROOT).as_posix(),
        REPORT_MD.relative_to(ROOT).as_posix(),
        HANDOFF_CURRENT.relative_to(ROOT).as_posix(),
        HANDOFF_HISTORY.relative_to(ROOT).as_posix(),
        OLD_HELPER.relative_to(ROOT).as_posix(),
        SELF.relative_to(ROOT).as_posix(),
        WORKFLOW.relative_to(ROOT).as_posix(),
    ]
    run("git", "add", "-A", "--", *stage_paths)
    run("git", "diff", "--cached", "--check")
    expected_staged = EXPECTED_FORMAL_FILES | {
        REPORT_JSON.relative_to(ROOT).as_posix(),
        REPORT_MD.relative_to(ROOT).as_posix(),
        HANDOFF_CURRENT.relative_to(ROOT).as_posix(),
        HANDOFF_HISTORY.relative_to(ROOT).as_posix(),
        OLD_HELPER.relative_to(ROOT).as_posix(),
        SELF.relative_to(ROOT).as_posix(),
        WORKFLOW.relative_to(ROOT).as_posix(),
    }
    observed_staged = changed_names(cached=True)
    if observed_staged != expected_staged:
        raise RuntimeError(
            "final staged scope mismatch: "
            f"expected={sorted(expected_staged)}, observed={sorted(observed_staged)}"
        )

    run("git", "fetch", "origin", "main")
    remote_head = output("git", "rev-parse", "origin/main")
    if remote_head != trigger_commit:
        raise RuntimeError(
            "main changed concurrently; refusing to overwrite: "
            f"trigger={trigger_commit}, origin/main={remote_head}"
        )

    run("git", "config", "user.name", "github-actions[bot]")
    run(
        "git",
        "config",
        "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    )
    run("git", "commit", "-m", "Promote transactional HF3D CFL controller")
    promotion_commit = output("git", "rev-parse", "HEAD")
    run("git", "push", "origin", "HEAD:main")
    print(json.dumps({"promotion_commit": promotion_commit}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
