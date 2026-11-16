# -*- coding: utf-8 -*-
"""组结果聚合和资格门禁状态机。"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from service.eligibility.constants import PENDING_EVIDENCE, RECOMMENDATION_SCORE_THRESHOLD, SPECIAL_PASS_MIN_SCORE


def _all_items(groups: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [item for group in groups for item in group.get("items") or []]


def aggregate(profile: Dict[str, Any], groups: List[Dict[str, Any]]) -> Dict[str, Any]:
    hard_groups = [group for group in groups if group.get("groupType") == "hardRuleGroups"]
    special_groups = [group for group in groups if group.get("groupType") == "specialPassRuleGroups"]
    normal_groups = [group for group in groups if group.get("groupType") != "specialPassRuleGroups"]
    normal_items = _all_items(normal_groups)
    special_items = _all_items(special_groups)
    all_items = normal_items + special_items
    hard_failed = any(group.get("groupStatus") == "fail" for group in hard_groups)
    special_pass_hit = any(group.get("groupStatus") == "pass" and all(item.get("evidence", {}).get("evidenceTier") in {"A1_exact", "A2_derived"} for item in group.get("items") or []) for group in special_groups)
    scored = [item for item in normal_items if item.get("contributesToScore") and item.get("score") is not None]
    score = round(sum(float(item["score"]) for item in scored) / len(scored), 4) if scored else None
    if hard_failed:
        final_score = 0.0
    elif special_pass_hit:
        final_score = max(score or 0.0, SPECIAL_PASS_MIN_SCORE)
    else:
        final_score = score
    pending = [item for item in all_items if item.get("evalStatus") in {"missing", "not_evaluable"} or item.get("evidence", {}).get("evidenceTier") in PENDING_EVIDENCE]
    failed = [item for item in all_items if item.get("evalStatus") == "fail"]
    verified = [item for item in all_items if item.get("evidence", {}).get("evidenceTier") in {"A1_exact", "A2_derived"}]
    default_missing = [item for item in all_items if item.get("passSource") == "default_missing_assumed"]
    coverage = profile.get("coverage")
    reasons: List[str] = []
    if coverage in {"empty", "invalid"} or not all_items:
        status, reasons = "not_evaluable", ["RULE_PROFILE_EMPTY" if coverage == "empty" else "NO_SCORABLE_CONDITION"]
    elif hard_failed:
        status, reasons = "not_recommended", ["HARD_CONDITION_FAILED"]
    elif coverage != "complete" or pending:
        status = "needs_supplement"
        reasons = (["RULE_PROFILE_PARTIAL"] if coverage != "complete" else []) + (["MISSING_ENTERPRISE_FACT"] if any(item.get("evalStatus") == "missing" for item in pending) else []) + (["CONDITION_NOT_EVALUABLE"] if any(item.get("evalStatus") == "not_evaluable" for item in pending) else [])
    elif final_score is not None and final_score < RECOMMENDATION_SCORE_THRESHOLD:
        status, reasons = "not_recommended", ["SCORE_BELOW_THRESHOLD"]
    else:
        status, reasons = "direct_push", ["SPECIAL_PASS_VERIFIED"] if special_pass_hit else []
    return {
        "gateStatus": status, "directPushAllowed": status == "direct_push", "conditionMatchScore": round(final_score * 100, 2) if final_score is not None else None,
        "hardPass": not hard_failed, "specialPassHit": special_pass_hit, "ruleCoverage": coverage,
        "scoredConditionCount": len(scored), "verifiedConditionCount": len(verified), "defaultMissingCount": len(default_missing),
        "pendingConditionCount": len(pending), "failedConditionCount": len(failed), "evidenceCoverage": round(len(verified) / len(all_items), 4) if all_items else 0.0,
        "gateReasons": reasons,
    }
