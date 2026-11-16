# -*- coding: utf-8 -*-
"""仅加载 V2 本地资格快照，禁止访问 V1 文件。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Tuple

from service.eligibility.constants import MANIFEST_SCHEMA, PROFILE_SCHEMA
from service.eligibility.models import sha256
from service.paths import ELIGIBILITY_MANIFEST_PATH, ELIGIBILITY_PROFILES_PATH, env_str


class ProfileLoadError(RuntimeError):
    pass


def _paths() -> Tuple[Path, Path]:
    manifest = Path(env_str("PEMATCHING_V2_ELIGIBILITY_RULE_MANIFEST", str(ELIGIBILITY_MANIFEST_PATH)))
    profiles = manifest.parent / "policy_profiles.jsonl" if manifest != ELIGIBILITY_MANIFEST_PATH else ELIGIBILITY_PROFILES_PATH
    return manifest, profiles


@lru_cache(maxsize=1)
def load_snapshot() -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
    manifest_path, profiles_path = _paths()
    if not manifest_path.exists() or not profiles_path.exists():
        return {}, {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileLoadError(f"资格 manifest 不可读取：{exc}") from exc
    if manifest.get("schemaVersion") != MANIFEST_SCHEMA:
        raise ProfileLoadError("资格 manifest schemaVersion 不匹配")
    profiles: Dict[str, Dict[str, Any]] = {}
    lines = profiles_path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            profile = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProfileLoadError(f"规则快照第 {index} 行不是 JSON") from exc
        policy_id = str(profile.get("fusionPolicyId") or "").strip()
        if profile.get("schemaVersion") != PROFILE_SCHEMA or not policy_id or policy_id in profiles:
            raise ProfileLoadError(f"规则快照第 {index} 行 schema 或政策 ID 非法")
        profiles[policy_id] = profile
    actual_hash = sha256([profiles[key] for key in sorted(profiles)])
    if manifest.get("contentSha256") and manifest["contentSha256"] != actual_hash:
        raise ProfileLoadError("资格快照哈希与 manifest 不一致")
    return manifest, profiles


def reset_cache() -> None:
    load_snapshot.cache_clear()


def get_profile(policy_id: Any) -> Dict[str, Any] | None:
    return load_snapshot()[1].get(str(policy_id or "").strip())
