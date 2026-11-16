# -*- coding: utf-8 -*-
"""T7 新条件匹配实时与批量统一编排器。"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import date
from hashlib import sha256
from threading import BoundedSemaphore, Lock
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from service import fields as F
from service.condition_scoring import (
    application_window,
    load_scoring_rules,
    score_policy_match,
    scoring_config_version,
)
from service.match_cache import MatchCache, get_match_cache
from service.match_policy_source import load_match_policy_packages
from service.match_prefilter import prefilter_policies
from service.policy_condition_package import load_condition_package_snapshot
from service.policy_match_model import (
    OUTPUT_SCHEMA_VERSION,
    PROMPT_VERSION,
    build_model_input,
    judge_policy,
)
from service.profile import build_profile
from service.paths import env_bool, env_int, env_str


ENGINE_NAME = "condition_llm"
_SEMAPHORE_LOCK = Lock()
_MODEL_SEMAPHORE: Optional[BoundedSemaphore] = None
_MODEL_SEMAPHORE_LIMIT: Optional[int] = None


def _canonical_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(raw.encode("utf-8")).hexdigest()


def _emit_progress(
    callback: Optional[Callable[[Dict[str, Any]], None]], event: str, **payload: Any,
) -> None:
    """进度回调仅用于观测，回调异常不得中断匹配主流程。"""
    if callback is None:
        return
    try:
        callback({"event": event, **payload})
    except Exception:
        return


def _policy_workers() -> int:
    return max(env_int("PEMATCHING_V2_POLICY_LLM_WORKERS", 6), 1)


def _global_model_semaphore() -> BoundedSemaphore:
    global _MODEL_SEMAPHORE, _MODEL_SEMAPHORE_LIMIT
    limit = _policy_workers()
    with _SEMAPHORE_LOCK:
        if _MODEL_SEMAPHORE is None or _MODEL_SEMAPHORE_LIMIT != limit:
            _MODEL_SEMAPHORE = BoundedSemaphore(limit)
            _MODEL_SEMAPHORE_LIMIT = limit
        return _MODEL_SEMAPHORE


def _profile_hash(package: Dict[str, Any], profile: Dict[str, Any], rules: Dict[str, Any]) -> str:
    model_input = build_model_input(package, profile, rules)
    return _canonical_hash({"profile": model_input["enterprise"]})


def _model_cache_key(package: Dict[str, Any], profile_hash: str, rules: Dict[str, Any]) -> str:
    return _canonical_hash({
        "enterpriseProfileHash": profile_hash,
        "policyContentHash": F.text(package.get("policyContentHash")),
        "modelName": env_str("V2_MODEL_NAME", "minimax2.7"),
        "promptVersion": PROMPT_VERSION,
        "scoringConfigVersion": scoring_config_version(rules),
    })


def _empty_model_output(package: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schemaVersion": OUTPUT_SCHEMA_VERSION,
        "sourceConflicts": [],
        "conditionResults": [],
        "discoveredConditions": [],
    }


def _has_any_policy_condition(package: Dict[str, Any]) -> bool:
    if any(
        F.text(condition.get("applicationRequirement"))
        for condition in package.get("conditions") or []
        if isinstance(condition, dict)
    ):
        return True
    table = package.get("ruleTableJson") or {}
    return isinstance(table, dict) and any(table.get(key) for key in (
        "hardRuleGroups", "softRuleGroups", "specialPassRuleGroups",
    ))


def _call_model(
    package: Dict[str, Any], profile: Dict[str, Any], rules: Dict[str, Any],
    judge: Callable[..., Dict[str, Any]],
) -> Tuple[Dict[str, Any], int]:
    semaphore = _global_model_semaphore()
    queued = time.monotonic()
    with semaphore:
        queue_wait_ms = int((time.monotonic() - queued) * 1000)
        return judge(package, profile, rules=rules), queue_wait_ms


def _model_result_for_candidate(
    package: Dict[str, Any], profile: Dict[str, Any], rules: Dict[str, Any], *,
    cache: MatchCache, cache_enabled: bool, judge: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    profile_hash = _profile_hash(package, profile, rules)
    cache_key = _model_cache_key(package, profile_hash, rules)
    if not cache_enabled:
        result, queue_wait_ms = _call_model(package, profile, rules, judge)
        return {
            "result": result, "cacheHit": False, "modelCallCount": 1 + int(result.get("retryCount") or 0),
            "queueWaitMs": queue_wait_ms, "enterpriseProfileHash": profile_hash,
        }

    while True:
        state, cached, event = cache.acquire(cache_key)
        if state == "hit":
            return {
                "result": cached, "cacheHit": True, "modelCallCount": 0, "queueWaitMs": 0,
                "enterpriseProfileHash": profile_hash,
            }
        if state == "wait":
            # 等待同一内容键的主调用结束，随后重新读取；超时后也重新竞争 owner。
            event.wait(timeout=max(env_int("PEMATCHING_V2_POLICY_LLM_TIMEOUT", 120) * 2, 1))
            continue
        try:
            result, queue_wait_ms = _call_model(package, profile, rules, judge)
            cache.complete(cache_key, result)
            return {
                "result": result, "cacheHit": False, "modelCallCount": 1 + int(result.get("retryCount") or 0),
                "queueWaitMs": queue_wait_ms, "enterpriseProfileHash": profile_hash,
            }
        except Exception:
            cache.fail(cache_key)
            raise


def _recommendation(scored: Dict[str, Any], package: Dict[str, Any], model_run: Dict[str, Any]) -> Dict[str, Any]:
    model = model_run["result"]
    return {
        "fusionPolicyId": scored["fusionPolicyId"],
        "policyTitle": scored["policyTitle"],
        "rank": 0,
        "finalScore": scored["finalScore"],
        "conditionScore": scored["verifiedConditionScore"],
        "timelinessScore": scored["timelinessScore"],
        "reason": scored["reason"],
        "applicationWindow": scored["applicationWindow"],
        "audit": {
            "conditionResults": scored["conditionResults"],
            "groupResults": scored["groupResults"],
            "sourceConflicts": scored["sourceConflicts"],
            "verifiedConditionScore": scored["verifiedConditionScore"],
            "policyContentHash": F.text(package.get("policyContentHash")),
            "enterpriseProfileHash": model_run["enterpriseProfileHash"],
            "modelName": F.text(model.get("modelName")) or env_str("V2_MODEL_NAME", "minimax2.7"),
            "promptVersion": F.text(model.get("promptVersion")) or PROMPT_VERSION,
            "scoreSource": "server_verified",
            "elapsedMs": 0 if model_run["cacheHit"] else int(model.get("elapsedMs") or 0),
            "cacheHit": model_run["cacheHit"],
            "retryCount": int(model.get("retryCount") or 0),
            "queueWaitMs": model_run["queueWaitMs"],
            "invalidConditionKeys": scored["invalidConditionKeys"],
            "invalidDiscoveredConditionCount": scored["invalidDiscoveredConditionCount"],
            "dataPending": scored["dataPending"],
        },
    }


def _sort_key(item: Dict[str, Any]) -> Tuple[int, float, str, str]:
    data_pending = bool((item.get("audit") or {}).get("dataPending"))
    score = item.get("finalScore")
    return (1 if data_pending else 0, -(float(score) if score is not None else -1.0), F.text(item.get("policyTitle")), F.text(item.get("fusionPolicyId")))


def _load_packages(policy_packages: Optional[Iterable[Dict[str, Any]]]) -> Tuple[List[Dict[str, Any]], str]:
    if policy_packages is not None:
        packages = list(policy_packages)
        return packages, _canonical_hash([F.text(item.get("policyContentHash")) for item in packages])
    snapshot = load_condition_package_snapshot()
    records = load_match_policy_packages()
    return list(records.values()), F.text(snapshot.get("versionHash"))


def run_condition_match(
    record: Dict[str, Any], *, today: Optional[date] = None, top_n: Optional[int] = None,
    policy_packages: Optional[Iterable[Dict[str, Any]]] = None,
    model_judge: Callable[..., Dict[str, Any]] = judge_policy,
    cache: Optional[MatchCache] = None,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """单企业实时入口。传入 ``policy_packages/model_judge`` 仅用于离线测试。"""
    started = time.monotonic()
    rules = load_scoring_rules()
    packages, policy_version = _load_packages(policy_packages)
    profile = build_profile(record, today=today)
    prefiltered = prefilter_policies(profile, packages, today=today)
    cache_enabled = env_bool("PEMATCHING_V2_MATCH_CACHE_ENABLED", True)
    effective_cache = cache or get_match_cache(int(rules["maximumCacheEntries"]))

    meta: Dict[str, Any] = {
        "engine": ENGINE_NAME,
        "policyVersion": policy_version,
        "policyCount": len(packages),
        "candidateCount": len(prefiltered["candidates"]),
        "excludedCount": len(prefiltered["excluded"]),
        "modelCallCount": 0,
        "cacheHitCount": 0,
        "failedCount": 0,
        "modelRetryCount": 0,
        "modelQueueWaitMs": 0,
        "scoringConfigVersion": scoring_config_version(rules),
        "globalExclusion": prefiltered["globalExclusion"],
        "prefilterExclusions": prefiltered["excluded"],
        "failures": [],
    }
    enterprise_name = F.text(profile.get("enterpriseName"))
    _emit_progress(
        progress_callback,
        "enterprise_started",
        enterpriseName=enterprise_name,
        policyCount=len(packages),
        candidateCount=len(prefiltered["candidates"]),
        prefilterExcludedCount=len(prefiltered["excluded"]),
        policyWorkers=min(_policy_workers(), max(len(prefiltered["candidates"]), 1)),
    )
    if prefiltered["globalExclusion"]:
        meta["elapsedMs"] = int((time.monotonic() - started) * 1000)
        meta["allRecommendationCount"] = 0
        meta["returnedCount"] = 0
        _emit_progress(
            progress_callback,
            "enterprise_completed",
            enterpriseName=enterprise_name,
            elapsedMs=meta["elapsedMs"],
            recommendationCount=0,
            failedCount=0,
            modelCallCount=0,
            cacheHitCount=0,
            globalExcluded=True,
        )
        return {"data": {"enterpriseName": enterprise_name, "recommendations": []}, "meta": meta}

    scored_results: List[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]] = []
    failures: List[Dict[str, Any]] = []
    candidates = prefiltered["candidates"]

    def execute(package: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        if not _has_any_policy_condition(package):
            profile_hash = _profile_hash(package, profile, rules)
            model_run = {
                "result": {"output": _empty_model_output(package), "modelName": env_str("V2_MODEL_NAME", "minimax2.7"), "promptVersion": PROMPT_VERSION, "retryCount": 0, "elapsedMs": 0},
                "cacheHit": False, "modelCallCount": 0, "queueWaitMs": 0, "enterpriseProfileHash": profile_hash,
            }
        else:
            model_run = _model_result_for_candidate(
                package, profile, rules, cache=effective_cache, cache_enabled=cache_enabled, judge=model_judge,
            )
        scored = score_policy_match(package, profile, model_run["result"]["output"], today=today, rules=rules)
        return scored, package, model_run

    workers = min(_policy_workers(), max(len(candidates), 1))
    completed_count = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {executor.submit(execute, package): package for package in candidates}
        for future in as_completed(future_map):
            package = future_map[future]
            completed_count += 1
            try:
                scored, current_package, model_run = future.result()
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                failures.append({
                    "fusionPolicyId": F.text(package.get("fusionPolicyId")),
                    "error": error,
                })
                _emit_progress(
                    progress_callback,
                    "policy_completed",
                    enterpriseName=enterprise_name,
                    completed=completed_count,
                    total=len(candidates),
                    fusionPolicyId=F.text(package.get("fusionPolicyId")),
                    policyTitle=F.text(package.get("title")),
                    outcome="failed",
                    error=error,
                )
                continue
            meta["modelCallCount"] += model_run["modelCallCount"]
            meta["cacheHitCount"] += int(model_run["cacheHit"])
            meta["modelRetryCount"] += int(model_run["result"].get("retryCount") or 0)
            meta["modelQueueWaitMs"] += int(model_run["queueWaitMs"])
            if scored["directExclusion"]:
                outcome = "direct_exclusion"
                meta["excludedCount"] += 1
                meta["prefilterExclusions"].append({
                    "fusionPolicyId": scored["fusionPolicyId"], "reason": scored["directExclusion"],
                })
            elif scored["recommendable"]:
                outcome = "recommended"
                scored_results.append((scored, current_package, model_run))
            else:
                outcome = "no_effective_match"
                meta["excludedCount"] += 1
                meta["prefilterExclusions"].append({
                    "fusionPolicyId": scored["fusionPolicyId"],
                    "reason": {"code": "NO_EFFECTIVE_MATCH", "message": "没有可验证且分数大于 0 的政策条件。", "evidence": []},
                })
            model = model_run["result"]
            _emit_progress(
                progress_callback,
                "policy_completed",
                enterpriseName=enterprise_name,
                completed=completed_count,
                total=len(candidates),
                fusionPolicyId=scored["fusionPolicyId"],
                policyTitle=scored["policyTitle"],
                outcome=outcome,
                finalScore=scored["finalScore"],
                modelElapsedMs=0 if model_run["cacheHit"] else int(model.get("elapsedMs") or 0),
                queueWaitMs=int(model_run["queueWaitMs"]),
                cacheHit=bool(model_run["cacheHit"]),
                retryCount=int(model.get("retryCount") or 0),
            )

    meta["failedCount"] = len(failures)
    meta["failures"] = failures
    recommendations = [_recommendation(scored, package, model_run) for scored, package, model_run in scored_results]
    recommendations.sort(key=_sort_key)
    for rank, item in enumerate(recommendations, start=1):
        item["rank"] = rank
    meta["allRecommendationCount"] = len(recommendations)
    if top_n is not None:
        recommendations = recommendations[:max(top_n, 0)]
    meta["returnedCount"] = len(recommendations)
    meta["elapsedMs"] = int((time.monotonic() - started) * 1000)
    _emit_progress(
        progress_callback,
        "enterprise_completed",
        enterpriseName=enterprise_name,
        elapsedMs=meta["elapsedMs"],
        recommendationCount=meta["allRecommendationCount"],
        returnedCount=meta["returnedCount"],
        failedCount=meta["failedCount"],
        modelCallCount=meta["modelCallCount"],
        cacheHitCount=meta["cacheHitCount"],
        globalExcluded=False,
    )
    return {
        "data": {"enterpriseName": enterprise_name, "recommendations": recommendations},
        "meta": meta,
    }


def run_condition_match_batch(
    records: Iterable[Dict[str, Any]], *, today: Optional[date] = None, top_n: Optional[int] = None,
    policy_packages: Optional[Iterable[Dict[str, Any]]] = None,
    model_judge: Callable[..., Dict[str, Any]] = judge_policy,
    workers: Optional[int] = None,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """批量入口。企业失败隔离，输出顺序严格保持输入顺序。"""
    indexed = list(enumerate(records))
    results: List[Optional[Dict[str, Any]]] = [None] * len(indexed)
    enterprise_workers = max(workers or env_int("V2_MAX_WORKERS", 4), 1)
    # 物化一次，避免生成器在每家企业重新读取且确保实时与批量使用同一政策版本。
    packages = list(policy_packages) if policy_packages is not None else None

    def execute(item: Tuple[int, Dict[str, Any]]) -> Tuple[int, Dict[str, Any]]:
        index, enterprise = item
        def forward_progress(event: Dict[str, Any]) -> None:
            _emit_progress(
                progress_callback,
                event["event"],
                enterpriseIndex=index + 1,
                enterpriseTotal=len(indexed),
                **{key: value for key, value in event.items() if key != "event"},
            )

        try:
            result = run_condition_match(
                enterprise, today=today, top_n=top_n, policy_packages=packages, model_judge=model_judge,
                progress_callback=forward_progress,
            )
            return index, {"index": index, "enterpriseName": F.text(enterprise.get("enterpriseName")), "success": True, **result}
        except Exception as exc:
            _emit_progress(
                progress_callback,
                "enterprise_failed",
                enterpriseIndex=index + 1,
                enterpriseTotal=len(indexed),
                enterpriseName=F.text(enterprise.get("enterpriseName")),
                error=f"{type(exc).__name__}: {exc}",
            )
            return index, {
                "index": index, "enterpriseName": F.text(enterprise.get("enterpriseName")), "success": False,
                "error": f"{type(exc).__name__}: {exc}",
            }

    with ThreadPoolExecutor(max_workers=min(enterprise_workers, max(len(indexed), 1))) as executor:
        for future in as_completed([executor.submit(execute, item) for item in indexed]):
            index, value = future.result()
            results[index] = value
    final = [item for item in results if item is not None]
    return {
        "data": {"results": final},
        "meta": {
            "engine": ENGINE_NAME,
            "total": len(final),
            "succeeded": sum(1 for item in final if item["success"]),
            "failed": sum(1 for item in final if not item["success"]),
        },
    }


def condition_match_health() -> Dict[str, Any]:
    rules = load_scoring_rules()
    return {
        "engine": ENGINE_NAME,
        "policyLlmWorkers": _policy_workers(),
        "policyLlmTimeout": env_int("PEMATCHING_V2_POLICY_LLM_TIMEOUT", env_int("V2_LLM_TIMEOUT", 120)),
        "cacheEnabled": env_bool("PEMATCHING_V2_MATCH_CACHE_ENABLED", True),
        "modelArchiveEnabled": env_bool("PEMATCHING_V2_MODEL_ARCHIVE_ENABLED", False),
        "cache": get_match_cache(int(rules["maximumCacheEntries"])).stats(),
        "scoringConfigVersion": scoring_config_version(rules),
    }
