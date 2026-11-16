"""结构化政策接口采集与标准化。"""

from __future__ import annotations

import json
import os
import re
import time
from copy import deepcopy
from html import unescape
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Dict, List, Optional

import requests


DEFAULT_BASE_URL = "http://127.0.0.1:8080"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DAILY_POLICY_CACHE_ENABLED = os.getenv("PEMATCHING_DAILY_POLICY_CACHE_ENABLED", "true").strip().lower() not in {"0", "false", "no"}
_POLICY_CACHE_LOCK = Lock()


FIELD_LABELS = {
    "basicEnterpriseTypeValue": "申报主体类型",
    "basicInstitutionNature": "机构性质",
    "regActualAddress": "实际经营地",
    "regTerritorialInfo": "注册地要求",
    "regTaxTerritory": "纳税地要求",
    "bizSubjectQualification": "主体资格",
    "bizOperationStatus": "经营状态",
    "bizScopeValue": "经营范围要求",
    "teamRdPersonnelCount": "常驻研发人员数量",
    "teamTechPersonnelRatio": "研发人员占比",
    "teamTechPersonnelCount": "科技人员数量",
    "equipRdEquipmentOriginal": "研发设备原值",
    "costAnnualRdInvestment": "年度研发投入",
    "costRdInvestmentIntensity": "研发投入强度",
    "projImplementationLocation": "项目实施地",
    "tradeAchievementType": "技术交易类型",
    "tradeTechTradeAmount": "技术交易收入",
    "tradeTechAchievementTransfer": "成果转化要求",
    "ipAchievement": "科研成果要求",
    "ipIpAgreement": "知识产权权属约定",
    "collabInternationalFoundation": "国际合作要求",
    "collabResearchInstitute": "科研院所合作要求",
    "collabUniversity": "高校合作要求",
    "qualEnterpriseQualificationValue": "企业资质要求",
    "projCoopUnitType": "合作单位类型",
    "mainService": "主要服务内容",
    "nationalStrategicIndustryCategory": "战略产业领域",
    "industryField": "行业领域",
    "coreTechnologyField": "核心技术领域",
    "infraFacilityStatus": "研发设施条件",
    "mgmtRdSystemSource": "管理运营机制",
}


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _compact_text(value: Any) -> str:
    text = _safe_text(value)
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _split_lines(value: Any) -> List[str]:
    text = _safe_text(value)
    if not text:
        return []
    lines = re.split(r"[\r\n]+", text)
    return [line.strip() for line in lines if line and line.strip()]


def _dedupe_conditions(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen = set()
    deduped: List[Dict[str, str]] = []
    for item in items:
        key = (item.get("条件名称", ""), item.get("政策原文", ""), item.get("政策要求", ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _extract_cluster_conditions(cluster: Dict[str, Any], path: str = "") -> List[Dict[str, str]]:
    conditions: List[Dict[str, str]] = []
    if not isinstance(cluster, dict):
        return conditions

    for key, value in cluster.items():
        if isinstance(value, dict):
            next_path = f"{path}.{key}" if path else key
            conditions.extend(_extract_cluster_conditions(value, next_path))

    for key, value in cluster.items():
        if key.endswith("Source") or isinstance(value, dict):
            continue
        value_text = _compact_text(value)
        if not value_text:
            continue

        source_key = ""
        if key.endswith("Value") and f"{key[:-5]}Source" in cluster:
            source_key = f"{key[:-5]}Source"
        elif f"{key}Source" in cluster:
            source_key = f"{key}Source"

        source_text = _compact_text(cluster.get(source_key)) if source_key else ""
        label = FIELD_LABELS.get(key, key)
        raw_text = source_text or f"{label}：{value_text}"
        conditions.append(
            {
                "条件名称": label,
                "政策原文": raw_text,
                "政策要求": value_text,
                "来源字段": f"{path}.{key}" if path else key,
            }
        )
    return conditions


def extract_structured_conditions(detail_data: Dict[str, Any]) -> List[Dict[str, str]]:
    """从结构化详情中提取可直接用于匹配的政策条件。"""

    condition_items: List[Dict[str, str]] = []
    for condition in detail_data.get("conditions", []):
        applicable_target = _compact_text(condition.get("applicableTarget"))
        if applicable_target:
            condition_items.append(
                {
                    "条件名称": "适用对象",
                    "政策原文": f"适用对象：{applicable_target}",
                    "政策要求": applicable_target,
                    "来源字段": "conditions.applicableTarget",
                }
            )

        application_requirement = _safe_text(condition.get("applicationRequirement"))
        for idx, line in enumerate(_split_lines(application_requirement), start=1):
            condition_items.append(
                {
                    "条件名称": f"申报要求{idx}",
                    "政策原文": line,
                    "政策要求": line,
                    "来源字段": "conditions.applicationRequirement",
                }
            )

        application_instruction = _safe_text(condition.get("applicationInstruction"))
        for idx, line in enumerate(_split_lines(application_instruction), start=1):
            condition_items.append(
                {
                    "条件名称": f"申报说明{idx}",
                    "政策原文": line,
                    "政策要求": line,
                    "来源字段": "conditions.applicationInstruction",
                }
            )

        condition_items.extend(_extract_cluster_conditions(condition.get("cluster") or {}, "conditions.cluster"))

    return _dedupe_conditions(condition_items)


class StructurePolicyClient:
    """结构化政策接口客户端。"""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def _get_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        last_error: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                response = self.session.get(
                    f"{self.base_url}{path}",
                    params=params,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                return response.json()
            except Exception as exc:
                last_error = exc
                if attempt >= 3:
                    raise
                time.sleep(0.8 * attempt)
        if last_error:
            raise last_error
        raise RuntimeError("请求失败")

    def fetch_policy_page(self, page: int = 1, size: int = 50, **extra_params: Any) -> Dict[str, Any]:
        params = {"page": page, "size": size, "requireSourcePolicyId": 1}
        params.update({key: value for key, value in extra_params.items() if value not in (None, "")})
        payload = self._get_json("/api/fusion-classification/policies", params=params)
        if not payload.get("success"):
            raise ValueError("获取结构化政策列表失败。")
        return payload

    def fetch_policy_detail(self, fusion_policy_id: str) -> Dict[str, Any]:
        payload = self._get_json(
            "/api/fusion-classification/fusion-detail",
            params={"fusionPolicyId": fusion_policy_id},
        )
        if not payload.get("success"):
            raise ValueError(f"获取政策详情失败: {fusion_policy_id}")
        return payload.get("data") or {}

    def fetch_document_content(self, document_id: str) -> Dict[str, Any]:
        payload = self._get_json(
            "/api/fusion-classification/documents/content",
            params={"id": document_id},
        )
        if not payload.get("success"):
            raise ValueError(f"获取政策正文失败: {document_id}")
        return payload.get("data") or {}


def _normalize_policy(
    summary: Dict[str, Any],
    detail: Dict[str, Any],
    document_contents: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    policy_info = detail.get("policyInfo") or {}
    support_methods = detail.get("supportMethods") or []
    materials = detail.get("materials") or []
    structured_conditions = extract_structured_conditions(detail)
    documents = detail.get("documents") or []

    notes_parts = []
    if _safe_text(policy_info.get("summary")):
        notes_parts.append(f"政策摘要：{_safe_text(policy_info.get('summary'))}")
    if support_methods:
        notes_parts.append("支持方式：" + "；".join(_compact_text(item.get("contentDesc")) for item in support_methods if _compact_text(item.get("contentDesc"))))
    if materials:
        notes_parts.append("申报材料：" + "；".join(_compact_text(item.get("materials")) for item in materials if _compact_text(item.get("materials"))))
    if document_contents:
        excerpt_parts = []
        for item in document_contents:
            title = _safe_text(item.get("title"))
            content = _safe_text(item.get("content"))
            excerpt = content[:1200] if content else ""
            if title or excerpt:
                excerpt_parts.append(f"{title}\n{excerpt}")
        if excerpt_parts:
            notes_parts.append("关联正文摘录：\n" + "\n\n".join(excerpt_parts))

    rule_table_json = detail.get("ruleTableJson")
    if not isinstance(rule_table_json, dict):
        rule_table_json = None

    association_document_excerpt = ""
    if document_contents:
        association_document_excerpt = "\n".join(
            re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", _safe_text(item.get("content"))))).strip()
            for item in document_contents
            if isinstance(item, dict) and _safe_text(item.get("content"))
        )[:3000]

    return {
        "id": _safe_text(detail.get("id") or summary.get("id")),
        "title": _safe_text(policy_info.get("policyTitle") or detail.get("directoryTitle") or summary.get("policyTitle")),
        "sourcePolicyId": _safe_text(detail.get("sourcePolicyId") or summary.get("sourcePolicyId")),
        "notes": "\n\n".join([part for part in notes_parts if part]),
        "structured_conditions": structured_conditions,
        "documents": documents,
        "document_titles": [_safe_text(item.get("policyTitle")) for item in documents if _safe_text(item.get("policyTitle"))],
        "policy_info": policy_info,
        "support_methods": support_methods,
        "materials": materials,
        "ruleTableJson": rule_table_json,
        "rule_table_json": rule_table_json,
        "has_rule_table_json": bool(rule_table_json),
        "association_document_excerpt": association_document_excerpt,
        "raw_summary": summary,
        "raw_detail": detail,
    }


def _daily_policy_cache_path() -> Path:
    return CONFIG_DIR / "主政策申报条件.json"


def _load_daily_policy_cache() -> Optional[List[Dict[str, Any]]]:
    if not DAILY_POLICY_CACHE_ENABLED:
        return None
    cache_path = _daily_policy_cache_path()
    if not cache_path.is_file():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        policies = payload.get("data") if isinstance(payload, dict) else None
        exported_at = ((payload.get("source") or {}).get("exportedAt") if isinstance(payload, dict) else "")
        if (
            not isinstance(policies, list)
            or not policies
            or not all(isinstance(item, dict) for item in policies)
            or not str(exported_at).startswith(datetime.now().date().isoformat())
        ):
            return None
        return policies
    except (OSError, json.JSONDecodeError):
        return None


def _write_daily_policy_cache(policies: List[Dict[str, Any]], base_url: str) -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _daily_policy_cache_path()
    payload = {
        "schemaVersion": "pematching-structure-policy-cache-v1",
        "source": {
            "baseUrl": base_url.rstrip("/"),
            "listEndpoint": "/api/fusion-classification/policies",
            "detailEndpoint": "/api/fusion-classification/fusion-detail",
            "exportedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
        "summary": {"total": len(policies)},
        "data": policies,
    }
    temporary_path = cache_path.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_path.replace(cache_path)
    return cache_path


def _load_structure_policies_from_api(
    base_url: str = DEFAULT_BASE_URL,
    page_size: int = 50,
    max_policies: Optional[int] = None,
    detail_workers: int = 5,
    include_document_content: bool = False,
    document_limit_per_policy: int = 1,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> List[Dict[str, Any]]:
    """从 structure 页面真实接口批量加载政策。"""

    client = StructurePolicyClient(base_url=base_url)
    page = 1
    summaries: List[Dict[str, Any]] = []

    while True:
        payload = client.fetch_policy_page(page=page, size=page_size)
        data = payload.get("data") or []
        if progress_callback:
            progress_callback(
                f"已加载政策列表分页 page={page}, 本页={len(data)} 条, 当前累计={len(summaries) + len(data)} 条"
            )
        if not data:
            break

        summaries.extend(data)
        if max_policies and len(summaries) >= max_policies:
            summaries = summaries[:max_policies]
            if progress_callback:
                progress_callback(f"达到 max_policies={max_policies}，停止继续拉取列表分页")
            break

        pagination = payload.get("pagination") or {}
        total = int(pagination.get("total") or 0)
        if len(summaries) >= total:
            if progress_callback:
                progress_callback(f"政策列表分页拉取完成，总量={len(summaries)}")
            break
        page += 1

    policies: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, detail_workers)) as executor:
        future_map = {
            executor.submit(client.fetch_policy_detail, _safe_text(summary.get("id"))): summary
            for summary in summaries
        }
        for future in as_completed(future_map):
            summary = future_map[future]
            summary_id = _safe_text(summary.get("id"))
            summary_title = _safe_text(summary.get("policyTitle") or summary.get("directoryTitle"))
            detail = future.result()
            document_contents: List[Dict[str, Any]] = []
            if include_document_content:
                selected_docs = [
                    item
                    for item in detail.get("documents", [])
                    if _safe_text(item.get("policyAttribute")) in ("申报通知", "实施细则", "主政策")
                ]
                if not selected_docs:
                    selected_docs = list(detail.get("documents", []))
                for doc in selected_docs[: max(1, document_limit_per_policy)]:
                    document_id = _safe_text(doc.get("id"))
                    if document_id:
                        try:
                            document_contents.append(client.fetch_document_content(document_id))
                        except Exception:
                            continue

            normalized_policy = _normalize_policy(summary, detail, document_contents)
            policies.append(normalized_policy)
            if progress_callback:
                progress_callback(
                    "政策详情已加载 "
                    f"id={summary_id} title={summary_title or normalized_policy.get('title', '')} "
                    f"conditions={len(normalized_policy.get('structured_conditions', []) or [])} "
                    f"documents={len(normalized_policy.get('documents', []) or [])}"
                )

    policies.sort(key=lambda item: item.get("title", ""))
    if progress_callback:
        progress_callback(f"政策详情标准化完成，总政策数={len(policies)}")
    return policies


def load_structure_policies(
    base_url: str = DEFAULT_BASE_URL,
    page_size: int = 50,
    max_policies: Optional[int] = None,
    detail_workers: int = 5,
    include_document_content: bool = False,
    include_sparse_document_content: bool = False,
    document_limit_per_policy: int = 1,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> List[Dict[str, Any]]:
    """加载主政策；默认优先复用当天的结构化条件快照。"""
    if include_document_content:
        if progress_callback:
            progress_callback("已请求关联正文，跳过当天主政策条件快照并从 API 加载")
        return _load_structure_policies_from_api(
            base_url=base_url,
            page_size=page_size,
            max_policies=max_policies,
            detail_workers=detail_workers,
            include_document_content=True,
            document_limit_per_policy=document_limit_per_policy,
            progress_callback=progress_callback,
        )

    policies = _load_daily_policy_cache()
    if policies is None:
        with _POLICY_CACHE_LOCK:
            policies = _load_daily_policy_cache()
            if policies is None:
                policies = _load_structure_policies_from_api(
                    base_url=base_url,
                    page_size=page_size,
                    max_policies=None,
                    detail_workers=detail_workers,
                    include_document_content=False,
                    document_limit_per_policy=document_limit_per_policy,
                    progress_callback=progress_callback,
                )
                cache_path = _write_daily_policy_cache(policies, base_url)
                if progress_callback:
                    progress_callback(f"已更新当天主政策申报条件快照: {cache_path.name} ({len(policies)} 条)")
    elif progress_callback:
        progress_callback(f"使用当天主政策申报条件快照: {_daily_policy_cache_path().name} ({len(policies)} 条)")

    if max_policies is not None and max_policies > 0:
        policies = policies[:max_policies]
    if include_sparse_document_content:
        policies = _attach_sparse_policy_document_excerpts(
            policies,
            base_url=base_url,
            max_workers=detail_workers,
            progress_callback=progress_callback,
        )
    return policies


def _association_policy_is_sparse(policy: Dict[str, Any]) -> bool:
    policy_info = policy.get("policy_info") if isinstance(policy.get("policy_info"), dict) else {}
    signals = (
        _safe_text(policy_info.get("summary")),
        _safe_text(policy_info.get("industryField")),
        _safe_text(policy_info.get("applicableScope")),
        _safe_text(policy.get("structured_conditions")),
        _safe_text(policy.get("ruleTableJson") or policy.get("rule_table_json")),
    )
    return sum(bool(value) for value in signals) < 2


def _association_document_id(policy: Dict[str, Any]) -> str:
    documents = policy.get("documents") if isinstance(policy.get("documents"), list) else []
    selected = [
        item for item in documents
        if isinstance(item, dict) and _safe_text(item.get("policyAttribute")) in ("申报通知", "实施细则", "主政策")
    ]
    if not selected:
        selected = [item for item in documents if isinstance(item, dict)]
    return _safe_text(selected[0].get("id")) if selected else ""


def _clean_association_excerpt(payload: Dict[str, Any]) -> str:
    content = _safe_text(payload.get("content"))
    if not content:
        return ""
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", content))).strip()[:3000]


def _attach_sparse_policy_document_excerpts(
    policies: List[Dict[str, Any]],
    *,
    base_url: str,
    max_workers: int,
    progress_callback: Optional[Callable[[str], None]],
) -> List[Dict[str, Any]]:
    """仅为结构化信息不足的政策补充一份有界正文摘录。"""

    enriched = deepcopy(policies)
    client = StructurePolicyClient(base_url=base_url)
    targets = [
        (index, _association_document_id(policy))
        for index, policy in enumerate(enriched)
        if _association_policy_is_sparse(policy) and not _safe_text(policy.get("association_document_excerpt"))
    ]
    targets = [(index, document_id) for index, document_id in targets if document_id]
    if not targets:
        return enriched

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        future_map = {
            executor.submit(client.fetch_document_content, document_id): (index, document_id)
            for index, document_id in targets
        }
        for future in as_completed(future_map):
            index, _document_id = future_map[future]
            try:
                excerpt = _clean_association_excerpt(future.result())
            except Exception:
                excerpt = ""
            if excerpt:
                enriched[index]["association_document_excerpt"] = excerpt
    if progress_callback:
        loaded_count = sum(bool(_safe_text(policy.get("association_document_excerpt"))) for policy in enriched)
        progress_callback(f"已为结构化信息稀疏政策补充正文摘录：{loaded_count}/{len(targets)}")
    return enriched
