#!/usr/bin/env bash

set -u

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
expected_origin="https://github.com/cjt42554-crypto/iypt-10-Air_vortex.git"
hard_fail=0

pass() {
    printf 'OK        %s\n' "$1"
}

fail() {
    printf 'FAIL      %s\n' "$1"
    hard_fail=1
}

if ! repo_root="$(git -C "$script_dir" rev-parse --show-toplevel 2>/dev/null)"; then
    printf 'FAIL      当前脚本不在 Git 仓库中\n'
    exit 1
fi

printf 'REPO      %s\n' "$repo_root"
printf 'HEAD      %s\n' "$(git -C "$repo_root" rev-parse HEAD)"

origin_fetch_url="$(git -C "$repo_root" remote get-url origin 2>/dev/null || true)"
origin_push_url="$(git -C "$repo_root" remote get-url --push origin 2>/dev/null || true)"
if [[ "$origin_fetch_url" == "$expected_origin" ]] && [[ "$origin_push_url" == "$expected_origin" ]]; then
    pass "origin_fetch_push=$expected_origin"
else
    fail "origin_fetch=$origin_fetch_url origin_push=$origin_push_url（预期均为 $expected_origin）"
fi

current_branch="$(git -C "$repo_root" branch --show-current)"
if [[ "$current_branch" == "main" ]]; then
    pass "current_branch=main"
else
    fail "current_branch=${current_branch:-DETACHED}（预期 main）"
fi

upstream="$(git -C "$repo_root" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)"
if [[ "$upstream" == "origin/main" ]]; then
    pass "upstream=origin/main"
else
    fail "upstream=${upstream:-NONE}（预期 origin/main）"
fi

mapfile -t local_heads < <(git -C "$repo_root" for-each-ref --format='%(refname)' refs/heads)
if [[ ${#local_heads[@]} -eq 1 ]] && [[ "${local_heads[0]}" == "refs/heads/main" ]]; then
    pass "local_heads=refs/heads/main"
else
    fail "local_heads=${local_heads[*]:-NONE}"
fi

mapfile -t tracking_heads < <(
    git -C "$repo_root" for-each-ref --format='%(refname)' refs/remotes/origin \
        | sed '/^refs\/remotes\/origin\/HEAD$/d'
)
if [[ ${#tracking_heads[@]} -eq 1 ]] && [[ "${tracking_heads[0]}" == "refs/remotes/origin/main" ]]; then
    pass "remote_tracking_heads=refs/remotes/origin/main"
else
    fail "remote_tracking_heads=${tracking_heads[*]:-NONE}"
fi

if remote_output="$(git -C "$repo_root" ls-remote --heads origin 2>/dev/null)"; then
    mapfile -t remote_heads < <(printf '%s\n' "$remote_output" | awk 'NF >= 2 {print $2}')
    if [[ ${#remote_heads[@]} -eq 1 ]] && [[ "${remote_heads[0]}" == "refs/heads/main" ]]; then
        pass "remote_live_heads=refs/heads/main"
        remote_main_sha="$(printf '%s\n' "$remote_output" | awk '$2 == "refs/heads/main" {print $1}')"
        local_main_sha="$(git -C "$repo_root" rev-parse main)"
        if [[ "$local_main_sha" == "$remote_main_sha" ]]; then
            pass "local_main_matches_remote=$local_main_sha"
        else
            printf 'ATTENTION local_main=%s remote_main=%s；先确认待拉取或待推送提交\n' \
                "$local_main_sha" "$remote_main_sha"
        fi
    else
        fail "remote_live_heads=${remote_heads[*]:-NONE}"
    fi
else
    fail "无法查询 origin 的实时 heads"
fi

baseline_paths=("01_最终理论" "02_模拟程序" "03_项目资料")
baseline_hashes=(
    "238914477bfa15cf6d8dcd1a7e731b1559b6506d"
    "2d65edcd2717ac262d028ab51d1feecc850b838f"
    "84f0e9e210b86b556935b9117310b5e668813bd7"
)

for index in "${!baseline_paths[@]}"; do
    path="${baseline_paths[$index]}"
    expected_hash="${baseline_hashes[$index]}"
    actual_hash="$(git -C "$repo_root" rev-parse "HEAD:$path" 2>/dev/null || true)"
    if [[ "$actual_hash" == "$expected_hash" ]]; then
        pass "$path=$actual_hash"
    else
        fail "$path=${actual_hash:-MISSING}（预期 $expected_hash）"
    fi
done

mature_status="$(git -C "$repo_root" status --short -- "${baseline_paths[@]}")"
if [[ -z "$mature_status" ]]; then
    pass "mature_worktree=unchanged"
else
    printf 'FAIL      成熟基线工作树或暂存区有修改：\n%s\n' "$mature_status"
    hard_fail=1
fi

worktree_status="$(git -C "$repo_root" status --short)"
if [[ -z "$worktree_status" ]]; then
    pass "worktree=clean"
else
    printf 'ATTENTION worktree 有修改；先确认来源和任务重叠：\n%s\n' "$worktree_status"
fi

if [[ $hard_fail -ne 0 ]]; then
    printf 'RESULT    FAIL_REPOSITORY_PREFLIGHT\n'
    exit 1
fi

printf 'RESULT    PASS_REPOSITORY_PREFLIGHT\n'
