# -*- coding: utf-8 -*-
"""资格规则快照的迁移、校验和稳定序列化。"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, Iterable, List, Tuple

from service import fields as F
from service.eligibility.constants import GROUP_KEYS, PROFILE_SCHEMA, VALID_OPERATORS, VALID_RULE_KINDS


class ProfileValidationError(ValueError):
    """规则画像的结构或可执行语义不合法。"""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def semantic_item_key(policy_id: str, group_type: str, group_index: int, item_index: int) -> str:
    return f"{policy_id}:{group_type}:{group_index}:{item_index}"


def _as_list(value: Any, label: str) -> List[Dict[str, Any]]:
    if value in (None, ""):
        return []
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ProfileValidationError(f"{label} 必须是对象数组")
    return value


def _normalise_item(item: Dict[str, Any], policy_id: str, group_type: str, group_index: int, item_index: int) -> Dict[str, Any]:
    output = copy.deepcopy(item)
    rule = output.get("matchRule")
    if not isinstance(rule, dict):
        raise ProfileValidationError(f"{policy_id} {group_type}[{group_index}] 条件项缺少 matchRule")
    operator = F.text(rule.get("operator")).lower()
    kind = F.text(rule.get("ruleKind")).lower()
    if operator not in VALID_OPERATORS:
        raise ProfileValidationError(f"{policy_id} 条件 {item_index} 存在未知操作符 {operator!r}")
    if kind not in VALID_RULE_KINDS:
        raise ProfileValidationError(f"{policy_id} 条件 {item_index} 存在非法 ruleKind {kind!r}")
    if operator not in {"has_value", "llm_judge", "is_true", "is_false"} and rule.get("targetValue") in (None, ""):
        raise ProfileValidationError(f"{policy_id} 条件 {item_index} 缺少 targetValue")
    output["semanticItemKey"] = semantic_item_key(policy_id, group_type, group_index, item_index)
    output.setdefault("enterpriseFields", [])
    return output


def migrate_rule_table(policy_id: str, policy_title: str, rule_table: Dict[str, Any], *, source_updated_at: str = "") -> Dict[str, Any]:
    """把 V1 规则表复制为 V2 自有画像，不解释或丢弃原文容器。"""
    policy_id = F.text(policy_id or rule_table.get("fusionPolicyId"))
    if not policy_id:
        raise ProfileValidationError("缺少 fusionPolicyId")
    if not isinstance(rule_table, dict):
        raise ProfileValidationError(f"{policy_id} ruleTableJson 必须是对象")

    profile: Dict[str, Any] = {
        "schemaVersion": PROFILE_SCHEMA,
        "fusionPolicyId": policy_id,
        "policyTitle": F.text(policy_title or rule_table.get("policyDisplayName")),
        "sourcePolicyUpdatedAt": F.text(source_updated_at),
        "sourceRuleTableVersion": F.text(rule_table.get("schemaVersion")),
        "sourceRuleTableHash": sha256(rule_table),
        "migrationStatus": "migrated",
        "coverage": "complete",
        "definitionNotes": copy.deepcopy(rule_table.get("definitionNotes") or []),
        "applicationChecklistGroups": copy.deepcopy(rule_table.get("applicationChecklistGroups") or []),
        "migrationWarnings": [],
    }
    executable_count = 0
    unresolved_count = 0
    for group_type in GROUP_KEYS:
        groups: List[Dict[str, Any]] = []
        for group_index, source_group in enumerate(_as_list(rule_table.get(group_type), group_type), start=1):
            group = copy.deepcopy(source_group)
            items = _as_list(group.get("conditionFieldItems"), f"{policy_id}.{group_type}.conditionFieldItems")
            group["conditionFieldItems"] = [
                _normalise_item(item, policy_id, group_type, group_index, item_index)
                for item_index, item in enumerate(items, start=1)
            ]
            group.setdefault("conditionOperator", "AND")
            group.setdefault("unresolvedFragments", [])
            if not isinstance(group["unresolvedFragments"], list):
                raise ProfileValidationError(f"{policy_id} {group_type}[{group_index}] unresolvedFragments 必须是数组")
            unresolved_count += len(group["unresolvedFragments"])
            unresolved_count += sum(1 for item in group["conditionFieldItems"] if F.text(item.get("itemStatus")).lower() == "unresolved")
            executable_count += len(group["conditionFieldItems"])
            groups.append(group)
        profile[group_type] = groups
    if not executable_count:
        profile["coverage"] = "empty"
    elif unresolved_count:
        profile["coverage"] = "partial"
        profile["migrationWarnings"].append(f"存在 {unresolved_count} 个未解析片段或条件项")
    profile["profileHash"] = sha256({key: value for key, value in profile.items() if key != "profileHash"})
    return profile


def profile_stats(profiles: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    items = list(profiles)
    return {
        "profileCount": len(items),
        "executableProfileCount": sum(any(profile.get(key) for key in GROUP_KEYS) for profile in items),
        "emptyProfileCount": sum(profile.get("coverage") == "empty" for profile in items),
        "partialProfileCount": sum(profile.get("coverage") == "partial" for profile in items),
        "hardGroupCount": sum(len(profile.get("hardRuleGroups") or []) for profile in items),
        "softGroupCount": sum(len(profile.get("softRuleGroups") or []) for profile in items),
        "specialPassGroupCount": sum(len(profile.get("specialPassRuleGroups") or []) for profile in items),
    }
