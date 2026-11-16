# -*- coding: utf-8 -*-
"""比较前的确定性单位标准化。"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from service import fields as F

_MONEY = {"元": 1.0, "万元": 10_000.0, "亿元": 100_000_000.0}
_RATIO = {"%", "百分比", "比例", "1", "ratio", ""}


def normalise_unit(unit: Any) -> str:
    value = F.text(unit).replace("人民币", "").replace("（", "").replace("）", "")
    return {"万": "万元", "亿": "亿元", "percent": "%", "百分数": "%"}.get(value.lower(), value)


def convert(value: Any, from_unit: Any, to_unit: Any) -> Tuple[float | None, str, str]:
    """返回标准化值、公式和失败原因；未知单位不得直接比较。"""
    numeric = F.to_float(value)
    source, target = normalise_unit(from_unit), normalise_unit(to_unit)
    if numeric is None:
        return None, "", "企业值不是可计算数值"
    if not target or source == target:
        return numeric, f"{numeric} {source or target}".strip(), ""
    if source in _MONEY and target in _MONEY:
        converted = numeric * _MONEY[source] / _MONEY[target]
        return converted, f"{numeric}{source} × {_MONEY[source] / _MONEY[target]:g} = {converted:g}{target}", ""
    if source in _RATIO and target in _RATIO:
        if target == "%" and source != "%":
            return numeric * 100, f"{numeric:g} × 100 = {numeric * 100:g}%", ""
        if source == "%" and target != "%":
            return numeric / 100, f"{numeric:g}% ÷ 100 = {numeric / 100:g}", ""
        return numeric, f"{numeric:g}{source}", ""
    return None, "", f"单位无法换算：{source or '未提供'} -> {target or '未提供'}"
