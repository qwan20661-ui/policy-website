"""企业与政策关联度的独立模型判断服务。"""

from __future__ import annotations

import json
import os
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

ASSOCIATION_PROMPT_FILE = PROJECT_ROOT / "prompts" / "policy_association_system_prompt.md"
ASSOCIATION_LLM_ENABLED = (
    os.getenv("POLICY_ASSOCIATION_LLM_ENABLED", "false") or "false"
).strip().lower() not in {"0", "false", "no"}
ASSOCIATION_LLM_API_URL = (
    os.getenv("POLICY_ASSOCIATION_API_URL", os.getenv("API_URL", "")) or ""
).strip()
ASSOCIATION_LLM_API_KEY = (
    os.getenv("POLICY_ASSOCIATION_API_KEY", os.getenv("API_KEY", "")) or ""
).strip()
ASSOCIATION_LLM_MODEL_NAME = (
    os.getenv("POLICY_ASSOCIATION_MODEL_NAME", os.getenv("MODEL_NAME", "")) or ""
).strip()
ASSOCIATION_LLM_TIMEOUT = max(
    1,
    int(os.getenv("POLICY_ASSOCIATION_LLM_TIMEOUT", os.getenv("SEMANTIC_MATCH_LLM_TIMEOUT", "90"))),
)
ASSOCIATION_LLM_RETRIES = max(
    0,
    int(os.getenv("POLICY_ASSOCIATION_LLM_RETRIES", os.getenv("SEMANTIC_MATCH_LLM_RETRIES", "2"))),
)
ASSOCIATION_LLM_RETRY_BACKOFF = max(
    0.0,
    float(os.getenv("POLICY_ASSOCIATION_LLM_RETRY_BACKOFF", os.getenv("SEMANTIC_MATCH_LLM_RETRY_BACKOFF", "0.5"))),
)

DIMENSION_WEIGHTS: Dict[str, int] = {
    "industry": 20,
    "scale": 15,
    "lifecycle_qualification": 15,
    "research_development": 15,
    "employment": 10,
    "region_park": 15,
    "education_talent": 10,
}
STATUS_COEFFICIENTS: Dict[str, Optional[float]] = {
    "strong": 1.0,
    "medium": 0.7,
    "weak": 0.4,
    "conflict": 0.0,
    "unknown": 0.0,
    "not_applicable": None,
}
ASSOCIATION_SCORE_FORMULA = "Σ(维度权重×状态系数)÷有效维度权重合计×100"

# 每个维度只能使用与自身语义相符的画像字段。仅验证字段存在会让地域、
# 行业等事实被错误挪用到企业规模等维度，从而产生不可解释的分数。
DIMENSION_ENTERPRISE_EVIDENCE_FIELDS: Dict[str, frozenset[str]] = {
    "industry": frozenset({"一级行业", "二级行业", "三级行业", "业务标签", "科技服务领域", "行业领域层级", "行业领域（原始）", "高新技术领域", "企业简介（原始）", "产品和服务介绍"}),
    "scale": frozenset({"企业规模", "企业规模（原始）", "职工总数", "在职职工人数", "员工总人数（原始）", "销售收入(万元)", "主营业务收入(万元)", "营业收入（原始）", "资产总额(万元)"}),
    "lifecycle_qualification": frozenset({"注册日期", "注册日期（原始）", "注册类型", "荣誉资质", "企业资质", "平台资质", "单位类型", "指标5_高新技术企业", "指标6_研发机构"}),
    "research_development": frozenset({"研发费用总额(万元)", "研发费用占销售收入总额比例", "研发费用（原始）", "研发人员数量（原始）", "科技人员总数", "知识产权总数", "I类知识产权数量", "II类知识产权数量", "发明专利授权总量", "是否有研发类会计科目", "是否企业办研发机构", "企业办研发机构名称", "是否享受加计扣除"}),
    "employment": frozenset({"职工总数", "在职职工人数", "员工总人数（原始）", "本年新增人数", "本年新增人数（原始）", "吸纳高校毕业生人数", "吸纳高校毕业生人数（原始）"}),
    "region_park": frozenset({"注册地", "注册地详细地址", "所在省", "所在市", "所在区", "注册地址（原始）", "注册行政区", "所属国家高新区"}),
    "education_talent": frozenset({"博士人数", "硕士人数", "本科人数", "高级职称人数", "中级职称人数", "企业博士人数（原始）", "企业硕士人数（原始）", "企业本科人数（原始）", "人才资质"}),
}
DIMENSION_POLICY_EVIDENCE_FIELDS: Dict[str, frozenset[str]] = {
    "industry": frozenset({"title", "summary", "industryField", "industryFieldPath", "applicableTargets", "applicationRequirements", "ruleRequirements", "supportMethods", "policyTypes", "documentExcerpt"}),
    "scale": frozenset({"title", "summary", "applicableTargets", "applicationRequirements", "ruleRequirements", "supportMethods", "policyTypes", "documentExcerpt"}),
    "lifecycle_qualification": frozenset({"title", "summary", "applicableTargets", "applicationRequirements", "ruleRequirements", "supportMethods", "policyTypes", "documentExcerpt"}),
    "research_development": frozenset({"title", "summary", "applicableTargets", "applicationRequirements", "ruleRequirements", "supportMethods", "policyTypes", "documentExcerpt"}),
    "employment": frozenset({"title", "summary", "applicableTargets", "applicationRequirements", "ruleRequirements", "supportMethods", "policyTypes", "documentExcerpt"}),
    "region_park": frozenset({"title", "summary", "applicableScope", "applicableTargets", "applicationRequirements", "ruleRequirements", "policyTypes", "documentExcerpt"}),
    "education_talent": frozenset({"title", "summary", "applicableTargets", "applicationRequirements", "ruleRequirements", "supportMethods", "policyTypes", "documentExcerpt"}),
}

ENTERPRISE_FIELDS = (
    "注册日期", "注册地", "注册地详细地址", "所在省", "所在市", "所在区", "企业规模",
    "一级行业", "二级行业", "三级行业", "业务标签", "科技服务领域", "行业领域层级", "企业简介",
    "职工总数", "在职职工人数", "本年新增人数", "吸纳高校毕业生人数",
    "科技人员总数", "研发费用总额(万元)", "研发费用占销售收入总额比例",
    "知识产权总数", "I类知识产权数量", "II类知识产权数量", "发明专利授权总量",
    "销售收入(万元)", "主营业务收入(万元)", "资产总额(万元)",
    "博士人数", "硕士人数", "本科人数", "高级职称人数", "中级职称人数",
    "荣誉资质", "企业资质", "平台资质", "人才资质", "单位类型",
    "指标5_高新技术企业", "指标6_研发机构", "是否享受加计扣除",
)
RAW_ENTERPRISE_FIELDS: Dict[str, str] = {
    "registerDate": "注册日期（原始）",
    "registerAddress": "注册地址（原始）",
    "registerType": "注册类型",
    "registerDistrict": "注册行政区",
    "nationalHighTechZone": "所属国家高新区",
    "industryField": "行业领域（原始）",
    "highTechField": "高新技术领域",
    "enterpriseScale": "企业规模（原始）",
    "brief": "企业简介（原始）",
    "productServiceIntro": "产品和服务介绍",
    "hasRndAccount": "是否有研发类会计科目",
    "hasRndInstitution": "是否企业办研发机构",
    "rndInstitutionName": "企业办研发机构名称",
}


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    return str(value).strip()


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _first_dict(*values: Any) -> Dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def _first_text(*values: Any) -> str:
    for value in values:
        text = _safe_text(value)
        if text:
            return text
    return ""


def _bounded_unique_texts(values: Iterable[Any], limit: int = 80) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        text = _safe_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text[:1200])
        if len(result) >= limit:
            break
    return result


def build_enterprise_association_profile(enterprise: Dict[str, Any]) -> Dict[str, Any]:
    """从现有标准企业对象中构造只读关联画像，不改变输入契约。"""

    source = deepcopy(enterprise)
    profile: Dict[str, Any] = {}
    for field_name in ENTERPRISE_FIELDS:
        value = source.get(field_name)
        if _has_value(value):
            profile[field_name] = value

    raw = source.get("原始数据") if isinstance(source.get("原始数据"), dict) else {}
    for raw_name, display_name in RAW_ENTERPRISE_FIELDS.items():
        value = raw.get(raw_name)
        if _has_value(value) and display_name not in profile:
            profile[display_name] = value

    operation_items = raw.get("operationInfoList") if isinstance(raw.get("operationInfoList"), list) else []
    if operation_items:
        latest = next((item for item in operation_items if isinstance(item, dict)), {})
        raw_operation_fields = {
            "year": "经营数据年份",
            "employeeCount": "员工总人数（原始）",
            "rdPersonnelCount": "研发人员数量（原始）",
            "rdExpense": "研发费用（原始）",
            "operatingIncome": "营业收入（原始）",
            "newRecruitCount": "本年新增人数（原始）",
            "collegeGraduateCount": "吸纳高校毕业生人数（原始）",
            "doctorCount": "企业博士人数（原始）",
            "masterCount": "企业硕士人数（原始）",
            "bachelorCount": "企业本科人数（原始）",
        }
        for raw_name, display_name in raw_operation_fields.items():
            value = latest.get(raw_name)
            if _has_value(value) and display_name not in profile:
                profile[display_name] = value
    return profile


def _collect_rule_fragments(value: Any, output: List[str]) -> None:
    if len(output) >= 80:
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"policyFragment", "policyRequirement", "conditionContext", "conditionBranchText"}:
                text = _safe_text(child)
                if text:
                    output.append(text[:1200])
            elif isinstance(child, (dict, list)):
                _collect_rule_fragments(child, output)
    elif isinstance(value, list):
        for child in value:
            _collect_rule_fragments(child, output)


def build_policy_association_profile(policy: Dict[str, Any]) -> Dict[str, Any]:
    """构造有界的结构化政策摘要，避免默认发送材料和整篇附件。"""

    source = deepcopy(policy)
    raw_detail = _first_dict(source.get("raw_detail"))
    policy_info = _first_dict(source.get("policy_info"), raw_detail.get("policyInfo"))
    raw_conditions = raw_detail.get("conditions")
    if isinstance(raw_conditions, dict):
        raw_conditions = [raw_conditions]
    if not isinstance(raw_conditions, list):
        raw_conditions = []

    structured_conditions = source.get("structured_conditions")
    if not isinstance(structured_conditions, list):
        structured_conditions = []
    applicable_targets = _bounded_unique_texts(
        item.get("applicableTarget") for item in raw_conditions if isinstance(item, dict)
    )
    application_requirements = _bounded_unique_texts(
        [item.get("applicationRequirement") for item in raw_conditions if isinstance(item, dict)]
        + [item.get("政策原文") or item.get("政策要求") for item in structured_conditions if isinstance(item, dict)]
    )

    support_methods = raw_detail.get("supportMethods") or source.get("support_methods") or []
    if not isinstance(support_methods, list):
        support_methods = []
    support_texts = _bounded_unique_texts(
        item.get("contentDesc") if isinstance(item, dict) else item for item in support_methods
    )

    documents = raw_detail.get("documents") or source.get("documents") or []
    if not isinstance(documents, list):
        documents = []
    document_titles = _bounded_unique_texts(
        _first_text(
            item.get("policyTitle"), item.get("title"), item.get("name"), item.get("fileName")
        )
        for item in documents if isinstance(item, dict)
    )

    rule_fragments: List[str] = []
    _collect_rule_fragments(source.get("ruleTableJson") or source.get("rule_table_json") or {}, rule_fragments)
    rule_fragments = _bounded_unique_texts(rule_fragments)

    core_text_count = sum(bool(value) for value in (
        policy_info.get("summary"), policy_info.get("industryField"), policy_info.get("applicableScope"),
        applicable_targets, application_requirements,
    ))
    excerpts: List[str] = []
    if core_text_count < 2:
        # 正文只能来自加载器生成的清洗、限长摘录，不能回退发送附件原文。
        excerpts = _bounded_unique_texts([source.get("association_document_excerpt")])
        joined = "\n".join(excerpts)[:3000]
        excerpts = [joined] if joined else []

    return {
        "policyId": _first_text(source.get("id"), source.get("fusionPolicyId"), raw_detail.get("id")),
        "title": _first_text(source.get("title"), policy_info.get("policyTitle"), raw_detail.get("mainFileTitle")),
        "summary": _safe_text(policy_info.get("summary"))[:3000],
        "issuingDepartment": _first_text(raw_detail.get("issuingDepartment"), policy_info.get("issuingDepartment")),
        "industryField": _safe_text(policy_info.get("industryField")),
        "industryFieldPath": _safe_text(policy_info.get("industryFieldPath")),
        "applicableScope": _first_text(raw_detail.get("applicableScope"), policy_info.get("applicableScope")),
        "applicableTargets": applicable_targets,
        "applicationRequirements": application_requirements,
        "ruleRequirements": rule_fragments,
        "supportMethods": support_texts,
        "policyTypes": document_titles,
        "documentExcerpt": excerpts,
    }


def _json_object_from_text(content: str) -> Dict[str, Any]:
    text = content.strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start < 0 or end <= start:
        raise ValueError("关联模型返回中未找到 JSON 对象")
    payload = json.loads(text[start:end])
    if not isinstance(payload, dict):
        raise ValueError("关联模型返回必须是 JSON 对象")
    return payload


def _evidence_is_valid(
    evidence: Any, source: Dict[str, Any], value_key: str, allowed_fields: frozenset[str],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    valid: List[Dict[str, Any]] = []
    rejected: List[str] = []
    if not isinstance(evidence, list):
        return valid, rejected
    for item in evidence:
        if not isinstance(item, dict):
            rejected.append("证据不是对象")
            continue
        field = _safe_text(item.get("field"))
        claimed = _safe_text(item.get(value_key))
        if field not in allowed_fields:
            rejected.append(f"字段不属于当前维度：{field or '空字段'}")
            continue
        if field not in source:
            rejected.append(f"字段不存在：{field or '空字段'}")
            continue
        actual = _safe_text(source.get(field))
        if not claimed or (claimed not in actual and actual not in claimed):
            rejected.append(f"证据无法回读：{field}")
            continue
        valid.append({"field": field, value_key: claimed})
    return valid, rejected


def association_level(score: Optional[float]) -> str:
    if score is None:
        return "unknown"
    if score >= 80:
        return "strong"
    if score >= 60:
        return "medium"
    if score >= 30:
        return "weak"
    return "none"


def validate_and_score_association(
    model_payload: Dict[str, Any], enterprise_profile: Dict[str, Any], policy_profile: Dict[str, Any],
) -> Dict[str, Any]:
    """校验模型证据并使用固定权重计算关联分。"""

    expected_policy_id = _safe_text(policy_profile.get("policyId"))
    returned_policy_id = _safe_text(model_payload.get("policyId"))
    if returned_policy_id != expected_policy_id:
        raise ValueError(f"关联模型 policyId 不一致：expected={expected_policy_id}, actual={returned_policy_id}")

    raw_dimensions = model_payload.get("dimensions")
    if not isinstance(raw_dimensions, list):
        raise ValueError("关联模型 dimensions 必须是数组")

    dimensions_by_name: Dict[str, Dict[str, Any]] = {}
    duplicate_names = set()
    for item in raw_dimensions:
        if not isinstance(item, dict):
            continue
        name = _safe_text(item.get("dimension"))
        if name in dimensions_by_name:
            duplicate_names.add(name)
        elif name in DIMENSION_WEIGHTS:
            dimensions_by_name[name] = item

    dimensions: List[Dict[str, Any]] = []
    rejected_evidence: List[Dict[str, str]] = []
    denominator = 0.0
    numerator = 0.0
    informative_dimension_count = 0
    reason_candidates: List[Tuple[float, str]] = []
    for name, weight in DIMENSION_WEIGHTS.items():
        raw = dimensions_by_name.get(name, {})
        status = _safe_text(raw.get("status"))
        reason = _safe_text(raw.get("reason"))[:500]
        local_rejections: List[str] = []

        if name in duplicate_names:
            status = "unknown"
            reason = "模型返回重复维度，已降级为未知。"
            local_rejections.append("重复维度")
        elif status not in STATUS_COEFFICIENTS:
            status = "unknown"
            reason = "模型返回非法状态，已降级为未知。"
            local_rejections.append("非法状态")

        enterprise_evidence, enterprise_rejections = _evidence_is_valid(
            raw.get("enterpriseEvidence"), enterprise_profile, "value",
            DIMENSION_ENTERPRISE_EVIDENCE_FIELDS[name],
        )
        policy_evidence, policy_rejections = _evidence_is_valid(
            raw.get("policyEvidence"), policy_profile, "text",
            DIMENSION_POLICY_EVIDENCE_FIELDS[name],
        )
        local_rejections.extend(enterprise_rejections)
        local_rejections.extend(policy_rejections)

        if status in {"strong", "medium", "weak", "conflict"} and (
            not enterprise_evidence or not policy_evidence
        ):
            status = "unknown"
            reason = "关联结论缺少可回读的企业或政策双侧证据，已降级为未知。"
            local_rejections.append("双侧证据不足")

        coefficient = STATUS_COEFFICIENTS[status]
        contribution: Optional[float] = None
        if coefficient is not None:
            denominator += weight
            contribution = round(weight * coefficient, 4)
            numerator += contribution
            if status in {"strong", "medium", "weak", "conflict"}:
                informative_dimension_count += 1
            if contribution > 0 and reason and enterprise_evidence and policy_evidence:
                reason_candidates.append((contribution, reason))

        for rejection in local_rejections:
            rejected_evidence.append({"dimension": name, "reason": rejection})
        dimensions.append({
            "dimension": name,
            "weight": weight,
            "status": status,
            "coefficient": coefficient,
            "contribution": contribution,
            "enterpriseEvidence": enterprise_evidence,
            "policyEvidence": policy_evidence,
            "reason": reason,
        })

    score = (
        round(numerator / denominator * 100, 2)
        if denominator and informative_dimension_count
        else None
    )
    for dimension in dimensions:
        contribution = dimension.get("contribution")
        included = dimension.get("coefficient") is not None
        dimension["includedInDenominator"] = included
        dimension["normalizedContribution"] = (
            round(float(contribution) / denominator * 100, 2)
            if included and contribution is not None and denominator
            else None
        )
    reason_candidates.sort(key=lambda item: item[0], reverse=True)
    reasons: List[str] = []
    for _contribution, reason in reason_candidates:
        if reason not in reasons:
            reasons.append(reason)
        if len(reasons) >= 3:
            break
    return {
        "score": score,
        "level": association_level(score),
        "isAssociated": score >= 60 if score is not None else None,
        "status": "matched",
        "reasons": reasons,
        "calculation": {
            "formula": ASSOCIATION_SCORE_FORMULA,
            "expression": (
                f"{round(numerator, 4)}÷{round(denominator, 4)}×100={score}"
                if score is not None
                else ""
            ),
            "weightedContribution": round(numerator, 4) if denominator else None,
            "effectiveWeight": round(denominator, 4) if denominator else None,
            "score": score,
        },
        "dimensions": dimensions,
        "rejectedEvidence": rejected_evidence,
    }


def disabled_association_result(reason: str) -> Dict[str, Any]:
    return {
        "score": None,
        "level": "unknown",
        "isAssociated": None,
        "status": "disabled",
        "reasons": [],
        "calculation": {
            "formula": ASSOCIATION_SCORE_FORMULA,
            "expression": "",
            "weightedContribution": None,
            "effectiveWeight": None,
            "score": None,
        },
        "dimensions": [],
        "rejectedEvidence": [],
        "disabledReason": reason,
    }


def retryable_association_result(error_type: str, message: str, attempts: int) -> Dict[str, Any]:
    return {
        "score": None,
        "level": "unknown",
        "isAssociated": None,
        "status": "待重试",
        "reasons": [],
        "calculation": {
            "formula": ASSOCIATION_SCORE_FORMULA,
            "expression": "",
            "weightedContribution": None,
            "effectiveWeight": None,
            "score": None,
        },
        "dimensions": [],
        "rejectedEvidence": [],
        "errorType": error_type,
        "error": message[:500],
        "retryable": True,
        "attempts": attempts,
    }


def assess_policy_association(
    enterprise: Dict[str, Any], policy: Dict[str, Any], progress_callback: Optional[Any] = None,
) -> Dict[str, Any]:
    """独立调用模型判断关联度；任何失败都收敛为关联链路自己的状态。"""

    if not ASSOCIATION_LLM_ENABLED:
        return disabled_association_result("POLICY_ASSOCIATION_LLM_ENABLED 未启用。")
    if not ASSOCIATION_LLM_API_URL or not ASSOCIATION_LLM_MODEL_NAME:
        return disabled_association_result("关联模型 API_URL 或 MODEL_NAME 未配置。")

    enterprise_profile = build_enterprise_association_profile(enterprise)
    policy_profile = build_policy_association_profile(policy)
    policy_id = _safe_text(policy_profile.get("policyId"))
    if not policy_id:
        return retryable_association_result("association_policy_id_missing", "政策缺少 fusionPolicyId。", 0)

    model_input = {
        "policyId": policy_id,
        "enterpriseProfile": enterprise_profile,
        "policyProfile": policy_profile,
        "dimensions": [{"dimension": name, "weight": weight} for name, weight in DIMENSION_WEIGHTS.items()],
    }
    prompt = json.dumps(model_input, ensure_ascii=False, separators=(",", ":"), default=str)
    system_prompt = ASSOCIATION_PROMPT_FILE.read_text(encoding="utf-8").strip()
    headers = {"Content-Type": "application/json"}
    if ASSOCIATION_LLM_API_KEY:
        headers["Authorization"] = f"Bearer {ASSOCIATION_LLM_API_KEY}"

    attempts = ASSOCIATION_LLM_RETRIES + 1
    started_at = time.monotonic()
    last_error: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            if progress_callback:
                progress_callback(
                    f"政策关联模型开始请求：政策ID={policy_id} attempt={attempt}/{attempts} promptChars={len(prompt)}"
                )
            response = requests.post(
                ASSOCIATION_LLM_API_URL,
                headers=headers,
                json={
                    "model": ASSOCIATION_LLM_MODEL_NAME,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.0,
                    "response_format": {"type": "json_object"},
                    "chat_template_kwargs": {"enable_thinking": False},
                },
                timeout=ASSOCIATION_LLM_TIMEOUT,
            )
            response.raise_for_status()
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            model_payload = _json_object_from_text(_safe_text(content))
            result = validate_and_score_association(model_payload, enterprise_profile, policy_profile)
            usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
            result.update({
                "model": ASSOCIATION_LLM_MODEL_NAME,
                "metrics": {
                    "requestMs": round((time.monotonic() - started_at) * 1000),
                    "promptChars": len(prompt),
                    "promptTokens": usage.get("prompt_tokens"),
                    "completionTokens": usage.get("completion_tokens"),
                    "totalTokens": usage.get("total_tokens"),
                    "attempts": attempt,
                },
                "modelInput": model_input,
                "modelOutput": model_payload,
            })
            if progress_callback:
                progress_callback(
                    f"政策关联模型完成：政策ID={policy_id} score={result['score']} requestMs={result['metrics']['requestMs']}"
                )
            return result
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            error_type = "association_model_timeout" if isinstance(exc, requests.Timeout) else "association_model_connection_error"
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            last_error = exc
            error_type = "association_model_protocol_error"
        if attempt < attempts and ASSOCIATION_LLM_RETRY_BACKOFF:
            time.sleep(ASSOCIATION_LLM_RETRY_BACKOFF * attempt)

    return retryable_association_result(error_type, str(last_error or "关联模型调用失败"), attempts)
