# -*- coding: utf-8 -*-
"""逐条件确定性求值，保持 V1 的 10/30/100 评分口径。"""

from __future__ import annotations

from typing import Any, Dict

from service import fields as F
from service.eligibility.constants import DEFAULT_MISSING_SCORE, PRESENT_DATA_BASE_SCORE
from service.eligibility.enterprise_adapter import resolve_fact
from service.eligibility.unit_converter import convert


def _missing(item: Dict[str, Any], fact: Dict[str, Any]) -> Dict[str, Any]:
    return {"semanticItemKey": item.get("semanticItemKey"), "itemId": item.get("itemId"), "itemName": item.get("itemName"), "evalStatus": "missing", "score": DEFAULT_MISSING_SCORE, "evidence": fact, "passSource": "default_missing_assumed", "contributesToScore": True, "message": f"{fact.get('reason')}；按 V1 缺失口径记 10 分。"}


def _not_evaluable(item: Dict[str, Any], fact: Dict[str, Any], reason: str) -> Dict[str, Any]:
    return {"semanticItemKey": item.get("semanticItemKey"), "itemId": item.get("itemId"), "itemName": item.get("itemName"), "evalStatus": "not_evaluable", "score": None, "evidence": fact, "passSource": "not_evaluable", "contributesToScore": False, "message": reason}


def evaluate_condition(
    profile: Dict[str, Any],
    item: Dict[str, Any],
    *,
    group_type: str,
    model_fact: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    rule = item.get("matchRule") or {}
    operator = F.text(rule.get("operator")).lower()
    # 模型已经逐条件比对过时，优先使用其选择且已被服务端回读的证据；
    # 未得到模型证据才回退到静态精确映射，保证模型短暂不可用不影响旁路运行。
    fact = model_fact or resolve_fact(profile, item)
    if fact.get("evidenceTier") in {"U_missing", "R_rejected"}:
        return _missing(item, fact)
    actual = fact.get("value")
    # 只接受标量事实：模型回读/静态映射指向整条记录或嵌套数组时，
    # 无法作为单一事实参与比较或换算，按缺失口径记分，避免 dict 流到评分与导出。
    if isinstance(actual, (dict, list, tuple)):
        return _missing(item, fact)
    hard = group_type == "hardRuleGroups"
    target = rule.get("targetValue")
    base = {"semanticItemKey": item.get("semanticItemKey"), "itemId": item.get("itemId"), "itemName": item.get("itemName"), "operator": operator, "targetValue": target, "targetUnit": rule.get("unit"), "enterpriseValue": actual, "enterpriseUnit": fact.get("unit"), "evidence": fact, "contributesToScore": True}

    if operator == "llm_judge":
        judgement = F.text(fact.get("modelJudgement")).lower()
        if judgement not in {"pass", "fail"}:
            return _missing(item, fact)
        if judgement == "pass":
            return {**base, "evalStatus": "pass", "score": 1.0, "passSource": "model_fact_then_server", "message": "模型给出可回读企业事实，服务端记录为满足；记 100 分。"}
        if hard:
            return {**base, "evalStatus": "fail", "score": 0.0, "passSource": "model_fact_then_server", "message": "模型给出可回读企业事实，服务端记录为不满足硬性条件；记 0 分。"}
        return {**base, "evalStatus": "fail", "score": PRESENT_DATA_BASE_SCORE, "passSource": "model_fact_then_server", "message": "模型给出可回读企业事实，服务端记录为不满足软性条件；记 30 分。"}
    if operator == "has_value":
        return {**base, "evalStatus": "pass", "score": 1.0, "passSource": "exact_presence", "message": "企业字段有值，记 100 分。"}
    if operator in {"is_true", "is_false"}:
        expected = operator == "is_true"
        if not isinstance(actual, bool):
            return _not_evaluable(item, fact, "布尔条件的企业事实不是布尔值")
        passed = actual is expected
    elif operator in {"gt", "gte", "lt", "lte", "eq", "equals"}:
        value, formula, error = convert(actual, fact.get("unit"), rule.get("unit"))
        target_number = F.to_float(target)
        if value is None or target_number is None:
            return _not_evaluable(item, fact, error or "目标值不是可计算数值")
        base.update({"normalisedEnterpriseValue": value, "normalisedTargetValue": target_number, "conversionFormula": formula})
        comparisons = {"gt": value > target_number, "gte": value >= target_number, "lt": value < target_number, "lte": value <= target_number, "eq": abs(value - target_number) < 1e-9, "equals": abs(value - target_number) < 1e-9}
        passed = comparisons[operator]
    else:
        return _not_evaluable(item, fact, f"不支持的操作符 {operator!r}")

    if passed:
        return {**base, "evalStatus": "pass", "score": 1.0, "passSource": "exact_rule", "message": "企业事实满足政策条件，记 100 分。"}
    if hard:
        return {**base, "evalStatus": "fail", "score": 0.0, "passSource": "exact_rule", "message": "企业事实明确不满足硬性条件，记 0 分。"}
    if operator in {"gt", "gte"} and F.to_float(target) not in (None, 0) and F.to_float(base.get("normalisedEnterpriseValue")) is not None:
        score = min(1.0, max(PRESENT_DATA_BASE_SCORE, PRESENT_DATA_BASE_SCORE + float(base["normalisedEnterpriseValue"]) / float(base["normalisedTargetValue"])))
    else:
        score = PRESENT_DATA_BASE_SCORE
    return {**base, "evalStatus": "fail", "score": round(score, 4), "passSource": "exact_rule", "message": "企业事实未达到软性条件，按 V1 口径记分。"}
