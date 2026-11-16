# -*- coding: utf-8 -*-
"""政策条件包组装、质量检查、稳定哈希与本地快照。"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from service import fields as F
from service.paths import POLICY_CONDITION_SNAPSHOT_PATH, atomic_write_json
from service.policy_sync import load_snapshot as load_policy_snapshot

PACKAGE_SCHEMA = "policy-condition-package-v1"
PACKAGE_SNAPSHOT_SCHEMA = "policy-condition-package-snapshot-v1"
BUILDER_VERSION = "t2-v1"

POLICY_INFO_FIELDS = (
    "policyTitle",
    "summary",
    "policyAttribute",
    "policyOriginalTitle",
    "sceneUsage",
    "sceneUsagePath",
    "applicableScope",
    "applicableScopePath",
    "industryField",
    "industryFieldPath",
    "effectiveStartDate",
    "effectiveEndDate",
    "issuingDepartment",
    "policyCode",
)
CONDITION_FIELDS = (
    "id",
    "applicableTarget",
    "applicableTargetPath",
    "applicationInstruction",
    "applicationRequirement",
    "applicationStartTime",
    "applicationEndTime",
    "announcementDate",
)
SUPPORT_METHOD_FIELDS = (
    "id",
    "supportType",
    "supportTypePath",
    "contentDesc",
    "amount",
    "maxAmount",
)
KEYWORD_FIELDS = ("id", "text", "pathText")
DOCUMENT_FIELDS = (
    "publishDate",
    "policyLevel",
    "issuingDepartment",
    "policyAttribute",
    "policyTitle",
    "reviewStatus",
)
RULE_GROUP_KEYS = (
    ("hard", "hardRuleGroups"),
    ("soft", "softRuleGroups"),
    ("special_pass", "specialPassRuleGroups"),
)
TARGET_REQUIRED_OPERATORS = {
    "gt",
    "gte",
    "lt",
    "lte",
    "eq",
    "neq",
    "in",
    "not_in",
    "between",
    "range",
}
OR_EXPRESSION_PATTERN = re.compile(r"或者|任一|满足其一|二选一|或")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class PolicyConditionPackageError(ValueError):
    """政策条件包无法组装或校验。"""


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _select_fields(source: Any, fields: Tuple[str, ...]) -> Dict[str, Any]:
    if not isinstance(source, dict):
        return {field: "" for field in fields}
    return {
        field: deepcopy(source.get(field)) if source.get(field) is not None else ""
        for field in fields
    }


def _select_list(items: Any, fields: Tuple[str, ...]) -> List[Dict[str, Any]]:
    if not isinstance(items, list):
        return []
    return [
        _select_fields(item, fields)
        for item in items
        if isinstance(item, dict)
    ]


def _conditions(policy: Dict[str, Any]) -> List[Dict[str, Any]]:
    source = policy.get("conditions")
    if not isinstance(source, list):
        source = policy.get("structuredConditions")
    return _select_list(source, CONDITION_FIELDS)


def _application_requirements(conditions: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    requirements = []
    for index, condition in enumerate(conditions):
        text = condition.get("applicationRequirement")
        if isinstance(text, str) and text.strip():
            requirements.append((f"$.conditions[{index}].applicationRequirement", text))
    return requirements


def _iter_rule_groups(rule_table: Any) -> Iterator[Tuple[str, str, int, Dict[str, Any]]]:
    if not isinstance(rule_table, dict):
        return
    for group_type, key in RULE_GROUP_KEYS:
        for index, group in enumerate(rule_table.get(key) or []):
            if isinstance(group, dict):
                yield group_type, key, index, group


def _is_empty_target(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _issue(
    code: str,
    severity: str,
    message: str,
    locations: List[str],
) -> Dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "locations": locations,
    }


def _quality_flags(
    policy: Dict[str, Any],
    conditions: List[Dict[str, Any]],
    rule_table: Any,
) -> Dict[str, Any]:
    requirements = _application_requirements(conditions)
    original_or_locations = [
        location
        for location, text in requirements
        if OR_EXPRESSION_PATTERN.search(text)
    ]
    group_operators: List[str] = []
    unresolved_locations: List[str] = []
    llm_judge_locations: List[str] = []
    empty_target_locations: List[str] = []
    missing_required_target_locations: List[str] = []
    rule_group_count = 0
    rule_item_count = 0

    for _, key, group_index, group in _iter_rule_groups(rule_table):
        rule_group_count += 1
        operator = F.text(group.get("conditionOperator")).upper() or "MISSING"
        group_operators.append(operator)
        items = group.get("conditionFieldItems") or []
        for item_index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            rule_item_count += 1
            location = (
                f"$.ruleTableJson.{key}[{group_index}]."
                f"conditionFieldItems[{item_index}]"
            )
            if F.text(item.get("itemStatus")).lower() == "unresolved":
                unresolved_locations.append(location)
            match_rule = item.get("matchRule") or {}
            match_operator = F.text(match_rule.get("operator")).lower()
            if match_operator == "llm_judge":
                llm_judge_locations.append(f"{location}.matchRule.operator")
            if _is_empty_target(match_rule.get("targetValue")):
                target_location = f"{location}.matchRule.targetValue"
                empty_target_locations.append(target_location)
                if match_operator in TARGET_REQUIRED_OPERATORS:
                    missing_required_target_locations.append(target_location)

    rule_table_empty = rule_item_count == 0
    rule_table_all_and = bool(group_operators) and all(
        operator == "AND" for operator in group_operators
    )
    suspected_or_mismatch = bool(original_or_locations) and rule_table_all_and
    source_hash = F.text(policy.get("policyContentHash"))
    source_hash_missing = not bool(SHA256_PATTERN.fullmatch(source_hash))

    issues: List[Dict[str, Any]] = []
    if rule_table_empty:
        issues.append(_issue(
            "RULE_TABLE_EMPTY",
            "warning",
            "规则表没有硬性、软性或特殊通道评分项，必须保留申报条件原文。",
            ["$.ruleTableJson"],
        ))
    if unresolved_locations:
        issues.append(_issue(
            "UNRESOLVED_RULE_ITEMS",
            "info",
            "存在未绑定标准企业字段的规则项；该状态不代表条件无效。",
            unresolved_locations,
        ))
    if llm_judge_locations:
        issues.append(_issue(
            "LLM_JUDGE_ITEMS",
            "info",
            "存在需要语义判断或组合事实判断的规则项。",
            llm_judge_locations,
        ))
    if suspected_or_mismatch:
        issues.append(_issue(
            "OR_OPERATOR_MISMATCH_RISK",
            "warning",
            "申报条件原文包含 OR 表达，但现有评分规则组全部标记为 AND。",
            original_or_locations,
        ))
    if empty_target_locations:
        issues.append(_issue(
            "EMPTY_TARGET_VALUES",
            "info",
            "存在空目标值；定性条件仅记录，定量条件需进一步核验。",
            empty_target_locations,
        ))
    if missing_required_target_locations:
        issues.append(_issue(
            "MISSING_REQUIRED_TARGET_VALUES",
            "warning",
            "存在定量运算符缺少目标值的规则项。",
            missing_required_target_locations,
        ))
    if not requirements:
        issues.append(_issue(
            "APPLICATION_REQUIREMENT_EMPTY",
            "warning",
            "政策没有有效 applicationRequirement 原文。",
            ["$.conditions"],
        ))
    if source_hash_missing:
        issues.append(_issue(
            "SOURCE_POLICY_HASH_MISSING",
            "warning",
            "来源政策缺少 T1 内容指纹，条件包使用本地确定性指纹兜底。",
            ["$.policyContentHash"],
        ))

    return {
        "ruleTableEmpty": rule_table_empty,
        "hasUnresolvedItems": bool(unresolved_locations),
        "hasLlmJudgeItems": bool(llm_judge_locations),
        "originalContainsOrExpression": bool(original_or_locations),
        "ruleTableAllAnd": rule_table_all_and,
        "suspectedOrOperatorMismatch": suspected_or_mismatch,
        "hasEmptyTargetValues": bool(empty_target_locations),
        "hasMissingRequiredTargetValues": bool(missing_required_target_locations),
        "hasApplicationRequirement": bool(requirements),
        "sourcePolicyHashMissing": source_hash_missing,
        "applicationRequirementCount": len(requirements),
        "ruleGroupCount": rule_group_count,
        "ruleItemCount": rule_item_count,
        "unresolvedItemCount": len(unresolved_locations),
        "llmJudgeItemCount": len(llm_judge_locations),
        "emptyTargetValueCount": len(empty_target_locations),
        "missingRequiredTargetValueCount": len(missing_required_target_locations),
        "ruleTableOperators": sorted(set(group_operators)),
        "issues": issues,
    }


def _source_policy_hash(policy: Dict[str, Any]) -> str:
    source_hash = F.text(policy.get("policyContentHash"))
    if SHA256_PATTERN.fullmatch(source_hash):
        return source_hash
    content = {
        key: value
        for key, value in policy.items()
        if key != "policyContentHash"
    }
    return _sha256(content)


def build_condition_package(policy: Dict[str, Any]) -> Dict[str, Any]:
    """从单条 T1 政策记录组装精简、完整且可审计的条件包。"""
    if not isinstance(policy, dict):
        raise PolicyConditionPackageError(
            f"政策记录必须是对象，实际 {type(policy).__name__}"
        )
    policy_id = F.text(policy.get("fusionPolicyId") or policy.get("id"))
    if not policy_id:
        raise PolicyConditionPackageError("政策记录缺少 fusionPolicyId")

    conditions = _conditions(policy)
    rule_table = deepcopy(policy.get("ruleTableJson"))
    package = {
        "schemaVersion": PACKAGE_SCHEMA,
        "fusionPolicyId": policy_id,
        "sourcePolicyId": F.text(policy.get("sourcePolicyId")),
        "title": F.text(policy.get("title")),
        "policyInfo": _select_fields(policy.get("policyInfo"), POLICY_INFO_FIELDS),
        "conditions": conditions,
        "ruleTableJson": rule_table,
        "supportMethods": _select_list(
            policy.get("supportMethods"), SUPPORT_METHOD_FIELDS
        ),
        "keywords": _select_list(policy.get("keywords"), KEYWORD_FIELDS),
        "documents": _select_list(policy.get("documents"), DOCUMENT_FIELDS),
        "relatedPolicies": deepcopy(policy.get("relatedPolicies") or {}),
        "policyContentHash": _source_policy_hash(policy),
        "qualityFlags": _quality_flags(policy, conditions, rule_table),
        "builderVersion": BUILDER_VERSION,
    }
    package["conditionPackageHash"] = _sha256(package)
    validate_condition_package(package)
    return package


def validate_condition_package(package: Any) -> Dict[str, Any]:
    """执行无需第三方依赖的核心契约与哈希校验。"""
    if not isinstance(package, dict):
        raise PolicyConditionPackageError("条件包根节点不是对象")
    if package.get("schemaVersion") != PACKAGE_SCHEMA:
        raise PolicyConditionPackageError("条件包 schemaVersion 不匹配")
    if not F.text(package.get("fusionPolicyId")):
        raise PolicyConditionPackageError("条件包缺少 fusionPolicyId")
    for key in ("conditions", "supportMethods", "keywords", "documents"):
        if not isinstance(package.get(key), list):
            raise PolicyConditionPackageError(f"条件包 {key} 不是数组")
    if not isinstance(package.get("policyInfo"), dict):
        raise PolicyConditionPackageError("条件包 policyInfo 不是对象")
    if not isinstance(package.get("relatedPolicies"), dict):
        raise PolicyConditionPackageError("条件包 relatedPolicies 不是对象")
    if not isinstance(package.get("qualityFlags"), dict):
        raise PolicyConditionPackageError("条件包 qualityFlags 不是对象")
    source_hash = F.text(package.get("policyContentHash"))
    if not SHA256_PATTERN.fullmatch(source_hash):
        raise PolicyConditionPackageError("条件包 policyContentHash 非法")
    actual_hash = F.text(package.get("conditionPackageHash"))
    content = {
        key: value
        for key, value in package.items()
        if key != "conditionPackageHash"
    }
    if actual_hash != _sha256(content):
        raise PolicyConditionPackageError("条件包内容哈希校验失败")
    return package


def _snapshot_version(packages: List[Dict[str, Any]]) -> str:
    fingerprints = sorted(
        (
            {
                "fusionPolicyId": package["fusionPolicyId"],
                "conditionPackageHash": package["conditionPackageHash"],
            }
            for package in packages
        ),
        key=lambda item: item["fusionPolicyId"],
    )
    return _sha256(fingerprints)


def build_condition_package_snapshot(
    policy_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """为一份完整政策快照生成对应条件包快照。"""
    source = policy_snapshot or load_policy_snapshot()
    records = source.get("data")
    if not isinstance(records, list):
        raise PolicyConditionPackageError("政策快照 data 不是数组")
    source_count = source.get("count")
    if not isinstance(source_count, int) or source_count != len(records):
        raise PolicyConditionPackageError(
            f"政策快照 count 不一致：count={source_count}, actual={len(records)}"
        )
    packages = [build_condition_package(record) for record in records]
    snapshot = {
        "schemaVersion": PACKAGE_SNAPSHOT_SCHEMA,
        "builtAt": _now_iso(),
        "builderVersion": BUILDER_VERSION,
        "sourceSnapshotSchemaVersion": F.text(source.get("schemaVersion")),
        "sourceSnapshotVersion": F.text(source.get("versionHash")) or _sha256(records),
        "count": len(packages),
        "versionHash": _snapshot_version(packages),
        "data": packages,
    }
    validate_condition_package_snapshot(snapshot)
    return snapshot


def validate_condition_package_snapshot(snapshot: Any) -> Dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise PolicyConditionPackageError("条件包快照根节点不是对象")
    if snapshot.get("schemaVersion") != PACKAGE_SNAPSHOT_SCHEMA:
        raise PolicyConditionPackageError("条件包快照 schemaVersion 不匹配")
    packages = snapshot.get("data")
    if not isinstance(packages, list):
        raise PolicyConditionPackageError("条件包快照 data 不是数组")
    if snapshot.get("count") != len(packages):
        raise PolicyConditionPackageError("条件包快照 count 不一致")
    for package in packages:
        validate_condition_package(package)
    if snapshot.get("versionHash") != _snapshot_version(packages):
        raise PolicyConditionPackageError("条件包快照版本哈希校验失败")
    return snapshot


def write_condition_package_snapshot(snapshot: Dict[str, Any]) -> None:
    validate_condition_package_snapshot(snapshot)
    atomic_write_json(POLICY_CONDITION_SNAPSHOT_PATH, snapshot)


def load_condition_package_snapshot(
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    target = path or POLICY_CONDITION_SNAPSHOT_PATH
    if not target.exists():
        raise FileNotFoundError(f"政策条件包快照不存在：{target}")
    try:
        snapshot = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PolicyConditionPackageError(
            f"政策条件包快照不是合法 JSON：{target}（{exc}）"
        ) from exc
    return validate_condition_package_snapshot(snapshot)


def condition_package_stats(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    packages = snapshot.get("data") or []
    return {
        "count": len(packages),
        "versionHash": F.text(snapshot.get("versionHash")),
        "sourceSnapshotVersion": F.text(snapshot.get("sourceSnapshotVersion")),
        "ruleTableEmpty": sum(
            1 for package in packages if package["qualityFlags"]["ruleTableEmpty"]
        ),
        "hasUnresolvedItems": sum(
            1 for package in packages if package["qualityFlags"]["hasUnresolvedItems"]
        ),
        "hasLlmJudgeItems": sum(
            1 for package in packages if package["qualityFlags"]["hasLlmJudgeItems"]
        ),
        "suspectedOrOperatorMismatch": sum(
            1
            for package in packages
            if package["qualityFlags"]["suspectedOrOperatorMismatch"]
        ),
        "hasEmptyTargetValues": sum(
            1 for package in packages if package["qualityFlags"]["hasEmptyTargetValues"]
        ),
        "hasMissingRequiredTargetValues": sum(
            1
            for package in packages
            if package["qualityFlags"]["hasMissingRequiredTargetValues"]
        ),
    }
