"""将政策推荐企业接口的英文 JSON 路径转换为中文字段名称。"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIELD_MAP_PATH = PROJECT_ROOT / "config" / "policy_recommendation_field_map.json"


@lru_cache(maxsize=1)
def _field_map() -> Dict[str, str]:
    with FIELD_MAP_PATH.open("r", encoding="utf-8") as stream:
        payload = json.load(stream)
    return payload if isinstance(payload, dict) else {}


def _mapping_key(path: str) -> str:
    return re.sub(r"\[\d+\]", "[*]", path)


def is_mapped_enterprise_field(path: str) -> bool:
    """判断原始接口路径是否可作为模型候选字段。"""
    return bool(_field_map().get(_mapping_key(path)))


def chinese_field_name(path: str) -> str:
    """获取映射文件中声明的中文字段名称。"""
    return str(_field_map().get(_mapping_key(path)) or "")


def candidates_with_chinese_field_names(candidates: Dict[str, Any]) -> List[Dict[str, Any]]:
    """仅向模型提供已在映射表中声明的原始接口字段。"""
    field_map = _field_map()
    mapped_candidates: List[Dict[str, Any]] = []
    for path, value in candidates.items():
        field_name = field_map.get(_mapping_key(path))
        if not field_name:
            continue
        mapped_candidates.append({"字段名称": field_name, "字段值": value})
    return mapped_candidates
