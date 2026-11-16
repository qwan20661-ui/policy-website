"""匹配服务编排。"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from service.scoring_service import score_policy_against_enterprise
from service.rule_engine_service import resolve_rule_profile
from service.semantic_match_service import SemanticMatchRetryableError, assess_enterprise_policy
from service.policy_association_service import assess_policy_association, retryable_association_result


def _first_non_empty_text(*values: Any) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _sort_number(value: Any) -> float:
    """排序用数值转换；匹配失败待重试记录的分数为 None，统一按 -1 排到末尾。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            return -1.0
    return float(value)


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _apply_recommendation_guards(
    enterprise: Dict[str, Any], policy: Dict[str, Any], score_result: Dict[str, Any],
) -> Dict[str, Any]:
    """兼容旧调用；规则外日期、地域和主体信息不再影响评分。"""
    score_result["资格前置校验"] = {"通过": True, "阻断项": []}
    score_result["推荐阻断项"] = []
    return score_result


def _deduplicate_policies(policies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    unique_policies: List[Dict[str, Any]] = []
    seen = set()
    for policy in policies:
        key = _first_non_empty_text(policy.get("id"), policy.get("fusionPolicyId"), policy.get("title"))
        if not key or key in seen:
            continue
        seen.add(key)
        unique_policies.append(policy)
    return unique_policies


def _enterprise_summary(enterprise: Dict[str, Any]) -> Dict[str, Any]:
    if enterprise.get("_source_type") == "enterprise_profile_record":
        area_field = enterprise.get("22+N", "") or enterprise.get("所属行业领域(22+N)", "")
        emerging_field = enterprise.get("国家统计局-工业战略性新兴产业分类目录（2023）", "") or enterprise.get("战略性新兴产业分类", "")
        return {
            "企业ID": enterprise.get("企业ID", "") or enterprise.get("序号", ""),
            "企业名称": enterprise.get("企业名称", ""),
            "统一社会信用代码": enterprise.get("统一社会信用代码", ""),
            "注册地": enterprise.get("注册地", ""),
            "一级行业": enterprise.get("一级行业", ""),
            "二级行业": enterprise.get("二级行业", ""),
            "企业资质": enterprise.get("企业资质", ""),
            "平台资质": enterprise.get("平台资质", ""),
            "人才资质": enterprise.get("人才资质", ""),
            "所属行业领域(22+N)": area_field,
            "战略性新兴产业分类": emerging_field,
            "高新技术领域": enterprise.get("高新技术领域", ""),
            "源记录数量": enterprise.get("_source_record_count", 1),
            "源记录年份": enterprise.get("_source_record_years", []),
        }
    return {
        "企业ID": enterprise.get("企业ID", ""),
        "企业名称": enterprise.get("企业名称", ""),
        "统一社会信用代码": enterprise.get("统一社会信用代码", ""),
        "注册地": enterprise.get("注册地", ""),
        "一级行业": enterprise.get("一级行业", ""),
        "二级行业": enterprise.get("二级行业", ""),
        "荣誉资质": enterprise.get("荣誉资质", []),
        "科技服务领域": enterprise.get("科技服务领域", []),
        "源记录数量": enterprise.get("_source_record_count", 1),
        "源记录年份": enterprise.get("_source_record_years", []),
    }


def _local_explanation(score_result: Dict[str, Any]) -> Dict[str, List[str] | str]:
    details = score_result.get("条件匹配详情", []) or []
    positives = [f"{item.get('条件名称', '')}：{item.get('说明', '')}" for item in details if item.get("得分", 0) >= 70]
    negatives = [f"{item.get('条件名称', '')}：{item.get('说明', '')}" for item in details if item.get("得分", 0) < 45]
    risks = list(score_result.get("风险提示", []) or [])

    if score_result.get("hard_fail_items"):
        risks.insert(0, "存在硬性条件未通过，已进入保守推荐模式。")

    suggestion = score_result.get("总体建议", "")
    if not suggestion:
        if score_result.get("hard_pass"):
            suggestion = "当前匹配结果可供人工复核。"
        else:
            suggestion = "当前不建议直接申报。"

    return {
        "优势分析": positives[:5],
        "劣势分析": negatives[:5] + list(score_result.get("hard_fail_items", [])[:3]),
        "风险提示": risks[:5],
        "总体建议": suggestion,
    }


def _format_excel_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (list, tuple, set)):
        return "；".join(str(item) for item in value if str(item).strip())
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return value


def _autosize_worksheet(worksheet: Any) -> None:
    for column_cells in worksheet.columns:
        max_length = 0
        column_letter = column_cells[0].column_letter
        for cell in column_cells:
            cell_value = "" if cell.value is None else str(cell.value)
            max_length = max(max_length, len(cell_value))
        worksheet.column_dimensions[column_letter].width = min(max(max_length + 2, 12), 60)


def _style_worksheet(worksheet: Any) -> None:
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)
    header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    content_alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
    border = Border(
        left=Side(style="thin", color="D9E2F3"),
        right=Side(style="thin", color="D9E2F3"),
        top=Side(style="thin", color="D9E2F3"),
        bottom=Side(style="thin", color="D9E2F3"),
    )

    for cell in worksheet[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = header_alignment
        cell.border = border

    for row in worksheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = content_alignment
            cell.border = border

    worksheet.row_dimensions[1].height = 28
    worksheet.auto_filter.ref = worksheet.dimensions
    worksheet.sheet_view.showGridLines = True


def _style_business_score_sheet(worksheet: Any) -> None:
    """突出业务人员最关心的条件结论、得分和计算依据。"""
    from openpyxl.styles import Font, PatternFill

    _style_worksheet(worksheet)
    widths = {
        1: 22, 2: 28, 3: 12, 4: 12, 5: 22, 6: 42, 7: 24, 8: 18,
        9: 12, 10: 18, 11: 20, 12: 14, 13: 14, 14: 16, 15: 14, 16: 20,
        17: 52, 18: 58, 19: 58,
    }
    for column_index, width in widths.items():
        worksheet.column_dimensions[worksheet.cell(1, column_index).column_letter].width = width

    fills = {
        "命中": PatternFill("solid", fgColor="E2F0D9"),
        "未命中": PatternFill("solid", fgColor="FCE4D6"),
        "待确认": PatternFill("solid", fgColor="FFF2CC"),
    }
    for row_index in range(2, worksheet.max_row + 1):
        conclusion_cell = worksheet.cell(row_index, 9)
        if conclusion_cell.value in fills:
            conclusion_cell.fill = fills[conclusion_cell.value]
            conclusion_cell.font = Font(bold=True)
        for column_index in (10, 15, 16):
            worksheet.cell(row_index, column_index).number_format = "0.00"
        worksheet.row_dimensions[row_index].height = 48


def _style_business_formula_sheet(worksheet: Any) -> None:
    from openpyxl.styles import Font, PatternFill

    _style_worksheet(worksheet)
    widths = {1: 22, 2: 28, 3: 18, 4: 20, 5: 20, 6: 26, 7: 16, 8: 16, 9: 14, 10: 90, 11: 46}
    for column_index, width in widths.items():
        worksheet.column_dimensions[worksheet.cell(1, column_index).column_letter].width = width
    for row_index in range(2, worksheet.max_row + 1):
        hard_pass_cell = worksheet.cell(row_index, 3)
        hard_pass_cell.fill = PatternFill(
            "solid",
            fgColor="E2F0D9" if hard_pass_cell.value == "是" else "FCE4D6",
        )
        hard_pass_cell.font = Font(bold=True)
        worksheet.cell(row_index, 9).number_format = "0.00"
        worksheet.row_dimensions[row_index].height = 54


def _detail_group_type(detail: Dict[str, Any]) -> str:
    score_group = str(detail.get("评分组别", "")).strip().upper()
    if score_group == "A":
        return "硬性"
    if score_group == "SP":
        return "直通车"
    if score_group == "B":
        return "软性"
    return ""


def _hit_label(eval_status: str) -> str:
    mapping = {
        "pass": "命中",
        "reference_hit": "命中",
        "supporting_hit": "命中",
        "fail": "未命中",
        "not_evaluable": "待确认",
        "skipped": "未命中",
    }
    return mapping.get(str(eval_status or "").strip(), "")


def _group_status_label(status: Any) -> str:
    mapping = {
        "pass": "通过",
        "fail": "未通过",
        "not_evaluable": "待确认",
        "reference_only": "仅形成参考命中",
        "skipped": "已跳过",
    }
    normalized = str(status or "").strip()
    if not normalized:
        return ""
    return mapping.get(normalized, "未识别组状态")


def _eval_status_label(status: Any) -> str:
    mapping = {
        "pass": "正式命中",
        "reference_hit": "参考命中",
        "supporting_hit": "辅助命中",
        "fail": "未命中",
        "not_evaluable": "待确认",
        "skipped": "已跳过",
    }
    normalized = str(status or "").strip()
    if not normalized:
        return ""
    return mapping.get(normalized, "未识别判定状态")


def _condition_operator_label(operator: Any) -> str:
    mapping = {
        "AND": "全部满足",
        "ANY": "满足其一",
    }
    normalized = str(operator or "").strip()
    if not normalized:
        return ""
    return mapping.get(normalized, "未识别组合关系")


def _rule_type_label(rule_type: Any) -> str:
    mapping = {
        "rule": "正式判断项",
        "unresolved": "补判项",
        "supporting": "辅助项",
        "reference": "参考项",
        "hard_boolean": "硬性布尔判断",
        "soft_boolean": "软性布尔判断",
        "soft_numeric": "数值判断",
        "qualitative": "定性相似度",
        "indirect": "间接相似度",
    }
    normalized = str(rule_type or "").strip()
    if not normalized:
        return ""
    return mapping.get(normalized, "未识别规则类型")


def _score_group_label(score_group: Any) -> str:
    mapping = {
        "A": "甲组（硬性门槛）",
        "SP": "直通车组（优先判定）",
        "B": "乙组（软性评分）",
        "C": "丙组（定性调节）",
    }
    normalized = str(score_group or "").strip().upper()
    if not normalized:
        return ""
    return mapping.get(normalized, "未分组")


def _evaluation_role_label(role: Any) -> str:
    mapping = {
        "special_pass": "直通车条件",
        "soft_support": "软性支持",
    }
    normalized = str(role or "").strip()
    if not normalized:
        return "常规评估"
    return mapping.get(normalized, "常规评估")


def _score_bucket_label(bucket: Any) -> str:
    mapping = {
        "verified": "A类（运行时精确/派生证据）",
        "supporting": "B类（运行时辅助证据）",
        "unverified": "未核验证据（缺失/拒绝）",
        "excluded": "不参与计分",
        "direct": "A类（直接命中）",
        "indirect": "B类（间接命中）",
        "special_pass": "直通车（不参与普通加权）",
    }
    normalized = str(bucket or "").strip().lower()
    return mapping.get(normalized, "历史结果未记录计分归类")


def _resolved_score_bucket(detail: Dict[str, Any]) -> str:
    """读取当前评分链路保存的计分归类；旧结果缺失时不做猜测。"""
    bucket = str(detail.get("计分归类", "")).strip().lower()
    if bucket in {
        "verified", "supporting", "unverified", "excluded",
        "direct", "indirect", "special_pass",
    }:
        return bucket
    if str(detail.get("评分组别", "")).strip().upper() == "SP":
        return "special_pass"
    return ""


def _score_bucket_weights(condition_details: List[Dict[str, Any]]) -> tuple[Dict[str, int], Dict[str, float]]:
    """按运行时证据层级统计条件；普通条件最终保持逐条同权。"""
    counts = {"verified": 0, "supporting": 0, "unverified": 0}
    for detail in condition_details:
        if str(detail.get("评分组别", "")).strip().upper() not in {"A", "B"}:
            continue
        bucket = _resolved_score_bucket(detail)
        if bucket in counts:
            counts[bucket] += 1

    total = sum(counts.values())
    normalized_weights = {bucket: (count / total if total else 0.0) for bucket, count in counts.items()}
    return counts, normalized_weights


def _score_formula_text(policy_result: Dict[str, Any], condition_details: List[Dict[str, Any]]) -> str:
    if not policy_result.get("hard_pass"):
        return "最终分 = 0 分。原因：存在未通过或待确认的硬性条件，未进入普通加权评分。"

    algorithm = str(policy_result.get("评分聚合算法", "")).strip()
    legacy_buckets = {
        str(detail.get("计分归类", "")).strip().lower()
        for detail in condition_details
        if str(detail.get("计分归类", "")).strip().lower() in {"direct", "indirect"}
    }
    if not algorithm and legacy_buckets:
        return (
            "历史结果采用旧版 A/B 聚合口径（规则表 direct/reference 与固定类别权重）；"
            f"已记录最终分为 {policy_result.get('final_score', 0)} 分，不能用当前运行时证据层级公式回放。"
        )

    bucket_counts, _bucket_weights = _score_bucket_weights(condition_details)
    has_unclassified_history = any(
        str(detail.get("评分组别", "")).strip().upper() in {"A", "B"}
        and not str(detail.get("计分归类", "")).strip()
        for detail in condition_details
    )
    if has_unclassified_history:
        return (
            f"本结果由旧版明细导出，未保存每条条件的 A/B 计分归类，无法可靠还原条件贡献。"
            f"已记录最终分为 {policy_result.get('final_score', 0)} 分；请使用当前脚本重新运行该企业后查看完整公式与贡献。"
        )
    condition_count = sum(bucket_counts.values())
    if not condition_count:
        return "没有适用且可评分的普通条件，不能形成推荐分。"
    formula = (
        f"基础匹配分 = {condition_count} 项参与计分条件的得分之和 ÷ {condition_count}；"
        "每条条件同权，A/B仅表示运行时证据层级，不改变条件权重；"
        "最终分 = 基础匹配分（兼容字段 coeff_C 固定为 1.0，不再二次修正）。"
    )
    if policy_result.get("special_pass_hit"):
        formula += " 命中直通车条件，最终分再与 90 分比较取较高值。"
    return formula


def _tech_sme_evaluation_summary(policy_result: Dict[str, Any]) -> str:
    evaluation = policy_result.get("科技型中小企业评价得分")
    if not isinstance(evaluation, dict):
        return ""
    return "；".join(
        [
            f"科技人员={evaluation.get('科技人员指标得分', '未核验')}分",
            f"研发投入={evaluation.get('研发投入指标得分', '未核验')}分（{evaluation.get('研发投入计分口径', '')}）",
            f"科技成果={evaluation.get('科技成果指标得分', '未核验')}分",
            f"总分={evaluation.get('评价总分', '未核验')}分",
            str(evaluation.get("评价结论", "")),
        ]
    )


def _condition_score_effect(detail: Dict[str, Any], policy_result: Dict[str, Any], bucket_counts: Dict[str, int], bucket_weights: Dict[str, float]) -> tuple[Any, Any, str]:
    """返回条件的基础分贡献、最终贡献及可读说明。"""
    score_group = str(detail.get("评分组别", "")).strip().upper()
    bucket = _resolved_score_bucket(detail)
    if score_group == "SP":
        hit = _hit_label(str(detail.get("evalStatus", "")).strip()) == "命中"
        return "", "", "直通车条件不参与普通加权；命中时最终分至少按 90 分处理。" if hit else "直通车未命中，不产生普通评分贡献。"
    if not policy_result.get("hard_pass"):
        return 0, 0, "硬性条件未全部通过，最终分按 0 分处理，本条件不再参与加权。"
    if bucket not in bucket_counts or not bucket_counts[bucket]:
        return "", "", "该条件未记录可参与等权评分的运行时证据归类。"

    try:
        condition_score = float(detail.get("得分", 0) or 0)
    except (TypeError, ValueError):
        condition_score = 0.0
    base_contribution = condition_score * bucket_weights[bucket] / bucket_counts[bucket]
    total_count = sum(bucket_counts.values())
    return (
        round(base_contribution, 2),
        round(base_contribution, 2),
        f"本条件得分 {condition_score:g} ÷ 全部 {total_count} 项参与计分条件；A/B证据层级不改变权重。",
    )


def _recommendation_label(value: Any) -> str:
    if isinstance(value, bool):
        return "推荐" if value else "暂不推荐"
    normalized = str(value).strip()
    if normalized in {"1", "是"}:
        return "推荐"
    if normalized in {"0", "否"}:
        return "暂不推荐"
    return "待判断"


def _group_item_summary(items: List[Dict[str, Any]], hit_only: bool = False) -> str:
    parts: List[str] = []
    for item in items or []:
        eval_status = str(item.get("evalStatus", "")).strip()
        hit = _hit_label(eval_status)
        if hit_only and hit != "命中":
            continue
        item_name = str(item.get("itemName", "")).strip()
        score = item.get("score", 0)
        message = str(item.get("message", "")).strip()
        parts.append(f"{item_name}[{hit}|{score}分] {message}".strip())
    return " | ".join(parts)


def _policy_hit_summary(policy_result: Dict[str, Any]) -> Dict[str, Any]:
    details = list(policy_result.get("条件匹配详情", []) or [])
    hard_details = [detail for detail in details if _detail_group_type(detail) == "硬性"]
    special_details = [detail for detail in details if _detail_group_type(detail) == "直通车"]
    soft_details = [detail for detail in details if _detail_group_type(detail) == "软性"]

    def _pick(items: List[Dict[str, Any]], labels: set[str]) -> List[Dict[str, Any]]:
        return [item for item in items if _hit_label(str(item.get("evalStatus", "")).strip()) in labels]

    hard_hits = _pick(hard_details, {"命中"})
    hard_misses = _pick(hard_details, {"未命中"})
    hard_pending = _pick(hard_details, {"待确认"})
    special_hits = _pick(special_details, {"命中"})
    special_misses = _pick(special_details, {"未命中"})
    special_pending = _pick(special_details, {"待确认"})
    soft_hits = _pick(soft_details, {"命中"})
    soft_misses = _pick(soft_details, {"未命中"})
    soft_pending = _pick(soft_details, {"待确认"})

    return {
        "hard_hits": hard_hits,
        "hard_misses": hard_misses,
        "hard_pending": hard_pending,
        "special_hits": special_hits,
        "special_misses": special_misses,
        "special_pending": special_pending,
        "soft_hits": soft_hits,
        "soft_misses": soft_misses,
        "soft_pending": soft_pending,
        "hard_hit_summary": " | ".join(
            f"{item.get('conditionNo')}-{item.get('itemId')} {item.get('条件名称')}({item.get('得分', 0)})"
            for item in hard_hits
        ),
        "special_hit_summary": " | ".join(
            f"{item.get('conditionNo')}-{item.get('itemId')} {item.get('条件名称')}({item.get('得分', 0)})"
            for item in special_hits
        ),
        "soft_hit_summary": " | ".join(
            f"{item.get('conditionNo')}-{item.get('itemId')} {item.get('条件名称')}({item.get('得分', 0)})"
            for item in soft_hits
        ),
        "soft_score_summary": " | ".join(
            f"{item.get('conditionNo')}-{item.get('itemId')} {item.get('条件名称')}={item.get('得分', 0)}"
            for item in soft_details
        ),
    }


def _hard_blocker_summary(policy_result: Dict[str, Any]) -> Dict[str, str]:
    blocked_groups: List[str] = []
    blocked_items: List[str] = []
    blocked_texts: List[str] = []

    for group in policy_result.get("规则组匹配详情", []) or []:
        if str(group.get("groupType", "")).strip() != "硬性":
            continue

        group_status = str(group.get("groupStatus", "")).strip()
        if group_status not in {"fail", "not_evaluable", "reference_only"}:
            continue

        condition_no = str(group.get("conditionNo", "")).strip()
        if condition_no:
            blocked_groups.append(f"条件组{condition_no}（{_group_status_label(group_status)}）")
        raw_text = str(group.get("rawText", "")).strip()
        if raw_text:
            blocked_texts.append(f"条件组{condition_no}：{raw_text}")

        for item in group.get("items", []) or []:
            eval_status = str(item.get("evalStatus", "")).strip()
            if eval_status not in {"fail", "not_evaluable", "skipped"}:
                continue
            item_id = str(item.get("itemId", "")).strip()
            item_name = str(item.get("itemName", "")).strip()
            item_label = _eval_status_label(eval_status)
            blocked_items.append(f"{item_id} {item_name}（{item_label}）".strip())

    return {
        "硬性阻止条件组": "；".join(dict.fromkeys(blocked_groups)),
        "硬性阻止条件项": "；".join(dict.fromkeys(blocked_items)),
        "硬性阻止条件说明": " | ".join(dict.fromkeys(blocked_texts)),
    }


def _write_excel_output(results: List[Dict[str, Any]], output_path: Path) -> Path:
    from openpyxl import Workbook

    workbook = Workbook()
    summary_sheet = workbook.active
    summary_sheet.title = "匹配汇总"
    condition_score_sheet = workbook.create_sheet("企业条件得分")
    formula_sheet = workbook.create_sheet("总分计算说明")
    group_sheet = workbook.create_sheet("规则组明细")
    detail_sheet = workbook.create_sheet("条件明细")
    hard_detail_sheet = workbook.create_sheet("硬性明细")
    special_detail_sheet = workbook.create_sheet("直通车明细")
    soft_detail_sheet = workbook.create_sheet("软性明细")

    summary_headers = [
        "企业名称",
        "政策标题",
        "匹配链路",
        "规则判定条件项总数",
        "规则判定已执行项数",
        "硬性是否通过",
        "硬性组数",
        "直通车组数",
        "软性组数",
        "硬性门槛结果",
        "硬性门槛得分",
        "软性评分得分",
        "定性调节系数",
        "基础得分",
        "最终得分",
        "匹配率",
        "星级推荐",
        "是否推荐",
        "硬性命中项数",
        "硬性未命中项数",
        "硬性待确认项数",
        "直通车命中项数",
        "直通车未命中项数",
        "直通车待确认项数",
        "软性命中项数",
        "软性未命中项数",
        "软性待确认项数",
        "硬性命中明细",
        "直通车命中明细",
        "软性命中明细",
        "软性得分明细",
        "总体建议",
        "硬性未通过明细",
        "硬性待确认明细",
        "阻止通过的硬性条件组",
        "阻止通过的硬性条件项",
        "阻止通过的硬性条件说明",
        "风险提示",
        "匹配状态",
        "错误类型",
        "可重试",
        "重试次数",
        "错误",
        "总分计算说明",
        "科技型中小企业评价得分",
        "条件得分查看位置",
    ]
    condition_score_headers = [
        "企业名称",
        "政策标题",
        "条件类别",
        "条件编号",
        "条件名称",
        "政策要求",
        "企业字段",
        "企业取值",
        "判定结论",
        "本条件得分（100分制）",
        "运行时证据层级",
        "证据分层原因",
        "规则表原始ruleKind（仅审计）",
        "单条件实际权重",
        "全部参与计分条件数",
        "基础分贡献",
        "最终分贡献",
        "本条件如何得分",
        "本条件对总分的作用",
        "数据与核验说明",
    ]
    formula_headers = [
        "企业名称",
        "政策标题",
        "硬性条件是否全部通过",
        "A类运行时核验证据得分",
        "B类运行时辅助证据得分",
        "未核验证据条件得分",
        "运行时证据层级分布",
        "兼容系数（固定1.0）",
        "是否命中直通车",
        "最终得分",
        "本次总分计算公式",
        "结论说明",
    ]
    group_headers = [
        "企业名称",
        "政策标题",
        "条件类别",
        "条件组编号",
        "组状态",
        "组合关系",
        "组原文",
        "命中项数",
        "未命中项数",
        "待确认项数",
        "项得分明细",
        "命中项明细",
        "全部项明细",
        "直通规则说明",
        "评估角色",
        "未解析片段",
    ]
    detail_headers = [
        "企业名称",
        "政策标题",
        "条件类别",
        "条件组编号",
        "条件项编号",
        "组状态",
        "命中结果",
        "条件名称",
        "规则类型",
        "来源字段",
        "企业字段",
        "企业值",
        "目标值",
        "匹配度",
        "得分",
        "得分说明",
        "说明",
        "评分组别",
        "计分归类",
        "运行时证据层级",
        "证据分层原因",
        "规则表原始ruleKind（仅审计）",
        "单位",
        "判定状态说明",
        "匹配方式",
        "模型事实状态",
        "模型置信度",
        "证据路径",
        "证据文本",
        "模型判定依据",
    ]

    summary_sheet.append(summary_headers)
    condition_score_sheet.append(condition_score_headers)
    formula_sheet.append(formula_headers)
    group_sheet.append(group_headers)
    detail_sheet.append(detail_headers)
    hard_detail_sheet.append(detail_headers)
    special_detail_sheet.append(detail_headers)
    soft_detail_sheet.append(detail_headers)

    for enterprise_result in results:
        enterprise_name = enterprise_result.get("企业名称", "")
        for policy_result in enterprise_result.get("政策匹配结果", []) or []:
            condition_details = policy_result.get("条件匹配详情", []) or []
            bucket_counts, bucket_weights = _score_bucket_weights(condition_details)
            has_unclassified_history = any(
                str(detail.get("评分组别", "")).strip().upper() in {"A", "B"}
                and not str(detail.get("计分归类", "")).strip()
                for detail in condition_details
            )
            group_lookup = {
                str(group.get("conditionNo")): group for group in (policy_result.get("规则组匹配详情", []) or [])
            }
            hit_summary = _policy_hit_summary(policy_result)
            blocker_summary = _hard_blocker_summary(policy_result)
            summary_sheet.append(
                [
                    _format_excel_value(enterprise_name),
                    _format_excel_value(policy_result.get("政策标题")),
                    _format_excel_value(policy_result.get("匹配模式")),
                    _format_excel_value(policy_result.get("规则判定条件项总数")),
                    _format_excel_value(policy_result.get("规则判定已执行项数")),
                    _format_excel_value(policy_result.get("hard_pass")),
                    _format_excel_value(policy_result.get("hard_indicator_group_count")),
                    _format_excel_value(policy_result.get("special_pass_group_count")),
                    _format_excel_value(policy_result.get("soft_group_count")),
                    _format_excel_value(policy_result.get("hard_pass")),
                    _format_excel_value(policy_result.get("score_A")),
                    _format_excel_value(policy_result.get("score_B")),
                    _format_excel_value(policy_result.get("coeff_C")),
                    _format_excel_value(policy_result.get("raw_score")),
                    _format_excel_value(policy_result.get("final_score")),
                    _format_excel_value(policy_result.get("匹配率")),
                    _format_excel_value(policy_result.get("星级推荐")),
                    _recommendation_label(policy_result.get("是否推荐")),
                    _format_excel_value(len(hit_summary["hard_hits"])),
                    _format_excel_value(len(hit_summary["hard_misses"])),
                    _format_excel_value(len(hit_summary["hard_pending"])),
                    _format_excel_value(len(hit_summary["special_hits"])),
                    _format_excel_value(len(hit_summary["special_misses"])),
                    _format_excel_value(len(hit_summary["special_pending"])),
                    _format_excel_value(len(hit_summary["soft_hits"])),
                    _format_excel_value(len(hit_summary["soft_misses"])),
                    _format_excel_value(len(hit_summary["soft_pending"])),
                    _format_excel_value(hit_summary["hard_hit_summary"]),
                    _format_excel_value(hit_summary["special_hit_summary"]),
                    _format_excel_value(hit_summary["soft_hit_summary"]),
                    _format_excel_value(hit_summary["soft_score_summary"]),
                    _format_excel_value(policy_result.get("总体建议")),
                    _format_excel_value(policy_result.get("hard_fail_items")),
                    _format_excel_value(policy_result.get("hard_review_items")),
                    _format_excel_value(blocker_summary.get("硬性阻止条件组")),
                    _format_excel_value(blocker_summary.get("硬性阻止条件项")),
                    _format_excel_value(blocker_summary.get("硬性阻止条件说明")),
                    _format_excel_value(policy_result.get("风险提示")),
                    _format_excel_value(policy_result.get("匹配状态")),
                    _format_excel_value(policy_result.get("错误类型")),
                    _format_excel_value(policy_result.get("可重试")),
                    _format_excel_value(policy_result.get("重试次数")),
                    _format_excel_value(policy_result.get("错误")),
                    _format_excel_value(_score_formula_text(policy_result, condition_details)),
                    _tech_sme_evaluation_summary(policy_result),
                    "企业条件得分工作表",
                ]
            )
            formula_sheet.append(
                [
                    _format_excel_value(enterprise_name),
                    _format_excel_value(policy_result.get("政策标题")),
                    _format_excel_value(policy_result.get("hard_pass")),
                    _format_excel_value(
                        "历史结果未记录" if has_unclassified_history
                        else policy_result.get("score_A_direct") if bucket_counts["verified"] else "未参与"
                    ),
                    _format_excel_value(
                        "历史结果未记录" if has_unclassified_history
                        else policy_result.get("score_B_indirect") if bucket_counts["supporting"] else "未参与"
                    ),
                    _format_excel_value(
                        "历史结果未记录" if has_unclassified_history
                        else policy_result.get("未核验证据条件得分") if bucket_counts["unverified"] else "未参与"
                    ),
                    _format_excel_value(
                        "历史结果未记录" if has_unclassified_history else "；".join(
                            label
                            for label, count in (
                                (f"A类核验证据：{bucket_counts['verified']}项", bucket_counts["verified"]),
                                (f"B类辅助证据：{bucket_counts['supporting']}项", bucket_counts["supporting"]),
                                (f"未核验证据：{bucket_counts['unverified']}项", bucket_counts["unverified"]),
                            )
                            if count
                        ) or "无普通计分条件"
                    ),
                    _format_excel_value(policy_result.get("coeff_C")),
                    _format_excel_value(policy_result.get("special_pass_hit")),
                    _format_excel_value(policy_result.get("final_score")),
                    _format_excel_value(_score_formula_text(policy_result, condition_details)),
                    _format_excel_value(policy_result.get("总体建议")),
                ]
            )

            for group in policy_result.get("规则组匹配详情", []) or []:
                group_items = list(group.get("items", []) or [])
                hit_count = sum(1 for item in group_items if _hit_label(str(item.get("evalStatus", "")).strip()) == "命中")
                miss_count = sum(1 for item in group_items if _hit_label(str(item.get("evalStatus", "")).strip()) == "未命中")
                pending_count = sum(1 for item in group_items if _hit_label(str(item.get("evalStatus", "")).strip()) == "待确认")
                group_sheet.append(
                    [
                        _format_excel_value(enterprise_name),
                        _format_excel_value(policy_result.get("政策标题")),
                        _format_excel_value(group.get("groupType")),
                        _format_excel_value(group.get("conditionNo")),
                        _group_status_label(group.get("groupStatus")),
                        _condition_operator_label(group.get("conditionOperator")),
                        _format_excel_value(group.get("rawText")),
                        _format_excel_value(hit_count),
                        _format_excel_value(miss_count),
                        _format_excel_value(pending_count),
                        _format_excel_value(
                            " | ".join(
                                f"{item.get('itemId', '')} {item.get('itemName', '')}={item.get('score', 0)}"
                                for item in group_items
                            )
                        ),
                        _format_excel_value(_group_item_summary(group_items, hit_only=True)),
                        _format_excel_value(_group_item_summary(group_items, hit_only=False)),
                        _format_excel_value(group.get("specialPassEffect")),
                        _evaluation_role_label(group.get("evaluationRole")),
                        _format_excel_value(group.get("unresolvedFragments")),
                    ]
                )

            if not condition_details and policy_result.get("错误"):
                empty_row = [
                    _format_excel_value(enterprise_name),
                    _format_excel_value(policy_result.get("政策标题")),
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    _format_excel_value(policy_result.get("错误")),
                    "",
                    "",
                    "",
                    "",
                ]
                detail_sheet.append(empty_row)
                condition_score_sheet.append(
                    [
                        _format_excel_value(enterprise_name),
                        _format_excel_value(policy_result.get("政策标题")),
                        "",
                        "",
                        "未生成条件匹配明细",
                        "",
                        "",
                        "",
                        "无法判定",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        _format_excel_value(policy_result.get("错误")),
                        "匹配过程发生错误，未形成可解释的条件得分。",
                        "",
                    ]
                )
                continue

            for detail in condition_details:
                detail_group_type = _detail_group_type(detail)
                group = group_lookup.get(str(detail.get("conditionNo")), {})
                row = [
                    _format_excel_value(enterprise_name),
                    _format_excel_value(policy_result.get("政策标题")),
                    _format_excel_value(detail_group_type),
                    _format_excel_value(detail.get("conditionNo")),
                    _format_excel_value(detail.get("itemId")),
                    _group_status_label(group.get("groupStatus")),
                    _format_excel_value(_hit_label(str(detail.get("evalStatus", "")).strip())),
                    _format_excel_value(detail.get("条件名称")),
                    _rule_type_label(detail.get("规则类型")),
                    _format_excel_value(detail.get("来源字段")),
                    _format_excel_value(detail.get("企业字段")),
                    _format_excel_value(detail.get("企业值")),
                    _format_excel_value(detail.get("目标值")),
                    _format_excel_value(detail.get("匹配度")),
                    _format_excel_value(detail.get("得分")),
                    _format_excel_value(detail.get("得分说明")),
                    _format_excel_value(detail.get("说明")),
                    _score_group_label(detail.get("评分组别")),
                    _score_bucket_label(detail.get("计分归类")),
                    _format_excel_value(detail.get("evidenceTier") or "历史结果未记录"),
                    _format_excel_value(detail.get("bucketReason") or "历史结果未记录"),
                    _format_excel_value(detail.get("规则表原始ruleKind") or "历史结果未记录"),
                    _format_excel_value(detail.get("单位")),
                    _eval_status_label(detail.get("evalStatus")),
                    _format_excel_value(detail.get("匹配方式")),
                    _format_excel_value(detail.get("模型事实状态")),
                    _format_excel_value(detail.get("模型置信度")),
                    _format_excel_value(detail.get("证据路径")),
                    _format_excel_value(detail.get("证据文本")),
                    _format_excel_value(detail.get("模型判定依据")),
                ]
                detail_sheet.append(row)
                bucket = _resolved_score_bucket(detail)
                total_condition_count = sum(bucket_counts.values())
                effective_weight = f"{1 / total_condition_count:.2%}" if bucket in bucket_counts and total_condition_count else ""
                base_contribution, adjusted_contribution, effect_note = _condition_score_effect(
                    detail,
                    policy_result,
                    bucket_counts,
                    bucket_weights,
                )
                evidence_parts = [
                    str(detail.get("说明", "")).strip(),
                    "；".join(str(item) for item in detail.get("证据文本", []) or [] if str(item).strip()),
                ]
                condition_score_sheet.append(
                    [
                        _format_excel_value(enterprise_name),
                        _format_excel_value(policy_result.get("政策标题")),
                        _format_excel_value(detail_group_type),
                        _format_excel_value(f"{detail.get('conditionNo', '')}-{detail.get('itemId', '')}".strip("-")),
                        _format_excel_value(detail.get("条件名称")),
                        _format_excel_value(detail.get("政策要求") or detail.get("政策原文")),
                        _format_excel_value(detail.get("企业字段")),
                        _format_excel_value(detail.get("企业值")),
                        _format_excel_value(_hit_label(str(detail.get("evalStatus", "")).strip())),
                        _format_excel_value(detail.get("得分")),
                        _score_bucket_label(bucket),
                        _format_excel_value(detail.get("bucketReason") or "历史结果未记录"),
                        _format_excel_value(detail.get("规则表原始ruleKind") or "历史结果未记录"),
                        effective_weight,
                        _format_excel_value(total_condition_count if bucket in bucket_counts else ""),
                        _format_excel_value(base_contribution),
                        _format_excel_value(adjusted_contribution),
                        _format_excel_value(detail.get("得分说明")),
                        effect_note,
                        _format_excel_value("；".join(part for part in evidence_parts if part)),
                    ]
                )
                if detail_group_type == "硬性":
                    hard_detail_sheet.append(row)
                elif detail_group_type == "直通车":
                    special_detail_sheet.append(row)
                elif detail_group_type == "软性":
                    soft_detail_sheet.append(row)

    summary_sheet.freeze_panes = "A2"
    condition_score_sheet.freeze_panes = "A2"
    formula_sheet.freeze_panes = "A2"
    group_sheet.freeze_panes = "A2"
    detail_sheet.freeze_panes = "A2"
    hard_detail_sheet.freeze_panes = "A2"
    special_detail_sheet.freeze_panes = "A2"
    soft_detail_sheet.freeze_panes = "A2"
    _autosize_worksheet(summary_sheet)
    _autosize_worksheet(condition_score_sheet)
    _autosize_worksheet(formula_sheet)
    _autosize_worksheet(group_sheet)
    _autosize_worksheet(detail_sheet)
    _autosize_worksheet(hard_detail_sheet)
    _autosize_worksheet(special_detail_sheet)
    _autosize_worksheet(soft_detail_sheet)
    _style_worksheet(summary_sheet)
    _style_business_score_sheet(condition_score_sheet)
    _style_business_formula_sheet(formula_sheet)
    _style_worksheet(group_sheet)
    _style_worksheet(detail_sheet)
    _style_worksheet(hard_detail_sheet)
    _style_worksheet(special_detail_sheet)
    _style_worksheet(soft_detail_sheet)

    workbook.save(output_path)
    return output_path


def _emit_detailed_result_logs(
    progress_callback: Optional[Callable[[str], None]],
    enterprise_name: str,
    policy_title: str,
    result: Dict[str, Any],
) -> None:
    if not progress_callback or "错误" in result:
        return

    progress_callback(
        "评分摘要："
        f"企业={enterprise_name} 政策={policy_title} "
        f"匹配链路={result.get('匹配模式', '')} "
        f"规则判定模型已启用={result.get('规则判定模型已启用', False)} "
        f"规则判定模型={result.get('规则判定模型', '')} "
        f"硬性通过={result.get('hard_pass', False)} "
        f"score_A={result.get('score_A', 0)} "
        f"score_B={result.get('score_B', 0)} "
        f"coeff_C={result.get('coeff_C', 0)} "
        f"final_score={result.get('final_score', 0)}"
    )

    hard_fail_items = result.get("hard_fail_items", []) or []
    hard_review_items = result.get("hard_review_items", []) or []
    if hard_fail_items:
        progress_callback(f"硬性未通过项：企业={enterprise_name} 政策={policy_title} 明细={' | '.join(hard_fail_items)}")
    if hard_review_items:
        progress_callback(f"硬性待确认项：企业={enterprise_name} 政策={policy_title} 明细={' | '.join(hard_review_items)}")

    for group in result.get("规则组匹配详情", []) or []:
        progress_callback(
            "规则组详情："
            f"企业={enterprise_name} 政策={policy_title} "
            f"groupType={group.get('groupType', '')} "
            f"conditionNo={group.get('conditionNo', '')} "
            f"groupStatus={group.get('groupStatus', '')} "
            f"operator={group.get('conditionOperator', '')} "
            f"rawText={group.get('rawText', '')}"
        )
        for item in group.get("items", []) or []:
            progress_callback(
                "条件项详情："
                f"企业={enterprise_name} 政策={policy_title} "
                f"conditionNo={group.get('conditionNo', '')} "
                f"itemId={item.get('itemId', '')} "
                f"itemName={item.get('itemName', '')} "
                f"itemStatus={item.get('itemStatus', '')} "
                f"evalStatus={item.get('evalStatus', '')} "
                f"message={item.get('message', '')}"
            )


def run_matching(
    enterprises: List[Dict[str, Any]],
    policies: List[Dict[str, Any]],
    max_workers: int = 3,
    output_file: Optional[str | Path] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> List[Dict[str, Any]]:
    """执行企业-政策匹配，仅使用结构化规则项判定。"""

    policies = _deduplicate_policies(policies)
    tasks = [
        (enterprise, policy, enterprise.get("企业名称", "未知企业"))
        for enterprise in enterprises
        for policy in policies
    ]
    if progress_callback:
        progress_callback(
            f"开始执行匹配任务：企业数={len(enterprises)}，政策数={len(policies)}，任务总数={len(tasks)}，并发={max(1, max_workers)}"
        )

    enterprise_result_map: Dict[str, Dict[str, Any]] = {}
    policy_result_map: Dict[tuple[str, str], Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        future_map = {
            executor.submit(_match_single_task, task, progress_callback): task
            for task in tasks
        }
        for future in as_completed(future_map):
            enterprise, policy, enterprise_name = future_map[future]
            result = future.result()
            if enterprise_name not in enterprise_result_map:
                snapshot = enterprise.get("_enterprise_standard_snapshot", {})
                enterprise_entry = {
                    "企业名称": enterprise_name,
                    "企业信息": _enterprise_summary(enterprise),
                    "政策匹配结果": [],
                }
                if snapshot:
                    enterprise_entry["enterprise_standard_snapshot"] = snapshot
                    enterprise_entry["企业标准JSON"] = snapshot
                enterprise_result_map[enterprise_name] = enterprise_entry
            enterprise_result_map[enterprise_name]["政策匹配结果"].append(result)
            policy_id = _first_non_empty_text(policy.get("id"), policy.get("fusionPolicyId"))
            if policy_id:
                policy_result_map[(enterprise_name, policy_id)] = result
            if progress_callback:
                policy_title = policy.get("title", "")
                if "错误" in result:
                    progress_callback(
                        f"匹配失败：企业={enterprise_name} 政策={policy_title} 错误={result.get('错误', '')}"
                    )
                else:
                    progress_callback(
                        "匹配完成："
                        f"企业={enterprise_name} 政策={policy_title} "
                        f"最终分={result.get('final_score', 0)} "
                        f"硬性通过={result.get('hard_pass', False)} "
                        f"星级={result.get('星级推荐', '')}"
                    )
                    _emit_detailed_result_logs(progress_callback, enterprise_name, policy_title, result)

    # 严格分为两个阶段：全部申报条件匹配结束后才启动关联模型调用，
    # 避免两类模型请求叠加后突破既有 MAX_WORKERS 容量边界。
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        association_future_map = {
            executor.submit(
                assess_policy_association,
                enterprise,
                policy,
                progress_callback,
            ): (enterprise, policy, enterprise_name)
            for enterprise, policy, enterprise_name in tasks
        }
        for future in as_completed(association_future_map):
            _enterprise, policy, enterprise_name = association_future_map[future]
            policy_id = _first_non_empty_text(policy.get("id"), policy.get("fusionPolicyId"))
            target = policy_result_map.get((enterprise_name, policy_id))
            if target is None:
                continue
            try:
                target["associationMatch"] = future.result()
            except Exception as exc:  # noqa: BLE001
                target["associationMatch"] = retryable_association_result(
                    "association_internal_error",
                    str(exc),
                    1,
                )

    final_results = list(enterprise_result_map.values())
    for item in final_results:
        # 双键排序：匹配分优先，同分时按证据覆盖率排序，让已核验政策排在纯默认放行政策之前。
        # 「匹配失败待重试」记录不带 final_score / 匹配率 键，默认值必须取 None 而不是 0，
        # 否则会混进 0 分档中间；_sort_number 把 None 归为 -1，使其稳定排到末尾。
        item["政策匹配结果"].sort(
            key=lambda record: (
                _sort_number(record.get("final_score", record.get("匹配率"))),
                _sort_number(record.get("证据覆盖率")),
            ),
            reverse=True,
        )

    if output_file:
        target = Path(output_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(final_results, ensure_ascii=False, indent=2), encoding="utf-8")
        if progress_callback:
            progress_callback(f"匹配结果 JSON 已写入: {target}")
        excel_target = target.with_suffix(".xlsx")
        actual_excel_target = excel_target
        try:
            _write_excel_output(final_results, excel_target)
        except PermissionError:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            actual_excel_target = excel_target.with_name(f"{excel_target.stem}_{timestamp}{excel_target.suffix}")
            _write_excel_output(final_results, actual_excel_target)
            if progress_callback:
                progress_callback(
                    f"原 Excel 文件被占用，已改写入新文件: {actual_excel_target}"
                )
        if progress_callback:
            progress_callback(f"匹配结果 Excel 已写入: {actual_excel_target}")

    if progress_callback:
        progress_callback(f"全部匹配任务完成，结果企业数={len(final_results)}")

    return final_results


def _match_single_task(
    args: tuple,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    enterprise, policy, _enterprise_name = args
    policy_title = policy.get("title", "")

    try:
        rule_profile = resolve_rule_profile(policy)
        semantic_assessment = (
            assess_enterprise_policy(enterprise, policy, rule_profile, progress_callback)
            if rule_profile else None
        )
        score_result = score_policy_against_enterprise(enterprise, policy, semantic_assessment=semantic_assessment)
        explanation = _local_explanation(score_result)
        score_result["LLM解释已启用"] = bool((semantic_assessment or {}).get("enabled"))
        score_result["解释模型跳过原因"] = "模型仅负责语义字段映射和证据提取；硬性判断及评分由本地规则引擎执行。"
        if semantic_assessment:
            score_result["模型语义判定错误"] = semantic_assessment.get("error", "")

        score_result.update(explanation)
        score_result.setdefault("推荐阻断项", [])
        score_result.update(_hard_blocker_summary(score_result))
        score_result["政策标题"] = policy_title
        # 带上政策自身 id。缺这一项时下游只能按标题反查政策表回填，
        # 同名不同政策（如集美区两条同名扶持政策 6529 / 6569）会被回填成同一个 id。
        score_result["fusionPolicyId"] = _first_non_empty_text(policy.get("id"), policy.get("fusionPolicyId"))
        snapshot = enterprise.get("_enterprise_standard_snapshot", {})
        if snapshot:
            score_result["enterprise_standard_snapshot"] = snapshot
        return score_result
    except SemanticMatchRetryableError as exc:
        return {
            "政策标题": policy_title,
            "fusionPolicyId": _first_non_empty_text(policy.get("id"), policy.get("fusionPolicyId")),
            "匹配状态": "匹配失败待重试",
            "错误类型": exc.error_type,
            "可重试": True,
            "重试次数": max(0, exc.attempts - 1),
            "是否推荐": None,
            "错误": str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "政策标题": policy_title,
            "fusionPolicyId": _first_non_empty_text(policy.get("id"), policy.get("fusionPolicyId")),
            "错误": str(exc),
        }
