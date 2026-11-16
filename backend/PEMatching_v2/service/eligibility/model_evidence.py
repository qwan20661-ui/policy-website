# -*- coding: utf-8 -*-
"""逐条件模型事实抽取。

模型负责从企业事实中选择证据路径并说明事实状态；数值换算、比较与评分仍由
服务端完成。这样既不把静态字段映射当成唯一入口，也避免模型直接产出分数。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Tuple

from service import fields as F
from service.eligibility.enterprise_adapter import _get_path
from service.llm_client import LLMError, chat_with_retry, extract_json_content
from service.paths import env_bool, env_int


# 这是上游企业数据的字段语义目录，不是“政策条件 -> 企业字段”的静态映射。
# 目录只用于校验模型选出的原始字段，防止相邻概念被模型误标为精确证据。
_PATH_METADATA: Dict[str, Dict[str, str]] = {
    "$.metrics.employeeCount": {"label": "企业职工总数", "unit": "人", "semanticCategory": "workforce.total"},
    "$.metrics.rdPersonnelCount": {"label": "研发人员数量", "unit": "人", "semanticCategory": "workforce.rd"},
    "$.metrics.revenueWan": {"label": "营业收入", "unit": "万元", "semanticCategory": "financial.revenue"},
    "$.metrics.mainBusinessIncomeWan": {"label": "主营业务收入", "unit": "万元", "semanticCategory": "financial.main_business_revenue"},
    "$.metrics.rdExpenseWan": {"label": "研发投入", "unit": "万元", "semanticCategory": "financial.rd_expense"},
    "$.metrics.rdExpenseRatioPct": {"label": "研发投入占比", "unit": "%", "semanticCategory": "financial.rd_expense_ratio"},
    "$.metrics.techContractWan": {"label": "技术交易额", "unit": "万元", "semanticCategory": "financial.tech_contract"},
    "$.metrics.totalAssetsWan": {"label": "资产总额", "unit": "万元", "semanticCategory": "financial.assets"},
    "$._eligibilityEvidence.operation.mainBusinessIncome": {"label": "主营业务收入（原始经营信息）", "unit": "元", "semanticCategory": "financial.main_business_revenue"},
    "$._eligibilityEvidence.operation.operatingIncome": {"label": "营业收入（原始经营信息）", "unit": "元", "semanticCategory": "financial.revenue"},
    "$._eligibilityEvidence.operation.rdExpense": {"label": "研发投入（原始经营信息）", "unit": "元", "semanticCategory": "financial.rd_expense"},
    "$._eligibilityEvidence.operation.rdExpenseRatio": {"label": "研发投入占比（原始经营信息）", "unit": "%", "semanticCategory": "financial.rd_expense_ratio"},
    "$.registerInfo.registerAddress": {"label": "注册地址", "unit": "", "semanticCategory": "location.register_address"},
    "$.registerInfo.registerDistrict": {"label": "注册区县", "unit": "", "semanticCategory": "location.register_district"},
    # 资质（企业原始数据，均视为精确证据）
    "$.qualifications.isTechSme": {"label": "科技型中小企业", "unit": "", "semanticCategory": "qualification.tech_sme"},
    "$.qualifications.isInnovativeSme": {"label": "创新型中小企业", "unit": "", "semanticCategory": "qualification.innovative_sme"},
    "$.qualifications.isHighTechEnterprise": {"label": "高新技术企业", "unit": "", "semanticCategory": "qualification.high_tech"},
    "$.qualifications.isSpecializedSme": {"label": "专精特新中小企业", "unit": "", "semanticCategory": "qualification.specialized_sme"},
    "$.qualifications.isSpecializedLittleGiant": {"label": "专精特新小巨人", "unit": "", "semanticCategory": "qualification.specialized_little_giant"},
    "$.qualifications.isTechLeaderEnterprise": {"label": "科技领军企业", "unit": "", "semanticCategory": "qualification.tech_leader"},
    "$.qualifications.isInnovativeLeader": {"label": "创新龙头企业", "unit": "", "semanticCategory": "qualification.innovative_leader"},
    # 科技项目/奖励/标准数量（企业原始数据）
    "$.qualificationCounts.cityTechProjectCount": {"label": "地市级科技计划项目数", "unit": "项", "semanticCategory": "qualification.city_project_count"},
    "$.qualificationCounts.provTechProjectCount": {"label": "省级科技计划项目数", "unit": "项", "semanticCategory": "qualification.prov_project_count"},
    "$.qualificationCounts.cityTechAwardCount": {"label": "地市级科技奖励数", "unit": "项", "semanticCategory": "qualification.city_award_count"},
    "$.qualificationCounts.provTechAwardCount": {"label": "省级科技奖励数", "unit": "项", "semanticCategory": "qualification.prov_award_count"},
    "$.qualificationCounts.provAboveTechAwardCount": {"label": "省级及以上科技奖励数", "unit": "项", "semanticCategory": "qualification.prov_above_award_count"},
    "$.qualificationCounts.standardFormulationCount": {"label": "参与制定标准数", "unit": "项", "semanticCategory": "qualification.standard_count"},
    # 研发条件（企业原始数据）
    "$.rndConditions.hasRndAccount": {"label": "是否有研发会计科目", "unit": "", "semanticCategory": "rnd.has_account"},
    "$.rndConditions.hasRndInstitution": {"label": "是否有研发机构", "unit": "", "semanticCategory": "rnd.has_institution"},
    "$.rndConditions.industryUniversityCoop": {"label": "产学研合作", "unit": "", "semanticCategory": "rnd.industry_university_coop"},
    # 注册信息补充
    "$.registerInfo.registerType": {"label": "注册类型", "unit": "", "semanticCategory": "location.register_type"},
    "$.registerInfo.nationalHighTechZone": {"label": "所属国家高新区", "unit": "", "semanticCategory": "location.national_high_tech_zone"},
    # 信用
    "$.credit.raw": {"label": "企业信用", "unit": "", "semanticCategory": "credit.raw"},
    "$.credit.negative": {"label": "信用负面标记", "unit": "", "semanticCategory": "credit.negative"},
}


def is_enabled(enabled: bool | None = None) -> bool:
    return env_bool("PEMATCHING_V2_ELIGIBILITY_LLM_ENABLED", False) if enabled is None else enabled


def _walk_values(value: Any, path: str) -> Iterable[Tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "_availability":
                continue
            yield from _walk_values(child, f"{path}.{key}")
        return
    if isinstance(value, list):
        # 展开数组：operationInfoList/mainProductList/_rawFactIndex.facts 等子表
        # 逐条进入候选，模型才能看到每条记录的年度、人员、产品等字段。
        for index, child in enumerate(value):
            yield from _walk_values(child, f"{path}[{index}]")
        return
    if not F.is_missing(value):
        yield path, value


def _candidate_facts(profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    """构造模型可选择的企业事实清单，且路径必须可被服务端回读。

    来源两部分：
    1. 画像扁平展开（_walk_values）：含 metrics/qualifications/registerInfo 等；
    2. _rawFactIndex.facts：原始字段的中文名 + 值 + sourcePath，
       覆盖净利润/成本/纳税/行业/资质等全部文档字段（精确证据）。
    """
    candidates: List[Dict[str, Any]] = []
    seen_paths: set = set()
    for path, value in _walk_values(profile, "$"):
        if path.startswith("$._availability"):
            continue
        # _rawFactIndex.mappedRecord 与 $.facts.* 内容重复且无精确语义，跳过，
        # 避免模型选到辅助版；精确版由下方 facts 分支提供。
        # _rawFactIndex.facts 是 [fieldName/value/sourcePath] 三件套，内部展开无意义，也跳过。
        if path.startswith("$._rawFactIndex.mappedRecord") or path.startswith("$._rawFactIndex.facts"):
            continue
        metadata = _PATH_METADATA.get(path)
        inferred = {
            "label": path.rsplit(".", 1)[-1],
            "unit": "",
            "semanticCategory": f"unclassified.{path.rsplit('.', 1)[-1]}",
        }
        candidates.append({"path": path, "value": value, **(metadata or inferred), "verifiedPath": bool(metadata)})
        seen_paths.add(path)

    # 补充 _rawFactIndex.facts 的原始字段（中文字段名，语义清晰）
    raw_facts = ((profile.get("_rawFactIndex") or {}).get("facts")) or []
    for fact in raw_facts:
        if not isinstance(fact, dict):
            continue
        field_name = F.text(fact.get("fieldName"))
        source_path = F.text(fact.get("sourcePath"))
        if not field_name or not source_path:
            continue
        # 避免与 _walk_values 已产出的路径重复（用 sourcePath 去重）
        if source_path in seen_paths:
            continue
        seen_paths.add(source_path)
        # 中文字段名作为 path，便于模型语义理解；label 同字段名
        candidates.append({
            "path": f"$.facts.{field_name}",
            "value": fact.get("value"),
            "label": field_name,
            "unit": "",
            "semanticCategory": f"raw.{field_name}",
            "verifiedPath": True,  # 企业原始数据视为精确事实
        })
    return candidates


def _condition_payload(item: Dict[str, Any]) -> Dict[str, Any]:
    rule = item.get("matchRule") or {}
    return {
        "semanticItemKey": F.text(item.get("semanticItemKey")),
        "itemName": F.text(item.get("itemName")),
        "enterpriseFields": item.get("enterpriseFields") or [],
        "policyFragment": F.text(item.get("policyFragment")),
        "operator": F.text(rule.get("operator")),
        "targetValue": rule.get("targetValue"),
        "targetUnit": F.text(rule.get("unit")),
    }


def _prompt(items: List[Dict[str, Any]], candidates: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    contract = {
        "conditionFacts": [{
            "semanticItemKey": "必须回填输入值",
            "factStatus": "exact | supporting | missing",
            "evidencePath": "仅可填写 candidates 中的 path；missing 时为空字符串",
            "semanticCategory": "必须回填候选字段的 semanticCategory；missing 时为空字符串",
            "qualitativeStatus": "仅 llm_judge 返回 pass | fail | unknown；其他条件返回 unknown",
            "evidenceText": "引用企业事实的简短说明，不得编造",
        }]
    }
    return [
        {"role": "system", "content": "你是企业资格条件的事实抽取器。逐条从候选企业事实中选择证据。不得计算分数、不得做单位换算、不得把相邻概念视为等价。例如营业收入不能证明主营业务收入。只返回合法 JSON。"},
        {"role": "user", "content": json.dumps({
            "任务": "为每条政策条件找出企业事实证据；没有精确或辅助事实时标为 missing。",
            "输出契约": contract,
            "conditions": [_condition_payload(item) for item in items],
            "candidates": candidates,
        }, ensure_ascii=False)},
    ]


def _model_fact(raw: Dict[str, Any], candidates_by_path: Dict[str, Dict[str, Any]]) -> Dict[str, Any] | None:
    status = F.text(raw.get("factStatus")).lower()
    path = F.text(raw.get("evidencePath"))
    if status not in {"exact", "supporting"} or not path:
        return None
    candidate = candidates_by_path.get(path)
    if not candidate:
        return None
    category = F.text(raw.get("semanticCategory"))
    if category != F.text(candidate.get("semanticCategory")):
        return {
            "evidenceTier": "R_rejected", "bucket": "unverified", "reason": "模型声明的语义类别与服务端证据路径不一致",
            "value": None, "sourcePaths": [], "unit": "", "modelEvidence": raw,
        }
    tier = "A1_exact" if status == "exact" and candidate.get("verifiedPath") else "B_supporting"
    return {
        "evidenceTier": tier,
        "bucket": "verified" if tier == "A1_exact" else "supporting",
        "reason": "模型逐条件选取企业事实，服务端已回读证据路径",
        "value": candidate.get("value"),
        "sourcePaths": [path],
        "unit": F.text(candidate.get("unit")),
        "semanticCategory": category,
        "modelJudgement": F.text(raw.get("qualitativeStatus")).lower(),
        "modelEvidence": {
            "factStatus": status,
            "evidenceText": F.text(raw.get("evidenceText")),
            "semanticCategory": category,
        },
    }


def resolve_condition_facts(
    profile: Dict[str, Any], items: List[Dict[str, Any]], *, enabled: bool | None = None
) -> Dict[str, Dict[str, Any]]:
    """调用一次模型，为一个政策的全部条件返回可回读事实。

    任何模型错误都返回空结果，由调用方安全回退至已有精确映射或缺失口径。
    """
    if not is_enabled(enabled) or not items:
        return {}
    candidates = _candidate_facts(profile)
    if not candidates:
        return {}
    try:
        message = chat_with_retry(
            _prompt(items, candidates),
            max_retries=env_int("PEMATCHING_V2_ELIGIBILITY_LLM_RETRY", 0),
            temperature=0.0,
        )
        parsed = extract_json_content(message)
    except LLMError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    by_path = {F.text(candidate.get("path")): candidate for candidate in candidates}
    result: Dict[str, Dict[str, Any]] = {}
    valid_keys = {F.text(item.get("semanticItemKey")) for item in items}
    for raw in parsed.get("conditionFacts") or []:
        if not isinstance(raw, dict):
            continue
        key = F.text(raw.get("semanticItemKey"))
        fact = _model_fact(raw, by_path)
        if key in valid_keys and fact is not None:
            result[key] = fact
    return result
