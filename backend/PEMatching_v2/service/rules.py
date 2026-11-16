# -*- coding: utf-8 -*-
"""四通道规则引擎（确定性，可单测）。

规则定义在 config/rules.json，引擎只做解释执行，增删调整规则不改代码。

三态判定是本引擎的核心：
    matched  — 条件全部满足
    unmatched— 条件可判定但不满足
    skipped  — requiredFields 里有字段为 unknown，判不了
skipped 既不算命中也不算未命中，响应里单列，不静默截断。

通道顺序：G0 拦截 → G2 直推 → G3 链路 → G4 精推（需 enableStage2）。
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from service import fields as F
from service.paths import RULES_PATH

RULES_SCHEMA = "pematching-v2-rules-v1"

MATCHED = "matched"
UNMATCHED = "unmatched"
SKIPPED = "skipped"

CHANNELS = ("G0", "G2", "G3", "G4")
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


@lru_cache(maxsize=1)
def load_rules() -> List[Dict[str, Any]]:
    """加载规则定义。"""
    if not RULES_PATH.exists():
        raise FileNotFoundError(f"规则配置不存在：{RULES_PATH}")
    try:
        payload = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"规则配置不是合法 JSON：{RULES_PATH}（{exc}）") from exc
    if payload.get("schemaVersion") != RULES_SCHEMA:
        raise ValueError(
            f"规则配置 schemaVersion 不匹配：{RULES_PATH}，"
            f"期望 {RULES_SCHEMA}，实际 {payload.get('schemaVersion')!r}"
        )
    rules = payload.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError(f"规则配置缺少 rules 数组：{RULES_PATH}")
    return rules


def reset_cache() -> None:
    load_rules.cache_clear()


def _resolve_path(profile: Dict[str, Any], path: str) -> Any:
    """按点分路径取画像里的值。

    特殊路径 _businessText：拼接业务描述文本，供 textContainsAny 用。
    """
    if path == "_businessText":
        parts: List[str] = []
        for basis in (profile.get("tagBasis") or {}).get("C") or []:
            parts.append(str(basis))
        metrics = profile.get("metrics") or {}
        parts.append(str(metrics.get("metricsYear") or ""))
        return " ".join(parts)

    current: Any = profile
    for segment in path.split("."):
        if isinstance(current, dict):
            current = current.get(segment)
        else:
            return None
    return current


def _evaluate_condition(profile: Dict[str, Any], condition: Dict[str, Any]) -> Tuple[bool, str]:
    """判定单个条件。返回 (是否满足, 说明)。"""
    field = str(condition.get("field") or "")
    operator = str(condition.get("op") or "")
    expected = condition.get("value")
    actual = _resolve_path(profile, field)

    if operator == "contains":
        items = actual if isinstance(actual, list) else []
        hit = expected in items
        return hit, f"{field}={items} {'含' if hit else '不含'} {expected}"

    if operator == "containsAny":
        items = set(actual if isinstance(actual, list) else [])
        wanted = set(expected or [])
        overlap = sorted(items & wanted)
        return bool(overlap), (
            f"{field} 与 {sorted(wanted)} 交集={overlap}" if overlap
            else f"{field}={sorted(items)} 与 {sorted(wanted)} 无交集"
        )

    if operator == "notContainsAny":
        items = set(actual if isinstance(actual, list) else [])
        wanted = set(expected or [])
        overlap = sorted(items & wanted)
        return not overlap, f"{field} 与 {sorted(wanted)} 交集={overlap}"

    if operator == "isTrue":
        return actual is True, f"{field}={actual!r}"

    if operator == "isFalse":
        return actual is False, f"{field}={actual!r}"

    if operator in ("gte", "lte", "gt", "lt"):
        number = F.to_float(actual)
        threshold = F.to_float(expected)
        if number is None or threshold is None:
            return False, f"{field}={actual!r} 无法比较"
        result = {
            "gte": number >= threshold,
            "lte": number <= threshold,
            "gt": number > threshold,
            "lt": number < threshold,
        }[operator]
        symbol = {"gte": ">=", "lte": "<=", "gt": ">", "lt": "<"}[operator]
        return result, f"{field}={number} {symbol} {threshold} → {result}"

    if operator == "textContainsAny":
        haystack = F.text(actual)
        words = [str(w) for w in (expected or [])]
        hits = [w for w in words if w and w in haystack]
        return bool(hits), (
            f"{field} 命中 {hits}" if hits else f"{field} 未命中 {words}"
        )

    if operator == "isEmpty":
        if isinstance(actual, list):
            return len(actual) == 0, f"{field} 列表长度={len(actual)}"
        number = F.to_float(actual)
        # 注意：这里 actual 为 None 时不该走到（会被 requiredFields 拦成 skipped）
        return number == 0, f"{field}={actual!r}"

    raise ValueError(f"未知运算符：{operator!r}（规则字段 {field}）")


def _check_required_fields(
    profile: Dict[str, Any], rule: Dict[str, Any]
) -> Optional[List[str]]:
    """检查 requiredFields 可用性。返回缺失字段列表，全部可用返回 None。"""
    required = rule.get("requiredFields") or []
    if not required:
        return None
    availability = profile.get("fieldAvailability") or {}
    missing = [
        name for name in required
        if availability.get(name, F.UNKNOWN) != F.AVAILABLE
    ]
    return missing or None


def evaluate_rule(profile: Dict[str, Any], rule: Dict[str, Any]) -> Dict[str, Any]:
    """判定单条规则，产出三态结果。"""
    rule_id = str(rule.get("ruleId") or "")
    base = {
        "ruleId": rule_id,
        "channel": str(rule.get("channel") or ""),
        "priority": str(rule.get("priority") or "P3"),
        "name": str(rule.get("name") or ""),
    }

    if rule.get("permanentlySkipped"):
        return {
            **base,
            "state": SKIPPED,
            "permanent": True,
            "skipReason": str(rule.get("skipReason") or "字段在接口契约中不存在"),
            "missingFields": list(rule.get("requiredFields") or []),
        }

    missing = _check_required_fields(profile, rule)
    if missing:
        return {
            **base,
            "state": SKIPPED,
            "permanent": False,
            "skipReason": "字段无值：" + "、".join(missing),
            "missingFields": missing,
        }

    details: List[str] = []
    for condition in rule.get("conditions") or []:
        satisfied, explanation = _evaluate_condition(profile, condition)
        details.append(explanation)
        if not satisfied:
            return {**base, "state": UNMATCHED, "details": details}

    return {
        **base,
        "state": MATCHED,
        "details": details,
        "signals": rule.get("signals") or {},
        "policyPackDesc": str(rule.get("policyPackDesc") or ""),
        "chainBonus": bool(rule.get("chainBonus")),
        "chainStage": str(rule.get("chainStage") or ""),
        "guidance": str(rule.get("guidance") or ""),
    }


def _merge_signals(matched: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """聚合命中规则的 signals，供检索使用。"""
    merged: Dict[str, List[str]] = {
        "aTags": [], "bTags": [], "cTags": [], "dLevels": [], "regionTags": [], "keywords": [],
    }
    for item in matched:
        for key, values in (item.get("signals") or {}).items():
            if key in merged:
                for value in values or []:
                    if value not in merged[key]:
                        merged[key].append(str(value))
    return merged


def evaluate_rules(
    profile: Dict[str, Any], enable_stage2: bool = False
) -> Dict[str, Any]:
    """执行四通道规则。

    G0 命中即拦截，后续通道不执行。G4 仅在 enable_stage2 时执行。
    """
    rules = load_rules()
    by_channel: Dict[str, List[Dict[str, Any]]] = {c: [] for c in CHANNELS}
    for rule in rules:
        channel = str(rule.get("channel") or "")
        if channel in by_channel:
            by_channel[channel].append(rule)

    # ---------- G0 前置拦截 ----------
    gate_results = [evaluate_rule(profile, rule) for rule in by_channel["G0"]]
    blocked = next((r for r in gate_results if r["state"] == MATCHED), None)
    credit = profile.get("credit") or {}
    credit_checked = bool(credit.get("checked"))

    gate_result: Dict[str, Any] = {
        "passed": blocked is None,
        "creditChecked": credit_checked,
        "blockedBy": blocked["ruleId"] if blocked else None,
        "rules": gate_results,
    }
    if blocked:
        gate_result["reason"] = f"{blocked['name']}：{'；'.join(blocked.get('details') or [])}"
        gate_result["guidance"] = blocked.get("guidance") or ""
    elif not credit_checked:
        # enterpriseCredit 实测 100% 为空。缺失时既不拦截也不算通过，
        # scoring 会据此把信用记录 10 分从分母剔除。
        gate_result["reason"] = "enterpriseCredit 字段无值，未执行信用校验"
    else:
        gate_result["reason"] = "信用记录无负面"

    channels: Dict[str, Any] = {}
    if blocked:
        for channel in ("G2", "G3", "G4"):
            channels[channel] = {
                "executed": False,
                "reason": f"G0 已拦截（{blocked['ruleId']}）",
                "matched": [], "unmatched": [], "skipped": [],
            }
        return {
            "gateResult": gate_result,
            "channels": channels,
            "aggregatedSignals": _merge_signals([]),
            "matchedRuleIds": [],
            "skippedRuleIds": [],
            "chainStages": [],
        }

    # ---------- G2 / G3 / G4 ----------
    all_matched: List[Dict[str, Any]] = []
    all_skipped: List[Dict[str, Any]] = []

    for channel in ("G2", "G3", "G4"):
        if channel == "G4" and not enable_stage2:
            channels[channel] = {
                "executed": False,
                "reason": "未启用第二轮精推（enableStage2=false）",
                "matched": [], "unmatched": [], "skipped": [],
            }
            continue

        results = [evaluate_rule(profile, rule) for rule in by_channel[channel]]
        matched = sorted(
            [r for r in results if r["state"] == MATCHED],
            key=lambda r: (PRIORITY_ORDER.get(r["priority"], 9), r["ruleId"]),
        )
        unmatched = [r for r in results if r["state"] == UNMATCHED]
        skipped = [r for r in results if r["state"] == SKIPPED]

        channels[channel] = {
            "executed": True,
            "matched": matched,
            "unmatched": [
                {"ruleId": r["ruleId"], "name": r["name"], "details": r.get("details") or []}
                for r in unmatched
            ],
            "skipped": [
                {
                    "ruleId": r["ruleId"], "name": r["name"],
                    "skipReason": r["skipReason"],
                    "permanent": r.get("permanent", False),
                    "missingFields": r.get("missingFields") or [],
                }
                for r in skipped
            ],
        }
        all_matched.extend(matched)
        all_skipped.extend(skipped)

    chain_stages = [
        {"ruleId": r["ruleId"], "stage": r["chainStage"], "policyPackDesc": r["policyPackDesc"]}
        for r in all_matched
        if r.get("chainBonus") and r.get("chainStage")
    ]

    return {
        "gateResult": gate_result,
        "channels": channels,
        "aggregatedSignals": _merge_signals(all_matched),
        "matchedRuleIds": [r["ruleId"] for r in all_matched],
        "skippedRuleIds": [r["ruleId"] for r in all_skipped],
        "chainStages": chain_stages,
    }


def collect_unmatched_signals(rule_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """命中规则里带 policyPackDesc 的清单。

    调用方拿它与库内政策比对，无对应政策的进 unmatchedSignals，
    作为补政策库的输入——不静默丢弃。
    """
    items: List[Dict[str, Any]] = []
    for channel_result in (rule_result.get("channels") or {}).values():
        for item in channel_result.get("matched") or []:
            if item.get("policyPackDesc"):
                items.append({
                    "ruleId": item["ruleId"],
                    "priority": item["priority"],
                    "policyPackDesc": item["policyPackDesc"],
                    "keywords": (item.get("signals") or {}).get("keywords") or [],
                })
    return items
