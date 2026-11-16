# -*- coding: utf-8 -*-
"""独立资质成长图配置。

本模块只负责加载和校验业务确认的“当前状态 -> 直接后继”关系。政策归类和
企业预过滤属于 T4，不能在这里通过标题猜测后直接排除政策。
"""

from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from service import fields as F
from service.paths import QUALIFICATION_GROWTH_CHAIN_PATH

CONFIG_SCHEMA = "qualification-growth-chain-v1"


class QualificationGrowthConfigError(ValueError):
    """资质成长图配置不完整或存在悬空引用。"""


def _string_list(value: Any, location: str, allow_empty: bool = False) -> List[str]:
    if not isinstance(value, list):
        raise QualificationGrowthConfigError(f"{location} 必须是数组")
    items = [F.text(item) for item in value]
    if any(not item for item in items):
        raise QualificationGrowthConfigError(f"{location} 不能包含空值")
    if len(items) != len(set(items)):
        raise QualificationGrowthConfigError(f"{location} 不能包含重复值")
    if not allow_empty and not items:
        raise QualificationGrowthConfigError(f"{location} 不能为空")
    return items


def validate_qualification_growth_config(payload: Any) -> Dict[str, Any]:
    """校验成长图结构、唯一标识和所有直接后继引用。"""
    if not isinstance(payload, dict):
        raise QualificationGrowthConfigError("资质成长图根节点必须是对象")
    if payload.get("schemaVersion") != CONFIG_SCHEMA:
        raise QualificationGrowthConfigError(
            f"资质成长图 schemaVersion 不匹配：期望 {CONFIG_SCHEMA}"
        )

    qualification_fields = set(_string_list(
        payload.get("qualificationFields"), "qualificationFields"
    ))
    targets = payload.get("policyTargets")
    states = payload.get("states")
    if not isinstance(targets, list) or not targets:
        raise QualificationGrowthConfigError("policyTargets 必须是非空数组")
    if not isinstance(states, list) or not states:
        raise QualificationGrowthConfigError("states 必须是非空数组")

    target_ids = set()
    for index, target in enumerate(targets):
        location = f"policyTargets[{index}]"
        if not isinstance(target, dict):
            raise QualificationGrowthConfigError(f"{location} 必须是对象")
        target_id = F.text(target.get("targetId"))
        if not target_id or not F.text(target.get("label")):
            raise QualificationGrowthConfigError(f"{location} 缺少 targetId 或 label")
        if target_id in target_ids:
            raise QualificationGrowthConfigError(f"targetId 重复：{target_id}")
        target_ids.add(target_id)
        _string_list(target.get("matchTerms"), f"{location}.matchTerms")
        _string_list(
            target.get("excludeTerms") or [],
            f"{location}.excludeTerms",
            allow_empty=True,
        )

    state_ids = set()
    fallback_count = 0
    for index, state in enumerate(states):
        location = f"states[{index}]"
        if not isinstance(state, dict):
            raise QualificationGrowthConfigError(f"{location} 必须是对象")
        state_id = F.text(state.get("stateId"))
        if not state_id or not F.text(state.get("label")):
            raise QualificationGrowthConfigError(f"{location} 缺少 stateId 或 label")
        if state_id in state_ids:
            raise QualificationGrowthConfigError(f"stateId 重复：{state_id}")
        state_ids.add(state_id)
        fields = set(_string_list(
            state.get("qualificationFields"),
            f"{location}.qualificationFields",
            allow_empty=True,
        ))
        unknown_fields = fields - qualification_fields
        if unknown_fields:
            raise QualificationGrowthConfigError(
                f"{location} 引用了未声明资质字段：{sorted(unknown_fields)}"
            )
        next_ids = set(_string_list(
            state.get("nextTargetIds"), f"{location}.nextTargetIds"
        ))
        unknown_targets = next_ids - target_ids
        if unknown_targets:
            raise QualificationGrowthConfigError(
                f"{location} 引用了不存在的后继目标：{sorted(unknown_targets)}"
            )
        if state.get("fallbackWhenNoQualificationIsTrue") is True:
            fallback_count += 1
            if fields:
                raise QualificationGrowthConfigError(
                    f"{location} 的无资质兜底状态不能绑定资质字段"
                )
    if fallback_count != 1:
        raise QualificationGrowthConfigError("必须且只能配置一个无资质兜底状态")
    return payload


@lru_cache(maxsize=4)
def _load_cached(path_text: str) -> Dict[str, Any]:
    path = Path(path_text)
    if not path.exists():
        raise FileNotFoundError(f"资质成长图配置不存在：{path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise QualificationGrowthConfigError(
            f"资质成长图不是合法 JSON：{path}（{exc}）"
        ) from exc
    validate_qualification_growth_config(payload)
    return payload


def load_qualification_growth_config(
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    """读取人工业务配置并返回副本，防止调用方污染进程内缓存。"""
    target = path or QUALIFICATION_GROWTH_CHAIN_PATH
    return deepcopy(_load_cached(str(target.resolve())))


def reset_qualification_growth_cache() -> None:
    _load_cached.cache_clear()


def qualification_growth_stats(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    config = payload if payload is not None else load_qualification_growth_config()
    validate_qualification_growth_config(config)
    states = config["states"]
    return {
        "schemaVersion": config["schemaVersion"],
        "qualificationFieldCount": len(config["qualificationFields"]),
        "stateCount": len(states),
        "policyTargetCount": len(config["policyTargets"]),
        "branchStateCount": sum(
            1 for state in states if len(state.get("nextTargetIds") or []) > 1
        ),
    }


def successor_target_ids(state_id: str, payload: Optional[Dict[str, Any]] = None) -> List[str]:
    """按状态标识读取直接后继；不递归展开，防止新链路跨级推荐。"""
    config = payload if payload is not None else load_qualification_growth_config()
    validate_qualification_growth_config(config)
    expected = F.text(state_id)
    for state in config["states"]:
        if state["stateId"] == expected:
            return list(state["nextTargetIds"])
    raise QualificationGrowthConfigError(f"未知资质状态：{state_id!r}")
