# -*- coding: utf-8 -*-
"""政策标签索引、检索、详情读取。

**这是存储层的唯一出口**（开发计划 11.5）。其他模块一律不许直接读
policy_snapshot.json 或 policy_tags.json——政策库现在 110 条可以全量读内存，
万级规模时要换 SQLite 或分片存储，届时只改本模块内部实现，上层不动。

检索原则「取交集但不苛刻」：
  - 政策 C9 通用应对所有 c_tags 查询命中
  - 政策无 B 标签时不因 b_tags 过滤而被排除
  - confidence=low 的维度不参与过滤，只影响排序
  - keyword 不做硬过滤，只影响排序（打错字不该让结果全空）
不可申报政策（applicability.applicable=false）默认排除。
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from service import fields as F
from service import taxonomy as tx
from service.paths import POLICY_TAGS_OVERRIDES_PATH, POLICY_TAGS_PATH
from service.policy_sync import load_snapshot
from service.scoring import resolve_timeliness  # noqa: F401  对外统一从本模块取
from service.tagging import validate_tagging_result

TAGS_SCHEMA = "pematching-v2-policy-tags-v1"

# applicableScopePath 里表示「全市通用」的层级，区级/园区只对本地企业适用
_UNIVERSAL_SCOPES = ("国家级", "省级", "市级")

SUMMARY_LIMIT = 200


class PolicyIndexError(RuntimeError):
    """索引加载失败。"""


def _load_overrides() -> Dict[str, Dict[str, Any]]:
    """人工复核修正。与 LLM 产出分离存放，重新打标不会冲掉复核成果。"""
    if not POLICY_TAGS_OVERRIDES_PATH.exists():
        return {}
    try:
        payload = json.loads(POLICY_TAGS_OVERRIDES_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    items = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return {}
    return {
        F.text(item.get("fusionPolicyId")): item
        for item in items
        if isinstance(item, dict) and F.text(item.get("fusionPolicyId"))
    }


def _apply_override(record: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """按维度粒度覆盖，未提及的维度保留 LLM 产出。"""
    merged = dict(record)
    # 默认仍保持既有人工复核语义；政策原文可直接证实的系统修正可显式标记来源。
    merged["_source"] = F.text(override.get("sourceKind")) or "manual"

    override_tags = override.get("tags") or {}
    if override_tags:
        tags = dict(merged.get("tags") or {})
        for dimension, entry in override_tags.items():
            if isinstance(entry, dict):
                tags[dimension] = entry
        merged["tags"] = tags

    for key in ("applicability", "applicationWindow", "growthChain", "keywordIndex",
                "scaleThresholds", "notes"):
        if key in override and override[key] is not None:
            merged[key] = override[key]
    return merged


@lru_cache(maxsize=1)
def load_policy_tags() -> Dict[str, Dict[str, Any]]:
    """{fusionPolicyId: 标签记录}。人工 overrides 优先于 LLM 产出。"""
    if not POLICY_TAGS_PATH.exists():
        raise PolicyIndexError(
            f"政策标签不存在：{POLICY_TAGS_PATH}，"
            "请先运行 python backend/PEMatching_v2/run_policy_tagging.py"
        )
    try:
        payload = json.loads(POLICY_TAGS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PolicyIndexError(f"政策标签不是合法 JSON：{POLICY_TAGS_PATH}（{exc}）") from exc
    if payload.get("schemaVersion") != TAGS_SCHEMA:
        raise PolicyIndexError(
            f"政策标签 schemaVersion 不匹配：期望 {TAGS_SCHEMA}，"
            f"实际 {payload.get('schemaVersion')!r}，请重新打标"
        )

    overrides = _load_overrides()
    records: Dict[str, Dict[str, Any]] = {}
    for item in payload.get("data") or []:
        if not isinstance(item, dict):
            continue
        policy_id = F.text(item.get("fusionPolicyId"))
        if not policy_id:
            continue
        record = dict(item)
        record.setdefault("_source", "llm")
        override = overrides.get(policy_id)
        if override:
            record = _apply_override(record, override)
            fatal, _ = validate_tagging_result(record, policy_id)
            if fatal:
                raise PolicyIndexError(
                    f"政策 {policy_id} 的人工 override 非法：{'；'.join(fatal)}"
                )
        records[policy_id] = record
    return records


@lru_cache(maxsize=1)
def _snapshot_index() -> Dict[str, Dict[str, Any]]:
    """{fusionPolicyId: 快照记录}，供属地筛选与详情读取用。"""
    return {
        F.text(record.get("fusionPolicyId")): record
        for record in (load_snapshot().get("data") or [])
        if isinstance(record, dict) and F.text(record.get("fusionPolicyId"))
    }


def reset_cache() -> None:
    """清空索引缓存。打标或同步后调用。"""
    load_policy_tags.cache_clear()
    _snapshot_index.cache_clear()
    _scope_index.cache_clear()


def parse_scope(scope_path: str) -> Tuple[bool, List[str]]:
    """解析 applicableScopePath。

    返回 (是否全市通用, 具体属地列表)。
    国家级/省级/市级 → 全市通用；区级或园区 → 只对该属地企业适用。
    空值按通用处理（不因缺字段而排除政策）。
    """
    raw = F.text(scope_path)
    if not raw:
        return True, []
    if any(level in raw for level in _UNIVERSAL_SCOPES):
        return True, []

    regions: List[str] = []
    library_regions = (tx.load_keyword_library().get("regions") or {}).keys()
    for region in library_regions:
        if region in raw or region.replace("厦门", "") in raw:
            regions.append(region)
    return (not regions), regions


@lru_cache(maxsize=1)
def _scope_index() -> Dict[str, Tuple[bool, Tuple[str, ...]]]:
    index: Dict[str, Tuple[bool, Tuple[str, ...]]] = {}
    for policy_id, record in _snapshot_index().items():
        scope = F.text((record.get("policyInfo") or {}).get("applicableScopePath"))
        universal, regions = parse_scope(scope)
        index[policy_id] = (universal, tuple(regions))
    return index


def _tag_values(record: Dict[str, Any], dimension: str) -> Tuple[List[str], str]:
    """取某维度的取值与置信度。"""
    entry = ((record.get("tags") or {}).get(dimension)) or {}
    confidence = F.text(entry.get("confidence")) or "low"
    if dimension == "A":
        values = [entry.get("primary")] + list(entry.get("secondary") or [])
    elif dimension in ("B", "C"):
        values = list(entry.get("values") or [])
    else:
        values = [entry.get("value")]
    return [str(v) for v in values if v], confidence


def _sub_values(record: Dict[str, Any]) -> List[str]:
    entry = ((record.get("tags") or {}).get("C")) or {}
    return [str(v) for v in (entry.get("subValues") or []) if v]


def is_applicable(record: Dict[str, Any]) -> bool:
    """政策是否面向企业可申报。缺 applicability 时按可申报处理（不因缺字段漏推）。"""
    applicability = record.get("applicability") or {}
    return applicability.get("applicable") is not False


def _match_dimension(
    policy_values: List[str],
    confidence: str,
    wanted: Optional[List[str]],
    generic: Optional[str] = None,
) -> Tuple[bool, bool]:
    """单维度匹配。返回 (是否保留, 是否算命中)。

    「取交集但不苛刻」：
      - 调用方未指定该维度 → 保留，不算命中
      - 政策该维度为空 → 保留，不算命中（不因政策缺标签而排除）
      - confidence=low → 保留，不算命中（低置信度不参与过滤）
      - 政策为通用标签 → 保留但不算命中（C9 只负责召回，不是领域证据）
    """
    if not wanted:
        return True, False
    if not policy_values:
        return True, False
    if confidence == "low":
        return True, False
    if generic and generic in policy_values:
        return True, False
    overlap = set(policy_values) & set(str(w) for w in wanted)
    return bool(overlap), bool(overlap)


def search_policies(
    a_tags: Optional[List[str]] = None,
    c_tags: Optional[List[str]] = None,
    c_sub_tags: Optional[List[str]] = None,
    b_tags: Optional[List[str]] = None,
    d_levels: Optional[List[str]] = None,
    region_tags: Optional[List[str]] = None,
    keyword: str = "",
    limit: int = 20,
    include_not_applicable: bool = False,
) -> List[Dict[str, Any]]:
    """按标签检索政策，返回摘要列表（不含全文）。

    排序：命中维度数降序 → 置信度高低 → fusionPolicyId 稳定排序，
    保证同一查询结果可复现（三期双模式比对依赖这一点）。
    """
    generic = tx.load_taxonomy()["dimensions"]["C"]["genericTag"]
    scopes = _scope_index()
    wanted_regions: Set[str] = set(region_tags or [])
    keyword_text = F.text(keyword)

    scored: List[Tuple[int, int, str, Dict[str, Any]]] = []

    for policy_id, record in load_policy_tags().items():
        if not include_not_applicable and not is_applicable(record):
            continue

        hits = 0
        matched_dimensions: List[str] = []

        a_values, a_confidence = _tag_values(record, "A")
        keep, hit = _match_dimension(a_values, a_confidence, a_tags)
        if not keep:
            continue
        if hit:
            hits += 1
            matched_dimensions.append("A")

        c_values, c_confidence = _tag_values(record, "C")
        keep, hit = _match_dimension(c_values, c_confidence, c_tags, generic)
        if not keep:
            continue
        if hit:
            hits += 1
            matched_dimensions.append("C")

        if c_sub_tags:
            sub_overlap = set(_sub_values(record)) & set(str(s) for s in c_sub_tags)
            if sub_overlap:
                hits += 2  # 子类命中权重更高，对应领域匹配度 30 分档
                matched_dimensions.append("C子类")

        b_values, b_confidence = _tag_values(record, "B")
        keep, hit = _match_dimension(b_values, b_confidence, b_tags)
        if not keep:
            continue
        if hit:
            hits += 1
            matched_dimensions.append("B")

        d_values, d_confidence = _tag_values(record, "D")
        keep, hit = _match_dimension(d_values, d_confidence, d_levels)
        if not keep:
            continue
        if hit:
            hits += 1
            matched_dimensions.append("D")

        universal, policy_regions = scopes.get(policy_id, (True, ()))
        if not universal:
            if wanted_regions and not (set(policy_regions) & wanted_regions):
                continue
            if not wanted_regions:
                # 调用方未给属地，区级政策无法判定是否适用，保留但不算命中
                pass
        if policy_regions and wanted_regions and (set(policy_regions) & wanted_regions):
            hits += 1
            matched_dimensions.append("属地")

        # keyword 只影响排序，不做硬过滤——打错字不该让结果全空
        if keyword_text:
            index = record.get("keywordIndex") or {}
            haystack = " ".join([
                F.text(record.get("title")),
                " ".join(str(x) for x in (index.get("title") or [])),
                " ".join(str(x) for x in (index.get("aKeywords") or [])),
                " ".join(str(x) for x in (index.get("cKeywords") or [])),
            ])
            if keyword_text in haystack:
                hits += 1
                matched_dimensions.append("关键词")

        confidence_score = sum(
            {"high": 3, "medium": 2, "low": 1}.get(c, 0)
            for c in (a_confidence, c_confidence, b_confidence, d_confidence)
        )
        summary = dict(_summarize(record, policy_id))
        summary["matchedDimensions"] = matched_dimensions
        summary["matchScore"] = hits
        scored.append((-hits, -confidence_score, policy_id, summary))

    scored.sort(key=lambda item: (item[0], item[1], item[2]))
    return [item[3] for item in scored[:limit]]


def _summarize(record: Dict[str, Any], policy_id: str) -> Dict[str, Any]:
    """政策摘要。不返回全文——防 Agent 上下文膨胀（开发计划 7.2）。"""
    snapshot = _snapshot_index().get(policy_id) or {}
    policy_info = snapshot.get("policyInfo") or {}
    return {
        "fusionPolicyId": policy_id,
        "title": F.text(record.get("title")) or F.text(snapshot.get("title")),
        "tags": record.get("tags") or {},
        "applicationWindow": record.get("applicationWindow") or {},
        "scaleThresholds": record.get("scaleThresholds") or {},
        "applicability": record.get("applicability") or {},
        "growthChain": record.get("growthChain") or {},
        "summary": F.text(policy_info.get("summary"))[:SUMMARY_LIMIT],
        "applicableScopePath": F.text(policy_info.get("applicableScopePath")),
        "source": F.text(record.get("_source")) or "llm",
    }


def get_policy_summary(fusion_policy_id: str) -> Optional[Dict[str, Any]]:
    """单条政策摘要。用于按 ID 补齐信息。"""
    policy_id = F.text(fusion_policy_id)
    record = load_policy_tags().get(policy_id)
    return _summarize(record, policy_id) if record else None


def get_policy_detail(fusion_policy_id: str) -> Dict[str, Any]:
    """单条政策完整信息，Agent 按需调用。

    只支持单条——批量读全文是万级规模下爆内存的主因（开发计划 11.5）。
    """
    policy_id = F.text(fusion_policy_id)
    snapshot = _snapshot_index().get(policy_id)
    if snapshot is None:
        raise PolicyIndexError(f"政策不存在：{fusion_policy_id}")
    tags = load_policy_tags().get(policy_id) or {}
    policy_info = snapshot.get("policyInfo") or {}

    return {
        "fusionPolicyId": policy_id,
        "title": F.text(snapshot.get("title")),
        "summary": F.text(policy_info.get("summary")),
        "industryField": F.text(policy_info.get("industryField")),
        "applicableScopePath": F.text(policy_info.get("applicableScopePath")),
        "sceneUsage": F.text(policy_info.get("sceneUsage")),
        "issuingDepartment": F.text(policy_info.get("issuingDepartment")),
        "supportMethods": snapshot.get("supportMethods") or [],
        "structuredConditions": snapshot.get("structuredConditions") or [],
        "materials": snapshot.get("materials") or [],
        "keywords": snapshot.get("keywords") or [],
        "tags": tags.get("tags") or {},
        "applicability": tags.get("applicability") or {},
        "applicationWindow": tags.get("applicationWindow") or {},
        "scaleThresholds": tags.get("scaleThresholds") or {},
        "growthChain": tags.get("growthChain") or {},
    }


def iter_policies() -> Iterator[Dict[str, Any]]:
    """流式遍历已打标政策，供批跑与统计用。"""
    for policy_id, record in sorted(load_policy_tags().items()):
        yield record


def index_stats() -> Dict[str, Any]:
    """索引统计，供 /health 与排查用。"""
    records = load_policy_tags()
    scopes = _scope_index()
    applicable = sum(1 for r in records.values() if is_applicable(r))
    manual = sum(1 for r in records.values() if F.text(r.get("_source")) == "manual")

    from collections import Counter

    c_major: Counter = Counter()
    c_sub: Counter = Counter()
    a_primary: Counter = Counter()
    d_levels: Counter = Counter()
    region_only: Counter = Counter()

    for policy_id, record in records.items():
        values, _ = _tag_values(record, "C")
        for value in values:
            c_major[value] += 1
        for value in _sub_values(record):
            c_sub[value] += 1
        a_values, _ = _tag_values(record, "A")
        if a_values:
            a_primary[a_values[0]] += 1
        d_values, _ = _tag_values(record, "D")
        if d_values:
            d_levels[d_values[0]] += 1
        universal, regions = scopes.get(policy_id, (True, ()))
        if not universal:
            for region in regions:
                region_only[region] += 1

    return {
        "total": len(records),
        "applicable": applicable,
        "notApplicable": len(records) - applicable,
        "manualOverrides": manual,
        "cMajor": dict(c_major.most_common()),
        "cSub": dict(c_sub.most_common()),
        "aPrimary": dict(a_primary.most_common()),
        "dLevels": dict(d_levels.most_common()),
        "regionRestricted": dict(region_only.most_common()),
    }
