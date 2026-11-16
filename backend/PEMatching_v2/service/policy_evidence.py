# -*- coding: utf-8 -*-
"""政策级证据与直推保护。

企业侧规则命中只能说明企业具备某类信号，不能因为 A/C 粗标签相同就证明
某一具体政策适用。本模块把规则与政策的关联收紧为可展示的关键词证据，并在
逐条申报条件尚未结构化前禁止把推荐分数解释为严格申报资格。
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

from service import fields as F
from service.policy_eligibility import evaluate_policy_eligibility


STATUS_NEEDS_POLICY_EVIDENCE = "needs_policy_evidence"
STATUS_NEEDS_REQUIREMENT_EVALUATION = "needs_requirement_evaluation"


def _normalized(value: Any) -> str:
    """用于中文关键词的保守比较，忽略空白和常见标点。"""
    return re.sub(r"[\s，,、。；;：:（）()【】\[\]‘’'\"“”]", "", F.text(value)).lower()


def _policy_keywords(policy_record: Dict[str, Any]) -> List[str]:
    index = policy_record.get("keywordIndex") or {}
    values: List[str] = [F.text(policy_record.get("title"))]
    for key in ("title", "aKeywords", "cKeywords"):
        values.extend(F.text(item) for item in (index.get(key) or []))
    return sorted({_normalized(value) for value in values if _normalized(value)})


def rule_evidence_for_policy(
    matched_rules: Iterable[Dict[str, Any]], policy_record: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """返回有政策关键词证据的规则。

    不再以 A/C 标签交集作为政策规则背书。关键词至少应有一方完整包含另一方，
    才能把规则呈现为该具体政策的触发依据。
    """
    policy_keywords = _policy_keywords(policy_record)
    evidence: List[Dict[str, Any]] = []
    for rule in matched_rules:
        signals = rule.get("signals") or {}
        hits = []
        for raw in signals.get("keywords") or []:
            keyword = _normalized(raw)
            if not keyword:
                continue
            if any(keyword in item or item in keyword for item in policy_keywords):
                hits.append(F.text(raw))
        if hits:
            evidence.append({
                "ruleId": F.text(rule.get("ruleId")),
                "matchedKeywords": sorted(set(hits)),
                "basis": "规则信号关键词与政策关键词存在直接交集",
            })
    return sorted(evidence, key=lambda item: item["ruleId"])


def assess_recommendation_safety(
    policy_id: str,
    policy_record: Dict[str, Any],
    rule_evidence: List[Dict[str, Any]],
    profile: Dict[str, Any],
) -> Dict[str, Any]:
    """合并政策级规则证据与逐条件资格结论。

    是否可直推完全取决于资格引擎的 ``eligible``，不以标签分数或关键词命中
    取代条件核验；关键词证据仍保留为可解释的候选来源。
    """
    has_evidence = bool(rule_evidence)
    evaluation = evaluate_policy_eligibility(policy_id, profile)
    reasons = list(evaluation.get("reasons") or [])
    if not has_evidence:
        reasons.insert(0, "没有规则关键词与该政策直接对应；该项仅影响候选解释，不替代资格核验")

    # 没有逐条件配置时，沿用原有细分状态，便于调用方区分“无政策级证据”。
    if (evaluation["status"] == STATUS_NEEDS_REQUIREMENT_EVALUATION
            and evaluation["strictEligibility"] == "not_evaluated" and not has_evidence):
        evaluation["status"] = STATUS_NEEDS_POLICY_EVIDENCE
    evaluation["reasons"] = reasons
    return evaluation
