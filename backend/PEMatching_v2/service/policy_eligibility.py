# -*- coding: utf-8 -*-
"""逐政策资格评估。

标签和分数只服务于候选召回与排序；本模块才负责回答企业是否满足已结构化的
申报条件。缺少企业事实时返回 ``unknown``，绝不能把它提升为 ``pass``。
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional

from service import fields as F
from service.paths import POLICY_ELIGIBILITY_RULES_PATH


SCHEMA = "pematching-v2-policy-eligibility-v1"

PASS = "pass"
FAIL = "fail"
UNKNOWN = "unknown"
NOT_APPLICABLE = "not_applicable"


class EligibilityConfigError(RuntimeError):
    """资格规则配置不合法。"""


@lru_cache(maxsize=1)
def load_eligibility_rules() -> Dict[str, Dict[str, Any]]:
    """加载逐政策规则；缺少配置不是异常，而是尚未建立资格判定能力。"""
    if not POLICY_ELIGIBILITY_RULES_PATH.exists():
        return {}
    try:
        payload = json.loads(POLICY_ELIGIBILITY_RULES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EligibilityConfigError(f"资格规则文件不可读取：{exc}") from exc
    if payload.get("schemaVersion") != SCHEMA:
        raise EligibilityConfigError(
            f"资格规则 schemaVersion 不匹配：{payload.get('schemaVersion')!r}"
        )
    data = payload.get("policies") or {}
    if not isinstance(data, dict):
        raise EligibilityConfigError("资格规则 policies 必须是对象")
    return {F.text(policy_id): rule for policy_id, rule in data.items()
            if F.text(policy_id) and isinstance(rule, dict)}


def reset_cache() -> None:
    load_eligibility_rules.cache_clear()


def _get_path(source: Dict[str, Any], path: str) -> Any:
    current: Any = source
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _condition_result(profile: Dict[str, Any], rule: Dict[str, Any]) -> Dict[str, Any]:
    """执行一个原子条件，返回三态而非布尔值。"""
    field = F.text(rule.get("field"))
    operator = F.text(rule.get("operator"))
    expected = rule.get("value")
    actual = _get_path(profile, field) if field else None
    result = UNKNOWN

    if not field or F.is_missing(actual):
        basis = f"企业字段 {field or '未映射'} 未提供"
    elif operator == "lte":
        result = PASS if actual <= expected else FAIL
        basis = f"实际值 {actual}，要求不超过 {expected}"
    elif operator == "gte":
        result = PASS if actual >= expected else FAIL
        basis = f"实际值 {actual}，要求不低于 {expected}"
    elif operator == "is_true":
        result = PASS if actual is True else FAIL
        basis = f"实际值 {actual!r}，要求为 true"
    elif operator == "is_false":
        result = PASS if actual is False else FAIL
        basis = f"实际值 {actual!r}，要求为 false"
    elif operator == "equals":
        result = PASS if actual == expected else FAIL
        basis = f"实际值 {actual!r}，要求为 {expected!r}"
    else:
        basis = f"运算符 {operator or '未配置'} 尚不可执行"

    return {
        "ruleId": F.text(rule.get("ruleId")),
        "label": F.text(rule.get("label")),
        "field": field,
        "operator": operator,
        "expected": expected,
        "actual": actual,
        "result": result,
        "source": F.text(rule.get("source")),
        "basis": basis,
    }


def _evaluate_all(profile: Dict[str, Any], rules: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [_condition_result(profile, rule) for rule in rules if isinstance(rule, dict)]


def evaluate_policy_eligibility(policy_id: str, profile: Dict[str, Any]) -> Dict[str, Any]:
    """返回政策资格结论和逐条件证据。

    只有 ``coverage=complete``、全部硬条件通过、且不存在排除分支时才返回
    ``eligible``。配置缺失、来源冲突、条件缺字段均保守降级。
    """
    rule_set = load_eligibility_rules().get(F.text(policy_id))
    if not rule_set:
        return {
            "status": "needs_requirement_evaluation",
            "strictEligibility": "not_evaluated",
            "coverage": "none",
            "directPushAllowed": False,
            "conditionResults": [],
            "reasons": ["该政策尚未建立可执行的逐条件资格规则"],
        }

    if F.text(rule_set.get("sourceStatus")) == "conflict":
        return {
            "status": "source_conflict",
            "strictEligibility": "not_evaluated",
            "coverage": F.text(rule_set.get("coverage")) or "none",
            "directPushAllowed": False,
            "conditionResults": [],
            "reasons": [F.text(rule_set.get("sourceConflict")) or "政策标签与原文条件存在冲突"],
        }

    action_results = _evaluate_all(profile, rule_set.get("notApplicableWhen") or [])
    matched_action = next((item for item in action_results if item["result"] == PASS), None)
    if matched_action:
        return {
            "status": NOT_APPLICABLE,
            "strictEligibility": NOT_APPLICABLE,
            "coverage": F.text(rule_set.get("coverage")) or "none",
            "directPushAllowed": False,
            "action": F.text(rule_set.get("notApplicableAction")) or "information_only",
            "conditionResults": action_results,
            "reasons": [F.text(rule_set.get("notApplicableReason")) or matched_action["label"]],
        }

    conditions = _evaluate_all(profile, rule_set.get("hardConditions") or [])
    coverage = F.text(rule_set.get("coverage")) or "partial"
    failures = [item for item in conditions if item["result"] == FAIL]
    unknowns = [item for item in conditions if item["result"] == UNKNOWN]

    if failures:
        status, strict, reasons = "ineligible", "ineligible", [
            "未满足硬性条件：" + "、".join(item["label"] or item["ruleId"] for item in failures)
        ]
    elif unknowns:
        status, strict, reasons = "needs_data", UNKNOWN, [
            "缺少硬性条件证据：" + "、".join(item["label"] or item["ruleId"] for item in unknowns)
        ]
    elif coverage != "complete":
        status, strict, reasons = "needs_requirement_evaluation", "not_evaluated", [
            "政策原文条件尚未完整结构化，已通过的局部条件不能推导严格符合"
        ]
    else:
        status, strict, reasons = "eligible", "eligible", ["全部已结构化硬性条件均已通过"]

    return {
        "status": status,
        "strictEligibility": strict,
        "coverage": coverage,
        "directPushAllowed": strict == "eligible",
        "action": F.text(rule_set.get("action")) or "apply",
        "conditionResults": action_results + conditions,
        "reasons": reasons,
    }
