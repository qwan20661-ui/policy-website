"""规则表单政策模式的共享工具。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict


PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PROJECT_ROOT.parent.parent
DEFAULT_TEST_ENTERPRISE_JSON_PATH = PROJECT_ROOT / "企业测试" / "企业数据.json"
DEFAULT_TECH_SME_RULE_PROFILE_PATH = PROJECT_ROOT / "企业测试" / "科技型中小企业_企业数据直接匹配规则表_20260722.json"


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def derive_rule_policy_title(rule_profile_path: Path, profile: Dict[str, Any], explicit_title: str = "") -> str:
    if explicit_title.strip():
        return explicit_title.strip()

    profile_name = _safe_text(profile.get("policyDisplayName"))
    if profile_name:
        return profile_name

    stem = rule_profile_path.stem
    stem = re.sub(r"_\d{8}$", "", stem)
    stem = stem.replace("_企业数据直接匹配规则表", "")
    stem = stem.replace("_规则表", "")
    stem = stem.strip("_- ")
    if stem:
        return stem

    profile_name = _safe_text(profile.get("name"))
    if profile_name:
        profile_name = profile_name.replace("企业数据直接匹配规则表", "").strip("_- ")
        if profile_name:
            return profile_name

    return rule_profile_path.stem


def build_rule_profile_policy(rule_profile_path: Path, explicit_title: str = "") -> Dict[str, Any]:
    with rule_profile_path.open("r", encoding="utf-8") as fp:
        profile = json.load(fp)

    title = derive_rule_policy_title(rule_profile_path, profile, explicit_title)
    policy_slug = re.sub(r"[^\w\u4e00-\u9fff]+", "-", title).strip("-") or rule_profile_path.stem
    return {
        "id": f"policy::{policy_slug}",
        "title": title,
        "sourcePolicyId": "",
        "structured_conditions": [],
        "documents": [],
        "rule_profile_path": str(rule_profile_path),
        "rule_profile_name": _safe_text(profile.get("name")),
        "rule_profile_version": _safe_text(profile.get("schemaVersion")),
        "condition_profile_name": _safe_text(profile.get("name")),
        "condition_profile_version": _safe_text(profile.get("schemaVersion")),
        "condition_profile_summary": profile.get("summary") or {},
        "condition_profile_flow": profile.get("evaluationFlow") or {},
        "policy_source_type": "condition_profile_json",
    }
