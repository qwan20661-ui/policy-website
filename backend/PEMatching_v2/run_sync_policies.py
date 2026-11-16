# -*- coding: utf-8 -*-
"""同步结构化政策到 config/policy_snapshot.json。

v2 自己维护快照，不读老目录的 config/主政策申报条件.json。

用法：
    python3 backend/PEMatching_v2/run_sync_policies.py           # 完整重拉并原子切换
    python3 backend/PEMatching_v2/run_sync_policies.py --stats   # 只看统计不重拉
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from service.paths import POLICY_SNAPSHOT_PATH
from service.policy_sync import (
    PolicySyncError,
    fetch_policies,
    load_snapshot,
    snapshot_stats,
)


def _print_stats(stats: dict) -> None:
    print(f"政策条数：{stats['count']}　拉取时间：{stats['fetchedAt']}　来源：{stats['source']}")
    print(f"版本哈希：{stats['versionHash']}")
    if stats["failures"]:
        print(f"⚠ 详情拉取失败 {stats['failures']} 条（见快照 failures 字段）")
    print()
    print("字段非空率：")
    for label, key in (
        ("标题", "hasTitle"),
        ("摘要", "hasSummary"),
        ("行业领域 industryField", "hasIndustryField"),
        ("适用范围 applicableScopePath", "hasApplicableScope"),
        ("支持方式", "hasSupportMethods"),
        ("申报条件", "hasConditions"),
        ("平台关键词", "hasKeywords"),
        ("申报材料", "hasMaterials"),
        ("父政策", "hasParent"),
        ("子政策", "hasChildren"),
        ("更新时间（增量指纹）", "hasUpdateTime"),
    ):
        print(f"  {label:<28} {stats[key]}")
    print()
    print("政策属性分布（documents.policyAttribute）：")
    for name, count in stats["policyAttributes"].items():
        print(f"  {name:<12} {count}")


def main() -> int:
    parser = argparse.ArgumentParser(description="同步结构化政策快照")
    parser.add_argument("--force", action="store_true", help="兼容旧命令；当前始终完整重拉")
    parser.add_argument("--stats", action="store_true", help="只打印现有快照统计，不重拉")
    parser.add_argument("--page-size", type=int, default=None, help="列表分页大小")
    args = parser.parse_args()

    if args.stats:
        try:
            snapshot = load_snapshot()
        except (FileNotFoundError, ValueError) as exc:
            print(exc)
            return 1
        _print_stats(snapshot_stats(snapshot))
        return 0

    try:
        snapshot = fetch_policies(page_size=args.page_size)
    except PolicySyncError as exc:
        print(f"同步失败：{exc}")
        return 1

    print(f"快照：{POLICY_SNAPSHOT_PATH}")
    print()
    _print_stats(snapshot_stats(snapshot))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
