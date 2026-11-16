# -*- coding: utf-8 -*-
"""新匹配链路的政策读取边界。

这里只读取 T2 条件包，并把后续判断所需的接口原始字段按职责归组。模块不做
行业、属地、主体或时效判定，也不改变原文；确定性排除由 T4 负责。
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

from service import fields as F
from service.policy_condition_package import (
    load_condition_package_snapshot,
    validate_condition_package,
)
from service.qualification_growth import qualification_growth_stats


class MatchPolicySourceError(ValueError):
    """新匹配政策源不完整或包含重复政策。"""


def _condition_sources(package: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    applicant_targets = []
    application_windows = []
    for condition in package.get("conditions") or []:
        if not isinstance(condition, dict):
            continue
        condition_id = F.text(condition.get("id"))
        applicant_targets.append({
            "conditionId": condition_id,
            "applicableTarget": deepcopy(condition.get("applicableTarget")),
            "applicableTargetPath": deepcopy(condition.get("applicableTargetPath")),
        })
        application_windows.append({
            "conditionId": condition_id,
            "applicationStartTime": deepcopy(condition.get("applicationStartTime")),
            "applicationEndTime": deepcopy(condition.get("applicationEndTime")),
            "announcementDate": deepcopy(condition.get("announcementDate")),
            "applicationInstruction": deepcopy(condition.get("applicationInstruction")),
        })
    return {
        "applicantTargets": applicant_targets,
        "applicationWindows": application_windows,
    }


def direct_policy_fields(package: Dict[str, Any]) -> Dict[str, Any]:
    """输出可追溯到结构接口的匹配事实，不引入推断标签。"""
    validate_condition_package(package)
    policy_info = package.get("policyInfo") or {}
    condition_sources = _condition_sources(package)
    return {
        "fusionPolicyId": F.text(package.get("fusionPolicyId")),
        "title": F.text(package.get("title")),
        "industry": {
            "value": deepcopy(policy_info.get("industryField")),
            "path": deepcopy(policy_info.get("industryFieldPath")),
        },
        "location": {
            "value": deepcopy(policy_info.get("applicableScope")),
            "path": deepcopy(policy_info.get("applicableScopePath")),
        },
        "applicantTargets": condition_sources["applicantTargets"],
        "supportMethods": deepcopy(package.get("supportMethods") or []),
        "applicationWindows": condition_sources["applicationWindows"],
        "effectivePeriod": {
            "start": deepcopy(policy_info.get("effectiveStartDate")),
            "end": deepcopy(policy_info.get("effectiveEndDate")),
        },
        "keywords": deepcopy(package.get("keywords") or []),
        "policyContentHash": F.text(package.get("policyContentHash")),
        "conditionPackageHash": F.text(package.get("conditionPackageHash")),
    }


def load_match_policy_packages(path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """按政策 ID 加载完整条件包，作为新链路唯一政策集合。"""
    snapshot = load_condition_package_snapshot(path)
    records: Dict[str, Dict[str, Any]] = {}
    for package in snapshot.get("data") or []:
        validate_condition_package(package)
        policy_id = F.text(package.get("fusionPolicyId"))
        if policy_id in records:
            raise MatchPolicySourceError(f"条件包存在重复政策：{policy_id}")
        records[policy_id] = package
    if len(records) != snapshot.get("count"):
        raise MatchPolicySourceError(
            f"条件包政策数不一致：count={snapshot.get('count')}, actual={len(records)}"
        )
    return records


def match_policy_source_stats(path: Optional[Path] = None) -> Dict[str, Any]:
    snapshot = load_condition_package_snapshot(path)
    records = load_match_policy_packages(path)
    direct_fields = [direct_policy_fields(package) for package in records.values()]
    return {
        "status": "ready",
        "count": len(records),
        "versionHash": F.text(snapshot.get("versionHash")),
        "withIndustry": sum(
            1 for item in direct_fields if F.text(item["industry"]["value"])
        ),
        "withLocation": sum(
            1 for item in direct_fields if F.text(item["location"]["value"])
        ),
        "withApplicantTarget": sum(
            1
            for item in direct_fields
            if any(F.text(target.get("applicableTarget")) for target in item["applicantTargets"])
        ),
        "withSupportMethod": sum(
            1 for item in direct_fields if item["supportMethods"]
        ),
        "withApplicationWindow": sum(
            1
            for item in direct_fields
            if any(
                F.text(window.get("applicationStartTime"))
                or F.text(window.get("applicationEndTime"))
                or F.text(window.get("applicationInstruction"))
                for window in item["applicationWindows"]
            )
        ),
        "qualificationGrowth": qualification_growth_stats(),
    }
