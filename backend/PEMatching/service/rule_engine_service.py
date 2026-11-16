"""按规则表执行企业-政策匹配。"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests
from dotenv import load_dotenv

from service.evidence_tiering import classify_runtime_evidence
from service.semantic_match_service import semantic_item_key


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

PROMPTS_DIR = PROJECT_ROOT / "prompts"
RULE_ITEM_JUDGE_SYSTEM_PROMPT_FILE = PROMPTS_DIR / "rule_item_judge_system_prompt.md"
RULE_ITEM_JUDGE_USER_PROMPT_FILE = PROMPTS_DIR / "rule_item_judge_user_prompt.md"
DEFAULT_RULE_PROFILES = [
    {
        "policy_keyword": "科技型中小企业评价",
        "rule_path": PROJECT_ROOT / "企业测试" / "科技型中小企业_企业数据直接匹配规则表_20260722.json",
    }
]
RULE_LLM_API_URL = (os.getenv("API_URL", "") or "").strip()
RULE_LLM_API_KEY = (os.getenv("API_KEY", "") or "").strip()
RULE_LLM_MODEL_NAME = (os.getenv("MODEL_NAME", "") or "").strip()
RULE_LLM_TIMEOUT = int(os.getenv("RULE_LLM_TIMEOUT", "90"))
RULE_LLM_STRICT = (os.getenv("RULE_LLM_STRICT", "true") or "true").strip().lower() not in {"0", "false", "no"}
DEFAULT_MISSING_SCORE = 0.1
PRESENT_DATA_BASE_SCORE = 0.3
FIELD_ALIASES = {
    "地址": ["注册地址", "实际经营地址", "注册地详细地址", "办公地址", "详细地址"],
    "注册地址": ["注册地", "地址", "实际经营地址", "注册地详细地址", "办公地址", "详细地址"],
    "主体资格": ["机构性质", "企业性质"],
    "单位类型": ["企业类型", "机构性质", "单位类型"],
    "税务信用": ["税务信用等级"],
    "研发制度": ["研发制度"],
    "22+N": ["所属行业领域(22+N)"],
    "国家统计局-工业战略性新兴产业分类目录（2023）": ["战略性新兴产业分类"],
    "技术交易额": ["技术合同成交额(万元)"],
    "技术成果转化": ["技术成果转化数量"],
    "成果": ["成果数量"],
    "软著或商标": ["软著或商标数量"],
    "论文或文献": ["论文或文献数量"],
    "知识产权约定": ["知识产权归属"],
    "年研发投入": ["年研发投入(万元)", "研发费用总额（万元）"],
    "研发设备原值": ["研发设备原值(万元)"],
    "融资金额": ["融资金额(万元)"],
    "联合申报": ["联合申报情况"],
    "合作协议": ["产学研合作协议"],
    "合作基础": ["产学研合作基础"],
    "销售收入（万元）": ["销售收入(万元)", "销售收入（万元）"],
    "年销售收入": ["销售收入(万元)", "主营业务收入(万元)"],
    "营业收入": ["销售收入(万元)", "主营业务收入(万元)"],
    "营收规模": ["销售收入(万元)", "销售收入（万元）"],
    "资产总额（万元）": ["资产总额(万元)", "资产总额（万元）"],
    "资产规模": ["资产总额(万元)", "资产总额（万元）"],
    "研发费用总额（万元）": ["研发费用总额(万元)", "研发费用总额（万元）", "年研发投入(万元)"],
    "税收规模": ["纳税总额（万元）", "纳税总额(万元)"],
    "指标4：企业信用": ["指标4_企业信用", "企业信用", "企业信用状态", "合规经营状态"],
    "指标5：高新技术企业": ["指标5_高新技术企业", "高新技术企业认定"],
    "指标6：研发机构": ["指标6_研发机构", "研发机构认定", "新型研发机构认定"],
    "指标7：科技奖励": ["指标7_科技奖励", "荣誉资质"],
    "指标8：制定标准": ["指标8_制定标准", "标准数量"],
    "企业职工总数": ["职工总数"],
    "科技人员占比": ["科技人员占比", "科技人员占企业职工总数比例"],
    "科技人员数量": ["科技人员总数", "研发人员数量"],
    "企业资质": ["指标5_高新技术企业", "高新技术企业认定"],
    "平台资质": ["指标6_研发机构", "研发机构认定", "新型研发机构认定"],
    "科技奖励": ["指标7_科技奖励", "荣誉资质"],
    "标准": ["指标8_制定标准", "标准数量"],
    "博士人数": ["企业博士人数"],
    "硕士人数": ["企业硕士人数"],
    "本科人数": ["企业本科人数"],
    "设施情况": ["研发设施情况"],
}


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


_MONEY_UNIT_FACTORS = {
    "元": 1.0,
    "万元": 10_000.0,
    "亿元": 100_000_000.0,
}

_API_MONEY_FIELD_UNITS = {
    "rdExpense": "元",
    "techContractAmount": "元",
    "operatingIncome": "元",
    "mainBusinessIncome": "元",
    "netProfit": "元",
    "beginOwnerEquity": "元",
    "endOwnerEquity": "元",
    "registerCapital": "万元",
}


def _normalize_money_unit(value: Any) -> str:
    """标准化金额单位；只处理可以精确换算的人民币金额单位。"""
    text = _safe_text(value).replace("人民币", "").replace(" ", "")
    return text if text in _MONEY_UNIT_FACTORS else ""


def _convert_money_value(value: Any, source_unit: Any, target_unit: Any) -> float | None:
    numeric = _safe_float(value)
    source_factor = _MONEY_UNIT_FACTORS.get(_normalize_money_unit(source_unit))
    target_factor = _MONEY_UNIT_FACTORS.get(_normalize_money_unit(target_unit))
    if numeric is None or source_factor is None or target_factor is None:
        return None
    return numeric * source_factor / target_factor


def _direct_field_name_from_path(path: Any) -> str:
    """提取扁平化原始 JSON 路径中的顶层字段名，复杂路径不作猜测。"""
    text = _safe_text(path)
    if text.startswith("$."):
        candidate = text[2:]
        return candidate if candidate and "." not in candidate and "[" not in candidate else ""
    if text.startswith("$[") and text.endswith("]"):
        candidate = text[2:-1]
        return candidate if candidate and "[" not in candidate and "]" not in candidate else ""
    return ""


def _infer_money_unit_from_field_name(field_name: str) -> str:
    normalized_name = field_name.replace("（", "(").replace("）", ")")
    if "亿元" in normalized_name:
        return "亿元"
    if "万元" in normalized_name:
        return "万元"
    if "元" in normalized_name:
        return "元"
    return ""


def _candidate_path_tokens(path: Any) -> List[str | int]:
    text = _safe_text(path)
    if text.startswith("$normalized."):
        text = "$.normalized." + text[len("$normalized."):]
    if not text.startswith("$"):
        return []
    tokens: List[str | int] = []
    for name, index, bracket_name in re.findall(r"\.([^\.\[]+)|\[(\d+)\]|\[([^\]]+)\]", text[1:]):
        if index:
            tokens.append(int(index))
        else:
            tokens.append(name or bracket_name)
    return tokens


def _resolve_candidate_path(enterprise: Dict[str, Any], path: Any) -> Tuple[bool, Any, str]:
    tokens = _candidate_path_tokens(path)
    if not tokens:
        return False, None, ""

    if tokens[0] == "normalized":
        current: Any = enterprise
        tokens = tokens[1:]
    else:
        current = enterprise.get("原始数据") if isinstance(enterprise.get("原始数据"), dict) else enterprise

    for token in tokens:
        if isinstance(token, int):
            if not isinstance(current, list) or token >= len(current):
                return False, None, ""
            current = current[token]
        else:
            if not isinstance(current, dict) or token not in current:
                return False, None, ""
            current = current[token]
    return True, current, _safe_text(tokens[-1]) if tokens else ""


def _source_unit_for_candidate_path(enterprise: Dict[str, Any], path: Any, field_name: str) -> str:
    raw_record = enterprise.get("原始数据") if isinstance(enterprise.get("原始数据"), dict) else enterprise
    if isinstance(raw_record, dict):
        explicit_unit = _normalize_money_unit(raw_record.get(f"{field_name}单位"))
        if explicit_unit:
            return explicit_unit
    if field_name in _API_MONEY_FIELD_UNITS:
        return _API_MONEY_FIELD_UNITS[field_name]
    inferred = _infer_money_unit_from_field_name(field_name)
    if inferred:
        return inferred
    tokens = _candidate_path_tokens(path)
    if tokens and tokens[0] == "normalized":
        index = enterprise.get("_enterprise_standard_index") or {}
        entry = index.get(field_name) if isinstance(index, dict) else None
        if isinstance(entry, dict):
            return _normalize_money_unit(entry.get("单位"))
    return ""


def _semantic_fact_paths(fact: Dict[str, Any]) -> List[str]:
    evidence = fact.get("evidence") if isinstance(fact.get("evidence"), list) else []
    paths = [_safe_text(entry.get("sourcePath")) for entry in evidence if isinstance(entry, dict)]
    paths.extend(_safe_text(path) for path in fact.get("mappedFields", []) if _safe_text(path))
    return list(dict.fromkeys(path for path in paths if path))


def _money_path_matches_rule(item: Dict[str, Any], path: Any, field_name: str) -> bool:
    """校验金额规则与证据字段的业务语义，禁止把资质 0/1 当成金额。"""
    candidate_text = f"{_safe_text(path)}；{field_name}".lower()
    rule_text = "；".join(
        [
            _safe_text(item.get("itemName")),
            _safe_text(item.get("policyFragment")),
            "；".join(_safe_text(value) for value in item.get("enterpriseFields", []) or []),
        ]
    ).lower()
    if any(marker in candidate_text for marker in (
        "ishightechenterprise", "istechsme", "isinnovativesme",
        "isspecialized", "istechleader", "资格", "资质",
    )):
        return False
    if any(marker in rule_text for marker in ("技术交易", "技术合同")):
        return any(marker in candidate_text for marker in ("techcontract", "技术交易", "技术合同"))
    if any(marker in rule_text for marker in ("研发费用", "研发投入")):
        return any(marker in candidate_text for marker in ("rdexpense", "研发费用", "研发投入"))
    if any(marker in rule_text for marker in ("营业收入", "营收", "销售收入", "主营业务收入")):
        return any(marker in candidate_text for marker in (
            "operatingincome", "mainbusinessincome", "revenue", "sales", "营业收入", "销售收入", "主营业务收入", "营收",
        ))
    return any(marker in candidate_text for marker in (
        "income", "revenue", "sales", "amount", "expense", "营业收入", "销售收入", "金额", "费用", "投入", "资产", "利润",
    ))


def _invalidate_incompatible_money_fact(fact: Dict[str, Any]) -> Dict[str, Any]:
    corrected = dict(fact)
    corrected.update(
        {
            "status": "missing",
            "mappingMethod": "none",
            "mappedFields": [],
            "normalizedValue": None,
            "normalizedUnit": "",
            "displayValue": "",
            "confidence": 0.0,
            "evidence": [],
            "reason": "模型金额证据字段与当前规则的业务语义不兼容，已拒绝使用，等待正确经营字段取证。",
        }
    )
    return corrected


def _invalidate_semantic_fact(fact: Dict[str, Any], reason: str, error_code: str) -> Dict[str, Any]:
    corrected = dict(fact)
    corrected.update(
        {
            "status": "missing",
            "mappingMethod": "none",
            "mappedFields": [],
            "normalizedValue": None,
            "normalizedUnit": "",
            "displayValue": "",
            "confidence": 0.0,
            "evidence": [],
            "errorCode": error_code,
            "reason": reason,
        }
    )
    return corrected


def _semantic_fact_search_text(fact: Dict[str, Any]) -> str:
    parts = _semantic_fact_paths(fact)
    for entry in fact.get("evidence", []) or []:
        if isinstance(entry, dict):
            parts.extend([_safe_text(entry.get("sourcePath")), _safe_text(entry.get("sourceText"))])
    return " ".join(part for part in parts if part).lower()


def _semantic_evidence_incompatibility(item: Dict[str, Any], fact: Dict[str, Any]) -> str:
    requirement = " ".join(
        _safe_text(value)
        for value in (
            item.get("itemName"), item.get("policyFragment"),
            " ".join(item.get("enterpriseFields", []) or []),
            (item.get("matchRule") or {}).get("description"),
        )
        if _safe_text(value)
    ).lower()
    evidence = _semantic_fact_search_text(fact)
    if not requirement or not evidence:
        return ""

    education_markers = ("学历", "大专", "本科", "硕士", "博士", "education", "college", "bachelor", "master", "doctor")
    rd_markers = ("研发人员", "科技人员", "rdpersonnel", "researchpersonnel", "technicalpersonnel")
    if any(marker in requirement for marker in education_markers) and any(marker in evidence for marker in rd_markers):
        return "学历人员指标不能由研发人员或科技人员指标证明"
    if any(marker in requirement for marker in rd_markers) and any(marker in evidence for marker in education_markers):
        return "研发人员或科技人员指标不能由学历人员指标证明"

    total_staff_markers = ("职工总数", "员工总数", "人员总数", "employeecount", "staffcount")
    specialized_staff_markers = ("专职人员", "研发人员", "科技人员", "rdpersonnelcount", "researchpersonnelcount")
    if any(marker in requirement for marker in specialized_staff_markers) and any(marker in evidence for marker in total_staff_markers):
        return "员工总数不能代替政策要求的专门人员数量"

    conflict_groups = (
        (("科技奖励", "获奖", "奖项"), ("实验室", "研发机构", "联盟", "项目参与", "制定标准"), "科技奖励证据类别不兼容"),
        (("实验室", "研发机构"), ("科技型中小企业", "高新技术企业", "奖项", "制定标准"), "研发机构证据类别不兼容"),
        (("制定标准", "标准数量"), ("科技型中小企业", "高新技术企业", "奖项", "实验室"), "标准制定证据类别不兼容"),
        (("项目参与", "承担项目", "项目经验"), ("科技型中小企业", "高新技术企业", "奖项", "实验室"), "项目参与证据类别不兼容"),
    )
    for required_markers, incompatible_markers, reason in conflict_groups:
        if any(marker in requirement for marker in required_markers) and any(marker in evidence for marker in incompatible_markers):
            return reason

    ip_groups = {
        "发明专利": ("实用新型", "外观设计", "软件著作权", "商标"),
        "实用新型": ("发明专利", "外观设计", "软件著作权", "商标"),
        "软件著作权": ("发明专利", "实用新型", "外观设计", "商标"),
        "商标": ("发明专利", "实用新型", "外观设计", "软件著作权"),
    }
    for required_type, incompatible_types in ip_groups.items():
        if required_type in requirement and any(marker in evidence for marker in incompatible_types):
            return f"知识产权类型不兼容：政策要求{required_type}"

    districts = ("思明区", "湖里区", "集美区", "海沧区", "同安区", "翔安区")
    required_districts = [district for district in districts if district in requirement]
    evidence_districts = [district for district in districts if district in evidence]
    if required_districts and evidence_districts and not set(required_districts).intersection(evidence_districts):
        return "地址证据与政策要求的行政区不一致"

    required_years = set(re.findall(r"20\d{2}", requirement))
    evidence_years = set(re.findall(r"20\d{2}", evidence))
    if required_years and evidence_years and not required_years.intersection(evidence_years):
        return "企业证据年度与政策要求年度不一致"
    return ""


def _source_unit_for_standard_field(enterprise: Dict[str, Any], field_name: str) -> str:
    """读取本地标准字段或其别名的金额单位。"""
    index = enterprise.get("_enterprise_standard_index") or {}
    for candidate in [field_name] + FIELD_ALIASES.get(field_name, []):
        entry = index.get(candidate) if isinstance(index, dict) else None
        if isinstance(entry, dict) and _is_present(entry.get("标准值")):
            unit = _normalize_money_unit(entry.get("单位"))
            if unit:
                return unit
        unit = _infer_money_unit_from_field_name(candidate)
        if unit:
            return unit
    return ""


def _convert_local_money_values_for_rule(
    enterprise: Dict[str, Any],
    resolved_fields: List[str],
    values: List[Any],
    rule_unit: str,
) -> List[float]:
    """将本地标准金额字段转换到政策规则单位，避免只替换单位文本。"""
    converted_values: List[float] = []
    for field_name, value in zip(resolved_fields, values):
        source_unit = _source_unit_for_standard_field(enterprise, field_name)
        converted = _convert_money_value(value, source_unit, rule_unit)
        if converted is not None:
            converted_values.append(converted)
    return converted_values


def _validate_semantic_fact_evidence(
    enterprise: Dict[str, Any], item: Dict[str, Any], fact: Dict[str, Any] | None,
) -> Dict[str, Any] | None:
    if not isinstance(fact, dict):
        return fact
    if fact.get("localDerived") is True:
        return fact
    if _safe_text(fact.get("status")).lower() == "missing":
        return fact

    incompatibility = _semantic_evidence_incompatibility(item, fact)
    if incompatibility:
        return _invalidate_semantic_fact(
            fact,
            f"语义证据类型校验拒绝：{incompatibility}。",
            "semantic_evidence_incompatible",
        )

    valid_paths = []
    for path in _semantic_fact_paths(fact):
        found, value, _ = _resolve_candidate_path(enterprise, path)
        if found and _is_present(value):
            valid_paths.append(path)
    if valid_paths:
        return fact

    return _invalidate_semantic_fact(
        fact,
        "模型给出的证据路径未在本次企业接口数据中找到，已拒绝使用该事实。",
        "semantic_evidence_path_invalid",
    )


def _format_numeric_value(value: float) -> str:
    return f"{value:.12g}"


def _reconcile_direct_money_fact(
    enterprise: Dict[str, Any], item: Dict[str, Any], fact: Dict[str, Any] | None,
) -> Dict[str, Any] | None:
    """以原始直接字段及其单位纠正模型金额换算，模型仅保留字段映射职责。"""
    fact = _validate_semantic_fact_evidence(enterprise, item, fact)
    if not isinstance(fact, dict) or _safe_text(fact.get("mappingMethod")).lower() == "none":
        return fact

    target_unit = _normalize_money_unit(fact.get("normalizedUnit"))
    if not target_unit:
        return fact
    corrected_values: List[Tuple[float, str, str, float]] = []
    compatible_paths: List[str] = []
    has_compatible_path = False
    for path in _semantic_fact_paths(fact):
        found, raw_value, field_name = _resolve_candidate_path(enterprise, path)
        if not found:
            continue
        if not _money_path_matches_rule(item, path, field_name):
            continue
        has_compatible_path = True
        source_value = _safe_float(raw_value)
        source_unit = _source_unit_for_candidate_path(enterprise, path, field_name)
        corrected_value = _convert_money_value(source_value, source_unit, target_unit)
        if corrected_value is None:
            continue
        corrected_values.append((corrected_value, field_name, source_unit, source_value or 0.0))
        compatible_paths.append(path)
    if corrected_values:
        model_value = _safe_float(fact.get("normalizedValue"))
        selected = min(
            corrected_values,
            key=lambda item: abs(item[0] - model_value) if model_value is not None else 0.0,
        )
        corrected_value, field_name, source_unit, source_value = selected
        corrected = dict(fact)
        corrected["normalizedValue"] = corrected_value
        corrected["normalizedUnit"] = target_unit
        corrected["displayValue"] = f"{_format_numeric_value(corrected_value)} {target_unit}"
        corrected["mappedFields"] = compatible_paths
        corrected["evidence"] = [
            entry for entry in fact.get("evidence", []) or []
            if isinstance(entry, dict) and _safe_text(entry.get("sourcePath")) in compatible_paths
        ]
        corrected["reason"] = (
            f"本地按原始字段 {field_name}={_format_numeric_value(source_value)}{source_unit} "
            f"归一化为 {_format_numeric_value(corrected_value)}{target_unit}。"
        )
        return corrected
    if _semantic_fact_paths(fact) and not has_compatible_path:
        return _invalidate_incompatible_money_fact(fact)
    return fact


def _looks_like_fraction_ratio_path(path: Any) -> bool:
    text = _safe_text(path).lower()
    return any(marker in text for marker in ("ratio", "比例", "占比", "增长率", "收益率"))


def _normalize_percentage_for_rule(
    value: Any,
    source_unit: Any,
    source_paths: List[str],
    enterprise_source_type: str,
) -> float | None:
    numeric = _safe_float(value)
    if numeric is None:
        return None
    normalized_unit = _safe_text(source_unit).lower()
    fraction_unit = normalized_unit in {"小数", "比例", "ratio", "fraction"}
    api_fraction_path = (
        enterprise_source_type == "policy_recommendation_api_record"
        and any(
            _looks_like_fraction_ratio_path(path) or "operationInfoList" in _safe_text(path)
            for path in source_paths
        )
        and abs(numeric) <= 1
    )
    return numeric * 100 if fraction_unit or api_fraction_path else numeric


def _is_present(value: Any) -> bool:
    return value is not None and not (isinstance(value, str) and value.strip() == "")


def _sanitize_llm_json(content: str) -> Dict[str, Any]:
    text = _safe_text(content)
    if not text:
        raise ValueError("模型返回为空")

    json_start = text.find("{")
    json_end = text.rfind("}") + 1
    if json_start < 0 or json_end <= json_start:
        raise ValueError("模型返回中未找到 JSON 对象")

    json_text = text[json_start:json_end]
    try:
        return json.loads(json_text)
    except json.JSONDecodeError:
        fixed_text = re.sub(r",(\s*[}\]])", r"\1", json_text)
        return json.loads(fixed_text)


def _read_prompt_file(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"提示词文件不存在: {path}")
    return path.read_text(encoding="utf-8").strip()


@lru_cache(maxsize=1)
def _load_rule_item_judge_system_prompt() -> str:
    return _read_prompt_file(RULE_ITEM_JUDGE_SYSTEM_PROMPT_FILE)


@lru_cache(maxsize=1)
def _load_rule_item_judge_user_prompt_template() -> str:
    return _read_prompt_file(RULE_ITEM_JUDGE_USER_PROMPT_FILE)


def _build_llm_enterprise_context(enterprise: Dict[str, Any], field_names: List[str], values: List[Any]) -> Dict[str, Any]:
    relevant_fields: Dict[str, Any] = {}
    for index, field_name in enumerate(field_names):
        if index < len(values):
            relevant_fields[field_name] = values[index]

    if not relevant_fields:
        fallback_fields = [
            "注册地址",
            "实际经营地址",
            "属地信息",
            "注册地",
            "地址",
            "一级行业",
            "二级行业",
            "22+N",
            "国家统计局-工业战略性新兴产业分类目录（2023）",
            "所属行业领域(22+N)",
            "高新技术领域",
            "主体资格",
            "单位类型",
            "税务信用",
            "研发制度",
            "安全生产",
            "质量合规",
            "企业信用",
            "工商信用",
            "科研信用",
            "企业职工总数",
            "营收规模",
            "资产规模",
            "科技人员占比",
            "科技人员数量",
            "企业资质",
            "平台资质",
            "科技奖励",
            "标准",
            "年研发投入",
            "研发投入强度",
        ]
        for field_name in fallback_fields:
            value = _lookup_enterprise_value(enterprise, field_name)
            if _is_present(value):
                relevant_fields[field_name] = value

    enterprise_snapshot: Dict[str, Any] = {}
    snapshot_candidates = [
        "企业名称",
        "统一社会信用代码",
        "注册地",
        "注册地址",
        "实际经营地址",
        "属地信息",
        "地址",
        "一级行业",
        "二级行业",
        "22+N",
        "国家统计局-工业战略性新兴产业分类目录（2023）",
        "企业资质",
        "平台资质",
        "科技奖励",
        "标准",
        "人才资质",
        "主体资格",
        "单位类型",
        "税务信用",
        "研发制度",
        "所属行业领域(22+N)",
        "战略性新兴产业分类",
        "高新技术领域",
        "职工总数",
        "企业职工总数",
        "营收规模",
        "资产规模",
        "科技人员数量",
        "科技人员占比",
        "科技人员总数",
        "科技人员占企业职工总数比例",
        "年研发投入",
        "研发投入强度",
        "研发费用总额(万元)",
        "研发费用占销售收入总额比例",
        "销售收入(万元)",
        "资产总额(万元)",
        "税收规模",
        "总得分",
        "指标4_企业信用",
        "指标5_高新技术企业",
        "指标6_研发机构",
        "指标7_科技奖励",
        "指标8_制定标准",
        "安全生产",
        "质量合规",
        "科研信用",
        "工商信用",
        "税务信用",
        "研发制度",
    ]
    for field_name in snapshot_candidates:
        value = _lookup_enterprise_value(enterprise, field_name)
        if _is_present(value):
            enterprise_snapshot[field_name] = value

    return {
        "企业名称": enterprise.get("企业名称", ""),
        "统一社会信用代码": enterprise.get("统一社会信用代码", ""),
        "注册地": enterprise.get("注册地", ""),
        "一级行业": enterprise.get("一级行业", ""),
        "二级行业": enterprise.get("二级行业", ""),
        "关键判断字段": relevant_fields,
        "企业摘要字段": enterprise_snapshot,
    }


def _build_item_specific_guidance(enterprise: Dict[str, Any], item: Dict[str, Any], group: Dict[str, Any]) -> str:
    group_text = _safe_text(group.get("rawText"))
    policy_fragment = _safe_text(item.get("policyFragment"))
    guidance: List[str] = []

    if "科技人员指标得分不得为0分" in group_text or "科技人员指标得分不得为0分" in policy_fragment:
        staff_total = _safe_float(_lookup_enterprise_value(enterprise, "企业职工总数"))
        tech_count = _safe_float(_lookup_enterprise_value(enterprise, "科技人员数量"))
        tech_ratio = _safe_float(_lookup_enterprise_value(enterprise, "科技人员占比"))
        total_score = _safe_float(_lookup_enterprise_value(enterprise, "总得分"))

        guidance.append("针对“科技人员指标得分不得为0分”，可以联合参考企业职工总数、科技人员数量、科技人员占比、总得分等字段，不要求企业必须直接提供同名字段。")
        guidance.append("如果企业职工总数大于0、科技人员数量大于0、科技人员占比大于0，说明企业存在实际科技人员，可据此判断科技人员指标通常不为0分；除非存在明确相反证据。")
        guidance.append("如果综合评价总得分已达到60分及以上，且同时存在实际科技人员证据，应优先给出 pass，而不是简单返回 not_evaluable。")

        observed_parts: List[str] = []
        if staff_total is not None:
            observed_parts.append(f"企业职工总数={staff_total}")
        if tech_count is not None:
            observed_parts.append(f"科技人员数量={tech_count}")
        if tech_ratio is not None:
            observed_parts.append(f"科技人员占比={tech_ratio}")
        if total_score is not None:
            observed_parts.append(f"总得分={total_score}")
        if observed_parts:
            guidance.append(f"当前企业已提供的相关字段：{'；'.join(observed_parts)}。")

    return "\n".join(f"{index + 1}. {text}" for index, text in enumerate(guidance) if text)


def _build_rule_item_judge_prompt(
    enterprise: Dict[str, Any],
    item: Dict[str, Any],
    group: Dict[str, Any],
    values: List[Any],
    target_value: Any,
) -> str:
    rule = item.get("matchRule") or {}
    field_names = list(item.get("enterpriseFields", []) or [])
    enterprise_context = _build_llm_enterprise_context(enterprise, field_names, values)
    item_specific_guidance = _build_item_specific_guidance(enterprise, item, group) or "无"
    template = _load_rule_item_judge_user_prompt_template()
    replacements = {
        "__GROUP_RAW_TEXT__": _safe_text(group.get("rawText")),
        "__CONDITION_NO__": _safe_text(group.get("conditionNo")),
        "__ITEM_NAME__": _safe_text(item.get("itemName")),
        "__POLICY_FRAGMENT__": _safe_text(item.get("policyFragment")),
        "__ITEM_STATUS__": _safe_text(item.get("itemStatus")),
        "__RULE_KIND__": _safe_text(rule.get("ruleKind")),
        "__OPERATOR__": _safe_text(rule.get("operator")),
        "__FIELD_TYPE__": _safe_text(rule.get("fieldType")),
        "__TARGET_VALUE__": _safe_text(target_value),
        "__RULE_DESCRIPTION__": _safe_text(rule.get("description")),
        "__ENTERPRISE_FIELDS__": json.dumps(field_names, ensure_ascii=False),
        "__ITEM_SPECIFIC_GUIDANCE__": item_specific_guidance,
        "__ENTERPRISE_CONTEXT_JSON__": json.dumps(enterprise_context, ensure_ascii=False, indent=2),
    }
    prompt = template
    for placeholder, value in replacements.items():
        prompt = prompt.replace(placeholder, value)
    return prompt


def _call_rule_judge_llm(
    enterprise: Dict[str, Any],
    item: Dict[str, Any],
    group: Dict[str, Any],
    values: List[Any],
    target_value: Any,
) -> Dict[str, Any]:
    if not RULE_LLM_API_URL or not RULE_LLM_MODEL_NAME:
        raise RuntimeError("规则判定 LLM 配置缺失，请检查 API_URL / MODEL_NAME")

    prompt = _build_rule_item_judge_prompt(enterprise, item, group, values, target_value)
    system_prompt = _load_rule_item_judge_system_prompt()

    headers = {
        "Content-Type": "application/json",
    }
    if RULE_LLM_API_KEY:
        headers["Authorization"] = f"Bearer {RULE_LLM_API_KEY}"

    payload = {
        "model": RULE_LLM_MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
        "chat_template_kwargs": {"enable_thinking": False},
    }

    response = requests.post(RULE_LLM_API_URL, headers=headers, json=payload, timeout=RULE_LLM_TIMEOUT)
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    parsed = _sanitize_llm_json(content)
    verdict = _safe_text(parsed.get("verdict")).lower()
    if verdict not in {"pass", "fail", "not_evaluable"}:
        raise ValueError(f"模型返回 verdict 非法: {verdict or '空'}")
    return parsed


def _match_degree(status: str) -> str:
    mapping = {
        "pass": "完全匹配",
        "supporting_hit": "辅助命中",
        "reference_hit": "参考命中",
        "fail": "不匹配",
        "not_evaluable": "待补判",
        "skipped": "未计分",
    }
    return mapping.get(status, "待补判")


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


def _score_to_percentage(value: Any) -> float:
    numeric = _safe_float(value)
    if numeric is None:
        return 0.0
    return round(numeric * 100, 2)


@lru_cache(maxsize=16)
def _load_rule_profile(rule_path: str) -> Dict[str, Any]:
    path = Path(rule_path)
    with path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def resolve_rule_profile(policy: Dict[str, Any]) -> Dict[str, Any] | None:
    inline_rule_table = policy.get("ruleTableJson")
    if not isinstance(inline_rule_table, dict):
        inline_rule_table = policy.get("rule_table_json")
    if isinstance(inline_rule_table, dict) and inline_rule_table:
        return inline_rule_table

    explicit_path = _safe_text(policy.get("rule_profile_path"))
    if explicit_path:
        path = Path(explicit_path)
        if path.exists():
            return _load_rule_profile(str(path.resolve()))

    title = _safe_text(policy.get("title"))
    for item in DEFAULT_RULE_PROFILES:
        if item["policy_keyword"] in title and item["rule_path"].exists():
            return _load_rule_profile(str(item["rule_path"].resolve()))
    return None


def _lookup_enterprise_value(enterprise: Dict[str, Any], field_name: str) -> Any:
    candidates = [field_name] + FIELD_ALIASES.get(field_name, [])
    index = enterprise.get("_enterprise_standard_index") or {}
    for candidate in candidates:
        if candidate in enterprise and _is_present(enterprise.get(candidate)):
            return enterprise.get(candidate)
        if candidate in index and _is_present(index[candidate].get("标准值")):
            return index[candidate].get("标准值")
    return None


def _collect_enterprise_values(enterprise: Dict[str, Any], field_names: List[str]) -> Tuple[List[str], List[Any]]:
    resolved_fields: List[str] = []
    values: List[Any] = []
    for field_name in field_names:
        value = _lookup_enterprise_value(enterprise, field_name)
        if _is_present(value):
            resolved_fields.append(field_name)
            values.append(value)
    return resolved_fields, values


def _evaluate_llm_rule(
    enterprise: Dict[str, Any],
    item: Dict[str, Any],
    group: Dict[str, Any],
    values: List[Any],
    target_value: Any,
) -> Tuple[str, float, Any, str]:
    enterprise_value = "；".join(_safe_text(value) for value in values if _safe_text(value)) or None
    try:
        llm_result = _call_rule_judge_llm(enterprise, item, group, values, target_value)
        verdict = _safe_text(llm_result.get("verdict")).lower()
        reason = _safe_text(llm_result.get("reason")) or "模型未给出原因说明。"
        evidence = _safe_text(llm_result.get("evidence"))
        message = f"LLM判定：{reason}"
        if evidence:
            message = f"{message} 证据：{evidence}"
        score = 1.0 if verdict == "pass" else 0.0
        return verdict, score, enterprise_value, message
    except Exception as exc:  # noqa: BLE001
        if RULE_LLM_STRICT:
            raise RuntimeError(f"规则判定模型调用失败：{str(exc)[:160]}") from exc
        failure_message = f"LLM判定失败：{str(exc)[:160]}"
        return "not_evaluable", 0.0, enterprise_value, failure_message


def _first_numeric_value(values: List[Any]) -> float | None:
    for value in values:
        numeric_value = _safe_float(value)
        if numeric_value is not None:
            return numeric_value
    return None


def _evaluate_numeric_rule(
    values: List[Any], rule: Dict[str, Any], *, hard_rule: bool = False,
) -> Tuple[str, float, str]:
    """按10/30/100口径计算数值项，硬性规则明确不满足时返回失败。"""
    operator = _safe_text(rule.get("operator")).lower()
    target_value = _safe_float(rule.get("targetValue"))
    current_value = _first_numeric_value(values)

    if operator == "has_value":
        if any(_is_present(value) for value in values):
            return "pass", 1.0, "企业字段有值，按有无型规则记 100 分。"
        return "pass", DEFAULT_MISSING_SCORE, "企业接口未提供该字段，按缺失兜底口径记 10 分。"

    if current_value is None and target_value is not None and target_value >= 0:
        return (
            "pass",
            DEFAULT_MISSING_SCORE,
            "企业接口未提供该数值字段，企业真实值保持缺失；按缺失兜底口径记 10 分。",
        )
    if current_value is None or target_value is None or target_value < 0:
        return "not_evaluable", 0.0, "缺少可计算的企业值或目标值，当前无法评价。"

    if operator == "lte":
        if current_value > target_value:
            score = 0.0 if hard_rule else PRESENT_DATA_BASE_SCORE
            status = "fail" if hard_rule else "pass"
            return status, score, f"上限条件未满足：企业值={current_value}，超过上限={target_value}，记 {_score_to_percentage(score)} 分。"
        return "pass", 1.0, f"上限条件满足：企业值={current_value}，不超过上限={target_value}。"

    if operator == "lt":
        if current_value >= target_value:
            score = 0.0 if hard_rule else PRESENT_DATA_BASE_SCORE
            status = "fail" if hard_rule else "pass"
            return status, score, f"严格上限条件未满足：企业值={current_value}，不小于上限={target_value}，记 {_score_to_percentage(score)} 分。"
        return "pass", 1.0, f"严格上限条件满足：企业值={current_value}，小于上限={target_value}。"

    if operator == "equals":
        matched = abs(current_value - target_value) < 1e-9
        score = 1.0 if matched else 0.0 if hard_rule else PRESENT_DATA_BASE_SCORE
        return ("pass" if matched or not hard_rule else "fail"), score, (
            f"等值条件：企业值={current_value}，目标值={target_value}，得分={_score_to_percentage(score)}分。"
        )

    if target_value == 0:
        matched = current_value > 0 if operator == "gt" else current_value >= 0
        score = 1.0 if matched else 0.0 if hard_rule else PRESENT_DATA_BASE_SCORE
        return ("pass" if matched or not hard_rule else "fail"), score, (
            f"零基线成长指标：企业值={current_value}，目标值=0，"
            f"当前达标度={_score_to_percentage(score)}分。"
        )
    matched = current_value > target_value if operator == "gt" else current_value >= target_value
    if hard_rule:
        score = 1.0 if matched else 0.0
        status = "pass" if matched else "fail"
    else:
        score = min(max(PRESENT_DATA_BASE_SCORE + current_value / target_value, PRESENT_DATA_BASE_SCORE), 1.0)
        status = "pass"
    return status, round(score, 4), (
        f"下限型成长指标：企业值={current_value}，目标值={target_value}，"
        f"得分={_score_to_percentage(score)}分。"
    )


def _evaluate_item(
    enterprise: Dict[str, Any],
    item: Dict[str, Any],
    group: Dict[str, Any],
    *,
    allow_default_qualitative: bool = True,
    semantic_fact: Dict[str, Any] | None = None,
    hard_rule: bool = False,
) -> Dict[str, Any]:
    """按模型事实快照或本地字段执行当前初筛与成长性评分。"""
    status = _safe_text(item.get("itemStatus"))
    field_names = list(item.get("enterpriseFields", []) or [])
    resolved_fields, values = _collect_enterprise_values(enterprise, field_names)
    rule = item.get("matchRule") or {}
    rule_kind = _safe_text(rule.get("ruleKind"))
    field_type = _safe_text(rule.get("fieldType")).lower()
    enterprise_value = "；".join(_safe_text(value) for value in values if _safe_text(value)) or None
    fact = _reconcile_direct_money_fact(
        enterprise, item, semantic_fact if isinstance(semantic_fact, dict) else None,
    )
    fact_status = _safe_text(fact.get("status")).lower() if fact else ""
    branch_applicability = _safe_text(fact.get("branchApplicability")).lower() if fact else ""
    if branch_applicability == "not_applicable":
        return {
            "status": "skipped",
            "score": 0.0,
            "enterprise_value": None,
            "message": _safe_text(fact.get("branchReason")) or "当前企业事实不适用该条件分支。",
            "resolved_fields": [],
            "resolved_values": [],
            "rule_kind": _safe_text((item.get("matchRule") or {}).get("ruleKind")),
            "pass_source": "branch_not_applicable",
            "score_type": "quantitative" if _safe_text((item.get("matchRule") or {}).get("fieldType")).lower() == "number" else "qualitative",
            "semantic_fact": fact,
        }
    has_local_numeric_value = bool(values) and (
        field_type == "number" or _safe_text(rule.get("operator")).lower() == "has_value"
    )
    if branch_applicability == "unknown" and not has_local_numeric_value:
        return {
            "status": "pass",
            "score": DEFAULT_MISSING_SCORE,
            "enterprise_value": None,
            "message": (
                f"{_safe_text(fact.get('branchReason')) or '缺少判断当前条件分支是否适用的企业字段。'}"
                " 按缺失兜底口径记 10 分。"
            ),
            "resolved_fields": [],
            "resolved_values": [],
            "rule_kind": _safe_text((item.get("matchRule") or {}).get("ruleKind")),
            "pass_source": "default_missing_assumed",
            "score_type": "quantitative" if _safe_text((item.get("matchRule") or {}).get("fieldType")).lower() == "number" else "qualitative",
            "semantic_fact": fact,
        }
    fact_value = fact.get("normalizedValue") if fact else None
    fact_display_value = _safe_text(fact.get("displayValue")) if fact else ""
    fact_fields = list(fact.get("mappedFields", []) or []) if fact else []
    if fact and fact_fields:
        resolved_fields = fact_fields
    if fact and (fact_display_value or fact_value is not None):
        enterprise_value = fact_display_value or fact_value

    if field_type == "number" or _safe_text(rule.get("operator")).lower() == "has_value":
        numeric_values = [fact_value] if fact and fact_status != "missing" and fact_value is not None else values
        fact_unit = _normalize_money_unit(fact.get("normalizedUnit")) if fact else ""
        rule_unit = _normalize_money_unit(rule.get("unit"))
        converted_fact_value = _convert_money_value(fact_value, fact_unit, rule_unit) if fact_unit and rule_unit else None
        if converted_fact_value is not None:
            numeric_values = [converted_fact_value]
            enterprise_value = f"{_format_numeric_value(converted_fact_value)} {rule_unit}"
        elif rule_unit and values:
            converted_local_values = _convert_local_money_values_for_rule(
                enterprise,
                resolved_fields,
                values,
                rule_unit,
            )
            if converted_local_values:
                original_numeric_values = [_safe_float(value) for value in numeric_values]
                numeric_values = converted_local_values
                conversion_changed_value = any(
                    original is not None and abs(original - converted) > 1e-12
                    for original, converted in zip(original_numeric_values, converted_local_values)
                )
                if conversion_changed_value:
                    enterprise_value = f"{_format_numeric_value(converted_local_values[0])} {rule_unit}"
        elif _safe_text(rule.get("unit")) == "%":
            source_paths = _semantic_fact_paths(fact) if fact else resolved_fields
            source_values = []
            for path in source_paths:
                found, source_value, _ = _resolve_candidate_path(enterprise, path)
                if found and _safe_float(source_value) is not None:
                    source_values.append(source_value)
            if source_values:
                numeric_values = source_values
            source_unit = fact.get("normalizedUnit") if fact else ""
            normalized_percentages = [
                _normalize_percentage_for_rule(
                    value,
                    source_unit,
                    source_paths,
                    _safe_text(enterprise.get("_source_type")),
                )
                for value in numeric_values
            ]
            numeric_values = [value for value in normalized_percentages if value is not None]
            if numeric_values:
                enterprise_value = f"{_format_numeric_value(numeric_values[0])} %"
        result_status, score, message = _evaluate_numeric_rule(numeric_values, rule, hard_rule=hard_rule)
        default_assumed = result_status == "pass" and score == DEFAULT_MISSING_SCORE and not numeric_values
        if fact:
            if fact_status == "missing":
                message = f"模型未找到可确认的数值字段：{_safe_text(fact.get('reason')) or '无相关证据'}。{message}"
            else:
                message = f"模型语义映射后按本地数值规则计算：{message}"
        return {
            "status": result_status,
            "score": score,
            "enterprise_value": enterprise_value,
            "message": message,
            "resolved_fields": resolved_fields,
            "resolved_values": values,
            "rule_kind": rule_kind,
            "pass_source": (
                "default_missing_assumed"
                if default_assumed
                else "semantic_quantitative_rule" if fact else "quantitative_rule"
            ),
            "score_type": "presence" if _safe_text(rule.get("operator")).lower() == "has_value" else "quantitative",
            "default_assumed": default_assumed,
            "semantic_fact": fact,
        }

    if fact_status == "contradicted":
        return {
            "status": "fail" if hard_rule else "pass",
            "score": 0.0 if hard_rule else PRESENT_DATA_BASE_SCORE,
            "enterprise_value": enterprise_value,
            "message": f"模型发现明确相反证据：{_safe_text(fact.get('reason')) or '企业事实与政策定性要求相反'}。",
            "resolved_fields": resolved_fields,
            "resolved_values": values,
            "rule_kind": rule_kind,
            "pass_source": "semantic_contradicted",
            "score_type": "qualitative",
            "semantic_fact": fact,
        }

    if fact_status == "supported":
        return {
            "status": "pass",
            "score": 1.0,
            "enterprise_value": enterprise_value,
            "message": f"模型语义映射已找到支持证据：{_safe_text(fact.get('reason')) or '符合政策定性要求'}。",
            "resolved_fields": resolved_fields,
            "resolved_values": values,
            "rule_kind": rule_kind,
            "pass_source": "semantic_evidence",
            "score_type": "qualitative",
            "semantic_fact": fact,
        }

    if allow_default_qualitative:
        return {
            "status": "pass",
            "score": DEFAULT_MISSING_SCORE,
            # 规则表提供的候选字段仅用于模型检索，不能在模型判为 missing 时
            # 伪装成该政策条件的企业事实。例如“企业资质=是”不能证明施工资质。
            "enterprise_value": None,
            "message": "模型未找到可核验的定性证据，按缺失兜底口径记 10 分。",
            "resolved_fields": [],
            "resolved_values": [],
            "rule_kind": rule_kind,
            "pass_source": "default_missing_assumed",
            "score_type": "qualitative",
            "semantic_fact": fact,
        }

    return {
        "status": "pass",
        "score": DEFAULT_MISSING_SCORE,
        "enterprise_value": None,
        "message": "定性条件未找到可核验的企业证据，按缺失兜底口径记 10 分。",
        "resolved_fields": [],
        "resolved_values": [],
        "rule_kind": rule_kind,
        "pass_source": "default_missing_assumed",
        "score_type": "qualitative",
        "semantic_fact": fact,
    }


def _soft_score_type(item: Dict[str, Any], rule: Dict[str, Any]) -> str:
    operator = _safe_text(rule.get("operator")).lower()
    field_type = _safe_text(rule.get("fieldType")).lower()
    target_value = rule.get("targetValue")
    target_text = _safe_text(target_value)
    item_status = _safe_text(item.get("itemStatus"))

    if field_type == "number" and _safe_float(target_value) is not None:
        return "quantitative"
    if operator == "has_value":
        return "presence"
    if item_status in {"reference", "supporting"} and target_text in {"", "是", "符合", "有"}:
        return "presence"
    return "qualitative"


def _hard_item_score_note(item_status: str, eval_status: str) -> str:
    if item_status == "rule":
        return "硬性正式判断项：命中记 100 分，未命中或待确认记 0 分。"
    if item_status == "unresolved":
        return "硬性补判项：模型结合企业摘要明确判通过记 100 分，否则记 0 分。"
    if item_status == "supporting":
        if eval_status == "supporting_hit":
            return "硬性辅助项：辅助命中记 45 分。"
        return "硬性辅助项：未形成有效辅助证据记 0 分。"
    if item_status == "reference":
        if eval_status == "reference_hit":
            return "硬性参考项：参考命中记 55 分。"
        return "硬性参考项：未形成有效参考信号记 0 分。"
    return "未命中或无法判定时记 0 分。"


# 缺失条件按10分参与类别内等权平均，但不能算作已核验证据。
ASSUMED_PASS_SOURCES = {"default_missing_assumed", "tech_sme_evaluation_partial_assumed"}
EVIDENCE_BACKED_WEIGHT = 1.0
DEFAULT_ASSUMED_WEIGHT = 1.0
NON_SCORING_EVAL_STATUS = {"not_evaluable", "skipped"}
# 推荐阈值保持60分；缺失条件为10分，因此全缺失政策不会进入推荐。
RECOMMENDATION_SCORE_THRESHOLD = 0.6
# 科技型中小企业评价三个维度的满分（20/50/30）。
TECH_SME_PERSONNEL_MAX = 20
TECH_SME_RD_MAX = 50
TECH_SME_ACHIEVEMENT_MAX = 30
# 维度数据缺失时按该维度满分的10%补足，与条件级缺失10分口径一致。
TECH_SME_MISSING_DIMENSION_RATIO = DEFAULT_MISSING_SCORE


def _is_evidence_backed(pass_source: Any) -> bool:
    """判断该条件是否有企业真实证据支撑（区别于缺失默认放行）。"""
    return _safe_text(pass_source) not in ASSUMED_PASS_SOURCES


def _score_weight(evaluated: Dict[str, Any]) -> float:
    return EVIDENCE_BACKED_WEIGHT if _is_evidence_backed(evaluated.get("pass_source")) else DEFAULT_ASSUMED_WEIGHT


def _weighted_mean(pairs: List[Tuple[float, float]]) -> float | None:
    """按证据权重求均值；无有效权重时返回 None，交由上层按"该类别不存在"处理。"""
    total_weight = sum(weight for _, weight in pairs)
    if total_weight <= 0:
        return None
    return round(sum(score * weight for score, weight in pairs) / total_weight, 4)


def _confidence_level(evidence_ratio: float) -> str:
    if evidence_ratio >= 0.6:
        return "high"
    if evidence_ratio >= 0.3:
        return "medium"
    return "low"


def _band_score(value: Any, bands: List[Tuple[float, int]]) -> int | None:
    numeric = _safe_float(value)
    if numeric is None:
        return None
    for threshold, score in bands:
        if numeric >= threshold:
            return score
    return 0


def _percentage_to_fraction(value: Any) -> float | None:
    """标准企业比例字段使用百分数，专项分档使用0-1比例小数。"""
    numeric = _safe_float(value)
    if numeric is None:
        return None
    return numeric / 100 if abs(numeric) > 1 else numeric


def _calculate_tech_sme_evaluation(
    assessment: Dict[str, Any] | None,
    enterprise: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """模型仅提取指标值；本地按科技型中小企业评价分档计算，避免模型直接决定分数。"""
    metrics = (assessment or {}).get("techSmeEvaluation", {}) if isinstance(assessment, dict) else {}
    def metric(name: str) -> Any:
        api_field_map = {
            "techPersonnelRatio": "科技人员占企业职工总数比例",
            "rdExpenseRevenueRatio": "研发费用占销售收入总额比例",
            "rdExpenseCostRatio": "研发费用占成本费用支出总额比例",
            "classICount": "I类知识产权数量",
            "classIICount": "II类知识产权数量",
        }
        if isinstance(enterprise, dict) and enterprise.get("_source_type") == "policy_recommendation_api_record":
            return enterprise.get(api_field_map[name])
        record = metrics.get(name, {}) if isinstance(metrics, dict) else {}
        return record.get("value") if isinstance(record, dict) else None

    personnel_ratio = _percentage_to_fraction(metric("techPersonnelRatio"))
    revenue_ratio = _percentage_to_fraction(metric("rdExpenseRevenueRatio"))
    cost_ratio = _percentage_to_fraction(metric("rdExpenseCostRatio"))
    class_i = metric("classICount")
    class_ii = metric("classIICount")
    personnel_score = _band_score(personnel_ratio, [(0.30, 20), (0.25, 16), (0.20, 12), (0.15, 8), (0.10, 4)])
    revenue_score = _band_score(revenue_ratio, [(0.06, 50), (0.05, 40), (0.04, 30), (0.03, 20), (0.02, 10)])
    cost_score = _band_score(cost_ratio, [(0.30, 50), (0.25, 40), (0.20, 30), (0.15, 20), (0.10, 10)])
    rd_candidates = [("研发费用/销售收入", revenue_score), ("研发费用/成本费用", cost_score)]
    available_rd = [(name, score) for name, score in rd_candidates if score is not None]
    rd_path, rd_score = max(available_rd, key=lambda item: item[1]) if available_rd else ("", None)
    class_i_count = _safe_float(class_i)
    class_ii_count = _safe_float(class_ii)
    achievement_score: int | None
    if class_i_count is None and class_ii_count is None:
        achievement_score = None
    elif (class_i_count or 0) >= 1:
        achievement_score = 30
    elif (class_ii_count or 0) >= 4:
        achievement_score = 24
    elif (class_ii_count or 0) >= 3:
        achievement_score = 18
    elif (class_ii_count or 0) >= 2:
        achievement_score = 12
    elif (class_ii_count or 0) >= 1:
        achievement_score = 6
    else:
        achievement_score = 0

    dimension_scores = [
        ("科技人员", personnel_score, TECH_SME_PERSONNEL_MAX),
        ("研发投入", rd_score, TECH_SME_RD_MAX),
        ("科技成果", achievement_score, TECH_SME_ACHIEVEMENT_MAX),
    ]
    verified_dimensions = [(name, score, cap) for name, score, cap in dimension_scores if score is not None]
    missing_dimensions = [(name, cap) for name, score, cap in dimension_scores if score is None]

    # 三项全缺失时无任何可核验指标，保持 None 走条件级缺失10分。
    # 部分缺失维度按其满分的10%补足，并保留假定来源标记。
    verified_total = sum(score for _, score, _ in verified_dimensions) if verified_dimensions else None
    if not verified_dimensions:
        total = None
        assumed_total = None
    elif missing_dimensions:
        assumed_total = sum(cap * TECH_SME_MISSING_DIMENSION_RATIO for _, cap in missing_dimensions)
        total = int(round(verified_total + assumed_total))
    else:
        assumed_total = 0
        total = int(verified_total)

    missing_names = [name for name, _ in missing_dimensions]
    if total is None:
        conclusion = "未核验"
    elif missing_dimensions:
        conclusion = (
            "%s（%s维度数据缺失，按召回优先口径以该维度满分的%d%%补足）"
            % (
                "达到60分" if total >= 60 else "未达到60分，按成长性低分处理",
                "、".join(missing_names),
                int(TECH_SME_MISSING_DIMENSION_RATIO * 100),
            )
        )
    else:
        conclusion = "达到60分" if total >= 60 else "未达到60分，按成长性低分处理"

    return {
        "科技人员占比": personnel_ratio,
        "科技人员指标得分": personnel_score,
        "研发费用占销售收入比例": revenue_ratio,
        "研发费用占成本费用比例": cost_ratio,
        "研发投入计分口径": rd_path or "未核验",
        "研发投入指标得分": rd_score,
        "I类知识产权数量": class_i,
        "II类知识产权数量": class_ii,
        "科技成果指标得分": achievement_score,
        "评价总分": total,
        "已核验维度得分": verified_total,
        "缺失维度": missing_names,
        "缺失维度补足分": assumed_total,
        "总分是否达到60分": total >= 60 if total is not None else None,
        "科技人员指标是否为0分": personnel_score == 0 if personnel_score is not None else None,
        "评价结论": conclusion,
    }


def _apply_tech_sme_evaluation_item_score(
    item: Dict[str, Any],
    evaluated: Dict[str, Any],
    evaluation: Dict[str, Any],
) -> Dict[str, Any]:
    """把 20/50/30 评价总分作为政策中“评价指标”条件的真实达标度。"""
    item_name = _safe_text(item.get("itemName"))
    if "中小企业评价指标" not in item_name:
        return evaluated

    total_score = evaluation.get("评价总分")
    if total_score is None:
        return evaluated
    normalized_score = max(0.0, min(float(total_score) / 100.0, 1.0))
    missing_dimensions = list(evaluation.get("缺失维度") or [])
    # 含补足分的总分仍属"部分缺失"，pass_source 要能与全核验区分，
    # 否则证据覆盖率会把补足出来的分数当成真实证据统计。
    pass_source = (
        "tech_sme_evaluation_partial_assumed" if missing_dimensions else "tech_sme_evaluation_total_score"
    )
    missing_note = (
        (
            f"其中{('、'.join(missing_dimensions))}维度数据缺失，"
            f"按该维度满分的{int(TECH_SME_MISSING_DIMENSION_RATIO * 100)}%补足"
            f"（已核验={evaluation.get('已核验维度得分')}分，补足={evaluation.get('缺失维度补足分')}分）；"
        )
        if missing_dimensions
        else ""
    )
    evaluated.update(
        {
            "status": "pass",
            "score": normalized_score,
            "enterprise_value": f"评价总分{total_score}分",
            "message": (
                f"科技型中小企业评价总分={total_score}分（科技人员={evaluation.get('科技人员指标得分')}分，"
                f"研发投入={evaluation.get('研发投入指标得分')}分，科技成果={evaluation.get('科技成果指标得分')}分）；"
                f"{missing_note}"
                f"该条件按评价总分/100计入政策评分，当前得分={_score_to_percentage(normalized_score)}分，"
                "不使用通用下限指标的30分基础加比例公式。"
            ),
            "pass_source": pass_source,
            "score_type": "quantitative",
        }
    )
    return evaluated


def _tech_sme_evaluation_fact(assessment: Dict[str, Any] | None, evaluation: Dict[str, Any]) -> Dict[str, Any] | None:
    """构造评价指标的本地事实，不再沿用企业原始“总得分”字段。"""
    if evaluation.get("评价总分") is None:
        return None
    metrics = (assessment or {}).get("techSmeEvaluation", {}) if isinstance(assessment, dict) else {}
    metric_specs = [
        ("techPersonnelRatio", "科技人员占企业职工总数比例"),
        ("rdExpenseRevenueRatio", "研发费用占销售收入总额比例"),
        ("rdExpenseCostRatio", "研发费用占成本费用支出总额比例"),
        ("classICount", "I类知识产权数量"),
        ("classIICount", "II类知识产权数量"),
    ]
    fields: List[str] = []
    evidence: List[Dict[str, str]] = []
    for metric_name, _label in metric_specs:
        metric = metrics.get(metric_name, {}) if isinstance(metrics, dict) else {}
        for entry in metric.get("evidence", []) if isinstance(metric, dict) else []:
            if not isinstance(entry, dict):
                continue
            path = _safe_text(entry.get("sourcePath"))
            text = _safe_text(entry.get("sourceText"))
            if path and path not in fields:
                fields.append(path)
            if path:
                evidence.append({"sourcePath": path, "sourceText": text})
    return {
        "status": "supported",
        "mappingMethod": "composite",
        "localDerived": True,
        "mappedFields": fields,
        "normalizedValue": evaluation["评价总分"],
        "normalizedUnit": "分",
        "displayValue": f"本地评价总分{evaluation['评价总分']}分",
        "confidence": 1.0,
        "evidence": evidence[:10],
        "reason": (
            "本地按科技人员、研发投入、科技成果的分档规则计算："
            f"科技人员={evaluation.get('科技人员指标得分')}分，研发投入={evaluation.get('研发投入指标得分')}分，"
            f"科技成果={evaluation.get('科技成果指标得分')}分，总分={evaluation['评价总分']}分。"
        ),
    }


def _apply_tech_sme_evaluation_detail(detail: Dict[str, Any], evaluation: Dict[str, Any]) -> None:
    """让明细页展示真实评价输入，避免误导为直接采用企业“总得分”。"""
    if "中小企业评价指标" not in _safe_text(detail.get("条件名称")):
        return
    detail["来源字段"] = "科技人员占企业职工总数比例；研发费用占销售收入总额比例；研发费用占成本费用支出总额比例；I类知识产权数量；II类知识产权数量"
    detail["政策要求"] = "按科技人员、研发投入、科技成果三项分档计算，评价总分不低于60分"
    detail["目标值"] = 60
    detail["单位"] = "分"
    detail["企业值"] = (
        f"科技人员占比={evaluation.get('科技人员占比')}（{evaluation.get('科技人员指标得分')}分）；"
        f"研发投入={evaluation.get('研发投入计分口径')}（{evaluation.get('研发投入指标得分')}分）；"
        f"I类={evaluation.get('I类知识产权数量')}项，II类={evaluation.get('II类知识产权数量')}项"
        f"（科技成果{evaluation.get('科技成果指标得分')}分）；评价总分={evaluation.get('评价总分')}分"
    )


def _group_summary(group: Dict[str, Any], item_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    rule_items = [item for item in item_results if item.get("itemStatus") == "rule"]
    rule_pass = [item for item in rule_items if item.get("evalStatus") == "pass"]
    rule_fail = [item for item in rule_items if item.get("evalStatus") == "fail"]
    rule_pending = [item for item in rule_items if item.get("evalStatus") == "not_evaluable"]
    reference_hits = [item for item in item_results if item.get("evalStatus") == "reference_hit"]
    supporting_hits = [item for item in item_results if item.get("evalStatus") == "supporting_hit"]
    unresolved = [item for item in item_results if item.get("itemStatus") == "unresolved"]
    operator = _safe_text(group.get("conditionOperator") or "AND")

    passed_items = [
        item for item in item_results
        if item.get("evalStatus") in {"pass", "reference_hit", "supporting_hit"}
    ]
    failed_items = [item for item in item_results if item.get("evalStatus") == "fail"]
    active_items = [item for item in item_results if item.get("evalStatus") != "skipped"]
    pending_items = [item for item in active_items if item.get("evalStatus") == "not_evaluable"]

    if operator == "ANY":
        if passed_items:
            group_status = "pass"
        elif failed_items:
            group_status = "fail"
        else:
            group_status = "not_evaluable"
    else:
        if failed_items:
            group_status = "fail"
        elif active_items and not pending_items:
            group_status = "pass"
        else:
            group_status = "not_evaluable"

    return {
        "conditionNo": group.get("conditionNo"),
        "groupStatus": group_status,
        "rulePassCount": len(rule_pass),
        "ruleFailCount": len(rule_fail),
        "rulePendingCount": len(rule_pending),
        "referenceHitCount": len(reference_hits),
        "supportingHitCount": len(supporting_hits),
        "unresolvedCount": len(unresolved),
    }


def _hard_group_gate_status(group_summary: Dict[str, Any], item_results: List[Dict[str, Any]]) -> str:
    if group_summary.get("groupStatus") == "fail":
        return "fail"
    return "pass" if group_summary.get("groupStatus") == "pass" else "not_evaluable"


def score_policy_against_rule_profile(
    enterprise: Dict[str, Any],
    policy: Dict[str, Any],
    profile: Dict[str, Any],
    semantic_assessment: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    policy_identifier = _safe_text(
        policy.get("fusionPolicyId") or policy.get("policyId") or policy.get("id")
        or profile.get("fusionPolicyId") or profile.get("policyId") or policy.get("title") or profile.get("name")
        or "unknown-policy"
    )
    # 用对象身份保存位置键，允许同一政策中原始 itemId 重复而不串用模型事实。
    semantic_key_by_item_identity: Dict[int, str] = {}
    for group_type in ("hardRuleGroups", "softRuleGroups", "specialPassRuleGroups"):
        for group_ordinal, source_group in enumerate(profile.get(group_type, []) or [], start=1):
            for item_ordinal, source_item in enumerate(source_group.get("conditionFieldItems", []) or [], start=1):
                semantic_key_by_item_identity[id(source_item)] = semantic_item_key(
                    policy_identifier, group_type, group_ordinal, item_ordinal,
                )

    def semantic_key_for(item: Dict[str, Any]) -> str:
        return semantic_key_by_item_identity.get(id(item), "")

    def _is_special_pass_group(group: Dict[str, Any]) -> bool:
        evaluation_role = _safe_text(group.get("evaluationRole")).lower()
        if evaluation_role == "special_pass":
            return True
        raw_text = _safe_text(group.get("rawText"))
        special_markers = [
            "直通",
            "直通车",
            "可直接确认",
            "可直接认定",
            "可直接通过",
            "不受综合评价所得分值高低限制",
            "不受综合评价得分高低限制",
            "不受综合评价分值高低限制",
        ]
        return any(marker in raw_text for marker in special_markers)

    hard_groups = list(profile.get("hardRuleGroups", []) or [])
    soft_groups = list(profile.get("softRuleGroups", []) or [])
    special_pass_groups = list(profile.get("specialPassRuleGroups", []) or [])

    normalized_hard_groups: List[Dict[str, Any]] = []
    normalized_soft_groups: List[Dict[str, Any]] = []
    normalized_special_groups: List[Dict[str, Any]] = []

    for group in hard_groups:
        if _is_special_pass_group(group):
            group["evaluationRole"] = "special_pass"
            normalized_special_groups.append(group)
        else:
            normalized_hard_groups.append(group)

    for group in soft_groups:
        if _is_special_pass_group(group):
            group["evaluationRole"] = "special_pass"
            normalized_special_groups.append(group)
        else:
            if not _safe_text(group.get("evaluationRole")):
                group["evaluationRole"] = "soft_support"
            normalized_soft_groups.append(group)

    for group in special_pass_groups:
        group["evaluationRole"] = "special_pass"
        normalized_special_groups.append(group)

    hard_groups = normalized_hard_groups
    soft_groups = normalized_soft_groups
    special_pass_groups = normalized_special_groups

    condition_details: List[Dict[str, Any]] = []
    group_details: List[Dict[str, Any]] = []
    hard_fail_items: List[str] = []
    hard_review_items: List[str] = []
    soft_quantitative_scores: List[float] = []
    soft_presence_scores: List[float] = []
    soft_qualitative_base_scores: List[float] = []
    qualitative_coeffs: List[float] = []
    qualitative_similarities: List[float] = []
    # 总分按所有参与条件等权；A/B仅保留为运行时证据质量审计分层。
    all_condition_scores: List[Tuple[float, float]] = []
    verified_scores: List[Tuple[float, float]] = []
    supporting_scores: List[Tuple[float, float]] = []
    unverified_scores: List[Tuple[float, float]] = []
    scored_item_count = 0
    evidence_item_count = 0
    unresolved_item_count = 0
    llm_judged_item_count = 0
    special_pass_hit = False
    semantic_facts = (semantic_assessment or {}).get("facts", {}) if isinstance(semantic_assessment, dict) else {}
    tech_sme_evaluation = _calculate_tech_sme_evaluation(semantic_assessment, enterprise)

    def semantic_fact_for(item: Dict[str, Any]) -> Dict[str, Any] | None:
        item_name = _safe_text(item.get("itemName"))
        if "中小企业评价指标" in item_name and tech_sme_evaluation.get("评价总分") is not None:
            return _tech_sme_evaluation_fact(semantic_assessment, tech_sme_evaluation)
        if "科技人员指标得分" in item_name and tech_sme_evaluation.get("科技人员指标得分") is not None:
            return {
                "status": "supported", "mappingMethod": "composite", "mappedFields": [],
                "localDerived": True,
                "normalizedValue": tech_sme_evaluation["科技人员指标得分"], "normalizedUnit": "分",
                "displayValue": f"科技人员指标得分{tech_sme_evaluation['科技人员指标得分']}分",
                "confidence": 1.0, "evidence": [],
                "reason": f"本地按科技人员占比 {tech_sme_evaluation.get('科技人员占比')} 分档计算。",
            }
        fact = semantic_facts.get(semantic_key_for(item)) if isinstance(semantic_facts, dict) else None
        # 兼容历史归档的 itemId 键；新调用永远优先位置唯一的 semanticItemKey。
        if not isinstance(fact, dict) and isinstance(semantic_facts, dict):
            fact = semantic_facts.get(_safe_text(item.get("itemId")))
        return _reconcile_direct_money_fact(enterprise, item, fact if isinstance(fact, dict) else None)

    def apply_semantic_trace(detail: Dict[str, Any], item: Dict[str, Any]) -> None:
        fact = semantic_fact_for(item)
        detail["匹配方式"] = _safe_text(fact.get("mappingMethod")) if fact else "本地字段规则"
        detail["模型事实状态"] = _safe_text(fact.get("status")) if fact else ""
        detail["条件分支适用性"] = _safe_text(fact.get("branchApplicability")) if fact else ""
        detail["条件分支判定依据"] = _safe_text(fact.get("branchReason")) if fact else ""
        detail["模型置信度"] = fact.get("confidence") if fact else None
        detail["证据路径"] = [entry.get("sourcePath", "") for entry in fact.get("evidence", [])] if fact else []
        detail["证据文本"] = [entry.get("sourceText", "") for entry in fact.get("evidence", [])] if fact else []
        detail["模型判定依据"] = _safe_text(fact.get("reason")) if fact else ""

    for group in hard_groups:
        item_results: List[Dict[str, Any]] = []
        for item in group.get("conditionFieldItems", []) or []:
            llm_judged_item_count += 1
            rule = item.get("matchRule") or {}
            requires_qualitative_evidence = (
                _safe_text(item.get("itemStatus")).lower() == "unresolved"
                or _safe_text(rule.get("fieldType")).lower() != "number"
                or _safe_text(rule.get("operator")).lower() == "llm_judge"
            )
            evaluated = _evaluate_item(
                enterprise,
                item,
                group,
                allow_default_qualitative=not requires_qualitative_evidence,
                semantic_fact=semantic_fact_for(item),
                hard_rule=True,
            )
            evaluated = _apply_tech_sme_evaluation_item_score(item, evaluated, tech_sme_evaluation)
            if "科技人员指标得分" in _safe_text(item.get("itemName")) and tech_sme_evaluation.get("科技人员指标得分") == 0:
                evaluated.update({
                    "status": "fail", "score": 0.0,
                    "message": "科技人员指标按分档计算为 0 分，属于明确不通过的硬性条件。",
                    "pass_source": "tech_sme_personnel_zero",
                })
            item_status = _safe_text(item.get("itemStatus"))
            eval_status = _safe_text(evaluated.get("status"))
            if item_status == "unresolved":
                unresolved_item_count += 1

            contributes_to_score = (
                eval_status not in {"not_evaluable", "skipped"}
                or bool(evaluated.get("provisional_score"))
            )
            evidence_tier = classify_runtime_evidence(evaluated)
            coeff, coeff_note = 1.0, ""

            if contributes_to_score:
                item_weight = _score_weight(evaluated)
                score_pair = (float(evaluated.get("score", 0.0)), item_weight)
                all_condition_scores.append(score_pair)
                scored_item_count += 1
                if evidence_tier.evidence_backed:
                    evidence_item_count += 1
                if evidence_tier.bucket == "verified":
                    verified_scores.append(score_pair)
                elif evidence_tier.bucket == "supporting":
                    supporting_scores.append(score_pair)
                else:
                    unverified_scores.append(score_pair)

            detail = {
                "条件名称": _safe_text(item.get("itemName")),
                "条件组原文": _safe_text(group.get("rawText")),
                "条件组操作符": _safe_text(group.get("conditionOperator") or "AND"),
                "条件上下文": _safe_text(group.get("conditionContext")),
                "条件分支说明": _safe_text(item.get("conditionBranchText")) or _safe_text(group.get("conditionBranchText")),
                "政策原文": _safe_text(item.get("policyFragment")) or _safe_text(group.get("rawText")),
                "政策要求": _safe_text(rule.get("targetValue")) or _safe_text(group.get("rawText")),
                "来源字段": "；".join(item.get("enterpriseFields", []) or []),
                "评分组别": "A",
                "规则类型": item_status,
                "计分归类": evidence_tier.bucket,
                "evidenceTier": evidence_tier.tier,
                "bucketReason": evidence_tier.reason,
                "规则表原始ruleKind": _safe_text(evaluated.get("rule_kind")),
                "企业字段": "；".join(evaluated.get("resolved_fields", []) or item.get("enterpriseFields", []) or []),
                "企业值": evaluated.get("enterprise_value"),
                "目标值": rule.get("targetValue"),
                "单位": _safe_text(rule.get("unit")),
                "企业字段状态": item_status,
                "匹配度": _match_degree(eval_status),
                "得分": round(float(evaluated.get("score", 0.0)), 4),
                "得分说明": evaluated.get("message"),
                "说明": " ".join(part for part in [evaluated.get("message", ""), coeff_note] if part),
                "conditionNo": group.get("conditionNo"),
                "itemId": _safe_text(item.get("itemId")),
                "evalStatus": eval_status,
                "是否参与计分": contributes_to_score,
                "passSource": _safe_text(evaluated.get("pass_source")),
                "scoreType": _safe_text(evaluated.get("score_type")),
            }
            apply_semantic_trace(detail, item)
            _apply_tech_sme_evaluation_detail(detail, tech_sme_evaluation)
            condition_details.append(detail)
            item_results.append(
                {
                    "itemId": detail["itemId"],
                    "itemName": detail["条件名称"],
                    "itemStatus": item_status,
                    "evalStatus": eval_status,
                    "score": detail["得分"],
                    "scoreNote": detail["得分说明"],
                    "matchDegree": detail["匹配度"],
                    "message": detail["说明"],
                    "passSource": detail["passSource"],
                }
            )

        group_summary = _group_summary(group, item_results)
        group_summary["groupStatus"] = _hard_group_gate_status(group_summary, item_results)
        group_details.append(
            {
                "conditionNo": group.get("conditionNo"),
                "groupType": "硬性",
                "rawText": _safe_text(group.get("rawText")),
                "conditionOperator": _safe_text(group.get("conditionOperator")),
                "groupStatus": group_summary["groupStatus"],
                "items": item_results,
                "unresolvedFragments": list(group.get("unresolvedFragments", []) or []),
                "evaluationRole": _safe_text(group.get("evaluationRole")),
                "specialPassEffect": _safe_text(group.get("specialPassEffect")),
            }
        )

        if group_summary["groupStatus"] == "fail":
            hard_fail_items.append(f"条件组{group.get('conditionNo')}未通过：{_safe_text(group.get('rawText'))}")
        elif group_summary["groupStatus"] in {"not_evaluable", "reference_only"}:
            hard_review_items.append(f"条件组{group.get('conditionNo')}未完全确认：{_safe_text(group.get('rawText'))}")

    hard_indicator_group_count = len(hard_groups)
    hard_indicator_limit_exceeded = hard_indicator_group_count > 3
    hard_pass = not hard_fail_items and not hard_review_items

    if hard_pass:
        for group in special_pass_groups:
            item_results = []
            for item in group.get("conditionFieldItems", []) or []:
                llm_judged_item_count += 1
                evaluated = _evaluate_item(enterprise, item, group, allow_default_qualitative=False, semantic_fact=semantic_fact_for(item))
                # 评价总分口径（总分/100，不走通用30分基础加比例公式）在三类组里必须一致。
                evaluated = _apply_tech_sme_evaluation_item_score(item, evaluated, tech_sme_evaluation)
                item_status = _safe_text(item.get("itemStatus"))
                eval_status = _safe_text(evaluated.get("status"))
                rule = item.get("matchRule") or {}
                # 直通车必须有实际可核验的充分证据；成长性默认通过不能触发直通资格。
                if _safe_text(rule.get("fieldType")).lower() == "number" and float(evaluated.get("score", 0.0)) < 1.0:
                    eval_status = "not_evaluable"
                is_hit = eval_status in {"pass", "reference_hit", "supporting_hit"}
                detail_message = evaluated.get("message") or ""
                if is_hit:
                    detail_message = f"{detail_message} 该项满足直通车判定。".strip()

                detail = {
                    "条件名称": _safe_text(item.get("itemName")),
                    "条件组原文": _safe_text(group.get("rawText")),
                    "条件组操作符": _safe_text(group.get("conditionOperator") or "AND"),
                    "条件上下文": _safe_text(group.get("conditionContext")),
                    "条件分支说明": _safe_text(item.get("conditionBranchText")) or _safe_text(group.get("conditionBranchText")),
                    "政策原文": _safe_text(item.get("policyFragment")) or _safe_text(group.get("rawText")),
                    "政策要求": _safe_text(rule.get("targetValue")) or _safe_text(group.get("rawText")),
                    "来源字段": "；".join(item.get("enterpriseFields", []) or []),
                    "评分组别": "SP",
                    "规则类型": item_status,
                    "计分归类": "special_pass",
                    "企业字段": "；".join(evaluated.get("resolved_fields", []) or item.get("enterpriseFields", []) or []),
                    "企业值": evaluated.get("enterprise_value"),
                    "目标值": rule.get("targetValue"),
                    "单位": _safe_text(rule.get("unit")),
                    "企业字段状态": item_status,
                    "匹配度": _match_degree(eval_status),
                    "得分": 1.0 if is_hit else 0.0,
                    "得分说明": "命中直通车条件" if is_hit else "未命中直通车条件",
                    "说明": detail_message,
                    "conditionNo": group.get("conditionNo"),
                    "itemId": _safe_text(item.get("itemId")),
                    "evalStatus": eval_status,
                    "是否参与计分": False,
                }
                apply_semantic_trace(detail, item)
                condition_details.append(detail)
                item_results.append(
                    {
                        "itemId": detail["itemId"],
                        "itemName": detail["条件名称"],
                        "itemStatus": item_status,
                        "evalStatus": eval_status,
                        "score": detail["得分"],
                        "scoreNote": detail["得分说明"],
                        "matchDegree": detail["匹配度"],
                        "message": detail["说明"],
                    }
                )

            group_summary = _group_summary(group, item_results)
            group_details.append(
                {
                    "conditionNo": group.get("conditionNo"),
                    "groupType": "直通车",
                    "rawText": _safe_text(group.get("rawText")),
                    "conditionOperator": _safe_text(group.get("conditionOperator")),
                    "groupStatus": group_summary["groupStatus"],
                    "items": item_results,
                    "unresolvedFragments": list(group.get("unresolvedFragments", []) or []),
                    "evaluationRole": "special_pass",
                    "specialPassEffect": "命中后优先按直通车路径判定，不再依赖普通软性评分。",
                }
            )
            if group_summary["groupStatus"] == "pass":
                special_pass_hit = True

        if special_pass_hit:
            for group in soft_groups:
                group_details.append(
                    {
                        "conditionNo": group.get("conditionNo"),
                        "groupType": "软性",
                        "rawText": _safe_text(group.get("rawText")),
                        "conditionOperator": _safe_text(group.get("conditionOperator")),
                        "groupStatus": "skipped",
                        "items": [],
                        "unresolvedFragments": list(group.get("unresolvedFragments", []) or []),
                        "evaluationRole": _safe_text(group.get("evaluationRole")),
                        "specialPassEffect": "已命中直通车条件，普通软性评分已跳过。",
                    }
                )
        else:
            for group in soft_groups:
                item_results = []
                for item in group.get("conditionFieldItems", []) or []:
                    llm_judged_item_count += 1
                    item_status = _safe_text(item.get("itemStatus"))
                    rule = item.get("matchRule") or {}
                    requires_semantic_evidence = (
                        item_status.lower() == "unresolved"
                        or _safe_text(rule.get("fieldType")).lower() != "number"
                        or _safe_text(rule.get("operator")).lower() == "llm_judge"
                    )
                    evaluated = _evaluate_item(
                        enterprise,
                        item,
                        group,
                        allow_default_qualitative=not requires_semantic_evidence,
                        semantic_fact=semantic_fact_for(item),
                    )
                    # 评价总分口径（总分/100，不走通用30分基础加比例公式）在三类组里必须一致。
                    evaluated = _apply_tech_sme_evaluation_item_score(item, evaluated, tech_sme_evaluation)
                    eval_status = _safe_text(evaluated.get("status"))
                    if item_status.lower() == "unresolved":
                        unresolved_item_count += 1
                    soft_score_type = _safe_text(evaluated.get("score_type"))
                    contributes_to_score = (
                        eval_status not in {"not_evaluable", "skipped"}
                        or bool(evaluated.get("provisional_score"))
                    )
                    coeff, coeff_note = 1.0, ""
                    if contributes_to_score:
                        if soft_score_type == "quantitative":
                            soft_quantitative_scores.append(float(evaluated.get("score", 0.0)))
                        elif soft_score_type == "presence":
                            soft_presence_scores.append(float(evaluated.get("score", 0.0)))
                        else:
                            soft_qualitative_base_scores.append(float(evaluated.get("score", 0.0)))
                            coeff, coeff_note = 1.0, ""

                        item_weight = _score_weight(evaluated)
                        evidence_tier = classify_runtime_evidence(evaluated)
                        score_pair = (float(evaluated.get("score", 0.0)), item_weight)
                        all_condition_scores.append(score_pair)
                        scored_item_count += 1
                        if evidence_tier.evidence_backed:
                            evidence_item_count += 1
                        if evidence_tier.bucket == "verified":
                            verified_scores.append(score_pair)
                        elif evidence_tier.bucket == "supporting":
                            supporting_scores.append(score_pair)
                        else:
                            unverified_scores.append(score_pair)
                    else:
                        evidence_tier = classify_runtime_evidence(evaluated)

                    detail_message = " ".join(
                        part for part in [evaluated.get("message", ""), coeff_note if soft_score_type == "qualitative" else ""] if part
                    )

                    detail = {
                        "条件名称": _safe_text(item.get("itemName")),
                        "条件组原文": _safe_text(group.get("rawText")),
                        "条件组操作符": _safe_text(group.get("conditionOperator") or "AND"),
                        "条件上下文": _safe_text(group.get("conditionContext")),
                        "条件分支说明": _safe_text(item.get("conditionBranchText")) or _safe_text(group.get("conditionBranchText")),
                        "政策原文": _safe_text(item.get("policyFragment")) or _safe_text(group.get("rawText")),
                        "政策要求": _safe_text(rule.get("targetValue")) or _safe_text(group.get("rawText")),
                        "来源字段": "；".join(item.get("enterpriseFields", []) or []),
                        "评分组别": "B",
                        "规则类型": item_status,
                        "计分归类": evidence_tier.bucket,
                        "evidenceTier": evidence_tier.tier,
                        "bucketReason": evidence_tier.reason,
                        "规则表原始ruleKind": _safe_text(evaluated.get("rule_kind")),
                        "企业字段": "；".join(evaluated.get("resolved_fields", []) or item.get("enterpriseFields", []) or []),
                        "企业值": evaluated.get("enterprise_value"),
                        "目标值": rule.get("targetValue"),
                        "单位": _safe_text(rule.get("unit")),
                        "企业字段状态": item_status,
                        "匹配度": _match_degree(eval_status),
                        "得分": round(float(evaluated.get("score", 0.0)), 4),
                        "得分说明": evaluated.get("message") or "",
                        "说明": detail_message,
                        "conditionNo": group.get("conditionNo"),
                        "itemId": _safe_text(item.get("itemId")),
                        "evalStatus": eval_status,
                        "是否参与计分": contributes_to_score,
                        "softScoreType": soft_score_type,
                        "qualitativeSimilarity": None,
                        "qualitativeCoeff": 1.0,
                        "passSource": _safe_text(evaluated.get("pass_source")),
                    }
                    apply_semantic_trace(detail, item)
                    condition_details.append(detail)
                    item_results.append(
                        {
                            "itemId": detail["itemId"],
                            "itemName": detail["条件名称"],
                            "itemStatus": item_status,
                            "evalStatus": eval_status,
                            "score": detail["得分"],
                            "scoreNote": detail["得分说明"],
                            "matchDegree": detail["匹配度"],
                    "message": detail["说明"],
                    "passSource": detail["passSource"],
                        }
                    )

                group_summary = _group_summary(group, item_results)
                group_details.append(
                    {
                        "conditionNo": group.get("conditionNo"),
                        "groupType": "软性",
                        "rawText": _safe_text(group.get("rawText")),
                        "conditionOperator": _safe_text(group.get("conditionOperator")),
                        "groupStatus": group_summary["groupStatus"],
                        "items": item_results,
                        "unresolvedFragments": list(group.get("unresolvedFragments", []) or []),
                        "evaluationRole": _safe_text(group.get("evaluationRole")),
                        "specialPassEffect": _safe_text(group.get("specialPassEffect")),
                    }
                )
    else:
        for group in special_pass_groups:
            group_details.append(
                {
                    "conditionNo": group.get("conditionNo"),
                    "groupType": "直通车",
                    "rawText": _safe_text(group.get("rawText")),
                    "conditionOperator": _safe_text(group.get("conditionOperator")),
                    "groupStatus": "skipped",
                    "items": [],
                    "unresolvedFragments": list(group.get("unresolvedFragments", []) or []),
                    "evaluationRole": "special_pass",
                    "specialPassEffect": "硬性条件未全部通过，直通车判定已跳过。",
                }
            )

        for group in soft_groups:
            group_details.append(
                {
                    "conditionNo": group.get("conditionNo"),
                    "groupType": "软性",
                    "rawText": _safe_text(group.get("rawText")),
                    "conditionOperator": _safe_text(group.get("conditionOperator")),
                    "groupStatus": "skipped",
                    "items": [],
                    "unresolvedFragments": list(group.get("unresolvedFragments", []) or []),
                    "evaluationRole": _safe_text(group.get("evaluationRole")),
                    "specialPassEffect": "硬性条件未全部通过，软性评分已跳过。",
                }
            )

    score_a = 1.0 if hard_pass else 0.0
    score_a_direct = _weighted_mean(verified_scores)
    score_b_indirect = _weighted_mean(supporting_scores)
    unverified_score = _weighted_mean(unverified_scores)
    # 规则表不再决定A/B权重。每条适用且可评分的政策条件同权参与最终分。
    weighted_match_score = _weighted_mean(all_condition_scores) or 0.0
    all_soft_scores = soft_quantitative_scores + soft_presence_scores + soft_qualitative_base_scores
    score_b = weighted_match_score
    coeff_c = 1.0
    raw_score = weighted_match_score
    final_score = round(min(raw_score * coeff_c, 1.0) if not hard_fail_items else 0.0, 4)
    if special_pass_hit and hard_pass:
        final_score = round(max(final_score, 0.9), 4)

    pending_condition_count = sum(1 for item in condition_details if item.get("evalStatus") == "not_evaluable")
    failed_condition_count = sum(1 for item in condition_details if item.get("evalStatus") == "fail")
    all_rules_confirmed = bool(condition_details) and pending_condition_count == 0 and failed_condition_count == 0
    default_assumed_count = sum(1 for item in condition_details if item.get("passSource") == "default_missing_assumed")
    evidence_ratio = round(evidence_item_count / scored_item_count, 4) if scored_item_count else 0.0
    confidence_level = _confidence_level(evidence_ratio)
    recommendation_eligible = bool(hard_pass and all_rules_confirmed and final_score >= RECOMMENDATION_SCORE_THRESHOLD)

    risks: List[str] = []
    if hard_fail_items:
        risks.append("存在明确硬性规则未通过。")
    if hard_review_items:
        risks.append("存在无法按当前规则计算的硬性条件，已按 0 分处理。")
    if unresolved_item_count:
        risks.append("部分补判条件缺少可核验证据，已标记待确认且不计入匹配分母。")
    if scored_item_count and confidence_level == "low":
        risks.append(
            f"证据覆盖率仅 {_score_to_percentage(evidence_ratio)}%"
            f"（{evidence_item_count}/{scored_item_count} 项有企业证据），"
            "当前分数主要由召回优先默认值构成，建议优先补齐企业数据后复核。"
        )
    if not all_condition_scores:
        risks.append("规则表没有任何可评分条件，不能形成有效匹配分。")
    if special_pass_hit:
        risks.append("直通车条件命中，已优先按直通车路径判定。")
    if hard_indicator_limit_exceeded:
        risks.append(f"当前政策条件文件硬性条件组数为 {hard_indicator_group_count}，超过建议上限 3 组；如需严格控制，应在条件文件层裁剪。")

    positives = [f"{item['条件名称']}：{item['说明']}" for item in condition_details if item.get("evalStatus") in {"pass", "reference_hit", "supporting_hit"}]
    negatives = [f"{item['条件名称']}：{item['说明']}" for item in condition_details if item.get("evalStatus") in {"fail", "not_evaluable"}]

    if not hard_pass:
        suggestion = "存在当前无法逆转的上限型硬性条件未通过，当前直接按 0 分处理。"
    elif special_pass_hit:
        suggestion = "已命中直通车条件，可优先按直通车路径推进人工复核和材料准备。"
    elif not all_condition_scores:
        suggestion = "当前规则表没有可评分条件，暂不推荐；请检查规则结构后再评估。"
    elif pending_condition_count:
        suggestion = "已有规则项形成成长性匹配分，但仍存在待确认条件，补齐企业证据后再决定是否推荐。"
    elif final_score >= 0.8 and confidence_level == "high":
        suggestion = "政策条件匹配度较高且证据充分，可优先进入人工复核和材料准备。"
    elif final_score >= 0.8:
        suggestion = f"匹配分较高，但证据覆盖率仅 {_score_to_percentage(evidence_ratio)}%，建议先补齐企业数据再推进申报。"
    elif final_score >= RECOMMENDATION_SCORE_THRESHOLD:
        suggestion = "具备一定匹配基础，建议补齐证据后再评估申报。"
    else:
        suggestion = "企业具备成长性匹配基础，建议持续跟进关键数值指标。"

    for detail in condition_details:
        detail["得分"] = _score_to_percentage(detail.get("得分", 0.0))

    for group in group_details:
        for item in group.get("items", []) or []:
            item["score"] = _score_to_percentage(item.get("score", 0.0))

    display_score_a = _score_to_percentage(score_a)
    display_score_b = _score_to_percentage(score_b)
    display_raw_score = _score_to_percentage(raw_score)
    display_final_score = _score_to_percentage(final_score)
    semantic_items = [
        item
        for group in hard_groups + special_pass_groups + soft_groups
        for item in group.get("conditionFieldItems", []) or []
    ]
    item_by_semantic_key = {semantic_key_for(item): item for item in semantic_items}
    item_by_id: Dict[str, Dict[str, Any]] = {}
    duplicate_item_ids = set()
    for item in semantic_items:
        item_id = _safe_text(item.get("itemId"))
        if not item_id:
            continue
        if item_id in item_by_id:
            duplicate_item_ids.add(item_id)
        else:
            item_by_id[item_id] = item
    for item_id in duplicate_item_ids:
        item_by_id.pop(item_id, None)
    display_semantic_facts = {
        fact_key: _reconcile_direct_money_fact(
            enterprise,
            item_by_semantic_key.get(fact_key) or item_by_id.get(fact_key) or {},
            fact,
        )
        for fact_key, fact in semantic_facts.items()
        if isinstance(fact, dict)
    } if isinstance(semantic_facts, dict) else {}
    item_id_counts: Counter[str] = Counter(
        _safe_text(item.get("itemId"))
        for group in hard_groups + special_pass_groups + soft_groups
        for item in group.get("conditionFieldItems", []) or []
        if _safe_text(item.get("itemId"))
    )
    # 旧明细阅读器按 itemId 查看事实。仅在政策内唯一时提供别名，重复编号绝不覆盖 semanticItemKey 事实。
    for semantic_key, fact in list(display_semantic_facts.items()):
        original_item_id = _safe_text(fact.get("itemId")) if isinstance(fact, dict) else ""
        if original_item_id and item_id_counts[original_item_id] == 1:
            display_semantic_facts.setdefault(original_item_id, fact)
    for group in hard_groups:
        for item in group.get("conditionFieldItems", []) or []:
            if "中小企业评价指标" not in _safe_text(item.get("itemName")):
                continue
            fact = _tech_sme_evaluation_fact(semantic_assessment, tech_sme_evaluation)
            if fact:
                key = semantic_key_for(item) or _safe_text(item.get("itemId"))
                display_semantic_facts[key] = fact
                original_item_id = _safe_text(item.get("itemId"))
                if original_item_id and item_id_counts[original_item_id] == 1:
                    display_semantic_facts.setdefault(original_item_id, fact)

    return {
        "政策标题": _safe_text(policy.get("title")),
        "匹配模式": "模型语义事实映射+规则表定量成长性评分" if semantic_assessment else "规则表定量成长性评分",
        "分数口径": "百分制（0-100分，系数项保留原值）",
        "条件文件名称": _safe_text(profile.get("name")),
        "条件文件版本": _safe_text(profile.get("schemaVersion")),
        "规则表名称": _safe_text(profile.get("name")),
        "规则表版本": _safe_text(profile.get("schemaVersion")),
        "规则判定模型已启用": bool((semantic_assessment or {}).get("enabled")),
        "规则判定模型": _safe_text((semantic_assessment or {}).get("model")),
        "规则判定严格模式": bool(semantic_assessment),
        "模型语义事实": display_semantic_facts,
        "科技型中小企业评价得分": tech_sme_evaluation,
        "规则判定条件项总数": sum(len(group.get("conditionFieldItems", []) or []) for group in hard_groups + special_pass_groups + soft_groups),
        "规则判定已执行项数": llm_judged_item_count,
        "hard_pass": hard_pass,
        "hard_indicator_group_count": hard_indicator_group_count,
        "special_pass_group_count": len(special_pass_groups),
        "soft_group_count": len(soft_groups),
        "hard_indicator_limit_exceeded": hard_indicator_limit_exceeded,
        "hard_fail_items": hard_fail_items,
        "hard_review_items": hard_review_items,
        "score_A": display_score_a,
        "score_B": display_score_b,
        "score_A_direct": _score_to_percentage(score_a_direct),
        "score_B_indirect": _score_to_percentage(score_b_indirect),
        "A类直接匹配得分": _score_to_percentage(score_a_direct),
        "B类间接匹配得分": _score_to_percentage(score_b_indirect),
        "未核验证据条件得分": _score_to_percentage(unverified_score),
        "A类权重": round(len(verified_scores) / len(all_condition_scores) * 100, 2) if all_condition_scores else 0,
        "B类权重": round(len(supporting_scores) / len(all_condition_scores) * 100, 2) if all_condition_scores else 0,
        "未核验证据权重": round(len(unverified_scores) / len(all_condition_scores) * 100, 2) if all_condition_scores else 0,
        "评分聚合算法": "runtime_evidence_tiering_balanced_mean_v1",
        "coeff_C": coeff_c,
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
                else "规则表内全部适用规则已有证据，且成长性匹配分达到推荐阈值。"
            )
            if recommendation_eligible
            else f"保留成长性匹配分，但存在明确失败或分数未达 {_score_to_percentage(RECOMMENDATION_SCORE_THRESHOLD)} 分推荐阈值的规则项。"
        ),
        "soft_quantitative_scores": [_score_to_percentage(value) for value in soft_quantitative_scores],
        "soft_presence_scores": [_score_to_percentage(value) for value in soft_presence_scores],
        "soft_qualitative_base_scores": [_score_to_percentage(value) for value in soft_qualitative_base_scores],
        "soft_qualitative_similarities": qualitative_similarities,
        "条件匹配详情": condition_details,
        "规则组匹配详情": group_details,
        "优势分析": positives[:5],
        "劣势分析": negatives[:5] + hard_fail_items[:3],
        "风险提示": risks,
        "总体建议": suggestion,
        "special_pass_hit": special_pass_hit,
    }
