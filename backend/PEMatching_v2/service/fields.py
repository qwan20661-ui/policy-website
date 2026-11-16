# -*- coding: utf-8 -*-
"""字段取值与可用性标记。

本链路的核心前提：不假定任何企业字段一定有值。每个字段都走
「取值 → 判空 → 标记可用性」三步，得到 available / unknown 两种状态。

最关键的一条：`0` 与 `null` 必须区分。
    ipClass1Count = 0    → 确认没有 I 类知识产权，创新活跃度计 0 分
    ipClass1Count = null → 不知道，该计分项从分母剔除
两者绝不可混同，不填默认值、不用均值补造。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

AVAILABLE = "available"
UNKNOWN = "unknown"

# 接口里表示「无值」的各种写法
_NULL_TEXTS = {"", "null", "none", "nan", "-", "--", "无", "未知", "暂无"}


def is_missing(value: Any) -> bool:
    """判断是否为「无值」。

    注意：数字 0 和 False **不是**无值，它们是已核验的真实取值。
    """
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return False
    if isinstance(value, str):
        return value.strip().lower() in _NULL_TEXTS
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0
    return False


def text(value: Any) -> str:
    """取字符串。无值返回空串。"""
    if is_missing(value):
        return ""
    return str(value).strip()


def to_int(value: Any) -> Optional[int]:
    """取整数。无值或无法解析返回 None——None 表示 unknown，与 0 语义不同。"""
    if is_missing(value):
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(float(str(value).strip().replace(",", "")))
    except (TypeError, ValueError):
        return None


def to_float(value: Any) -> Optional[float]:
    """取浮点数。无值或无法解析返回 None。"""
    if is_missing(value):
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip().replace(",", "").rstrip("%"))
    except (TypeError, ValueError):
        return None


def yuan_to_wan(value: Any) -> Optional[float]:
    """元 → 万元。接口的经营信息金额单位是元，内部统一万元。"""
    amount = to_float(value)
    if amount is None:
        return None
    return round(amount / 10000.0, 4)


def yes_no(value: Any) -> Optional[bool]:
    """接口的「是/否」字段 → 布尔。

    无值返回 None（unknown），不当作 False。资质字段实测大面积为 null，
    若当 False 会把「不知道有没有资质」误判成「确认没有资质」。
    """
    if is_missing(value):
        return None
    if isinstance(value, bool):
        return value
    raw = str(value).strip().lower()
    if raw in {"是", "y", "yes", "true", "1", "有"}:
        return True
    if raw in {"否", "n", "no", "false", "0", "没有"}:
        return False
    return None


class Availability:
    """字段可用性登记簿。

    每次取字段都登记一次，最终产出 fieldAvailability 明细与 dataCompleteness 汇总，
    让调用方看得出结果建立在多少信息量上。
    """

    def __init__(self) -> None:
        self._states: Dict[str, str] = {}

    def mark(self, field: str, value: Any) -> Any:
        """登记字段状态并原样返回取值，便于链式使用。"""
        self._states[field] = UNKNOWN if is_missing(value) else AVAILABLE
        return value

    def mark_state(self, field: str, state: str) -> None:
        """直接登记状态。用于派生字段（如 ipTotal 由多个源字段回退得出）。"""
        self._states[field] = state

    def mark_resolved(self, field: str, resolved: Any) -> Any:
        """按解析结果登记：None 记 unknown，其余（含 0、False）记 available。"""
        self._states[field] = UNKNOWN if resolved is None else AVAILABLE
        return resolved

    def state(self, field: str) -> str:
        return self._states.get(field, UNKNOWN)

    def is_available(self, field: str) -> bool:
        return self._states.get(field) == AVAILABLE

    def any_available(self, fields: Iterable[str]) -> bool:
        return any(self.is_available(f) for f in fields)

    def all_available(self, fields: Iterable[str]) -> bool:
        fields = list(fields)
        return bool(fields) and all(self.is_available(f) for f in fields)

    def unknown_fields(self) -> List[str]:
        return sorted(f for f, s in self._states.items() if s == UNKNOWN)

    def available_fields(self) -> List[str]:
        return sorted(f for f, s in self._states.items() if s == AVAILABLE)

    def as_dict(self) -> Dict[str, str]:
        return dict(sorted(self._states.items()))

    def completeness(self) -> Dict[str, int]:
        available = sum(1 for s in self._states.values() if s == AVAILABLE)
        return {
            "available": available,
            "unknown": len(self._states) - available,
            "total": len(self._states),
        }


def first_available(*candidates: Tuple[str, Any]) -> Tuple[str, Any]:
    """按顺序返回第一个有值的候选。

    用于多源回退（如知产数量 ipClass1+2 → ipCount → inventionPatentCount），
    同时回传命中的来源名，写进结果便于解释分数。
    全部无值时返回 ("", None)。
    """
    for name, value in candidates:
        if value is not None and not is_missing(value):
            return name, value
    return "", None
