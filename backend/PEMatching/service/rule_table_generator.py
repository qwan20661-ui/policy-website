"""规则表 JSON 生成器 —— 从融合政策详情的申报条件用 LLM 生成规则表。

读取外部提示词文件 prompts/rule_table_generation_prompt.md，
使用 config/ 下的申报条件管理 JSON 作为标准字段参考。
"""

from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

LLM_API_URL = (os.getenv("API_URL", "") or "").strip()
LLM_API_KEY = (os.getenv("API_KEY", "") or "").strip()
LLM_MODEL_NAME = (os.getenv("MODEL_NAME", "") or "").strip()
LLM_CONNECT_TIMEOUT = int(os.getenv("RULE_LLM_CONNECT_TIMEOUT", "10"))
LLM_TIMEOUT = int(os.getenv("RULE_LLM_TIMEOUT", "300"))
RULE_TABLE_CONDITION_WORKERS = max(1, int(os.getenv("RULE_TABLE_CONDITION_WORKERS", "10")))
STRUCTURE_API_BASE_URL = os.getenv("STRUCTURE_API_BASE_URL", "http://127.0.0.1:8080")

PROMPT_FILE = PROJECT_ROOT / "prompts" / "rule_table_generation_prompt.md"
CONFIG_DIR = PROJECT_ROOT / "config"

SCORING_GROUP_KEYS = ("hardRuleGroups", "softRuleGroups", "specialPassRuleGroups")
RULE_TABLE_LIST_KEYS = (*SCORING_GROUP_KEYS, "definitionNotes", "applicationChecklistGroups")


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return "；".join(_safe_text(item) for item in value if _safe_text(item))
    return str(value).strip()


def _llm_endpoint_for_log() -> str:
    """返回可安全记录的模型接口地址，不包含鉴权信息或查询参数。"""
    parsed = urlparse(LLM_API_URL)
    if not parsed.scheme or not parsed.netloc:
        return "未配置或格式无效"
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path or '/'}"


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
        return _decode_literal_unicode_escapes(json.loads(json_text))
    except json.JSONDecodeError:
        fixed_text = re.sub(r",(\s*[}\]])", r"\1", json_text)
        return _decode_literal_unicode_escapes(json.loads(fixed_text))


def _decode_literal_unicode_escapes(value: Any) -> Any:
    """修正模型偶发双重转义的 \\uXXXX，防止规则表在页面显示为转义文本。"""
    if isinstance(value, dict):
        return {key: _decode_literal_unicode_escapes(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode_literal_unicode_escapes(item) for item in value]
    if not isinstance(value, str):
        return value
    current = value
    for _ in range(3):
        normalized = re.sub(
            r"\\+u([0-9a-fA-F]{4})",
            lambda match: chr(int(match.group(1), 16)),
            current,
        )
        if normalized == current:
            break
        current = normalized
    return current


def _load_prompt_template() -> str:
    """从 prompts/rule_table_generation_prompt.md 加载完整提示词模板。

    将 System Prompt 和 User Prompt Template 合并为单一模板，
    确保所有规则 + 数据 + 字段列表都在同一条消息中发给模型。

    Returns:
        完整模板字符串，含 {policy_title}/{applicable_target}/... 占位符
    """
    if not PROMPT_FILE.exists():
        raise FileNotFoundError(f"提示词文件不存在: {PROMPT_FILE}")

    content = PROMPT_FILE.read_text(encoding="utf-8")

    # 提取 System Prompt
    system_match = re.search(r"## System Prompt\s*\n(.*?)(?=\n## (?:核心规则|输出格式|User Prompt))", content, re.DOTALL)
    if not system_match:
        raise ValueError("提示词文件中未找到 '## System Prompt' 段落")
    system_text = system_match.group(1).strip()

    # 提取后面的所有规则 + 格式 + User Template（即 System Prompt 之后的所有内容）
    rest_start = system_match.end()
    rest_text = content[rest_start:].strip()

    # 合并：规则和格式说明 + 数据占位符都在一条消息里
    combined = system_text + "\n\n" + rest_text
    return combined


def _sync_and_load_field_entries() -> List[Dict[str, Any]]:
    """加载当天申报条件字段条目；当天无快照时从 API 同步。"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config_file = CONFIG_DIR / "申报条件管理.json"
    cached_entries = _load_field_snapshot(config_file)
    if cached_entries:
        print(f"[RuleTable] 使用当天申报条件管理快照: {config_file.name} ({len(cached_entries)} 条)")
        return cached_entries

    api_entries = _try_fetch_api_fields()
    if not api_entries:
        print("[RuleTable] API 未返回申报条件管理字段，且当天无可用快照")
        return []
    print(f"[RuleTable] 已从 API 同步最新申报条件管理字段: {len(api_entries)} 条")

    normalized = {
        "schemaVersion": "v1",
        "source": {"page": "/index.html#base-data-condition", "moduleType": "application_condition",
                    "endpoint": "/api/categoryFieldInfo", "exportedAt": datetime.now().isoformat()},
        "summary": {
            "total": len(api_entries),
            "level1Count": len(set(e.get("level1Category", "") for e in api_entries if e.get("level1Category"))),
        },
        "data": api_entries,
    }
    temporary_file = config_file.with_suffix(".tmp")
    temporary_file.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_file.replace(config_file)
    print(f"[RuleTable] 已保存当天 API 快照到 config: {config_file.name} ({len(api_entries)} 条)")

    return api_entries


def _sync_and_load_field_reference() -> str:
    """加载当天申报条件字段快照并转换为提示词字段参考。"""
    entries = _sync_and_load_field_entries()
    if not entries:
        return "（未找到标准字段配置）"
    return _build_field_reference_text(entries)


def _load_field_snapshot(config_file: Path) -> List[Dict[str, Any]]:
    """只加载当天且结构完整的申报条件字段快照。"""
    if not config_file.is_file():
        return []
    try:
        payload = json.loads(config_file.read_text(encoding="utf-8"))
        entries = payload.get("data") if isinstance(payload, dict) else None
        exported_at = ((payload.get("source") or {}).get("exportedAt") if isinstance(payload, dict) else "")
        if (
            not isinstance(entries, list)
            or not entries
            or not all(isinstance(item, dict) for item in entries)
            or not str(exported_at).startswith(datetime.now().date().isoformat())
        ):
            return []
        return entries
    except (OSError, json.JSONDecodeError):
        return []


def _try_fetch_api_fields() -> List[Dict[str, Any]]:
    """从分页接口拉取全部最新字段，转换为标准格式。"""
    api_url = f"{STRUCTURE_API_BASE_URL}/api/categoryFieldInfo"
    try:
        first_response = requests.get(api_url, params={"page": 1, "size": 1}, timeout=30)
        first_response.raise_for_status()
        first_data = first_response.json()
        if not isinstance(first_data, dict) or not first_data.get("success", False):
            print("[RuleTable] API 未返回成功的申报条件管理响应")
            return []

        pagination = first_data.get("pagination") or {}
        total = int(pagination.get("total") or len(first_data.get("data") or []))
        if total <= 0:
            return []

        response = requests.get(api_url, params={"page": 1, "size": total}, timeout=30)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or not data.get("success", False):
            print("[RuleTable] API 全量申报条件管理响应失败")
            return []
        raw = data.get("data") or []
        if len(raw) != total:
            print(f"[RuleTable] API 申报条件管理数量不完整: 期望 {total} 条，实际 {len(raw)} 条")
            return []
        if not raw:
            return []
        # 转换: name → level3Category, 补充 levelPath/rowNo/sourcePage
        entries = []
        for idx, item in enumerate(raw, start=1):
            l1 = _safe_text(item.get("level1Category"))
            l2 = _safe_text(item.get("level2Category"))
            l3 = _safe_text(item.get("level3Category") or item.get("name"))
            entries.append({
                "rowNo": idx,
                "id": _safe_text(item.get("id")),
                "level1Category": l1, "level2Category": l2, "level3Category": l3,
                "coreField": _safe_text(item.get("coreField")),
                "status": _safe_text(item.get("status")),
                "createdBy": _safe_text(item.get("createdBy")),
                "updatedBy": _safe_text(item.get("updatedBy")),
                "createTime": _safe_text(item.get("createTime")),
                "updateTime": _safe_text(item.get("updateTime")),
                "levelPath": [l1, l2, l3] if l1 else [],
                "sourcePage": "/index.html#base-data-condition",
            })
        return entries
    except Exception as exc:
        print(f"[RuleTable] API 获取跳过: {exc}")
        return []


def _build_field_reference_text(entries: List[Dict[str, Any]]) -> str:
    """将申报条件管理条目列表构建为扁平字段选择列表。

    每行格式: level3Category → enterpriseField
    分类信息保留在括号中作为辅助参考。
    """
    if not entries:
        return "（标准字段配置为空）"

    lines: List[str] = []
    for entry in entries:
        l1 = _safe_text(entry.get("level1Category"))
        l2 = _safe_text(entry.get("level2Category"))
        l3 = _safe_text(entry.get("level3Category"))
        core = _safe_text(entry.get("coreField"))
        if not l3:
            continue
        # 格式: level3Category → enterpriseField  (分类: level1 / level2)
        ef = core if core else l3
        lines.append(f"  {l3} → {ef}  ({l1} / {l2})")

    # 去重
    seen = set()
    unique = []
    for line in lines:
        if line not in seen:
            seen.add(line)
            unique.append(line)

    print(f"[RuleTable] 已加载 {len(unique)} 个可选字段")
    return "\n".join(unique)


def _split_field_aliases(value: Any) -> List[str]:
    """将标准字段别名拆分为可匹配的候选词。"""
    text = _safe_text(value)
    if not text:
        return []

    tokens = [text]
    tokens.extend(re.split(r"[、,，；;（）()\s]+", text))

    unique: List[str] = []
    seen = set()
    for token in tokens:
        normalized = str(token).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


def _build_standard_field_lookup(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """构建标准字段索引，供生成结果标准化使用。"""
    by_level3: Dict[str, Dict[str, Any]] = {}
    alias_candidates: Dict[str, List[Dict[str, Any]]] = {}

    for entry in entries:
        level3 = _safe_text(entry.get("level3Category"))
        if not level3:
            continue

        canonical = {
            "rowNo": entry.get("rowNo"),
            "level1Category": _safe_text(entry.get("level1Category")),
            "level2Category": _safe_text(entry.get("level2Category")),
            "level3Category": level3,
            "coreField": _safe_text(entry.get("coreField")),
        }
        by_level3[level3] = canonical

        for alias in _split_field_aliases(entry.get("coreField")):
            alias_candidates.setdefault(alias, []).append(canonical)

    return {
        "by_level3": by_level3,
        "by_alias": alias_candidates,
    }


def _resolve_standard_field(candidate: Any, lookup: Dict[str, Any]) -> Dict[str, Any] | None:
    """按 level3Category 或唯一别名解析标准字段。"""
    text = _safe_text(candidate)
    if not text:
        return None

    by_level3 = lookup.get("by_level3") or {}
    if text in by_level3:
        return by_level3[text]

    for level3, entry in by_level3.items():
        if text.startswith(f"{level3}-") or text.startswith(f"{level3}：") or text.startswith(f"{level3}:"):
            return entry

    alias_hits = lookup.get("by_alias", {}).get(text) or []
    unique_hits = {hit.get("level3Category"): hit for hit in alias_hits if hit.get("level3Category")}
    if len(unique_hits) == 1:
        return next(iter(unique_hits.values()))

    return None


def _find_item_standard_field(item: Dict[str, Any], lookup: Dict[str, Any]) -> Dict[str, Any] | None:
    """尽量把生成项回对齐到最新标准字段。"""
    source_field = item.get("sourceStandardField")
    if isinstance(source_field, dict):
        for candidate in (
            source_field.get("level3Category"),
            source_field.get("coreField"),
        ):
            matched = _resolve_standard_field(candidate, lookup)
            if matched:
                return matched

    for candidate in (
        item.get("itemName"),
        *(item.get("enterpriseFields") or []),
    ):
        matched = _resolve_standard_field(candidate, lookup)
        if matched:
            return matched

    return None


def _resolve_policy_fragment_standard_field(fragment: Any, lookup: Dict[str, Any]) -> Dict[str, Any] | None:
    """为原文中的明确企业事实补充受控的标准字段映射。"""
    content = _safe_text(fragment)
    semantic_field_map = (
        (("科研失信", "科研诚信失信"), "科研信用"),
        (("信用联合惩戒", "严重失信", "失信记录"), "企业信用"),
        (("伦理审查", "科研伦理"), "伦理合规"),
        (("在厦实际经营", "实际经营地"), "实际经营地址"),
        (("注册地", "注册地址", "注册在", "在厦注册", "注册成立地"), "注册地址"),
        (("所属园区", "高新区", "自贸区", "属地"), "属地信息"),
        (("纳税地", "本地纳税", "纳税属地"), "纳税属地"),
        (("注册成立", "成立年限", "经营年限", "成立满", "成立于", "设立于"), "成立年限"),
        (("知识产权纠纷", "知识产权归属"), "知识产权约定"),
    )
    for markers, level3_category in semantic_field_map:
        if any(marker in content for marker in markers):
            return (lookup.get("by_level3") or {}).get(level3_category)
    return None


def _looks_like_evaluation_metric(text: Any) -> bool:
    """判断文本是否属于评价指标/得分/分值这类派生语义。"""
    content = _safe_text(text)
    if not content:
        return False

    metric_markers = [
        "评价指标",
        "综合评价",
        "总得分",
        "得分",
        "分值",
        "评分",
        "科技人员指标",
    ]
    return any(marker in content for marker in metric_markers)


def _prefer_semantic_item_name(item: Dict[str, Any], matched: Dict[str, Any] | None) -> bool:
    """判断当前条件项是否应保留更贴近政策语义的 itemName。"""
    current_name = _safe_text(item.get("itemName"))
    fragment = _safe_text(item.get("policyFragment"))
    description = _safe_text((item.get("matchRule") or {}).get("description"))
    enterprise_fields = item.get("enterpriseFields") or []
    current_candidates = [current_name, fragment, description, *enterprise_fields]
    if any(_looks_like_evaluation_metric(candidate) for candidate in current_candidates):
        return True

    if not matched:
        return False

    canonical_name = _safe_text(matched.get("level3Category"))
    if canonical_name and current_name and current_name != canonical_name:
        current_name_is_specific = len(current_name) > len(canonical_name)
        has_semantic_suffix = any(token in current_name for token in ("-", "：", ":", "得分", "评价", "指标"))
        if current_name_is_specific or has_semantic_suffix:
            return True

    return False


def _should_drop_misleading_standard_field(item: Dict[str, Any], matched: Dict[str, Any]) -> bool:
    """避免把评价得分类概念错误映射到信用等不相关标准字段。"""
    if not matched:
        return False

    level3 = _safe_text(matched.get("level3Category"))
    core_field = _safe_text(matched.get("coreField"))
    combined_text = " ".join(
        [
            _safe_text(item.get("itemName")),
            _safe_text(item.get("policyFragment")),
            _safe_text((item.get("matchRule") or {}).get("description")),
            _safe_text(item.get("enterpriseFields")),
        ]
    )
    if not _looks_like_evaluation_metric(combined_text):
        return False

    misleading_targets = {"企业信用", "工商信用", "科研信用", "安全生产"}
    misleading_core_markers = ["是否黑名单", "失信", "经营异常", "严重违法"]
    if level3 in misleading_targets:
        return True
    return any(marker in core_field for marker in misleading_core_markers)


def _requires_semantic_reference(item: Dict[str, Any], matched: Dict[str, Any] | None) -> bool:
    """识别只能作为语义核验线索、不能直接等价到标准字段的条件。"""
    fragment = " ".join(
        [
            _safe_text(item.get("policyFragment")),
            _safe_text(item.get("itemName")),
            _safe_text((item.get("matchRule") or {}).get("description")),
        ]
    )
    level3 = _safe_text((matched or {}).get("level3Category"))

    if level3 == "技术交易额" and any(
        marker in fragment for marker in ("试验验证", "检测认证", "规模化应用", "场景开放", "场景适配")
    ):
        return True

    return level3 == "投资单位" and any(
        marker in fragment for marker in ("直接持股", "股份占比", "互相持股", "第三方直接持股")
    )


def _is_standard_field_semantically_compatible(item: Dict[str, Any], matched: Dict[str, Any]) -> bool:
    """仅在标准字段的名称或 coreField 别名能从原文回证时允许直接映射。"""
    content = " ".join(
        [_safe_text(item.get("policyFragment")), _safe_text((item.get("matchRule") or {}).get("description"))]
    )
    normalized_content = re.sub(r"(?:上一|当|本|该|年度|年|度)", "", content)
    candidates = [
        _safe_text(matched.get("level3Category")),
        *_split_field_aliases(matched.get("coreField")),
    ]
    for candidate in candidates:
        normalized_candidate = re.sub(r"(?:上一|当|本|该|年度|年|度|率)", "", candidate)
        if len(normalized_candidate) >= 2 and normalized_candidate in normalized_content:
            return True
    return False


def _infer_quantitative_item_name(item: Dict[str, Any], rejected_standard_name: str = "") -> str:
    """保留模型从原文给出的字段名；模型未给出主语时只做通用原文截取。"""
    fragment = _safe_text(item.get("policyFragment"))
    current_name = _safe_text(item.get("itemName"))
    if current_name and current_name != rejected_standard_name:
        return current_name

    threshold_match = re.search(
        r"([^；，。:：]{2,32}?)(?:不低于|不少于|不超过|不高于|达到|达|超过|高于|低于|大于|小于)\s*\d",
        fragment,
    )
    if threshold_match:
        candidate = re.sub(r"^(?:当年|本年度|上一年度|当年度|近\d+年|\d{4}年度)", "", threshold_match.group(1))
        candidate = candidate.strip(" ，、；:：")
        if candidate:
            return candidate
    return ""


def _keep_unmapped_quantitative_item(item: Dict[str, Any], rejected_standard_name: str = "") -> bool:
    """以模型提取的原文业务字段保存未映射数值项；无主语时拒绝伪规则。"""
    item_name = _infer_quantitative_item_name(item, rejected_standard_name)
    if not item_name:
        return False
    item["itemName"] = item_name
    item["enterpriseFields"] = [item_name]
    item["sourceStandardField"] = None
    item["itemStatus"] = "unresolved"
    match_rule = item.setdefault("matchRule", {})
    if _safe_text(match_rule.get("ruleKind")) in {"", "direct"}:
        match_rule["ruleKind"] = "reference"
    match_rule["fieldType"] = "number"
    return True


def _mark_as_semantic_reference(item: Dict[str, Any]) -> None:
    """将非等价字段绑定降级为待核验的语义证据，不再伪装成直接规则。"""
    item["itemStatus"] = "unresolved"
    item["sourceStandardField"] = None
    match_rule = item.setdefault("matchRule", {})
    if _safe_text(match_rule.get("ruleKind")) in {"", "direct"}:
        match_rule["ruleKind"] = "reference"
    match_rule["fieldType"] = "string"
    match_rule["operator"] = "llm_judge"


def _looks_like_total_score_rule(item: Dict[str, Any]) -> bool:
    """判断是否为综合评价总得分门槛。"""
    combined_text = " ".join(
        [
            _safe_text(item.get("itemName")),
            _safe_text(item.get("policyFragment")),
            _safe_text((item.get("matchRule") or {}).get("description")),
        ]
    )
    if "科技人员指标得分" in combined_text:
        return False
    # 只有科技型中小企业评价才可套用本地三项分档计算；其他认定的综合得分不能混用。
    is_tech_sme_evaluation = "科技型中小企业" in combined_text or "中小企业评价指标" in combined_text
    return is_tech_sme_evaluation and (
        ("综合评价" in combined_text and ("得分" in combined_text or "分值" in combined_text))
        or "总得分" in combined_text
    )


def _looks_like_staff_score_rule(item: Dict[str, Any]) -> bool:
    """判断是否为科技人员指标得分相关条件。"""
    combined_text = " ".join(
        [
            _safe_text(item.get("itemName")),
            _safe_text(item.get("policyFragment")),
            _safe_text((item.get("matchRule") or {}).get("description")),
        ]
    )
    return "科技人员指标得分" in combined_text


def _apply_semantic_metric_corrections(item: Dict[str, Any]) -> bool:
    """对评价指标/得分类条件项做语义纠偏。"""
    corrected = False
    if _looks_like_total_score_rule(item):
        item["itemName"] = "中小企业评价指标"
        item["enterpriseFields"] = ["总得分"]
        item["sourceStandardField"] = None
        corrected = True
    elif _looks_like_staff_score_rule(item):
        item["itemName"] = "科技人员指标得分"
        item["enterpriseFields"] = ["企业职工总数", "科技人员数量", "科技人员占比", "总得分"]
        item["sourceStandardField"] = None
        corrected = True

    if corrected:
        item["itemStatus"] = _safe_text(item.get("itemStatus")) or "rule"
    return corrected


def _looks_like_reward_formula(text: Any) -> bool:
    """判断文本是否更像奖励/补助计算公式，而非申报条件。"""
    content = _safe_text(text)
    if not content:
        return False

    # “单项资助金额不超过 X 万元”是申报约束，不能按奖励公式删除。
    if "单项资助金额" in content:
        return False

    reward_markers = [
        "给予奖励",
        "给予补助",
        "补助标准",
        "奖励标准",
        "补贴标准",
        "资助标准",
        "奖励金额",
        "补助金额",
        "按1%给予奖励",
        "按0.5%给予奖励",
        "按照1%给予奖励",
        "按照0.5%给予奖励",
        "按一定比例给予",
        "按比例给予",
        "拨付比例",
        "奖补",
        "封顶",
        "奖补",
    ]
    if any(marker in content for marker in reward_markers):
        return True
    return (
        any(marker in content for marker in ("最高不超过", "最高不高于", "封顶", "上限"))
        and any(marker in content for marker in ("奖励", "补助", "补贴", "资助"))
    )


def _looks_like_explicit_quantitative_requirement(text: Any) -> bool:
    """识别具有可计算门槛的条件，不能因同时出现材料/中介词被分流。"""
    content = _safe_text(text)
    if not content:
        return False
    threshold_markers = ("不超过", "不低于", "不少于", "达到", "超过", "以上", "以下")
    quantitative_subjects = ("资助金额", "自筹经费", "综合得分", "评价得分", "人数占比", "在职职工", "注册会计师", "税务师")
    return any(marker in content for marker in threshold_markers) and any(
        subject in content for subject in quantitative_subjects
    )


def _is_application_checklist_text(text: Any) -> bool:
    """判断材料、流程、时效和中介机构要求；这些内容不参与企业匹配评分。"""
    content = _safe_text(text)
    if _is_hard_exclusion_condition(content):
        return False
    if _looks_like_explicit_quantitative_requirement(content):
        return False
    checklist_markers = [
        "申报材料", "申请书", "上传材料", "营业执照复印件", "审计报告", "鉴证报告",
        "财务会计报告", "纳税申报表", "中介机构", "注册会计师", "税务师",
        "报告编码", "二维码", "报告备案", "截止时间", "逾期", "第一批", "第二批",
        "系统申报", "系统提交", "无纸化", "纸质材料", "告知承诺", "材料真实性",
        "弄虚作假", "登录", "填报", "办理流程",
    ]
    return any(marker in content for marker in checklist_markers)


def _is_hard_exclusion_condition(text: Any) -> bool:
    """判断不可逆的合规或资格排除事实，不能误分流为申报清单。"""
    content = _safe_text(text)
    exclusion_markers = (
        "严重违法",
        "严重失信",
        "科研失信",
        "信用联合惩戒",
        "经营异常",
        "黑名单",
        "惩戒执行期",
    )
    if any(marker in content for marker in exclusion_markers):
        return True
    return ("不予受理" in content or "不得申报" in content or "禁止申报" in content) and any(
        marker in content for marker in ("失信", "违法", "经营异常", "黑名单", "惩戒")
    )


def _checklist_type(text: Any) -> str:
    content = _safe_text(text)
    if any(marker in content for marker in ("截止", "逾期", "第一批", "第二批", "时间")):
        return "时效"
    if any(marker in content for marker in ("中介机构", "注册会计师", "税务师", "报告编码", "二维码", "报告备案")):
        return "中介机构"
    if any(marker in content for marker in ("真实性", "承诺", "弄虚作假")):
        return "承诺"
    if any(marker in content for marker in ("流程", "系统申报", "无纸化", "登录", "填报")):
        return "流程"
    return "材料"


def _is_staff_workday_definition(item: Dict[str, Any]) -> bool:
    """防止把科技人员统计口径中的工作天数误写成科技人员人数阈值。"""
    rule = item.get("matchRule") or {}
    combined = " ".join(
        [
            _safe_text(item.get("itemName")),
            _safe_text(item.get("policyFragment")),
            _safe_text(rule.get("description")),
            _safe_text(rule.get("targetValue")),
            _safe_text(rule.get("unit")),
        ]
    )
    return (
        "科技人员" in combined
        and "183" in combined
        and ("工作时间" in combined or "实际工作" in combined or _safe_text(rule.get("unit")) == "天")
    )


def _append_definition_note(rule_table_json: Dict[str, Any], item: Dict[str, Any]) -> None:
    """将人员工作天数等统计口径保存为说明，避免进入评分规则。"""
    notes = rule_table_json.setdefault("definitionNotes", [])
    content = _safe_text(item.get("policyFragment")) or _safe_text(item.get("itemName"))
    if not content or any(_safe_text(note.get("content")) == content for note in notes if isinstance(note, dict)):
        return
    notes.append(
        {
            "noteId": f"D{len(notes) + 1}",
            "subject": "科技人员",
            "noteType": "统计口径",
            "content": content,
            "affectsFields": ["科技人员数量"],
        }
    )


def _is_soft_signal(text: Any) -> bool:
    """仅根据原文的评分、优先或鼓励语义判断软性条件。"""
    content = _safe_text(text)
    if not content:
        return False

    soft_markers = [
        "优先支持",
        "优先推荐",
        "择优支持",
        "同等条件下优先",
        "加分项",
        "鼓励项",
        "参考项",
        "评分",
        "得分",
        "分值",
        "评价指标",
        "综合评价",
    ]
    return any(marker in content for marker in soft_markers)


def _is_hard_policy_requirement(text: Any) -> bool:
    """仅依据申报要求原文的资格、限制和门槛语义判定硬性条件。"""
    content = _safe_text(text)
    if not content:
        return False

    hard_markers = (
        "限制条件",
        "申报条件",
        "资格条件",
        "须",
        "必须",
        "应当",
        "不得",
        "不予受理",
        "不予支持",
        "禁止申报",
        "方可",
        "仅限",
        "不低于",
        "不少于",
        "不超过",
        "不高于",
        "至少",
        "至多",
        "超过",
        "高于",
        "低于",
        "大于",
        "小于",
        "以上",
        "以下",
        "达到",
        "应在",
        "应于",
        "应为",
    )
    return any(marker in content for marker in hard_markers)


def _is_alternative_policy_requirement(text: Any) -> bool:
    """判断原文是否要求若干资格条件满足其一，不能把每一项都作为硬性必选。"""
    content = _safe_text(text)
    alternative_markers = ("条件之一", "满足其一", "任一条件", "以下任一", "具备其一", "符合其一")
    return any(marker in content for marker in alternative_markers)


def _is_special_pass_signal(text: Any) -> bool:
    """判断文本是否明确表达直通车/直接确认等特殊直通语义。"""
    content = _safe_text(text)
    if not content:
        return False

    special_markers = [
        "直通",
        "直通车",
        "可直接确认",
        "可直接认定",
        "可直接通过",
        "直接确认符合",
        "直接认定为",
        "不受综合评价所得分值高低限制",
        "不受综合评价得分高低限制",
        "不受综合评价分值高低限制",
    ]
    return any(marker in content for marker in special_markers)


def _ensure_rule_group_arrays(rule_table_json: Dict[str, Any]) -> None:
    """确保规则表的评分、口径和申报清单容器均为数组。"""
    for group_key in RULE_TABLE_LIST_KEYS:
        groups = rule_table_json.get(group_key)
        if not isinstance(groups, list):
            rule_table_json[group_key] = []


def _is_quantitative_condition_item(item: Dict[str, Any]) -> bool:
    """判断条件项是否有明确的数值目标，供分组时保留其定量语义。"""
    rule = item.get("matchRule") or {}
    target = rule.get("targetValue")
    if _safe_text(rule.get("fieldType")).lower() != "number":
        return False
    try:
        float(target)
    except (TypeError, ValueError):
        return False
    return _safe_text(rule.get("operator")).lower() in {"gt", "gte", "lt", "lte", "equals"}


def _quantitative_threshold_matches(text: Any) -> List[re.Match[str]]:
    """定位原文中带明确比较关系的阿拉伯数字门槛。"""
    content = _safe_text(text).replace(",", "").replace("，", "")
    if not content:
        return []
    unit = r"(?:亿元|万元|万美元|元|%|％|分|人|家|项|件|个|年|个月|月|天|平方米|平米|份|场次|场|次|倍|周岁|岁|万次|亿次)?"
    patterns = (
        rf"(?:不低于|不少于|不超过|不高于|不得为|达到|达|超过|高于|低于|大于|小于|至少|至多|≥|≤|>|<)\s*\d+(?:\.\d+)?\s*{unit}(?:\s*\([^)]*含[^)]*\)|\s*（[^）]*含[^）]*）)?(?:\s*(?:以上|以下|以内))?",
        rf"\d+(?:\.\d+)?\s*{unit}(?:\s*\([^)]*含[^)]*\)|\s*（[^）]*含[^）]*）)?\s*(?:及以上|及以下|以上|以下|以内|起)",
        r"(?:不低于|不少于|不超过|不高于|达到|达)\s*\d+(?:\.\d+)?\s*:\s*\d+(?:\.\d+)?",
    )
    matches: List[re.Match[str]] = []
    occupied: List[Tuple[int, int]] = []
    for pattern in patterns:
        for matched in re.finditer(pattern, content):
            span = matched.span()
            if any(span[0] < end and start < span[1] for start, end in occupied):
                continue
            matches.append(matched)
            occupied.append(span)
    return sorted(matches, key=lambda matched: matched.start())


def _count_quantitative_thresholds(text: Any) -> int:
    """统计可独立核验的数字门槛数量，供定量完整性复核使用。"""
    return len(_quantitative_threshold_matches(text))


def _route_rule_groups(rule_table_json: Dict[str, Any]) -> None:
    """将可匹配条件收敛为独立的推荐信号。

    规则表用于企业政策推荐，不是完整的申报资格裁决器。模型通常只能提取
    “条件之一”或“或”分支中的部分数值，不能据此还原完整逻辑树；因此每个
    可匹配项独立放入软性组，避免把不同项目、档位或备选路径错误叠为硬性 AND/ANY。
    """
    _ensure_rule_group_arrays(rule_table_json)

    routed_hard_groups: List[Dict[str, Any]] = []
    routed_soft_groups: List[Dict[str, Any]] = []
    routed_special_groups: List[Dict[str, Any]] = []
    checklist_groups = list(rule_table_json.get("applicationChecklistGroups") or [])

    for source_group_key in ("hardRuleGroups", "softRuleGroups"):
        for group in rule_table_json.get(source_group_key) or []:
            raw_text = _safe_text(group.get("rawText"))
            items = list(group.get("conditionFieldItems", []) or [])
            has_quantitative_items = any(_is_quantitative_condition_item(item) for item in items)
            if _is_application_checklist_text(raw_text) and not has_quantitative_items:
                checklist_groups.append(
                    {
                        "checklistNo": group.get("conditionNo"),
                        "checklistType": _checklist_type(raw_text),
                        "rawText": raw_text,
                        "items": [
                            _safe_text(item.get("policyFragment")) or _safe_text(item.get("itemName"))
                            for item in group.get("conditionFieldItems", []) or []
                        ] + list(group.get("unresolvedFragments", []) or []),
                    }
                )
            elif _is_special_pass_signal(raw_text):
                group["evaluationRole"] = "special_pass"
                routed_special_groups.append(group)
            else:
                # 每条数值/事实项单独计入推荐信号。单项组不依赖 AND/ANY，
                # 因而不会将“或”“条件之一”“至少满足 N 项”等不完整逻辑错误简化。
                for item in items:
                    item_fragment = _safe_text(item.get("policyFragment")) or raw_text
                    routed_soft_groups.append(
                        {
                            "conditionNo": group.get("conditionNo"),
                            "rawText": item_fragment,
                            "conditionContext": _safe_text(group.get("conditionContext")),
                            "conditionBranchId": _safe_text(item.get("conditionBranchId"))
                            or _safe_text(group.get("conditionBranchId")),
                            "conditionBranchText": _safe_text(item.get("conditionBranchText"))
                            or _safe_text(group.get("conditionBranchText")),
                            "conditionOperator": "AND",
                            "conditionFieldItems": [item],
                            "unresolvedFragments": [],
                            "evaluationRole": "quantitative_signal"
                            if _is_quantitative_condition_item(item)
                            else "eligibility_signal",
                        }
                    )

    for group in rule_table_json.get("specialPassRuleGroups") or []:
        group["evaluationRole"] = "special_pass"
        routed_special_groups.append(group)

    # 统计口径或申报清单移出后可能留下空组；空组不能参与评分或干扰组数量。
    rule_table_json["hardRuleGroups"] = [
        group for group in routed_hard_groups if group.get("conditionFieldItems")
    ]
    rule_table_json["softRuleGroups"] = [
        group for group in routed_soft_groups if group.get("conditionFieldItems")
    ]
    rule_table_json["specialPassRuleGroups"] = [
        group for group in routed_special_groups if group.get("conditionFieldItems")
    ]
    deduplicated_checklists: List[Dict[str, Any]] = []
    seen_checklist_texts = set()
    for checklist in checklist_groups:
        raw_text = _safe_text(checklist.get("rawText")) if isinstance(checklist, dict) else ""
        # 模型可能把同一条排除性条件同时放进硬性组和承诺清单；清单不能弱化其语义。
        if not raw_text or _is_hard_exclusion_condition(raw_text) or raw_text in seen_checklist_texts:
            continue
        seen_checklist_texts.add(raw_text)
        deduplicated_checklists.append(checklist)
    rule_table_json["applicationChecklistGroups"] = deduplicated_checklists


def _normalize_rule_table_items(rule_table_json: Dict[str, Any], field_entries: List[Dict[str, Any]]) -> None:
    """将生成结果标准化：仅保留可回对齐到标准字段的条件项。"""
    _ensure_rule_group_arrays(rule_table_json)
    _normalize_financing_ratio_rules(rule_table_json)

    lookup = _build_standard_field_lookup(field_entries)
    if not lookup.get("by_level3"):
        return

    normalized_count = 0
    semantic_name_kept_count = 0
    semantic_metric_corrected_count = 0
    misleading_mapping_removed_count = 0
    moved_to_unresolved_count = 0
    skipped_reward_formula_count = 0
    skipped_ip_type_detail_count = 0
    skipped_unsupported_qualitative_count = 0
    skipped_ambiguous_numeric_count = 0
    for group_key in SCORING_GROUP_KEYS:
        for group in rule_table_json.get(group_key) or []:
            unresolved_fragments = group.get("unresolvedFragments")
            if not isinstance(unresolved_fragments, list):
                unresolved_fragments = []

            kept_items: List[Dict[str, Any]] = []
            for item in group.get("conditionFieldItems") or []:
                _normalize_explicit_numeric_item(item, group)
                fragment = _safe_text(item.get("policyFragment")) or _safe_text(item.get("itemName"))
                item_status = _safe_text(item.get("itemStatus")).lower()

                if (
                    _safe_text((item.get("matchRule") or {}).get("fieldType")).lower() == "number"
                    and not _is_compensation_condition(item)
                    and not _has_single_explicit_numeric_threshold(item, group)
                ):
                    skipped_ambiguous_numeric_count += 1
                    continue

                if _looks_like_reward_formula(fragment):
                    skipped_reward_formula_count += 1
                    continue

                if _is_unusable_growth_metric_placeholder(item):
                    skipped_unsupported_qualitative_count += 1
                    continue

                if _is_compensation_condition(item):
                    _normalize_compensation_item(item, group)
                    kept_items.append(item)
                    continue

                if _is_non_scoring_ip_type_detail(item, group):
                    skipped_ip_type_detail_count += 1
                    continue

                if _is_unsupported_qualitative_assertion(item):
                    _mark_as_semantic_reference(item)
                    kept_items.append(item)
                    continue

                if _is_non_matchable_qualitative_item(item):
                    _mark_as_semantic_reference(item)
                    kept_items.append(item)
                    continue

                if _is_staff_workday_definition(item):
                    _append_definition_note(rule_table_json, item)
                    continue

                if _apply_semantic_metric_corrections(item):
                    semantic_metric_corrected_count += 1
                    kept_items.append(item)
                    continue

                matched = _find_item_standard_field(item, lookup)
                fragment_matched = _resolve_policy_fragment_standard_field(fragment, lookup)
                if not matched:
                    matched = fragment_matched
                rejected_standard_name = ""
                is_explicit_fragment_mapping = bool(
                    matched
                    and fragment_matched
                    and _safe_text(matched.get("level3Category")) == _safe_text(fragment_matched.get("level3Category"))
                )
                if matched and not is_explicit_fragment_mapping and not _is_standard_field_semantically_compatible(item, matched):
                    rejected_standard_name = _safe_text(matched.get("level3Category"))
                    matched = None
                if not matched:
                    # 标准字段不是量化条件的白名单。未收录字段仍以原始名称保留，
                    # 供后续模型基于企业数据做语义数值匹配，不能套用相近的错误字段。
                    if _is_quantitative_condition_item(item):
                        if _keep_unmapped_quantitative_item(item, rejected_standard_name):
                            kept_items.append(item)
                        else:
                            skipped_ambiguous_numeric_count += 1
                        continue
                    if _is_hard_exclusion_condition(fragment):
                        _mark_as_semantic_reference(item)
                        kept_items.append(item)
                        continue
                    _mark_as_semantic_reference(item)
                    kept_items.append(item)
                    continue

                current_name = _safe_text(item.get("itemName"))
                canonical_name = matched["level3Category"]
                if _should_drop_misleading_standard_field(item, matched):
                    item["sourceStandardField"] = None
                    misleading_mapping_removed_count += 1
                elif _requires_semantic_reference(item, matched):
                    _mark_as_semantic_reference(item)
                    misleading_mapping_removed_count += 1
                else:
                    item["sourceStandardField"] = matched

                # unresolved 表示尚无稳定的字段级判断，不能继续标记为直接等价规则。
                if _safe_text(item.get("itemStatus")).lower() == "unresolved":
                    _mark_as_semantic_reference(item)

                if item_status in {"rule", "unresolved"}:
                    if _prefer_semantic_item_name(item, matched):
                        semantic_name_kept_count += 1
                    elif current_name != canonical_name:
                        item["itemName"] = canonical_name
                        normalized_count += 1
                kept_items.append(item)

            group["conditionFieldItems"] = kept_items
            group["unresolvedFragments"] = unresolved_fragments

    _route_rule_groups(rule_table_json)

    if normalized_count:
        print(f"[RuleTable] 已按最新标准字段修正 itemName: {normalized_count} 项")
    if semantic_metric_corrected_count:
        print(f"[RuleTable] 已纠正评价指标/得分类条件项: {semantic_metric_corrected_count} 项")
    if semantic_name_kept_count:
        print(f"[RuleTable] 已保留更贴近政策语义的 itemName: {semantic_name_kept_count} 项")
    if misleading_mapping_removed_count:
        print(f"[RuleTable] 已清理误导性标准字段映射: {misleading_mapping_removed_count} 项")
    if moved_to_unresolved_count:
        print(f"[RuleTable] 已移除非标准字段条件项并转入 unresolvedFragments: {moved_to_unresolved_count} 项")
    if skipped_reward_formula_count:
        print(f"[RuleTable] 已跳过奖励/补助计算公式条件项: {skipped_reward_formula_count} 项")
    if skipped_ip_type_detail_count:
        print(f"[RuleTable] 已跳过知识产权类型/登记明细: {skipped_ip_type_detail_count} 项")
    if skipped_unsupported_qualitative_count:
        print(f"[RuleTable] 已跳过无企业字段支撑的定性结论: {skipped_unsupported_qualitative_count} 项")
    if skipped_ambiguous_numeric_count:
        print(f"[RuleTable] 已跳过范围/枚举或缺少明确比较关系的伪数值项: {skipped_ambiguous_numeric_count} 项")


def _is_non_scoring_ip_type_detail(item: Dict[str, Any], group: Dict[str, Any]) -> bool:
    """识别知识产权类型枚举中的证据明细，避免拆成多条重复评分条件。"""
    group_text = _safe_text(group.get("rawText"))
    fragment = _safe_text(item.get("policyFragment"))
    if "知识产权的类型包括" not in group_text:
        return False

    summary_markers = ("拥有受中国法律保护的知识产权", "核心技术拥有受中国法律保护")
    if any(marker in fragment for marker in summary_markers):
        return False

    detail_markers = (
        "登记的集成电路布图设计专有权",
        "登记的软件著作权",
        "获得授权的发明专利",
        "获得授权的实用新型专利",
        "药品注册证书",
        "医疗器械注册证",
        "专利年限限制",
    )
    return any(marker in fragment for marker in detail_markers)


def _is_unsupported_qualitative_assertion(item: Dict[str, Any]) -> bool:
    """过滤不能由当前企业字段直接判断的泛化定性结论，避免伪装成直接规则。"""
    fragment = _safe_text(item.get("policyFragment"))
    target = _safe_text((item.get("matchRule") or {}).get("targetValue"))
    qualitative_targets = {"有效", "具备", "通过", "符合", "基于核心技术", "国内先进以上", "国际先进以上"}
    if target not in qualitative_targets:
        return False

    unsupported_markers = (
        "受中国法律保护的知识产权",
        "国家认证认可检测机构的检测",
        "产品生产许可",
        "强制性产品认证",
        "国家重点支持的高新技术领域",
        "基于核心技术研发",
        "达到国内先进以上水平",
        "达到国际先进以上技术水平",
    )
    return any(marker in fragment for marker in unsupported_markers)


def _is_compensation_condition(item: Dict[str, Any]) -> bool:
    """判断政策条件是否约束个人工资、薪金或年薪，而非企业营业收入。"""
    rule = item.get("matchRule") or {}
    content = " ".join(
        [
            _safe_text(item.get("itemName")),
            _safe_text(item.get("policyFragment")),
            _safe_text(rule.get("description")),
            _safe_text(rule.get("targetValue")),
        ]
    )
    return any(marker in content for marker in ("工资薪金", "工资总额", "薪金总额", "年薪", "薪酬", "劳动报酬"))


def _normalize_compensation_item(item: Dict[str, Any], group: Dict[str, Any]) -> None:
    """保留薪酬门槛原语义，禁止因金额单位误映射为企业营业收入。"""
    rule = item.setdefault("matchRule", {})
    content = " ".join(
        [
            _safe_text(item.get("itemName")),
            _safe_text(item.get("policyFragment")),
            _safe_text(rule.get("description")),
        ]
    )
    if "工资薪金总额" in content or "工资总额" in content or "薪金总额" in content:
        compensation_name = "工资薪金总额"
    elif "年薪" in content:
        compensation_name = "年薪"
    elif "薪酬" in content:
        compensation_name = "薪酬"
    else:
        compensation_name = "工资薪金"

    group_text = _safe_text(group.get("rawText"))
    if compensation_name == "年薪":
        for category_name in ("A类领军人才", "B类核心人才", "C类专业人才"):
            if category_name in group_text:
                compensation_name = f"{category_name}年薪门槛"
                break

    item["itemName"] = compensation_name
    item["enterpriseFields"] = [compensation_name]
    item["sourceStandardField"] = None
    item["itemStatus"] = "unresolved"
    rule["ruleKind"] = "reference"

    if _is_quantitative_condition_item(item):
        return

    rule["fieldType"] = "string"
    rule["operator"] = "llm_judge"


def _has_explicit_numeric_threshold(text: Any) -> bool:
    """判断原文是否同时包含比较关系和实际数值，避免把“符合要求”当作门槛。"""
    content = _safe_text(text)
    return bool(
        re.search(
            r"(?:不低于|不少于|不超过|不高于|不得为|达到|达|超过|高于|低于|大于|小于|以上|以下|至少|至多|≥|≤|>|<)"
            r"\s*\d+(?:\.\d+)?(?:\s*(?:%|％|万元|元|人|分|年|月|天|倍|项|件|个|:))?",
            content,
        )
    )


def _has_single_explicit_numeric_threshold(item: Dict[str, Any], group: Dict[str, Any]) -> bool:
    """只保留原文可独立解释的单一数值门槛，剔除模型编造的枚举数值。"""
    if not _is_quantitative_condition_item(item):
        return False

    fragment = _safe_text(item.get("policyFragment"))
    context = _safe_text(group.get("rawText"))
    if not fragment:
        return False

    # “分档”“5000万/1亿/…”、“3-35周岁”等只是枚举或区间，缺少单一可比较目标。
    if "分档" in context or "对应增速" in context:
        return False
    if re.search(r"\d+(?:\.\d+)?\s*(?:亿|万|万?元|亿元|万?次|人|个|项|年|月|天)?\s*(?:至|到|[-—])\s*\d+", fragment):
        return False
    if re.search(r"\d+(?:\.\d+)?\s*(?:万?元|亿元|万?次)?\s*/\s*\d+", fragment):
        return False

    # 同一片段的多个“或”比较无法在没有完整逻辑树时安全拆为独立规则。
    comparison_count = len(re.findall(r"(?:不低于|不少于|不超过|不高于|不得为|达到|达|超过|高于|低于|大于|小于|以上|以下|至少|至多|≥|≤|>|<)", fragment))
    if "或" in fragment and comparison_count > 1:
        return False

    if _has_explicit_numeric_threshold(fragment):
        return True

    # 覆盖“建筑面积3万平方米以上”“五十人以上”等数值在比较词前的写法。
    numeric_or_chinese = r"(?:\d+(?:\.\d+)?|[一二三四五六七八九十百千万两]+)"
    return bool(
        re.search(
            numeric_or_chinese
            + r"\s*[^\d；，。]{0,8}"
            + r"(?:以上|以下|以内|及以上|及以下|起|不含)",
            fragment,
        )
    )


def _is_unusable_growth_metric_placeholder(item: Dict[str, Any]) -> bool:
    """过滤研发、收入占比等没有原文阈值的“符合要求”占位项。"""
    rule = item.get("matchRule") or {}
    fragment = _safe_text(item.get("policyFragment")) or _safe_text(item.get("itemName"))
    combined = " ".join(
        [
            _safe_text(item.get("itemName")),
            fragment,
            _safe_text(rule.get("description")),
            _safe_text(rule.get("targetValue")),
        ]
    )
    metric_markers = (
        "研发投入强度",
        "研发投入比例",
        "研发费用占比",
        "研发费用比例",
        "核心产品收入占比",
        "高新技术产品收入占比",
        "主要产品（服务）收入",
    )
    if not any(marker in combined for marker in metric_markers):
        return False
    return not _has_explicit_numeric_threshold(fragment)


def _is_matchable_location_or_enterprise_time_item(item: Dict[str, Any]) -> bool:
    """识别企业注册属地、成立日期/年限等可由企业事实核验的具体条件。"""
    rule = item.get("matchRule") or {}
    content = " ".join(
        [
            _safe_text(item.get("itemName")),
            _safe_text(item.get("policyFragment")),
            _safe_text(rule.get("description")),
            _safe_text(rule.get("targetValue")),
        ]
    )
    location_markers = ("注册地", "注册地址", "注册在", "在厦注册", "属地", "纳税地", "实际经营地")
    location_values = ("厦门", "福建", "思明", "湖里", "集美", "海沧", "翔安", "同安", "高新区", "自贸区", "园区")
    if any(marker in content for marker in location_markers) and any(value in content for value in location_values):
        return True

    if any(marker in content for marker in ("申报时间", "受理时间", "填报时间", "截止时间")):
        return False
    time_markers = ("注册成立", "成立时间", "注册时间", "成立于", "设立于", "成立满", "成立年限", "经营年限")
    has_time_value = bool(re.search(r"20\d{2}\s*[年./-]", content)) or bool(
        re.search(r"(?:满|不少于|超过|达到)\s*\d+(?:\.\d+)?\s*年", content)
    )
    return any(marker in content for marker in time_markers) and has_time_value


def _is_non_matchable_qualitative_item(item: Dict[str, Any]) -> bool:
    """识别后续匹配器没有数值、派生计算或语义事实支撑的泛化定性项。"""
    rule = item.get("matchRule") or {}
    field_type = _safe_text(rule.get("fieldType")).lower()
    operator = _safe_text(rule.get("operator")).lower()
    rule_kind = _safe_text(rule.get("ruleKind")).lower()
    if field_type == "number" and _is_quantitative_condition_item(item):
        return False
    if field_type == "number":
        return True
    if operator != "llm_judge":
        return False
    if _is_matchable_location_or_enterprise_time_item(item):
        return False
    # 已有确定派生计算或显式语义事实协议的条件仍可参与匹配。
    if rule_kind in {"derived", "proxy"} or _looks_like_evaluation_metric(item):
        return False
    return True


def _is_non_matchable_qualitative_section(text: Any) -> bool:
    """判断一整段通用负向合规描述是否没有可执行的后续匹配规则。"""
    content = _safe_text(text)
    generic_markers = (
        "黑名单",
        "严重违法失信名单",
        "重大违法失信",
        "重大质量事故",
        "重大安全事故",
        "严重环境违法",
        "科研失信行为",
    )
    evidence_markers = ("惩戒执行期", "逾期未验收", "经营异常", "禁止申报")
    return any(marker in content for marker in generic_markers) and not any(marker in content for marker in evidence_markers)


def _policy_requirement_sections(detail_data: Dict[str, Any]) -> List[Tuple[int, str]]:
    """按原始编号拆分申报要求，供生成后的完整性校验使用。"""
    sections: List[Tuple[int, str]] = []
    for condition in detail_data.get("conditions") or []:
        requirement = _safe_text(condition.get("applicationRequirement"))
        matches = list(re.finditer(r"(?<!\d)(\d{1,2})[、.．]", requirement))
        if not matches:
            if requirement:
                sections.append((len(sections) + 1, requirement))
            continue
        for index, matched in enumerate(matches):
            content_end = matches[index + 1].start() if index + 1 < len(matches) else len(requirement)
            content = _safe_text(requirement[matched.end():content_end])
            if content:
                sections.append((int(matched.group(1)), content))
    return sections


def _build_numbered_requirement_context(detail_data: Dict[str, Any]) -> str:
    """保留原始编号，为模型在每条条件内拆分原子片段提供稳定锚点。"""
    sections = _policy_requirement_sections(detail_data)
    if not sections:
        return "（无申报要求文本）"
    return "\n\n".join(f"[C{condition_no}]\n{section}" for condition_no, section in sections)


def _compact_for_fragment_check(value: Any) -> str:
    """仅忽略空白字符，用于核验模型片段是否可从原文连续回查。"""
    return re.sub(r"\s+", "", _safe_text(value))


def _extract_requirement_context(section: str) -> str:
    """提取编号条件中冒号前的场景标题，用于区分相同指标的不同申报门槛。"""
    normalized_section = _safe_text(section)
    if not normalized_section:
        return ""
    match = re.match(r"\s*([^：:]{1,80})[：:]", normalized_section)
    return match.group(1).strip(" ，、；;。") if match else ""


def _extract_requirement_branches(section: str) -> List[str]:
    """按原文分号切分候选分支，保留每段完整文本供后续匹配解释。"""
    normalized_section = _safe_text(section)
    if not normalized_section:
        return []
    _, separator, remainder = normalized_section.partition("：")
    if not separator:
        _, separator, remainder = normalized_section.partition(":")
    branch_source = remainder if separator else normalized_section
    return [part.strip(" ，、；;。") for part in re.split(r"[；;]", branch_source) if part.strip(" ，、；;。")]


def _filter_items_not_grounded_in_requirements(
    rule_table_json: Dict[str, Any], detail_data: Dict[str, Any]
) -> None:
    """删除编号或原文片段无法回证的模型项，不尝试猜测其实际业务含义。"""
    source_by_condition: Dict[int, List[Tuple[str, str, List[str]]]] = {}
    for condition_no, section in _policy_requirement_sections(detail_data):
        source_by_condition.setdefault(condition_no, []).append(
            (
                _compact_for_fragment_check(section),
                _extract_requirement_context(section),
                _extract_requirement_branches(section),
            )
        )

    removed_count = 0
    for group_key in SCORING_GROUP_KEYS:
        for group in rule_table_json.get(group_key) or []:
            try:
                condition_no = int(group.get("conditionNo"))
            except (TypeError, ValueError):
                group["conditionFieldItems"] = []
                removed_count += 1
                continue

            source_entries = source_by_condition.get(condition_no) or []
            kept_items: List[Dict[str, Any]] = []
            matched_context = ""
            for item in group.get("conditionFieldItems") or []:
                source_fragment_id = _safe_text(item.get("sourceFragmentId"))
                fragment = _compact_for_fragment_check(item.get("policyFragment"))
                expected_prefix = f"C{condition_no}-A"
                if (
                    not source_fragment_id.startswith(expected_prefix)
                    or not fragment
                ):
                    removed_count += 1
                    continue
                matched_source = next((entry for entry in source_entries if fragment in entry[0]), None)
                if not matched_source:
                    removed_count += 1
                    continue
                if not matched_context:
                    matched_context = matched_source[1]
                matched_branch_index = next(
                    (
                        index
                        for index, branch in enumerate(matched_source[2], start=1)
                        if fragment in _compact_for_fragment_check(branch)
                    ),
                    0,
                )
                if matched_branch_index:
                    item["conditionBranchId"] = f"C{condition_no}-B{matched_branch_index}"
                    item["conditionBranchText"] = matched_source[2][matched_branch_index - 1]
                    if not _safe_text(group.get("conditionBranchId")):
                        group["conditionBranchId"] = item["conditionBranchId"]
                        group["conditionBranchText"] = item["conditionBranchText"]
                kept_items.append(item)
            group["conditionFieldItems"] = kept_items
            if matched_context:
                group["conditionContext"] = matched_context

    if removed_count:
        print(f"[RuleTable] 已跳过编号或原文片段无法回证的模型项: {removed_count} 项")


def _hard_exclusion_identity(text: Any) -> str:
    """返回可用于去重的排除条件标识，避免与模型已提取的同义组重复。"""
    content = _safe_text(text)
    for marker in ("科研失信", "信用联合惩戒", "逾期未验收", "惩戒执行期", "重大科技项目", "经营异常", "黑名单"):
        if marker in content:
            return marker
    return "不予受理" if "不予受理" in content else ""


def _ensure_hard_exclusion_coverage(rule_table_json: Dict[str, Any], detail_data: Dict[str, Any]) -> None:
    """补回模型漏提的硬性排除条件，统一标记为需证据的语义参考规则。"""
    _ensure_rule_group_arrays(rule_table_json)
    existing_hard_text = "\n".join(
        _safe_text(group.get("rawText")) for group in rule_table_json.get("hardRuleGroups") or []
    )
    appended_count = 0

    for condition_no, section in _policy_requirement_sections(detail_data):
        if _is_non_matchable_qualitative_section(section):
            continue
        if not _is_hard_exclusion_condition(section):
            continue
        identity = _hard_exclusion_identity(section)
        if identity and identity in existing_hard_text:
            continue

        fragments: List[Tuple[str, str]] = []
        for clause in re.split(r"[；;]", section):
            clause = _safe_text(clause)
            if not clause:
                continue
            if any(marker in clause for marker in ("伦理审查", "人体研究", "人类遗传资源", "实验动物")):
                fragments.append(("伦理合规审查", clause))
            if _is_hard_exclusion_condition(clause):
                fragments.append(("科研信用与项目验收限制", clause))
        if not fragments:
            fragments.append(("资格排除限制", section))

        items: List[Dict[str, Any]] = []
        for item_index, (item_name, fragment) in enumerate(fragments, start=1):
            item_id = f"H{condition_no}-F{item_index}"
            items.append(
                {
                    "itemId": item_id,
                    "itemName": item_name,
                    "policyFragment": fragment,
                    "itemStatus": "unresolved",
                    "enterpriseFields": [],
                    "sourceStandardField": None,
                    "matchRule": {
                        "ruleId": item_id,
                        "ruleKind": "reference",
                        "fieldType": "string",
                        "operator": "llm_judge",
                        "targetValue": fragment,
                        "unit": "",
                        "description": "政策原文中的硬性排除条件，需由企业事实或人工材料核验。",
                    },
                }
            )

        rule_table_json["hardRuleGroups"].append(
            {
                "conditionNo": condition_no,
                "rawText": section,
                "conditionOperator": "AND",
                "conditionFieldItems": items,
                "unresolvedFragments": [],
            }
        )
        existing_hard_text = f"{existing_hard_text}\n{section}"
        appended_count += 1

    if appended_count:
        _route_rule_groups(rule_table_json)
        print(f"[RuleTable] 已补回模型漏提的硬性排除条件: {appended_count} 组")


def _has_policy_fragment(rule_table_json: Dict[str, Any], marker: str) -> bool:
    """仅以已生成条件项去重，不能用整组原文掩盖模型漏提的子条件。"""
    for group_key in SCORING_GROUP_KEYS:
        for group in rule_table_json.get(group_key) or []:
            for item in group.get("conditionFieldItems", []) or []:
                text = " ".join(
                    [
                        _safe_text(item.get("itemName")),
                        _safe_text(item.get("policyFragment")),
                        _safe_text((item.get("matchRule") or {}).get("description")),
                    ]
                )
                if marker in text:
                    return True
    return False


def _numeric_operator(comparator: str, inclusive: bool, target_value: float) -> str:
    if comparator == "不得为" and target_value == 0:
        return "gt"
    if comparator in {"不低于", "不少于", "至少", "达到", "达", "≥"}:
        return "gte"
    if comparator in {"不超过", "不高于", "至多", "≤"}:
        return "lte"
    if comparator in {"超过", "高于", "大于", ">"}:
        return "gte" if inclusive else "gt"
    if comparator in {"低于", "小于", "<"}:
        return "lte" if inclusive else "lt"
    return ""


def _normalize_explicit_numeric_item(item: Dict[str, Any], group: Dict[str, Any]) -> bool:
    """将模型输出的明确单门槛语义项规范为可计算数值规则。"""
    fragment = _safe_text(item.get("policyFragment"))
    combined = f"{_safe_text(item.get('itemName'))} {fragment} {_safe_text(group.get('rawText'))}"
    rule = item.get("matchRule")
    if not fragment or not isinstance(rule, dict):
        return False

    ratio_match = re.search(
        r"(不低于|不少于|不超过|不高于|达到|达)\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)",
        fragment,
    )
    if ratio_match and "比例" in combined:
        target_value = float(ratio_match.group(2))
        rule["fieldType"] = "number"
        rule["operator"] = _numeric_operator(ratio_match.group(1), False, target_value)
        rule["targetValue"] = int(target_value) if target_value.is_integer() else target_value
        rule["unit"] = f":{ratio_match.group(3)}"
        rule["ruleKind"] = "reference"
        item["itemStatus"] = "unresolved"
        return True

    if _count_quantitative_thresholds(fragment) != 1:
        return False

    normalized_fragment = fragment.replace(",", "").replace("，", "")
    prefix_match = re.search(
        r"(不低于|不少于|不超过|不高于|不得为|达到|达|超过|高于|低于|大于|小于|至少|至多|≥|≤|>|<)"
        r"\s*(\d+(?:\.\d+)?)\s*(亿元|万元|万美元|元|%|％|分|人|家|项|件|个|年|个月|月|天|平方米|平米|份|场次|场|次|倍|周岁|岁|万次|亿次)?",
        normalized_fragment,
    )
    suffix_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(亿元|万元|万美元|元|%|％|分|人|家|项|件|个|年|个月|月|天|平方米|平米|份|场次|场|次|倍|周岁|岁|万次|亿次)?"
        r"(?:\s*\([^)]*含[^)]*\)|\s*（[^）]*含[^）]*）)?\s*(及以上|及以下|以上|以下|以内|起)",
        normalized_fragment,
    )
    if prefix_match:
        comparator = prefix_match.group(1)
        target_value = float(prefix_match.group(2))
        unit = prefix_match.group(3) or ""
        following = normalized_fragment[prefix_match.end():prefix_match.end() + 12]
        operator = _numeric_operator(comparator, "含" in following, target_value)
    elif suffix_match:
        target_value = float(suffix_match.group(1))
        unit = suffix_match.group(2) or ""
        operator = "lte" if suffix_match.group(3) in {"及以下", "以下", "以内"} else "gte"
    else:
        return False
    if not operator:
        return False

    rule["fieldType"] = "number"
    rule["operator"] = operator
    rule["targetValue"] = int(target_value) if target_value.is_integer() else target_value
    rule["unit"] = unit
    if _safe_text(rule.get("ruleKind")) != "direct":
        rule["ruleKind"] = "reference"
        item["itemStatus"] = "unresolved"
    return True


def _normalize_financing_ratio_rules(rule_table_json: Dict[str, Any]) -> None:
    """统一自筹与资助比例为“数值 + :分母”，保证展示和计算口径一致。"""
    corrected_count = 0
    for group_key in SCORING_GROUP_KEYS:
        for group in rule_table_json.get(group_key) or []:
            for item in group.get("conditionFieldItems") or []:
                item_name = _safe_text(item.get("itemName"))
                fragment = _safe_text(item.get("policyFragment"))
                if "比例" not in f"{item_name} {fragment}":
                    continue

                match_rule = item.get("matchRule")
                if not isinstance(match_rule, dict):
                    continue

                ratio_match = re.search(
                    r"(?:不低于|不少于|达到|达|不超过|不高于)\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)",
                    fragment,
                )
                if ratio_match:
                    numerator = float(ratio_match.group(1))
                    denominator = ratio_match.group(2)
                    if numerator.is_integer():
                        numerator = int(numerator)
                    expected_unit = f":{denominator}"
                    if match_rule.get("targetValue") != numerator or _safe_text(match_rule.get("unit")) != expected_unit:
                        match_rule["targetValue"] = numerator
                        match_rule["unit"] = expected_unit
                        corrected_count += 1
                    if "不低于" in fragment or "不少于" in fragment or "达到" in fragment:
                        match_rule["operator"] = "gte"
                    elif "不超过" in fragment or "不高于" in fragment:
                        match_rule["operator"] = "lte"
                    match_rule["fieldType"] = "number"
                    match_rule["ruleKind"] = "reference"
                    item["itemStatus"] = "unresolved"
                elif _safe_text(match_rule.get("unit")) == "1":
                    # 旧模型输出会漏掉冒号；该条件的历史规则均为“X:1”。
                    match_rule["unit"] = ":1"
                    corrected_count += 1

    if corrected_count:
        print(f"[RuleTable] 已修正自筹与资助比例的存储格式: {corrected_count} 项")


def _extract_cluster_fields(cluster: Dict[str, Any]) -> List[Dict[str, str]]:
    """从 cluster 字典中提取结构化字段列表。"""
    fields: List[Dict[str, str]] = []
    if not isinstance(cluster, dict):
        return fields

    for key, value in cluster.items():
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                fields.append({"field": f"{key}.{sub_key}", "value": _safe_text(sub_value)})
        else:
            fields.append({"field": key, "value": _safe_text(value)})
    return fields


def _build_generation_prompt(
    detail_data: Dict[str, Any],
    *,
    requirement_context: str = "",
    prompt_template: str = "",
    field_reference: str = "",
) -> str:
    """从融合政策详情构建完整的 LLM 生成 Prompt（规则 + 数据 + 字段列表合并为一条消息）。

    Returns:
        完整的 prompt 字符串
    """
    conditions = detail_data.get("conditions") or []
    policy_info = detail_data.get("policyInfo") or {}

    policy_title = _safe_text(
        policy_info.get("policyTitle")
        or detail_data.get("directoryTitle", "")
    )

    # 收集申报说明和附带结构化字段。申报要求正文在下方按原始编号统一构建，
    # 让模型只在同一编号的完整上下文中拆分原子条件。
    instruction_parts: List[str] = []
    cluster_fields_all: List[Dict[str, str]] = []
    applicable_targets: List[str] = []

    for condition in conditions:
        target = _safe_text(condition.get("applicableTarget"))
        if target:
            applicable_targets.append(target)

        instruction = _safe_text(condition.get("applicationInstruction"))
        if instruction:
            instruction_parts.append(f"[申报说明，仅用于材料/流程清单] {instruction}")

        cluster = condition.get("cluster") or {}
        cluster_fields_all.extend(_extract_cluster_fields(cluster))

    requirements_text = requirement_context or _build_numbered_requirement_context(detail_data)
    if not requirement_context and instruction_parts:
        requirements_text = f"{requirements_text}\n\n" + "\n".join(instruction_parts)
    target_text = "；".join(applicable_targets) if applicable_targets else "未指定"

    # cluster 字段摘要
    cluster_lines: List[str] = []
    for item in cluster_fields_all[:80]:
        cluster_lines.append(f"  {item['field']}: {item['value']}")
    cluster_text = "\n".join(cluster_lines) if cluster_lines else "（无结构化字段）"

    # 加载提示词模板（System + User 合并为一条消息）
    template = prompt_template or _load_prompt_template()

    # 加载标准字段参考（从 API 同步最新 → 保存到 config/ → 取本地最全版本）
    resolved_field_reference = field_reference or _sync_and_load_field_reference()

    condition_task_contract = ""
    if requirement_context:
        condition_task_contract = """### 本次编号条件提取任务（最高优先级）
本次只处理输入中唯一的 `[C数字]` 原始条件，其他编号不在本次输出范围。

1. 逐一检查该条件中的每个原子片段。只要原文存在明确比较关系和数值/单位，就必须输出一个 `softRuleGroups` 条件项；标准字段未收录时保留原文指标名并标记 `unresolved/reference`，不能遗漏。`不得为0分` 按 `gt 0分` 输出；`X:Y` 比例按数值分子和 `unit: ":Y"` 输出。
2. 主体资格、注册属地、行业限制、企业资质、荣誉奖励、研发机构、标准制定、信用合规等可由企业原始数据或外部事实核验的定性条件，也必须输出为 `unresolved/reference`，使用 `fieldType: "string"`、`operator: "llm_judge"`，不得因为标准字段暂缺而丢弃。
3. 同一片段由“且”连接多个事实时，每个事实分别输出；由“或”连接多个分支时，逐分支检查并分别输出可独立回证的事实。不得只保留其中一个数字或资格。
4. 本任务的普通条件统一输出到 `softRuleGroups`；`hardRuleGroups`、`specialPassRuleGroups`、`definitionNotes`、`applicationChecklistGroups` 均输出空数组。后续匹配器会将缺少证据的定性项标记为待确认，不会默认通过。
5. 材料、流程、奖励公式、纯编号枚举不生成条件项；上下限范围若主语和比较关系明确，必须分别提取上下限并保留同一分支上下文。没有可匹配条件时，三个规则组均输出空数组。
6. 每一个输出项的 `conditionNo` 必须等于当前 `[C数字]`，`sourceFragmentId` 必须使用当前编号，例如 `C18-A1`、`C18-A2`。"""

    # 填充模板占位符
    prompt = template.replace("{policy_title}", policy_title)
    prompt = prompt.replace("{applicable_target}", target_text)
    prompt = prompt.replace("{application_requirements}", requirements_text)
    prompt = prompt.replace("{cluster_fields}", cluster_text)
    prompt = prompt.replace("{standard_field_reference}", resolved_field_reference)
    prompt = prompt.replace("{condition_task_contract}", condition_task_contract)

    return prompt


def _validate_rule_table_json(data: Dict[str, Any]) -> Tuple[bool, str]:
    """校验生成的规则表 JSON 结构是否合法。"""
    if not isinstance(data, dict):
        return False, "输出不是 JSON 对象"

    _ensure_rule_group_arrays(data)

    for group_key in SCORING_GROUP_KEYS:
        groups = data.get(group_key)
        if not isinstance(groups, list):
            return False, f"缺少 {group_key} 数组"

        for idx, group in enumerate(groups):
            if not isinstance(group, dict):
                return False, f"{group_key}[{idx}] 不是对象"
            if "conditionNo" not in group:
                return False, f"{group_key}[{idx}] 缺少 conditionNo"
            items = group.get("conditionFieldItems")
            if not isinstance(items, list):
                return False, f"{group_key}[{idx}] (conditionNo={group.get('conditionNo')}) conditionFieldItems 不是数组"
            # 允许空数组（LLM 可能生成空的软性组），后续会自动过滤

            for item_idx, item in enumerate(items):
                if not isinstance(item, dict):
                    return False, f"{group_key}[{idx}].items[{item_idx}] 不是对象"
                if "itemId" not in item:
                    return False, f"{group_key}[{idx}].items[{item_idx}] 缺少 itemId"
                if not re.fullmatch(r"C\d+-A\d+", _safe_text(item.get("sourceFragmentId"))):
                    return False, f"{group_key}[{idx}].items[{item_idx}] 缺少或非法 sourceFragmentId"
                if not _safe_text(item.get("policyFragment")):
                    return False, f"{group_key}[{idx}].items[{item_idx}] 缺少 policyFragment"
                if "itemStatus" not in item:
                    return False, f"{group_key}[{idx}].items[{item_idx}] 缺少 itemStatus"
                if item.get("itemStatus") not in {"rule", "unresolved", "supporting", "reference"}:
                    return False, f"{group_key}[{idx}].items[{item_idx}] itemStatus 值非法: {item.get('itemStatus')}"
                match_rule = item.get("matchRule")
                if match_rule is not None and not isinstance(match_rule, dict):
                    return False, f"{group_key}[{idx}].items[{item_idx}] matchRule 格式错误"
                if isinstance(match_rule, dict):
                    rule_kind = _safe_text(match_rule.get("ruleKind"))
                    if rule_kind and rule_kind not in {"direct", "derived", "reference", "proxy", "supporting"}:
                        return False, f"{group_key}[{idx}].items[{item_idx}] ruleKind 值非法: {rule_kind}"

    for note_idx, note in enumerate(data.get("definitionNotes") or []):
        if not isinstance(note, dict) or not _safe_text(note.get("content")):
            return False, f"definitionNotes[{note_idx}] 必须是包含 content 的对象"

    for group_idx, group in enumerate(data.get("applicationChecklistGroups") or []):
        if not isinstance(group, dict) or not _safe_text(group.get("rawText")):
            return False, f"applicationChecklistGroups[{group_idx}] 必须是包含 rawText 的对象"

    return True, ""


def _call_llm_for_generation(prompt: str, task_label: str = "") -> Dict[str, Any]:
    """调用 LLM 生成规则表 JSON，带一次重试。

    所有规则 + 数据 + 字段列表合并为一条 user 消息发送。
    """
    if not LLM_API_URL or not LLM_MODEL_NAME:
        raise RuntimeError("LLM 配置缺失，请检查 .env 中的 API_URL / MODEL_NAME")

    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"

    payload = {
        "model": LLM_MODEL_NAME,
        "messages": [
            {"role": "system", "content": "你是一个 JSON 输出工具。只输出合法 JSON，不输出任何解释。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
        "chat_template_kwargs": {"enable_thinking": False},
    }

    last_error: str = ""
    for attempt in range(1, 3):
        content = ""
        started_at = time.monotonic()
        print(
            "[RuleTable] LLM 请求: "
            f"task={task_label or 'policy'}, "
            f"attempt={attempt}/2, endpoint={_llm_endpoint_for_log()}, "
            f"model={LLM_MODEL_NAME}, connectTimeout={LLM_CONNECT_TIMEOUT}s, "
            f"readTimeout={LLM_TIMEOUT}s, promptChars={len(prompt)}"
        )
        try:
            response = requests.post(
                LLM_API_URL,
                headers=headers,
                json=payload,
                timeout=(LLM_CONNECT_TIMEOUT, LLM_TIMEOUT),
            )
            response.raise_for_status()
            response_payload = response.json()
            content = response_payload["choices"][0]["message"]["content"]
            response_model = _safe_text(response_payload.get("model")) if isinstance(response_payload, dict) else ""
            usage = response_payload.get("usage") if isinstance(response_payload, dict) else {}
            usage = usage if isinstance(usage, dict) else {}
            choice = (response_payload.get("choices") or [{}])[0] if isinstance(response_payload, dict) else {}
            finish_reason = _safe_text(choice.get("finish_reason")) if isinstance(choice, dict) else ""
            elapsed_seconds = time.monotonic() - started_at
            print(
                "[RuleTable] LLM 响应: "
                f"task={task_label or 'policy'}, "
                f"attempt={attempt}/2, status={response.status_code}, "
                f"elapsed={elapsed_seconds:.1f}s, contentChars={len(_safe_text(content))}, "
                f"responseModel={response_model or '服务端未回传'}, "
                f"finishReason={finish_reason or '未回传'}, "
                f"promptTokens={usage.get('prompt_tokens', '未回传')}, "
                f"completionTokens={usage.get('completion_tokens', '未回传')}"
            )

            parsed = _sanitize_llm_json(content)
            valid, error_msg = _validate_rule_table_json(parsed)
            if valid:
                return parsed

            last_error = f"Schema 校验失败: {error_msg}"
            if attempt == 1:
                payload["messages"].append(
                    {"role": "assistant", "content": content},
                )
                payload["messages"].append(
                    {
                        "role": "user",
                        "content": f"上次输出的 JSON 校验未通过：{error_msg}。请严格按照之前定义的 Schema 重新输出完整 JSON，确保所有必填字段存在且 itemStatus 取值正确。",
                    },
                )
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = f"JSON 解析失败: {str(exc)[:200]}"
            elapsed_seconds = time.monotonic() - started_at
            print(
                f"[RuleTable] LLM 输出格式失败: task={task_label or 'policy'}, "
                f"attempt={attempt}/2, elapsed={elapsed_seconds:.1f}s, error={last_error}"
            )
            if attempt == 1:
                payload["messages"].append(
                    {
                        "role": "user",
                        "content": "上一轮输出不是合法 JSON。请重新输出完整 JSON 对象：只允许 JSON，不要 Markdown、解释文字或未转义的换行。",
                    }
                )
        except Exception as exc:
            last_error = f"LLM 调用异常: {str(exc)[:200]}"
            elapsed_seconds = time.monotonic() - started_at
            print(
                f"[RuleTable] LLM 调用失败: task={task_label or 'policy'}, "
                f"attempt={attempt}/2, elapsed={elapsed_seconds:.1f}s, error={last_error}"
            )
            if attempt >= 2:
                raise RuntimeError(last_error) from exc

    raise RuntimeError(last_error or "生成失败，未知错误")


def _merge_rule_table_sections(rule_tables: List[Dict[str, Any]]) -> Dict[str, Any]:
    """按原始条件编号合并并发模型结果，保留后续统一去重与校验。"""
    merged: Dict[str, Any] = {key: [] for key in RULE_TABLE_LIST_KEYS}
    for rule_table in rule_tables:
        for key in RULE_TABLE_LIST_KEYS:
            value = rule_table.get(key)
            if isinstance(value, list):
                merged[key].extend(value)
    return merged


def _quantitative_coverage_count(rule_table: Dict[str, Any]) -> int:
    """统计模型结果中已经由原文片段覆盖的明确数字门槛。"""
    covered = 0
    for group_key in SCORING_GROUP_KEYS:
        for group in rule_table.get(group_key) or []:
            for item in group.get("conditionFieldItems") or []:
                covered += _count_quantitative_thresholds(item.get("policyFragment"))
    return covered


def _build_quantitative_retry_prompt(prompt: str, condition_no: int, expected_count: int, covered_count: int) -> str:
    """为定量覆盖不足的编号追加最高优先级复核任务。"""
    return f"""{prompt}

### 定量完整性复核（覆盖前文所有一般说明，最高优先级）
当前 `[C{condition_no}]` 原文检测到至少 {expected_count} 个带比较关系的数字门槛，上一轮仅覆盖 {covered_count} 个。
本轮只输出该编号中的定量条件，定性资格、材料、流程、奖励计算公式全部忽略。
逐句检查“且、并、；、或”连接的每个门槛，不得只保留第一项；`不得为0分` 转为 `operator: "gt"`、`targetValue: 0`、`unit: "分"`。
`X:Y` 自筹与资助比例必须使用 `fieldType: "number"`，分子写入 `targetValue`，分母写入 `unit: ":Y"`。
原文中的每个数值项都必须使用可连续回查的最小 `policyFragment`；已提取过的定量项也完整重发，后处理会去重。"""


def _deduplicate_scoring_groups(rule_table_json: Dict[str, Any]) -> None:
    """按编号、原文片段和数值规则去重，避免定量复核重复计分。"""
    seen = set()
    for group_key in SCORING_GROUP_KEYS:
        deduplicated_groups: List[Dict[str, Any]] = []
        for group in rule_table_json.get(group_key) or []:
            kept_items: List[Dict[str, Any]] = []
            for item in group.get("conditionFieldItems") or []:
                rule = item.get("matchRule") or {}
                identity = (
                    group.get("conditionNo"),
                    _compact_for_fragment_check(item.get("policyFragment")),
                    _safe_text(rule.get("operator")).lower(),
                    _safe_text(rule.get("targetValue")),
                    _safe_text(rule.get("unit")),
                )
                if identity in seen:
                    continue
                seen.add(identity)
                kept_items.append(item)
            if kept_items:
                group["conditionFieldItems"] = kept_items
                deduplicated_groups.append(group)
        rule_table_json[group_key] = deduplicated_groups


def _generate_rule_table_by_requirement_sections(
    detail_data: Dict[str, Any],
    prompt_template: str,
    field_reference: str,
) -> Dict[str, Any]:
    """按原始条件编号并发调用模型，避免跨行业条件在同一输出中互相污染。"""
    sections = _policy_requirement_sections(detail_data)
    if not sections:
        return {key: [] for key in RULE_TABLE_LIST_KEYS}

    workers = min(RULE_TABLE_CONDITION_WORKERS, len(sections))
    print(f"[RuleTable] Step 3/4: 按原始条件并发生成，sections={len(sections)}, workers={workers}")
    completed: Dict[int, Dict[str, Any]] = {}
    failures: List[str] = []

    def generate_one(index: int, condition_no: int, section: str) -> Tuple[int, Dict[str, Any]]:
        requirement_context = f"[C{condition_no}]\n{section}"
        prompt = _build_generation_prompt(
            detail_data,
            requirement_context=requirement_context,
            prompt_template=prompt_template,
            field_reference=field_reference,
        )
        initial_table = _call_llm_for_generation(prompt, task_label=f"C{condition_no}")
        expected_count = _count_quantitative_thresholds(section)
        covered_count = _quantitative_coverage_count(initial_table)
        if expected_count <= covered_count:
            return index, initial_table
        print(
            f"[RuleTable] 定量覆盖不足，执行复核: C{condition_no}, "
            f"expected={expected_count}, covered={covered_count}"
        )
        retry_prompt = _build_quantitative_retry_prompt(prompt, condition_no, expected_count, covered_count)
        retry_table = _call_llm_for_generation(retry_prompt, task_label=f"C{condition_no}-quantitative-retry")
        return index, _merge_rule_table_sections([initial_table, retry_table])

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="rule-table-condition") as executor:
        futures = {
            executor.submit(generate_one, index, condition_no, section): (index, condition_no)
            for index, (condition_no, section) in enumerate(sections, start=1)
        }
        for future in as_completed(futures):
            index, condition_no = futures[future]
            try:
                result_index, rule_table = future.result()
                completed[result_index] = rule_table
                print(f"[RuleTable] 条件完成: C{condition_no} ({len(completed)}/{len(sections)})")
            except Exception as exc:
                failures.append(f"C{condition_no}: {str(exc)[:200]}")
                print(f"[RuleTable] 条件失败: C{condition_no}, error={str(exc)[:200]}")

    if not completed:
        raise RuntimeError("所有原始条件的模型提取均失败: " + "；".join(failures[:3]))
    if failures:
        raise RuntimeError(
            f"原始条件提取未完整完成: {len(failures)}/{len(sections)} 条失败；"
            "为避免写入缺失条件的规则表，本次不合并、不写回。"
            + "；".join(failures[:3])
        )

    return _merge_rule_table_sections([completed[index] for index in sorted(completed)])


def generate_rule_table(fusion_policy_id: str, base_url: str = "") -> Dict[str, Any]:
    """从融合政策详情生成规则表 JSON。

    流程：
    1. 获取融合政策详情
    2. 同步最新的申报条件管理 JSON 到 config/
    3. 加载外部提示词模板 prompts/rule_table_generation_prompt.md
    4. 构建 Prompt（System + User），调用 LLM 生成
    5. 补充元数据并返回

    Args:
        fusion_policy_id: 融合政策 ID
        base_url: structure API 基地址，为空则用 env 配置

    Returns:
        { ruleTableJson, stats: {hardGroupCount, softGroupCount, itemCount} }
    """
    global STRUCTURE_API_BASE_URL  # noqa: PLW0603
    if base_url.strip():
        STRUCTURE_API_BASE_URL = base_url.strip().rstrip("/")

    # 1. 获取融合政策详情
    print(f"[RuleTable] Step 1/4: 获取融合政策详情, fusionPolicyId={fusion_policy_id}")
    from service.policy_loader import StructurePolicyClient  # noqa: PLC0415

    client = StructurePolicyClient(base_url=STRUCTURE_API_BASE_URL)
    detail_data = client.fetch_policy_detail(fusion_policy_id)
    if not detail_data:
        raise ValueError(f"未获取到融合政策详情: {fusion_policy_id}")
    policy_title_preview = _safe_text(
        (detail_data.get("policyInfo") or {}).get("policyTitle")
        or detail_data.get("directoryTitle", "")
    )
    condition_count = len(detail_data.get("conditions") or [])
    print(f"[RuleTable] Step 1/4 done: policy={policy_title_preview}, conditions={condition_count}")

    # 2. 预加载 Prompt 和字段配置，供每个原始条件复用，避免并发重复同步配置。
    print(f"[RuleTable] Step 2/4: 构建 Prompt（同步字段配置 + 加载提示词模板）...")
    prompt_template = _load_prompt_template()
    field_entries = _sync_and_load_field_entries()
    field_reference = _build_field_reference_text(field_entries) if field_entries else "（未找到标准字段配置）"
    section_count = len(_policy_requirement_sections(detail_data))
    print(f"[RuleTable] Step 2/4 done: sections={section_count}, fieldReferenceChars={len(field_reference)}")

    # 3. 每个原始编号独立提取，受控并发后按编号合并。
    print(f"[RuleTable] Step 3/4: 调用 LLM ({LLM_MODEL_NAME}) 生成规则表 JSON...")
    rule_table_json = _generate_rule_table_by_requirement_sections(
        detail_data,
        prompt_template,
        field_reference,
    )

    # 3.5 仅验证模型输出与标准字段配置的对应关系；不再按业务词表或正则
    # 从原文补造条件项，避免将局部数字误解释为独立申报条件。
    _filter_items_not_grounded_in_requirements(rule_table_json, detail_data)
    _normalize_rule_table_items(rule_table_json, field_entries)
    _deduplicate_scoring_groups(rule_table_json)

    # 3.6 过滤空的评分条件组 + 可疑数值告警
    for group_key in SCORING_GROUP_KEYS:
        groups = rule_table_json.get(group_key) or []
        filtered = [g for g in groups if isinstance(g.get("conditionFieldItems"), list) and len(g["conditionFieldItems"]) > 0]
        if len(filtered) < len(groups):
            print(f"[RuleTable] 过滤空{group_key}: {len(groups)} -> {len(filtered)} 组")
            rule_table_json[group_key] = filtered

    # 3.7 数值合理性检查
    all_items = []
    for group_key in SCORING_GROUP_KEYS:
        for g in rule_table_json.get(group_key) or []:
            all_items.extend(g.get("conditionFieldItems") or [])
    for item in all_items:
        mr = item.get("matchRule") or {}
        tv = mr.get("targetValue")
        unit = str(mr.get("unit") or "").strip()
        if isinstance(tv, (int, float)) and tv > 10000 and "万" in unit:
            print(f"[RuleTable] WARNING: {item.get('itemId')} targetValue={tv}{unit} 数值偏大，请检查单位是否正确（可能多乘了10000）")
        if isinstance(tv, (int, float)) and tv > 10000000:
            print(f"[RuleTable] WARNING: {item.get('itemId')} targetValue={tv} 数值异常大，请检查")

    # 4. 补充元数据 + 统计
    print(f"[RuleTable] Step 4/4: 补充元数据...")
    policy_info = detail_data.get("policyInfo") or {}
    policy_title = _safe_text(
        policy_info.get("policyTitle") or detail_data.get("directoryTitle", "")
    )
    rule_table_json["name"] = f"{policy_title}_规则表" if policy_title else "未命名规则表"
    rule_table_json["schemaVersion"] = "1.2"
    rule_table_json["policyDisplayName"] = policy_title
    rule_table_json["fusionPolicyId"] = fusion_policy_id

    hard_groups = rule_table_json.get("hardRuleGroups") or []
    soft_groups = rule_table_json.get("softRuleGroups") or []
    special_groups = rule_table_json.get("specialPassRuleGroups") or []
    definition_notes = rule_table_json.get("definitionNotes") or []
    checklist_groups = rule_table_json.get("applicationChecklistGroups") or []
    item_count = sum(
        len(group.get("conditionFieldItems", []) or [])
        for group in hard_groups + soft_groups + special_groups
    )
    no_matchable_rules = item_count == 0

    print(
        f"[RuleTable] Step 4/4 done: "
        f"hardGroups={len(hard_groups)}, "
        f"softGroups={len(soft_groups)}, "
        f"specialPassGroups={len(special_groups)}, "
        f"definitionNotes={len(definition_notes)}, "
        f"checklists={len(checklist_groups)}, "
        f"totalItems={item_count}"
    )
    if no_matchable_rules:
        print("[RuleTable] 未提取到可用于后续匹配的规则项，将由调用方跳过写回。")
    print(f"[RuleTable] ====== 生成完成: {policy_title} ======")

    return {
        "ruleTableJson": rule_table_json,
        "stats": {
            "hardGroupCount": len(hard_groups),
            "softGroupCount": len(soft_groups),
            "specialPassGroupCount": len(special_groups),
            "definitionNoteCount": len(definition_notes),
            "applicationChecklistGroupCount": len(checklist_groups),
            "itemCount": item_count,
            "noMatchableRules": no_matchable_rules,
        },
    }
