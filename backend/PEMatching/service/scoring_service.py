"""企业-政策本地评分服务。"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Dict, List, Tuple

from service.rule_engine_service import (
    DEFAULT_MISSING_SCORE,
    PRESENT_DATA_BASE_SCORE,
    RECOMMENDATION_SCORE_THRESHOLD,
    _confidence_level,
    resolve_rule_profile,
    score_policy_against_rule_profile,
)
from service.evidence_tiering import classify_runtime_evidence


HARD_COMPANY_KEYWORDS = ["高新技术企业", "科技型中小企业", "专精特新", "小巨人", "新型研发机构", "研发机构"]
NEGATIVE_COMPLIANCE_KEYWORDS = ["重大安全", "质量事故", "严重环境违法", "经营异常", "失信", "违法"]
INDIRECT_HINT_KEYWORDS = ["行业", "领域", "技术", "产品", "服务", "研发管理", "组织管理", "成果转化", "资质", "荣誉"]


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return "；".join(_safe_text(item) for item in value if _safe_text(item))
    return str(value).strip()


def _safe_float(value: Any) -> float | None:
    text = _safe_text(value)
    if not text:
        return None
    text = text.replace(",", "").replace("，", "")
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _char_ngrams(text: str, n: int = 2) -> List[str]:
    cleaned = re.sub(r"\s+", "", _safe_text(text))
    if not cleaned:
        return []
    if len(cleaned) <= n:
        return [cleaned]
    return [cleaned[i : i + n] for i in range(len(cleaned) - n + 1)]


def _cosine_similarity(text_a: str, text_b: str) -> float:
    tokens_a = Counter(_char_ngrams(text_a))
    tokens_b = Counter(_char_ngrams(text_b))
    if not tokens_a or not tokens_b:
        return 0.0
    common = set(tokens_a) & set(tokens_b)
    numerator = sum(tokens_a[token] * tokens_b[token] for token in common)
    denominator = math.sqrt(sum(v * v for v in tokens_a.values())) * math.sqrt(sum(v * v for v in tokens_b.values()))
    if denominator <= 0:
        return 0.0
    return max(0.0, min(numerator / denominator, 1.0))


def _enterprise_text_corpus(enterprise: Dict[str, Any]) -> Dict[str, str]:
    snapshot = enterprise.get("_enterprise_standard_snapshot") or {}
    flat_fields = enterprise.get("_enterprise_standard_index") or {}

    direct_parts = [
        flat_fields.get("企业名称", {}).get("标准值", ""),
        flat_fields.get("统一社会信用代码", {}).get("标准值", ""),
        flat_fields.get("注册地", {}).get("标准值", ""),
        flat_fields.get("主体类型", {}).get("标准值", ""),
        flat_fields.get("是否在厦注册", {}).get("标准值", ""),
        flat_fields.get("高新技术企业认定", {}).get("标准值", ""),
        flat_fields.get("科技型中小企业认定", {}).get("标准值", ""),
        flat_fields.get("专精特新认定", {}).get("标准值", ""),
        flat_fields.get("新型研发机构认定", {}).get("标准值", ""),
        flat_fields.get("研发机构认定", {}).get("标准值", ""),
        flat_fields.get("合规经营状态", {}).get("标准值", ""),
    ]
    indirect_parts = [
        flat_fields.get("一级行业", {}).get("标准值", ""),
        flat_fields.get("二级行业", {}).get("标准值", ""),
        flat_fields.get("三级行业", {}).get("标准值", ""),
        flat_fields.get("科技服务领域", {}).get("标准值", ""),
        flat_fields.get("行业领域层级", {}).get("标准值", ""),
        flat_fields.get("业务标签", {}).get("标准值", ""),
        flat_fields.get("主要产品或服务", {}).get("标准值", ""),
        flat_fields.get("核心技术领域", {}).get("标准值", ""),
        flat_fields.get("荣誉资质", {}).get("标准值", ""),
        enterprise.get("企业简介", ""),
    ]

    return {
        "direct": "；".join(_safe_text(item) for item in direct_parts if _safe_text(item)),
        "indirect": "；".join(_safe_text(item) for item in indirect_parts if _safe_text(item)),
    }


def _combine_condition_text(condition: Dict[str, Any]) -> str:
    return "；".join(
        [
            _safe_text(condition.get("条件名称")),
            _safe_text(condition.get("政策原文")),
            _safe_text(condition.get("政策要求")),
        ]
    )


def _extract_number(text: str) -> float | None:
    matches = re.findall(r"(\d+(?:\.\d+)?)", _safe_text(text).replace(",", ""))
    if not matches:
        return None
    try:
        return float(matches[0])
    except ValueError:
        return None


def _extract_operator(text: str) -> str:
    if any(keyword in text for keyword in ["不低于", "不少于", "大于等于", "达到", "≥", ">=", "以上", "至少", "满足"]):
        return ">="
    if any(keyword in text for keyword in ["不高于", "不超过", "小于等于", "≤", "<=", "以下", "至多", "不得超过"]):
        return "<="
    if any(keyword in text for keyword in ["等于", "为", "是"]):
        return "="
    return "contains"


def _extract_field_rule(text: str) -> Dict[str, Any]:
    lower_text = _safe_text(text)
    numeric_value = _extract_number(lower_text)
    operator = _extract_operator(lower_text)

    if any(keyword in lower_text for keyword in ["重大安全", "质量事故", "严重环境违法", "经营异常", "失信", "违法"]):
        return {"field": "合规经营状态", "kind": "hard_boolean", "weight_group": "A", "operator": "not_contains", "target": lower_text}

    if any(keyword in lower_text for keyword in ["厦门", "注册地", "在厦", "注册地址"]):
        return {"field": "是否在厦注册", "kind": "hard_boolean", "weight_group": "A", "operator": "contains", "target": "厦门"}

    if any(keyword in lower_text for keyword in ["独立法人", "法人主体", "申报主体", "企业主体"]):
        return {"field": "主体类型", "kind": "hard_boolean", "weight_group": "A", "operator": "contains", "target": "企业"}

    if any(keyword in lower_text for keyword in ["高新技术企业", "科技型中小企业", "专精特新", "小巨人", "新型研发机构", "研发机构"]):
        if any(keyword in lower_text for keyword in ["必须", "须为", "应为", "应当", "仅限", "仅支持", "必须是"]):
            field_map = {
                "高新技术企业": "高新技术企业认定",
                "科技型中小企业": "科技型中小企业认定",
                "专精特新": "专精特新认定",
                "小巨人": "专精特新认定",
                "新型研发机构": "新型研发机构认定",
                "研发机构": "研发机构认定",
            }
            for keyword, field_name in field_map.items():
                if keyword in lower_text:
                    return {"field": field_name, "kind": "hard_boolean", "weight_group": "A", "operator": "contains", "target": "是"}
        field_map = {
            "高新技术企业": "高新技术企业认定",
            "科技型中小企业": "科技型中小企业认定",
            "专精特新": "专精特新认定",
            "小巨人": "专精特新认定",
            "新型研发机构": "新型研发机构认定",
            "研发机构": "研发机构认定",
        }
        for keyword, field_name in field_map.items():
            if keyword in lower_text:
                return {"field": field_name, "kind": "soft_boolean", "weight_group": "A", "operator": "contains", "target": "是"}

    if any(keyword in lower_text for keyword in ["科技人员占", "科技人员比例", "科技人员占比"]):
        return {"field": "科技人员占企业职工总数比例", "kind": "soft_numeric", "weight_group": "A", "operator": operator, "target": numeric_value, "unit": "%"}

    if any(keyword in lower_text for keyword in ["科技人员总数", "研发人员数量", "研发人员数", "科技人员数", "职工总数"]):
        field_map = {
            "科技人员总数": "科技人员总数",
            "研发人员数量": "科技人员总数",
            "研发人员数": "科技人员总数",
            "科技人员数": "科技人员总数",
            "职工总数": "职工总数",
        }
        for keyword, field_name in field_map.items():
            if keyword in lower_text:
                return {"field": field_name, "kind": "soft_numeric", "weight_group": "A", "operator": operator, "target": numeric_value, "unit": "人"}

    if any(keyword in lower_text for keyword in ["研发费用占销售收入", "研发投入强度", "研发费用占比"]):
        return {"field": "研发费用占销售收入总额比例", "kind": "soft_numeric", "weight_group": "A", "operator": operator, "target": numeric_value, "unit": "%"}

    if any(keyword in lower_text for keyword in ["研发费用", "研发投入", "研发资金"]):
        return {"field": "研发费用总额(万元)", "kind": "soft_numeric", "weight_group": "A", "operator": operator, "target": numeric_value, "unit": "万元"}

    if any(keyword in lower_text for keyword in ["知识产权总数", "发明专利", "实用新型", "软件著作权", "外观专利", "PCT专利"]):
        if "I类" in lower_text:
            return {"field": "I类知识产权数量", "kind": "soft_numeric", "weight_group": "A", "operator": operator, "target": numeric_value, "unit": "件"}
        if "II类" in lower_text:
            return {"field": "II类知识产权数量", "kind": "soft_numeric", "weight_group": "A", "operator": operator, "target": numeric_value, "unit": "件"}
        return {"field": "知识产权总数", "kind": "soft_numeric", "weight_group": "A", "operator": operator, "target": numeric_value, "unit": "件"}

    if any(keyword in lower_text for keyword in ["资产总额", "净资产", "利润总额", "主营业务收入", "销售收入", "营业收入", "纳税总额", "企业规模"]):
        field_map = [
            ("资产总额", "资产总额(万元)", "万元"),
            ("净资产", "净资产(万元)", "万元"),
            ("利润总额", "利润总额(万元)", "万元"),
            ("主营业务收入", "主营业务收入(万元)", "万元"),
            ("销售收入", "销售收入(万元)", "万元"),
            ("营业收入", "销售收入(万元)", "万元"),
            ("纳税总额", "纳税总额(万元)", "万元"),
        ]
        for keyword, field_name, unit in field_map:
            if keyword in lower_text:
                return {"field": field_name, "kind": "soft_numeric", "weight_group": "A", "operator": operator, "target": numeric_value, "unit": unit}

    if any(keyword in lower_text for keyword in ["行业", "领域", "产品", "服务", "技术", "研发管理", "组织管理", "成果转化", "资质", "荣誉"]):
        return {"field": "文本相似度", "kind": "qualitative", "weight_group": "C", "operator": "similarity", "target": None, "unit": ""}

    return {"field": "文本相似度", "kind": "indirect", "weight_group": "B", "operator": "similarity", "target": None, "unit": ""}


def _score_boolean(enterprise: Dict[str, Any], field_name: str, target: str, hardness: str) -> Tuple[bool, float, str]:
    index = enterprise.get("_enterprise_standard_index") or {}
    current = _safe_text(index.get(field_name, {}).get("标准值", enterprise.get(field_name, "")))
    if not current:
        return True, DEFAULT_MISSING_SCORE, ""
    current_truthy = current in {"是", "符合", "已认定", "存在", "有", "企业"}

    if field_name == "合规经营状态":
        current_truthy = "异常" not in current and "违法" not in current and "失信" not in current
        description = current or "未见重大违法异常记录"
    elif field_name == "是否在厦注册":
        description = current or _safe_text(enterprise.get("注册地", ""))
        current_truthy = "厦门" in description or "厦门" in _safe_text(enterprise.get("所在市", ""))
    else:
        description = current

    passed = current_truthy
    score = 1.0 if passed else 0.0 if hardness == "hard" else PRESENT_DATA_BASE_SCORE
    return passed, score, description


def _score_numeric(
    enterprise: Dict[str, Any],
    field_name: str,
    target: float | None,
    operator: str,
    unit: str = "",
) -> Tuple[bool, float, Any, str]:
    index = enterprise.get("_enterprise_standard_index") or {}
    current = index.get(field_name, {}).get("标准值", enterprise.get(field_name))
    current_value = _safe_float(current)
    if current_value is None:
        return True, DEFAULT_MISSING_SCORE, current, "pass"
    if unit == "%":
        current_value *= 100
    if target is None or target < 0:
        return False, 0.0, current_value, "not_evaluable"

    if operator == "<=":
        passed = current_value <= target
        score = 1.0 if passed else PRESENT_DATA_BASE_SCORE
    elif operator == "=":
        passed = abs(current_value - target) < 1e-9
        score = 1.0 if passed else PRESENT_DATA_BASE_SCORE
    else:
        passed = current_value >= target
        if target == 0:
            score = 1.0 if passed else PRESENT_DATA_BASE_SCORE
        else:
            score = min(max(PRESENT_DATA_BASE_SCORE + current_value / target, PRESENT_DATA_BASE_SCORE), 1.0)
    return passed, score, current_value, "pass"


def _evaluate_text_similarity(enterprise: Dict[str, Any], policy_text: str, direct: bool = False) -> float:
    corpus = _enterprise_text_corpus(enterprise)
    base_text = corpus["direct"] if direct else corpus["indirect"]
    similarity = _cosine_similarity(base_text, policy_text)
    return max(0.0, min(similarity, 1.0))


def _format_condition_detail(
    condition: Dict[str, Any],
    rule: Dict[str, Any],
    passed: bool,
    score: float,
    enterprise_value: Any,
    message: str,
    field_status: str = "",
    eval_status: str = "",
    pass_source: str = "",
) -> Dict[str, Any]:
    if score >= 0.85 and passed:
        match_degree = "完全匹配"
    elif score >= 0.45 or passed:
        match_degree = "部分匹配"
    else:
        match_degree = "不匹配"

    return {
        "条件名称": _safe_text(condition.get("条件名称")),
        "政策原文": _safe_text(condition.get("政策原文")),
        "政策要求": _safe_text(condition.get("政策要求")),
        "来源字段": _safe_text(condition.get("来源字段")),
        "评分组别": rule.get("weight_group", "B"),
        "规则类型": rule.get("kind", "indirect"),
        "企业字段": rule.get("field", ""),
        "企业值": enterprise_value,
        "目标值": rule.get("target"),
        "单位": rule.get("unit", ""),
        "企业字段状态": field_status,
        "匹配度": match_degree,
        "得分": round(score, 4),
        "说明": message,
        "evalStatus": eval_status or ("pass" if passed else "fail"),
        "passSource": pass_source,
    }


def _score_to_percentage(value: Any) -> float:
    numeric = _safe_float(value)
    if numeric is None:
        return 0.0
    return round(numeric * 100, 2)


def score_policy_against_enterprise(
    enterprise: Dict[str, Any],
    policy: Dict[str, Any],
    semantic_assessment: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    rule_profile = resolve_rule_profile(policy)
    if rule_profile:
        return score_policy_against_rule_profile(enterprise, policy, rule_profile, semantic_assessment=semantic_assessment)

    conditions = policy.get("structured_conditions", []) or []
    condition_details: List[Dict[str, Any]] = []
    hard_fail_items: List[str] = []
    hard_pass = True
    score_a_values: List[float] = []
    score_b_values: List[float] = []

    for condition in conditions:
        policy_text = _combine_condition_text(condition)
        rule = _extract_field_rule(policy_text)

        if rule["kind"] in {"hard_boolean", "soft_boolean"}:
            passed, score, enterprise_value = _score_boolean(enterprise, rule["field"], _safe_text(rule.get("target")), rule["kind"].replace("_boolean", ""))
            default_assumed = not _safe_text(enterprise_value)
            field_status = (enterprise.get("_enterprise_standard_index") or {}).get(rule["field"], {}).get("数据状态", "")
            if rule["kind"] == "hard_boolean" and not passed:
                hard_pass = False
                hard_fail_items.append(f"{_safe_text(condition.get('条件名称'))}：{policy_text}")
            detail = _format_condition_detail(
                condition,
                rule,
                passed,
                score,
                enterprise_value,
                "企业接口未提供该字段，按缺失兜底口径记 10 分" if default_assumed else "布尔条件命中" if passed else "布尔条件未命中，按有数据基础分记 30 分",
                field_status,
                pass_source="default_missing_assumed" if default_assumed else "",
            )
        elif rule["kind"] == "soft_numeric":
            passed, score, enterprise_value, eval_status = _score_numeric(
                enterprise,
                rule["field"],
                rule.get("target"),
                rule.get("operator", ">="),
                rule.get("unit", ""),
            )
            field_status = (enterprise.get("_enterprise_standard_index") or {}).get(rule["field"], {}).get("数据状态", "")
            default_assumed = enterprise_value is None or not _safe_text(enterprise_value)
            if rule["field"] in {"是否在厦注册", "主体类型", "高新技术企业认定", "科技型中小企业认定", "专精特新认定", "新型研发机构认定", "研发机构认定"}:
                score = 1.0 if passed else 0.0
            detail = _format_condition_detail(
                condition,
                rule,
                passed,
                score,
                enterprise_value,
                (
                    "企业接口未提供该数值字段，企业真实值保持缺失；按缺失兜底口径记 10 分"
                    if default_assumed
                    else "数值条件满足" if passed else "数值条件未满足"
                ),
                field_status,
                eval_status,
                "default_missing_assumed" if default_assumed else "",
            )
        elif rule["kind"] == "qualitative":
            corpus = _enterprise_text_corpus(enterprise)["indirect"]
            default_assumed = not corpus
            similarity = _evaluate_text_similarity(enterprise, policy_text, direct=False) if not default_assumed else 0.0
            score = min(1.0, PRESENT_DATA_BASE_SCORE + similarity) if not default_assumed else DEFAULT_MISSING_SCORE
            detail = _format_condition_detail(
                condition,
                rule,
                score >= 0.45,
                score,
                corpus,
                "企业接口未提供可核验语料，按缺失兜底口径记 10 分" if default_assumed else "定性条件按30分基础分加文本匹配度计算",
                (enterprise.get("_enterprise_standard_index") or {}).get("核心技术领域", {}).get("数据状态", ""),
                pass_source="default_missing_assumed" if default_assumed else "",
            )
        else:
            corpus = _enterprise_text_corpus(enterprise)["indirect"]
            default_assumed = not corpus
            similarity = _evaluate_text_similarity(enterprise, policy_text, direct=False) if not default_assumed else 0.0
            score = min(1.0, PRESENT_DATA_BASE_SCORE + similarity) if not default_assumed else DEFAULT_MISSING_SCORE
            detail = _format_condition_detail(
                condition,
                rule,
                score >= 0.4,
                score,
                corpus,
                "企业接口未提供可核验语料，按缺失兜底口径记 10 分" if default_assumed else "间接条件按30分基础分加文本匹配度计算",
                "",
                pass_source="default_missing_assumed" if default_assumed else "",
            )

        # 兜底路径也使用运行时证据分层，确保有无规则表时展示和聚合口径一致。
        tier = classify_runtime_evidence({
            "status": detail.get("evalStatus"),
            "pass_source": detail.get("passSource"),
            "enterprise_value": detail.get("企业值"),
            "resolved_values": [detail.get("企业值")] if detail.get("企业值") is not None else [],
        })
        detail.update(tier.as_dict())
        detail["计分归类"] = tier.bucket
        detail["evidenceTier"] = tier.tier
        detail["bucketReason"] = tier.reason
        detail["规则表原始ruleKind"] = ""
        if rule["weight_group"] == "A":
            score_a_values.append(detail["得分"])
        elif rule["weight_group"] == "B":
            score_b_values.append(detail["得分"])

        condition_details.append(detail)

    score_a_value = round(sum(score_a_values) / len(score_a_values), 4) if score_a_values else None
    score_b_value = round(sum(score_b_values) / len(score_b_values), 4) if score_b_values else None
    # 无规则表兜底路径与规则表主路径保持相同口径：每条可评分条件同权。
    # A/B 仍按结构化条件的运行时类别输出审计分，但不再改变最终分。
    all_scores = [float(item.get("得分", 0.0) or 0.0) for item in condition_details]
    raw_score = round(sum(all_scores) / len(all_scores), 4) if all_scores else 0.0
    score_a = score_a_value or 0.0
    score_b = score_b_value or 0.0
    coeff_c = 1.0
    final_score = round(raw_score if hard_pass else 0.0, 4)
    pending_condition_count = sum(1 for item in condition_details if item.get("evalStatus") == "not_evaluable")
    failed_condition_count = sum(1 for item in condition_details if item.get("evalStatus") == "fail")
    all_rules_confirmed = bool(condition_details) and pending_condition_count == 0 and failed_condition_count == 0
    default_assumed_count = sum(1 for item in condition_details if item.get("passSource") == "default_missing_assumed")
    scored_item_count = len(condition_details)
    evidence_item_count = scored_item_count - default_assumed_count
    evidence_ratio = round(evidence_item_count / scored_item_count, 4) if scored_item_count else 0.0
    confidence_level = _confidence_level(evidence_ratio)
    recommendation_eligible = bool(hard_pass and all_rules_confirmed and final_score >= RECOMMENDATION_SCORE_THRESHOLD)

    positive_items = [item for item in condition_details if item["得分"] >= 0.6]
    negative_items = [item for item in condition_details if item["得分"] < 0.6]
    risks = []
    if not hard_pass:
        risks.append("存在硬性条件未通过，政策总分已置零。")
    if any((enterprise.get("_enterprise_standard_index") or {}).get(item.get("企业字段", ""), {}).get("数据状态") == "缺失补齐" for item in condition_details):
        risks.append("部分企业字段使用默认均值补齐，建议人工复核。")

    for item in condition_details:
        item["得分"] = _score_to_percentage(item.get("得分", 0.0))

    display_score_a = _score_to_percentage(score_a)
    display_score_b = _score_to_percentage(score_b)
    display_raw_score = _score_to_percentage(raw_score)
    display_final_score = _score_to_percentage(final_score)

    return {
        "政策标题": _safe_text(policy.get("title", "")),
        "分数口径": "百分制（0-100分，系数项保留原值）",
        "hard_pass": hard_pass,
        "hard_fail_items": hard_fail_items,
        "score_A": display_score_a,
        "score_B": display_score_b,
        "coeff_C": coeff_c,
        "score_A_direct": display_score_a,
        "score_B_indirect": display_score_b,
        "评分聚合算法": "runtime_evidence_tiering_balanced_mean_v1",
        "raw_score": display_raw_score,
        "final_score": display_final_score,
        "匹配率": display_final_score,
        "星级推荐": _calc_star_rating(final_score),
        "是否推荐": 1 if recommendation_eligible else 0,
        "全部规则已确认": all_rules_confirmed,
        "待确认条件项数": pending_condition_count,
        "未满足条件项数": failed_condition_count,
        "默认假定条件项数": default_assumed_count,
        "参与计分条件项数": scored_item_count,
        "已核验条件项数": evidence_item_count,
        "证据覆盖率": evidence_ratio,
        "证据置信度": confidence_level,
        "推荐状态说明": (
            (
                f"按规则匹配分推荐，其中 {default_assumed_count} 项因企业接口缺失按兜底口径计 10 分"
                f"（证据覆盖率 {_score_to_percentage(evidence_ratio)}%，置信度 {confidence_level}）。"
                if default_assumed_count
                else "全部适用规则已确认，且匹配分达到推荐阈值。"
            )
            if recommendation_eligible
            else f"存在明确失败或分数未达 {_score_to_percentage(RECOMMENDATION_SCORE_THRESHOLD)} 分推荐阈值，当前不推荐。"
        ),
        "条件匹配详情": condition_details,
        "优势分析": [f"{item['条件名称']}：{item['说明']}" for item in positive_items[:5]],
        "劣势分析": [f"{item['条件名称']}：{item['说明']}" for item in negative_items[:5]] + hard_fail_items[:3],
        "风险提示": risks,
        "总体建议": _build_overall_suggestion(hard_pass, final_score, hard_fail_items),
    }


def _calc_star_rating(match_rate: float) -> str:
    if match_rate >= 0.9:
        return "五星推荐"
    if match_rate >= 0.8:
        return "四星建议"
    if match_rate >= 0.7:
        return "三星参考"
    if match_rate >= 0.6:
        return "二星谨慎"
    if match_rate >= 0.5:
        return "一星观望"
    return "不推荐"


def _build_overall_suggestion(hard_pass: bool, final_score: float, hard_fail_items: List[str]) -> str:
    if not hard_pass:
        return "硬性条件未通过，不建议直接申报。"
    if final_score >= 0.8:
        return "整体匹配度较高，建议优先申报。"
    if final_score >= 0.6:
        return "具备一定匹配基础，建议补齐材料后申报。"
    if final_score >= 0.4:
        return "存在一定匹配可能，建议结合政策正文进一步核验。"
    return "当前匹配度偏低，建议暂缓申报。"
