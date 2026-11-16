# -*- coding: utf-8 -*-
"""审核政策同步、完整性校验、内容指纹与可回滚快照。"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from service import fields as F
from service.paths import (
    POLICY_LIBRARY_DIR,
    POLICY_SNAPSHOT_PATH,
    atomic_write_json,
    env_bool,
    env_float,
    env_int,
    env_str,
)

SNAPSHOT_SCHEMA = "pematching-v2-policy-snapshot-v2"
LEGACY_SNAPSHOT_SCHEMA = "pematching-v2-policy-snapshot-v1"
SUPPORTED_SNAPSHOT_SCHEMAS = {SNAPSHOT_SCHEMA, LEGACY_SNAPSHOT_SCHEMA}

LIST_PATH = "/api/fusion-classification/policies"
DETAIL_PATH = "/api/fusion-classification/fusion-detail"
FIXED_LIST_FILTERS = {
    "reviewStatus": "1",
    "structureReviewStatus": "1",
    "requireSourcePolicyId": 1,
}
RELATED_CONDITION_LIMIT = 5

_SYNC_STATE_LOCK = threading.Lock()
_SYNC_STATE: Dict[str, Any] = {
    "status": "not_started",
    "ready": False,
    "policyCount": 0,
    "version": "",
    "snapshotFetchedAt": "",
    "lastAttemptAt": "",
    "source": "",
    "degradedReason": "",
}


class PolicySyncError(RuntimeError):
    """政策同步失败，且不会切换当前可用快照。"""

    def __init__(self, message: str, failures: Optional[List[Dict[str, str]]] = None) -> None:
        super().__init__(message)
        self.failures = failures or []


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _base_url() -> str:
    url = env_str("V2_STRUCTURE_API_BASE_URL", "http://127.0.0.1:3000")
    return url.rstrip("/")


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _policy_content_hash(record: Dict[str, Any]) -> str:
    content = {key: value for key, value in record.items() if key != "policyContentHash"}
    return _sha256(content)


def _version_hash(records: List[Dict[str, Any]]) -> str:
    fingerprints = sorted(
        (
            {
                "fusionPolicyId": F.text(record.get("fusionPolicyId")),
                "policyContentHash": F.text(record.get("policyContentHash")),
            }
            for record in records
        ),
        key=lambda item: item["fusionPolicyId"],
    )
    return _sha256(fingerprints)


def _set_sync_state(**changes: Any) -> Dict[str, Any]:
    with _SYNC_STATE_LOCK:
        _SYNC_STATE.update(changes)
        return deepcopy(_SYNC_STATE)


def get_sync_status() -> Dict[str, Any]:
    """返回进程内政策同步状态，供健康接口读取。"""
    with _SYNC_STATE_LOCK:
        return deepcopy(_SYNC_STATE)


def _get_json(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    query = urllib.parse.urlencode(params or {})
    url = f"{_base_url()}{path}" + (f"?{query}" if query else "")
    timeout = env_int("V2_POLICY_API_TIMEOUT", 30)
    retry_count = max(env_int("PEMATCHING_V2_POLICY_SYNC_RETRY", 2), 0)
    backoff = max(env_float("PEMATCHING_V2_POLICY_SYNC_BACKOFF", 0.8), 0.0)

    last_error: Optional[Exception] = None
    for attempt in range(retry_count + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise PolicySyncError(f"接口响应不是对象：{path}")
            if not payload.get("success"):
                raise PolicySyncError(f"接口返回 success=false：{path}")
            return payload
        except PolicySyncError:
            raise
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < retry_count:
                time.sleep(backoff * (attempt + 1))
    raise PolicySyncError(f"请求失败：{url}（{last_error}）")


def _slim_related(items: Any) -> List[Dict[str, Any]]:
    """关联政策只展开一层，避免递归放大快照。"""
    if not isinstance(items, list):
        return []
    slim: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        policy_info = item.get("policyInfo") or {}
        conditions = item.get("conditions") or item.get("structuredConditions") or []
        slim.append({
            "fusionPolicyId": F.text(item.get("id") or item.get("fusionPolicyId")),
            "title": F.text(
                item.get("policyTitle")
                or item.get("directoryTitle")
                or policy_info.get("policyTitle")
            ),
            "summary": F.text(policy_info.get("summary"))[:300],
            "conditions": [
                {
                    "applicableTarget": F.text(condition.get("applicableTarget"))[:200],
                    "applicableTargetPath": F.text(condition.get("applicableTargetPath"))[:300],
                    "applicationRequirement": F.text(
                        condition.get("applicationRequirement")
                    )[:1000],
                }
                for condition in conditions[:RELATED_CONDITION_LIMIT]
                if isinstance(condition, dict)
            ],
        })
    return slim


def _slim_documents(items: Any) -> List[Dict[str, Any]]:
    """文档保留政策年份与动作判断所需字段，不保存正文和附件。"""
    if not isinstance(items, list):
        return []
    return [
        {
            "publishDate": F.text(document.get("publishDate")),
            "policyLevel": F.text(document.get("policyLevel")),
            "issuingDepartment": F.text(document.get("issuingDepartment")),
            "policyAttribute": F.text(document.get("policyAttribute")),
            "policyTitle": F.text(document.get("policyTitle")),
            "reviewStatus": F.text(document.get("reviewStatus")),
        }
        for document in items
        if isinstance(document, dict)
    ]


def _extract_update_time(summary: Dict[str, Any], detail: Dict[str, Any]) -> str:
    for source in (summary, detail):
        for key in ("updatedAt", "reviewedAt", "structureReviewedAt"):
            value = F.text(source.get(key))
            if value:
                return value
    dates = [
        F.text(document.get("publishDate"))
        for document in (detail.get("documents") or [])
        if isinstance(document, dict)
    ]
    dates = [value for value in dates if value]
    return max(dates) if dates else ""


def _normalize(summary: Dict[str, Any], detail: Dict[str, Any]) -> Dict[str, Any]:
    """组装 T1 快照记录，同时保留旧链路需要的兼容字段。"""
    policy_info = detail.get("policyInfo") or summary.get("policyInfo") or {}
    conditions = detail.get("conditions") or []
    if not isinstance(conditions, list):
        raise PolicySyncError(f"政策 {detail.get('id') or summary.get('id')} conditions 不是数组")

    record = {
        "fusionPolicyId": F.text(detail.get("id") or summary.get("id")),
        "sourcePolicyId": F.text(
            detail.get("sourcePolicyId") or summary.get("sourcePolicyId")
        ),
        "title": F.text(
            summary.get("policyTitle")
            or summary.get("directoryTitle")
            or detail.get("directoryTitle")
            or policy_info.get("policyTitle")
            or detail.get("mainFileTitle")
        ),
        "updateTime": _extract_update_time(summary, detail),
        "status": F.text(detail.get("status") or summary.get("status")),
        "reviewStatus": F.text(
            detail.get("reviewStatus") or summary.get("reviewStatus")
        ),
        "structureReviewStatus": F.text(
            detail.get("structureReviewStatus") or summary.get("structureReviewStatus")
        ),
        "reviewedAt": F.text(detail.get("reviewedAt") or summary.get("reviewedAt")),
        "structureReviewedAt": F.text(
            detail.get("structureReviewedAt") or summary.get("structureReviewedAt")
        ),
        "policyInfo": policy_info,
        "conditions": conditions,
        "structuredConditions": conditions,
        "ruleTableJson": detail.get("ruleTableJson"),
        "supportMethods": detail.get("supportMethods") or [],
        "keywords": detail.get("keywords") or [],
        "materials": detail.get("materials") or [],
        "documents": _slim_documents(detail.get("documents")),
        "relatedPolicies": {
            "parent": _slim_related(
                [detail.get("parentPolicy")] if detail.get("parentPolicy") else []
            ),
            "directParents": _slim_related(detail.get("directParentPolicies")),
            "ancestors": _slim_related(detail.get("ancestorPolicies")),
            "children": _slim_related(detail.get("childPolicies")),
            "descendants": _slim_related(detail.get("descendantPolicies")),
        },
    }
    if not record["fusionPolicyId"]:
        raise PolicySyncError("政策详情缺少 id")
    record["policyContentHash"] = _policy_content_hash(record)
    return record


def _fetch_summaries(page_size: int) -> Tuple[List[Dict[str, Any]], int]:
    summaries: List[Dict[str, Any]] = []
    seen_policy_ids = set()
    expected_total: Optional[int] = None
    page = 1

    while expected_total is None or len(summaries) < expected_total:
        params = {
            "page": page,
            "size": page_size,
            **FIXED_LIST_FILTERS,
        }
        payload = _get_json(LIST_PATH, params)
        items = payload.get("data")
        if not isinstance(items, list):
            raise PolicySyncError(f"列表接口 data 不是数组：{type(items).__name__}")

        pagination = payload.get("pagination")
        if not isinstance(pagination, dict) or pagination.get("total") is None:
            raise PolicySyncError("列表接口缺少 pagination.total，拒绝生成不完整快照")
        current_total = F.to_int(pagination.get("total"))
        if current_total is None or current_total < 0:
            raise PolicySyncError(f"pagination.total 非法：{pagination.get('total')!r}")
        if expected_total is None:
            expected_total = current_total
        elif expected_total != current_total:
            raise PolicySyncError("分页过程中 pagination.total 发生变化，拒绝生成快照")

        if not items and len(summaries) < expected_total:
            raise PolicySyncError(
                f"分页提前结束：total={expected_total}, actual={len(summaries)}"
            )
        for summary in items:
            if not isinstance(summary, dict):
                raise PolicySyncError("列表接口包含非对象政策")
            policy_id = F.text(summary.get("id") or summary.get("fusionPolicyId"))
            if not policy_id:
                raise PolicySyncError("列表接口包含缺少 id 的政策")
            if policy_id in seen_policy_ids:
                raise PolicySyncError(f"列表接口包含重复政策 id：{policy_id}")
            seen_policy_ids.add(policy_id)
            summaries.append(summary)
        page += 1

    if expected_total is None or len(summaries) != expected_total:
        raise PolicySyncError(
            f"分页总数不一致：total={expected_total}, actual={len(summaries)}"
        )
    return summaries, expected_total


def _fetch_detail(summary: Dict[str, Any]) -> Dict[str, Any]:
    policy_id = F.text(summary.get("id") or summary.get("fusionPolicyId"))
    payload = _get_json(DETAIL_PATH, {"fusionPolicyId": policy_id})
    detail = payload.get("data")
    if not isinstance(detail, dict) or not detail:
        raise PolicySyncError(f"政策 {policy_id} 详情为空")
    detail_policy_id = F.text(detail.get("id") or detail.get("fusionPolicyId"))
    if detail_policy_id != policy_id:
        raise PolicySyncError(
            f"政策详情 id 不一致：request={policy_id}, response={detail_policy_id}"
        )
    if F.text(detail.get("reviewStatus")) != FIXED_LIST_FILTERS["reviewStatus"]:
        raise PolicySyncError(f"政策 {policy_id} 主政策审核状态不是 1")
    if (
        F.text(detail.get("structureReviewStatus"))
        != FIXED_LIST_FILTERS["structureReviewStatus"]
    ):
        raise PolicySyncError(f"政策 {policy_id} 结构审核状态不是 1")
    if not F.text(detail.get("sourcePolicyId")):
        raise PolicySyncError(f"政策 {policy_id} 缺少 sourcePolicyId")
    return _normalize(summary, detail)


def _fetch_details(summaries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    worker_count = min(
        max(env_int("PEMATCHING_V2_POLICY_SYNC_WORKERS", 6), 1),
        max(len(summaries), 1),
        32,
    )
    records: List[Optional[Dict[str, Any]]] = [None] * len(summaries)
    failures: List[Dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(_fetch_detail, summary): (index, summary)
            for index, summary in enumerate(summaries)
        }
        for future in as_completed(futures):
            index, summary = futures[future]
            policy_id = F.text(summary.get("id") or summary.get("fusionPolicyId"))
            try:
                records[index] = future.result()
            except Exception as exc:
                failures.append({
                    "fusionPolicyId": policy_id,
                    "error": f"{type(exc).__name__}: {exc}",
                })
    if failures:
        failures.sort(key=lambda item: item["fusionPolicyId"])
        failed_ids = ", ".join(item["fusionPolicyId"] for item in failures[:10])
        raise PolicySyncError(
            f"{len(failures)} 条政策详情拉取失败，拒绝切换不完整快照：{failed_ids}",
            failures=failures,
        )
    return [record for record in records if record is not None]


def previous_snapshot_path() -> Path:
    return POLICY_SNAPSHOT_PATH.with_name(
        f"{POLICY_SNAPSHOT_PATH.stem}.previous{POLICY_SNAPSHOT_PATH.suffix}"
    )


def _validate_snapshot(snapshot: Any) -> Dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise ValueError("政策快照根节点不是对象")
    schema_version = snapshot.get("schemaVersion")
    if schema_version not in SUPPORTED_SNAPSHOT_SCHEMAS:
        raise ValueError(
            f"政策快照 schemaVersion 不匹配：支持 {sorted(SUPPORTED_SNAPSHOT_SCHEMAS)}，"
            f"实际 {schema_version!r}"
        )
    records = snapshot.get("data")
    if not isinstance(records, list):
        raise ValueError("政策快照 data 不是数组")
    count = F.to_int(snapshot.get("count"))
    if count is None or count != len(records):
        raise ValueError(f"政策快照 count 不一致：count={count}, actual={len(records)}")

    if schema_version == SNAPSHOT_SCHEMA:
        if snapshot.get("failures"):
            raise ValueError("新版政策快照包含详情失败记录")
        if F.to_int(snapshot.get("listTotal")) != count:
            raise ValueError("新版政策快照 listTotal 与 count 不一致")
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("新版政策快照包含非对象记录")
            expected_hash = _policy_content_hash(record)
            if record.get("policyContentHash") != expected_hash:
                raise ValueError(
                    f"政策 {record.get('fusionPolicyId')} 内容指纹校验失败"
                )
        if snapshot.get("versionHash") != _version_hash(records):
            raise ValueError("政策快照全库版本哈希校验失败")
    return snapshot


def _read_snapshot(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"政策快照不存在：{path}")
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"政策快照不是合法 JSON：{path}（{exc}）") from exc
    return _validate_snapshot(snapshot)


def _write_snapshot(snapshot: Dict[str, Any]) -> None:
    if POLICY_SNAPSHOT_PATH.exists():
        try:
            current = _read_snapshot(POLICY_SNAPSHOT_PATH)
        except (FileNotFoundError, OSError, ValueError):
            current = None
        if current is not None:
            atomic_write_json(previous_snapshot_path(), current)
    atomic_write_json(POLICY_SNAPSHOT_PATH, snapshot)


def fetch_policies(force: bool = True, page_size: Optional[int] = None) -> Dict[str, Any]:
    """从权威接口完整同步政策；保留 force 参数仅为兼容旧调用方。"""
    del force
    size = page_size or env_int("V2_POLICY_PAGE_SIZE", 100)
    if size <= 0:
        raise PolicySyncError(f"分页大小必须为正整数，实际 {size}")

    summaries, expected_total = _fetch_summaries(size)
    records = _fetch_details(summaries)
    if len(records) != expected_total:
        raise PolicySyncError(
            f"详情总数不一致：total={expected_total}, actual={len(records)}"
        )

    snapshot = {
        "schemaVersion": SNAPSHOT_SCHEMA,
        "fetchedAt": _now_iso(),
        "source": _base_url(),
        "filters": dict(FIXED_LIST_FILTERS),
        "count": len(records),
        "listTotal": expected_total,
        "versionHash": _version_hash(records),
        "failures": [],
        "data": records,
    }
    _validate_snapshot(snapshot)
    _write_snapshot(snapshot)
    return snapshot


def load_snapshot() -> Dict[str, Any]:
    """读取当前快照；兼容旧版快照以支持首次同步失败时降级。"""
    try:
        return _read_snapshot(POLICY_SNAPSHOT_PATH)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"政策快照不存在：{POLICY_SNAPSHOT_PATH}，"
            "请先运行 python3 backend/PEMatching_v2/run_sync_policies.py"
        ) from exc


def load_previous_snapshot() -> Dict[str, Any]:
    """读取上一份可回滚快照。"""
    return _read_snapshot(previous_snapshot_path())


def _snapshot_version(snapshot: Dict[str, Any]) -> str:
    version = F.text(snapshot.get("versionHash"))
    if version:
        return version
    records = [record for record in snapshot.get("data") or [] if isinstance(record, dict)]
    return _sha256(sorted(records, key=lambda item: F.text(item.get("fusionPolicyId"))))


def _activate_existing_snapshot(status: str, reason: str = "") -> Dict[str, Any]:
    try:
        snapshot = load_snapshot()
    except (FileNotFoundError, OSError, ValueError):
        snapshot = load_previous_snapshot()
        atomic_write_json(POLICY_SNAPSHOT_PATH, snapshot)
    return _set_sync_state(
        status=status,
        ready=True,
        policyCount=snapshot.get("count", 0),
        version=_snapshot_version(snapshot),
        snapshotFetchedAt=F.text(snapshot.get("fetchedAt")),
        lastAttemptAt=_now_iso(),
        source=F.text(snapshot.get("source")),
        degradedReason=reason,
    )


def synchronize_on_startup() -> Dict[str, Any]:
    """启动时同步一次；失败时使用旧快照，首次失败则保持未就绪。"""
    if POLICY_LIBRARY_DIR:
        try:
            return _activate_existing_snapshot("ready")
        except (FileNotFoundError, OSError, ValueError) as exc:
            return _set_sync_state(
                status="unavailable",
                ready=False,
                lastAttemptAt=_now_iso(),
                degradedReason=f"独立政策库不可用：{type(exc).__name__}: {exc}",
            )

    if not env_bool("PEMATCHING_V2_POLICY_SYNC_ON_STARTUP", True):
        try:
            return _activate_existing_snapshot("ready")
        except (FileNotFoundError, OSError, ValueError) as exc:
            return _set_sync_state(
                status="unavailable",
                ready=False,
                lastAttemptAt=_now_iso(),
                degradedReason=f"启动同步已关闭且无可用快照：{type(exc).__name__}: {exc}",
            )

    _set_sync_state(
        status="syncing",
        ready=False,
        lastAttemptAt=_now_iso(),
        degradedReason="",
    )
    try:
        snapshot = fetch_policies()
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        try:
            return _activate_existing_snapshot("degraded", reason)
        except (FileNotFoundError, OSError, ValueError):
            return _set_sync_state(
                status="unavailable",
                ready=False,
                policyCount=0,
                version="",
                snapshotFetchedAt="",
                lastAttemptAt=_now_iso(),
                source=_base_url(),
                degradedReason=reason,
            )

    return _set_sync_state(
        status="ready",
        ready=True,
        policyCount=snapshot["count"],
        version=snapshot["versionHash"],
        snapshotFetchedAt=snapshot["fetchedAt"],
        lastAttemptAt=_now_iso(),
        source=snapshot["source"],
        degradedReason="",
    )


def iter_policies() -> Iterator[Dict[str, Any]]:
    """流式遍历政策，存储实现变化时调用方无需调整。"""
    for record in load_snapshot().get("data") or []:
        if isinstance(record, dict):
            yield record


def snapshot_stats(snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """返回快照质量、版本与核心字段覆盖率。"""
    snapshot = snapshot or load_snapshot()
    records = [record for record in (snapshot.get("data") or []) if isinstance(record, dict)]
    total = len(records)

    def ratio(predicate) -> str:
        hits = sum(1 for record in records if predicate(record))
        return f"{hits}/{total}" + (f" ({hits * 100 // total}%)" if total else "")

    attributes: Dict[str, int] = {}
    for record in records:
        for document in record.get("documents") or []:
            key = F.text(document.get("policyAttribute")) or "（空）"
            attributes[key] = attributes.get(key, 0) + 1

    return {
        "count": total,
        "listTotal": F.to_int(snapshot.get("listTotal")),
        "versionHash": _snapshot_version(snapshot),
        "fetchedAt": F.text(snapshot.get("fetchedAt")),
        "source": F.text(snapshot.get("source")),
        "failures": len(snapshot.get("failures") or []),
        "hasTitle": ratio(lambda record: bool(F.text(record.get("title")))),
        "hasSummary": ratio(
            lambda record: bool(F.text((record.get("policyInfo") or {}).get("summary")))
        ),
        "hasIndustryField": ratio(
            lambda record: bool(
                F.text((record.get("policyInfo") or {}).get("industryField"))
            )
        ),
        "hasApplicableScope": ratio(
            lambda record: bool(
                F.text((record.get("policyInfo") or {}).get("applicableScopePath"))
            )
        ),
        "hasSupportMethods": ratio(lambda record: bool(record.get("supportMethods"))),
        "hasConditions": ratio(
            lambda record: bool(record.get("conditions") or record.get("structuredConditions"))
        ),
        "hasRuleTable": ratio(lambda record: bool(record.get("ruleTableJson"))),
        "hasKeywords": ratio(lambda record: bool(record.get("keywords"))),
        "hasMaterials": ratio(lambda record: bool(record.get("materials"))),
        "hasParent": ratio(
            lambda record: bool(
                (record.get("relatedPolicies") or {}).get("parent")
                or (record.get("relatedPolicies") or {}).get("directParents")
            )
        ),
        "hasChildren": ratio(
            lambda record: bool((record.get("relatedPolicies") or {}).get("children"))
        ),
        "hasUpdateTime": ratio(lambda record: bool(F.text(record.get("updateTime")))),
        "policyAttributes": dict(sorted(attributes.items(), key=lambda item: -item[1])),
    }
