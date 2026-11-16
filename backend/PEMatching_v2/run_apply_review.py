# -*- coding: utf-8 -*-
"""将政策标签复核表的修改写入独立 overrides 文件。

用法：
    python backend/PEMatching_v2/run_apply_review.py --file output/policy_tags_review_reviewed.xlsx
    python backend/PEMatching_v2/run_apply_review.py --file reviewed.xlsx --dry-run
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from service import fields as F
from service import taxonomy as tx
from service.paths import POLICY_TAGS_OVERRIDES_PATH, POLICY_TAGS_PATH, atomic_write_json
from service.tagging import validate_tagging_result


def _split(value: Any) -> List[str]:
    text = F.text(value).replace(",", "、").replace("，", "、")
    return [item.strip() for item in text.split("、") if item.strip()]


def _optional_number(value: Any) -> Optional[float]:
    if value is None or F.text(value) == "":
        return None
    number = F.to_float(value)
    if number is None:
        raise ValueError(f"不是合法数字：{value!r}")
    return int(number) if number.is_integer() else number


def _load_raw_tags() -> Dict[str, Dict[str, Any]]:
    payload = json.loads(POLICY_TAGS_PATH.read_text(encoding="utf-8"))
    return {
        F.text(item.get("fusionPolicyId")): item
        for item in payload.get("data") or []
        if isinstance(item, dict) and F.text(item.get("fusionPolicyId"))
    }


def _row_to_record(headers: Dict[str, int], values: tuple, base: Dict[str, Any]) -> Dict[str, Any]:
    def cell(name: str, default: Any = None) -> Any:
        index = headers.get(name)
        return values[index] if index is not None and index < len(values) else default

    record = copy.deepcopy(base)
    tags = record.setdefault("tags", {})
    for dimension, value_key, value_header in (
        ("A", "primary", "A主"), ("D", "value", "D"),
        ("E", "value", "E"), ("F", "value", "F"),
    ):
        entry = tags.setdefault(dimension, {})
        entry[value_key] = F.text(cell(value_header)) or None
    tags["A"]["secondary"] = _split(cell("A次"))
    tags.setdefault("B", {})["values"] = _split(cell("B值"))
    tags.setdefault("C", {})["values"] = _split(cell("C大类"))
    tags["C"]["subValues"] = _split(cell("C子类"))
    for dimension in "ABCD":
        tags[dimension]["confidence"] = F.text(cell(f"{dimension}置信度"))
        tags[dimension]["basis"] = F.text(cell(f"{dimension}依据"))

    record["applicability"] = {
        "applicable": F.text(cell("可申报")) not in ("否", "false", "0"),
        "audience": F.text(cell("受众")),
        "reason": F.text(cell("适用性依据")),
    }
    record["applicationWindow"] = {
        "startDate": F.text(cell("申报开始")) or None,
        "endDate": F.text(cell("申报结束")) or None,
        "isRolling": F.text(cell("长期受理")) in ("是", "true", "1"),
        "source": F.text(cell("申报期来源")),
        "basis": F.text(cell("申报期依据")),
    }
    record["growthChain"] = {
        "prerequisite": F.text(cell("链路前置")) or None,
        "nextStage": F.text(cell("链路后继")) or None,
        "basis": F.text((base.get("growthChain") or {}).get("basis")),
    }
    keyword_index = copy.deepcopy(base.get("keywordIndex") or {})
    keyword_index["title"] = _split(cell("标题关键词"))
    record["keywordIndex"] = keyword_index
    record["notes"] = F.text(cell("备注"))

    scale_headers = {"职工下限", "职工上限", "营收下限(万元)", "营收上限(万元)"}
    if scale_headers & set(headers):
        record["scaleThresholds"] = {
            "employeeMin": _optional_number(cell("职工下限")),
            "employeeMax": _optional_number(cell("职工上限")),
            "revenueMinWan": _optional_number(cell("营收下限(万元)")),
            "revenueMaxWan": _optional_number(cell("营收上限(万元)")),
            "confidence": F.text(cell("规模置信度")),
            "basis": F.text(cell("规模依据")),
        }
    return record


def _diff_override(base: Dict[str, Any], reviewed: Dict[str, Any]) -> Dict[str, Any]:
    override: Dict[str, Any] = {"fusionPolicyId": F.text(base.get("fusionPolicyId"))}
    changed_tags = {
        dimension: reviewed["tags"][dimension]
        for dimension in "ABCDEF"
        if reviewed["tags"].get(dimension) != (base.get("tags") or {}).get(dimension)
    }
    if changed_tags:
        override["tags"] = changed_tags
    for key in ("applicability", "applicationWindow", "growthChain", "keywordIndex",
                "scaleThresholds", "notes"):
        if reviewed.get(key) != base.get(key):
            override[key] = reviewed.get(key)
    return override


def build_overrides(review_file: Path) -> List[Dict[str, Any]]:
    from openpyxl import load_workbook

    base_records = _load_raw_tags()
    workbook = load_workbook(review_file, read_only=True, data_only=True)
    sheet = workbook["政策标签复核"] if "政策标签复核" in workbook.sheetnames else workbook.active
    rows = sheet.iter_rows(values_only=True)
    header_values = next(rows)
    headers = {F.text(value): index for index, value in enumerate(header_values) if F.text(value)}
    if "政策ID" not in headers:
        raise ValueError("复核表缺少“政策ID”列")

    overrides: List[Dict[str, Any]] = []
    errors: List[str] = []
    for row_number, values in enumerate(rows, start=2):
        policy_id = F.text(values[headers["政策ID"]])
        if not policy_id:
            continue
        base = base_records.get(policy_id)
        if base is None:
            errors.append(f"第 {row_number} 行：政策 {policy_id} 不在当前 policy_tags.json")
            continue
        try:
            reviewed = _row_to_record(headers, values, base)
            fatal, _ = validate_tagging_result(reviewed, policy_id)
            if fatal:
                errors.append(f"第 {row_number} 行（{policy_id}）：{'；'.join(fatal)}")
                continue
            override = _diff_override(base, reviewed)
            if len(override) > 1:
                override["reviewNote"] = F.text(values[headers["复核意见"]]) if "复核意见" in headers else ""
                overrides.append(override)
        except ValueError as exc:
            errors.append(f"第 {row_number} 行（{policy_id}）：{exc}")
    if errors:
        raise ValueError("复核表校验失败：\n" + "\n".join(errors[:20]))
    return overrides


def main() -> int:
    parser = argparse.ArgumentParser(description="将政策标签复核结果写入 overrides")
    parser.add_argument("--file", type=Path, required=True, help="人工复核后的 xlsx")
    parser.add_argument("--dry-run", action="store_true", help="只校验并显示差异，不写文件")
    args = parser.parse_args()

    overrides = build_overrides(args.file.resolve())
    payload = {
        "schemaVersion": "pematching-v2-policy-tag-overrides-v1",
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "sourceFile": str(args.file.resolve()),
        "count": len(overrides),
        "data": overrides,
    }
    if not args.dry_run:
        atomic_write_json(POLICY_TAGS_OVERRIDES_PATH, payload)
    action = "校验完成" if args.dry_run else f"已写入 {POLICY_TAGS_OVERRIDES_PATH}"
    print(f"{action}：{len(overrides)} 条政策存在人工修改")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
