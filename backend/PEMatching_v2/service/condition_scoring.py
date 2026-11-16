# -*- coding: utf-8 -*-
"""T6 条件评分验证器。

模型只提供语义状态和候选证据。本模块回读企业事实、复算数值条件，并以固定的
AND/OR 聚合和时效权重产生正式分数，因此同一输入可稳定复现。
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import date
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from service import fields as F
from service.eligibility.unit_converter import convert
from service.match_prefilter import _date_from_value, policy_year
from service.paths import MATCH_RULES_PATH


RULE_GROUPS = (
    ("hardRuleGroups", "hard"),
    ("softRuleGroups", "soft"),
    ("specialPassRuleGroups", "special_pass"),
)
VALID_STATUS = {"satisfied", "partially_satisfied", "not_satisfied", "missing"}
VALID_FAILURE_EFFECT = {
    "none", "out_of_scope", "irreversible", "growable", "ordinary_gap", "missing_data",
}


class ConditionScoringError(ValueError):
    """评分配置、模型条件或企业证据不符合新链路契约。"""


def _canonical_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(raw.encode("utf-8")).hexdigest()


@lru_cache(maxsize=2)
def _load_rules_cached(path_text: str) -> Dict[str, Any]:
    path = Path(path_text)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConditionScoringError(f"评分规则不可读取：{path}（{exc}）") from exc
    if not isinstance(payload, dict) or payload.get("schemaVersion") != "condition-match-rules-v1":
        raise ConditionScoringError("评分规则 schemaVersion 不匹配")
    if F.to_float(payload.get("missingScore")) != 10:
        raise ConditionScoringError("缺失条件分必须固定为 10")
    if round((F.to_float(payload.get("conditionWeight")) or 0) + (F.to_float(payload.get("timelinessWeight")) or 0), 8) != 1:
        raise ConditionScoringError("条件分与时效分权重之和必须为 1")
    if not isinstance(payload.get("timelinessScores"), dict):
        raise ConditionScoringError("timelinessScores 必须是对象")
    return payload


def load_scoring_rules(path: Optional[Path] = None) -> Dict[str, Any]:
    return deepcopy(_load_rules_cached(str((path or MATCH_RULES_PATH).resolve())))


def reset_scoring_rules_cache() -> None:
    _load_rules_cached.cache_clear()


def scoring_config_version(rules: Optional[Dict[str, Any]] = None) -> str:
    payload = rules if rules is not None else load_scoring_rules()
    return f"{F.text(payload.get('version')) or 'unknown'}:{_canonical_hash(payload)}"


def _group_operator(value: Any) -> str:
    return "OR" if F.text(value).upper() in {"OR", "ANY"} else "AND"


def _enum_text(value: Any) -> str:
    """`none` 是模型契约的合法枚举，不能使用会把它当缺失值的 F.text。"""
    return value.strip() if isinstance(value, str) else "" if value is None else str(value).strip()


def _policy_title(package: Dict[str, Any]) -> str:
    return F.text(package.get("title") or (package.get("policyInfo") or {}).get("policyTitle"))


def condition_catalog(package: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把规则表转换为稳定条件键目录，供模型回填和服务端复算共同使用。"""
    table = package.get("ruleTableJson") or {}
    if not isinstance(table, dict):
        return []
    result: List[Dict[str, Any]] = []
    seen_keys = set()
    for group_field, group_type in RULE_GROUPS:
        for group_index, group in enumerate(table.get(group_field) or []):
            if not isinstance(group, dict):
                continue
            branch = F.text(group.get("conditionBranchId") or group.get("conditionNo") or group_index + 1)
            group_key = f"{group_type}:{group_index + 1}:{branch}"
            operator = _group_operator(group.get("conditionOperator"))
            for item_index, item in enumerate(group.get("conditionFieldItems") or []):
                if not isinstance(item, dict):
                    continue
                rule = item.get("matchRule") or {}
                item_key = F.text(rule.get("ruleId") or item.get("itemId") or item_index + 1)
                condition_key = f"{group_key}/{item_key}"
                if condition_key in seen_keys:
                    raise ConditionScoringError(f"规则表条件键重复：{condition_key}")
                seen_keys.add(condition_key)
                result.append({
                    "conditionKey": condition_key,
                    "sourceType": "rule_table",
                    "sourceText": F.text(item.get("policyFragment") or item.get("itemName") or group.get("rawText")),
                    "groupKey": group_key,
                    "groupType": group_type,
                    "groupOperator": operator,
                    "matchRule": deepcopy(rule),
                    "itemName": F.text(item.get("itemName")),
                    "enterpriseFields": deepcopy(item.get("enterpriseFields") or []),
                })
    return result


def condition_catalog_for_model(package: Dict[str, Any]) -> List[str]:
    """模型只需回填条件键，其他规则元数据由服务端目录持有。"""
    return [item["conditionKey"] for item in condition_catalog(package)]


def _walk_profile(value: Any, path: str, result: Dict[str, Dict[str, Any]]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.startswith("_") or key in {"enterpriseName", "creditCode"}:
                continue
            _walk_profile(child, f"{path}.{key}", result)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _walk_profile(child, f"{path}[{index}]", result)
        return
    if not F.is_missing(value):
        result[path] = {"value": value, "unit": _profile_unit(path), "text": f"企业字段 {path}"}


def _profile_unit(path: str) -> str:
    units = {
        "$.metrics.companyAgeYears": "年",
        "$.metrics.revenueWan": "万元",
        "$.metrics.mainBusinessIncomeWan": "万元",
        "$.metrics.rdExpenseWan": "万元",
        "$.metrics.rdExpenseRatioPct": "%",
        "$.metrics.techContractWan": "万元",
        "$.metrics.netAssetsWan": "万元",
        "$.metrics.totalAssetsWan": "万元",
        "$.metrics.employeeCount": "人",
        "$.metrics.rdPersonnelCount": "人",
        "$.metrics.ipTotal": "件",
        "$.metrics.ipClass1Count": "件",
        "$.metrics.ipClass2Count": "件",
        "$.metrics.inventionPatentCount": "件",
    }
    return units.get(path, "")


def evidence_catalog(profile: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """返回模型输入中所有允许引用、且服务端可精确回读的企业事实。"""
    result: Dict[str, Dict[str, Any]] = {}
    _walk_profile(profile, "$", result)
    for fact in ((profile.get("_rawFactIndex") or {}).get("facts") or []):
        if not isinstance(fact, dict):
            continue
        path = F.text(fact.get("sourcePath"))
        if path and not F.is_missing(fact.get("value")):
            result[path] = {
                "value": fact.get("value"),
                "unit": "",
                "text": f"原始企业字段：{F.text(fact.get('fieldName'))}",
            }
    return result


def verify_evidence(
    entries: Any, catalog: Dict[str, Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[str], Optional[Dict[str, Any]]]:
    """按模型返回的路径回读证据；证据值和说明始终由服务端生成。"""
    if not isinstance(entries, list) or not entries:
        return [], ["没有企业证据"], None
    verified: List[Dict[str, Any]] = []
    errors: List[str] = []
    first_fact: Optional[Dict[str, Any]] = None
    for entry in entries:
        path = F.text(entry)
        candidate = catalog.get(path)
        if not candidate:
            errors.append(f"证据路径不可回读：{path or '空路径'}")
            continue
        item = {
            "path": path,
            "value": candidate["value"],
            "text": candidate["text"],
            "proxySource": None,
            "unit": candidate["unit"],
        }
        verified.append(item)
        if first_fact is None:
            first_fact = candidate
    return verified, errors, first_fact


def _bounded_score(value: Any, fallback: float = 0.0) -> float:
    number = F.to_float(value)
    return round(min(100.0, max(0.0, number if number is not None else fallback)), 2)


def _numeric_result(
    raw: Dict[str, Any], descriptor: Dict[str, Any], fact: Dict[str, Any],
) -> Tuple[str, float, Optional[str], str]:
    rule = descriptor.get("matchRule") or {}
    operator = F.text(rule.get("operator")).lower()
    target = F.to_float(rule.get("targetValue"))
    actual, _, error = convert(fact["value"], fact.get("unit"), rule.get("unit"))
    if actual is None or target is None:
        return raw["status"], _bounded_score(raw.get("score")), None, error or "数值条件无法复算"
    if operator in {"gte", "gt"}:
        passed = actual >= target if operator == "gte" else actual > target
        if passed:
            return "satisfied", 100.0, None, "服务端按最低门槛复算为满足"
        score = 0.0 if target <= 0 else _bounded_score(actual / target * 100)
        return ("partially_satisfied" if score > 0 else "not_satisfied"), score, None, "服务端按实际值/目标值复算"
    if operator in {"lte", "lt"}:
        passed = actual <= target if operator == "lte" else actual < target
        if passed:
            return "satisfied", 100.0, None, "服务端按最高上限复算为满足"
        return "not_satisfied", 0.0, "IRREVERSIBLE_UPPER_BOUND", "服务端确认超过不可逆上限"
    if operator in {"eq", "equals"}:
        passed = abs(actual - target) < 1e-9
        return ("satisfied", 100.0, None, "服务端按等值条件复算") if passed else (
            "not_satisfied", 0.0, None, "服务端按等值条件复算未满足"
        )
    return raw["status"], _bounded_score(raw.get("score")), None, "规则操作符不属于可复算数值条件"


def _normalise_raw_result(raw: Dict[str, Any], descriptor: Dict[str, Any], catalog: Dict[str, Dict[str, Any]], missing_score: float) -> Dict[str, Any]:
    status = F.text(raw.get("status"))
    failure_effect = _enum_text(raw.get("failureEffect"))
    if status not in VALID_STATUS or failure_effect not in VALID_FAILURE_EFFECT:
        raise ConditionScoringError("模型条件状态或失败效果不合法")
    verified, errors, fact = verify_evidence(raw.get("evidencePaths"), catalog)
    base = {
        "conditionKey": descriptor["conditionKey"],
        "sourceType": descriptor["sourceType"],
        "sourceText": descriptor["sourceText"],
        "groupKey": descriptor["groupKey"],
        "groupType": descriptor["groupType"],
        "groupOperator": descriptor["groupOperator"],
        "status": status,
        "failureEffect": failure_effect,
        "score": _bounded_score(raw.get("score")),
        "evidence": verified,
        "reason": F.text(raw.get("reason")),
        "confidence": F.text(raw.get("confidence")),
        "modelScore": _bounded_score(raw.get("score")),
        "evidenceValid": not errors and bool(verified),
        "evidenceErrors": errors,
        "scoreSource": "model_semantic",
    }
    if status == "missing" or errors or not verified:
        base.update({
            "status": "missing",
            "failureEffect": "missing_data",
            "score": missing_score,
            "reason": base["reason"] or "没有可回读的企业证据。",
            "scoreSource": "server_missing",
        })
        return base

    rule = descriptor.get("matchRule") or {}
    operator = F.text(rule.get("operator")).lower()
    if operator in {"gte", "gt", "lte", "lt", "eq", "equals"} and fact is not None:
        status, score, direct_code, message = _numeric_result(base, descriptor, fact)
        base.update({"status": status, "score": score, "scoreSource": "server_numeric", "serverMessage": message})
        if direct_code:
            base["directExclusionCode"] = direct_code
            base["failureEffect"] = "irreversible"
        return base
    if operator in {"is_true", "is_false"} and fact is not None:
        expected = operator == "is_true"
        if isinstance(fact["value"], bool):
            passed = fact["value"] is expected
            base.update({
                "status": "satisfied" if passed else "not_satisfied",
                "score": 100.0 if passed else 0.0,
                "scoreSource": "server_boolean",
            })
        return base
    if operator == "has_value":
        base.update({"status": "satisfied", "score": 100.0, "scoreSource": "server_presence"})
        return base
    if base["status"] == "satisfied":
        base["score"] = 100.0
    elif base["status"] == "not_satisfied":
        base["score"] = 0.0
    return base


def _missing_condition(descriptor: Dict[str, Any], missing_score: float) -> Dict[str, Any]:
    return {
        "conditionKey": descriptor["conditionKey"], "sourceType": descriptor["sourceType"],
        "sourceText": descriptor["sourceText"], "groupKey": descriptor["groupKey"],
        "groupType": descriptor["groupType"], "groupOperator": descriptor["groupOperator"],
        "status": "missing", "failureEffect": "missing_data", "score": missing_score,
        "modelScore": missing_score, "evidence": [], "reason": "模型没有返回该政策条件。",
        "confidence": "low", "evidenceValid": False, "evidenceErrors": ["模型遗漏条件"],
        "scoreSource": "server_missing",
    }


def _application_requirement_text(package: Dict[str, Any]) -> str:
    return "\n".join(
        F.text(condition.get("applicationRequirement"))
        for condition in package.get("conditions") or []
        if isinstance(condition, dict) and F.text(condition.get("applicationRequirement"))
    )


def _discovered_descriptor(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    key = F.text(raw.get("conditionKey"))
    if not key:
        return None
    group_type = F.text(raw.get("groupType"))
    if group_type not in {"hard", "soft", "special_pass"}:
        return None
    return {
        "conditionKey": key,
        "sourceType": "discovered",
        "sourceText": F.text(raw.get("sourceText")),
        "groupKey": F.text(raw.get("groupKey")),
        "groupType": group_type,
        "groupOperator": _group_operator(raw.get("groupOperator")),
        "matchRule": {},
    }


def _group_scores(results: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for item in results:
        grouped.setdefault(item["groupKey"], []).append(item)
    output = []
    for group_key, items in grouped.items():
        operator = items[0]["groupOperator"]
        values = [float(item["score"]) for item in items]
        score = max(values) if operator == "OR" else sum(values) / len(values)
        output.append({
            "groupKey": group_key,
            "groupType": items[0]["groupType"],
            "operator": operator,
            "score": round(score, 2),
            "conditionKeys": [item["conditionKey"] for item in items],
        })
    return sorted(output, key=lambda item: item["groupKey"])


def application_window(package: Dict[str, Any], today: Optional[date] = None, rules: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """区分申报期和政策有效期，按新规则给时效状态和分数。"""
    reference = today or date.today()
    effective_rules = rules or load_scoring_rules()
    windows = [item for item in package.get("conditions") or [] if isinstance(item, dict)]
    selected = windows[0] if windows else {}
    start = _date_from_value(selected.get("applicationStartTime"))
    end = _date_from_value(selected.get("applicationEndTime"))
    policy_year_value, _, _ = policy_year(package)
    if policy_year_value is not None and policy_year_value <= reference.year - 2:
        status = "unknown"
    elif policy_year_value == reference.year - 1:
        status = "last_year"
    elif start and start > reference:
        status = "upcoming"
    elif end and end < reference:
        status = "closed"
    elif start and start <= reference and (end is None or end >= reference):
        status = "open"
    elif policy_year_value == reference.year:
        status = "unknown"
    else:
        status = "unknown"
    scores = effective_rules["timelinessScores"]
    return {
        "status": status,
        "startTime": F.text(selected.get("applicationStartTime")) or None,
        "endTime": F.text(selected.get("applicationEndTime")) or None,
        "instruction": F.text(selected.get("applicationInstruction")),
        "score": _bounded_score(scores.get(status, scores.get("unknown", 50))),
    }


def score_policy_match(
    package: Dict[str, Any], profile: Dict[str, Any], model_output: Dict[str, Any], *,
    today: Optional[date] = None, rules: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """把一个经过语法契约校验的模型输出转换为正式可审计结果。"""
    effective_rules = rules or load_scoring_rules()
    missing_score = float(effective_rules["missingScore"])
    expected = condition_catalog(package)
    expected_by_key = {item["conditionKey"]: item for item in expected}
    evidence = evidence_catalog(profile)
    raw_results = model_output.get("conditionResults") or []
    actual_by_key: Dict[str, Dict[str, Any]] = {}
    invalid_keys: List[str] = []
    for raw in raw_results:
        if not isinstance(raw, dict):
            continue
        key = F.text(raw.get("conditionKey"))
        descriptor = expected_by_key.get(key)
        if descriptor is None or key in actual_by_key:
            invalid_keys.append(key or "<empty>")
            continue
        actual_by_key[key] = _normalise_raw_result(raw, descriptor, evidence, missing_score)
    results = [actual_by_key.get(item["conditionKey"], _missing_condition(item, missing_score)) for item in expected]

    requirement = _application_requirement_text(package)
    raw_discovered = model_output.get("discoveredConditions") or []
    discovered_keys = set(expected_by_key)
    invalid_discovered = 0
    if expected:
        # 规则表是已审核条件目录；正文只用于解释和冲突审计，不能扩充正式评分条件。
        invalid_discovered = len(raw_discovered) if isinstance(raw_discovered, list) else 1
    else:
        # 仅当规则表没有有效条件时，才允许从申报要求正文回退发现条件。
        for raw in raw_discovered:
            if not isinstance(raw, dict):
                invalid_discovered += 1
                continue
            descriptor = _discovered_descriptor(raw)
            source_text = F.text(raw.get("sourceText"))
            if not descriptor or descriptor["conditionKey"] in discovered_keys or not source_text or source_text not in requirement:
                invalid_discovered += 1
                continue
            discovered_keys.add(descriptor["conditionKey"])
            results.append(_normalise_raw_result(raw, descriptor, evidence, missing_score))

    groups = _group_scores(results)
    condition_score = round(sum(group["score"] for group in groups) / len(groups), 2) if groups else None
    window = application_window(package, today=today, rules=effective_rules)
    final_score = None if condition_score is None else round(
        condition_score * float(effective_rules["conditionWeight"]) + window["score"] * float(effective_rules["timelinessWeight"]), 2
    )
    direct_exclusion = None
    irreversible = next((item for item in results if item.get("directExclusionCode")), None)
    semantic_exclusion = next((
        item for item in results
        if item["evidenceValid"] and item["failureEffect"] in {"out_of_scope", "irreversible"}
        and item["status"] == "not_satisfied"
    ), None)
    if irreversible:
        direct_exclusion = {
            "code": irreversible["directExclusionCode"],
            "message": "服务端复算确认不可逆上限不满足。",
            "evidence": irreversible["evidence"],
        }
    elif semantic_exclusion:
        direct_exclusion = {
            "code": (
                "SEMANTIC_OUT_OF_SCOPE"
                if semantic_exclusion["failureEffect"] == "out_of_scope"
                else "SEMANTIC_IRREVERSIBLE"
            ),
            "message": semantic_exclusion["reason"] or "企业事实不符合不可逆政策条件。",
            "evidence": semantic_exclusion["evidence"],
        }

    effective_matches = [
        item for item in results
        if item["evidenceValid"] and item["status"] != "missing" and item["score"] > 0
    ]
    no_policy_conditions = not expected and not requirement
    recommendable = not direct_exclusion and (bool(effective_matches) or no_policy_conditions)
    reason = (
        "政策条件数据待补齐。" if no_policy_conditions else
        f"已核验 {len(effective_matches)}/{len(results)} 个存在企业事实的政策条件。"
    )
    return {
        "fusionPolicyId": F.text(package.get("fusionPolicyId")),
        "policyTitle": _policy_title(package),
        "recommendable": recommendable,
        "dataPending": no_policy_conditions,
        "reason": reason,
        "conditionResults": results,
        "groupResults": groups,
        "sourceConflicts": deepcopy(model_output.get("sourceConflicts") or []),
        "directExclusion": direct_exclusion,
        "verifiedConditionScore": condition_score,
        "timelinessScore": window["score"],
        "applicationWindow": {key: value for key, value in window.items() if key != "score"},
        "finalScore": final_score,
        "invalidConditionKeys": invalid_keys,
        "invalidDiscoveredConditionCount": invalid_discovered,
        "scoringConfigVersion": scoring_config_version(effective_rules),
    }
