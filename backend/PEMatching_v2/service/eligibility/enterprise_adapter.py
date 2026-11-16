# -*- coding: utf-8 -*-
"""把 V2 标准企业画像转换为只读、可回读的资格事实。"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Dict, Iterable, List

from service import fields as F
from service.eligibility.constants import FIELD_MAP_SCHEMA
from service.paths import ELIGIBILITY_FIELD_MAPPING_PATH


class FieldMappingError(RuntimeError):
    pass


def _get_path(source: Any, path: str) -> tuple[bool, Any]:
    current = source
    parts = [part for part in path.removeprefix("$.").split(".") if part]
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


@lru_cache(maxsize=1)
def load_field_mapping() -> Dict[str, Dict[str, Any]]:
    if not ELIGIBILITY_FIELD_MAPPING_PATH.exists():
        return {}
    try:
        payload = json.loads(ELIGIBILITY_FIELD_MAPPING_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FieldMappingError(f"字段映射不可读取：{exc}") from exc
    if payload.get("schemaVersion") != FIELD_MAP_SCHEMA or not isinstance(payload.get("mappings"), list):
        raise FieldMappingError("字段映射 schema 非法")
    result: Dict[str, Dict[str, Any]] = {}
    for mapping in payload["mappings"]:
        if not isinstance(mapping, dict) or not F.text(mapping.get("v1Field")):
            raise FieldMappingError("字段映射项缺少 v1Field")
        result[F.text(mapping["v1Field"])] = mapping
    return result


def reset_cache() -> None:
    load_field_mapping.cache_clear()


def _candidate_mappings(item: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    mapping = load_field_mapping()
    standard_field = item.get("sourceStandardField")
    standard_name = F.text(standard_field.get("coreField")) if isinstance(standard_field, dict) else F.text(standard_field)
    names = [standard_name, *[F.text(name) for name in item.get("enterpriseFields") or []]]
    for name in names:
        if name and name in mapping:
            yield mapping[name]


def resolve_fact(profile: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
    """回读映射路径。没有精确映射时不拿相邻字段充数。"""
    candidates = list(_candidate_mappings(item))
    if not candidates:
        return {"evidenceTier": "U_missing", "bucket": "unverified", "reason": "没有已验证的 V1 字段映射", "value": None, "sourcePaths": [], "unit": ""}
    for mapping in candidates:
        if F.text(mapping.get("mappingStatus")) != "verified":
            continue
        for path in mapping.get("v2Paths") or []:
            found, value = _get_path(profile, F.text(path))
            if not found or F.is_missing(value):
                continue
            # 只回读标量事实：映射路径指向整条企业记录或嵌套数组时，
            # 无法按单一事实参与比较/换算，视为无可用映射，避免 dict 流到评分与导出。
            if isinstance(value, (dict, list, tuple)):
                continue
            return {
                "evidenceTier": "A1_exact", "bucket": "verified", "reason": "已验证字段映射并回读企业事实",
                "value": value, "sourcePaths": [F.text(path)], "unit": F.text(mapping.get("canonicalUnit")),
                "semanticCategory": F.text(mapping.get("semanticCategory")), "yearPolicy": F.text(mapping.get("yearPolicy")),
            }
    return {"evidenceTier": "U_missing", "bucket": "unverified", "reason": "已验证映射的企业字段没有值", "value": None, "sourcePaths": [], "unit": ""}
