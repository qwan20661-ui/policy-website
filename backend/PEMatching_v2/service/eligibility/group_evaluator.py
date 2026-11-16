# -*- coding: utf-8 -*-
"""保留组和分支边界，不在条件级扁平化前改变 AND/OR 语义。"""

from __future__ import annotations

from typing import Any, Dict, List

from service import fields as F
from service.eligibility.condition_evaluator import evaluate_condition


def _group_status(operator: str, items: List[Dict[str, Any]]) -> str:
    statuses = [item.get("evalStatus") for item in items]
    if not statuses:
        return "not_evaluable"
    if operator in {"OR", "ANY"}:
        if "pass" in statuses:
            return "pass"
        return "unknown" if any(status in {"missing", "not_evaluable"} for status in statuses) else "fail"
    if "fail" in statuses:
        return "fail"
    return "unknown" if any(status in {"missing", "not_evaluable"} for status in statuses) else "pass"


def evaluate_group(
    profile: Dict[str, Any],
    group: Dict[str, Any],
    *,
    group_type: str,
    model_facts: Dict[str, Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    operator = F.text(group.get("conditionOperator") or "AND").upper()
    if operator not in {"AND", "OR", "ANY"}:
        operator = "AND"
    items = [
        evaluate_condition(
            profile,
            item,
            group_type=group_type,
            model_fact=(model_facts or {}).get(F.text(item.get("semanticItemKey"))),
        )
        for item in group.get("conditionFieldItems") or []
    ]
    return {"conditionNo": group.get("conditionNo"), "groupType": group_type, "rawText": group.get("rawText"), "conditionOperator": operator, "conditionBranchId": group.get("conditionBranchId"), "unresolvedFragments": group.get("unresolvedFragments") or [], "groupStatus": _group_status(operator, items), "items": items}
