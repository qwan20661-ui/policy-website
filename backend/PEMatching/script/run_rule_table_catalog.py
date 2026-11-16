"""查看已生成规则表的主政策列表与详情。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PROJECT_ROOT.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from service.rule_table_catalog_service import (
    build_rule_table_catalog_entry,
    get_rule_table_policy_detail,
    load_policies_with_rule_tables,
)


load_dotenv(PROJECT_ROOT / ".env")


DEFAULT_STRUCTURE_API_BASE_URL = os.getenv("STRUCTURE_API_BASE_URL", "http://127.0.0.1:8080").strip().rstrip("/")
DEFAULT_PAGE_SIZE = int(os.getenv("POLICY_PAGE_SIZE", "50") or 50)
DEFAULT_DETAIL_WORKERS = int(os.getenv("MAX_WORKERS", "5") or 5)


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="查看已生成规则表的主政策列表与详情。")
    parser.add_argument(
        "--structure-api-base-url",
        default=DEFAULT_STRUCTURE_API_BASE_URL,
        help="structure 接口基地址，默认读取 backend/PEMatching/.env 的 STRUCTURE_API_BASE_URL。",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="查看已生成规则表的主政策列表；不传 --detail 时默认也是列表模式。",
    )
    parser.add_argument(
        "--detail",
        default="",
        help="查看指定 fusionPolicyId 的规则表详情。",
    )
    parser.add_argument(
        "--keyword",
        default="",
        help="按主政策标题关键字筛选。",
    )
    parser.add_argument(
        "--policy-id",
        action="append",
        default=[],
        help="指定一个或多个 fusionPolicyId 来查看列表。",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="列表模式最多返回多少条，0 表示不限制。",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help="结构化列表每页抓取条数。",
    )
    parser.add_argument(
        "--detail-workers",
        type=int,
        default=DEFAULT_DETAIL_WORKERS,
        help="并发抓取详情数。",
    )
    parser.add_argument(
        "--review-status",
        default="1",
        help="主政策审核状态筛选，默认 1。",
    )
    parser.add_argument(
        "--structure-review-status",
        default="all",
        help="结构化审核状态筛选，默认 all。",
    )
    parser.add_argument(
        "--output",
        default="",
        help="可选，保存 JSON 到指定路径。",
    )
    return parser.parse_args()


def _print_list(items: List[Dict[str, Any]]) -> None:
    print("=" * 108)
    print("已生成规则表的主政策列表")
    print("=" * 108)
    if not items:
        print("没有查到已生成规则表的主政策。")
        return

    for index, item in enumerate(items, start=1):
        print(
            f"{index:>3}. 政策ID={item.get('政策ID', '')} | "
            f"标题={item.get('政策标题', '')} | "
            f"规则表版本={item.get('规则表版本', '') or '-'} | "
            f"硬性={item.get('硬性组数', 0)} | "
            f"软性={item.get('软性组数', 0)} | "
            f"直通车={item.get('直通车组数', 0)} | "
            f"条件项={item.get('条件项总数', 0)}"
        )


def _print_detail(detail: Dict[str, Any]) -> None:
    summary = build_rule_table_catalog_entry(detail, include_rule_table_json=False, include_raw_detail=False)
    print("=" * 108)
    print("规则表详情")
    print("=" * 108)
    for key in ["政策ID", "政策标题", "来源政策ID", "规则表名称", "规则表版本", "硬性组数", "软性组数", "直通车组数", "条件项总数"]:
        print(f"{key}: {summary.get(key, '')}")
    print("-" * 108)
    print(json.dumps(detail.get("ruleTableJson") or {}, ensure_ascii=False, indent=2))


def _write_output(path_text: str, payload: Any) -> None:
    path = Path(path_text)
    if not path.is_absolute():
        path = (REPO_ROOT / path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nJSON 已写入: {path}")


def main() -> int:
    args = parse_args()
    base_url = _safe_text(args.structure_api_base_url).rstrip("/")
    if not base_url:
        raise SystemExit("缺少 structure 接口地址。")

    if _safe_text(args.detail):
        detail = get_rule_table_policy_detail(_safe_text(args.detail), base_url=base_url)
        _print_detail(detail)
        if _safe_text(args.output):
            _write_output(args.output, detail)
        return 0

    policies = load_policies_with_rule_tables(
        base_url=base_url,
        limit=max(0, int(args.limit or 0)),
        page_size=max(1, int(args.page_size or 1)),
        detail_workers=max(1, int(args.detail_workers or 1)),
        keyword=_safe_text(args.keyword),
        review_status=_safe_text(args.review_status) or "1",
        structure_review_status=_safe_text(args.structure_review_status) or "all",
        policy_ids=args.policy_id,
    )
    rows = [build_rule_table_catalog_entry(item) for item in policies]
    _print_list(rows)
    if _safe_text(args.output):
        _write_output(args.output, rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
