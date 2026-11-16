from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from threading import Lock
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PROJECT_ROOT.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from service.policy_loader import StructurePolicyClient


load_dotenv(PROJECT_ROOT / ".env")


DEFAULT_STRUCTURE_API_BASE_URL = os.getenv("STRUCTURE_API_BASE_URL", "http://127.0.0.1:8080").strip().rstrip("/")
DEFAULT_RULE_TABLE_API_BASE_URL = os.getenv(
    "RULE_TABLE_API_BASE_URL",
    os.getenv("STRUCTURE_PAGE_API_BASE_URL", "http://127.0.0.1:3000"),
).strip().rstrip("/")
DEFAULT_PAGE_SIZE = int(os.getenv("POLICY_PAGE_SIZE", "20") or 20)
DEFAULT_REQUEST_TIMEOUT = int(os.getenv("RULE_TABLE_REQUEST_TIMEOUT", "660") or 660)
LOG_DIR = PROJECT_ROOT / "logs"


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda match: chr(int(match.group(1), 16)), text)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量为 /index.html#structure 主政策生成并回写规则表。")
    parser.add_argument(
        "--structure-api-base-url",
        default=DEFAULT_STRUCTURE_API_BASE_URL,
        help="结构化详情的读取/保存接口基地址，默认读取 backend/PEMatching/.env 的 STRUCTURE_API_BASE_URL。",
    )
    parser.add_argument(
        "--rule-table-api-base-url",
        default=DEFAULT_RULE_TABLE_API_BASE_URL,
        help="structure 页面 Node 网关地址，用于 POST /api/rule-table/generate；本机默认 http://127.0.0.1:3000。",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help="每页拉取政策数量。",
    )
    parser.add_argument(
        "--policy-limit",
        type=int,
        default=0,
        help="只处理前 N 条政策，0 表示不限制。",
    )
    parser.add_argument(
        "--keyword",
        default="",
        help="按主政策标题关键字筛选。",
    )
    parser.add_argument(
        "--structure-review-status",
        default="all",
        help="结构化审核状态筛选，默认 all；可传 1 或 0。",
    )
    parser.add_argument(
        "--review-status",
        default="1",
        help="主政策审核状态筛选，默认 1，对齐 structure 页面当前列表口径。",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="若详情中已存在 ruleTableJson，则跳过该政策。",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="只生成并校验申报条件规则，不写回 structure 详情；用于全量可用性摸底。",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.0,
        help="提交下一条政策前额外等待秒数，便于给 LLM/接口降压。",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="并发处理数，默认 1（串行）；例如 10 表示最多同时处理 10 条政策。",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_REQUEST_TIMEOUT,
        help="规则生成与保存接口的超时时间（秒），默认 660 秒，需高于 Node 网关的 600 秒生成等待窗口。",
    )
    return parser.parse_args(argv)


def fetch_structure_policies(
    client: StructurePolicyClient,
    page_size: int,
    policy_limit: int,
    keyword: str,
    review_status: str,
    structure_review_status: str,
) -> List[Dict[str, Any]]:
    page = 1
    summaries: List[Dict[str, Any]] = []
    normalized_keyword = keyword.strip()
    normalized_limit = max(0, int(policy_limit or 0))

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
                if normalized_keyword in _safe_text(item.get("mainFileTitle") or item.get("directoryTitle") or item.get("policyTitle"))
            ]
        summaries.extend(data)
        if normalized_limit and len(summaries) >= normalized_limit:
            summaries = summaries[:normalized_limit]
            break
        pagination = payload.get("pagination") or {}
        total = int(pagination.get("total") or 0)
        if total and page * max(1, page_size) >= total:
            break
        page += 1

    return summaries


def save_rule_table(
    session: requests.Session,
    base_url: str,
    fusion_policy_id: str,
    rule_table_json: Dict[str, Any],
    timeout: int,
) -> Dict[str, Any]:
    """通过规则表专用保存模式，仅回写 RULE_TABLE_JSON。"""
    payload: Dict[str, Any] = {
        "fusionPolicyId": fusion_policy_id,
        "ruleTableJson": rule_table_json,
        "ruleTableOnly": True,
    }
    response = session.post(
        f"{base_url}/api/fusion-classification/fusion-detail/save",
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    result = response.json()
    if not result.get("success"):
        raise ValueError(result.get("message") or "保存规则表失败")
    return result


def generate_rule_table_via_api(
    session: requests.Session,
    base_url: str,
    fusion_policy_id: str,
    timeout: int,
) -> Dict[str, Any]:
    """调用 structure 页面实际使用的规则表生成接口。

    不能在批处理里直接 import 生成器，否则会绕过 server.js 的 PEMatching
    转发，造成“脚本可跑、详情按钮不可跑”却无法被发现的链路偏差。
    """
    normalized_base_url = _safe_text(base_url).rstrip("/")
    if not normalized_base_url:
        raise ValueError("缺少 structure 接口地址")
    response = session.post(
        f"{normalized_base_url}/api/rule-table/generate",
        params={"fusionPolicyId": fusion_policy_id},
        json={"fusionPolicyId": fusion_policy_id},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("success"):
        raise ValueError(payload.get("message") or "规则表生成接口返回失败")
    result = payload.get("data")
    if not isinstance(result, dict):
        raise ValueError("规则表生成接口未返回 data 对象")
    if not isinstance(result.get("ruleTableJson"), dict):
        raise ValueError("规则表生成接口未返回 ruleTableJson 对象")
    return result


def build_log_paths() -> tuple[Path, Path, Path]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    return (
        LOG_DIR / f"rule_table_batch_{timestamp}.log",
        LOG_DIR / f"rule_table_batch_{timestamp}.json",
        LOG_DIR / f"rule_table_batch_{timestamp}.csv",
    )


def evaluate_rule_table_usability(rule_table_json: Dict[str, Any]) -> Dict[str, Any]:
    """给出规则表能否进入企业匹配链路的明确结论。

    规则生成器已经完成 JSON schema 校验；这里额外统计实际条件项，避免空表被
    误报为可用。``rule``/``unresolved`` 是可直接或待模型补判的正式条件项，
    ``reference``/``supporting`` 也能被规则引擎执行，但只能提供弱证据。
    """
    status_counts = {"rule": 0, "unresolved": 0, "reference": 0, "supporting": 0}
    item_count = 0
    for group_key in ("hardRuleGroups", "softRuleGroups", "specialPassRuleGroups"):
        for group in rule_table_json.get(group_key) or []:
            if not isinstance(group, dict):
                continue
            for item in group.get("conditionFieldItems") or []:
                if not isinstance(item, dict):
                    continue
                item_count += 1
                item_status = _safe_text(item.get("itemStatus"))
                if item_status in status_counts:
                    status_counts[item_status] += 1

    formal_rule_count = status_counts["rule"] + status_counts["unresolved"]
    usable = item_count > 0
    if not usable:
        conclusion = "不可用于规则匹配"
        reason = "规则表没有可执行的申报条件项"
    elif formal_rule_count > 0:
        conclusion = "可用于规则匹配"
        reason = f"已生成 {item_count} 个条件项，其中 {formal_rule_count} 个为正式或待模型补判条件项"
    else:
        conclusion = "可用于规则匹配（需人工复核）"
        reason = f"已生成 {item_count} 个条件项，但全部为参考/辅助证据项"

    return {
        "ruleTableUsable": usable,
        "usabilityConclusion": conclusion,
        "usabilityReason": reason,
        "conditionFieldItemCount": item_count,
        "itemStatusCounts": status_counts,
    }


def build_application_requirement_audit(
    detail_data: Dict[str, Any],
    rule_table_json: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """建立申报要求原文与规则表提取结果的逐编号对照。"""
    condition_records: List[Dict[str, Any]] = []
    source_sections: List[Dict[str, Any]] = []
    for condition_index, condition in enumerate(detail_data.get("conditions") or [], start=1):
        requirement = _safe_text(condition.get("applicationRequirement"))
        matches = list(re.finditer(r"(?<!\d)(\d{1,2})[、.．]", requirement))
        sections: List[Dict[str, Any]] = []
        if matches:
            for section_index, matched in enumerate(matches, start=1):
                content_end = matches[section_index].start() if section_index < len(matches) else len(requirement)
                source_text = _safe_text(requirement[matched.end():content_end])
                if not source_text:
                    continue
                section = {
                    "conditionNo": int(matched.group(1)),
                    "sourceText": source_text,
                    "sectionIndex": section_index,
                }
                sections.append(section)
        elif requirement:
            sections.append(
                {
                    "conditionNo": len(source_sections) + 1,
                    "sourceText": requirement,
                    "sectionIndex": 1,
                }
            )

        for section in sections:
            source_sections.append(
                {
                    **section,
                    "conditionIndex": condition_index,
                    "conditionId": _safe_text(condition.get("id")),
                }
            )
        condition_records.append(
            {
                "conditionIndex": condition_index,
                "conditionId": _safe_text(condition.get("id")),
                "applicableTarget": _safe_text(condition.get("applicableTarget")),
                "applicationInstruction": _safe_text(condition.get("applicationInstruction")),
                "applicationRequirement": requirement,
                "sections": sections,
            }
        )

    extracted_by_condition: Dict[int, List[Dict[str, Any]]] = {}
    extracted_item_count = 0
    table = rule_table_json or {}
    group_labels = {
        "hardRuleGroups": "hard",
        "softRuleGroups": "soft",
        "specialPassRuleGroups": "special_pass",
    }
    for group_key, group_type in group_labels.items():
        for group in table.get(group_key) or []:
            if not isinstance(group, dict):
                continue
            try:
                condition_no = int(group.get("conditionNo"))
            except (TypeError, ValueError):
                continue
            extracted_items: List[Dict[str, Any]] = []
            for item in group.get("conditionFieldItems") or []:
                if not isinstance(item, dict):
                    continue
                extracted_item_count += 1
                extracted_items.append(
                    {
                        "itemId": _safe_text(item.get("itemId")),
                        "sourceFragmentId": _safe_text(item.get("sourceFragmentId")),
                        "itemName": _safe_text(item.get("itemName")),
                        "policyFragment": _safe_text(item.get("policyFragment")),
                        "itemStatus": _safe_text(item.get("itemStatus")),
                        "enterpriseFields": list(item.get("enterpriseFields") or []),
                        "sourceStandardField": item.get("sourceStandardField"),
                        "matchRule": item.get("matchRule") or {},
                        "conditionBranchId": _safe_text(item.get("conditionBranchId")),
                        "conditionBranchText": _safe_text(item.get("conditionBranchText")),
                    }
                )
            extracted_by_condition.setdefault(condition_no, []).append(
                {
                    "groupType": group_type,
                    "evaluationRole": _safe_text(group.get("evaluationRole")),
                    "rawText": _safe_text(group.get("rawText")),
                    "conditionContext": _safe_text(group.get("conditionContext")),
                    "conditionBranchId": _safe_text(group.get("conditionBranchId")),
                    "conditionBranchText": _safe_text(group.get("conditionBranchText")),
                    "conditionOperator": _safe_text(group.get("conditionOperator")),
                    "extractedItems": extracted_items,
                    "unresolvedFragments": list(group.get("unresolvedFragments") or []),
                }
            )

    audited_sections: List[Dict[str, Any]] = []
    covered_condition_numbers = set()
    covered_section_keys = set()
    source_number_counts: Dict[int, int] = {}
    for section in source_sections:
        condition_no = section["conditionNo"]
        source_number_counts[condition_no] = source_number_counts.get(condition_no, 0) + 1
    for section in source_sections:
        condition_no = section["conditionNo"]
        groups = extracted_by_condition.get(condition_no) or []
        if source_number_counts.get(condition_no, 0) > 1:
            compact_source = re.sub(r"\s+", "", _safe_text(section.get("sourceText")))
            groups = [
                group
                for group in groups
                if any(
                    re.sub(r"\s+", "", _safe_text(item.get("policyFragment"))) in compact_source
                    for item in group.get("extractedItems") or []
                    if _safe_text(item.get("policyFragment"))
                )
                or (
                    _safe_text(group.get("rawText"))
                    and re.sub(r"\s+", "", _safe_text(group.get("rawText"))) in compact_source
                )
            ]
        section_item_count = sum(len(group["extractedItems"]) for group in groups)
        section_key = f"R{section['conditionIndex']}-C{condition_no}-S{section['sectionIndex']}"
        if section_item_count:
            covered_condition_numbers.add(condition_no)
            covered_section_keys.add(section_key)
        audited_sections.append(
            {
                **section,
                "sourceSectionKey": section_key,
                "extractionStatus": "extracted" if section_item_count else "not_extracted",
                "extractedItemCount": section_item_count,
                "extractedGroups": groups,
            }
        )

    extracted_without_source = [
        {
            "conditionNo": condition_no,
            "extractedGroups": groups,
        }
        for condition_no, groups in sorted(extracted_by_condition.items())
        if condition_no not in {section["conditionNo"] for section in source_sections}
    ]
    uncovered_sections = [
        section for section in audited_sections if section["sourceSectionKey"] not in covered_section_keys
    ]
    return {
        "rawConditionCount": len(condition_records),
        "sourceRequirementSectionCount": len(source_sections),
        "coveredConditionNoCount": len(covered_condition_numbers),
        "coveredSourceSectionCount": len(covered_section_keys),
        "uncoveredConditionNos": sorted({section["conditionNo"] for section in uncovered_sections}),
        "uncoveredSectionKeys": [section["sourceSectionKey"] for section in uncovered_sections],
        "extractedItemCount": extracted_item_count,
        "conditions": condition_records,
        "sections": audited_sections,
        "extractedWithoutSource": extracted_without_source,
        "definitionNotes": list(table.get("definitionNotes") or []),
        "applicationChecklistGroups": list(table.get("applicationChecklistGroups") or []),
    }


def _audit_csv_summary(audit: Dict[str, Any]) -> Dict[str, str | int]:
    requirements = [
        _safe_text(condition.get("applicationRequirement"))
        for condition in audit.get("conditions") or []
        if _safe_text(condition.get("applicationRequirement"))
    ]
    extracted_summaries: List[str] = []
    for section in audit.get("sections") or []:
        names = [
            _safe_text(item.get("itemName"))
            for group in section.get("extractedGroups") or []
            for item in group.get("extractedItems") or []
            if _safe_text(item.get("itemName"))
        ]
        if names:
            extracted_summaries.append(f"C{section.get('conditionNo')}:{'、'.join(names)}")
    return {
        "申报条件原文": "\n\n".join(requirements),
        "原始申报条件编号数": int(audit.get("sourceRequirementSectionCount", 0)),
        "已覆盖编号数": int(audit.get("coveredConditionNoCount", 0)),
        "未提取编号": ",".join(str(value) for value in audit.get("uncoveredConditionNos") or []),
        "提取条件摘要": "；".join(extracted_summaries),
    }


def write_csv_report(path: Path, results: List[Dict[str, Any]]) -> None:
    """写出便于业务人员查看的逐政策结论表。"""
    fieldnames = [
        "序号",
        "融合政策ID",
        "政策标题",
        "结构化申报条件数",
        "申报条件原文",
        "原始申报条件编号数",
        "已覆盖编号数",
        "未提取编号",
        "提取条件摘要",
        "申报条件规则是否可用",
        "规则结论",
        "结论说明",
        "正式或待模型补判项数",
        "参考项数",
        "辅助项数",
        "条件项总数",
        "是否已写回结构页",
        "处理状态",
        "失败原因",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for item in results:
            item_counts = item.get("itemStatusCounts") or {}
            audit_summary = _audit_csv_summary(item.get("applicationRequirementAudit") or {})
            is_skipped = item.get("status") == "skipped"
            writer.writerow(
                {
                    "序号": item.get("index", ""),
                    "融合政策ID": item.get("fusionPolicyId", ""),
                    "政策标题": item.get("title", ""),
                    "结构化申报条件数": item.get("structuredConditionCount", 0),
                    **audit_summary,
                    "申报条件规则是否可用": (
                        (
                            "未复核（已存在规则表）"
                            if item.get("message") == "已存在 ruleTableJson"
                            else "不适用（无可匹配规则）"
                        )
                        if is_skipped
                        else ("可以" if item.get("ruleTableUsable") else "不可以")
                    ),
                    "规则结论": "已跳过" if is_skipped else item.get("usabilityConclusion", ""),
                    "结论说明": item.get("message", "") if is_skipped else item.get("usabilityReason", ""),
                    "正式或待模型补判项数": int(item_counts.get("rule", 0)) + int(item_counts.get("unresolved", 0)),
                    "参考项数": item_counts.get("reference", 0),
                    "辅助项数": item_counts.get("supporting", 0),
                    "条件项总数": item.get("conditionFieldItemCount", 0),
                    "是否已写回结构页": "是" if item.get("writeBackStatus") == "saved" else "否",
                    "处理状态": item.get("status", ""),
                    "失败原因": item.get("message", ""),
                }
            )


def process_policy(
    *,
    index: int,
    total: int,
    summary: Dict[str, Any],
    structure_api_base_url: str,
    rule_table_api_base_url: str,
    timeout: int,
    skip_existing: bool,
    check_only: bool,
    log: Callable[[str], None],
) -> Dict[str, Any]:
    """生成并按需写回一条政策的规则表。

    每个并发任务独立创建 HTTP 客户端和会话，避免 requests.Session 被多个线程共享。
    """
    fusion_policy_id = _safe_text(summary.get("id"))
    title = _safe_text(summary.get("mainFileTitle") or summary.get("directoryTitle") or summary.get("policyTitle"))
    if not fusion_policy_id:
        result_item = {
            "index": index,
            "fusionPolicyId": "",
            "title": title,
            "status": "failed",
            "message": "缺少政策 ID",
        }
        log(f"[{index}/{total}] 跳过：缺少 fusionPolicyId，title={title or '-'}")
        return result_item

    log(f"[{index}/{total}] 开始处理 fusionPolicyId={fusion_policy_id} title={title or '-'}")
    client = StructurePolicyClient(base_url=structure_api_base_url, timeout=max(30, timeout))
    session = requests.Session()
    requirement_audit: Dict[str, Any] = {}
    try:
        detail_data = client.fetch_policy_detail(fusion_policy_id)
        structured_condition_count = len(detail_data.get("conditions") or [])
        requirement_audit = build_application_requirement_audit(detail_data)
        if skip_existing and detail_data.get("ruleTableJson"):
            requirement_audit = build_application_requirement_audit(detail_data, detail_data.get("ruleTableJson"))
            result_item = {
                "index": index,
                "fusionPolicyId": fusion_policy_id,
                "title": title,
                "status": "skipped",
                "message": "已存在 ruleTableJson",
                "structuredConditionCount": structured_condition_count,
                "applicationRequirementAudit": requirement_audit,
            }
            log(f"[{index}/{total}] 跳过：已存在 ruleTableJson")
            return result_item

        generated = generate_rule_table_via_api(
            session=session,
            fusion_policy_id=fusion_policy_id,
            base_url=rule_table_api_base_url,
            timeout=max(30, timeout),
        )
        rule_table_json = generated.get("ruleTableJson") or {}
        requirement_audit = build_application_requirement_audit(detail_data, rule_table_json)
        stats = generated.get("stats") or {}
        usability = evaluate_rule_table_usability(rule_table_json)
        if stats.get("noMatchableRules") or not usability["ruleTableUsable"]:
            write_back_status = "not_requested" if check_only else "saved"
            write_back_message = "未提取到可用于规则匹配的条件项。"
            if not check_only:
                try:
                    save_rule_table(
                        session=session,
                        base_url=structure_api_base_url,
                        fusion_policy_id=fusion_policy_id,
                        rule_table_json=rule_table_json,
                        timeout=max(30, timeout),
                    )
                    write_back_message += "已写回空规则表，覆盖旧规则表。"
                except Exception as exc:
                    write_back_status = "failed"
                    write_back_message += f"写回失败：{exc}"

            status = "checked_no_matchable_rules" if check_only else (
                "saved_no_matchable_rules" if write_back_status == "saved" else "generated_not_saved"
            )
            result_item = {
                "index": index,
                "fusionPolicyId": fusion_policy_id,
                "title": title,
                "status": status,
                "structuredConditionCount": structured_condition_count,
                "writeBackStatus": write_back_status,
                "message": write_back_message,
                "stats": stats,
                "applicationRequirementAudit": requirement_audit,
                **usability,
            }
            log(
                f"[{index}/{total}] 完成：无可匹配规则，"
                f"写回={write_back_status}，"
                f"items={stats.get('itemCount', 0)}"
            )
            return result_item

        write_back_status = "not_requested" if check_only else "saved"
        write_back_message = ""
        if not check_only:
            try:
                save_rule_table(
                    session=session,
                    base_url=structure_api_base_url,
                    fusion_policy_id=fusion_policy_id,
                    rule_table_json=rule_table_json,
                    timeout=max(30, timeout),
                )
            except Exception as exc:
                write_back_status = "failed"
                write_back_message = str(exc)

        status = "success" if write_back_status in {"saved", "not_requested"} else "generated_not_saved"
        result_item = {
            "index": index,
            "fusionPolicyId": fusion_policy_id,
            "title": title,
            "status": status,
            "structuredConditionCount": structured_condition_count,
            "writeBackStatus": write_back_status,
            "message": write_back_message,
            "stats": stats,
            "applicationRequirementAudit": requirement_audit,
            **usability,
        }
        log(
            f"[{index}/{total}] 完成："
            f"规则={usability['usabilityConclusion']}，"
            f"写回={write_back_status}，"
            f"hard={stats.get('hardGroupCount', 0)}, "
            f"special={stats.get('specialPassGroupCount', 0)}, "
            f"soft={stats.get('softGroupCount', 0)}, "
            f"definitions={stats.get('definitionNoteCount', 0)}, "
            f"checklists={stats.get('applicationChecklistGroupCount', 0)}, "
            f"items={stats.get('itemCount', 0)}"
        )
        return result_item
    except Exception as exc:
        result_item = {
            "index": index,
            "fusionPolicyId": fusion_policy_id,
            "title": title,
            "status": "failed",
            "structuredConditionCount": 0,
            "writeBackStatus": "not_attempted",
            "ruleTableUsable": False,
            "usabilityConclusion": "不可用于规则匹配",
            "usabilityReason": "规则表生成或结构化详情读取失败",
            "applicationRequirementAudit": requirement_audit,
            "message": str(exc),
        }
        log(f"[{index}/{total}] 失败：{exc}")
        return result_item
    finally:
        session.close()


def main() -> int:
    args = parse_args()
    structure_api_base_url = _safe_text(args.structure_api_base_url).rstrip("/")
    if not structure_api_base_url:
        raise SystemExit("缺少 structure 接口地址，请通过 --structure-api-base-url 或 .env 的 STRUCTURE_API_BASE_URL 指定。")
    rule_table_api_base_url = _safe_text(args.rule_table_api_base_url).rstrip("/")
    if not rule_table_api_base_url:
        raise SystemExit("缺少规则表生成接口地址，请通过 --rule-table-api-base-url 或 .env 的 RULE_TABLE_API_BASE_URL 指定。")

    log_path, summary_path, csv_path = build_log_paths()
    log_lock = Lock()

    def log(message: str) -> None:
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
        with log_lock:
            print(line, flush=True)
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    run_mode = "仅检查，不写回" if args.check_only else "生成并写回"
    log(
        "开始批量生成规则表，"
        f"structureApiBaseUrl={structure_api_base_url}，"
        f"ruleTableApiBaseUrl={rule_table_api_base_url}，"
        f"模式={run_mode}，"
        f"并发数={max(1, args.workers)}"
    )
    log(
        "列表过滤条件："
        f"reviewStatus={args.review_status}, "
        f"requireSourcePolicyId=1, "
        f"structureReviewStatus={args.structure_review_status}, "
        f"keyword={_safe_text(args.keyword) or '-'}"
    )

    list_client = StructurePolicyClient(base_url=structure_api_base_url, timeout=max(30, args.timeout))
    summaries = fetch_structure_policies(
        client=list_client,
        page_size=max(1, args.page_size),
        policy_limit=max(0, args.policy_limit),
        keyword=_safe_text(args.keyword),
        review_status=_safe_text(args.review_status) or "1",
        structure_review_status=_safe_text(args.structure_review_status) or "all",
    )
    log(f"待处理主政策数量={len(summaries)}")

    worker_count = max(1, args.workers)
    results_by_index: Dict[int, Dict[str, Any]] = {}

    def submit_policy(index: int, summary: Dict[str, Any]) -> Dict[str, Any]:
        return process_policy(
            index=index,
            total=len(summaries),
            summary=summary,
            structure_api_base_url=structure_api_base_url,
            rule_table_api_base_url=rule_table_api_base_url,
            timeout=args.timeout,
            skip_existing=bool(args.skip_existing),
            check_only=bool(args.check_only),
            log=log,
        )

    if worker_count == 1:
        for index, summary in enumerate(summaries, start=1):
            results_by_index[index] = submit_policy(index, summary)
            if args.sleep_seconds > 0 and index < len(summaries):
                time.sleep(args.sleep_seconds)
    else:
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="rule-table") as executor:
            futures: Dict[Future[Dict[str, Any]], int] = {}
            for index, summary in enumerate(summaries, start=1):
                futures[executor.submit(submit_policy, index, summary)] = index
                if args.sleep_seconds > 0 and index < len(summaries):
                    time.sleep(args.sleep_seconds)
            for future in as_completed(futures):
                index = futures[future]
                try:
                    results_by_index[index] = future.result()
                except Exception as exc:
                    results_by_index[index] = {
                        "index": index,
                        "fusionPolicyId": "",
                        "title": "",
                        "status": "failed",
                        "writeBackStatus": "not_attempted",
                        "ruleTableUsable": False,
                        "usabilityConclusion": "不可用于规则匹配",
                        "usabilityReason": "并发任务异常退出",
                        "message": str(exc),
                    }
                    log(f"[{index}/{len(summaries)}] 失败：并发任务异常退出：{exc}")

    results = [results_by_index[index] for index in sorted(results_by_index)]
    success_count = sum(item.get("status") in {"success", "saved_no_matchable_rules"} for item in results)
    skip_count = sum(item.get("status") == "skipped" for item in results)
    write_back_failed_count = sum(item.get("writeBackStatus") == "failed" for item in results)
    fail_count = sum(
        item.get("status") == "failed" or item.get("writeBackStatus") == "failed"
        for item in results
    )
    usable_count = sum(
        item.get("status") != "skipped" and bool(item.get("ruleTableUsable"))
        for item in results
    )

    summary_payload = {
        "runAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "structureApiBaseUrl": structure_api_base_url,
        "ruleTableApiBaseUrl": rule_table_api_base_url,
        "filters": {
            "reviewStatus": _safe_text(args.review_status) or "1",
            "requireSourcePolicyId": "1",
            "structureReviewStatus": _safe_text(args.structure_review_status) or "all",
            "keyword": _safe_text(args.keyword),
            "policyLimit": max(0, args.policy_limit),
            "skipExisting": bool(args.skip_existing),
            "checkOnly": bool(args.check_only),
            "workers": worker_count,
        },
        "summary": {
            "total": len(summaries),
            "success": success_count,
            "skipped": skip_count,
            "failed": fail_count,
            "ruleTableUsable": usable_count,
            "writeBackFailed": write_back_failed_count,
        },
        "results": results,
    }
    summary_path.write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv_report(csv_path, results)
    log(
        "批量生成结束："
        f"total={len(summaries)}, success={success_count}, skipped={skip_count}, failed={fail_count}, "
        f"ruleTableUsable={usable_count}, writeBackFailed={write_back_failed_count}"
    )
    log(f"详细日志：{log_path}")
    log(f"结果汇总：{summary_path}")
    log(f"业务结论表：{csv_path}")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
