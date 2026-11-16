# -*- coding: utf-8 -*-
"""资格门禁服务入口，不调用 V2 推荐链路。"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from service import fields as F
from service.eligibility.constants import GROUP_KEYS, RESULT_SCHEMA
from service.eligibility.group_evaluator import evaluate_group
from service.eligibility.model_evidence import is_enabled, resolve_condition_facts
from service.eligibility.profile_loader import ProfileLoadError, get_profile, load_snapshot
from service.eligibility.score_aggregator import aggregate


def evaluate_policy(
    enterprise_profile: Dict[str, Any],
    policy_id: Any,
    *,
    include_condition_details: bool = True,
    use_llm_evidence: bool | None = None,
) -> Dict[str, Any]:
    policy_id = F.text(policy_id)
    try:
        profile = get_profile(policy_id)
    except ProfileLoadError as exc:
        return {"fusionPolicyId": policy_id, "gateStatus": "not_evaluable", "directPushAllowed": False, "conditionMatchScore": None, "gateReasons": ["RULE_PROFILE_INVALID", str(exc)]}
    if not profile:
        return {"fusionPolicyId": policy_id, "gateStatus": "not_evaluable", "directPushAllowed": False, "conditionMatchScore": None, "ruleCoverage": "none", "gateReasons": ["RULE_PROFILE_NOT_FOUND"], "conditionDetails": [], "ruleGroupDetails": []}
    condition_items = [
        item
        for group_type in GROUP_KEYS
        for group in profile.get(group_type) or []
        for item in group.get("conditionFieldItems") or []
        if isinstance(item, dict)
    ]
    model_facts = resolve_condition_facts(enterprise_profile, condition_items, enabled=use_llm_evidence)
    groups = [
        evaluate_group(enterprise_profile, group, group_type=group_type, model_facts=model_facts)
        for group_type in GROUP_KEYS
        for group in profile.get(group_type) or []
    ]
    result = {"fusionPolicyId": policy_id, "policyTitle": profile.get("policyTitle"), **aggregate(profile, groups)}
    result["modelEvidence"] = {
        "requested": is_enabled(use_llm_evidence),
        "resolvedConditionCount": len(model_facts),
    }
    result["conditionDetails"] = [item for group in groups for item in group.get("items") or []] if include_condition_details else []
    result["ruleGroupDetails"] = groups if include_condition_details else []
    return result


def evaluate_policies(
    enterprise_profile: Dict[str, Any],
    policy_ids: Iterable[Any],
    *,
    include_condition_details: bool = True,
    use_llm_evidence: bool | None = None,
) -> Dict[str, Any]:
    manifest, _ = load_snapshot()
    evaluations = []
    for policy_id in policy_ids:
        policy_text = F.text(policy_id)
        try:
            evaluations.append(evaluate_policy(
                enterprise_profile,
                policy_text,
                include_condition_details=include_condition_details,
                use_llm_evidence=use_llm_evidence,
            ))
        except Exception as exc:  # 单条异常绝不能中断同一请求中的其他政策
            evaluations.append({"fusionPolicyId": policy_text, "gateStatus": "not_evaluable", "directPushAllowed": False, "conditionMatchScore": None, "gateReasons": ["POLICY_EVALUATION_FAILED", f"{type(exc).__name__}: {exc}"]})
    return {"eligibilityEvaluations": evaluations, "eligibilitySchemaVersion": RESULT_SCHEMA, "ruleManifestHash": manifest.get("contentSha256", "")}
