# -*- coding: utf-8 -*-
"""Agent 工具注册层测试。

对应 docs/三期_Agent与接口开发流程.md 第 3.4 节测试要求。
重点验证三条防越权设计：状态不经模型手、只能对检索过的政策评分、重复调用被拦。
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from service import agent_tools as at
from service.agent_tools import (
    HANDLERS,
    REPEAT_LIMIT,
    SEARCH_LIMIT_CAP,
    TOOL_NAMES,
    TOOL_SCHEMAS,
    SessionState,
    execute_tool,
)

TODAY = date(2026, 8, 3)


def _record(**overrides):
    record = {
        "enterpriseName": "测试企业",
        "registerDate": "2016-10-13 00:00:00",
        "registerAddress": "厦门市思明区高雄路20号",
        "industryLevel1": "I 信息传输、软件和信息技术服务业",
        "industryLevel2": "软件和信息技术服务业",
        "ipClass1Count": 5,
        "ipClass2Count": 6,
        "operationInfoList": [{
            "year": "2023", "employeeCount": 108, "operatingIncome": 23995200.0,
            "rdExpense": 7353900.0,
        }],
        "honorQualificationList": [{"isTechSme": "是", "isHighTechEnterprise": "是"}],
    }
    record.update(overrides)
    return record


def _state(**kwargs):
    return SessionState(_record(), today=TODAY, **kwargs)


# --------------------------------------------------------------------------
# schema 合法性
# --------------------------------------------------------------------------


def test_六个工具齐全():
    assert len(TOOL_SCHEMAS) == 6
    assert set(TOOL_NAMES) == set(HANDLERS)


def test_每个schema结构合法():
    """OpenAI tool-calling 格式：type=function + function.name/description/parameters。"""
    for schema in TOOL_SCHEMAS:
        assert schema["type"] == "function"
        function = schema["function"]
        assert function["name"]
        assert function["description"]
        parameters = function["parameters"]
        assert parameters["type"] == "object"
        assert isinstance(parameters["properties"], dict)
        assert isinstance(parameters["required"], list)


def test_schema可json序列化():
    """要发给 LLM，必须能序列化。"""
    assert json.loads(json.dumps(TOOL_SCHEMAS, ensure_ascii=False))


def test_工具schema不接受profile参数():
    """状态存 SessionState，不经模型手——防伪造 profile 操纵分数（三期 3.3）。"""
    for schema in TOOL_SCHEMAS:
        properties = schema["function"]["parameters"]["properties"]
        assert "profile" not in properties, schema["function"]["name"]
        assert "enterprise" not in properties, schema["function"]["name"]


def test_score_policy要求政策ID列表():
    schema = next(s for s in TOOL_SCHEMAS if s["function"]["name"] == "score_policy")

    assert "fusionPolicyIds" in schema["function"]["parameters"]["required"]


def test_描述里说明分数不可自行计算():
    """prompt 之外再加一道说明，模型看工具描述的概率更高。"""
    schema = next(s for s in TOOL_SCHEMAS if s["function"]["name"] == "score_policy")

    assert "不得自行计算" in schema["function"]["description"]


def test_search_policies描述提示可反复调用():
    """换标签组合再检索是 Agent 相对 deterministic 的主要增量价值。"""
    schema = next(s for s in TOOL_SCHEMAS if s["function"]["name"] == "search_policies")

    assert "反复调用" in schema["function"]["description"]


def test_子类描述只列docx定义的四个():
    schema = next(s for s in TOOL_SCHEMAS if s["function"]["name"] == "search_policies")
    description = schema["function"]["parameters"]["properties"]["cSubTags"]["description"]

    for code in ("C1.1", "C1.2", "C1.3", "C10.1"):
        assert code in description
    for absent in ("C2.1", "C4.1", "C10.2"):
        assert absent not in description


# --------------------------------------------------------------------------
# build_enterprise_profile
# --------------------------------------------------------------------------


def test_构建画像成功():
    state = _state()

    result = execute_tool(state, "build_enterprise_profile", {})

    assert result["ok"] is True
    assert result["profile"]["tags"]["C"]
    assert state.profile is not None


def test_画像回填只含unknown字段名不含全量键值():
    state = _state()

    result = execute_tool(state, "build_enterprise_profile", {})

    assert "unknownFields" in result["profile"]
    assert "fieldAvailability" not in result["profile"], "全量键值对不该回填给模型"
    assert "availableRawFields" in result["profile"]
    assert "sourcePath" not in result["profile"], "内部 JSONPath 不应直接暴露给模型"
    assert result["profile"]["originalEnterpriseData"]["企业全称"] == "测试企业"


def test_模型可按中文字段名读取映射后的原始企业事实():
    state = _state()
    execute_tool(state, "build_enterprise_profile", {})

    result = execute_tool(
        state,
        "get_enterprise_raw_facts",
        {"fieldNames": ["营业收入", "员工总人数", "不存在的字段"]},
    )

    assert result["ok"] is True
    assert {item["fieldName"] for item in result["facts"]} >= {"营业收入", "员工总人数"}
    assert "不存在的字段" in result["missingFieldNames"]
    assert all("sourcePath" not in item for item in result["facts"])


def test_未建画像时读取原始事实返回纠正提示():
    result = execute_tool(_state(), "get_enterprise_raw_facts", {"fieldNames": ["营业收入"]})

    assert result["ok"] is False
    assert "build_enterprise_profile" in result["error"]


def test_画像回填体积可控():
    state = _state()

    result = execute_tool(state, "build_enterprise_profile", {})

    assert len(json.dumps(result, ensure_ascii=False)) < 4000


def test_重复构建画像不重算():
    state = _state()
    execute_tool(state, "build_enterprise_profile", {})
    first = state.profile

    execute_tool(state, "build_enterprise_profile", {})

    assert state.profile is first


# --------------------------------------------------------------------------
# run_channel_rules
# --------------------------------------------------------------------------


def test_未建画像时执行规则返回纠正提示():
    """返回错误提示而非抛异常，让模型自我纠正。"""
    state = _state()

    result = execute_tool(state, "run_channel_rules", {})

    assert result["ok"] is False
    assert "build_enterprise_profile" in result["error"]


def test_执行规则返回三态():
    state = _state()
    execute_tool(state, "build_enterprise_profile", {})

    result = execute_tool(state, "run_channel_rules", {})

    g2 = result["ruleResult"]["channels"]["G2"]
    assert g2["executed"] is True
    assert "matched" in g2
    assert "unmatchedIds" in g2
    assert "skipped" in g2


def test_规则回填未命中只回ID列表():
    """未命中的规则不需要明细，只回 ID 省上下文。"""
    state = _state()
    execute_tool(state, "build_enterprise_profile", {})

    result = execute_tool(state, "run_channel_rules", {})

    unmatched = result["ruleResult"]["channels"]["G2"]["unmatchedIds"]
    assert all(isinstance(item, str) for item in unmatched)


def test_规则回填跳过项含原因():
    state = _state()
    execute_tool(state, "build_enterprise_profile", {})

    result = execute_tool(state, "run_channel_rules", {})

    skipped = result["ruleResult"]["channels"]["G2"]["skipped"]
    assert skipped
    assert all(item["skipReason"] for item in skipped)


def test_G0拦截信息完整回填():
    state = SessionState(
        _record(operationInfoList=[{
            "year": "2023", "employeeCount": 10, "enterpriseCredit": "存在经营异常",
        }]),
        today=TODAY,
    )
    execute_tool(state, "build_enterprise_profile", {})

    result = execute_tool(state, "run_channel_rules", {})

    gate = result["ruleResult"]["gateResult"]
    assert gate["passed"] is False
    assert gate["blockedBy"] == "R00"


def test_enable_stage2控制G4执行():
    off = _state(enable_stage2=False)
    execute_tool(off, "build_enterprise_profile", {})
    off_result = execute_tool(off, "run_channel_rules", {})

    on = _state(enable_stage2=True)
    execute_tool(on, "build_enterprise_profile", {})
    on_result = execute_tool(on, "run_channel_rules", {})

    assert off_result["ruleResult"]["channels"]["G4"]["executed"] is False
    assert on_result["ruleResult"]["channels"]["G4"]["executed"] is True


# --------------------------------------------------------------------------
# search_policies
# --------------------------------------------------------------------------


def test_检索返回摘要不含全文():
    state = _state()

    result = execute_tool(state, "search_policies", {"cTags": ["C9"], "limit": 5})

    assert result["ok"] is True
    for policy in result["policies"]:
        assert "structuredConditions" not in policy
        assert "supportMethods" not in policy
        assert "materials" not in policy


def test_检索limit受上限约束():
    state = _state()

    result = execute_tool(state, "search_policies", {"cTags": ["C9"], "limit": 999})

    assert len(result["policies"]) <= SEARCH_LIMIT_CAP


def test_检索结果累积到session():
    """后续 score_policy 要校验政策是否检索过。"""
    state = _state()

    execute_tool(state, "search_policies", {"cTags": ["C9"], "limit": 3})
    execute_tool(state, "search_policies", {"cTags": ["C1"], "limit": 3})

    assert len(state.searched) >= 3


def test_检索接受字符串参数并归一化():
    """模型有时把数组写成字符串。"""
    state = _state()

    result = execute_tool(state, "search_policies", {"cTags": "C9", "limit": 3})

    assert result["ok"] is True


def test_检索空参数不报错():
    state = _state()

    result = execute_tool(state, "search_policies", {})

    assert result["ok"] is True


def test_检索用会话关键词兜底():
    state = _state(keyword="研发补助")

    execute_tool(state, "search_policies", {"cTags": ["C9"], "limit": 5})

    assert state.searched


# --------------------------------------------------------------------------
# score_policy：防越权核心
# --------------------------------------------------------------------------


def test_未建画像时评分返回纠正提示():
    state = _state()

    result = execute_tool(state, "score_policy", {"fusionPolicyIds": ["6548"]})

    assert result["ok"] is False
    assert "build_enterprise_profile" in result["error"]


def test_只能对检索过的政策评分():
    """防模型凭空造政策 ID。"""
    state = _state()
    execute_tool(state, "build_enterprise_profile", {})
    execute_tool(state, "search_policies", {"cTags": ["C9"], "limit": 3})

    result = execute_tool(state, "score_policy", {"fusionPolicyIds": ["99999"]})

    assert result["scored"] == []
    assert result["rejected"][0]["fusionPolicyId"] == "99999"
    assert "search_policies" in result["rejected"][0]["reason"]


def test_评分检索过的政策成功():
    state = _state()
    execute_tool(state, "build_enterprise_profile", {})
    search = execute_tool(state, "search_policies", {"cTags": ["C9"], "limit": 3})
    ids = [p["fusionPolicyId"] for p in search["policies"]]

    result = execute_tool(state, "score_policy", {"fusionPolicyIds": ids})

    assert len(result["scored"]) == len(ids)
    assert all("conditionMatchScore" in item and "gateStatus" in item for item in result["scored"])


def test_评分结果按符合度降序():
    state = _state()
    execute_tool(state, "build_enterprise_profile", {})
    search = execute_tool(state, "search_policies", {"cTags": ["C9"], "limit": 8})
    ids = [p["fusionPolicyId"] for p in search["policies"]]

    result = execute_tool(state, "score_policy", {"fusionPolicyIds": ids})
    scores = [item["conditionMatchScore"] for item in result["scored"]]
    non_none = [score for score in scores if score is not None]

    assert non_none == sorted(non_none, reverse=True)
    assert all(score is None for score in scores[len(non_none):])  # 不可评估排最后


def test_完整评分结果存入session():
    """最终响应从 session 取分数，不信模型转述。"""
    state = _state()
    execute_tool(state, "build_enterprise_profile", {})
    search = execute_tool(state, "search_policies", {"cTags": ["C9"], "limit": 3})
    ids = [p["fusionPolicyId"] for p in search["policies"]]

    execute_tool(state, "score_policy", {"fusionPolicyIds": ids})

    assert len(state.scores) == len(ids)
    for entry in state.scores.values():
        assert entry["eligibility"]["gateStatus"], "session 里应有完整资格评估"


def test_评分回填含条件明细摘要():
    state = _state()
    execute_tool(state, "build_enterprise_profile", {})
    search = execute_tool(state, "search_policies", {"cTags": ["C9"], "limit": 2})
    ids = [p["fusionPolicyId"] for p in search["policies"]]

    result = execute_tool(state, "score_policy", {"fusionPolicyIds": ids})
    item = result["scored"][0]

    assert "conditionMatchScore" in item
    assert "gateStatus" in item
    assert "gateReasons" in item
    assert "conditionDetails" in item


def test_评分参数非数组时报错():
    state = _state()
    execute_tool(state, "build_enterprise_profile", {})

    result = execute_tool(state, "score_policy", {"fusionPolicyIds": []})

    assert result["ok"] is False


def test_评分接受单个字符串ID():
    state = _state()
    execute_tool(state, "build_enterprise_profile", {})
    search = execute_tool(state, "search_policies", {"cTags": ["C9"], "limit": 2})
    policy_id = search["policies"][0]["fusionPolicyId"]

    result = execute_tool(state, "score_policy", {"fusionPolicyIds": policy_id})

    assert len(result["scored"]) == 1


# --------------------------------------------------------------------------
# get_policy_detail
# --------------------------------------------------------------------------


def test_详情裁掉关联政策与文档():
    """三期 3.2：去掉 relatedPolicies 与 documents。"""
    state = _state()

    result = execute_tool(state, "get_policy_detail", {"fusionPolicyId": "6548"})

    assert result["ok"] is True
    assert "relatedPolicies" not in result["policy"]
    assert "documents" not in result["policy"]


def test_详情含申报条件与支持方式():
    state = _state()

    result = execute_tool(state, "get_policy_detail", {"fusionPolicyId": "6548"})

    assert "structuredConditions" in result["policy"]
    assert "supportMethods" in result["policy"]


def test_详情不存在的政策返回错误():
    state = _state()

    result = execute_tool(state, "get_policy_detail", {"fusionPolicyId": "NOT_EXIST"})

    assert result["ok"] is False


def test_详情缺参数返回错误():
    state = _state()

    result = execute_tool(state, "get_policy_detail", {})

    assert result["ok"] is False


# --------------------------------------------------------------------------
# execute_tool 通用行为
# --------------------------------------------------------------------------


def test_未知工具返回可用工具列表():
    state = _state()

    result = execute_tool(state, "delete_all_policies", {})

    assert result["ok"] is False
    assert "未知工具" in result["error"]


def test_参数为None返回提示():
    """llm_client.parse_tool_arguments 在非法 JSON 时返回 None。"""
    state = _state()

    result = execute_tool(state, "search_policies", None)

    assert result["ok"] is False
    assert "合法 JSON" in result["error"]


def test_重复调用超限被拦():
    """防模型刷轮次。"""
    state = _state()
    arguments = {"cTags": ["C9"], "limit": 3}

    for _ in range(REPEAT_LIMIT):
        assert execute_tool(state, "search_policies", arguments)["ok"] is True

    result = execute_tool(state, "search_policies", arguments)

    assert result["ok"] is False
    assert "结果同上" in result["error"]


def test_不同参数不算重复():
    state = _state()

    for limit in range(1, 6):
        result = execute_tool(state, "search_policies", {"cTags": ["C9"], "limit": limit})
        assert result["ok"] is True, limit


def test_工具异常不抛出而返回错误(monkeypatch):
    state = _state()

    def boom(state_, arguments):
        raise RuntimeError("模拟异常")

    monkeypatch.setitem(at.HANDLERS, "search_policies", boom)

    result = execute_tool(state, "search_policies", {})

    assert result["ok"] is False
    assert "RuntimeError" in result["error"]


def test_调用记录写入session():
    state = _state()

    execute_tool(state, "build_enterprise_profile", {})
    execute_tool(state, "search_policies", {"cTags": ["C9"]})

    assert len(state.tool_calls) == 2
    assert state.tool_calls[0]["name"] == "build_enterprise_profile"
    assert all("elapsedMs" in call for call in state.tool_calls)


def test_调用写入event_log():
    from service.event_log import NullEventLog

    class Recorder(NullEventLog):
        def __init__(self):
            super().__init__()
            self.records = []

        def write(self, kind, detail=None):
            self.records.append((kind, detail))

    state = _state()
    log = Recorder()

    execute_tool(state, "build_enterprise_profile", {}, event_log=log)

    assert log.records
    assert log.records[0][0] == "tool_call"
    assert log.records[0][1]["name"] == "build_enterprise_profile"


# --------------------------------------------------------------------------
# unmatchedSignals
# --------------------------------------------------------------------------


def test_收集库内无对应政策的规则信号():
    state = _state()
    execute_tool(state, "build_enterprise_profile", {})
    execute_tool(state, "run_channel_rules", {})

    signals = at.collect_unmatched_policy_signals(state)

    assert signals
    assert all(item["ruleId"] and item["policyPackDesc"] for item in signals)


def test_未跑规则时无信号():
    state = _state()

    assert at.collect_unmatched_policy_signals(state) == []
