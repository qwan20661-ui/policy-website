# -*- coding: utf-8 -*-
"""从当前政策快照生成可审计的政策条件包快照。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from service.paths import POLICY_CONDITION_SNAPSHOT_PATH
from service.policy_condition_package import (
    PolicyConditionPackageError,
    build_condition_package_snapshot,
    condition_package_stats,
    write_condition_package_snapshot,
)


def main() -> int:
    try:
        snapshot = build_condition_package_snapshot()
        write_condition_package_snapshot(snapshot)
    except (FileNotFoundError, PolicyConditionPackageError, ValueError) as exc:
        print(f"条件包生成失败：{exc}")
        return 1

    stats = condition_package_stats(snapshot)
    print(f"条件包快照：{POLICY_CONDITION_SNAPSHOT_PATH}")
    print(f"政策条数：{stats['count']}　版本：{stats['versionHash']}")
    print(
        "质量标记："
        f"规则表空 {stats['ruleTableEmpty']}，"
        f"unresolved {stats['hasUnresolvedItems']}，"
        f"llm_judge {stats['hasLlmJudgeItems']}，"
        f"疑似 OR 冲突 {stats['suspectedOrOperatorMismatch']}，"
        f"空目标值 {stats['hasEmptyTargetValues']}，"
        f"定量目标缺失 {stats['hasMissingRequiredTargetValues']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
