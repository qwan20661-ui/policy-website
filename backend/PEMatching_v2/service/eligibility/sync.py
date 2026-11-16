# -*- coding: utf-8 -*-
"""资格规则快照的 API 同步入口，供 CLI 与 api_server 启动复用。

从权威 structure API 拉取“审核通过 + 结构审核通过”的主政策详情，
把其中的 ruleTableJson 迁移为 V2 独立资格快照（policy_profiles.jsonl + manifest.json）。

与 V1 启动时生成“主政策申报条件.json”对应：V1 拉取主政策快照，
这里把同源 API 的规则表直接落为 V2 可执行快照，避免依赖老目录文件。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from service.paths import ELIGIBILITY_CONFIG_DIR, ELIGIBILITY_MANIFEST_PATH, env_str
from service.eligibility.constants import MANIFEST_SCHEMA
from service.eligibility.models import ProfileValidationError, migrate_rule_table, profile_stats, sha256
from service.eligibility.profile_loader import reset_cache as _reset_loader_cache
from service.paths import atomic_write_json

# 与 index.html#structure 页面 filterStats 对齐：
#   mainReviewApproved = 主政策审核通过（reviewStatus=1）
#   structureReviewApproved = 结构审核通过（structureReviewStatus=1）
DEFAULT_REVIEW_STATUS = "1"
DEFAULT_STRUCTURE_REVIEW_STATUS = "1"


class EligibilitySyncError(RuntimeError):
    pass


def _request_json(url: str, params: dict) -> dict:
    from urllib.request import urlopen
    from urllib.parse import urlencode

    query = urlencode({key: value for key, value in params.items() if value not in (None, "")})
    try:
        with urlopen(f"{url}?{query}", timeout=30) as response:  # noqa: S310 - 内部权威结构 API
            return json.loads(response.read().decode("utf-8"))
    except OSError as exc:
        raise EligibilitySyncError(f"结构 API 请求失败（{url}）：{exc}") from exc


def fetch_reviewed_policies(
    base_url: str,
    review_status: str = DEFAULT_REVIEW_STATUS,
    structure_review_status: str = DEFAULT_STRUCTURE_REVIEW_STATUS,
    page_size: int = 50,
) -> Tuple[List[Dict[str, Any]], int]:
    """拉取审核通过的主政策列表及其规则表详情。

    返回 (rows, total)：rows 每项含 fusionPolicyId/title/raw_detail，
    total 为接口 pagination.total（审核过滤后，页面 76 条）。
    """
    base = base_url.rstrip("/")
    page, expected_total, rows = 1, None, []
    while True:
        payload = _request_json(
            f"{base}/api/fusion-classification/policies",
            {
                "page": page, "size": page_size, "requireSourcePolicyId": 1,
                "reviewStatus": review_status, "structureReviewStatus": structure_review_status,
            },
        )
        if not payload.get("success"):
            raise EligibilitySyncError("权威结构 API 返回 success=false")
        data = payload.get("data") or []
        pagination = payload.get("pagination") or {}
        total = pagination.get("total")
        if total is None:
            raise EligibilitySyncError("权威结构 API 未返回 pagination.total，拒绝执行不完整同步")
        if expected_total is None:
            expected_total = int(total)
        elif expected_total != int(total):
            raise EligibilitySyncError("分页过程中 pagination.total 发生变化，拒绝生成不可复现快照")
        for summary in data:
            policy_id = str(summary.get("fusionPolicyId") or summary.get("id") or "").strip()
            if not policy_id:
                continue
            detail_payload = _request_json(
                f"{base}/api/fusion-classification/fusion-detail",
                {"fusionPolicyId": policy_id},
            )
            if not detail_payload.get("success"):
                raise EligibilitySyncError(f"详情拉取失败：{policy_id}")
            rows.append({
                "fusionPolicyId": policy_id,
                "title": summary.get("title") or "",
                "raw_detail": detail_payload.get("data") or {},
            })
        if len(rows) >= expected_total or not data:
            break
        page += 1
    if len(rows) != expected_total:
        raise EligibilitySyncError(f"分页总数与详情落盘数不一致：total={expected_total}, actual={len(rows)}")
    return rows, expected_total


def _entry_rule_table(entry: dict) -> Tuple[str, str, str, Dict[str, Any]]:
    """从 API 详情里提取 ruleTableJson，与同步脚本同一口径。"""
    detail = entry.get("raw_detail") or {}
    rule = entry.get("ruleTableJson") or detail.get("ruleTableJson") or entry.get("rule_table_json")
    policy_info = detail.get("policyInfo") or {}
    policy_id = str(entry.get("fusionPolicyId") or (rule or {}).get("fusionPolicyId") or detail.get("id") or "").strip()
    title = str(entry.get("policyTitle") or entry.get("title") or policy_info.get("policyTitle") or (rule or {}).get("policyDisplayName") or "").strip()
    updated_at = str(entry.get("updatedAt") or detail.get("updatedAt") or detail.get("reviewedAt") or "").strip()
    if not isinstance(rule, dict):
        raise ProfileValidationError(f"{policy_id or '<unknown>'} 缺少 ruleTableJson")
    return policy_id, title, updated_at, rule


def sync_from_api(
    base_url: str,
    *,
    review_status: str = DEFAULT_REVIEW_STATUS,
    structure_review_status: str = DEFAULT_STRUCTURE_REVIEW_STATUS,
    page_size: int = 50,
    write: bool = True,
) -> Dict[str, Any]:
    """拉取并迁移资格快照。返回 manifest（未写盘时可仅用于预览）。"""
    source_rows, source_total = fetch_reviewed_policies(
        base_url,
        review_status=review_status,
        structure_review_status=structure_review_status,
        page_size=page_size,
    )

    return _sync_rows(
        source_rows,
        source_total,
        write=write,
        source_metadata={
            "sourceType": "structure_api_rule_table_json",
            "sourceBaseUrlHash": hashlib.sha256(base_url.encode()).hexdigest(),
            "sourceReviewStatus": review_status,
            "sourceStructureReviewStatus": structure_review_status,
        },
    )


def sync_from_policy_snapshot(
    snapshot: Dict[str, Any],
    *,
    write: bool = True,
) -> Dict[str, Any]:
    """从 T1 已同步政策快照迁移资格规则，避免启动阶段重复请求结构接口。"""
    records = snapshot.get("data")
    if not isinstance(records, list):
        raise EligibilitySyncError("政策快照 data 不是数组")
    source_total = snapshot.get("count")
    if not isinstance(source_total, int) or source_total != len(records):
        raise EligibilitySyncError(
            f"政策快照 count 不一致：count={source_total}, actual={len(records)}"
        )
    source_rows = [
        {
            "fusionPolicyId": record.get("fusionPolicyId"),
            "title": record.get("title"),
            "updatedAt": record.get("updateTime"),
            "ruleTableJson": record.get("ruleTableJson"),
            "raw_detail": record,
        }
        for record in records
        if isinstance(record, dict)
    ]
    if len(source_rows) != source_total:
        raise EligibilitySyncError("政策快照包含非对象记录")
    return _sync_rows(
        source_rows,
        source_total,
        write=write,
        source_metadata={
            "sourceType": "policy_snapshot_rule_table_json",
            "sourceSnapshotVersion": snapshot.get("versionHash") or "legacy",
            "sourceReviewStatus": DEFAULT_REVIEW_STATUS,
            "sourceStructureReviewStatus": DEFAULT_STRUCTURE_REVIEW_STATUS,
        },
    )


def _sync_rows(
    source_rows: List[Dict[str, Any]],
    source_total: int,
    *,
    write: bool,
    source_metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """统一处理 API 与政策快照来源的规则迁移。"""

    profiles: List[Dict[str, Any]] = []
    source_profiles: List[Dict[str, Any]] = []
    invalid: List[Dict[str, Any]] = []
    seen = set()
    for entry in source_rows:
        try:
            policy_id, title, updated_at, rule = _entry_rule_table(entry)
            if not policy_id or policy_id in seen:
                raise ProfileValidationError(f"政策 ID 缺失或重复：{policy_id!r}")
            seen.add(policy_id)
            source_profiles.append({"fusionPolicyId": policy_id, "policyTitle": title, "sourcePolicyUpdatedAt": updated_at, "ruleTableJson": rule})
            profiles.append(migrate_rule_table(policy_id, title, rule, source_updated_at=updated_at))
        except (ProfileValidationError, TypeError, ValueError) as exc:
            invalid.append({"entry": entry.get("fusionPolicyId") or "", "reason": str(exc)})
    if len(seen) + len(invalid) != source_total:
        raise EligibilitySyncError("迁移入账数与来源总数不一致")

    profiles.sort(key=lambda item: item["fusionPolicyId"])
    stats = profile_stats(profiles)
    manifest = {
        "schemaVersion": MANIFEST_SCHEMA,
        "generatedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "sourcePaginationTotal": source_total,
        "fetchedPolicyCount": len(source_rows),
        **stats,
        "invalidProfileCount": len(invalid),
        "contentSha256": sha256(profiles),
        "generatorVersion": "v2-sync",
        **source_metadata,
    }
    if write:
        _write_jsonl(ELIGIBILITY_CONFIG_DIR / "source_profiles.jsonl", sorted(source_profiles, key=lambda item: item["fusionPolicyId"]))
        _write_jsonl(ELIGIBILITY_CONFIG_DIR / "policy_profiles.jsonl", profiles)
        atomic_write_json(ELIGIBILITY_MANIFEST_PATH, manifest)
        atomic_write_json(ELIGIBILITY_CONFIG_DIR / "invalid_profiles.json", invalid)
    return manifest


def _write_jsonl(path, rows: List[Dict[str, Any]]) -> None:
    from service.eligibility.models import canonical_json
    from service.paths import ensure_dir

    ensure_dir(path.parent)
    content = "".join(canonical_json(row) + "\n" for row in rows)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    import os
    os.replace(temporary, path)


def eligibility_api_base_url() -> str:
    """结构 API 根地址：优先 V2_STRUCTURE_API_BASE_URL，回退 STRUCTURE_API_BASE_URL。"""
    url = env_str("V2_STRUCTURE_API_BASE_URL", "") or env_str("STRUCTURE_API_BASE_URL", "")
    if not url:
        raise EligibilitySyncError("未配置结构 API 根地址（V2_STRUCTURE_API_BASE_URL / STRUCTURE_API_BASE_URL）")
    return url


def reset_eligibility_cache() -> None:
    """清掉资格快照的内存缓存，同步后立即生效。"""
    _reset_loader_cache()
