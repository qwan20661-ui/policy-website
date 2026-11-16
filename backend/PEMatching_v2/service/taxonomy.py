# -*- coding: utf-8 -*-
"""标签体系、行业映射表、关键词库的加载与校验。

三个配置文件都是人工维护的源文件，加载时严格校验 schemaVersion，
缺失或版本不符直接抛错并指明文件路径，不静默用默认值兜底。
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Tuple

from service.paths import (
    INDUSTRY_MAP_PATH,
    KEYWORD_LIBRARY_PATH,
    TAXONOMY_PATH,
)

TAXONOMY_SCHEMA = "pematching-v2-taxonomy-v1"
INDUSTRY_MAP_SCHEMA = "pematching-v2-industry-map-v1"
KEYWORD_LIBRARY_SCHEMA = "pematching-v2-keywords-v1"

CONFIDENCE_ORDER = {"high": 3, "medium": 2, "low": 1, "none": 0}


def _load_json_config(path: Path, expected_schema: str, hint: str = "") -> Dict[str, Any]:
    if not path.exists():
        message = f"配置文件不存在：{path}"
        if hint:
            message += f"。{hint}"
        raise FileNotFoundError(message)
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"配置文件不是合法 JSON：{path}（{exc}）") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"配置文件根节点必须是对象：{path}")
    actual = payload.get("schemaVersion")
    if actual != expected_schema:
        raise ValueError(
            f"配置文件 schemaVersion 不匹配：{path}，期望 {expected_schema}，实际 {actual!r}"
        )
    return payload


@lru_cache(maxsize=1)
def load_taxonomy() -> Dict[str, Any]:
    """加载 A/B/C/D/E/F 维度定义与评分参数。"""
    return _load_json_config(TAXONOMY_PATH, TAXONOMY_SCHEMA)


@lru_cache(maxsize=1)
def load_industry_map() -> Dict[str, Any]:
    """加载国标行业 → C 标签映射表。"""
    return _load_json_config(
        INDUSTRY_MAP_PATH,
        INDUSTRY_MAP_SCHEMA,
        hint="请先运行 python backend/PEMatching_v2/run_build_industry_map.py 生成",
    )


@lru_cache(maxsize=1)
def load_keyword_library() -> Dict[str, Any]:
    """加载 C 子类关键词与属地园区关键词。"""
    return _load_json_config(KEYWORD_LIBRARY_PATH, KEYWORD_LIBRARY_SCHEMA)


def reset_caches() -> None:
    """清空配置缓存。测试里改配置后调用。"""
    load_taxonomy.cache_clear()
    load_industry_map.cache_clear()
    load_keyword_library.cache_clear()
    scoring_config.cache_clear()
    _subtag_patterns.cache_clear()
    _region_patterns.cache_clear()
    _credit_negative_words.cache_clear()


@lru_cache(maxsize=1)
def scoring_config() -> Dict[str, Any]:
    """评分参数段。权重、分档、阈值全部取自配置，不在代码里写死。"""
    config = load_taxonomy().get("scoring")
    if not isinstance(config, dict):
        raise ValueError(f"配置缺少 scoring 段：{TAXONOMY_PATH}")
    return config


# --------------------------------------------------------------------------
# 枚举校验：二期政策打标依赖这两个函数拒绝非法标签值
# --------------------------------------------------------------------------


def _dimension(dimension: str) -> Dict[str, Any]:
    dims = load_taxonomy().get("dimensions") or {}
    spec = dims.get(dimension)
    if not isinstance(spec, dict):
        raise ValueError(f"未定义的维度：{dimension!r}")
    return spec


def valid_tags(dimension: str, include_sub: bool = True) -> List[str]:
    """返回某维度的全部合法取值。C 维度默认含子类。"""
    spec = _dimension(dimension)
    tags = list((spec.get("values") or {}).keys())
    if include_sub:
        tags.extend((spec.get("subValues") or {}).keys())
    return tags


def is_valid_tag(dimension: str, value: str) -> bool:
    """标签值是否合法。

    这是防「政策库词表污染匹配轴」的关口：C 维度只接受 C1-C11 及子类编号，
    `通用行业`、`半导体和集成电路` 这类政策库原始取值一律拒绝。
    """
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        return value.strip() in set(valid_tags(dimension))
    except ValueError:
        return False


def sub_tag_parent(sub_tag: str) -> str:
    """子类归属的大类。非子类返回空串。"""
    subs = _dimension("C").get("subValues") or {}
    entry = subs.get(sub_tag)
    return str(entry.get("parent") or "") if isinstance(entry, dict) else ""


def validate_tag_payload(tags: Dict[str, Any]) -> List[str]:
    """校验一条政策标签产出。返回错误列表，空列表表示合法。

    结构约定见 docs/开发计划.md 3.2 节输出契约。
    """
    errors: List[str] = []
    if not isinstance(tags, dict):
        return ["tags 必须是对象"]

    def check_confidence(dim: str, entry: Dict[str, Any]) -> None:
        levels = load_taxonomy().get("confidenceLevels") or []
        confidence = entry.get("confidence")
        if confidence not in levels:
            errors.append(f"{dim}.confidence 非法：{confidence!r}，合法值 {levels}")
        if not str(entry.get("basis") or "").strip():
            errors.append(f"{dim}.basis 不能为空")

    for dim in ("A", "B", "C", "D", "E", "F"):
        entry = tags.get(dim)
        if entry is None:
            spec = _dimension(dim)
            if spec.get("required"):
                errors.append(f"缺少必填维度 {dim}")
            continue
        if not isinstance(entry, dict):
            errors.append(f"{dim} 必须是对象")
            continue
        check_confidence(dim, entry)

        if dim == "A":
            primary = entry.get("primary")
            if primary is not None and not is_valid_tag("A", str(primary)):
                errors.append(f"A.primary 非法：{primary!r}")
            for item in entry.get("secondary") or []:
                if not is_valid_tag("A", str(item)):
                    errors.append(f"A.secondary 含非法值：{item!r}")
        elif dim in ("B", "C"):
            for item in entry.get("values") or []:
                if not is_valid_tag(dim, str(item)):
                    errors.append(f"{dim}.values 含非法值：{item!r}")
            if dim == "C":
                for item in entry.get("subValues") or []:
                    if not is_valid_tag("C", str(item)):
                        errors.append(f"C.subValues 含非法值：{item!r}")
                    elif not sub_tag_parent(str(item)):
                        errors.append(f"C.subValues 里 {item!r} 不是子类编号")
        else:
            value = entry.get("value")
            if value is not None and not is_valid_tag(dim, str(value)):
                errors.append(f"{dim}.value 非法：{value!r}")

    return errors


# --------------------------------------------------------------------------
# 国标行业 → C 大类
# --------------------------------------------------------------------------


def resolve_c_tags(level1: str, level2: str) -> Tuple[List[str], str]:
    """国标行业 → C 大类。

    优先级：byLevel2 命中 → byLevel1 命中 → 都未命中。
    返回 (cTags, confidence)。两级都没有映射时返回 ([], "none")，
    由调用方决定落 C9 还是记 unknown（两者语义不同：前者是「通用」，后者是「不知道」）。
    """
    mapping = load_industry_map()
    for key, source in (("byLevel2", level2), ("byLevel1", level1)):
        table = mapping.get(key) or {}
        entry = table.get(str(source or "").strip())
        if not isinstance(entry, dict):
            continue
        tags = [t for t in (entry.get("cTags") or []) if is_valid_tag("C", str(t))]
        if tags:
            return tags, str(entry.get("confidence") or "medium")
        # 有映射记录但 cTags 为空，表示「口径过宽，明确落通用」
        return [], str(entry.get("confidence") or "low")
    return [], "none"


# --------------------------------------------------------------------------
# 关键词匹配
# --------------------------------------------------------------------------


def _compile_keyword(word: str, boundary_required: bool) -> re.Pattern[str]:
    escaped = re.escape(word)
    if boundary_required and re.fullmatch(r"[A-Za-z0-9 ]+", word):
        # 纯英文/数字短词加词边界，避免 AI 命中 CHAIN、EDA 命中 EDABC
        return re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", re.IGNORECASE)
    return re.compile(escaped, re.IGNORECASE)


@lru_cache(maxsize=1)
def _subtag_patterns() -> Dict[str, List[Tuple[str, re.Pattern[str]]]]:
    library = load_keyword_library()
    boundary = set(library.get("wordBoundaryRequired") or [])
    compiled: Dict[str, List[Tuple[str, re.Pattern[str]]]] = {}
    for sub_tag, entries in (library.get("cSubTags") or {}).items():
        items: List[Tuple[str, re.Pattern[str]]] = []
        for entry in entries or []:
            word = str(entry.get("kw") or "").strip() if isinstance(entry, dict) else str(entry).strip()
            if not word:
                continue
            items.append((word, _compile_keyword(word, word in boundary)))
        if items:
            compiled[sub_tag] = items
    return compiled


def match_c_subtags(text: str, with_hits: bool = False):
    """在文本里匹配 C 子类关键词。

    with_hits=True 时返回 {子类: [命中的词]}，便于产出 basis 与排查；
    否则返回子类编号列表。
    """
    haystack = str(text or "")
    hits: Dict[str, List[str]] = {}
    if haystack.strip():
        for sub_tag, items in _subtag_patterns().items():
            matched = [word for word, pattern in items if pattern.search(haystack)]
            if matched:
                hits[sub_tag] = matched
    if with_hits:
        return hits
    return sorted(hits.keys())


@lru_cache(maxsize=1)
def _region_patterns() -> List[Tuple[str, List[re.Pattern[str]]]]:
    library = load_keyword_library()
    compiled: List[Tuple[str, List[re.Pattern[str]]]] = []
    for region, words in (library.get("regions") or {}).items():
        patterns = [re.compile(re.escape(str(w))) for w in (words or []) if str(w).strip()]
        if patterns:
            compiled.append((region, patterns))
    return compiled


def match_regions(district: str = "", zone: str = "", address: str = "") -> List[str]:
    """属地匹配，三级回退：registerDistrict → nationalHighTechZone → registerAddress。

    三个源全部参与匹配后取并集：一家企业可能同时属于某行政区和某园区
    （docx R13/R14 的「市区园区三级叠加」语义）。
    """
    haystack = " ".join(str(v or "") for v in (district, zone, address))
    if not haystack.strip():
        return []
    matched = [region for region, patterns in _region_patterns()
               if any(p.search(haystack) for p in patterns)]
    return sorted(set(matched))


@lru_cache(maxsize=1)
def _credit_negative_words() -> Tuple[str, ...]:
    return tuple(
        str(w).strip()
        for w in (load_keyword_library().get("creditNegative") or [])
        if str(w).strip()
    )


def has_credit_negative(text: str) -> Tuple[bool, List[str]]:
    """信用负面判定。返回 (是否命中, 命中的词)。"""
    haystack = str(text or "")
    if not haystack.strip():
        return False, []
    matched = [word for word in _credit_negative_words() if word in haystack]
    return bool(matched), matched


def better_confidence(left: str, right: str) -> str:
    """取置信度较高的一个。"""
    return left if CONFIDENCE_ORDER.get(left, 0) >= CONFIDENCE_ORDER.get(right, 0) else right
