# -*- coding: utf-8 -*-
"""政策 LLM 打标：上下文拼装、校验、重试。

上下文按信息价值从高到低排（模型注意力有限）：
    标题摘要 → 支持方式 → 平台关键词 → policyInfo → 申报条件 → 关联政策 → 材料

校验分两级：致命错误触发重试（并把错误回传给模型，比原样重试有效），
警告只记录不重试。申报期不在本模块产出——见 application_window.py。
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from service import fields as F
from service import taxonomy as tx
from service.application_window import extract_window, merge_llm_window
from service.llm_client import LLMError, chat_with_retry, extract_json_content
from service.paths import PROMPTS_DIR

PROMPT_PATH = PROMPTS_DIR / "policy_tagging_prompt.md"

# 单条政策上下文里各段的长度上限，防止个别超长政策挤爆 prompt
_MAX_SUMMARY = 800
_MAX_CONDITION_TEXT = 1200
_MAX_CONDITIONS = 12
_MAX_SUPPORT_METHODS = 8
_MAX_RELATED = 4
_MAX_MATERIALS = 5


class TaggingError(RuntimeError):
    """打标失败。异常信息含 fusionPolicyId 便于定位。"""


@lru_cache(maxsize=1)
def load_prompt() -> str:
    if not PROMPT_PATH.exists():
        raise FileNotFoundError(f"打标 prompt 不存在：{PROMPT_PATH}")
    return PROMPT_PATH.read_text(encoding="utf-8")


def _clean(value: Any, limit: int = 0) -> str:
    text = F.text(value).replace("\xa0", " ")
    text = " ".join(text.split())
    return text[:limit] if limit and len(text) > limit else text


def _support_methods_text(policy: Dict[str, Any]) -> str:
    rows: List[str] = []
    for method in (policy.get("supportMethods") or [])[:_MAX_SUPPORT_METHODS]:
        if not isinstance(method, dict):
            continue
        parts = []
        for label, key in (
            ("supportType", "supportType"),
            ("supportTypePath", "supportTypePath"),
            ("contentDesc", "contentDesc"),
            ("amount", "amount"),
            ("maxAmount", "maxAmount"),
        ):
            value = _clean(method.get(key), 300)
            if value:
                parts.append(f"{label}={value}")
        if parts:
            rows.append("  - " + "；".join(parts))
    return "\n".join(rows)


def _keywords_text(policy: Dict[str, Any]) -> str:
    items: List[str] = []
    for keyword in policy.get("keywords") or []:
        if not isinstance(keyword, dict):
            continue
        text = _clean(keyword.get("text"), 60)
        path = _clean(keyword.get("pathText"), 120)
        if text:
            items.append(f"{text}（分类路径：{path}）" if path else text)
    return "、".join(items)


def _conditions_text(policy: Dict[str, Any]) -> str:
    rows: List[str] = []
    for index, condition in enumerate(
        (policy.get("structuredConditions") or [])[:_MAX_CONDITIONS], start=1
    ):
        if not isinstance(condition, dict):
            continue
        parts = []
        for label, key in (
            ("适用对象", "applicableTarget"),
            ("适用对象路径", "applicableTargetPath"),
            ("申报要求", "applicationRequirement"),
            ("申报说明", "applicationInstruction"),
        ):
            value = _clean(condition.get(key), _MAX_CONDITION_TEXT)
            if value:
                parts.append(f"{label}：{value}")
        if parts:
            rows.append(f"  条件{index} " + "｜".join(parts))
    return "\n".join(rows)


def _related_text(policy: Dict[str, Any]) -> str:
    related = policy.get("relatedPolicies") or {}
    blocks: List[str] = []
    for label, key in (
        ("父政策", "parent"),
        ("上级政策", "directParents"),
        ("祖先政策", "ancestors"),
        ("子政策", "children"),
    ):
        items = related.get(key) or []
        for item in items[:_MAX_RELATED]:
            if not isinstance(item, dict):
                continue
            title = _clean(item.get("title"), 80)
            summary = _clean(item.get("summary"), 200)
            if not title and not summary:
                continue
            line = f"  - [{label}] {title}"
            if summary:
                line += f"：{summary}"
            conditions = [
                _clean(c.get("applicationRequirement") or c.get("applicableTarget"), 150)
                for c in (item.get("conditions") or [])
                if isinstance(c, dict)
            ]
            conditions = [c for c in conditions if c]
            if conditions:
                line += "｜条件摘要：" + "；".join(conditions[:2])
            blocks.append(line)
    return "\n".join(blocks)


def _materials_text(policy: Dict[str, Any]) -> str:
    rows: List[str] = []
    for material in (policy.get("materials") or [])[:_MAX_MATERIALS]:
        if not isinstance(material, dict):
            continue
        text = _clean(material.get("materials"), 300)
        method = _clean(material.get("method"), 200)
        if text or method:
            rows.append(f"  - {text}" + (f"（办理方式：{method}）" if method else ""))
    return "\n".join(rows)


def build_tagging_context(policy: Dict[str, Any]) -> str:
    """按信息价值从高到低拼装上下文。"""
    policy_info = policy.get("policyInfo") or {}
    sections: List[str] = []

    sections.append(f"政策ID：{_clean(policy.get('fusionPolicyId'))}")
    sections.append(f"标题：{_clean(policy.get('title')) or _clean(policy_info.get('policyTitle'))}")

    summary = _clean(policy_info.get("summary"), _MAX_SUMMARY)
    if summary:
        sections.append(f"摘要：{summary}")

    support = _support_methods_text(policy)
    if support:
        sections.append("支持方式：\n" + support)
    else:
        sections.append("支持方式：（无 supportMethods 数据）")

    keywords = _keywords_text(policy)
    if keywords:
        sections.append(f"平台已有关键词：{keywords}")

    info_lines = []
    for label, key in (
        ("行业领域 industryField", "industryField"),
        ("行业领域路径 industryFieldPath", "industryFieldPath"),
        ("适用范围 applicableScopePath", "applicableScopePath"),
        ("场景用途 sceneUsage", "sceneUsage"),
        ("政策属性 policyAttribute", "policyAttribute"),
        ("发文单位", "issuingDepartment"),
    ):
        value = _clean(policy_info.get(key), 300)
        if value:
            info_lines.append(f"  {label}：{value}")
    if info_lines:
        sections.append("政策信息：\n" + "\n".join(info_lines))

    conditions = _conditions_text(policy)
    if conditions:
        sections.append("申报条件：\n" + conditions)
    else:
        sections.append("申报条件：（无 structuredConditions 数据）")

    related = _related_text(policy)
    if related:
        sections.append(
            "关联政策（子政策条件常不完整，可据此补齐口径）：\n" + related
        )

    materials = _materials_text(policy)
    if materials:
        sections.append("申报材料：\n" + materials)

    # 申报期由系统确定性提取，此处只作为判断 F 兑现周期的参考
    window = extract_window(policy)
    if window.get("isRolling"):
        sections.append("系统已提取的申报期：长期受理")
    elif window.get("startDate") or window.get("endDate"):
        sections.append(
            f"系统已提取的申报期：{window.get('startDate') or '?'} 至 {window.get('endDate') or '?'}"
        )
    else:
        sections.append("系统已提取的申报期：未能提取")

    return "\n\n".join(sections)


def validate_tagging_result(
    result: Any, policy_id: str
) -> Tuple[List[str], List[str]]:
    """校验打标产出。返回 (致命错误, 警告)。

    致命错误触发重试；警告只记录。
    """
    fatal: List[str] = []
    warnings: List[str] = []

    if not isinstance(result, dict):
        return ["产出不是 JSON 对象"], []

    returned_id = F.text(result.get("fusionPolicyId"))
    if returned_id and returned_id != F.text(policy_id):
        fatal.append(f"fusionPolicyId 不一致：期望 {policy_id}，实际 {returned_id}")

    tags = result.get("tags")
    if not isinstance(tags, dict):
        return ["缺少 tags 对象"], warnings

    # 复用一期的枚举校验——防政策库词表污染匹配轴的关口在这里
    fatal.extend(tx.validate_tag_payload(tags))

    # C 子类必须属于已声明的大类
    c_entry = tags.get("C") or {}
    major = set(str(v) for v in (c_entry.get("values") or []))
    for sub in c_entry.get("subValues") or []:
        parent = tx.sub_tag_parent(str(sub))
        if parent and parent not in major:
            fatal.append(f"C.subValues 里 {sub} 的大类 {parent} 未出现在 values 中")

    generic = tx.load_taxonomy()["dimensions"]["C"]["genericTag"]
    if generic in major and len(major) > 1:
        warnings.append(f"C 同时含通用标签 {generic} 与具体领域 {sorted(major - {generic})}，语义可能矛盾")
    if generic in major and (c_entry.get("subValues") or []):
        fatal.append(f"C 为通用 {generic} 却给了子类 {c_entry.get('subValues')}")

    confidences = [
        (tags.get(dim) or {}).get("confidence")
        for dim in ("A", "B", "C", "D")
        if isinstance(tags.get(dim), dict)
    ]
    if confidences and all(c == "low" for c in confidences):
        warnings.append("A/B/C/D 四个核心维度全为 low 置信度，可能是政策信息过少")

    applicability = result.get("applicability")
    if applicability is None:
        fatal.append("缺少 applicability——需先判断政策是否面向企业可申报")
    elif not isinstance(applicability, dict):
        fatal.append("applicability 必须是对象")
    else:
        if not isinstance(applicability.get("applicable"), bool):
            fatal.append("applicability.applicable 必须是布尔值")
        if not F.text(applicability.get("reason")):
            fatal.append("applicability.reason 不能为空")
        if applicability.get("applicable") is False:
            warnings.append(
                f"判定为不可申报政策（{F.text(applicability.get('audience')) or '受众未标注'}）"
                f"：{F.text(applicability.get('reason'))[:60]}"
            )

    growth_chain = result.get("growthChain")
    if growth_chain is not None and not isinstance(growth_chain, dict):
        fatal.append("growthChain 必须是对象或省略")

    keyword_index = result.get("keywordIndex")
    if keyword_index is not None:
        if not isinstance(keyword_index, dict):
            fatal.append("keywordIndex 必须是对象或省略")
        else:
            for key in ("title", "aKeywords", "cKeywords"):
                value = keyword_index.get(key)
                if value is not None and not isinstance(value, list):
                    fatal.append(f"keywordIndex.{key} 必须是数组")

    if isinstance(keyword_index, dict) and not (keyword_index.get("title") or []):
        warnings.append("keywordIndex.title 为空，关键词命中主标题的加分将失效")

    thresholds = result.get("scaleThresholds")
    if thresholds is not None:
        if not isinstance(thresholds, dict):
            fatal.append("scaleThresholds 必须是对象或省略")
        else:
            for key in ("employeeMin", "employeeMax", "revenueMinWan", "revenueMaxWan"):
                value = thresholds.get(key)
                if value is not None and F.to_float(value) is None:
                    fatal.append(f"scaleThresholds.{key} 必须是数字或 null")
            confidence = F.text(thresholds.get("confidence"))
            if confidence and confidence not in tx.load_taxonomy()["confidenceLevels"]:
                fatal.append("scaleThresholds.confidence 取值非法")
            if any(thresholds.get(key) is not None for key in (
                "employeeMin", "employeeMax", "revenueMinWan", "revenueMaxWan"
            )) and not F.text(thresholds.get("basis")):
                fatal.append("scaleThresholds 有门槛值时 basis 不能为空")

    return fatal, warnings


def _build_messages(policy: Dict[str, Any]) -> List[Dict[str, str]]:
    return [
        {"role": "system", "content": load_prompt()},
        {"role": "user", "content": build_tagging_context(policy)},
    ]


def tag_policy(
    policy: Dict[str, Any],
    max_retries: int = 2,
    event_log: Any = None,
) -> Dict[str, Any]:
    """单条政策打标。

    返回符合 policy_tags.json 契约的 dict。失败抛 TaggingError。
    申报期由 application_window 确定性提取后合并，模型只兜底无线索的少数情况。
    """
    policy_id = F.text(policy.get("fusionPolicyId"))
    if not policy_id:
        raise TaggingError("政策缺少 fusionPolicyId")

    messages = _build_messages(policy)
    last_raw: Optional[str] = None
    last_errors: List[str] = []

    for attempt in range(max_retries + 1):
        try:
            message = chat_with_retry(
                messages,
                response_format={"type": "json_object"},
                temperature=0.0,
                event_log=event_log,
            )
        except LLMError as exc:
            raise TaggingError(f"政策 {policy_id} 调用失败：{exc}") from exc

        last_raw = F.text(message.get("content"))
        parsed = extract_json_content(message)
        if parsed is None:
            fatal, warnings = ["产出不是合法 JSON"], []
        else:
            fatal, warnings = validate_tagging_result(parsed, policy_id)

        if not fatal:
            deterministic = extract_window(policy)
            window = merge_llm_window(deterministic, (parsed or {}).get("applicationWindow"))
            return {
                "fusionPolicyId": policy_id,
                "title": F.text(policy.get("title")),
                "tags": parsed["tags"],
                "applicability": parsed.get("applicability") or {},
                "applicationWindow": window,
                "growthChain": parsed.get("growthChain") or {},
                "keywordIndex": parsed.get("keywordIndex") or {},
                "scaleThresholds": parsed.get("scaleThresholds") or {},
                "notes": F.text(parsed.get("notes")),
                "warnings": warnings,
                "_attempts": attempt + 1,
            }

        last_errors = fatal
        if attempt >= max_retries:
            break

        # 把校验错误回传给模型，比原样重试有效
        messages = messages + [
            {"role": "assistant", "content": last_raw or ""},
            {
                "role": "user",
                "content": "上一次输出有以下问题，请修正后重新输出完整 JSON：\n"
                + "\n".join(f"- {e}" for e in fatal[:15]),
            },
        ]

    raise TaggingError(
        f"政策 {policy_id} 打标失败（重试 {max_retries} 次）："
        + "；".join(last_errors[:5])
        + (f"｜最后一次原始响应：{last_raw[:300]}" if last_raw else "")
    )
