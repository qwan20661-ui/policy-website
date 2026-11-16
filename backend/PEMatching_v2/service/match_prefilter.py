# -*- coding: utf-8 -*-
"""T4 新条件匹配链路的确定性预过滤。

本模块只处理已有事实能够确定的“不适用”。文本歧义、企业字段缺失和需要
政策原文语义理解的场景一律保留给单政策模型，避免把召回优化变成误排除。
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from service import fields as F
from service.qualification_growth import load_qualification_growth_config


_DATE_RE = re.compile(r"(?P<year>20\d{2})(?:[./-](?P<month>\d{1,2})(?:[./-](?P<day>\d{1,2}))?)?")
_NON_ENTERPRISE_TARGETS = ("个人", "自然人", "高校", "高等院校", "事业单位", "科研院所", "社会组织")
_ENTERPRISE_TARGETS = ("企业", "公司", "单位", "机构")
_GENERIC_SCOPE = ("通用", "全国", "全市", "本市", "全省", "全区", "不限")
_GENERIC_INDUSTRY = ("", "通用行业", "通用", "不限", "全行业")
_INDUSTRY_MARKERS = {
    "软件": "software",
    "信息技术": "software",
    "制造": "manufacturing",
    "工业": "manufacturing",
    "农业": "agriculture",
    "建筑": "construction",
    "金融": "finance",
    "教育": "education",
    "医疗": "medical",
    "医药": "medical",
    "文化": "culture",
    "旅游": "tourism",
}


def _text(value: Any) -> str:
    return F.text(value).strip()


def _normalised(value: Any) -> str:
    return re.sub(r"[\s，,、。；;：:（）()【】\[\]\\/-]", "", _text(value)).lower()


def _deep_get(payload: Any, path: str) -> Tuple[bool, Any]:
    current = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _reason(code: str, message: str, evidence: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"code": code, "message": message, "evidence": evidence}


def _evidence(path: str, value: Any, text: str) -> Dict[str, Any]:
    return {"path": path, "value": value, "text": text}


def _date_from_value(value: Any) -> Optional[date]:
    raw = _text(value)
    match = _DATE_RE.search(raw)
    if not match:
        return None
    try:
        return date(
            int(match.group("year")),
            int(match.group("month") or 1),
            int(match.group("day") or 1),
        )
    except ValueError:
        return None


def policy_year(package: Dict[str, Any]) -> Tuple[Optional[int], str, Any]:
    """按申报时间、公告时间、已审核文档发布时间顺序确定政策年份。"""
    for index, condition in enumerate(package.get("conditions") or []):
        if not isinstance(condition, dict):
            continue
        for field in ("applicationStartTime", "applicationEndTime", "announcementDate"):
            value = condition.get(field)
            parsed = _date_from_value(value)
            if parsed:
                return parsed.year, f"$.conditions[{index}].{field}", value
    dates: List[Tuple[date, int, Any]] = []
    for index, document in enumerate(package.get("documents") or []):
        if not isinstance(document, dict):
            continue
        value = document.get("publishDate")
        parsed = _date_from_value(value)
        if parsed:
            dates.append((parsed, index, value))
    if dates:
        parsed, index, value = max(dates)
        return parsed.year, f"$.documents[{index}].publishDate", value
    return None, "", None


def _policy_title(package: Dict[str, Any]) -> str:
    return _text(package.get("title") or (package.get("policyInfo") or {}).get("policyTitle"))


def _policy_text(package: Dict[str, Any]) -> str:
    policy_info = package.get("policyInfo") or {}
    parts = [_policy_title(package)]
    for field in ("summary", "sceneUsage", "industryField", "applicableScope"):
        parts.append(_text(policy_info.get(field)))
    for condition in package.get("conditions") or []:
        if isinstance(condition, dict):
            for field in ("applicableTarget", "applicableTargetPath", "applicationRequirement"):
                parts.append(_text(condition.get(field)))
    for support in package.get("supportMethods") or []:
        if isinstance(support, dict):
            parts.append(_text(support.get("supportType")))
            parts.append(_text(support.get("contentDesc")))
    for keyword in package.get("keywords") or []:
        if isinstance(keyword, dict):
            parts.append(_text(keyword.get("text")))
        else:
            parts.append(_text(keyword))
    return "\n".join(part for part in parts if part)


def _is_research_subsidy(package: Dict[str, Any]) -> bool:
    text = _policy_text(package)
    return any(term in text for term in ("研发费用补助", "研发经费补助", "研发补助"))


def _subject_exclusion(package: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    targets = []
    for index, condition in enumerate(package.get("conditions") or []):
        if not isinstance(condition, dict):
            continue
        value = " ".join((
            _text(condition.get("applicableTarget")),
            _text(condition.get("applicableTargetPath")),
        )).strip()
        if value:
            targets.append((index, value))
    if not targets:
        return None
    combined = " ".join(value for _, value in targets)
    if any(term in combined for term in _NON_ENTERPRISE_TARGETS) and not any(
        term in combined for term in _ENTERPRISE_TARGETS
    ):
        index, value = targets[0]
        return _reason(
            "SUBJECT_NOT_ENTERPRISE",
            "政策申报对象明确为非企业主体，当前企业不适用。",
            [_evidence(
                f"$.conditions[{index}].applicableTarget",
                value,
                f"政策申报对象为“{value}”。",
            )],
        )
    return None


def _scope_exclusion(profile: Dict[str, Any], package: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    scope = _text((package.get("policyInfo") or {}).get("applicableScope"))
    company_location = " ".join(filter(None, (
        _text((profile.get("registerInfo") or {}).get("registerDistrict")),
        _text((profile.get("registerInfo") or {}).get("registerAddress")),
    )))
    if not scope or any(term in scope for term in _GENERIC_SCOPE) or not company_location:
        return None

    # 仅在政策范围和企业地址都出现明确行政区名时判定。单纯的“园区”等模糊词不拦截。
    areas = re.findall(r"[\u4e00-\u9fff]{2,12}(?:市|区|县|镇|街道|园区)", scope)
    if not areas:
        return None
    compact_location = _normalised(company_location)
    compact_areas = [_normalised(area) for area in areas]
    if all(area and area not in compact_location for area in compact_areas):
        return _reason(
            "LOCATION_MISMATCH",
            "企业注册地址明确不在政策限定属地内。",
            [
                _evidence("$.policyInfo.applicableScope", scope, f"政策限定属地为“{scope}”。"),
                _evidence("$.registerInfo", company_location, f"企业注册地址为“{company_location}”。"),
            ],
        )
    return None


def _industry_exclusion(profile: Dict[str, Any], package: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    policy_industry = _text((package.get("policyInfo") or {}).get("industryField"))
    enterprise_industry = " ".join(filter(None, (
        _text((profile.get("industryInfo") or {}).get("level1")),
        _text((profile.get("industryInfo") or {}).get("level2")),
        _text((profile.get("industryInfo") or {}).get("industryField")),
    )))
    if policy_industry in _GENERIC_INDUSTRY or not enterprise_industry:
        return None
    policy_markers = {value for key, value in _INDUSTRY_MARKERS.items() if key in policy_industry}
    enterprise_markers = {value for key, value in _INDUSTRY_MARKERS.items() if key in enterprise_industry}
    if policy_markers and enterprise_markers and not (policy_markers & enterprise_markers):
        return _reason(
            "INDUSTRY_MISMATCH",
            "企业所属行业与政策明确限定行业不一致。",
            [
                _evidence("$.policyInfo.industryField", policy_industry, f"政策行业为“{policy_industry}”。"),
                _evidence("$.industryLevel", enterprise_industry, f"企业行业为“{enterprise_industry}”。"),
            ],
        )
    return None


def _current_qualification_states(profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    config = load_qualification_growth_config()
    states = config["states"]
    matched: List[Dict[str, Any]] = []
    for state in states:
        fields = state.get("qualificationFields") or []
        if fields and any(_deep_get(profile, field)[1] is True for field in fields):
            matched.append(state)
    if matched:
        return matched
    return [state for state in states if state.get("fallbackWhenNoQualificationIsTrue") is True]


def _matched_policy_targets(package: Dict[str, Any]) -> List[Dict[str, Any]]:
    text = _policy_text(package)
    matches = []
    for target in load_qualification_growth_config()["policyTargets"]:
        if any(term in text for term in target.get("matchTerms") or []) and not any(
            term in text for term in target.get("excludeTerms") or []
        ):
            matches.append(target)
    return matches


def _qualification_exclusion(profile: Dict[str, Any], package: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    targets = _matched_policy_targets(package)
    if not targets:
        return None
    states = _current_qualification_states(profile)
    allowed_ids = {
        target_id
        for state in states
        for target_id in state.get("nextTargetIds") or []
    }
    if any(_text(target.get("targetId")) in allowed_ids for target in targets):
        return None
    target_labels = "、".join(_text(target.get("label")) for target in targets)
    state_labels = "、".join(_text(state.get("label")) for state in states)
    return _reason(
        "QUALIFICATION_NOT_DIRECT_SUCCESSOR",
        "资质成长链只保留企业当前状态的直接后继政策。",
        [
            _evidence("$.qualifications", profile.get("qualifications") or {}, f"企业当前资质状态：{state_labels}。"),
            _evidence("$.policy", target_labels, f"政策目标资质：{target_labels}。"),
        ],
    )


def _ip_agency_exclusion(profile: Dict[str, Any], package: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    target_text = " ".join(
        _text(condition.get(field))
        for condition in package.get("conditions") or []
        if isinstance(condition, dict)
        for field in ("applicableTarget", "applicableTargetPath")
    )
    if "知识产权代理" not in target_text and "专利代理" not in target_text:
        return None
    facts = profile.get("eligibilityFacts") or {}
    value = facts.get("isIntellectualPropertyAgency", facts.get("isIpAgency"))
    if value is False:
        return _reason(
            "IP_AGENCY_SUBJECT_MISMATCH",
            "政策明确面向知识产权代理机构，企业已明确不是该类机构。",
            [_evidence("$.eligibilityFacts", value, "企业代理机构资质标记为否。")],
        )
    return None


def _ip_subsidy_exclusion(profile: Dict[str, Any], package: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not _is_research_subsidy(package):
        return None
    metrics = profile.get("metrics") or {}
    class1, class2 = metrics.get("ipClass1Count"), metrics.get("ipClass2Count")
    total = metrics.get("ipTotal")
    # 优先使用Ⅰ/Ⅱ类口径；两者缺失时允许画像已通过专利总数或发明专利回退得到的
    # 明确正数继续参与，不能把“有回退事实”误判为无专利。
    if class1 is None and class2 is None and not (isinstance(total, (int, float)) and total > 0):
        return _reason(
            "IP_DATA_MISSING_FOR_RD_SUBSIDY",
            "研发补助类政策要求知识产权口径，Ⅰ类、Ⅱ类知识产权字段均为空。",
            [_evidence("$.metrics.ipClass1Count", class1, "Ⅰ类知识产权为空。"),
             _evidence("$.metrics.ipClass2Count", class2, "Ⅱ类知识产权为空。")],
        )
    if total == 0:
        return _reason(
            "IP_TOTAL_ZERO_FOR_RD_SUBSIDY",
            "企业已确认知识产权总数为 0，不推荐研发补助类政策。",
            [_evidence("$.metrics.ipTotal", total, "知识产权总数为 0。")],
        )
    return None


def _iter_rule_items(package: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    table = package.get("ruleTableJson") or {}
    if not isinstance(table, dict):
        return
    for group_type in ("hardRuleGroups", "softRuleGroups", "specialPassRuleGroups"):
        for group in table.get(group_type) or []:
            if not isinstance(group, dict):
                continue
            for item in group.get("conditionFieldItems") or []:
                if isinstance(item, dict):
                    yield item


def _upper_bound_fact(profile: Dict[str, Any], item: Dict[str, Any]) -> Tuple[str, Any, str]:
    names = " ".join([
        _text(item.get("itemName")),
        *(_text(value) for value in item.get("enterpriseFields") or []),
        _text(item.get("policyFragment")),
    ])
    metrics = profile.get("metrics") or {}
    candidates: Sequence[Tuple[Sequence[str], str, str]] = (
        (("成立", "注册年限", "经营年限"), "companyAgeYears", "年"),
        (("营业收入", "营收", "销售收入"), "revenueWan", "万元"),
        (("员工", "从业人员", "职工"), "employeeCount", "人"),
        (("总资产",), "totalAssetsWan", "万元"),
        (("净资产",), "netAssetsWan", "万元"),
    )
    for terms, metric, unit in candidates:
        if any(term in names for term in terms) and metrics.get(metric) is not None:
            return f"$.metrics.{metric}", metrics[metric], unit
    return "", None, ""


def _irreversible_upper_exclusion(profile: Dict[str, Any], package: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for item in _iter_rule_items(package):
        rule = item.get("matchRule") or {}
        operator = _text(rule.get("operator")).lower()
        if operator not in {"lt", "lte"}:
            continue
        target = F.to_float(rule.get("targetValue"))
        path, actual, actual_unit = _upper_bound_fact(profile, item)
        if target is None or actual is None:
            continue
        target_unit = _text(rule.get("unit"))
        # 预过滤只比较相同或明确可比较的常用单位；其他情况留给评分验证器。
        if target_unit and target_unit != actual_unit:
            continue
        exceeds = actual > target if operator == "lte" else actual >= target
        if exceeds:
            return _reason(
                "IRREVERSIBLE_UPPER_BOUND",
                "企业已明确超过政策不可逆上限条件。",
                [
                    _evidence(path, actual, f"企业实际值为 {actual}{actual_unit}。"),
                    _evidence("$.ruleTableJson", rule.get("targetValue"), f"政策上限为 {target}{target_unit}。"),
                ],
            )
    return None


def global_exclusion(profile: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    credit = profile.get("credit") or {}
    if credit.get("checked") and credit.get("negative") is True:
        return _reason(
            "NEGATIVE_CREDIT",
            "企业信用存在明确负面记录，停止全部政策匹配。",
            [_evidence("$.credit.raw", credit.get("raw"), "企业信用字段命中负面状态。")],
        )
    return None


def prefilter_policy(
    profile: Dict[str, Any], package: Dict[str, Any], today: Optional[date] = None,
) -> Optional[Dict[str, Any]]:
    """返回单条政策确定性排除原因；返回 ``None`` 表示必须进入后续语义判断。"""
    reference_date = today or date.today()
    year, path, source_value = policy_year(package)
    if year is not None and year <= reference_date.year - 2:
        return _reason(
            "POLICY_TOO_OLD",
            "政策年份为前年或更早，不进入推荐候选。",
            [_evidence(path, source_value, f"政策年份为 {year}。")],
        )
    for check in (
        _subject_exclusion,
        lambda item: _scope_exclusion(profile, item),
        lambda item: _industry_exclusion(profile, item),
        lambda item: _qualification_exclusion(profile, item),
        lambda item: _ip_agency_exclusion(profile, item),
        lambda item: _ip_subsidy_exclusion(profile, item),
        lambda item: _irreversible_upper_exclusion(profile, item),
    ):
        reason = check(package)
        if reason:
            return reason
    return None


def prefilter_policies(
    profile: Dict[str, Any], packages: Iterable[Dict[str, Any]], today: Optional[date] = None,
) -> Dict[str, Any]:
    """对整个政策集合执行稳定的、无模型依赖的预过滤。"""
    blocked = global_exclusion(profile)
    records = list(packages)
    if blocked:
        return {
            "globalExclusion": blocked,
            "candidates": [],
            "excluded": [{"fusionPolicyId": _text(item.get("fusionPolicyId")), "reason": blocked} for item in records],
        }
    candidates: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []
    for package in records:
        reason = prefilter_policy(profile, package, today=today)
        if reason:
            excluded.append({"fusionPolicyId": _text(package.get("fusionPolicyId")), "reason": reason})
        else:
            candidates.append(package)
    return {"globalExclusion": None, "candidates": candidates, "excluded": excluded}
