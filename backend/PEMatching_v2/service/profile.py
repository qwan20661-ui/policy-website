# -*- coding: utf-8 -*-
"""企业工商画像与标签计算（确定性，无 LLM）。

输入是企业接口响应对象（BaseBusinessInfo + 子表结构），输出企业画像：
B/C/属地标签、规模与创新指标、资质状态、信用状态，以及每个字段的可用性标记。

设计前提：任何字段都可能没有值。缺失一律记 unknown，不填默认值、不用均值补造。
`0` 与 `null` 严格区分——见 service/fields.py 的说明。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from service import fields as F
from service import taxonomy as tx
from service.raw_enterprise_mapping import build_fact_index


def _parse_date(value: Any) -> Optional[date]:
    """解析接口日期。支持 yyyy-MM-dd HH:mm:ss 与 yyyy-MM-dd 等常见写法。"""
    raw = F.text(value)
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%Y%m%d"):
        try:
            return datetime.strptime(raw[: len(fmt) + 6], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw.replace("Z", "")).date()
    except ValueError:
        return None


def _years_between(start: date, end: date) -> float:
    return round((end - start).days / 365.25, 2)


def _latest_operation(record: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    """取最新年度的经营信息。

    同一企业可能有多年记录，按 year 降序取第一条。返回 (记录, 年份)。
    子表缺失或为空时返回 ({}, "")。
    """
    rows = record.get("operationInfoList")
    if not isinstance(rows, list):
        return {}, ""
    valid = [r for r in rows if isinstance(r, dict)]
    if not valid:
        return {}, ""

    def sort_key(row: Dict[str, Any]) -> str:
        return F.text(row.get("year"))

    latest = sorted(valid, key=sort_key, reverse=True)[0]
    return latest, F.text(latest.get("year"))


def _ratio_to_percent(value: Any) -> Optional[float]:
    """将上游比例字段统一为百分数，供资格条件进行精确比较。"""
    ratio = F.to_float(value)
    if ratio is None:
        return None
    raw = F.text(value)
    if "%" in raw:
        return ratio
    # 工商接口的 rdExpenseRatio 为 0~1 小数；兼容已经按百分数传入的补充数据。
    return round(ratio * 100, 4) if abs(ratio) <= 1 else ratio


def _merge_qualifications(record: Dict[str, Any]) -> Dict[str, Optional[bool]]:
    """合并资质子表。

    多条记录时取「任一为是则为是」；全部为 null 则保持 None（unknown）。
    实测 isTechSme / isHighTechEnterprise 覆盖率 100%，其余 5 项为 0%。
    """
    keys = (
        "isTechSme",
        "isInnovativeSme",
        "isHighTechEnterprise",
        "isSpecializedSme",
        "isSpecializedLittleGiant",
        "isTechLeaderEnterprise",
        "isInnovativeLeader",
    )
    merged: Dict[str, Optional[bool]] = {key: None for key in keys}
    rows = record.get("honorQualificationList")
    if not isinstance(rows, list):
        return merged

    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in keys:
            parsed = F.yes_no(row.get(key))
            if parsed is None:
                continue
            merged[key] = True if parsed else (merged[key] if merged[key] else False)
    return merged


def _qualification_counts(record: Dict[str, Any]) -> Dict[str, Optional[int]]:
    """资质子表里的计数类字段（科技项目、科技奖励、标准制定）。"""
    keys = (
        "cityTechProjectCount",
        "provTechProjectCount",
        "cityTechAwardCount",
        "provTechAwardCount",
        "provAboveTechAwardCount",
        "standardFormulationCount",
    )
    result: Dict[str, Optional[int]] = {key: None for key in keys}
    rows = record.get("honorQualificationList")
    if not isinstance(rows, list):
        return result

    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in keys:
            parsed = F.to_int(row.get(key))
            if parsed is None:
                continue
            result[key] = parsed if result[key] is None else max(result[key], parsed)
    return result


def _collect_text_sources(record: Dict[str, Any]) -> str:
    """拼接「业务描述」类文本源，用于强子类判定。

    实测覆盖率：productServiceIntro 0%、highTechField 0%、brief 4.4%、
    mainProductList 2.9%、mainTechnologyList 5.4%，任一有值仅 7.0%。

    注意这里**不含** industryLevel2 与 industryField。行业名本身常含关键词
    （「软件和信息技术服务业」含「软件」），命中它只说明行业口径沾边，
    不等于企业真做该方向。行业名走 _collect_industry_text 判弱子类。
    """
    parts: List[str] = []
    for key in ("brief", "productServiceIntro", "highTechField", "rndInstitutionName"):
        value = F.text(record.get(key))
        if value:
            parts.append(value)

    for list_key, sub_keys in (
        ("mainProductList", ("productName", "productDescription")),
        ("mainTechnologyList", ("patentName", "brief")),
    ):
        rows = record.get(list_key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            for sub_key in sub_keys:
                value = F.text(row.get(sub_key))
                if value:
                    parts.append(value)

    return " ".join(parts)


def _collect_industry_text(record: Dict[str, Any]) -> str:
    """拼接行业名文本源，用于弱子类判定。

    与 _collect_text_sources 分开是因为两者证据强度不同：
    行业名沾边只能算弱信号，计分时记 25 分而非 30 分。
    """
    parts = [
        F.text(record.get("industryLevel2")),
        F.text(record.get("industryLevel1")),
        F.text(record.get("industryField")),
    ]
    return " ".join(p for p in parts if p)


def _resolve_b_tags(
    company_age: Optional[float],
    revenue_wan: Optional[float],
    qualifications: Dict[str, Optional[bool]],
    register_type: str,
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """B 发展阶段：三信号「或」逻辑，可同时归入多个阶段。

    docx 原文：采用「成立年限 / 营收规模 / 已有资质」三信号任一满足即归入的
    「或」逻辑，避免边界企业遗漏。三信号全部 unknown 时返回空列表。
    """
    signals_config = tx.load_taxonomy()["dimensions"]["B"]["signals"]
    matched: List[str] = []
    evidence: List[Dict[str, Any]] = []

    has_any_qualification = any(v is True for v in qualifications.values())
    qualification_known = any(v is not None for v in qualifications.values())

    for stage in ("B1", "B2", "B3"):
        spec = signals_config.get(stage) or {}
        hits: List[str] = []

        if company_age is not None:
            years_min = spec.get("yearsMin")
            years_max = spec.get("yearsMax")
            in_range = True
            if years_min is not None and company_age < years_min:
                in_range = False
            if years_max is not None and company_age > years_max:
                in_range = False
            if in_range:
                hits.append(f"成立 {company_age} 年")

        if revenue_wan is not None:
            revenue_min = spec.get("revenueMinWan")
            revenue_max = spec.get("revenueMaxWan")
            in_range = True
            if revenue_min is not None and revenue_wan < revenue_min:
                in_range = False
            if revenue_max is not None and revenue_wan > revenue_max:
                in_range = False
            if in_range:
                hits.append(f"营收 {revenue_wan} 万元")

        stage_qualifications = spec.get("qualifications") or []
        if stage_qualifications:
            owned = [q for q in stage_qualifications if qualifications.get(q) is True]
            if owned:
                hits.append("已获资质：" + "、".join(owned))
        elif spec.get("noQualification") and qualification_known and not has_any_qualification:
            hits.append("无任何科技资质")

        for keyword in spec.get("registerTypeKeywords") or []:
            if keyword and keyword in register_type:
                hits.append(f"企业类型含「{keyword}」")

        if hits:
            matched.append(stage)
            # signalCount 供计分用：命中信号越多，该阶段匹配越强。
            # 实测 B2 命中率 100%，仅靠「是否命中」无法区分，必须看命中强度。
            evidence.append({"stage": stage, "signals": hits, "signalCount": len(hits)})

    return matched, evidence


def _resolve_c_tags(
    level1: str,
    level2: str,
    business_text: str,
    industry_text: str,
) -> Dict[str, Any]:
    """C 行业领域：映射表出大类，关键词库出子类（分强弱两档）。

    强子类：由 brief / 产品 / 技术子表等业务描述文本命中，证据强，计 30 分。
    弱子类：仅由行业名（industryLevel2 等）命中，证据弱，计 25 分。
      行业名含关键词不等于企业真做该方向——实测「软件和信息技术服务业」
      让 461 家企业全部命中 C1.3，多数只是行业口径沾边。

    行业字段全空且无子类命中时大类为空——由调用方记 unknown，
    与「映射到 C9 通用」语义不同（前者是不知道，后者是已知为通用）。
    """
    basis: List[str] = []
    major_tags, confidence = tx.resolve_c_tags(level1, level2)
    if major_tags:
        basis.append(f"行业映射：{level2 or level1} → {'、'.join(major_tags)}")

    strong_hits = tx.match_c_subtags(business_text, with_hits=True)
    for sub_tag, words in strong_hits.items():
        basis.append(f"强子类 {sub_tag}：业务描述命中「{'、'.join(words)}」")

    industry_hits = tx.match_c_subtags(industry_text, with_hits=True)
    weak_hits = {k: v for k, v in industry_hits.items() if k not in strong_hits}
    for sub_tag, words in weak_hits.items():
        basis.append(f"弱子类 {sub_tag}：行业名含「{'、'.join(words)}」，未见业务描述佐证")

    strong_sub = sorted(strong_hits.keys())
    weak_sub = sorted(weak_hits.keys())
    all_sub = sorted(set(strong_sub) | set(weak_sub))

    # 子类命中但大类未命中时，用子类反推大类（如命中 C1.2 则补 C1）
    for sub_tag in all_sub:
        parent = tx.sub_tag_parent(sub_tag)
        if parent and parent not in major_tags:
            major_tags = major_tags + [parent]
            basis.append(f"由子类 {sub_tag} 反推大类 {parent}")

    has_industry_field = bool(F.text(level1) or F.text(level2))
    if not major_tags and has_industry_field:
        # 行业字段有值但映射表判定为「口径过宽」，明确落通用
        major_tags = [tx.load_taxonomy()["dimensions"]["C"]["genericTag"]]
        basis.append(f"行业「{level2 or level1}」口径过宽，落 C9 通用")
        confidence = confidence if confidence != "none" else "low"

    return {
        "major": sorted(set(major_tags)),
        "sub": all_sub,
        "strongSub": strong_sub,
        "weakSub": weak_sub,
        "confidence": confidence,
        "basis": basis,
    }


def build_profile(record: Dict[str, Any], today: Optional[date] = None) -> Dict[str, Any]:
    """构建企业画像。

    today 可注入，便于测试成立年限（不用 date.today() 硬编码，否则测试随时间失效）。
    """
    if not isinstance(record, dict):
        raise ValueError("企业记录必须是对象")
    reference_date = today or date.today()
    availability = F.Availability()
    # 原始事实不以画像指标替代：它按中文字段映射保存在请求内，供 Agent
    # 按政策申报条件精确读取。正常 API 响应仍保持精简，不直接返回该明细。
    raw_fact_index = build_fact_index(record)

    # ---------- 标识 ----------
    enterprise_name = F.text(availability.mark("enterpriseName", record.get("enterpriseName")))
    if not enterprise_name:
        raise ValueError("企业记录缺少 enterpriseName")
    credit_code = F.text(availability.mark("creditCode", record.get("creditCode")))

    # ---------- 注册信息 ----------
    register_date_raw = availability.mark("registerDate", record.get("registerDate"))
    register_date = _parse_date(register_date_raw)
    company_age = _years_between(register_date, reference_date) if register_date else None
    availability.mark_resolved("companyAgeYears", company_age)

    register_address = F.text(availability.mark("registerAddress", record.get("registerAddress")))
    register_district = F.text(availability.mark("registerDistrict", record.get("registerDistrict")))
    national_zone = F.text(availability.mark("nationalHighTechZone", record.get("nationalHighTechZone")))
    register_type = F.text(availability.mark("registerType", record.get("registerType")))

    # registerCapital 实测 3251 份仅 1 份有值（0.03%），不参与规模判定
    register_capital_wan = F.to_float(availability.mark("registerCapital", record.get("registerCapital")))

    # ---------- 行业 ----------
    level1 = F.text(availability.mark("industryLevel1", record.get("industryLevel1")))
    level2 = F.text(availability.mark("industryLevel2", record.get("industryLevel2")))
    availability.mark("industryField", record.get("industryField"))
    availability.mark("highTechField", record.get("highTechField"))
    availability.mark("brief", record.get("brief"))
    availability.mark("productServiceIntro", record.get("productServiceIntro"))
    availability.mark("enterpriseScale", record.get("enterpriseScale"))

    business_text = _collect_text_sources(record)
    industry_text = _collect_industry_text(record)
    availability.mark_state(
        "cSubTagTextSource", F.AVAILABLE if business_text.strip() else F.UNKNOWN
    )

    c_result = _resolve_c_tags(level1, level2, business_text, industry_text)
    c_major = c_result["major"]
    c_sub = c_result["sub"]
    c_confidence = c_result["confidence"]
    c_basis = c_result["basis"]
    availability.mark_state("cTags", F.AVAILABLE if c_major else F.UNKNOWN)

    # ---------- 知识产权 ----------
    ip_class1 = F.to_int(availability.mark("ipClass1Count", record.get("ipClass1Count")))
    ip_class2 = F.to_int(availability.mark("ipClass2Count", record.get("ipClass2Count")))
    ip_count = F.to_int(availability.mark("ipCount", record.get("ipCount")))
    invention_count = F.to_int(availability.mark("inventionPatentCount", record.get("inventionPatentCount")))

    # 三级回退。注意 0 是有效取值：ipClass1Count=0 表示确认没有 I 类知产，
    # 与 null（不知道）语义不同，前者参与计分记 0 分，后者剔除分母。
    ip_class_sum = None
    if ip_class1 is not None or ip_class2 is not None:
        ip_class_sum = (ip_class1 or 0) + (ip_class2 or 0)

    ip_source, ip_total = F.first_available(
        ("ipClass1+2", ip_class_sum),
        ("ipCount", ip_count),
        ("inventionPatentCount", invention_count),
    )
    availability.mark_resolved("ipTotal", ip_total)

    # ---------- 经营信息 ----------
    operation, metrics_year = _latest_operation(record)
    availability.mark_state("operationInfoList", F.AVAILABLE if operation else F.UNKNOWN)

    employee_count = F.to_int(availability.mark("employeeCount", operation.get("employeeCount")))
    revenue_wan = F.yuan_to_wan(availability.mark("operatingIncome", operation.get("operatingIncome")))
    availability.mark_resolved("revenueWan", revenue_wan)
    # 主营业务收入与营业收入属于不同统计口径，资格求值必须保留精确来源字段。
    main_business_income_wan = F.yuan_to_wan(
        availability.mark("mainBusinessIncome", operation.get("mainBusinessIncome"))
    )
    availability.mark_resolved("mainBusinessIncomeWan", main_business_income_wan)
    rd_expense_wan = F.yuan_to_wan(availability.mark("rdExpense", operation.get("rdExpense")))
    availability.mark_resolved("rdExpenseWan", rd_expense_wan)
    rd_expense_ratio_pct = _ratio_to_percent(
        availability.mark("rdExpenseRatio", operation.get("rdExpenseRatio"))
    )
    availability.mark_resolved("rdExpenseRatioPct", rd_expense_ratio_pct)
    tech_contract_wan = F.yuan_to_wan(
        availability.mark("techContractAmount", operation.get("techContractAmount"))
    )
    availability.mark_resolved("techContractWan", tech_contract_wan)

    rd_personnel = F.to_int(availability.mark("rdPersonnelCount", operation.get("rdPersonnelCount")))
    net_assets_wan = F.yuan_to_wan(availability.mark("netAssets", operation.get("netAssets")))
    total_assets_wan = F.yuan_to_wan(availability.mark("totalAssets", operation.get("totalAssets")))

    doctor_count = F.to_int(availability.mark("doctorCount", operation.get("doctorCount")))
    master_count = F.to_int(availability.mark("masterCount", operation.get("masterCount")))
    new_recruit = F.to_int(availability.mark("newRecruitCount", operation.get("newRecruitCount")))
    college_graduate = F.to_int(
        availability.mark("collegeGraduateCount", operation.get("collegeGraduateCount"))
    )

    # ---------- 研发条件 ----------
    has_rnd_account = F.yes_no(availability.mark("hasRndAccount", record.get("hasRndAccount")))
    has_rnd_institution = F.yes_no(
        availability.mark("hasRndInstitution", record.get("hasRndInstitution"))
    )
    industry_university_coop = F.yes_no(
        availability.mark("industryUniversityCoop", record.get("industryUniversityCoop"))
    )

    # ---------- 资质 ----------
    qualifications = _merge_qualifications(record)
    for key, value in qualifications.items():
        availability.mark_resolved(key, value)
    qualification_counts = _qualification_counts(record)
    for key, value in qualification_counts.items():
        availability.mark_resolved(key, value)

    # ---------- 信用 ----------
    # enterpriseCredit 实测 100% 为空。缺失时既不拦截也不算通过，
    # 记 checked=False，由 scoring 把信用记录 10 分从分母剔除。
    credit_raw = F.text(availability.mark("enterpriseCredit", operation.get("enterpriseCredit")))
    credit_negative, credit_words = tx.has_credit_negative(credit_raw)
    credit = {
        "checked": bool(credit_raw),
        "negative": credit_negative if credit_raw else None,
        "matchedWords": credit_words,
        "raw": credit_raw,
    }

    # 工商接口没有覆盖项目、材料、机构批次等全部申报事实时，可由上游权威
    # 业务系统通过 eligibilityFacts 补充。这里只透传可核验的结构化事实；
    # 未提供的字段仍由资格引擎判为 unknown，绝不补默认值。
    raw_eligibility_facts = record.get("eligibilityFacts")
    eligibility_facts = raw_eligibility_facts if isinstance(raw_eligibility_facts, dict) else {}
    for key, value in eligibility_facts.items():
        availability.mark(f"eligibilityFacts.{key}", value)

    # ---------- 标签 ----------
    b_tags, b_evidence = _resolve_b_tags(company_age, revenue_wan, qualifications, register_type)
    availability.mark_state("bTags", F.AVAILABLE if b_tags else F.UNKNOWN)

    regions = tx.match_regions(register_district, national_zone, register_address)
    availability.mark_state("regions", F.AVAILABLE if regions else F.UNKNOWN)

    return {
        "enterpriseName": enterprise_name,
        "creditCode": credit_code,
        "enterpriseId": F.text(record.get("id") or record.get("enterpriseId")),
        "tags": {
            "B": b_tags,
            "C": c_major,
            "cSub": c_sub,
            "cSubStrong": c_result["strongSub"],
            "cSubWeak": c_result["weakSub"],
            "cConfidence": c_confidence,
            "regions": regions,
        },
        "tagBasis": {"B": b_evidence, "C": c_basis},
        "metrics": {
            "companyAgeYears": company_age,
            "registerDate": F.text(register_date_raw),
            "employeeCount": employee_count,
            "rdPersonnelCount": rd_personnel,
            "revenueWan": revenue_wan,
            "mainBusinessIncomeWan": main_business_income_wan,
            "registerCapitalWan": register_capital_wan,
            "netAssetsWan": net_assets_wan,
            "totalAssetsWan": total_assets_wan,
            "ipTotal": ip_total,
            "ipSource": ip_source,
            "ipClass1Count": ip_class1,
            "ipClass2Count": ip_class2,
            "rdExpenseWan": rd_expense_wan,
            "rdExpenseRatioPct": rd_expense_ratio_pct,
            "techContractWan": tech_contract_wan,
            "doctorCount": doctor_count,
            "masterCount": master_count,
            "newRecruitCount": new_recruit,
            "collegeGraduateCount": college_graduate,
            "metricsYear": metrics_year,
        },
        "registerInfo": {
            "registerType": register_type,
            "registerDistrict": register_district,
            "nationalHighTechZone": national_zone,
            "registerAddress": register_address,
        },
        # 新条件匹配链路直接使用原始行业口径作明确排除和模型证据，不能再借用 C 标签。
        "industryInfo": {
            "level1": level1,
            "level2": level2,
            "industryField": F.text(record.get("industryField")),
        },
        "rndConditions": {
            "hasRndAccount": has_rnd_account,
            "hasRndInstitution": has_rnd_institution,
            "industryUniversityCoop": industry_university_coop,
        },
        "qualifications": qualifications,
        "qualificationCounts": qualification_counts,
        "eligibilityFacts": eligibility_facts,
        "credit": credit,
        "fieldAvailability": availability.as_dict(),
        "dataCompleteness": availability.completeness(),
        "unknownFields": availability.unknown_fields(),
        "_rawFactIndex": raw_fact_index,
        "_availability": availability,
    }
