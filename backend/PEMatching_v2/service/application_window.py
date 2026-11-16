# -*- coding: utf-8 -*-
"""申报期提取（确定性优先，LLM 兜底）。

原计划全部交 LLM 从申报条件文本里抽，但实测快照里 84% 的政策已有结构化字段
`applicationStartTime` / `applicationEndTime`（格式 2026.04.10），
96% 有 `applicationInstruction` 文本（含「长期受理」这类说法）。

因此改为三级策略：
  1. 结构化字段直接取（84%）——零风险，无编造可能
  2. `applicationInstruction` 正则匹配（补上大部分剩余）
  3. 都取不到时才交 LLM（少数），且 prompt 明令抽不出填 null

时效性判定本身在 scoring.resolve_timeliness，本模块只负责「取到窗口」。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from service import fields as F

# 2026.04.10 / 2026-04-10 / 2026/4/10
_DOT_DATE = re.compile(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})")
# 2026年04月10日
_CN_DATE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
# 2026年04月（无日，按月初/月末处理）
_CN_MONTH = re.compile(r"(\d{4})年(\d{1,2})月(?!\d*日)")

_ROLLING_WORDS = ("长期受理", "常年受理", "长期有效", "随时申报", "全年受理", "不限时间")

SOURCE_STRUCTURED = "structured_field"
SOURCE_INSTRUCTION = "instruction_text"
SOURCE_ROLLING = "rolling_text"
SOURCE_NONE = "not_found"


def _normalize_date(year: str, month: str, day: str) -> Optional[str]:
    try:
        y, m, d = int(year), int(month), int(day)
    except (TypeError, ValueError):
        return None
    if not (1900 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31):
        return None
    return f"{y:04d}-{m:02d}-{d:02d}"


def parse_date_text(text: str) -> Optional[str]:
    """从一段文本里取第一个日期，归一为 YYYY-MM-DD。取不到返回 None。"""
    raw = F.text(text)
    if not raw:
        return None
    for pattern in (_CN_DATE, _DOT_DATE):
        match = pattern.search(raw)
        if match:
            return _normalize_date(*match.groups())
    return None


def _extract_date_pairs(text: str) -> List[Tuple[str, str]]:
    """从文本里成对提取日期区间。"""
    raw = F.text(text)
    if not raw:
        return []
    dates: List[str] = []
    for pattern in (_CN_DATE, _DOT_DATE):
        for match in pattern.finditer(raw):
            value = _normalize_date(*match.groups())
            if value and value not in dates:
                dates.append(value)
    dates.sort()
    if len(dates) >= 2:
        return [(dates[0], dates[-1])]
    if len(dates) == 1:
        return [(dates[0], "")]
    return []


def _is_rolling(text: str) -> bool:
    raw = F.text(text)
    return any(word in raw for word in _ROLLING_WORDS)


def extract_window(policy: Dict[str, Any]) -> Dict[str, Any]:
    """从政策记录里提取申报期。

    返回结构与 policy_tags.json 的 applicationWindow 一致：
        {startDate, endDate, yearScope, isRolling, confidence, basis, source, needsLlm}

    needsLlm=True 表示确定性提取失败，需交 LLM 兜底。
    """
    conditions = [c for c in (policy.get("structuredConditions") or []) if isinstance(c, dict)]

    # ---------- 一级：结构化字段 ----------
    for condition in conditions:
        start = parse_date_text(condition.get("applicationStartTime"))
        end = parse_date_text(condition.get("applicationEndTime"))
        if start or end:
            year = (start or end or "")[:4]
            return {
                "startDate": start,
                "endDate": end,
                "yearScope": year,
                "isRolling": False,
                "confidence": "high",
                "basis": f"结构化字段 applicationStartTime={condition.get('applicationStartTime')!r}"
                         f" applicationEndTime={condition.get('applicationEndTime')!r}",
                "source": SOURCE_STRUCTURED,
                "needsLlm": False,
            }

    # ---------- 二级：申报说明文本 ----------
    for condition in conditions:
        instruction = F.text(condition.get("applicationInstruction"))
        if not instruction:
            continue
        if _is_rolling(instruction):
            return {
                "startDate": None,
                "endDate": None,
                "yearScope": "",
                "isRolling": True,
                "confidence": "high",
                "basis": f"申报说明：{instruction[:60]}",
                "source": SOURCE_ROLLING,
                "needsLlm": False,
            }
        pairs = _extract_date_pairs(instruction)
        if pairs:
            start, end = pairs[0]
            return {
                "startDate": start or None,
                "endDate": end or None,
                "yearScope": (start or end or "")[:4],
                "isRolling": False,
                "confidence": "medium",
                "basis": f"申报说明文本：{instruction[:60]}",
                "source": SOURCE_INSTRUCTION,
                "needsLlm": False,
            }

    # 申报要求正文里也可能带日期，但可靠性低，只作为 LLM 兜底的线索
    hints: List[str] = []
    for condition in conditions:
        requirement = F.text(condition.get("applicationRequirement"))
        if requirement and (_CN_DATE.search(requirement) or _DOT_DATE.search(requirement)):
            hints.append(requirement[:200])

    return {
        "startDate": None,
        "endDate": None,
        "yearScope": "",
        "isRolling": False,
        "confidence": "low",
        "basis": "结构化字段与申报说明均无申报期",
        "source": SOURCE_NONE,
        "needsLlm": True,
        "hints": hints[:3],
    }


def merge_llm_window(
    deterministic: Dict[str, Any], llm_window: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """合并确定性结果与 LLM 兜底结果。

    确定性结果优先——它来自结构化字段，不可能编造。只有确定性失败
    （needsLlm=True）时才采用 LLM 的值，且置信度降一档。
    """
    if not deterministic.get("needsLlm"):
        return {k: v for k, v in deterministic.items() if k not in ("needsLlm", "hints")}

    if not isinstance(llm_window, dict):
        return {k: v for k, v in deterministic.items() if k not in ("needsLlm", "hints")}

    start = parse_date_text(llm_window.get("startDate"))
    end = parse_date_text(llm_window.get("endDate"))
    rolling = bool(llm_window.get("isRolling"))

    if not (start or end or rolling):
        # LLM 也抽不出，保持 null——不编造
        return {k: v for k, v in deterministic.items() if k not in ("needsLlm", "hints")}

    return {
        "startDate": start,
        "endDate": end,
        "yearScope": (start or end or "")[:4],
        "isRolling": rolling,
        "confidence": "low",
        "basis": F.text(llm_window.get("basis")) or "由模型从申报条件文本抽取",
        "source": "llm",
    }


def window_stats(policies: List[Dict[str, Any]]) -> Dict[str, Any]:
    """统计各来源的命中情况，用于确认确定性提取的覆盖率。"""
    counter: Dict[str, int] = {}
    needs_llm = 0
    for policy in policies:
        window = extract_window(policy)
        source = window["source"]
        counter[source] = counter.get(source, 0) + 1
        if window.get("needsLlm"):
            needs_llm += 1
    total = len(policies) or 1
    return {
        "total": len(policies),
        "bySource": counter,
        "needsLlm": needs_llm,
        "deterministicRatio": round((len(policies) - needs_llm) * 100.0 / total, 1),
    }
