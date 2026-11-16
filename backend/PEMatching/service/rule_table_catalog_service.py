"""已生成规则表主政策目录服务。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional

from service.policy_loader import StructurePolicyClient, extract_structured_conditions


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _pick_policy_title(summary: Dict[str, Any], detail: Dict[str, Any]) -> str:
    policy_info = detail.get("policyInfo") or {}
    return (
        _safe_text(summary.get("mainFileTitle"))
        or _safe_text(detail.get("mainFileTitle"))
        or _safe_text(detail.get("directoryTitle"))
        or _safe_text(policy_info.get("policyTitle"))
        or _safe_text(summary.get("directoryTitle"))
        or _safe_text(summary.get("policyTitle"))
    )


def _summarize_rule_table(rule_table_json: Dict[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(rule_table_json, dict):
        return {
            "ruleTableName": "",
            "schemaVersion": "",
            "hardRuleGroupCount": 0,
            "softRuleGroupCount": 0,
            "specialPassRuleGroupCount": 0,
            "definitionNoteCount": 0,
            "applicationChecklistGroupCount": 0,
            "conditionFieldItemCount": 0,
        }

    hard_groups = rule_table_json.get("hardRuleGroups") or []
    soft_groups = rule_table_json.get("softRuleGroups") or []
    special_groups = rule_table_json.get("specialPassRuleGroups") or []
    definition_notes = rule_table_json.get("definitionNotes") or []
    checklist_groups = rule_table_json.get("applicationChecklistGroups") or []
    item_count = 0
    for group in [*hard_groups, *soft_groups, *special_groups]:
        item_count += len(group.get("conditionFieldItems") or [])

    return {
        "ruleTableName": _safe_text(rule_table_json.get("policyDisplayName"))
        or _safe_text(rule_table_json.get("name")),
        "schemaVersion": _safe_text(rule_table_json.get("schemaVersion")),
        "hardRuleGroupCount": len(hard_groups),
        "softRuleGroupCount": len(soft_groups),
        "specialPassRuleGroupCount": len(special_groups),
        "definitionNoteCount": len(definition_notes),
        "applicationChecklistGroupCount": len(checklist_groups),
        "conditionFieldItemCount": item_count,
    }


def _normalize_rule_table_policy(summary: Dict[str, Any], detail: Dict[str, Any]) -> Dict[str, Any]:
    policy_info = detail.get("policyInfo") or {}
    rule_table_json = detail.get("ruleTableJson")
    if not isinstance(rule_table_json, dict):
        rule_table_json = None

    documents = detail.get("documents") or []
    support_methods = detail.get("supportMethods") or []
    materials = detail.get("materials") or []
    structured_conditions = extract_structured_conditions(detail)
    rule_table_summary = _summarize_rule_table(rule_table_json)

    return {
        "id": _safe_text(detail.get("id") or summary.get("id")),
        "title": _pick_policy_title(summary, detail),
        "sourcePolicyId": _safe_text(detail.get("sourcePolicyId") or summary.get("sourcePolicyId")),
        "notes": _safe_text(policy_info.get("summary")),
        "structured_conditions": structured_conditions,
        "documents": documents,
        "document_titles": [_safe_text(item.get("policyTitle")) for item in documents if _safe_text(item.get("policyTitle"))],
        "policy_info": policy_info,
        "support_methods": support_methods,
        "materials": materials,
        "ruleTableJson": rule_table_json,
        "rule_table_json": rule_table_json,
        "has_rule_table_json": bool(rule_table_json),
        "rule_table_summary": rule_table_summary,
        "raw_summary": summary,
        "raw_detail": detail,
    }


def build_rule_table_catalog_entry(
    policy: Dict[str, Any],
    include_rule_table_json: bool = False,
    include_raw_detail: bool = False,
) -> Dict[str, Any]:
    summary = policy.get("rule_table_summary") or {}
    payload = {
        "政策ID": policy.get("id", ""),
        "政策标题": policy.get("title", ""),
        "来源政策ID": policy.get("sourcePolicyId", ""),
        "是否有规则表": bool(policy.get("has_rule_table_json")),
        "规则表名称": summary.get("ruleTableName", ""),
        "规则表版本": summary.get("schemaVersion", ""),
        "硬性组数": int(summary.get("hardRuleGroupCount") or 0),
        "软性组数": int(summary.get("softRuleGroupCount") or 0),
        "直通车组数": int(summary.get("specialPassRuleGroupCount") or 0),
        "指标口径数": int(summary.get("definitionNoteCount") or 0),
        "申报清单组数": int(summary.get("applicationChecklistGroupCount") or 0),
        "条件项总数": int(summary.get("conditionFieldItemCount") or 0),
        "结构化条件数": len(policy.get("structured_conditions", []) or []),
        "文档数": len(policy.get("documents", []) or []),
    }
    if include_rule_table_json:
        payload["ruleTableJson"] = policy.get("ruleTableJson")
    if include_raw_detail:
        payload["detail"] = policy.get("raw_detail") or {}
    return payload


def get_rule_table_policy_detail(
    fusion_policy_id: str,
    base_url: str,
) -> Dict[str, Any]:
    normalized_id = _safe_text(fusion_policy_id)
    if not normalized_id:
        raise ValueError("缺少 fusionPolicyId。")

    client = StructurePolicyClient(base_url=base_url)
    detail = client.fetch_policy_detail(normalized_id)
    summary = {
        "id": normalized_id,
        "mainFileTitle": _safe_text(detail.get("mainFileTitle")),
        "directoryTitle": _safe_text(detail.get("directoryTitle")),
        "policyTitle": _safe_text((detail.get("policyInfo") or {}).get("policyTitle")),
        "sourcePolicyId": _safe_text(detail.get("sourcePolicyId")),
    }
    return _normalize_rule_table_policy(summary, detail)


def load_policies_with_rule_tables(
    base_url: str,
    limit: int = 20,
    page_size: int = 50,
    detail_workers: int = 5,
    keyword: str = "",
    review_status: str = "1",
    structure_review_status: str = "all",
    policy_ids: Optional[List[str]] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> List[Dict[str, Any]]:
    client = StructurePolicyClient(base_url=base_url)
    normalized_keyword = _safe_text(keyword)
    normalized_ids = [_safe_text(item) for item in (policy_ids or []) if _safe_text(item)]
    effective_limit = max(0, int(limit or 0))
    matched: List[Dict[str, Any]] = []

    if normalized_ids:
        for policy_id in normalized_ids:
            policy = get_rule_table_policy_detail(policy_id, base_url=base_url)
            if normalized_keyword and normalized_keyword not in policy.get("title", ""):
                continue
            if policy.get("has_rule_table_json"):
                matched.append(policy)
        return matched[:effective_limit] if effective_limit else matched

    page = 1
    while True:
        payload = client.fetch_policy_page(
            page=page,
            size=max(1, page_size),
            reviewStatus=review_status,
            structureReviewStatus=structure_review_status,
        )
        data = payload.get("data") or []
        if not data:
            break

        if normalized_keyword:
            data = [
                item
                for item in data
                if normalized_keyword
                in (
                    _safe_text(item.get("mainFileTitle"))
                    or _safe_text(item.get("directoryTitle"))
                    or _safe_text(item.get("policyTitle"))
                )
            ]

        if progress_callback:
            progress_callback(
                f"规则表目录扫描 page={page} 本页候选={len(data)} 已命中={len(matched)}"
            )

        ordered_batch: List[Optional[Dict[str, Any]]] = [None] * len(data)
        with ThreadPoolExecutor(max_workers=max(1, detail_workers)) as executor:
            future_map = {
                executor.submit(client.fetch_policy_detail, _safe_text(summary.get("id"))): index
                for index, summary in enumerate(data)
            }
            for future in as_completed(future_map):
                index = future_map[future]
                summary = data[index]
                detail = future.result()
                ordered_batch[index] = _normalize_rule_table_policy(summary, detail)

        for policy in ordered_batch:
            if not policy or not policy.get("has_rule_table_json"):
                continue
            matched.append(policy)
            if effective_limit and len(matched) >= effective_limit:
                return matched

        pagination = payload.get("pagination") or {}
        total = int(pagination.get("total") or 0)
        if total and page * max(1, page_size) >= total:
            break
        page += 1

    return matched
