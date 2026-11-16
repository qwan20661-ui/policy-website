# -*- coding: utf-8 -*-
"""HTTP 接口测试。

只验接口层（参数校验、响应结构、错误映射），Agent 逻辑由 test_agent.py 覆盖，
故 run_match 被 mock 掉。对应 docs/三期 6.4 节测试要求。
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

import api_server as api
from api_server import (
    ApiError,
    _parse_options,
    _validate_record,
    handle_batch,
    handle_health,
    handle_policy_tags,
)


def _record(name="测试企业"):
    return {
        "enterpriseName": name,
        "registerDate": "2016-10-13 00:00:00",
        "registerAddress": "厦门市思明区高雄路20号",
        "industryLevel2": "软件和信息技术服务业",
        "ipClass1Count": 5,
        "ipClass2Count": 6,
        "operationInfoList": [{"year": "2023", "employeeCount": 108,
                               "operatingIncome": 23995200.0}],
        "honorQualificationList": [{"isTechSme": "是"}],
    }


def _fake_result(name="测试企业"):
    return {
        "data": {
            "enterprise": {"enterpriseName": name, "tags": {}, "dataCompleteness": {}},
            "gateResult": {"passed": True},
            "channelResults": {},
            "aggregatedSignals": {},
            "recommendations": [{"fusionPolicyId": "6548", "rank": 1, "totalScore": 88.0}],
            "supplementSuggestions": [],
            "unmatchedSignals": [],
            "summary": "",
            "candidateCount": 10,
            "scoredCount": 10,
        },
        "meta": {"mode": "deterministic", "requestId": "abc123", "turns": 0,
                 "degraded": False, "truncated": False,
                 "dataCompleteness": {"availableFields": 29, "unknownFields": 28}},
    }


@pytest.fixture(autouse=True)
def _旧接口测试显式使用回滚引擎(monkeypatch):
    """本文件覆盖旧接口分支；生产默认值由新条件接口测试单独验证。"""
    monkeypatch.setattr(api, "_active_engine", lambda: "legacy")


# --------------------------------------------------------------------------
# 参数校验
# --------------------------------------------------------------------------


def test_默认参数():
    options = _parse_options({})

    assert options["mode"] == "agent"
    assert options["keyword"] == ""
    assert options["enable_stage2"] is False
    assert options["top_n"] > 0
    assert options["today"] is None


def test_非法mode报错并列出合法值():
    with pytest.raises(ApiError) as exc:
        _parse_options({"mode": "magic"})

    assert "agent" in str(exc.value)
    assert "deterministic" in str(exc.value)


def test_两种合法mode都接受():
    for mode in ("agent", "deterministic"):
        assert _parse_options({"mode": mode})["mode"] == mode


def test_非法topN报错():
    for bad in ("abc", "0", "-5"):
        with pytest.raises(ApiError) as exc:
            _parse_options({"topN": bad})
        assert "topN" in str(exc.value)


def test_topN受上限约束():
    assert _parse_options({"topN": "999"})["top_n"] == 100


def test_非法enableStage2报错():
    with pytest.raises(ApiError) as exc:
        _parse_options({"enableStage2": "maybe"})

    assert "true/false" in str(exc.value)


def test_enableStage2多种真值写法():
    for truthy in ("true", "1", "yes", "TRUE"):
        assert _parse_options({"enableStage2": truthy})["enable_stage2"] is True
    for falsy in ("false", "0", "no"):
        assert _parse_options({"enableStage2": falsy})["enable_stage2"] is False


def test_非法today报错():
    with pytest.raises(ApiError) as exc:
        _parse_options({"today": "2026-13-45"})

    assert "YYYY-MM-DD" in str(exc.value)


def test_合法today被解析():
    assert _parse_options({"today": "2026-08-03"})["today"] == date(2026, 8, 3)


def test_keyword原样透传():
    assert _parse_options({"keyword": "研发补助"})["keyword"] == "研发补助"


# --------------------------------------------------------------------------
# 企业记录校验
# --------------------------------------------------------------------------


def test_合法记录通过():
    assert _validate_record(_record())["enterpriseName"] == "测试企业"


def test_非对象记录报错():
    with pytest.raises(ApiError) as exc:
        _validate_record(["不是对象"])

    assert "必须是对象" in str(exc.value)


def test_缺企业名报错():
    with pytest.raises(ApiError) as exc:
        _validate_record({"creditCode": "123"})

    assert "enterpriseName" in str(exc.value)


def test_批量校验错误含条目序号():
    """让调用方知道是第几条有问题。"""
    with pytest.raises(ApiError) as exc:
        _validate_record({}, index=2)

    assert "第 3 条" in str(exc.value)


# --------------------------------------------------------------------------
# /health
# --------------------------------------------------------------------------


def test_health返回必要字段():
    payload = handle_health()

    for field in ("service", "status", "port", "model", "config",
                  "policySnapshot", "policyConditionSnapshot", "policySync",
                  "matchPolicySource", "policyTags"):
        assert field in payload, field


def test_health含配置回显():
    config = handle_health()["config"]

    for key in ("maxWorkers", "llmTimeout", "agentMaxTurns", "batchMaxSize",
                "legacyTaggingOnStartup"):
        assert key in config, key


def test_health将旧政策索引标记为按需加载():
    payload = handle_health()

    assert payload["policyIndex"]["status"] == "legacy_on_demand"
    assert payload["policyIndex"]["loaded"] is False


def test_health在旧索引不可用时不影响新链路状态(monkeypatch):
    legacy_index = MagicMock(side_effect=api.pi.PolicyIndexError("标签文件不存在"))
    monkeypatch.setattr(api.pi, "index_stats", legacy_index)
    monkeypatch.setattr(api, "match_policy_source_stats", lambda: {
        "status": "ready",
        "count": 76,
        "versionHash": "a" * 64,
    })
    monkeypatch.setattr(api, "get_sync_status", lambda: {
        "status": "ready",
        "ready": True,
        "policyCount": 76,
        "version": "b" * 64,
        "snapshotFetchedAt": "2026-08-07T10:00:00",
        "lastAttemptAt": "2026-08-07T10:00:00",
        "source": "http://127.0.0.1:3000",
        "degradedReason": "",
    })

    payload = handle_health()

    assert payload["status"] == "ok"
    assert payload["matchPolicySource"]["count"] == 76
    assert payload["policyIndex"]["loaded"] is False
    legacy_index.assert_not_called()


def test_health反映政策同步降级原因(monkeypatch):
    monkeypatch.setattr(api, "get_sync_status", lambda: {
        "status": "degraded",
        "ready": True,
        "policyCount": 76,
        "version": "a" * 64,
        "snapshotFetchedAt": "2026-08-06T10:00:00",
        "lastAttemptAt": "2026-08-06T10:01:00",
        "source": "http://127.0.0.1:3000",
        "degradedReason": "接口超时",
    })

    payload = handle_health()

    assert payload["status"] == "degraded"
    assert payload["policySync"]["policyCount"] == 76
    assert payload["policySync"]["degradedReason"] == "接口超时"


def test_启动时从统一政策快照生成条件包():
    source_snapshot = {"count": 1, "data": [{"fusionPolicyId": "P1"}]}
    condition_snapshot = {"count": 1, "data": [{"fusionPolicyId": "P1"}]}
    stats = {
        "count": 1,
        "ruleTableEmpty": 0,
        "suspectedOrOperatorMismatch": 0,
    }

    with patch.object(api, "load_snapshot", return_value=source_snapshot), \
         patch.object(api, "build_condition_package_snapshot", return_value=condition_snapshot) as build, \
         patch.object(api, "write_condition_package_snapshot") as write, \
         patch.object(api, "condition_package_stats", return_value=stats):
        ready = api._startup_build_policy_condition_packages()

    assert ready is True
    build.assert_called_once_with(source_snapshot)
    write.assert_called_once_with(condition_snapshot)


# --------------------------------------------------------------------------
# batch
# --------------------------------------------------------------------------


def test_批量正常处理():
    options = {"mode": "deterministic", "keyword": "", "enable_stage2": False,
               "top_n": 3, "today": date(2026, 8, 3)}

    with patch.object(api, "run_match", side_effect=lambda record, **kw: _fake_result(
        record["enterpriseName"]
    )):
        result = handle_batch([_record("甲"), _record("乙")], options)

    assert result["meta"]["total"] == 2
    assert result["meta"]["succeeded"] == 2
    assert result["meta"]["failed"] == 0
    assert [item["enterpriseName"] for item in result["data"]["results"]] == ["甲", "乙"]


def test_批量结果保持输入顺序():
    """并发执行但结果顺序必须与输入一致。"""
    options = {"mode": "deterministic", "keyword": "", "enable_stage2": False,
               "top_n": 3, "today": None}
    records = [_record(f"企业{index}") for index in range(8)]

    with patch.object(api, "run_match", side_effect=lambda record, **kw: _fake_result(
        record["enterpriseName"]
    )):
        result = handle_batch(records, options)

    names = [item["enterpriseName"] for item in result["data"]["results"]]
    assert names == [f"企业{index}" for index in range(8)]
    assert [item["index"] for item in result["data"]["results"]] == list(range(8))


def test_批量单条失败不影响其余():
    options = {"mode": "deterministic", "keyword": "", "enable_stage2": False,
               "top_n": 3, "today": None}

    def flaky(record, **kwargs):
        if record["enterpriseName"] == "乙":
            raise RuntimeError("模拟失败")
        return _fake_result(record["enterpriseName"])

    with patch.object(api, "run_match", side_effect=flaky):
        result = handle_batch([_record("甲"), _record("乙"), _record("丙")], options)

    assert result["meta"]["succeeded"] == 2
    assert result["meta"]["failed"] == 1
    failed = next(item for item in result["data"]["results"] if not item["success"])
    assert failed["enterpriseName"] == "乙"
    assert "RuntimeError" in failed["error"]


def test_批量超上限报错():
    options = {"mode": "deterministic", "keyword": "", "enable_stage2": False,
               "top_n": 3, "today": None}

    with pytest.raises(ApiError) as exc:
        handle_batch([_record()] * 200, options)

    assert "批量上限" in str(exc.value)
    assert "run_tagmatch_batch" in str(exc.value), "应引导用 CLI"


def test_批量先校验全部记录再执行():
    """避免跑到一半才发现格式问题。"""
    options = {"mode": "deterministic", "keyword": "", "enable_stage2": False,
               "top_n": 3, "today": None}
    called = MagicMock()

    with patch.object(api, "run_match", called):
        with pytest.raises(ApiError):
            handle_batch([_record("甲"), {"bad": 1}], options)

    called.assert_not_called()


def test_批量失败项含requestId():
    """便于查日志定位。"""
    options = {"mode": "deterministic", "keyword": "", "enable_stage2": False,
               "top_n": 3, "today": None}

    with patch.object(api, "run_match", side_effect=RuntimeError("boom")):
        result = handle_batch([_record()], options)

    assert result["data"]["results"][0]["meta"]["requestId"]


# --------------------------------------------------------------------------
# /api/v2/policy-tags
# --------------------------------------------------------------------------


def test_政策标签列表默认排除不可申报():
    payload = handle_policy_tags({"limit": "200"})
    stats = payload["stats"]

    assert len(payload["policies"]) <= stats["applicable"]
    assert all(item["applicable"] is not False for item in payload["policies"])


def test_政策标签可包含不可申报():
    default = handle_policy_tags({"limit": "200"})
    full = handle_policy_tags({"includeNotApplicable": "true", "limit": "200"})

    assert len(full["policies"]) > len(default["policies"])


def test_政策标签limit生效():
    payload = handle_policy_tags({"limit": "3"})

    assert len(payload["policies"]) == 3


def test_政策标签单条查询():
    payload = handle_policy_tags({"fusionPolicyId": "6451"})

    assert len(payload["policies"]) == 1
    assert payload["policies"][0]["fusionPolicyId"] == "6451"


def test_政策标签查询不存在返回404():
    with pytest.raises(ApiError) as exc:
        handle_policy_tags({"fusionPolicyId": "NOT_EXIST"})

    assert exc.value.status == 404


def test_政策标签项含六维摘要():
    payload = handle_policy_tags({"limit": "1"})
    item = payload["policies"][0]

    for field in ("fusionPolicyId", "title", "A", "B", "C", "D",
                  "applicable", "audience", "applicationWindow", "source"):
        assert field in item, field


# --------------------------------------------------------------------------
# 响应格式
# --------------------------------------------------------------------------


def _fake_handler():
    handler = MagicMock()
    handler.wfile = MagicMock()
    written = {}

    def capture(data):
        written["body"] = data

    handler.wfile.write.side_effect = capture
    handler._written = written
    return handler


def test_成功响应统一格式():
    handler = _fake_handler()

    api._ok(handler, {"x": 1}, {"y": 2})

    payload = json.loads(handler._written["body"].decode("utf-8"))
    assert payload["success"] is True
    assert payload["data"] == {"x": 1}
    assert payload["meta"] == {"y": 2}
    assert payload["message"] == ""


def test_失败响应统一格式():
    handler = _fake_handler()

    api._fail(handler, "出错了", status=400)

    payload = json.loads(handler._written["body"].decode("utf-8"))
    assert payload["success"] is False
    assert payload["data"] is None
    assert payload["message"] == "出错了"
    handler.send_response.assert_called_with(400)


def test_响应含CORS头():
    handler = _fake_handler()

    api._ok(handler, {})

    headers = [call.args[0] for call in handler.send_header.call_args_list]
    assert "Access-Control-Allow-Origin" in headers


def test_响应声明utf8():
    handler = _fake_handler()

    api._ok(handler, {"中文": "值"})

    pairs = {call.args[0]: call.args[1] for call in handler.send_header.call_args_list}
    assert "utf-8" in pairs["Content-Type"]


def test_中文内容不被转义():
    handler = _fake_handler()

    api._ok(handler, {"企业名称": "厦门某某公司"})

    body = handler._written["body"].decode("utf-8")
    assert "厦门某某公司" in body


# --------------------------------------------------------------------------
# 请求体解析
# --------------------------------------------------------------------------


def _handler_with_body(raw: bytes):
    handler = MagicMock()
    handler.headers = {"Content-Length": str(len(raw))}
    handler.rfile = MagicMock()
    handler.rfile.read.return_value = raw
    return handler


def test_解析合法请求体():
    handler = _handler_with_body(json.dumps({"a": 1}).encode("utf-8"))

    assert api._read_json_body(handler) == {"a": 1}


def test_空请求体报错():
    handler = MagicMock()
    handler.headers = {"Content-Length": "0"}

    with pytest.raises(ApiError) as exc:
        api._read_json_body(handler)

    assert "为空" in str(exc.value)


def test_非法json请求体报错():
    handler = _handler_with_body(b"{broken")

    with pytest.raises(ApiError) as exc:
        api._read_json_body(handler)

    assert "合法 JSON" in str(exc.value)


def test_查询参数解析():
    query = api._parse_query("/api/v2/tagmatch/enterprise?mode=agent&topN=5")

    assert query == {"mode": "agent", "topN": "5"}


def test_无查询参数返回空字典():
    assert api._parse_query("/health") == {}
