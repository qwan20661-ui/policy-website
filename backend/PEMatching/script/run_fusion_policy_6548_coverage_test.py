"""使用实时 fusion-detail 规则表运行成长性评分覆盖测试。"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from service.enterprise_loader import load_enterprises
from service.matching_service import run_matching


DEFAULT_ENTERPRISE_FILE = PROJECT_ROOT / "企业测试" / "企业数据_成长性评分覆盖测试集.json"
DEFAULT_OUTPUT_FILE = PROJECT_ROOT / "output" / "fusion_policy_6548_growth_coverage_matching.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 fusionPolicyId=6548 成长性评分覆盖测试。")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080", help="融合政策接口基地址。")
    parser.add_argument("--policy-id", default="6548", help="融合政策 ID。")
    parser.add_argument("--enterprise-file", type=Path, default=DEFAULT_ENTERPRISE_FILE, help="企业测试数据文件。")
    parser.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT_FILE, help="匹配结果 JSON 输出路径。")
    return parser.parse_args()


def fetch_policy(base_url: str, policy_id: str) -> Dict[str, Any]:
    response = requests.get(
        f"{base_url.rstrip('/')}/api/fusion-classification/fusion-detail",
        params={"fusionPolicyId": policy_id},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    detail = payload.get("data") if payload.get("success") else None
    rule_table = detail.get("ruleTableJson") if isinstance(detail, dict) else None
    if not isinstance(rule_table, dict) or not rule_table:
        raise RuntimeError("接口未返回可执行的 ruleTableJson。")
    return {
        "id": detail.get("id", policy_id),
        "title": detail.get("mainFileTitle") or detail.get("directoryTitle") or f"政策{policy_id}",
        "ruleTableJson": rule_table,
    }


def build_summary(results: List[Dict[str, Any]], policy: Dict[str, Any], base_url: str, source_file: Path) -> Dict[str, Any]:
    source_records = json.loads(source_file.read_text(encoding="utf-8"))
    source_by_code = {
        str(record.get("统一社会信用代码", "")): record
        for record in source_records
        if isinstance(record, dict)
    }
    rows = []
    for enterprise in results:
        match = enterprise["政策匹配结果"][0]
        details = {item.get("itemId"): item for item in match.get("条件匹配详情", [])}
        enterprise_info = enterprise.get("企业信息", {})
        source = source_by_code.get(str(enterprise_info.get("统一社会信用代码", "")), {})
        rows.append(
            {
                "序号": enterprise_info.get("企业ID", ""),
                "企业名称": enterprise.get("企业名称", ""),
                "测试场景": source.get("测试场景", ""),
                "最终分": match.get("final_score", 0),
                "硬性通过": bool(match.get("hard_pass")),
                "星级推荐": match.get("星级推荐", ""),
                "直通车命中": bool(match.get("special_pass_hit")),
                "员工数": details.get("2-1", {}).get("企业值"),
                "营收规模(亿元)": details.get("2-2", {}).get("企业值"),
                "资产规模(亿元)": details.get("2-3", {}).get("企业值"),
                "综合评价分": details.get("5-1", {}).get("企业值"),
                "综合评价分达标度": details.get("5-1", {}).get("得分"),
                "硬性阻断": match.get("hard_fail_items", []),
                "总体建议": match.get("总体建议", ""),
            }
        )
    rows.sort(key=lambda item: str(item["序号"]))
    return {
        "测试时间": datetime.now().isoformat(timespec="seconds"),
        "政策接口": f"{base_url.rstrip('/')}/api/fusion-classification/fusion-detail?fusionPolicyId={policy['id']}",
        "政策名称": policy["title"],
        "规则表版本": policy["ruleTableJson"].get("schemaVersion"),
        "企业数据文件": str(source_file),
        "企业数": len(rows),
        "硬性通过数": sum(1 for item in rows if item["硬性通过"]),
        "硬性失败数": sum(1 for item in rows if not item["硬性通过"]),
        "直通车命中数": sum(1 for item in rows if item["直通车命中"]),
        "平均分": round(sum(float(item["最终分"] or 0) for item in rows) / len(rows), 2) if rows else 0,
        "星级分布": dict(Counter(item["星级推荐"] for item in rows)),
        "测试明细": rows,
    }


def main() -> None:
    args = parse_args()
    policy = fetch_policy(args.base_url, args.policy_id)
    enterprises = load_enterprises(args.enterprise_file)
    results = run_matching(enterprises, [policy], max_workers=4, output_file=args.output_file)
    summary = build_summary(results, policy, args.base_url, args.enterprise_file)
    summary_file = args.output_file.with_name(f"{args.output_file.stem}_summary.json")
    summary_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    rows_by_id = {row["序号"]: row for row in summary["测试明细"]}
    expected_hard_failures = {"GROWTH-012", "GROWTH-013", "GROWTH-014", "GROWTH-015"}
    actual_hard_failures = {row["序号"] for row in summary["测试明细"] if not row["硬性通过"]}
    if actual_hard_failures != expected_hard_failures:
        raise AssertionError(f"上限型硬失败覆盖断言失败: {sorted(actual_hard_failures)}")
    if not (
        rows_by_id["GROWTH-007"]["最终分"]
        < rows_by_id["GROWTH-005"]["最终分"]
        < rows_by_id["GROWTH-003"]["最终分"]
        <= rows_by_id["GROWTH-002"]["最终分"]
    ):
        raise AssertionError("成长性总得分未按预期拉开排序差异。")
    if any(row["直通车命中"] for row in summary["测试明细"]):
        raise AssertionError("定性默认通过不应自动触发直通车。")

    print(json.dumps({key: summary[key] for key in ("企业数", "硬性通过数", "硬性失败数", "直通车命中数", "平均分", "星级分布")}, ensure_ascii=False, indent=2))
    print(f"结果 JSON: {args.output_file.resolve()}")
    print(f"汇总 JSON: {summary_file.resolve()}")
    print(f"结果 Excel: {args.output_file.with_suffix('.xlsx').resolve()}")


if __name__ == "__main__":
    main()
