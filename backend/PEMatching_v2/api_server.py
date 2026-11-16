# -*- coding: utf-8 -*-
"""PEMatching_v2 独立 HTTP 服务。

默认端口 8020，与老链路 8010 并存互不影响。技术栈与老链路一致：
标准库 ThreadingHTTPServer + BaseHTTPRequestHandler，路由用 if 链。

四个 endpoint：
    GET  /health                        健康检查与配置回显
    POST /api/v2/tagmatch/enterprise    单企业匹配
    POST /api/v2/tagmatch/batch         批量匹配
    GET  /api/v2/policy-tags            政策标签与分布，排查用
    POST /api/v2/eligibility/evaluate   独立资格门禁评分

安全提示：本服务无鉴权，仅供内网调用。若需暴露到公网，必须在网关层加认证。
"""

from __future__ import annotations

import json
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from service import fields as F
from service import policy_index as pi
from service.agent import MODE_AGENT, MODE_DETERMINISTIC, run_match
from service.eligibility.archive import archive_run, archiveable_profile
from service.eligibility.gate_service import evaluate_policies
from service.eligibility.sync import (
    EligibilitySyncError,
    reset_eligibility_cache,
    sync_from_policy_snapshot,
)
from service.event_log import RequestEventLog, new_request_id
from service.match_orchestrator import (
    ENGINE_NAME as CONDITION_LLM_ENGINE,
    condition_match_health,
    run_condition_match,
    run_condition_match_batch,
)
from service.match_policy_source import match_policy_source_stats
from service.policy_condition_package import (
    PolicyConditionPackageError,
    build_condition_package_snapshot,
    condition_package_stats,
    write_condition_package_snapshot,
)
from service.policy_sync import (
    get_sync_status,
    load_snapshot,
    synchronize_on_startup,
)
from service.profile import build_profile
from service.paths import (
    POLICY_SNAPSHOT_PATH,
    POLICY_CONDITION_SNAPSHOT_PATH,
    POLICY_TAGS_PATH,
    ELIGIBILITY_MANIFEST_PATH,
    ELIGIBILITY_PROFILES_PATH,
    env_bool,
    env_int,
    env_str,
)

VALID_MODES = (MODE_AGENT, MODE_DETERMINISTIC)
VALID_ENGINES = ("legacy", CONDITION_LLM_ENGINE)


class ApiError(Exception):
    """带状态码的业务异常。"""

    def __init__(self, message: str, status: int = 400, detail: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail


# --------------------------------------------------------------------------
# 响应与请求工具
# --------------------------------------------------------------------------


def _json_response(
    handler: BaseHTTPRequestHandler,
    payload: Dict[str, Any],
    status: int = 200,
) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.end_headers()
    handler.wfile.write(body)


def _ok(handler, data: Any, meta: Optional[Dict[str, Any]] = None) -> None:
    _json_response(handler, {
        "success": True, "data": data, "meta": meta or {}, "message": "",
    })


def _fail(handler, message: str, status: int = 400, detail: Any = None) -> None:
    _json_response(handler, {
        "success": False, "data": None,
        "meta": {"detail": detail} if detail is not None else {},
        "message": message,
    }, status=status)


def _read_json_body(handler: BaseHTTPRequestHandler) -> Any:
    length = F.to_int(handler.headers.get("Content-Length")) or 0
    if length <= 0:
        raise ApiError("请求体为空")
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ApiError(f"请求体不是合法 JSON：{exc}") from exc


def _parse_query(path: str) -> Dict[str, str]:
    parsed = urlparse(path)
    return {key: values[0] for key, values in parse_qs(parsed.query).items() if values}


def _parse_options(query: Dict[str, str]) -> Dict[str, Any]:
    """解析并校验查询参数。非法值给明确提示，不静默兜底。"""
    mode = F.text(query.get("mode")) or MODE_AGENT
    if mode not in VALID_MODES:
        raise ApiError(f"mode 非法：{mode!r}，合法值 {list(VALID_MODES)}")

    top_n_raw = query.get("topN")
    top_n = env_int("V2_DEFAULT_TOP_N", 20)
    if top_n_raw is not None:
        parsed = F.to_int(top_n_raw)
        if parsed is None or parsed <= 0:
            raise ApiError(f"topN 必须是正整数，实际 {top_n_raw!r}")
        top_n = min(parsed, 100)

    stage2_raw = F.text(query.get("enableStage2")).lower()
    if stage2_raw and stage2_raw not in ("true", "false", "1", "0", "yes", "no"):
        raise ApiError(f"enableStage2 必须是 true/false，实际 {query.get('enableStage2')!r}")
    enable_stage2 = stage2_raw in ("true", "1", "yes")

    today_raw = F.text(query.get("today"))
    today = None
    if today_raw:
        try:
            today = date.fromisoformat(today_raw)
        except ValueError as exc:
            raise ApiError(f"today 必须是 YYYY-MM-DD，实际 {today_raw!r}") from exc

    llm_raw = F.text(query.get("useLlmEvidence")).lower()
    if llm_raw and llm_raw not in ("true", "false", "1", "0", "yes", "no"):
        raise ApiError(f"useLlmEvidence 必须是 true/false，实际 {query.get('useLlmEvidence')!r}")

    return {
        "mode": mode,
        "keyword": F.text(query.get("keyword")),
        "enable_stage2": enable_stage2,
        "top_n": top_n,
        "top_n_explicit": top_n_raw is not None,
        "today": today,
        # 未显式传 useLlmEvidence 时跟随环境 PEMATCHING_V2_ELIGIBILITY_LLM_ENABLED（当前 .env 为开启）
        "use_llm_evidence": None if not llm_raw else llm_raw in ("true", "1", "yes"),
    }


def _parse_condition_options(query: Dict[str, str]) -> Dict[str, Any]:
    """新条件匹配接口只接受最终分页和测试日期，不泄漏旧 Agent 参数。"""
    top_n = None
    if "topN" in query:
        parsed = F.to_int(query.get("topN"))
        if parsed is None or parsed <= 0:
            raise ApiError(f"topN 必须是正整数，实际 {query.get('topN')!r}")
        top_n = min(parsed, 100)
    today = None
    if F.text(query.get("today")):
        try:
            today = date.fromisoformat(F.text(query.get("today")))
        except ValueError as exc:
            raise ApiError(f"today 必须是 YYYY-MM-DD，实际 {query.get('today')!r}") from exc
    return {"top_n": top_n, "today": today}


def _active_engine() -> str:
    engine = env_str("PEMATCHING_V2_ENGINE", CONDITION_LLM_ENGINE)
    if engine not in VALID_ENGINES:
        raise ApiError(f"PEMATCHING_V2_ENGINE 非法：{engine!r}，合法值 {list(VALID_ENGINES)}", status=500)
    return engine


def _validate_record(record: Any, index: Optional[int] = None) -> Dict[str, Any]:
    prefix = f"第 {index + 1} 条企业" if index is not None else "企业"
    if not isinstance(record, dict):
        raise ApiError(f"{prefix}数据必须是对象，实际 {type(record).__name__}")
    if not F.text(record.get("enterpriseName")):
        raise ApiError(f"{prefix}数据缺少 enterpriseName")
    return record


# --------------------------------------------------------------------------
# 业务处理
# --------------------------------------------------------------------------


def handle_health() -> Dict[str, Any]:
    def file_info(path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {"exists": False}
        stat = path.stat()
        return {
            "exists": True,
            "sizeKb": round(stat.st_size / 1024, 1),
            "modifiedAt": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        }

    payload: Dict[str, Any] = {
        "service": "PEMatching_v2",
        "status": "ok",
        "host": env_str("V2_HOST", "0.0.0.0"),
        "port": env_int("V2_PORT", 8020),
        "model": env_str("V2_MODEL_NAME", "minimax2.7"),
        "config": {
            "engine": _active_engine(),
            "maxWorkers": env_int("V2_MAX_WORKERS", 4),
            "llmTimeout": env_int("V2_LLM_TIMEOUT", 240),
            "agentMaxTurns": env_int("V2_AGENT_MAX_TURNS", 12),
            "batchMaxSize": env_int("V2_BATCH_MAX_SIZE", 50),
            "defaultTopN": env_int("V2_DEFAULT_TOP_N", 20),
            "eligibilityEnabled": env_bool("PEMATCHING_V2_ELIGIBILITY_ENABLED", False),
            "eligibilityLlmEnabled": env_bool("PEMATCHING_V2_ELIGIBILITY_LLM_ENABLED", False),
            "eligibilityMaxPolicies": env_int("PEMATCHING_V2_ELIGIBILITY_MAX_POLICIES", 20),
            "legacyTaggingOnStartup": env_bool(
                "PEMATCHING_V2_LEGACY_TAGGING_ON_STARTUP", False
            ),
            "modelArchiveEnabled": env_bool("PEMATCHING_V2_MODEL_ARCHIVE_ENABLED", False),
        },
        "policySnapshot": file_info(POLICY_SNAPSHOT_PATH),
        "policyConditionSnapshot": file_info(POLICY_CONDITION_SNAPSHOT_PATH),
        "policySync": get_sync_status(),
        "policyTags": file_info(POLICY_TAGS_PATH),
        "eligibilityManifest": file_info(ELIGIBILITY_MANIFEST_PATH),
        "eligibilityProfiles": file_info(ELIGIBILITY_PROFILES_PATH),
    }
    if payload["policySync"]["status"] == "degraded":
        payload["status"] = "degraded"
    elif payload["policySync"]["status"] == "unavailable":
        payload["status"] = "unavailable"
    try:
        payload["matchPolicySource"] = match_policy_source_stats()
    except (FileNotFoundError, ValueError) as exc:
        if payload["status"] == "ok":
            payload["status"] = "degraded"
        payload["matchPolicySource"] = {
            "status": "unavailable",
            "error": f"{type(exc).__name__}: {exc}",
        }
    payload["conditionMatch"] = condition_match_health()

    # 旧索引只在旧接口收到请求时按需加载，健康检查不进入旧打标校验代码。
    payload["policyIndex"] = {
        "status": "legacy_on_demand",
        "available": POLICY_TAGS_PATH.exists(),
        "loaded": False,
    }
    return payload


def handle_enterprise(record: Dict[str, Any], options: Dict[str, Any]) -> Dict[str, Any]:
    log = RequestEventLog()
    log.write("request", {
        "endpoint": "enterprise",
        "enterpriseName": F.text(record.get("enterpriseName")),
        "options": {k: (v.isoformat() if isinstance(v, date) else v)
                    for k, v in options.items()},
    })
    if _active_engine() == CONDITION_LLM_ENGINE:
        result = run_condition_match(
            record,
            today=options.get("today"),
            top_n=options.get("top_n") if options.get("top_n_explicit") else None,
        )
    else:
        legacy_options = {key: value for key, value in options.items() if key != "top_n_explicit"}
        result = run_match(record, event_log=log, **legacy_options)
    return result


def handle_batch(records: List[Any], options: Dict[str, Any]) -> Dict[str, Any]:
    max_size = env_int("V2_BATCH_MAX_SIZE", 50)
    if len(records) > max_size:
        raise ApiError(f"批量上限 {max_size} 条，实际 {len(records)} 条。"
                       f"更大批量请用 run_tagmatch_batch.py")

    # 先校验全部记录，避免跑到一半才发现格式问题
    validated: List[Tuple[int, Dict[str, Any]]] = []
    for index, record in enumerate(records):
        validated.append((index, _validate_record(record, index)))

    if _active_engine() == CONDITION_LLM_ENGINE:
        result = run_condition_match_batch(
            [record for _, record in validated],
            today=options.get("today"),
            top_n=options.get("top_n") if options.get("top_n_explicit") else None,
        )
        return result

    workers = min(env_int("V2_MAX_WORKERS", 4), max(len(validated), 1))
    results: List[Optional[Dict[str, Any]]] = [None] * len(validated)

    def work(item: Tuple[int, Dict[str, Any]]) -> None:
        index, record = item
        log = RequestEventLog()
        try:
            legacy_options = {key: value for key, value in options.items() if key != "top_n_explicit"}
            outcome = run_match(record, event_log=log, **legacy_options)
            results[index] = {
                "index": index,
                "enterpriseName": F.text(record.get("enterpriseName")),
                "success": True,
                "data": outcome["data"],
                "meta": outcome["meta"],
            }
        except Exception as exc:  # 单个失败不影响其余
            results[index] = {
                "index": index,
                "enterpriseName": F.text(record.get("enterpriseName")),
                "success": False,
                "error": f"{type(exc).__name__}: {exc}",
                "meta": {"requestId": log.request_id},
            }

    with ThreadPoolExecutor(max_workers=workers) as executor:
        list(executor.map(work, validated))

    items = [item for item in results if item is not None]
    succeeded = sum(1 for item in items if item["success"])
    return {
        "data": {"results": items},
        "meta": {
            "total": len(items),
            "succeeded": succeeded,
            "failed": len(items) - succeeded,
            "mode": options["mode"],
        },
    }


def handle_condition_enterprise(record: Dict[str, Any], options: Dict[str, Any]) -> Dict[str, Any]:
    return run_condition_match(record, today=options.get("today"), top_n=options.get("top_n"))


def handle_condition_batch(records: List[Any], options: Dict[str, Any]) -> Dict[str, Any]:
    max_size = env_int("V2_BATCH_MAX_SIZE", 50)
    if len(records) > max_size:
        raise ApiError(f"批量上限 {max_size} 条，实际 {len(records)} 条。更大批量请用 run_condition_match_batch.py")
    validated = [_validate_record(record, index) for index, record in enumerate(records)]
    return run_condition_match_batch(validated, today=options.get("today"), top_n=options.get("top_n"))


def handle_policy_tags(query: Dict[str, str]) -> Dict[str, Any]:
    try:
        stats = pi.index_stats()
        records = pi.load_policy_tags()
    except pi.PolicyIndexError as exc:
        raise ApiError(str(exc), status=400) from exc

    include_not_applicable = F.text(query.get("includeNotApplicable")).lower() in (
        "true", "1", "yes"
    )
    limit = F.to_int(query.get("limit")) or 200
    policy_id = F.text(query.get("fusionPolicyId"))

    if policy_id:
        detail = pi.get_policy_summary(policy_id)
        if detail is None:
            raise ApiError(f"政策不存在或未打标：{policy_id}", status=404)
        return {"stats": stats, "policies": [detail]}

    policies: List[Dict[str, Any]] = []
    for pid, record in sorted(records.items()):
        if not include_not_applicable and not pi.is_applicable(record):
            continue
        tags = record.get("tags") or {}
        policies.append({
            "fusionPolicyId": pid,
            "title": F.text(record.get("title")),
            "A": (tags.get("A") or {}).get("primary"),
            "B": (tags.get("B") or {}).get("values"),
            "C": (tags.get("C") or {}).get("values"),
            "cSub": (tags.get("C") or {}).get("subValues"),
            "D": (tags.get("D") or {}).get("value"),
            "applicable": (record.get("applicability") or {}).get("applicable"),
            "audience": (record.get("applicability") or {}).get("audience"),
            "applicationWindow": record.get("applicationWindow") or {},
            "source": F.text(record.get("_source")) or "llm",
        })
        if len(policies) >= limit:
            break

    return {"stats": stats, "policies": policies}


def handle_eligibility(body: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """资格接口只接收指定政策 ID，不触发 V2 召回、评分或排序。"""
    if not env_bool("PEMATCHING_V2_ELIGIBILITY_ENABLED", False):
        raise ApiError("资格门禁接口未启用；请设置 PEMATCHING_V2_ELIGIBILITY_ENABLED=true", status=404)
    if not isinstance(body, dict):
        raise ApiError("请求体必须是对象")
    enterprise = body.get("enterprise")
    if not isinstance(enterprise, dict):
        raise ApiError("enterprise 必须是企业对象")
    policy_ids = body.get("fusionPolicyIds")
    if not isinstance(policy_ids, list) or not policy_ids:
        raise ApiError("fusionPolicyIds 必须是非空数组")
    max_policies = env_int("PEMATCHING_V2_ELIGIBILITY_MAX_POLICIES", 20)
    if len(policy_ids) > max_policies:
        raise ApiError(f"单次资格评估上限 {max_policies} 条，实际 {len(policy_ids)} 条")
    normalised_ids = [F.text(policy_id) for policy_id in policy_ids]
    if any(not policy_id for policy_id in normalised_ids):
        raise ApiError("fusionPolicyIds 不能包含空值")
    # 调用方可传已构建的 V2 profile；普通接口输入则复用纯函数构建，不读取 V1。
    profile = enterprise if isinstance(enterprise.get("metrics"), dict) else build_profile(enterprise)
    include_details = body.get("includeConditionDetails", True) is not False
    use_llm_evidence = body.get("useLlmEvidence")
    if use_llm_evidence is not None and not isinstance(use_llm_evidence, bool):
        raise ApiError("useLlmEvidence 必须是布尔值")
    run_id = new_request_id()
    # 归档必须保留全量证据；对外是否展开明细只影响响应载荷。
    result = evaluate_policies(
        profile,
        normalised_ids,
        include_condition_details=True,
        use_llm_evidence=use_llm_evidence,
    )
    archive_enabled = env_bool("PEMATCHING_V2_ELIGIBILITY_ARCHIVE_ENABLED", True)
    if archive_enabled:
        archive_run(run_id, {"requestId": F.text(body.get("requestId")), "enterprise": archiveable_profile(profile), "fusionPolicyIds": normalised_ids, "result": result})
    response_evaluations = deepcopy(result["eligibilityEvaluations"])
    if not include_details:
        for evaluation in response_evaluations:
            evaluation["conditionDetails"] = []
            evaluation["ruleGroupDetails"] = []
    data = {
        "enterprise": {"enterpriseName": F.text(profile.get("enterpriseName")), "creditCode": F.text(profile.get("creditCode"))},
        "eligibilityEvaluations": response_evaluations,
    }
    meta = {
        "runId": run_id,
        "eligibilitySchemaVersion": result["eligibilitySchemaVersion"],
        "ruleManifestHash": result["ruleManifestHash"],
        "archiveEnabled": archive_enabled,
        "useLlmEvidence": use_llm_evidence,
    }
    return data, meta


# --------------------------------------------------------------------------
# Handler
# --------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "PEMatchingV2/1.0"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        print(f"[V2] {self.address_string()} {format % args}")

    def do_OPTIONS(self) -> None:  # noqa: N802
        _json_response(self, {"success": True, "data": None, "meta": {}, "message": ""})

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = _parse_query(self.path)
        try:
            if parsed.path == "/health":
                _ok(self, handle_health())
                return
            if parsed.path == "/api/v2/policy-tags":
                payload = handle_policy_tags(query)
                _ok(self, payload["policies"], {"stats": payload["stats"]})
                return
            _fail(self, f"未知路径 {parsed.path}", status=404)
        except ApiError as exc:
            _fail(self, str(exc), status=exc.status, detail=exc.detail)
        except Exception as exc:
            traceback.print_exc()
            _fail(self, f"服务内部错误：{type(exc).__name__}: {exc}", status=500)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = _parse_query(self.path)
        try:
            if parsed.path == "/api/v2/tagmatch/enterprise":
                options = _parse_options(query)
                body = _read_json_body(self)
                # 兼容两种传法：直接传企业对象，或包在 enterprise 字段里
                record = body.get("enterprise") if (
                    isinstance(body, dict) and "enterprise" in body
                    and isinstance(body.get("enterprise"), dict)
                ) else body
                record = _validate_record(record)
                result = handle_enterprise(record, options)
                _ok(self, result["data"], result["meta"])
                return

            if parsed.path == "/api/v2/condition-match/enterprise":
                options = _parse_condition_options(query)
                body = _read_json_body(self)
                record = body.get("enterprise") if (
                    isinstance(body, dict) and isinstance(body.get("enterprise"), dict)
                ) else body
                result = handle_condition_enterprise(_validate_record(record), options)
                _ok(self, result["data"], result["meta"])
                return

            if parsed.path == "/api/v2/tagmatch/batch":
                options = _parse_options(query)
                body = _read_json_body(self)
                records = body.get("enterprises") if isinstance(body, dict) else body
                if not isinstance(records, list) or not records:
                    raise ApiError("请求体必须是非空企业数组，或含 enterprises 数组的对象")
                result = handle_batch(records, options)
                _ok(self, result["data"], result["meta"])
                return

            if parsed.path == "/api/v2/condition-match/batch":
                options = _parse_condition_options(query)
                body = _read_json_body(self)
                records = body.get("enterprises") if isinstance(body, dict) else body
                if not isinstance(records, list) or not records:
                    raise ApiError("请求体必须是非空企业数组，或含 enterprises 数组的对象")
                result = handle_condition_batch(records, options)
                _ok(self, result["data"], result["meta"])
                return

            if parsed.path == "/api/v2/eligibility/evaluate":
                content_length = F.to_int(self.headers.get("Content-Length")) or 0
                if content_length > env_int("PEMATCHING_V2_ELIGIBILITY_MAX_BODY_BYTES", 262144):
                    raise ApiError("资格评估请求体超过大小上限", status=413)
                body = _read_json_body(self)
                data, meta = handle_eligibility(body)
                _ok(self, data, meta)
                return

            _fail(self, f"未知路径 {parsed.path}", status=404)
        except ApiError as exc:
            _fail(self, str(exc), status=exc.status, detail=exc.detail)
        except (ValueError, FileNotFoundError) as exc:
            _fail(self, str(exc), status=400)
        except Exception as exc:
            traceback.print_exc()
            _fail(self, f"服务内部错误：{type(exc).__name__}: {exc}", status=500)


def _startup_sync_policies() -> bool:
    """同步一次审核政策；旧快照降级可启动，无快照失败则阻止就绪。"""
    state = synchronize_on_startup()
    if state["status"] == "ready":
        print(
            f"[Startup] 政策快照已同步：{state['policyCount']} 条，"
            f"版本 {state['version'][:12]}"
        )
    elif state["status"] == "degraded":
        print(
            f"[Startup] 政策同步失败，使用旧快照 {state['policyCount']} 条："
            f"{state['degradedReason']}"
        )
    else:
        print(f"[Startup] 政策不可用，服务不进入就绪状态：{state['degradedReason']}")
    if state["ready"]:
        pi.reset_cache()
    return bool(state["ready"])


def _startup_build_policy_condition_packages() -> bool:
    """从当前政策快照确定性生成条件包，不调用模型。"""
    try:
        snapshot = build_condition_package_snapshot(load_snapshot())
        write_condition_package_snapshot(snapshot)
    except (FileNotFoundError, PolicyConditionPackageError, ValueError) as exc:
        print(f"[Startup] 政策条件包生成失败：{type(exc).__name__}: {exc}")
        return False
    stats = condition_package_stats(snapshot)
    print(
        f"[Startup] 政策条件包已生成：{stats['count']} 条，"
        f"规则表空 {stats['ruleTableEmpty']}，"
        f"疑似 OR 冲突 {stats['suspectedOrOperatorMismatch']}"
    )
    return True


def _startup_sync_eligibility() -> None:
    """启动时同步资格规则快照（审核通过的主政策）。

    与 V1 启动时生成主政策申报条件快照对应：V1 拉取主政策、本服务把同源
    结构 API 的 ruleTableJson 直接落为 V2 可执行资格快照，不依赖老目录文件。
    同步成功后清掉内存缓存，让 /api/v2/eligibility/evaluate 立即用最新规则。
    """
    if not env_bool("PEMATCHING_V2_ELIGIBILITY_SYNC_ON_STARTUP", True):
        print("[Startup] 已关闭资格快照启动同步（PEMATCHING_V2_ELIGIBILITY_SYNC_ON_STARTUP=false）")
        return
    try:
        manifest = sync_from_policy_snapshot(load_snapshot())
        print(f"[Startup] 资格快照已同步：{manifest.get('profileCount')} 条政策"
              f"（可执行 {manifest.get('executableProfileCount')}，空 {manifest.get('emptyProfileCount')}，"
              f"无效 {manifest.get('invalidProfileCount')}）")
        reset_eligibility_cache()
    except (EligibilitySyncError, Exception) as exc:
        print(f"[Startup] 资格快照同步失败（使用现有快照）：{type(exc).__name__}: {exc}")


def _startup_sync_legacy_policy_tags() -> None:
    """为显式启用的旧链路后台增量打标。

    新链路禁止调用本函数。旧链路灰度或回滚时可通过专用开关启用；后台线程
    不阻塞服务启动，指纹未变的历史记录自动跳过。
    """
    if not env_bool("PEMATCHING_V2_LEGACY_TAGGING_ON_STARTUP", False):
        return

    def _run() -> None:
        try:
            from run_policy_tagging import (
                _fingerprint,
                _load_existing,
                _select_policies,
                _write_tags,
            )
            from service.policy_sync import load_snapshot
            from service.tagging import TaggingError, tag_policy
            from service.paths import env_int as _env_int
            from concurrent.futures import ThreadPoolExecutor, as_completed
            from datetime import datetime
            from service import fields as F

            snapshot = load_snapshot()
            policies = [p for p in (snapshot.get("data") or []) if isinstance(p, dict)]
            existing = _load_existing()
            pending, _ = _select_policies(policies, existing, force=False, ids=None, limit=None)
            if not pending:
                print("[Tagging] 无新政策需打标")
                return
            print(f"[Tagging] 后台打标 {len(pending)} 条新政策（指纹未变的历史标签跳过）...")
            workers = _env_int("V2_MAX_WORKERS", 4)
            records = dict(existing)
            failures: list = []
            done = 0
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(tag_policy, policy): policy for policy in pending}
                for future in as_completed(futures):
                    policy = futures[future]
                    policy_id = F.text(policy.get("fusionPolicyId"))
                    done += 1
                    try:
                        result = future.result()
                    except (TaggingError, Exception) as exc:
                        failures.append({
                            "fusionPolicyId": policy_id,
                            "title": F.text(policy.get("title")),
                            "error": str(exc)[:800],
                        })
                        continue
                    result["_fingerprint"] = _fingerprint(policy)
                    result["_taggedAt"] = datetime.now().isoformat(timespec="seconds")
                    records[policy_id] = result
            _write_tags(records, failures)
            from service import policy_index as _pi
            _pi.reset_cache()
            print(f"[Tagging] 后台打标完成：成功 {done - len(failures)}　失败 {len(failures)}")
        except Exception as exc:
            print(f"[Tagging] 后台打标失败：{type(exc).__name__}: {exc}")

    t = threading.Thread(target=_run, name="legacy-startup-policy-tagging", daemon=True)
    t.start()


def main() -> int:
    host = env_str("V2_HOST", "0.0.0.0")
    port = env_int("V2_PORT", 8020)

    print(f"PEMatching_v2 启动于 http://{host}:{port}")
    print(f"  模型 {env_str('V2_MODEL_NAME', 'minimax2.7')}　"
          f"并发 {env_int('V2_MAX_WORKERS', 4)}　"
          f"Agent 轮次上限 {env_int('V2_AGENT_MAX_TURNS', 12)}")
    if not _startup_sync_policies():
        return 1
    if not _startup_build_policy_condition_packages():
        return 1

    try:
        source_stats = match_policy_source_stats()
        print(
            f"  新匹配政策源 {source_stats['count']} 条，"
            f"版本 {source_stats['versionHash'][:12]}"
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"  新匹配政策源不可用：{type(exc).__name__}: {exc}")
        return 1

    print("  旧标签接口保留，标签索引仅在旧接口请求时按需加载")
    print("  注意：本服务无鉴权，仅供内网调用")
    print()
    print("  GET  /health")
    print("  POST /api/v2/tagmatch/enterprise?mode=agent|deterministic&keyword=&topN=&enableStage2=")
    print("  POST /api/v2/tagmatch/batch")
    print("  POST /api/v2/condition-match/enterprise?topN=&today=")
    print("  POST /api/v2/condition-match/batch?topN=&today=")
    print("  POST /api/v2/eligibility/evaluate (需 PEMATCHING_V2_ELIGIBILITY_ENABLED=true)")
    print("  GET  /api/v2/policy-tags")

    _startup_sync_eligibility()
    if env_bool("PEMATCHING_V2_LEGACY_TAGGING_ON_STARTUP", False):
        _startup_sync_legacy_policy_tags()

    server = ThreadingHTTPServer((host, port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
