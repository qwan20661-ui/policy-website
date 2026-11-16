# -*- coding: utf-8 -*-
"""从权威结构 API 同步资格规则快照（审核通过的主政策）。

与 run_sync_policies.py（推荐快照）对应：本脚本维护资格门禁评分用的
config/eligibility/policy_profiles.jsonl + manifest.json。

同步口径与 index.html#structure 页面一致：只拉
reviewStatus=1（主政策审核通过）+ structureReviewStatus=1（结构审核通过）的政策。

用法：
    python backend/PEMatching_v2/run_sync_eligibility.py              # 拉取审核通过政策并迁移
    python backend/PEMatching_v2/run_sync_eligibility.py --preview    # 只拉取预览，不落盘
    python backend/PEMatching_v2/run_sync_eligibility.py --stats      # 只看当前快照统计
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from service.paths import ELIGIBILITY_MANIFEST_PATH
from service.eligibility.profile_loader import load_snapshot
from service.eligibility.sync import (
    EligibilitySyncError,
    eligibility_api_base_url,
    sync_from_api,
)


def _print_stats(manifest) -> None:
    print("资格快照：{}".format(ELIGIBILITY_MANIFEST_PATH))
    print("  profileCount           {}".format(manifest.get("profileCount")))
    print("  executableProfileCount {}".format(manifest.get("executableProfileCount")))
    print("  emptyProfileCount      {}".format(manifest.get("emptyProfileCount")))
    print("  partialProfileCount    {}".format(manifest.get("partialProfileCount")))
    print("  hardGroupCount         {}".format(manifest.get("hardGroupCount")))
    print("  softGroupCount         {}".format(manifest.get("softGroupCount")))
    print("  specialPassGroupCount  {}".format(manifest.get("specialPassGroupCount")))
    print("  invalidProfileCount    {}".format(manifest.get("invalidProfileCount")))
    print("  generatedAt            {}".format(manifest.get("generatedAt")))
    print("  sourcePaginationTotal  {}".format(manifest.get("sourcePaginationTotal")))
    print("  sourceReviewStatus     {}".format(manifest.get("sourceReviewStatus")))
    print("  sourceStructureReview  {}".format(manifest.get("sourceStructureReviewStatus")))


def main() -> int:
    parser = argparse.ArgumentParser(description="同步资格规则快照（审核通过的主政策）")
    parser.add_argument("--api-base-url", default=None, help="覆盖结构 API 根地址")
    parser.add_argument("--preview", action="store_true", help="拉取并打印统计，不写盘")
    parser.add_argument("--stats", action="store_true", help="只打印当前快照统计，不拉取")
    parser.add_argument("--page-size", type=int, default=50)
    args = parser.parse_args()

    if args.stats:
        try:
            _, profiles = load_snapshot()
        except Exception as exc:
            print("快照不可读：{}".format(exc))
            return 1
        # load_snapshot 已校验 manifest 哈希；直接读 manifest 打印统计
        try:
            import json
            manifest = json.loads(ELIGIBILITY_MANIFEST_PATH.read_text(encoding="utf-8"))
        except OSError as exc:
            print("manifest 不可读：{}".format(exc))
            return 1
        print("资格快照：{} 条".format(len(profiles)))
        _print_stats(manifest)
        return 0

    try:
        base_url = args.api_base_url or eligibility_api_base_url()
        manifest = sync_from_api(
            base_url,
            page_size=args.page_size,
            write=not args.preview,
        )
    except (EligibilitySyncError, Exception) as exc:
        print("同步失败：{}".format(exc))
        return 1

    if args.preview:
        print("（预览模式，未写盘）")
    print("同步完成。")
    _print_stats(manifest)
    print("\n落盘文件：")
    print("  config/eligibility/policy_profiles.jsonl")
    print("  config/eligibility/manifest.json")
    print("  config/eligibility/source_profiles.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
