# -*- coding: utf-8 -*-
"""匹配批次的详细链路证据与简易报告导出。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from service import fields as F
from service import policy_index as pi
from service.raw_enterprise_mapping import map_record_to_chinese


DETAIL_REPORT_NAME = "推荐链路证据.xlsx"
SIMPLIFIED_REPORT_NAME = "推荐链路证据_简易版.xlsx"
VIEWER_REPORT_NAME = "推荐链路证据.html"

_HEADER_FILL = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
_WARNING_FILL = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
_EXCLUSION_FILL = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid")
_TITLE_FILL = PatternFill(start_color="17365D", end_color="17365D", fill_type="solid")
_PASS_FILL = PatternFill(start_color="E2F0D9", end_color="E2F0D9", fill_type="solid")
_FAIL_FILL = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid")
_MISSING_FILL = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")


def _cell_text(value: Any) -> str:
    """将嵌套数据转为可读且不会被 Excel 解释为公式的文本。"""
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, ensure_ascii=False, default=str)
    text = F.text(value)
    return f"'{text}" if text.startswith(("=", "+", "-", "@")) else text


def _join(values: Iterable[Any], separator: str = "；") -> str:
    return separator.join(_cell_text(value) for value in values if _cell_text(value))


def _style_header(sheet) -> None:
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    sheet.row_dimensions[1].height = 32
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    # 报告既可在 Excel 内筛选，也可直接导出/打印；默认纵向页面会把右侧证据列拆开。
    sheet.sheet_view.showGridLines = False
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = sheet.ORIENTATION_LANDSCAPE
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A3
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.print_title_rows = "1:1"
    sheet.page_margins.left = 0.2
    sheet.page_margins.right = 0.2
    sheet.page_margins.top = 0.35
    sheet.page_margins.bottom = 0.35


def _set_widths(sheet, widths: List[int]) -> None:
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)


def _append_table(sheet, headers: List[str], rows: Iterable[Iterable[Any]], widths: List[int]) -> None:
    sheet.append(headers)
    for row in rows:
        sheet.append(list(row))
    _style_header(sheet)
    _set_widths(sheet, widths)


def _append_review_table(
    sheet,
    title: str,
    subtitle: str,
    headers: List[str],
    rows: Iterable[Iterable[Any]],
    widths: List[int],
    *,
    status_column: int | None = None,
) -> None:
    """写入面向业务审核的表格：标题、口径和表头保持在首屏。"""
    max_column = len(headers)
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max_column)
    title_cell = sheet.cell(row=1, column=1, value=title)
    title_cell.font = Font(bold=True, color="FFFFFF", size=14)
    title_cell.fill = _TITLE_FILL
    title_cell.alignment = Alignment(vertical="center")
    sheet.row_dimensions[1].height = 28

    sheet.merge_cells(start_row=2, start_column=1, end_row=2, end_column=max_column)
    subtitle_cell = sheet.cell(row=2, column=1, value=subtitle)
    subtitle_cell.font = Font(color="404040", italic=True, size=10)
    subtitle_cell.fill = PatternFill(start_color="D9EAF7", end_color="D9EAF7", fill_type="solid")
    subtitle_cell.alignment = Alignment(vertical="center", wrap_text=True)
    sheet.row_dimensions[2].height = 30

    for column, header in enumerate(headers, start=1):
        sheet.cell(row=3, column=column, value=header)
    for row in rows:
        sheet.append(list(row))

    for cell in sheet[3]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    sheet.row_dimensions[3].height = 34
    sheet.freeze_panes = "A4"
    sheet.auto_filter.ref = f"A3:{get_column_letter(max_column)}{max(sheet.max_row, 3)}"
    sheet.sheet_view.showGridLines = False
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = sheet.ORIENTATION_LANDSCAPE
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A3
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.print_title_rows = "1:3"
    sheet.page_margins.left = 0.2
    sheet.page_margins.right = 0.2
    sheet.page_margins.top = 0.35
    sheet.page_margins.bottom = 0.35
    _set_widths(sheet, widths)

    if status_column is not None:
        for row in range(4, sheet.max_row + 1):
            value = F.text(sheet.cell(row=row, column=status_column).value)
            fill = None
            if value in {"满足", "允许直推"}:
                fill = _PASS_FILL
            elif value in {"不满足", "不推荐"}:
                fill = _FAIL_FILL
            elif value in {"缺失待核验", "待补齐", "不可计算"}:
                fill = _MISSING_FILL
            if fill:
                sheet.cell(row=row, column=status_column).fill = fill


def _rule_rows(data: Dict[str, Any], source: str, enterprise_name: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for channel_name, channel in (data.get("channelResults") or {}).items():
        for status in ("matched", "unmatched", "skipped"):
            for rule in channel.get(status) or []:
                if not isinstance(rule, dict):
                    continue
                signals = rule.get("signals") or {}
                rows.append({
                    "source": source,
                    "enterpriseName": enterprise_name,
                    "channel": channel_name,
                    "status": status,
                    "ruleId": F.text(rule.get("ruleId")),
                    "name": F.text(rule.get("name")),
                    "priority": F.text(rule.get("priority")),
                    "details": _join(rule.get("details") or []),
                    "skipReason": _cell_text(rule.get("skipReason")),
                    "missingFields": _join(rule.get("missingFields") or []),
                    "keywords": _join(signals.get("keywords") or []),
                    "signals": _cell_text(signals),
                    "policyPackDesc": _cell_text(rule.get("policyPackDesc")),
                    "chainBonus": rule.get("chainBonus"),
                    "chainStage": _cell_text(rule.get("chainStage")),
                })
    return rows


def _recommendation_evidence(recommendation: Dict[str, Any]) -> List[Dict[str, Any]]:
    evidence = recommendation.get("policyEvidence") or []
    if evidence:
        return [item for item in evidence if isinstance(item, dict)]
    # 兼容历史结果：仅用于展示，不将旧的 ruleId 当作新的关键词证据。
    return [{"ruleId": rule_id, "matchedKeywords": [], "basis": "历史结果未记录关键词依据"}
            for rule_id in (recommendation.get("matchedRules") or [])]


def _recommendation_rows(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in results:
        if not item.get("success"):
            continue
        data = item.get("data") or {}
        enterprise = data.get("enterprise") or {}
        eligibility_by_policy = {
            F.text(entry.get("fusionPolicyId")): entry
            for entry in ((data.get("eligibilityResult") or {}).get("eligibilityEvaluations") or [])
            if isinstance(entry, dict)
        }
        for recommendation in data.get("recommendations") or []:
            if not isinstance(recommendation, dict):
                continue
            evidence = _recommendation_evidence(recommendation)
            rows.append({
                "source": F.text(item.get("source")),
                "enterpriseName": F.text(enterprise.get("enterpriseName") or item.get("enterpriseName")),
                "recommendation": recommendation,
                "evidence": evidence,
                "eligibility": eligibility_by_policy.get(F.text(recommendation.get("fusionPolicyId"))) or {},
            })
    return rows


def _exclusion_rows(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in results:
        if not item.get("success"):
            continue
        data = item.get("data") or {}
        enterprise_name = F.text((data.get("enterprise") or {}).get("enterpriseName") or item.get("enterpriseName"))
        for entry in data.get("eligibilityExclusions") or []:
            if not isinstance(entry, dict):
                continue
            rows.append({
                "source": F.text(item.get("source")),
                "enterpriseName": enterprise_name,
                "kind": "资格排除",
                "policyId": F.text(entry.get("fusionPolicyId")),
                "title": F.text(entry.get("policyTitle")),
                "status": F.text(entry.get("gateStatus") or entry.get("reason")),
                "action": F.text(entry.get("action")),
                "reasons": _join(entry.get("reasons") or []) or _join(entry.get("gateReasons") or []),
            })
    return rows


def _overview_rows(results: List[Dict[str, Any]], summary: Dict[str, Any]) -> List[List[Any]]:
    rows = [
        ["企业总数", len(results)],
        ["成功企业", sum(1 for item in results if item.get("success"))],
        ["失败企业", sum(1 for item in results if not item.get("success"))],
        ["降级企业", summary.get("degraded")],
        ["G0 拦截", summary.get("gateBlocked")],
        ["无推荐", summary.get("noRecommendation")],
        ["资格已评估企业", (summary.get("eligibility") or {}).get("evaluatedEnterprises")],
        ["资格评估失败企业", (summary.get("eligibility") or {}).get("failedEnterprises")],
        ["耗时 P50（ms）", (summary.get("elapsedMs") or {}).get("p50")],
        ["耗时 P95（ms）", (summary.get("elapsedMs") or {}).get("p95")],
    ]
    for band, count in sorted((summary.get("bands") or {}).items()):
        rows.append([f"档位：{band}", count])
    for status, count in sorted(((summary.get("eligibility") or {}).get("statuses") or {}).items()):
        rows.append([f"资格门禁：{status}", count])
    return rows


def _tag_values(tag: Dict[str, Any], dimension: str) -> str:
    if dimension == "A":
        return _join([tag.get("primary"), *(tag.get("secondary") or [])], "、")
    if dimension == "B":
        return _join(tag.get("values") or [], "、")
    if dimension == "C":
        return _join([*(tag.get("values") or []), *(tag.get("subValues") or [])], "、")
    return _cell_text(tag.get("value"))


def _policy_tag_rows(recommendation_rows: List[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], List[List[Any]]]:
    tags_index = pi.load_policy_tags()
    policy_ids = sorted({F.text(row["recommendation"].get("fusionPolicyId")) for row in recommendation_rows})
    records = {policy_id: tags_index.get(policy_id) or {} for policy_id in policy_ids}
    rows: List[List[Any]] = []
    for policy_id in policy_ids:
        record = records[policy_id]
        tags = record.get("tags") or {}
        for dimension in ("A", "B", "C", "D", "E", "F"):
            tag = tags.get(dimension) or {}
            rows.append([
                policy_id, _cell_text(record.get("title")), dimension,
                _tag_values(tag, dimension), _cell_text(tag.get("confidence")), _cell_text(tag.get("basis")),
            ])
    return records, rows


def _enterprise_info_rows(results: List[Dict[str, Any]]) -> List[List[Any]]:
    qualification_labels = (
        ("isTechSme", "科技型中小企业"),
        ("isHighTechEnterprise", "国家级高企"),
        ("isInnovativeSme", "创新型中小企业"),
        ("isSpecializedSme", "专精特新"),
        ("isSpecializedLittleGiant", "小巨人"),
        ("isTechLeaderEnterprise", "科技领军(瞪羚)"),
    )

    def bool_text(value: Any) -> str:
        return "是" if value is True else "否" if value is False else "未知"

    rows: List[List[Any]] = []
    for item in results:
        if not item.get("success"):
            continue
        data = item.get("data") or {}
        enterprise = data.get("enterprise") or {}
        raw = item.get("enterpriseInput") or {}
        tags = enterprise.get("tags") or {}
        metrics = enterprise.get("metrics") or {}
        qualifications = enterprise.get("qualifications") or {}
        industry = _join([raw.get("industryLevel1"), raw.get("industryLevel2")], " / ")
        regions = _join(tags.get("regions") or [], "、")
        rows.append([
            _cell_text(enterprise.get("enterpriseName")), industry,
            _join(tags.get("C") or [], "、"), _join(tags.get("B") or [], "、"),
            _cell_text(raw.get("registerAddress")), regions, _cell_text(raw.get("registerType")),
            _cell_text(metrics.get("registerDate")), _cell_text(metrics.get("metricsYear")),
            metrics.get("employeeCount"), metrics.get("rdPersonnelCount"), metrics.get("revenueWan"),
            metrics.get("rdExpenseWan"), metrics.get("netAssetsWan"), metrics.get("newRecruitCount"),
            metrics.get("ipClass1Count"), metrics.get("ipClass2Count"),
            *(bool_text(qualifications.get(key)) for key, _ in qualification_labels),
            _cell_text(enterprise.get("tagBasis")), _cell_text(enterprise.get("dataCompleteness")),
        ])
    return rows


_EVAL_STATUS_LABELS = {
    "pass": "满足",
    "fail": "不满足",
    "missing": "缺失待核验",
    "not_evaluable": "不可计算",
    "skipped": "已跳过",
}

_EVIDENCE_TIER_LABELS = {
    "A1_exact": "A1 精确企业事实",
    "A2_derived": "A2 确定性推导",
    "B_supporting": "B 辅助事实（不可严格直推）",
    "U_missing": "U 无企业事实",
    "R_rejected": "R 已拒绝证据",
}

_GATE_STATUS_LABELS = {
    "direct_push": "允许直推",
    "needs_supplement": "待补齐",
    "not_recommended": "不推荐",
    "not_evaluable": "不可计算",
}

_OPERATOR_LABELS = {
    "gte": "≥",
    "gt": ">",
    "lte": "≤",
    "lt": "<",
    "eq": "=",
    "neq": "≠",
    "in": "属于",
    "contains": "包含",
}


def _score_text(score: Any) -> str:
    value = F.to_float(score)
    return "—" if value is None else f"{value * 100:.2f}"


def _display_value(value: Any, unit: Any = "") -> str:
    """展示企业值时保留空值语义，避免把无事实误读为零。"""
    if value is None or F.text(value) == "":
        return "—"
    if isinstance(value, (dict, list, tuple)):
        value = _cell_text(value)
    suffix = F.text(unit)
    return f"{value}{suffix}" if suffix else F.text(value)


def _policy_requirement(detail: Dict[str, Any], group: Dict[str, Any]) -> str:
    """将结构化阈值和政策原文一起呈现，便于人工核对规则口径。"""
    target = detail.get("targetValue")
    operator = _OPERATOR_LABELS.get(F.text(detail.get("operator")), F.text(detail.get("operator")))
    target_text = _display_value(target, detail.get("targetUnit")) if target is not None else ""
    structured = f"{operator} {target_text}".strip() if target_text else ""
    raw_text = F.text(group.get("rawText"))
    if structured and raw_text:
        return f"{structured}\n政策原文：{raw_text}"
    return structured or raw_text or "—"


def _condition_review_records(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """构造简易版和 HTML 共用的逐条件评分审阅账目。"""
    rows: List[Dict[str, Any]] = []
    for context in _eligibility_contexts(results):
        entry, group, detail = context["entry"], context["group"], context["detail"]
        evidence = detail.get("evidence") or {}
        model_evidence = evidence.get("modelEvidence") or {}
        enterprise_value = detail.get("enterpriseValue")
        if enterprise_value is None:
            enterprise_value = evidence.get("value")
        enterprise_unit = detail.get("enterpriseUnit") or evidence.get("unit")
        normalised_value = detail.get("normalisedEnterpriseValue")
        target_unit = detail.get("targetUnit")
        converted = "—"
        if normalised_value is not None:
            converted = _display_value(normalised_value, target_unit)
        elif detail.get("conversionFormula"):
            converted = F.text(detail.get("conversionFormula"))
        conversion_formula = F.text(detail.get("conversionFormula"))
        if conversion_formula and converted != conversion_formula:
            converted = f"{converted}\n换算：{conversion_formula}"
        paths = _join(evidence.get("sourcePaths") or [], "\n")
        evidence_parts = [
            _EVIDENCE_TIER_LABELS.get(F.text(evidence.get("evidenceTier")), F.text(evidence.get("evidenceTier"))),
            paths,
            F.text(model_evidence.get("evidenceText")),
        ]
        rows.append({
            "enterpriseName": context["enterpriseName"],
            "policyId": entry.get("fusionPolicyId"),
            "policyTitle": _cell_text(entry.get("policyTitle")),
            "conditionNo": group.get("conditionNo") or detail.get("itemId") or "—",
            "conditionText": F.text(group.get("rawText")) or F.text(detail.get("itemName")) or "—",
            "itemName": F.text(detail.get("itemName")) or "—",
            "requirement": _policy_requirement(detail, group),
            "enterpriseValue": _display_value(enterprise_value, enterprise_unit),
            "convertedValue": converted,
            "evidence": "\n".join(part for part in evidence_parts if part),
            "status": _EVAL_STATUS_LABELS.get(F.text(detail.get("evalStatus")), F.text(detail.get("evalStatus")) or "—"),
            "score": (round(float(detail["score"]) * 100, 2) if detail.get("score") is not None else None),
            "contributes": "参与" if detail.get("contributesToScore") else "不参与",
            "passSource": F.text(detail.get("passSource")) or "—",
            "message": F.text(detail.get("message")) or F.text(evidence.get("reason")) or "—",
        })
    return rows


def _condition_formula_record(entry: Dict[str, Any]) -> Dict[str, Any]:
    """将服务端已经实际使用的单项条件分组成可审计的最终分公式。"""
    scored = [
        detail for detail in entry.get("conditionDetails") or []
        if isinstance(detail, dict) and detail.get("contributesToScore") and detail.get("score") is not None
    ]
    component_scores = [_score_text(detail.get("score")) for detail in scored]
    denominator = len(component_scores)
    average = (sum(float(detail["score"]) for detail in scored) / denominator * 100) if denominator else None
    average_formula = (
        f"({ ' + '.join(component_scores) }) ÷ {denominator} = {average:.2f}"
        if average is not None else "无参与计分的条件"
    )
    final_score = entry.get("conditionMatchScore")
    if entry.get("hardPass") is False:
        final_formula = f"硬性条件明确失败，最终条件匹配分 = 0.00（平均账目：{average_formula}）"
    elif entry.get("specialPassHit"):
        final_formula = f"max({average_formula}, 90.00) = {_score_text(final_score)}"
    else:
        final_formula = f"最终条件匹配分 = {average_formula}"
    return {
        "conditionScore": final_score,
        "scoredCount": denominator,
        "componentScores": " + ".join(component_scores) or "—",
        "averageFormula": average_formula,
        "finalFormula": final_formula,
    }


def _condition_score_summary_rows(results: List[Dict[str, Any]]) -> List[List[Any]]:
    rows: List[List[Any]] = []
    for item in results:
        if not item.get("success"):
            continue
        enterprise_name = F.text(((item.get("data") or {}).get("enterprise") or {}).get("enterpriseName") or item.get("enterpriseName"))
        for entry in ((item.get("data") or {}).get("eligibilityResult") or {}).get("eligibilityEvaluations") or []:
            if not isinstance(entry, dict):
                continue
            formula = _condition_formula_record(entry)
            rows.append([
                enterprise_name, entry.get("fusionPolicyId"), _cell_text(entry.get("policyTitle")), formula["conditionScore"],
                _GATE_STATUS_LABELS.get(F.text(entry.get("gateStatus")), _cell_text(entry.get("gateStatus"))),
                "是" if entry.get("directPushAllowed") else "否", formula["scoredCount"], formula["componentScores"],
                formula["averageFormula"], formula["finalFormula"], entry.get("pendingConditionCount"),
                entry.get("failedConditionCount"), entry.get("defaultMissingCount"), entry.get("evidenceCoverage"),
            ])
    return rows


def _mapped_enterprise_rows(results: List[Dict[str, Any]]) -> List[List[Any]]:
    """将中文映射后的完整原始企业数据按子表记录展开到便于筛选的 Excel 行。"""
    rows: List[List[Any]] = []

    def append_leaf(enterprise_name: str, group: str, record_no: str, field_name: str, value: Any) -> None:
        if isinstance(value, dict):
            for nested_name, nested_value in value.items():
                append_leaf(enterprise_name, group, record_no, f"{field_name} / {nested_name}", nested_value)
            return
        if isinstance(value, list):
            for index, nested_value in enumerate(value, start=1):
                append_leaf(enterprise_name, group, f"{record_no} / 第{index}条" if record_no else f"第{index}条", field_name, nested_value)
            return
        rows.append([enterprise_name, group, record_no or "—", field_name, "—" if value is None else value])

    for item in results:
        if not item.get("success"):
            continue
        enterprise_name = F.text(((item.get("data") or {}).get("enterprise") or {}).get("enterpriseName") or item.get("enterpriseName"))
        raw = item.get("enterpriseInput") or {}
        if not isinstance(raw, dict):
            continue
        mapped = map_record_to_chinese(raw)
        for field_name, value in mapped.items():
            if isinstance(value, list):
                for index, record in enumerate(value, start=1):
                    append_leaf(enterprise_name, field_name, f"第{index}条", "记录", record)
            else:
                append_leaf(enterprise_name, "企业基本信息", "—", field_name, value)
    return rows


def _eligibility_contexts(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """以“企业 + 政策 + 条件”为粒度组装资格证据，保留规则组上下文。"""
    rows: List[Dict[str, Any]] = []
    for item in results:
        if not item.get("success"):
            continue
        data = item.get("data") or {}
        enterprise_name = F.text((data.get("enterprise") or {}).get("enterpriseName") or item.get("enterpriseName"))
        for entry in ((data.get("eligibilityResult") or {}).get("eligibilityEvaluations") or []):
            if not isinstance(entry, dict):
                continue
            details_by_key = {
                F.text(detail.get("semanticItemKey")): detail
                for detail in entry.get("conditionDetails") or []
                if isinstance(detail, dict)
            }
            seen_keys = set()
            for group in entry.get("ruleGroupDetails") or []:
                if not isinstance(group, dict):
                    continue
                for group_item in group.get("items") or []:
                    if not isinstance(group_item, dict):
                        continue
                    key = F.text(group_item.get("semanticItemKey"))
                    seen_keys.add(key)
                    rows.append({
                        "source": F.text(item.get("source")),
                        "enterpriseName": enterprise_name,
                        "entry": entry,
                        "group": group,
                        "detail": details_by_key.get(key) or group_item,
                    })
            # 兼容没有 group 明细的历史归档，不能因展示升级丢掉条件记录。
            for key, detail in details_by_key.items():
                if key in seen_keys:
                    continue
                rows.append({
                    "source": F.text(item.get("source")),
                    "enterpriseName": enterprise_name,
                    "entry": entry,
                    "group": {},
                    "detail": detail,
                })
    return rows


def _eligibility_condition_rows(results: List[Dict[str, Any]], *, include_source: bool) -> List[List[Any]]:
    rows: List[List[Any]] = []
    for context in _eligibility_contexts(results):
        entry, group, detail = context["entry"], context["group"], context["detail"]
        evidence = detail.get("evidence") or {}
        model_evidence = evidence.get("modelEvidence") or {}
        value = detail.get("enterpriseValue")
        if value is None:
            value = evidence.get("value")
        value = _cell_text(value) if not isinstance(value, (int, float)) else value
        unit = _cell_text(detail.get("enterpriseUnit") or evidence.get("unit"))
        base = [
            context["enterpriseName"], entry.get("fusionPolicyId"), _cell_text(entry.get("policyTitle")),
            _cell_text(group.get("groupType")), group.get("conditionNo"), _cell_text(group.get("conditionBranchId")),
            _cell_text(group.get("rawText")), detail.get("itemId"), _cell_text(detail.get("itemName")),
            _cell_text(detail.get("operator")), value, unit, detail.get("targetValue"), _cell_text(detail.get("targetUnit")),
            _cell_text(detail.get("normalisedEnterpriseValue")), _cell_text(detail.get("normalisedTargetValue")),
            _cell_text(detail.get("conversionFormula")),
            _EVAL_STATUS_LABELS.get(F.text(detail.get("evalStatus")), _cell_text(detail.get("evalStatus"))),
            _score_text(detail.get("score")),
            "参与" if detail.get("contributesToScore") else "不参与",
            _cell_text(detail.get("passSource")),
            _EVIDENCE_TIER_LABELS.get(F.text(evidence.get("evidenceTier")), _cell_text(evidence.get("evidenceTier"))),
            _join(evidence.get("sourcePaths") or [], "\n"),
            _cell_text(evidence.get("semanticCategory")),
            _cell_text(model_evidence.get("factStatus")), _cell_text(model_evidence.get("evidenceText")),
            _cell_text(evidence.get("reason")), _cell_text(detail.get("message")),
        ]
        rows.append(([context["source"]] if include_source else []) + base)
    return rows


def _eligibility_formula_rows(results: List[Dict[str, Any]], *, include_source: bool) -> List[List[Any]]:
    rows: List[List[Any]] = []
    for item in results:
        if not item.get("success"):
            continue
        data = item.get("data") or {}
        enterprise_name = F.text((data.get("enterprise") or {}).get("enterpriseName") or item.get("enterpriseName"))
        for entry in ((data.get("eligibilityResult") or {}).get("eligibilityEvaluations") or []):
            if not isinstance(entry, dict):
                continue
            scored = [
                detail for detail in entry.get("conditionDetails") or []
                if isinstance(detail, dict) and detail.get("contributesToScore") and detail.get("score") is not None
            ]
            component_scores = " + ".join(_score_text(detail.get("score")) for detail in scored) or "—"
            denominator = len(scored)
            average = (sum(float(detail["score"]) for detail in scored) / denominator * 100) if denominator else None
            base_formula = (
                f"条件平均分 = ({component_scores}) ÷ {denominator} = {average:.2f}"
                if average is not None else "没有可参与平均的条件"
            )
            if entry.get("hardPass") is False:
                final_formula = f"{base_formula}；存在硬性条件明确失败，最终资格条件分 = 0.00"
            elif entry.get("specialPassHit"):
                final_formula = f"最终资格条件分 = max({average:.2f}, 90.00) = {entry.get('conditionMatchScore'):.2f}"
            else:
                final_formula = f"最终资格条件分 = {base_formula.split(' = ', 1)[-1] if average is not None else '—'}"
            base = [
                enterprise_name, entry.get("fusionPolicyId"), _cell_text(entry.get("policyTitle")),
                entry.get("conditionMatchScore"), denominator, component_scores, base_formula, final_formula,
                _cell_text(entry.get("gateStatus")), "是" if entry.get("directPushAllowed") else "否",
                entry.get("pendingConditionCount"), entry.get("failedConditionCount"), entry.get("defaultMissingCount"),
                entry.get("evidenceCoverage"), entry.get("modelEvidence", {}).get("resolvedConditionCount"),
                _join(entry.get("gateReasons") or []),
            ]
            rows.append(([F.text(item.get("source"))] if include_source else []) + base)
    return rows


def export_evidence_reports(
    run_dir: Path, results: List[Dict[str, Any]], summary: Dict[str, Any]
) -> Tuple[Path, Path]:
    """从批次结果一次性导出详细链路证据与简易版报告。"""
    recommendation_rows = _recommendation_rows(results)
    exclusion_rows = _exclusion_rows(results)
    detailed_path = run_dir / DETAIL_REPORT_NAME
    simplified_path = run_dir / SIMPLIFIED_REPORT_NAME

    detailed = Workbook()
    overview = detailed.active
    overview.title = "批次概览"
    _append_table(overview, ["指标", "值"], _overview_rows(results, summary), [30, 24])

    recommendation_sheet = detailed.create_sheet("推荐证据")
    _append_table(
        recommendation_sheet,
        ["源文件", "企业名称", "排名", "政策ID", "政策标题", "符合度", "档位",
         "资格直推", "资格原因码", "命中规则", "政策级关键词证据", "推荐理由"],
        ([
            row["source"], row["enterpriseName"], rec.get("rank"), rec.get("fusionPolicyId"),
            _cell_text(rec.get("policyTitle")), rec.get("conditionMatchScore"), _cell_text(rec.get("gateStatus")),
            "是" if rec.get("directPushAllowed") else "否" if rec.get("gateStatus") else "",
            _join(rec.get("gateReasons") or []),
            _join(rec.get("matchedRules") or []),
            _join([f"{entry.get('ruleId')}：{_join(entry.get('matchedKeywords') or [], '、')}"
                   for entry in row["evidence"]]),
            _cell_text(rec.get("reason")),
        ] for row in recommendation_rows for rec in [row["recommendation"]]),
        [28, 24, 8, 12, 48, 10, 20, 12, 36, 20, 42, 64],
    )

    rule_sheet = detailed.create_sheet("企业规则证据")
    all_rule_rows = [
        rule for item in results if item.get("success")
        for rule in _rule_rows(
            item.get("data") or {}, F.text(item.get("source")),
            F.text(((item.get("data") or {}).get("enterprise") or {}).get("enterpriseName") or item.get("enterpriseName")),
        )
    ]
    _append_table(
        rule_sheet,
        ["源文件", "企业名称", "通道", "状态", "规则ID", "判断详情", "跳过原因", "缺失字段", "规则关键词", "政策包描述"],
        ([row[key] for key in ("source", "enterpriseName", "channel", "status", "ruleId", "details", "skipReason", "missingFields", "keywords", "policyPackDesc")]
         for row in all_rule_rows),
        [28, 24, 10, 12, 12, 54, 34, 28, 30, 42],
    )

    evidence_sheet = detailed.create_sheet("政策级证据")
    _append_table(
        evidence_sheet,
        ["源文件", "企业名称", "政策ID", "政策标题", "规则ID", "命中关键词", "证据依据"],
        ([
            row["source"], row["enterpriseName"], rec.get("fusionPolicyId"), _cell_text(rec.get("policyTitle")),
            entry.get("ruleId"), _join(entry.get("matchedKeywords") or [], "、"), _cell_text(entry.get("basis")),
        ] for row in recommendation_rows for rec in [row["recommendation"]] for entry in row["evidence"]),
        [28, 24, 12, 48, 12, 34, 50],
    )

    exclusion_sheet = detailed.create_sheet("排除候选")
    _append_table(
        exclusion_sheet,
        ["源文件", "企业名称", "排除类型", "政策ID", "政策标题", "状态", "建议动作", "原因"],
        ([row[key] for key in ("source", "enterpriseName", "kind", "policyId", "title", "status", "action", "reasons")]
         for row in exclusion_rows),
        [28, 24, 14, 12, 48, 28, 54, 54],
    )
    for row in exclusion_sheet.iter_rows(min_row=2):
        for cell in row:
            cell.fill = _EXCLUSION_FILL

    profile_sheet = detailed.create_sheet("企业画像")
    profile_rows = []
    for item in results:
        if not item.get("success"):
            continue
        data = item.get("data") or {}
        enterprise = data.get("enterprise") or {}
        profile_rows.append([
            F.text(item.get("source")), F.text(enterprise.get("enterpriseName")),
            _cell_text(enterprise.get("tags")), _cell_text(enterprise.get("metrics")),
            _cell_text(enterprise.get("qualifications")), _cell_text(enterprise.get("eligibilityFacts")),
            _cell_text((item.get("meta") or {}).get("dataCompleteness")),
        ])
    _append_table(
        profile_sheet,
        ["源文件", "企业名称", "企业标签", "指标", "资质", "资格事实", "数据完整度"],
        profile_rows,
        [28, 24, 50, 52, 52, 52, 34],
    )

    gate_sheet = detailed.create_sheet("资格门禁")
    _append_table(
        gate_sheet,
        ["源文件", "企业名称", "政策ID", "政策标题", "条件评分", "门禁状态", "允许直推", "硬性通过", "直通车命中", "规则覆盖", "已评分条件", "待补充条件", "失败条件", "默认缺失", "证据覆盖率", "原因码"],
        ([
            F.text(item.get("source")),
            F.text(((item.get("data") or {}).get("enterprise") or {}).get("enterpriseName") or item.get("enterpriseName")),
            entry.get("fusionPolicyId"), _cell_text(entry.get("policyTitle")), entry.get("conditionMatchScore"),
            _cell_text(entry.get("gateStatus")), "是" if entry.get("directPushAllowed") else "否",
            "是" if entry.get("hardPass") else "否" if entry.get("hardPass") is not None else "",
            "是" if entry.get("specialPassHit") else "否" if entry.get("specialPassHit") is not None else "",
            _cell_text(entry.get("ruleCoverage")), entry.get("scoredConditionCount"), entry.get("pendingConditionCount"),
            entry.get("failedConditionCount"), entry.get("defaultMissingCount"), entry.get("evidenceCoverage"),
            _join(entry.get("gateReasons") or []),
        ] for item in results if item.get("success")
          for entry in ((item.get("data") or {}).get("eligibilityResult") or {}).get("eligibilityEvaluations") or []
          if isinstance(entry, dict)),
        [28, 24, 12, 48, 12, 20, 12, 12, 12, 16, 12, 12, 12, 12, 14, 40],
    )

    condition_sheet = detailed.create_sheet("资格条件明细")
    _append_table(
        condition_sheet,
        ["源文件", "企业名称", "政策ID", "政策标题", "规则组", "条件序号", "条件分支", "条件原文", "项ID", "条件名称",
         "操作符", "企业事实值", "企业单位", "目标值", "目标单位", "换算后企业值", "换算后目标值", "单位换算过程",
         "判定状态", "单项得分（百分制）", "是否参与总分", "计分来源", "证据等级", "企业证据路径", "证据语义类别",
         "模型事实状态", "模型证据说明", "证据回读说明", "服务端判定说明"],
        _eligibility_condition_rows(results, include_source=True),
        [28, 24, 12, 48, 16, 10, 14, 64, 12, 26, 12, 16, 12, 12, 12, 16, 16, 36, 16, 16, 14, 24, 24, 42, 30, 14, 42, 42, 46],
    )

    formula_sheet = detailed.create_sheet("资格评分公式")
    _append_table(
        formula_sheet,
        ["源文件", "企业名称", "政策ID", "政策标题", "最终资格条件分", "参与计分条件数", "单项得分列表（百分制）", "条件平均公式", "最终分公式", "门禁状态", "允许直推", "待补充条件", "失败条件", "默认缺失", "证据覆盖率", "模型解析条件数", "原因码"],
        _eligibility_formula_rows(results, include_source=True),
        [28, 24, 12, 48, 16, 16, 32, 56, 64, 20, 12, 14, 12, 12, 14, 16, 42],
    )
    detailed.save(detailed_path)

    # 简易版仅保留业务审核时需要的六类证据，避免 V2 推荐分与 V1 条件分散落在多张表中。
    policy_records, policy_tag_rows = _policy_tag_rows(recommendation_rows)
    rule_by_key = {
        (row["enterpriseName"], row["ruleId"]): row
        for row in all_rule_rows if row["status"] == "matched"
    }
    simplified = Workbook()
    enterprise_sheet = simplified.active
    enterprise_sheet.title = "01_企业原始映射"
    _append_review_table(
        enterprise_sheet,
        "企业传入原始数据（中文字段映射）",
        "严格按 policy_recommendation_field_map.json 映射；保留子表及记录序号，空值仅表示原始数据未提供。",
        ["企业名称", "数据分组", "记录序号", "中文字段", "原始取值"],
        _mapped_enterprise_rows(results),
        [24, 28, 14, 42, 88],
    )

    simple_overview = simplified.create_sheet("02_推荐总览")
    _append_review_table(
        simple_overview,
        "推荐政策总览",
        "按申报要求符合度（0-100）排序；硬性条件不通过直接不推荐（不计入本表），符合度不足或数据不全的档位如实标注。",
        ["企业名称", "排名", "政策ID", "推荐政策", "申报要求符合度", "档位", "允许直推", "待补齐条件", "明确失败条件", "政策级命中规则", "推荐依据"],
        ([
            row["enterpriseName"], rec.get("rank"), rec.get("fusionPolicyId"), _cell_text(rec.get("policyTitle")),
            rec.get("conditionMatchScore"),
            _GATE_STATUS_LABELS.get(F.text(rec.get("gateStatus")), _cell_text(rec.get("gateStatus"))),
            "是" if rec.get("directPushAllowed") else "否" if rec.get("gateStatus") else "—",
            rec.get("pendingConditionCount"), rec.get("failedConditionCount"),
            _join(rec.get("matchedRules") or [], "、"), _cell_text(rec.get("reason")),
        ] for row in recommendation_rows for rec in [row["recommendation"]]),
        [24, 8, 12, 50, 22, 18, 12, 14, 14, 22, 82],
        status_column=6,
    )

    condition_sheet = simplified.create_sheet("03_政策条件评分账目")
    condition_records = _condition_review_records(results)
    _append_review_table(
        condition_sheet,
        "政策申报条件逐项评分账目",
        "每行是一条政策条件。缺失待核验的 10 分仅是 V1 默认计分，不构成企业已经满足该申报条件的证明。",
        ["企业名称", "政策ID", "推荐政策", "条件编号", "政策申报条件", "条件名称", "政策要求值", "企业原始事实值", "可比企业值/单位换算", "企业规则证据", "判定状态", "单项条件分（0-100）", "参与总分", "判定来源", "服务端判定说明"],
        ([
            row["enterpriseName"], row["policyId"], row["policyTitle"], row["conditionNo"], row["conditionText"], row["itemName"], row["requirement"],
            row["enterpriseValue"], row["convertedValue"], row["evidence"], row["status"], row["score"], row["contributes"], row["passSource"], row["message"],
        ] for row in condition_records),
        [24, 12, 46, 12, 64, 28, 38, 24, 38, 52, 16, 18, 14, 20, 52],
        status_column=11,
    )

    formula_sheet = simplified.create_sheet("04_政策评分汇总")
    _append_review_table(
        formula_sheet,
        "政策条件匹配分汇总（V1 规则口径）",
        "最终分只使用服务端实际参与计分的条件；存在硬性条件明确失败时以服务端最终结果为准。",
        ["企业名称", "政策ID", "推荐政策", "政策条件匹配分（V1口径）", "资格审查状态", "允许直推", "参与条件数", "单项分列表（0-100）", "条件平均公式", "最终分公式", "待补齐条件", "明确失败条件", "默认缺失条件", "证据覆盖率"],
        _condition_score_summary_rows(results),
        [24, 12, 48, 24, 18, 12, 14, 30, 48, 72, 14, 14, 16, 16],
        status_column=5,
    )

    evidence_sheet = simplified.create_sheet("05_V2推理与规则证据")
    evidence_rows = []
    for row in recommendation_rows:
        rec = row["recommendation"]
        for entry in row["evidence"]:
            rule = rule_by_key.get((row["enterpriseName"], F.text(entry.get("ruleId"))), {})
            evidence_rows.append([
                row["enterpriseName"], rec.get("rank"), rec.get("fusionPolicyId"), _cell_text(rec.get("policyTitle")),
                entry.get("ruleId"), rule.get("name", "—"), _join(entry.get("matchedKeywords") or [], "、"),
                _cell_text(entry.get("basis")), rule.get("details", "—"), rule.get("signals", "—"), _cell_text(rec.get("reason")),
            ])
    _append_review_table(
        evidence_sheet,
        "V2 推理链路与企业规则证据",
        "仅展示最终推荐政策的实际命中规则和关键词交集；该链路用于说明推荐相关度，不替代逐条件资格核验。",
        ["企业名称", "排名", "政策ID", "推荐政策", "企业规则ID", "企业规则", "政策关键词交集", "政策关联依据", "企业规则命中依据", "规则信号", "V2 推荐理由"],
        evidence_rows,
        [24, 8, 12, 48, 14, 28, 28, 38, 56, 52, 80],
    )

    policy_tag_sheet = simplified.create_sheet("06_政策标签证据")
    _append_review_table(
        policy_tag_sheet,
        "推荐政策标签证据",
        "仅展示最终推荐政策的标签、置信度和打标依据；标签用于 V2 推荐相关度，不等同于企业满足申报条件。",
        ["政策ID", "推荐政策", "标签维度", "标签值", "置信度", "打标依据"],
        policy_tag_rows,
        [12, 50, 12, 34, 14, 108],
    )
    simplified.save(simplified_path)
    return detailed_path, simplified_path


def _viewer_payload(results: List[Dict[str, Any]], summary: Dict[str, Any]) -> Dict[str, Any]:
    """整理离线查看页数据，只携带最终推荐及其对应证据。"""
    recommendation_rows = _recommendation_rows(results)
    policy_records, _ = _policy_tag_rows(recommendation_rows)
    rule_rows = {
        F.text(((item.get("data") or {}).get("enterprise") or {}).get("enterpriseName") or item.get("enterpriseName")): _rule_rows(
            item.get("data") or {}, F.text(item.get("source")),
            F.text(((item.get("data") or {}).get("enterprise") or {}).get("enterpriseName") or item.get("enterpriseName")),
        )
        for item in results if item.get("success")
    }
    conditions_by_policy: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for row in _condition_review_records(results):
        conditions_by_policy.setdefault((F.text(row["enterpriseName"]), F.text(row["policyId"])), []).append(row)

    enterprises: List[Dict[str, Any]] = []
    for item in results:
        if not item.get("success"):
            continue
        data = item.get("data") or {}
        enterprise_name = F.text((data.get("enterprise") or {}).get("enterpriseName") or item.get("enterpriseName"))
        eligibility_by_policy = {
            F.text(entry.get("fusionPolicyId")): entry
            for entry in ((data.get("eligibilityResult") or {}).get("eligibilityEvaluations") or [])
            if isinstance(entry, dict)
        }
        matched_rules = {
            row["ruleId"]: row for row in rule_rows.get(enterprise_name, []) if row.get("status") == "matched"
        }
        policies: List[Dict[str, Any]] = []
        for rec in data.get("recommendations") or []:
            if not isinstance(rec, dict):
                continue
            policy_id = F.text(rec.get("fusionPolicyId"))
            eligibility = eligibility_by_policy.get(policy_id) or rec  # 推荐项自身即携带资格评估
            policy_tags = (policy_records.get(policy_id) or {}).get("tags") or {}
            tag_rows = [{
                "dimension": dimension,
                "value": _tag_values(policy_tags.get(dimension) or {}, dimension) or "—",
                "confidence": F.text((policy_tags.get(dimension) or {}).get("confidence")) or "—",
                "basis": F.text((policy_tags.get(dimension) or {}).get("basis")) or "—",
            } for dimension in ("A", "B", "C", "D", "E", "F")]
            evidence_rows = []
            for evidence in _recommendation_evidence(rec):
                rule = matched_rules.get(F.text(evidence.get("ruleId"))) or {}
                evidence_rows.append({
                    "ruleId": F.text(evidence.get("ruleId")) or "—",
                    "ruleName": F.text(rule.get("name")) or "—",
                    "keywords": _join(evidence.get("matchedKeywords") or [], "、") or "—",
                    "basis": F.text(evidence.get("basis")) or "—",
                    "ruleDetails": F.text(rule.get("details")) or "—",
                    "signals": F.text(rule.get("signals")) or "—",
                })
            formula = _condition_formula_record(eligibility) if eligibility else {
                "conditionScore": None, "scoredCount": 0, "componentScores": "—",
                "averageFormula": "未建立可执行的逐条件资格规则", "finalFormula": "未计算",
            }
            policies.append({
                "policyId": policy_id,
                "title": F.text(rec.get("policyTitle")) or "—",
                "rank": rec.get("rank"),
                "recommendationScore": rec.get("conditionMatchScore"),
                "conditionScore": formula["conditionScore"] if formula.get("conditionScore") is not None else rec.get("conditionMatchScore"),
                "gateStatus": _GATE_STATUS_LABELS.get(F.text(eligibility.get("gateStatus")), "未计算"),
                "directPushAllowed": bool(eligibility.get("directPushAllowed")),
                "pendingCount": eligibility.get("pendingConditionCount"),
                "failedCount": eligibility.get("failedConditionCount"),
                "recommendationReason": F.text(rec.get("reason")) or "—",
                "conditions": conditions_by_policy.get((enterprise_name, policy_id), []),
                "formula": formula,
                "ruleEvidence": evidence_rows,
                "policyTags": tag_rows,
            })
        enterprises.append({
            "name": enterprise_name,
            "source": F.text(item.get("source")),
            "originalEnterpriseData": map_record_to_chinese(item.get("enterpriseInput") or {}),
            "policies": policies,
        })
    return {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {"total": len(results), "success": sum(1 for item in results if item.get("success"))},
        "enterprises": enterprises,
    }


def export_evidence_viewer(run_dir: Path, results: List[Dict[str, Any]], summary: Dict[str, Any]) -> Path:
    """生成无需服务即可打开的单文件推荐证据查看页。"""
    payload = json.dumps(_viewer_payload(results, summary), ensure_ascii=False, default=str).replace("</", "<\\/")
    document = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>政策推荐证据查看</title>
<style>
:root { --ink:#17212f; --muted:#64748b; --line:#d8e1ea; --blue:#145a86; --blue-pale:#eaf4fa; --green:#166534; --green-pale:#e9f7ec; --amber:#9a6700; --amber-pale:#fff6df; --red:#b42318; --red-pale:#ffefed; }
* { box-sizing:border-box; } body { margin:0; color:var(--ink); background:#f4f7fa; font:14px/1.55 "Microsoft YaHei", "Segoe UI", sans-serif; }
header { background:#123d5a; color:#fff; padding:26px max(24px, calc((100vw - 1440px)/2)); } h1 { margin:0; font-size:24px; font-weight:700; } .sub { margin:5px 0 0; color:#d8e9f3; } main { max-width:1440px; margin:0 auto; padding:20px 24px 44px; }
.toolbar { display:flex; gap:14px; align-items:center; margin-bottom:18px; padding:14px 16px; background:#fff; border:1px solid var(--line); } label { font-weight:700; } select { min-width:300px; padding:7px 10px; border:1px solid #aebdca; background:#fff; color:var(--ink); font:inherit; }
.notice { margin:0 0 18px; padding:11px 14px; background:var(--amber-pale); border-left:4px solid #d99a00; color:#664b00; }
.section { margin:20px 0; background:#fff; border:1px solid var(--line); } .section > h2 { margin:0; padding:12px 16px; font-size:17px; background:var(--blue-pale); border-bottom:1px solid var(--line); } .section-body { padding:14px 16px; }
table { width:100%; border-collapse:collapse; } th { color:#26465a; background:#f2f7fa; font-weight:700; text-align:left; } th, td { padding:9px 10px; vertical-align:top; border-bottom:1px solid #e3ebf1; word-break:break-word; } td.num, th.num { text-align:right; white-space:nowrap; } .muted { color:var(--muted); } .score { font-size:18px; font-weight:700; color:#0b5278; white-space:nowrap; }
.badge { display:inline-block; padding:2px 8px; border-radius:3px; font-size:12px; font-weight:700; white-space:nowrap; } .pass { color:var(--green); background:var(--green-pale); } .pending { color:var(--amber); background:var(--amber-pale); } .fail { color:var(--red); background:var(--red-pale); } .neutral { color:#475569; background:#edf1f5; }
details { margin:12px 0; border:1px solid var(--line); background:#fff; } summary { cursor:pointer; padding:14px 16px; font-weight:700; } details[open] summary { border-bottom:1px solid var(--line); background:#f9fbfc; } .policy-meta { display:flex; flex-wrap:wrap; gap:10px 20px; margin:0 0 14px; } .policy-meta strong { color:#1b587a; } .detail-block { margin-top:16px; } .detail-block h3 { margin:0 0 8px; font-size:15px; }
pre { margin:0; padding:14px; overflow:auto; background:#f8fafc; border:1px solid #e4ebf0; color:#243746; font:12px/1.55 Consolas, "Microsoft YaHei", monospace; white-space:pre-wrap; }
@media (max-width:760px) { header, main { padding-left:14px; padding-right:14px; } .toolbar { display:block; } select { display:block; width:100%; min-width:0; margin-top:8px; } table { min-width:880px; } .scroll { overflow-x:auto; } }
</style>
</head>
<body>
<header><h1>政策推荐证据查看</h1><p class="sub" id="subtitle"></p></header>
<main><div class="toolbar"><label for="enterprise">企业</label><select id="enterprise"></select></div><p class="notice">V2 推荐相关度分用于推荐排序；政策条件匹配分按 V1 规则口径逐条件计算。缺失待核验的默认分不代表企业已满足该条件。</p><div id="content"></div></main>
<script id="evidence-data" type="application/json">__PAYLOAD__</script>
<script>
const data = JSON.parse(document.getElementById('evidence-data').textContent);
const esc = value => String(value ?? '—').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const score = value => value === null || value === undefined || value === '' ? '—' : Number(value).toFixed(2);
function tone(value) { if (['满足','允许直推'].includes(value)) return 'pass'; if (['不满足','不推荐'].includes(value)) return 'fail'; if (['缺失待核验','待补齐','不可计算'].includes(value)) return 'pending'; return 'neutral'; }
function badge(value) { return `<span class="badge ${tone(value)}">${esc(value)}</span>`; }
function table(headers, rows, numeric = []) { return `<div class="scroll"><table><thead><tr>${headers.map((h,i)=>`<th class="${numeric.includes(i)?'num':''}">${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.length ? rows.map(row=>`<tr>${row.map((cell,i)=>`<td class="${numeric.includes(i)?'num':''}">${cell}</td>`).join('')}</tr>`).join('') : `<tr><td colspan="${headers.length}" class="muted">本次没有对应数据</td></tr>`}</tbody></table></div>`; }
function renderEnterpriseRaw(record) { return `<pre>${esc(JSON.stringify(record, null, 2))}</pre>`; }
function renderPolicy(policy, expanded) {
  const conditionRows = policy.conditions.map(row => [esc(row.conditionNo), esc(row.conditionText), esc(row.requirement).replace(/\\n/g,'<br>'), esc(row.enterpriseValue), esc(row.convertedValue).replace(/\\n/g,'<br>'), esc(row.evidence).replace(/\\n/g,'<br>'), badge(row.status), `<span class="score">${score(row.score)}</span>`, esc(row.contributes), esc(row.passSource), esc(row.message)]);
  const ruleRows = policy.ruleEvidence.map(row => [esc(row.ruleId), esc(row.ruleName), esc(row.keywords), esc(row.basis), esc(row.ruleDetails), esc(row.signals)]);
  const tagRows = policy.policyTags.map(row => [esc(row.dimension), esc(row.value), esc(row.confidence), esc(row.basis)]);
  return `<details ${expanded?'open':''}><summary>第 ${esc(policy.rank)} 名　${esc(policy.title)}（ID：${esc(policy.policyId)}）</summary><div class="section-body"><div class="policy-meta"><span>V2 推荐相关度分：<strong class="score">${score(policy.recommendationScore)}</strong></span><span>政策条件匹配分：<strong class="score">${score(policy.conditionScore)}</strong></span><span>资格状态：${badge(policy.gateStatus)}</span><span>允许直推：<strong>${policy.directPushAllowed?'是':'否'}</strong></span><span>待补齐：${esc(policy.pendingCount)}</span><span>明确失败：${esc(policy.failedCount)}</span></div><div class="detail-block"><h3>政策申报条件与逐项评分</h3>${table(['编号','政策申报条件','政策要求值','企业原始事实值','可比企业值/单位换算','企业规则证据','判定','单项分','参与总分','判定来源','服务端判定说明'],conditionRows,[7])}</div><div class="detail-block"><h3>政策条件匹配分计算</h3>${table(['参与条件数','单项分列表','条件平均公式','最终分公式'],[[esc(policy.formula.scoredCount),esc(policy.formula.componentScores),esc(policy.formula.averageFormula),esc(policy.formula.finalFormula)]])}</div><div class="detail-block"><h3>V2 推理与企业规则证据</h3><p>${esc(policy.recommendationReason)}</p>${table(['规则ID','企业规则','政策关键词交集','政策关联依据','企业规则命中依据','规则信号'],ruleRows)}</div><div class="detail-block"><h3>政策标签证据</h3>${table(['维度','标签值','置信度','打标依据'],tagRows)}</div></div></details>`;
}
function render() { const index = Number(document.getElementById('enterprise').value || 0); const enterprise = data.enterprises[index]; document.getElementById('content').innerHTML = `<section class="section"><h2>推荐政策</h2><div class="section-body">${enterprise.policies.map((policy,index)=>renderPolicy(policy,index===0)).join('') || '<p class="muted">本次没有最终推荐政策。</p>'}</div></section><section class="section"><h2>企业原始数据（中文字段映射）</h2><div class="section-body">${renderEnterpriseRaw(enterprise.originalEnterpriseData)}</div></section>`; }
const select = document.getElementById('enterprise'); data.enterprises.forEach((enterprise,index)=>select.insertAdjacentHTML('beforeend',`<option value="${index}">${esc(enterprise.name)}（${enterprise.source}）</option>`)); document.getElementById('subtitle').textContent = `生成时间：${data.generatedAt}　成功企业：${data.summary.success}/${data.summary.total}`; select.addEventListener('change',render); render();
</script>
</body></html>""".replace("__PAYLOAD__", payload)
    target = run_dir / VIEWER_REPORT_NAME
    target.write_text(document, encoding="utf-8")
    return target


_CM_STATUS_LABELS = {
    "satisfied": "满足",
    "partially_satisfied": "部分满足",
    "not_satisfied": "不满足",
    "missing": "缺失",
}
_CM_GATE_LABELS = {
    "pass": "满足",
    "fail": "不满足",
    "missing": "缺失待核验",
    "not_evaluable": "不可计算",
    "skipped": "已跳过",
}
_CM_EFFECT_LABELS = {
    "none": "无",
    "out_of_scope": "范围外",
    "irreversible": "不可逆",
    "growable": "可成长",
    "ordinary_gap": "普通差距",
    "missing_data": "缺数据",
}
_CM_WINDOW_LABELS = {
    "open": "申报中",
    "pending": "未开始",
    "last_year": "已过期",
    "long_term": "长期受理",
}


def _cm_condition_rows(conditions: List[Dict[str, Any]]) -> List[List[Any]]:
    rows: List[List[Any]] = []
    for item in conditions:
        if not isinstance(item, dict):
            continue
        evidence = _join([
            f"{F.text(e.get('path'))}={_display_value(e.get('value'), e.get('unit'))}"
            for e in (item.get("evidence") or []) if isinstance(e, dict)
        ], "\n")
        rows.append([
            F.text(item.get("conditionKey")),
            _cell_text(item.get("sourceText")),
            _CM_EFFECT_LABELS.get(F.text(item.get("failureEffect")), F.text(item.get("failureEffect")) or "—"),
            _CM_STATUS_LABELS.get(F.text(item.get("status")), F.text(item.get("status"))),
            _score_text(item.get("score")),
            F.text(item.get("groupType")),
            F.text(item.get("groupOperator")),
            F.text(item.get("reason")),
            F.text(item.get("confidence")),
            F.text(item.get("scoreSource")),
            F.text(item.get("serverMessage")),
            evidence,
        ])
    return rows


def _cm_group_rows(groups: List[Dict[str, Any]]) -> List[List[Any]]:
    rows: List[List[Any]] = []
    for item in groups:
        if not isinstance(item, dict):
            continue
        rows.append([
            F.text(item.get("groupKey")),
            F.text(item.get("groupType")),
            F.text(item.get("operator")),
            _score_text(item.get("score")),
            _join(item.get("conditionKeys") or [], "\n"),
        ])
    return rows


def _cm_viewer_payload(results: List[Dict[str, Any]], summary: Dict[str, Any]) -> Dict[str, Any]:
    """把新链路结果整理成离线查看页数据：推荐政策、逐条件账目、失败、预过滤排除、企业原始数据。"""
    enterprises: List[Dict[str, Any]] = []
    for item in results:
        if not item.get("success"):
            continue
        data = item.get("data") or {}
        meta = item.get("meta") or {}
        enterprise_name = F.text(data.get("enterpriseName") or item.get("enterpriseName"))
        policies: List[Dict[str, Any]] = []
        for rec in data.get("recommendations") or []:
            if not isinstance(rec, dict):
                continue
            audit = rec.get("audit") or {}
            conditions = audit.get("conditionResults") or []
            groups = audit.get("groupResults") or []
            statuses = {F.text(c.get("status")) for c in conditions if isinstance(c, dict)}
            gate = "pass" if "not_satisfied" not in statuses else "fail"
            if not conditions:
                gate = "not_evaluable"
            policies.append({
                "policyId": F.text(rec.get("fusionPolicyId")),
                "title": F.text(rec.get("policyTitle")) or "—",
                "rank": rec.get("rank"),
                "finalScore": rec.get("finalScore"),
                "conditionScore": rec.get("conditionScore"),
                "timelinessScore": rec.get("timelinessScore"),
                "reason": F.text(rec.get("reason")) or "—",
                "window": rec.get("applicationWindow") or {},
                "gate": gate,
                "modelName": F.text(audit.get("modelName")),
                "verifiedConditionScore": audit.get("verifiedConditionScore"),
                "dataPending": bool(audit.get("dataPending")),
                "conditionRows": _cm_condition_rows(conditions),
                "groupRows": _cm_group_rows(groups),
                "sourceConflicts": [
                    {
                        "applicationRequirementText": F.text(row.get("applicationRequirementText")),
                        "ruleTableText": F.text(row.get("ruleTableText")),
                        "description": F.text(row.get("description")),
                    }
                    for row in (audit.get("sourceConflicts") or []) if isinstance(row, dict)
                ],
            })
        enterprises.append({
            "name": enterprise_name,
            "source": F.text(item.get("source")),
            "originalEnterpriseData": map_record_to_chinese(item.get("enterpriseInput") or {}),
            "policies": policies,
            "failures": [
                {"policyId": F.text(row.get("fusionPolicyId")), "error": F.text(row.get("error"))}
                for row in (meta.get("failures") or []) if isinstance(row, dict)
            ],
            "prefilterExclusions": [
                {
                    "policyId": F.text(row.get("fusionPolicyId")),
                    "code": F.text((row.get("reason") or {}).get("code")),
                    "message": F.text((row.get("reason") or {}).get("message")),
                    "evidence": _join([
                        F.text(entry.get("text"))
                        for entry in ((row.get("reason") or {}).get("evidence") or [])
                        if isinstance(entry, dict)
                    ], "；"),
                }
                for row in (meta.get("prefilterExclusions") or []) if isinstance(row, dict)
            ],
            "stats": {
                "policyCount": meta.get("policyCount"),
                "candidateCount": meta.get("candidateCount"),
                "excludedCount": meta.get("excludedCount"),
                "failedCount": meta.get("failedCount"),
                "recommendationCount": meta.get("allRecommendationCount"),
                "elapsedMs": meta.get("elapsedMs"),
            },
        })
    return {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "total": len(results),
            "success": sum(1 for item in results if item.get("success")),
        },
        "enterprises": enterprises,
    }


def export_condition_match_viewer(run_dir: Path, results: List[Dict[str, Any]], summary: Dict[str, Any]) -> Path:
    """新条件匹配链路专用：生成无需服务即可打开的单文件结果查看页。

    结果结构不同于旧链路（旧 viewer 读 eligibilityResult/channelResults/enterpriseInput 等字段），
    这里按新 schema 的 recommendations[].audit.conditionResults 渲染逐条件账目，
    并展示失败政策与预过滤排除，便于直接查看 6543 这类契约失败。
    """
    payload = json.dumps(_cm_viewer_payload(results, summary), ensure_ascii=False, default=str).replace("</", "<\\/")
    document = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>政策条件匹配结果查看</title>
<style>
:root { --ink:#17212f; --muted:#64748b; --line:#d8e1ea; --blue:#145a86; --blue-pale:#eaf4fa; --green:#166534; --green-pale:#e9f7ec; --amber:#9a6700; --amber-pale:#fff6df; --red:#b42318; --red-pale:#ffefed; }
* { box-sizing:border-box; } body { margin:0; color:var(--ink); background:#f4f7fa; font:14px/1.55 "Microsoft YaHei", "Segoe UI", sans-serif; }
header { background:#123d5a; color:#fff; padding:26px max(24px, calc((100vw - 1440px)/2)); } h1 { margin:0; font-size:24px; font-weight:700; } .sub { margin:5px 0 0; color:#d8e9f3; } main { max-width:1440px; margin:0 auto; padding:20px 24px 44px; }
.toolbar { display:flex; gap:14px; align-items:center; margin-bottom:18px; padding:14px 16px; background:#fff; border:1px solid var(--line); } label { font-weight:700; } select { min-width:300px; padding:7px 10px; border:1px solid #aebdca; background:#fff; color:var(--ink); font:inherit; }
.section { margin:20px 0; background:#fff; border:1px solid var(--line); } .section > h2 { margin:0; padding:12px 16px; font-size:17px; background:var(--blue-pale); border-bottom:1px solid var(--line); } .section-body { padding:14px 16px; }
table { width:100%; border-collapse:collapse; } th { color:#26465a; background:#f2f7fa; font-weight:700; text-align:left; } th, td { padding:9px 10px; vertical-align:top; border-bottom:1px solid #e3ebf1; word-break:break-word; } td.num, th.num { text-align:right; white-space:nowrap; } .muted { color:var(--muted); } .score { font-size:18px; font-weight:700; color:#0b5278; white-space:nowrap; }
.badge { display:inline-block; padding:2px 8px; border-radius:3px; font-size:12px; font-weight:700; white-space:nowrap; } .pass { color:var(--green); background:var(--green-pale); } .pending { color:var(--amber); background:var(--amber-pale); } .fail { color:var(--red); background:var(--red-pale); } .neutral { color:#475569; background:#edf1f5; }
details { margin:12px 0; border:1px solid var(--line); background:#fff; } summary { cursor:pointer; padding:14px 16px; font-weight:700; } details[open] summary { border-bottom:1px solid var(--line); background:#f9fbfc; } .policy-meta { display:flex; flex-wrap:wrap; gap:10px 20px; margin:0 0 14px; } .policy-meta strong { color:#1b587a; } .detail-block { margin-top:16px; } .detail-block h3 { margin:0 0 8px; font-size:15px; }
pre { margin:0; padding:14px; overflow:auto; background:#f8fafc; border:1px solid #e4ebf0; color:#243746; font:12px/1.55 Consolas, "Microsoft YaHei", monospace; white-space:pre-wrap; }
.stat-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:10px; } .stat { background:#f2f7fa; border:1px solid var(--line); padding:10px 12px; } .stat b { display:block; font-size:18px; color:#0b5278; } .stat span { color:var(--muted); font-size:12px; }
@media (max-width:760px) { header, main { padding-left:14px; padding-right:14px; } .toolbar { display:block; } select { display:block; width:100%; min-width:0; margin-top:8px; } table { min-width:880px; } .scroll { overflow-x:auto; } }
</style>
</head>
<body>
<header><h1>政策条件匹配结果查看</h1><p class="sub" id="subtitle"></p></header>
<main><div class="toolbar"><label for="enterprise">企业</label><select id="enterprise"></select></div><div id="content"></div></main>
<script id="evidence-data" type="application/json">__PAYLOAD__</script>
<script>
const data = JSON.parse(document.getElementById('evidence-data').textContent);
const esc = value => String(value ?? '—').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const score = value => value === null || value === undefined || value === '' ? '—' : Number(value).toFixed(2);
function tone(value) { if (['满足','申报中'].includes(value)) return 'pass'; if (['缺失','缺失待核验','不可计算','待补齐','已过期'].includes(value)) return 'pending'; if (['不满足'].includes(value)) return 'fail'; return 'neutral'; }
function badge(value) { return `<span class="badge ${tone(value)}">${esc(value)}</span>`; }
function table(headers, rows, numeric = []) { return `<div class="scroll"><table><thead><tr>${headers.map((h,i)=>`<th class="${numeric.includes(i)?'num':''}">${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.length ? rows.map(row=>`<tr>${row.map((cell,i)=>`<td class="${numeric.includes(i)?'num':''}">${cell}</td>`).join('')}</tr>`).join('') : `<tr><td colspan="${headers.length}" class="muted">本次没有对应数据</td></tr>`}</tbody></table></div>`; }
function renderPolicy(policy, expanded) {
  const windowText = [policy.window.startTime, policy.window.endTime].filter(Boolean).join(' ~ ') || '—';
  const conditionRows = policy.conditionRows.map(row => [esc(row[0]), esc(row[1]).replace(/\\n/g,'<br>'), esc(row[2]), badge(row[3]), `<span class="score">${score(row[4])}</span>`, esc(row[5]), esc(row[6]), esc(row[7]), esc(row[8]), esc(row[9]), esc(row[10]).replace(/\\n/g,'<br>'), esc(row[11]).replace(/\\n/g,'<br>')]);
  const conflictRows = policy.sourceConflicts.map(row => [esc(row.applicationRequirementText).replace(/\\n/g,'<br>'), esc(row.ruleTableText).replace(/\\n/g,'<br>'), esc(row.description)]);
  return `<details ${expanded?'open':''}><summary>第 ${esc(policy.rank)} 名　${esc(policy.title)}（ID：${esc(policy.policyId)}）</summary><div class="section-body"><div class="policy-meta"><span>综合得分：<strong class="score">${score(policy.finalScore)}</strong></span><span>条件匹配分：<strong class="score">${score(policy.conditionScore)}</strong></span><span>时效分：<strong class="score">${score(policy.timelinessScore)}</strong></span><span>资格状态：${badge(policy.gate==='pass'?'满足':policy.gate==='fail'?'不满足':policy.gate==='not_evaluable'?'不可计算':'—')}</span><span>申报窗口：${esc(windowText)}</span><span>模型：${esc(policy.modelName)}</span></div><div class="detail-block"><h3>推荐理由</h3><p>${esc(policy.reason)}</p></div><div class="detail-block"><h3>政策申报条件逐项评分</h3>${table(['条件键','条件原文','失败效果','判定','单项分','分组类型','组内逻辑','模型理由','置信度','计分来源','服务端说明','企业证据'],conditionRows,[4])}</div><div class="detail-block"><h3>规则组汇总</h3>${table(['规则组','分组类型','组内逻辑','组得分','条件键'],policy.groupRows.map(row=>[esc(row[0]),esc(row[1]),esc(row[2]),`<span class="score">${score(row[3])}</span>`,esc(row[4]).replace(/\\n/g,'<br>')]),[3])}</div>${conflictRows.length?`<div class="detail-block"><h3>原文与规则表冲突</h3>${table(['申报条件原文','规则表原文','说明'],conflictRows)}</div>`:''}</div></details>`;
}
function renderEnterpriseRaw(record) { return `<pre>${esc(JSON.stringify(record, null, 2))}</pre>`; }
function render() {
  const index = Number(document.getElementById('enterprise').value || 0);
  const enterprise = data.enterprises[index];
  const stats = enterprise.stats || {};
  const failureRows = enterprise.failures.map(row => [esc(row.policyId), esc(row.error)]);
  const exclusionRows = enterprise.prefilterExclusions.map(row => [esc(row.policyId), esc(row.code), esc(row.message), esc(row.evidence)]);
  document.getElementById('content').innerHTML =
    `<section class="section"><h2>批次统计</h2><div class="section-body"><div class="stat-grid"><div class="stat"><b>${esc(stats.policyCount ?? '—')}</b><span>政策总数</span></div><div class="stat"><b>${esc(stats.candidateCount ?? '—')}</b><span>候选</span></div><div class="stat"><b>${esc(stats.excludedCount ?? '—')}</b><span>预过滤排除</span></div><div class="stat"><b>${esc(stats.failedCount ?? '—')}</b><span>失败</span></div><div class="stat"><b>${esc(stats.recommendationCount ?? '—')}</b><span>推荐</span></div><div class="stat"><b>${esc(stats.elapsedMs ?? '—')}</b><span>耗时 ms</span></div></div></div></section>` +
    `<section class="section"><h2>推荐政策</h2><div class="section-body">${enterprise.policies.map((policy,idx)=>renderPolicy(policy,idx===0)).join('') || '<p class="muted">本次没有最终推荐政策。</p>'}</div></section>` +
    (failureRows.length ? `<section class="section"><h2>政策匹配失败（${failureRows.length}）</h2><div class="section-body">${table(['政策ID','错误'],failureRows)}</div></section>` : '') +
    (exclusionRows.length ? `<section class="section"><h2>预过滤排除（${exclusionRows.length}）</h2><div class="section-body">${table(['政策ID','原因码','原因说明','证据'],exclusionRows)}</div></section>` : '') +
    `<section class="section"><h2>企业原始数据（中文字段映射）</h2><div class="section-body">${renderEnterpriseRaw(enterprise.originalEnterpriseData)}</div></section>`;
}
const select = document.getElementById('enterprise'); data.enterprises.forEach((enterprise,index)=>select.insertAdjacentHTML('beforeend',`<option value="${index}">${esc(enterprise.name)}${enterprise.source?`（${esc(enterprise.source)}）`:''}</option>`)); document.getElementById('subtitle').textContent = `生成时间：${data.generatedAt}　成功企业：${data.summary.success}/${data.summary.total}`; select.addEventListener('change',render); render();
</script>
</body></html>""".replace("__PAYLOAD__", payload)
    target = run_dir / "condition_match_viewer.html"
    target.write_text(document, encoding="utf-8")
    return target
