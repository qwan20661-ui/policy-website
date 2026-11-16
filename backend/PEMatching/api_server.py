# -*- coding: utf-8 -*-
"""PEMatching 轻量后端服务。"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PROJECT_ROOT.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

from service.enterprise_loader import (
    DEFAULT_ENTERPRISE_API_BASE_URL,
    DEFAULT_ENTERPRISE_API_PATH,
    load_enterprise_from_record,
    load_enterprises,
    load_enterprises_from_api,
)
from service.matching_service import run_matching
from service.policy_association_service import ASSOCIATION_LLM_ENABLED, ASSOCIATION_SCORE_FORMULA
from service.policy_loader import load_structure_policies
from service.rule_engine_service import ASSUMED_PASS_SOURCES
from service.rule_profile_service import (
    DEFAULT_TECH_SME_RULE_PROFILE_PATH,
    DEFAULT_TEST_ENTERPRISE_JSON_PATH,
    build_rule_profile_policy,
)
from service.rule_table_catalog_service import (
    build_rule_table_catalog_entry,
    get_rule_table_policy_detail,
    load_policies_with_rule_tables,
)


load_dotenv(PROJECT_ROOT / ".env")


def _resolve_config_path(value: Optional[str], default_path: Path) -> Path:
    raw = (value or "").strip()
    if not raw:
        return default_path
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate
    return (REPO_ROOT / candidate).resolve()


ENTERPRISE_JSON_PATH = DEFAULT_TEST_ENTERPRISE_JSON_PATH.resolve()
DEFAULT_ENTERPRISE_SOURCE = os.getenv("PEMATCHING_ENTERPRISE_SOURCE", "api").strip().lower() or "api"
ENTERPRISE_API_BASE_URL = os.getenv("PEMATCHING_ENTERPRISE_API_BASE_URL", DEFAULT_ENTERPRISE_API_BASE_URL)
ENTERPRISE_API_PATH = os.getenv("PEMATCHING_ENTERPRISE_API_PATH", DEFAULT_ENTERPRISE_API_PATH)
ENTERPRISE_API_PAGE_SIZE = int(os.getenv("PEMATCHING_ENTERPRISE_API_PAGE_SIZE", "100"))
ENTERPRISE_API_MAX_PAGES = int(os.getenv("PEMATCHING_ENTERPRISE_API_MAX_PAGES", "0"))
ENTERPRISE_API_TIMEOUT = float(os.getenv("PEMATCHING_ENTERPRISE_API_TIMEOUT", "15"))
RULE_PROFILE_PATH = _resolve_config_path(
    os.getenv("RULE_PROFILE_PATH"),
    DEFAULT_TECH_SME_RULE_PROFILE_PATH,
)
DEFAULT_POLICY_SOURCE = os.getenv("PEMATCHING_POLICY_SOURCE", "structure_api").strip().lower()
STRUCTURE_API_BASE_URL = os.getenv("STRUCTURE_API_BASE_URL", "http://127.0.0.1:8080")
MATCH_RESULT_STORE_BASE_URL = os.getenv(
    "PEMATCHING_RESULT_STORE_BASE_URL",
    "http://127.0.0.1:8080",
).rstrip("/")
MATCH_RESULT_STORE_TIMEOUT = float(os.getenv("PEMATCHING_RESULT_STORE_TIMEOUT", "30"))
POLICY_PAGE_SIZE = int(os.getenv("POLICY_PAGE_SIZE", "50"))
MAX_WORKERS = max(1, int(os.getenv("MAX_WORKERS", "3")))
RULE_TABLE_MATCH_ENTERPRISE_JSON_PATH = _resolve_config_path(
    os.getenv("PEMATCHING_RULE_TABLE_MATCH_ENTERPRISE_JSON_PATH"),
    PROJECT_ROOT / "企业测试" / "企业数据_测试集_100.json",
)
DEFAULT_OUTPUT_FILE = _resolve_config_path(
    os.getenv("OUTPUT_FILE"),
    PROJECT_ROOT / "llm_matching_results.json",
)
ENTERPRISE_MATCH_DETAIL_DIR = _resolve_config_path(
    os.getenv("PEMATCHING_ENTERPRISE_MATCH_DETAIL_DIR"),
    PROJECT_ROOT / "output" / "enterprise_match_details",
)
API_LOG_DIR = _resolve_config_path(
    os.getenv("PEMATCHING_API_LOG_DIR"),
    PROJECT_ROOT / "logs",
)
SERVER_HOST = os.getenv("PEMATCHING_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("PEMATCHING_PORT", "8010"))
RULE_TABLE_LLM_API_URL = (os.getenv("API_URL", "") or "").strip()
RULE_TABLE_LLM_MODEL_NAME = (os.getenv("MODEL_NAME", "") or "").strip()
RULE_TABLE_LLM_TIMEOUT = (os.getenv("RULE_LLM_TIMEOUT", "300") or "300").strip()
RULE_TABLE_LLM_CONNECT_TIMEOUT = (os.getenv("RULE_LLM_CONNECT_TIMEOUT", "10") or "10").strip()


def _rule_table_llm_endpoint_for_log() -> str:
    """返回可安全写入启动日志的模型服务地址，不暴露密钥或查询参数。"""
    if not RULE_TABLE_LLM_API_URL:
        return "未配置"
    parsed = urlparse(RULE_TABLE_LLM_API_URL)
    if not parsed.scheme or not parsed.netloc:
        return "格式无效"
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path or '/'}"


def _json_response(handler: BaseHTTPRequestHandler, payload: Dict[str, Any], status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.end_headers()
    handler.wfile.write(body)


def _read_json_body(handler: BaseHTTPRequestHandler) -> Dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8")
    if not raw.strip():
        return {}
    return json.loads(raw)


def _filter_enterprises(enterprises: List[Dict[str, Any]], names: List[str]) -> List[Dict[str, Any]]:
    if not names:
        return enterprises
    name_set = {name.strip() for name in names if name and name.strip()}
    return [item for item in enterprises if item.get("企业名称") in name_set]


def _filter_policies(
    policies: List[Dict[str, Any]],
    policy_ids: List[str],
    keyword: str,
    limit: int,
) -> List[Dict[str, Any]]:
    filtered = policies
    if policy_ids:
        id_set = {item.strip() for item in policy_ids if item and item.strip()}
        filtered = [item for item in filtered if item.get("id") in id_set]
    if keyword:
        filtered = [item for item in filtered if keyword in item.get("title", "")]
    if limit > 0:
        filtered = filtered[:limit]
    return filtered


def _load_rule_profile_policy(rule_profile_text: str, explicit_title: str = "") -> List[Dict[str, Any]]:
    rule_profile_path = _resolve_config_path(rule_profile_text, RULE_PROFILE_PATH)
    return [build_rule_profile_policy(rule_profile_path, explicit_title)]


def _resolve_enterprise_json_path(path_text: Optional[str], default_path: Path) -> Path:
    return _resolve_config_path((path_text or "").strip(), default_path)


def _first_non_empty_text(*values: Any) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


class _RequestEventLog:
    """维护单个 API 请求的独立运行日志文件。"""

    def __init__(self, request_id: str) -> None:
        self._lock = Lock()
        self.path: Optional[Path] = None
        try:
            API_LOG_DIR.mkdir(parents=True, exist_ok=True)
            self.path = API_LOG_DIR / f"api_request_{datetime.now():%Y%m%d_%H%M%S}_{request_id}.log"
        except OSError as exc:
            print(f"[PEMatching][API] 请求日志目录不可用: {exc}", flush=True)

    def write(self, line: str) -> None:
        if self.path is None:
            return
        try:
            with self._lock:
                with self.path.open("a", encoding="utf-8") as log_file:
                    log_file.write(f"{line}\n")
        except OSError as exc:
            print(f"[PEMatching][API] 请求日志写入失败: {exc}", flush=True)


def _log_api_event(
    event: str,
    request_log: Optional[_RequestEventLog] = None,
    **fields: Any,
) -> None:
    """向标准输出及单请求文件写入结构化运行日志。"""
    payload = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "event": event,
        **fields,
    }
    line = f"[PEMatching][API] {json.dumps(payload, ensure_ascii=False, default=str)}"
    print(line, flush=True)
    if request_log is not None:
        request_log.write(line)


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _support_method_contents(value: Any) -> List[str]:
    contents: List[str] = []
    for item in _as_list(value):
        if isinstance(item, dict):
            text = _first_non_empty_text(
                item.get("contentDesc"),
                item.get("content"),
                item.get("supportMethod"),
                item.get("name"),
            )
        else:
            text = _first_non_empty_text(item)
        if text:
            contents.append(text)
    return contents


def _has_response_value(value: Any) -> bool:
    """保留数值 0，避免将真实的零值误判为企业数据缺失。"""

    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _backfill_local_evidence(detail: Dict[str, Any], enterprise_value: Any, unit: Any) -> List[Dict[str, str]]:
    """为本地标准字段/派生公式补齐可追溯证据，避免有值但 evidence 为空。"""
    field_text = _first_non_empty_text(detail.get("企业字段"), detail.get("来源字段"))
    fields = [part.strip() for part in re.split(r"[；;]", field_text) if part.strip()]
    if not fields or not _has_response_value(enterprise_value):
        return []
    display_value = _first_non_empty_text(enterprise_value)
    display_unit = _first_non_empty_text(unit)
    source_text = f"{display_value}{display_unit}" if display_unit and display_unit not in display_value else display_value
    evidence: List[Dict[str, str]] = []
    for field in fields:
        source_path = field if field.startswith("$") else f"$normalized.{field}"
        evidence.append({"sourcePath": source_path, "sourceText": source_text})
    return evidence


def _build_condition_scores(match_result: Dict[str, Any], passed_only: bool = False) -> List[Dict[str, Any]]:
    """输出逐条件评分账目；可选地仅保留通过条件以兼容原有展示字段。"""

    condition_scores: List[Dict[str, Any]] = []
    for detail in _as_list(match_result.get("条件匹配详情")):
        if not isinstance(detail, dict):
            continue
        eval_status = _first_non_empty_text(detail.get("evalStatus"))
        if passed_only and eval_status != "pass":
            continue

        evidence_paths = _as_list(detail.get("证据路径"))
        evidence_texts = _as_list(detail.get("证据文本"))
        evidence_count = max(len(evidence_paths), len(evidence_texts))
        evidence = [
            {
                "sourcePath": _first_non_empty_text(evidence_paths[index] if index < len(evidence_paths) else ""),
                "sourceText": _first_non_empty_text(evidence_texts[index] if index < len(evidence_texts) else ""),
            }
            for index in range(evidence_count)
        ]

        enterprise_value = detail.get("企业值")
        pass_source = _first_non_empty_text(detail.get("passSource"))
        evidence_tier = _first_non_empty_text(detail.get("evidenceTier"))
        score_bucket = _first_non_empty_text(detail.get("计分归类"))
        # 缺失默认放行（含评价维度补足）不算企业证据。仅凭 enterpriseValue 非空判断会出错：
        # 字段映射可能把无关字段的值填进来（如把「职工总数15」当作「大专以上学历占比」的企业值），
        # 于是出现 hasEnterpriseEvidence=true 但 evidence=[]、evidenceRatio=0 的自相矛盾。
        # 与打分侧 _is_evidence_backed 共用 ASSUMED_PASS_SOURCES，保证两处口径一致。
        rejected_or_unverified = evidence_tier in {"R_rejected", "U_missing"} or score_bucket == "unverified"
        if rejected_or_unverified:
            evidence = []
        elif not evidence and evidence_tier in {"A1_exact", "A2_derived"}:
            evidence = _backfill_local_evidence(detail, enterprise_value, detail.get("单位"))
        has_enterprise_evidence = not rejected_or_unverified and pass_source not in ASSUMED_PASS_SOURCES and (
            bool(evidence) or _has_response_value(enterprise_value)
        )
        # 新结果显式保存该字段；历史归档只有不可计算和跳过项不参与总分。
        # 不根据数值猜测，避免把实际 0 分的失败条件错误排除。
        counts_towards_score = detail.get("是否参与计分")
        if not isinstance(counts_towards_score, bool):
            counts_towards_score = eval_status not in {"not_evaluable", "skipped"}
        condition_scores.append({
            "itemId": _first_non_empty_text(detail.get("itemId")),
            "conditionName": _first_non_empty_text(detail.get("条件名称")),
            "applicationRequirement": _first_non_empty_text(
                detail.get("政策原文"),
                detail.get("条件组原文"),
                detail.get("政策要求"),
            ),
            "enterpriseField": _first_non_empty_text(detail.get("企业字段"), detail.get("来源字段")),
            "enterpriseValue": enterprise_value,
            "unit": _first_non_empty_text(detail.get("单位")),
            "score": detail.get("得分"),
            "evalStatus": eval_status,
            "countsTowardsScore": counts_towards_score,
            "passSource": pass_source,
            "evidenceTier": evidence_tier,
            "scoreBucket": score_bucket,
            "bucketReason": _first_non_empty_text(detail.get("bucketReason")),
            "hasEnterpriseEvidence": has_enterprise_evidence,
            "evidence": evidence,
        })
    return condition_scores


def _build_matched_conditions(match_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """输出已通过条件，保留既有轻量响应字段。"""

    return _build_condition_scores(match_result, passed_only=True)


def _match_score_level(match_score: Any) -> Optional[int]:
    """按匹配分计算前端展示的星级。"""

    if match_score is None:
        return None
    try:
        score = float(match_score)
    except (TypeError, ValueError):
        return None
    if not 0 <= score <= 100:
        return None
    if score <= 1:
        return 0
    if score <= 40:
        return 1
    if score <= 60:
        return 2
    if score <= 70:
        return 3
    if score <= 90:
        return 4
    return 5


def _build_enterprise_recommendation_item(
    policy: Dict[str, Any],
    match_result: Dict[str, Any],
) -> Dict[str, Any]:
    """将内部跑数结果收敛为企业画像页面需要的政策字段。"""

    raw_detail = _as_dict(policy.get("raw_detail"))
    policy_info = _as_dict(raw_detail.get("policyInfo")) or _as_dict(policy.get("policy_info"))
    conditions = _as_dict(raw_detail.get("conditions"))
    raw_documents = _as_list(raw_detail.get("documents")) or _as_list(policy.get("documents"))
    # 文档透传自身标识与展示/跳转所需字段，便于前端点击查看原文或按类型分组；
    # 正文内容（content 等）不属于对外响应，仅保留在 runId 完整归档中。
    documents = [
        {
            "id": _first_non_empty_text(
                document.get("id"), document.get("documentId"), document.get("attachmentId"),
            ),
            "policyTitle": _first_non_empty_text(
                document.get("policyTitle"), document.get("title"), document.get("name"),
                document.get("mainFileTitle"), document.get("fileName"),
            ),
            "fileName": _first_non_empty_text(document.get("fileName")),
            "filePath": _first_non_empty_text(document.get("filePath")),
            "policyAttribute": _first_non_empty_text(document.get("policyAttribute")),
            "sourcePolicyId": _first_non_empty_text(document.get("sourcePolicyId")),
        }
        for document in raw_documents
        if isinstance(document, dict) and _first_non_empty_text(
            document.get("policyTitle"), document.get("title"), document.get("name"),
            document.get("mainFileTitle"), document.get("fileName"),
        )
    ]
    support_methods = _as_list(raw_detail.get("supportMethods")) or _as_list(policy.get("support_methods"))

    recommended = match_result.get("是否推荐")
    if recommended in (1, "1", True, "是"):
        is_recommended: Optional[bool] = True
    elif recommended in (0, "0", False, "否"):
        is_recommended = False
    else:
        is_recommended = None
    retryable = bool(match_result.get("可重试", False))
    match_status = _first_non_empty_text(match_result.get("匹配状态")) if retryable else "matched"
    association = _as_dict(match_result.get("associationMatch"))
    calculation = _as_dict(association.get("calculation"))
    dimension_names = {
        "industry": "所属行业",
        "scale": "企业规模",
        "lifecycle_qualification": "成立阶段与资质进阶",
        "research_development": "研发状态",
        "employment": "用工与就业",
        "region_park": "注册区域与园区",
        "education_talent": "学历与高层次人才",
    }
    public_dimensions = []
    for dimension in _as_list(association.get("dimensions")):
        if not isinstance(dimension, dict):
            continue
        dimension_key = _first_non_empty_text(dimension.get("dimension"))
        public_dimensions.append({
            "dimension": dimension_key,
            "name": dimension_names.get(dimension_key, dimension_key),
            "weight": dimension.get("weight"),
            "status": _first_non_empty_text(dimension.get("status")) or "unknown",
            "coefficient": dimension.get("coefficient"),
            "rawContribution": dimension.get("contribution"),
            "normalizedContribution": dimension.get("normalizedContribution"),
            "includedInDenominator": bool(dimension.get("includedInDenominator")),
            "reason": _first_non_empty_text(dimension.get("reason")),
        })
    public_association = {
        "score": association.get("score"),
        "level": _first_non_empty_text(association.get("level")) or "unknown",
        "isAssociated": association.get("isAssociated"),
        "status": _first_non_empty_text(association.get("status")) or "disabled",
        "reasons": [
            _first_non_empty_text(reason)
            for reason in _as_list(association.get("reasons"))[:3]
            if _first_non_empty_text(reason)
        ],
        "calculation": {
            "formula": _first_non_empty_text(calculation.get("formula")) or ASSOCIATION_SCORE_FORMULA,
            "expression": _first_non_empty_text(calculation.get("expression")),
            "weightedContribution": calculation.get("weightedContribution"),
            "effectiveWeight": calculation.get("effectiveWeight"),
            "score": calculation.get("score"),
            "dimensions": public_dimensions,
            "note": "关联分仅表示企业特征与政策方向的关联程度，不表示企业满足申报条件。",
        },
    }

    return {
        "fusionPolicyId": _first_non_empty_text(policy.get("id"), raw_detail.get("id")),
        "applicationStartTime": _first_non_empty_text(conditions.get("applicationStartTime")),
        "applicationEndTime": _first_non_empty_text(conditions.get("applicationEndTime")),
        "mainFileTitle": _first_non_empty_text(
            raw_detail.get("mainFileTitle"),
            policy.get("title"),
            match_result.get("政策标题"),
        ),
        "issuingDepartment": _first_non_empty_text(
            raw_detail.get("issuingDepartment"),
            policy_info.get("issuingDepartment"),
        ),
        "matchScore": match_result.get("final_score"),
        "level": _match_score_level(match_result.get("final_score")),
        "isRecommended": is_recommended,
        # 证据充分度与 matchScore 正交：matchScore 决定是否达到召回阈值，
        # evidenceRatio 用于同分政策的排序与前端"证据充分度"提示。
        "evidenceRatio": match_result.get("证据覆盖率"),
        "confidenceLevel": match_result.get("证据置信度"),
        "verifiedConditionCount": match_result.get("已核验条件项数"),
        "assumedConditionCount": match_result.get("默认假定条件项数"),
        "scoredConditionCount": match_result.get("参与计分条件项数"),
        "maxAmount": _first_non_empty_text(raw_detail.get("maxAmount"), policy_info.get("maxAmount")),
        "documents": documents,
        "applicableScope": _first_non_empty_text(
            raw_detail.get("applicableScope"),
            policy_info.get("applicableScope"),
        ),
        "industryField": _first_non_empty_text(policy_info.get("industryField")),
        "industryFieldPath": _first_non_empty_text(policy_info.get("industryFieldPath")),
        "supportMethods": _support_method_contents(support_methods),
        "matchStatus": match_status or "匹配失败待重试",
        "recommendationBlockers": [],
        "matchedConditions": _build_matched_conditions(match_result),
        # 用于页面回放实际计算分；不包含模型原始响应或企业完整快照。
        "conditionScores": _build_condition_scores(match_result),
        "associationMatch": public_association,
        "retryable": retryable,
    }


def _build_enterprise_recommendations(
    policies: List[Dict[str, Any]],
    match_result: Dict[str, Any],
) -> List[Dict[str, Any]]:
    policies_by_title: Dict[str, Dict[str, Any]] = {
        _first_non_empty_text(item.get("title")): item
        for item in policies
        if _first_non_empty_text(item.get("title"))
    }
    policies_by_id = {
        _first_non_empty_text(item.get("id")): item
        for item in policies
        if _first_non_empty_text(item.get("id"))
    }

    recommendations: List[Dict[str, Any]] = []
    seen_policy_ids = set()
    for index, item in enumerate(_as_list(match_result.get("政策匹配结果"))):
        if not isinstance(item, dict):
            continue
        policy = policies_by_id.get(_first_non_empty_text(item.get("fusionPolicyId")))
        policy = policy or policies_by_title.get(_first_non_empty_text(item.get("政策标题"))) or {}
        recommendation = _build_enterprise_recommendation_item(policy, item)
        # 去重键只能取匹配结果自身的标识。recommendation 上的 fusionPolicyId 是按标题
        # 反查政策表回填的，同名不同政策会回填出同一个 id，用它去重会静默吞掉一条；
        # 匹配结果自身又常常不带 id，因此没有自身 id 时退化为「标题+序号」，
        # 保证「进多少条出多少条」，上游 _deduplicate_policies 已负责真正的政策去重。
        own_policy_id = _first_non_empty_text(item.get("fusionPolicyId"))
        dedupe_key = own_policy_id or f"{_first_non_empty_text(recommendation.get('mainFileTitle'))}#{index}"
        if dedupe_key in seen_policy_ids:
            continue
        seen_policy_ids.add(dedupe_key)
        recommendations.append(recommendation)
    return recommendations


def _build_enterprise_match_detail(
    run_id: str,
    enterprise: Dict[str, Any],
    policies: List[Dict[str, Any]],
    match_result: Dict[str, Any],
    request_filters: Dict[str, Any],
) -> Dict[str, Any]:
    """构造完整跑数结果；对外接口只返回 runId，不返回完整明细。"""

    created_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    return {
        "schemaVersion": "enterprise-match-detail-v1",
        "runId": run_id,
        "createdAt": created_at,
        "request": {
            "enterpriseId": enterprise.get("企业ID", ""),
            "enterpriseName": enterprise.get("企业名称", ""),
            "creditCode": enterprise.get("统一社会信用代码", ""),
            **request_filters,
        },
        "policyInputs": policies,
        "matchResult": match_result,
    }


def _persist_enterprise_match_detail(detail_payload: Dict[str, Any]) -> Dict[str, Any]:
    """原子保存完整跑数文件归档，并返回可入库的文件关联信息。"""

    run_id = str(detail_payload.get("runId") or "").strip()
    if not run_id:
        raise ValueError("完整跑数明细缺少 runId。")
    target_dir = ENTERPRISE_MATCH_DETAIL_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"enterprise_match_{datetime.now():%Y%m%d_%H%M%S}_{run_id}.json"
    temporary = target.with_suffix(".tmp")
    # 归档内容保持完整，只取消格式空白，减少接口尾部写盘和长期存储成本。
    temporary.write_text(
        json.dumps(detail_payload, ensure_ascii=False, separators=(",", ":"), default=str),
        encoding="utf-8",
    )
    temporary.replace(target)
    return {
        "detailFileName": target.name,
        "detailFileSize": target.stat().st_size,
    }


def _persist_enterprise_match_result(
    enterprise: Dict[str, Any],
    response_data: Dict[str, Any],
    detail_payload: Dict[str, Any],
    detail_file: Dict[str, Any],
    policy_count: int,
) -> None:
    """通过根目录 JDBC 服务将匹配结果写入达梦数据库。"""

    request_payload = {
        "runId": detail_payload["runId"],
        "enterpriseId": enterprise.get("企业ID", ""),
        "enterpriseName": enterprise.get("企业名称", ""),
        "creditCode": enterprise.get("统一社会信用代码", ""),
        "policyCount": policy_count,
        "schemaVersion": detail_payload.get("schemaVersion", "enterprise-match-detail-v1"),
        "responseJson": json.dumps(response_data, ensure_ascii=False, separators=(",", ":"), default=str),
        "detailFileName": detail_file.get("detailFileName", ""),
        "detailFileSize": detail_file.get("detailFileSize", 0),
    }
    request = Request(
        f"{MATCH_RESULT_STORE_BASE_URL}/api/pematching/enterprise-match-results",
        data=json.dumps(request_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=MATCH_RESULT_STORE_TIMEOUT) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        response_text = exc.read().decode("utf-8", errors="replace")
        try:
            error_payload = json.loads(response_text)
            message = str(error_payload.get("message") or "").strip()
        except json.JSONDecodeError:
            message = response_text.strip()
        raise RuntimeError(message or f"匹配结果数据库服务返回 HTTP {exc.code}。") from exc
    except URLError as exc:
        raise RuntimeError(f"无法连接匹配结果数据库服务：{exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("匹配结果数据库服务响应超时。") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("匹配结果数据库服务返回了无效 JSON。") from exc

    if not isinstance(response_payload, dict) or not response_payload.get("success"):
        message = response_payload.get("message") if isinstance(response_payload, dict) else ""
        raise RuntimeError(str(message or "匹配结果写入数据库失败。"))


def _load_enterprises_for_request(
    payload: Dict[str, Any],
    default_json_path: Path,
) -> tuple[List[Dict[str, Any]], Dict[str, str]]:
    explicit_json_path = str(payload.get("enterpriseJsonPath") or "").strip()
    source = str(payload.get("enterpriseSource") or DEFAULT_ENTERPRISE_SOURCE).strip().lower()
    if explicit_json_path:
        source = "json"
    if source not in {"api", "json"}:
        raise ValueError("enterpriseSource 仅支持 api 或 json。")

    if source == "json":
        enterprise_json_path = _resolve_enterprise_json_path(explicit_json_path, default_json_path)
        if not enterprise_json_path.exists():
            raise FileNotFoundError(f"企业 JSON 不存在: {enterprise_json_path}")
        return load_enterprises(enterprise_json_path), {
            "enterpriseSource": "json",
            "enterpriseJsonPath": str(enterprise_json_path),
            "enterpriseApiUrl": "",
        }

    enterprise_api_url = f"{ENTERPRISE_API_BASE_URL.rstrip('/')}/{ENTERPRISE_API_PATH.lstrip('/')}"
    enterprises = load_enterprises_from_api(
        base_url=ENTERPRISE_API_BASE_URL,
        api_path=ENTERPRISE_API_PATH,
        page_size=ENTERPRISE_API_PAGE_SIZE,
        max_pages=ENTERPRISE_API_MAX_PAGES,
        timeout=ENTERPRISE_API_TIMEOUT,
        progress_callback=lambda message: print(f"[EnterpriseAPI] {message}"),
    )
    return enterprises, {
        "enterpriseSource": "api",
        "enterpriseJsonPath": "",
        "enterpriseApiUrl": enterprise_api_url,
    }


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "PEMatchingHTTP/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_OPTIONS(self) -> None:
        _json_response(self, {"success": True}, 200)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        try:
            if parsed.path == "/health":
                _json_response(
                    self,
                    {
                        "success": True,
                        "data": {
                            "service": "PEMatching",
                            "defaultEnterpriseSource": DEFAULT_ENTERPRISE_SOURCE,
                            "enterpriseJsonPath": str(ENTERPRISE_JSON_PATH),
                            "enterpriseApiUrl": f"{ENTERPRISE_API_BASE_URL.rstrip('/')}/{ENTERPRISE_API_PATH.lstrip('/')}",
                            "ruleTableMatchEnterpriseJsonPath": str(RULE_TABLE_MATCH_ENTERPRISE_JSON_PATH),
                            "ruleProfilePath": str(RULE_PROFILE_PATH),
                            "defaultPolicySource": DEFAULT_POLICY_SOURCE,
                            "structureApiBaseUrl": STRUCTURE_API_BASE_URL,
                        },
                    },
                )
                return

            if parsed.path == "/api/rule-table/generate":
                fusion_policy_id = (query.get("fusionPolicyId") or [""])[0].strip()
                if not fusion_policy_id:
                    _json_response(self, {"success": False, "message": "缺少 fusionPolicyId 参数。"}, 400)
                    return

                print(f"[API] GET /api/rule-table/generate start, fusionPolicyId={fusion_policy_id}")
                from service.rule_table_generator import generate_rule_table

                result = generate_rule_table(fusion_policy_id=fusion_policy_id)
                stats = result.get("stats", {})
                print(
                    f"[API] GET /api/rule-table/generate done, "
                    f"hardGroups={stats.get('hardGroupCount')}, "
                    f"softGroups={stats.get('softGroupCount')}, "
                    f"specialPassGroups={stats.get('specialPassGroupCount')}, "
                    f"definitionNotes={stats.get('definitionNoteCount')}, "
                    f"checklists={stats.get('applicationChecklistGroupCount')}, "
                    f"items={stats.get('itemCount')}"
                )
                _json_response(self, {"success": True, "data": result})
                return

            if parsed.path == "/api/pematching/enterprises":
                enterprise_source = (query.get("enterpriseSource") or [DEFAULT_ENTERPRISE_SOURCE])[0]
                enterprises, source_meta = _load_enterprises_for_request(
                    {"enterpriseSource": enterprise_source},
                    ENTERPRISE_JSON_PATH,
                )
                data = [
                    {
                        "企业ID": item.get("企业ID", ""),
                        "企业名称": item.get("企业名称", ""),
                        "统一社会信用代码": item.get("统一社会信用代码", ""),
                        "注册地": item.get("注册地", ""),
                        "一级行业": item.get("一级行业", ""),
                        "二级行业": item.get("二级行业", ""),
                        "荣誉资质": item.get("荣誉资质", []),
                    }
                    for item in enterprises
                ]
                _json_response(self, {"success": True, "data": data, "meta": source_meta})
                return

            if parsed.path == "/api/pematching/rule-tables":
                limit = int((query.get("limit") or ["20"])[0] or 20)
                keyword = (query.get("keyword") or [""])[0]
                review_status = (query.get("reviewStatus") or ["1"])[0]
                structure_review_status = (query.get("structureReviewStatus") or ["all"])[0]
                policy_ids = query.get("policyId") or []
                policies = load_policies_with_rule_tables(
                    base_url=STRUCTURE_API_BASE_URL,
                    limit=max(0, limit),
                    page_size=POLICY_PAGE_SIZE,
                    detail_workers=MAX_WORKERS,
                    keyword=keyword,
                    review_status=review_status,
                    structure_review_status=structure_review_status,
                    policy_ids=policy_ids,
                )
                data = [build_rule_table_catalog_entry(item) for item in policies]
                _json_response(
                    self,
                    {
                        "success": True,
                        "data": data,
                        "meta": {
                            "policyCount": len(data),
                            "limit": limit,
                            "keyword": keyword,
                            "reviewStatus": review_status,
                            "structureReviewStatus": structure_review_status,
                        },
                    },
                )
                return

            if parsed.path == "/api/pematching/rule-tables/detail":
                fusion_policy_id = (query.get("fusionPolicyId") or query.get("policyId") or [""])[0].strip()
                if not fusion_policy_id:
                    _json_response(self, {"success": False, "message": "缺少 fusionPolicyId 参数。"}, 400)
                    return
                policy = get_rule_table_policy_detail(fusion_policy_id, base_url=STRUCTURE_API_BASE_URL)
                if not policy.get("has_rule_table_json"):
                    _json_response(self, {"success": False, "message": "该主政策尚未生成规则表 JSON。"}, 404)
                    return
                _json_response(
                    self,
                    {
                        "success": True,
                        "data": build_rule_table_catalog_entry(
                            policy,
                            include_rule_table_json=True,
                            include_raw_detail=True,
                        ),
                    },
                )
                return

            if parsed.path == "/api/pematching/policies":
                limit = int((query.get("limit") or ["10"])[0])
                keyword = (query.get("keyword") or [""])[0]
                include_document_content = (query.get("includeDocumentContent") or ["false"])[0].lower() == "true"
                policy_source = ((query.get("policySource") or [DEFAULT_POLICY_SOURCE])[0] or DEFAULT_POLICY_SOURCE).strip().lower()
                if policy_source == "structure_api":
                    policies = load_structure_policies(
                        base_url=STRUCTURE_API_BASE_URL,
                        page_size=POLICY_PAGE_SIZE,
                        max_policies=max(limit, 1),
                        detail_workers=MAX_WORKERS,
                        include_document_content=include_document_content,
                    )
                    if keyword:
                        policies = [item for item in policies if keyword in item.get("title", "")]
                else:
                    policies = _load_rule_profile_policy(str(RULE_PROFILE_PATH))
                data = [
                    {
                        "政策ID": item.get("id", ""),
                        "政策标题": item.get("title", ""),
                        "来源政策ID": item.get("sourcePolicyId", ""),
                        "条件数": len(item.get("structured_conditions", []) or []),
                        "是否有规则表": bool(item.get("ruleTableJson") or item.get("rule_table_json")),
                    }
                    for item in policies[:limit]
                ]
                _json_response(self, {"success": True, "data": data})
                return

            _json_response(self, {"success": False, "message": "未找到接口。"}, 404)
        except (ValueError, FileNotFoundError) as exc:
            _json_response(self, {"success": False, "message": str(exc)}, 400)
        except Exception as exc:
            _json_response(self, {"success": False, "message": str(exc)}, 500)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        temporary_rule_table_path: Optional[Path] = None
        request_id = uuid.uuid4().hex[:12]
        started_at = time.monotonic()
        request_log = _RequestEventLog(request_id)

        def log_api_event(event: str, **fields: Any) -> None:
            _log_api_event(event, request_log=request_log, **fields)

        log_api_event(
            "请求已接收",
            requestId=request_id,
            method="POST",
            path=parsed.path,
            clientIp=self.client_address[0],
            contentLength=int(self.headers.get("Content-Length", "0") or 0),
        )
        try:
            if parsed.path == "/api/rule-table/generate":
                payload = _read_json_body(self)
                fusion_policy_id = (payload.get("fusionPolicyId") or "").strip()
                # 兼容：若 body 为空，尝试从 query 参数获取
                if not fusion_policy_id:
                    query = parse_qs(parsed.query)
                    fusion_policy_id = (query.get("fusionPolicyId") or [""])[0].strip()
                if not fusion_policy_id:
                    print(f"[API] POST /api/rule-table/generate missing fusionPolicyId, body={json.dumps(payload, ensure_ascii=False)[:200]}")
                    _json_response(self, {"success": False, "message": "缺少 fusionPolicyId 参数。"}, 400)
                    return

                print(f"[API] POST /api/rule-table/generate start, fusionPolicyId={fusion_policy_id}")
                from service.rule_table_generator import generate_rule_table

                result = generate_rule_table(fusion_policy_id=fusion_policy_id)
                stats = result.get("stats", {})
                print(
                    f"[API] POST /api/rule-table/generate done, "
                    f"hardGroups={stats.get('hardGroupCount')}, "
                    f"softGroups={stats.get('softGroupCount')}, "
                    f"specialPassGroups={stats.get('specialPassGroupCount')}, "
                    f"definitionNotes={stats.get('definitionNoteCount')}, "
                    f"checklists={stats.get('applicationChecklistGroupCount')}, "
                    f"items={stats.get('itemCount')}"
                )
                _json_response(self, {"success": True, "data": result})
                return

            if parsed.path == "/api/pematching/match/rule-tables":
                payload = _read_json_body(self)
                enterprise_names = payload.get("enterpriseNames") or []
                policy_ids = payload.get("policyIds") or []
                policy_keyword = (payload.get("policyKeyword") or "").strip()
                policy_limit = int(payload.get("policyLimit") or 10)
                persist_output = bool(payload.get("persistOutput", False))
                review_status = (payload.get("reviewStatus") or "1").strip() or "1"
                structure_review_status = (payload.get("structureReviewStatus") or "all").strip() or "all"
                enterprises, enterprise_source_meta = _load_enterprises_for_request(
                    payload,
                    RULE_TABLE_MATCH_ENTERPRISE_JSON_PATH,
                )
                enterprises = _filter_enterprises(enterprises, enterprise_names)
                if not enterprises:
                    _json_response(self, {"success": False, "message": "未找到符合条件的企业。"}, 400)
                    return

                policies = load_policies_with_rule_tables(
                    base_url=STRUCTURE_API_BASE_URL,
                    limit=0 if policy_limit <= 0 else max(policy_limit, len(policy_ids) or 1),
                    page_size=POLICY_PAGE_SIZE,
                    detail_workers=MAX_WORKERS,
                    keyword=policy_keyword,
                    review_status=review_status,
                    structure_review_status=structure_review_status,
                    policy_ids=policy_ids,
                )
                if policy_limit > 0:
                    policies = policies[:policy_limit]
                if not policies:
                    _json_response(self, {"success": False, "message": "未找到已生成规则表的主政策。"}, 400)
                    return

                output_file = DEFAULT_OUTPUT_FILE if persist_output else None
                result = run_matching(
                    enterprises=enterprises,
                    policies=policies,
                    max_workers=MAX_WORKERS,
                    output_file=output_file,
                )
                _json_response(
                    self,
                    {
                        "success": True,
                        "data": result,
                        "meta": {
                            "enterpriseCount": len(enterprises),
                            "policyCount": len(policies),
                            "policySource": "structure_rule_tables",
                            **enterprise_source_meta,
                            "persisted": bool(output_file),
                            "outputFile": str(output_file) if output_file else "",
                        },
                    },
                )
                return

            if parsed.path == "/api/pematching/match/enterprise":
                payload = _read_json_body(self)
                if not payload:
                    raise ValueError("企业信息请求体为空，请使用 application/json 提交单个企业对象。")
                if not isinstance(payload, dict):
                    raise ValueError("企业信息必须是 JSON 对象。")
                enterprise_record = payload.get("enterprise") if isinstance(payload.get("enterprise"), dict) else payload
                requested_enterprise_id = str(
                    enterprise_record.get("id") or enterprise_record.get("enterpriseId") or ""
                ).strip()
                if not requested_enterprise_id:
                    raise ValueError("企业信息格式错误：必须提供 id 或 enterpriseId。")
                enterprise = load_enterprise_from_record(
                    enterprise_record,
                    data_source="request:/api/pematching/match/enterprise",
                )
                enterprise["企业ID"] = requested_enterprise_id

                query = parse_qs(parsed.query)
                policy_ids = query.get("policyId") or query.get("fusionPolicyId") or []
                policy_keyword = (query.get("policyKeyword") or [""])[0].strip()
                include_document_content = (query.get("includeDocumentContent") or ["false"])[0].lower() == "true"
                policy_limit_text = (query.get("policyLimit") or ["0"])[0].strip() or "0"
                try:
                    policy_limit = int(policy_limit_text)
                except ValueError as exc:
                    raise ValueError("policyLimit 必须是非负整数。") from exc
                if policy_limit < 0:
                    raise ValueError("policyLimit 必须是非负整数。")

                log_api_event(
                    "单企业匹配参数已解析",
                    requestId=request_id,
                    enterpriseName=enterprise.get("企业名称", ""),
                    policyIds=policy_ids,
                    policyKeyword=policy_keyword,
                    policyLimit=policy_limit,
                    includeDocumentContent=include_document_content,
                )

                fetch_count = None if policy_limit == 0 else max(policy_limit, len(policy_ids) or 1)
                policies = load_structure_policies(
                    base_url=STRUCTURE_API_BASE_URL,
                    page_size=POLICY_PAGE_SIZE,
                    max_policies=fetch_count,
                    detail_workers=MAX_WORKERS,
                    include_document_content=include_document_content,
                    include_sparse_document_content=(
                        ASSOCIATION_LLM_ENABLED and not include_document_content
                    ),
                )
                policies = _filter_policies(policies, policy_ids, policy_keyword, policy_limit)
                if not policies:
                    log_api_event("单企业匹配未找到结构化政策", requestId=request_id)
                    _json_response(self, {"success": False, "message": "未找到符合条件的结构化主政策。"}, 400)
                    return

                log_api_event(
                    "单企业匹配已加载结构化政策",
                    requestId=request_id,
                    policyCount=len(policies),
                )

                log_api_event("单企业匹配开始执行", requestId=request_id, policyCount=len(policies))
                result = run_matching(
                    enterprises=[enterprise],
                    policies=policies,
                    max_workers=MAX_WORKERS,
                    output_file=None,
                    progress_callback=lambda message: log_api_event(
                        "单企业匹配进度",
                        requestId=request_id,
                        message=message,
                    ),
                )
                if not result:
                    log_api_event("单企业匹配未生成结果", requestId=request_id)
                    _json_response(self, {"success": False, "message": "未生成企业政策匹配结果。"}, 500)
                    return

                run_id = uuid.uuid4().hex
                response_data = {
                    "policies": _build_enterprise_recommendations(policies, result[0]),
                }
                detail_payload = _build_enterprise_match_detail(
                    run_id=run_id,
                    enterprise=enterprise,
                    policies=policies,
                    match_result=result[0],
                    request_filters={
                        "policyIds": policy_ids,
                        "policyKeyword": policy_keyword,
                        "policyLimit": policy_limit,
                        "includeDocumentContent": include_document_content,
                    },
                )
                detail_file = _persist_enterprise_match_detail(detail_payload)
                _persist_enterprise_match_result(
                    enterprise=enterprise,
                    response_data=response_data,
                    detail_payload=detail_payload,
                    detail_file=detail_file,
                    policy_count=len(policies),
                )

                log_api_event(
                    "单企业匹配执行完成",
                    requestId=request_id,
                    runId=run_id,
                    policyCount=len(policies),
                    elapsedMs=round((time.monotonic() - started_at) * 1000),
                )
                _json_response(
                    self,
                    {
                        "success": True,
                        "data": response_data,
                        "meta": {
                            "runId": run_id,
                            "policyCount": len(policies),
                        },
                    },
                )
                return

            if parsed.path != "/api/pematching/match":
                _json_response(self, {"success": False, "message": "未找到接口。"}, 404)
                return

            payload = _read_json_body(self)
            enterprise_names = payload.get("enterpriseNames") or []
            policy_ids = payload.get("policyIds") or []
            policy_keyword = (payload.get("policyKeyword") or "").strip()
            policy_limit = int(payload.get("policyLimit") or 10)
            policy_source = (payload.get("policySource") or DEFAULT_POLICY_SOURCE).strip().lower()
            rule_profile_path = (payload.get("ruleProfilePath") or str(RULE_PROFILE_PATH)).strip()
            rule_policy_title = (payload.get("rulePolicyTitle") or "").strip()
            include_document_content = bool(payload.get("includeDocumentContent", False))
            persist_output = bool(payload.get("persistOutput", False))
            rule_table_json = payload.get("ruleTableJson")
            if rule_table_json:
                import uuid as _uuid

                temp_dir = PROJECT_ROOT / "_temp_rule_tables"
                temp_dir.mkdir(parents=True, exist_ok=True)
                temp_path = temp_dir / f"temp_{_uuid.uuid4().hex[:8]}.json"
                temp_path.write_text(
                    json.dumps(rule_table_json, ensure_ascii=False), encoding="utf-8"
                )
                temporary_rule_table_path = temp_path
                rule_profile_path = str(temp_path)
                rule_policy_title = (
                    rule_policy_title
                    or str(rule_table_json.get("policyDisplayName", "") or "").strip()
                    or str(rule_table_json.get("name", "") or "").strip()
                )
                policy_source = "rule_profile"

            enterprises, enterprise_source_meta = _load_enterprises_for_request(payload, ENTERPRISE_JSON_PATH)
            enterprises = _filter_enterprises(enterprises, enterprise_names)
            if not enterprises:
                _json_response(self, {"success": False, "message": "未找到符合条件的企业。"}, 400)
                return

            if policy_source == "structure_api":
                max_policies = None if policy_limit <= 0 else max(policy_limit, len(policy_ids) or 1)
                policies = load_structure_policies(
                    base_url=STRUCTURE_API_BASE_URL,
                    page_size=POLICY_PAGE_SIZE,
                    max_policies=max_policies,
                    detail_workers=MAX_WORKERS,
                    include_document_content=include_document_content,
                )
                policies = _filter_policies(policies, policy_ids, policy_keyword, policy_limit)
            else:
                policies = _load_rule_profile_policy(rule_profile_path, rule_policy_title)
            if not policies:
                _json_response(self, {"success": False, "message": "未找到符合条件的政策。"}, 400)
                return

            output_file = DEFAULT_OUTPUT_FILE if persist_output else None
            result = run_matching(
                enterprises=enterprises,
                policies=policies,
                max_workers=MAX_WORKERS,
                output_file=output_file,
            )
            _json_response(
                self,
                {
                    "success": True,
                    "data": result,
                    "meta": {
                        "enterpriseCount": len(enterprises),
                        "policyCount": len(policies),
                        "policySource": policy_source,
                        **enterprise_source_meta,
                        "persisted": bool(output_file),
                        "outputFile": str(output_file) if output_file else "",
                    },
                },
            )
        except (ValueError, FileNotFoundError) as exc:
            log_api_event(
                "请求参数或文件错误",
                requestId=request_id,
                path=parsed.path,
                error=str(exc),
                elapsedMs=round((time.monotonic() - started_at) * 1000),
            )
            _json_response(self, {"success": False, "message": str(exc)}, 400)
        except Exception as exc:
            log_api_event(
                "请求执行异常",
                requestId=request_id,
                path=parsed.path,
                errorType=type(exc).__name__,
                error=str(exc),
                elapsedMs=round((time.monotonic() - started_at) * 1000),
            )
            _json_response(self, {"success": False, "message": str(exc)}, 500)
        finally:
            if temporary_rule_table_path is not None:
                try:
                    temporary_rule_table_path.unlink(missing_ok=True)
                except OSError:
                    pass


def _startup_sync_field_config() -> None:
    """启动时同步申报条件管理 JSON 到 config/。"""
    try:
        from service.rule_table_generator import _sync_and_load_field_reference

        ref = _sync_and_load_field_reference()
        line_count = len([l for l in ref.split("\n") if l.strip()])
        print(f"[Startup] 申报条件管理已同步: {line_count} 个可选字段")
    except Exception as exc:
        print(f"[Startup] 申报条件管理同步失败: {exc}")


def _startup_sync_policy_cache() -> None:
    """启动时确保当天主政策申报条件快照已生成。"""
    try:
        policies = load_structure_policies(
            base_url=STRUCTURE_API_BASE_URL,
            page_size=POLICY_PAGE_SIZE,
            max_policies=None,
            detail_workers=MAX_WORKERS,
            include_document_content=False,
        )
        print(f"[Startup] 主政策申报条件快照已就绪: {len(policies)} 条")
    except Exception as exc:
        print(f"[Startup] 主政策申报条件快照同步失败: {exc}")


def run_server(host: str = SERVER_HOST, port: int = SERVER_PORT) -> None:
    print(f"PEMatching 服务启动中...")
    print(
        "[Startup] 规则表生成 LLM: "
        f"endpoint={_rule_table_llm_endpoint_for_log()}, "
        f"model={RULE_TABLE_LLM_MODEL_NAME or '未配置'}, "
        f"connectTimeout={RULE_TABLE_LLM_CONNECT_TIMEOUT}s, readTimeout={RULE_TABLE_LLM_TIMEOUT}s"
    )
    print(
        "[Startup] 并发配置: "
        f"MAX_WORKERS={MAX_WORKERS}（主政策详情加载与全局条件模型队列共用）"
    )
    _startup_sync_field_config()
    _startup_sync_policy_cache()
    httpd = ThreadingHTTPServer((host, port), ApiHandler)
    print(f"PEMatching 服务已启动: http://{host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭服务...")
        httpd.shutdown()
        print("服务已关闭。")


if __name__ == "__main__":
    run_server()
