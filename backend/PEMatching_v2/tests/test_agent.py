# -*- coding: utf-8 -*-
"""Agent 循环控制测试。用录制的 tool_calls 序列回放，不依赖真实模型。

对应 docs/三期_Agent与接口开发流程.md 第 4.5 节测试要求，
七条路径：正常多轮 / 不调工具 / 不可解析 / 超轮次 / LLM 不可用 /
模型伪造参数 / deterministic 独立跑通。
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

import pytest

from service import agent as ag
from service.agent import (
    DEGRADE_LLM_UNAVAILABLE,
    DEGRADE_MODEL_IDLE,
    DEGRADE_TRUNCATED,
    DEGRADE_UNPARSEABLE,
    MODE_AGENT,
    MODE_DETERMINISTIC,
    run_agent,
    run_deterministic,
    run_match,
)
from service.event_log import NullEventLog
from service.llm_client import LLMError

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


def _tool_message(name, arguments=None, call_id="c1"):
    return {
        "content": None,
        "tool_calls": [{
            "id": call_id, "type": "function",
            "function": {"name": name,
                         "arguments": json.dumps(arguments or {}, ensure_ascii=False)},
        }],
    }


def _final_message(recommendations=None, summary="测试总结"):
    return {"content": json.dumps({
        "recommendations": recommendations or [],
        "supplementSuggestions": [{"field": "techContractWan", "note": "补充可解锁技术交易政策"}],
        "summary": summary,
    }, ensure_ascii=False)}


class Recorder(NullEventLog):
    """记录事件供断言。"""

    def __init__(self):
        super().__init__()
        self.records = []

    def write(self, kind, detail=None):
        self.records.append((kind, detail))

    def kinds(self):
        return [kind for kind, _ in self.records]


# --------------------------------------------------------------------------
# deterministic：一等公民路径
# --------------------------------------------------------------------------


def test_deterministic不调模型独立跑通():
    """不 mock 任何 LLM，断言能出完整结果。"""
    result = run_deterministic(_record(), keyword="研发补助", top_n=5, today=TODAY)

    data = result["data"]
    assert data["enterprise"]["enterpriseName"] == "测试企业"
    assert data["recommendations"]
    assert result["meta"]["mode"] == MODE_DETERMINISTIC
    assert result["meta"]["turns"] == 0
    assert result["meta"]["degraded"] is False


def test_deterministic响应结构完整():
    result = run_deterministic(_record(), top_n=3, today=TODAY)
    data = result["data"]

    for field in ("enterprise", "gateResult", "channelResults", "aggregatedSignals",
                  "recommendations", "eligibilityExclusions", "supplementSuggestions",
                  "unmatchedSignals", "candidateCount", "scoredCount",
                  "recommendableCount", "eligibilityResult"):
        assert field in data, field


def test_deterministic推荐项含资格评估明细():
    result = run_deterministic(_record(), top_n=3, today=TODAY)

    for item in result["data"]["recommendations"]:
        assert item["conditionMatchScore"] is not None
        assert item["gateStatus"] in ("direct_push", "needs_supplement")
        assert item["conditionDetails"]
        assert item["reason"]


def test_推荐项只含直推或需补充且按符合度排序():
    result = run_deterministic(_record(), top_n=100, today=TODAY)
    recommendations = result["data"]["recommendations"]

    for item in recommendations:
        assert item["gateStatus"] in ("direct_push", "needs_supplement")
    scores = [item["conditionMatchScore"] for item in recommendations]
    assert scores == sorted(scores, reverse=True)


def test_deterministic遵守top_n窗口():
    result = run_deterministic(_record(), top_n=20, today=TODAY)
    data = result["data"]

    assert len(data["recommendations"]) <= 20
    assert data["recommendableCount"] >= len(data["recommendations"])
    assert data["candidateCount"] >= data["scoredCount"]


def test_资格排除不占推荐名额():
    result = run_deterministic(_record(), top_n=100, today=TODAY)
    recommended_ids = {item["fusionPolicyId"] for item in result["data"]["recommendations"]}
    eligibility_exclusions = {
        item["fusionPolicyId"]: item for item in result["data"]["eligibilityExclusions"]
    }

    for policy_id in eligibility_exclusions:
        assert policy_id not in recommended_ids
    assert all(item.get("gateStatus") or item.get("reasons") for item in eligibility_exclusions.values())


def test_deterministic按符合度降序():
    result = run_deterministic(_record(), top_n=10, today=TODAY)
    scores = [item["conditionMatchScore"] for item in result["data"]["recommendations"]]

    assert scores == sorted(scores, reverse=True)


def test_deterministic可复现():
    """回归测试的基础。"""
    first = run_deterministic(_record(), keyword="研发", top_n=5, today=TODAY)
    second = run_deterministic(_record(), keyword="研发", top_n=5, today=TODAY)

    def ids(result):
        return [(x["fusionPolicyId"], x["conditionMatchScore"]) for x in result["data"]["recommendations"]]

    assert ids(first) == ids(second)


def test_deterministic遵守top_n():
    result = run_deterministic(_record(), top_n=3, today=TODAY)

    assert len(result["data"]["recommendations"]) <= 3


def test_G0拦截时不产出推荐():
    record = _record(operationInfoList=[{
        "year": "2023", "employeeCount": 10, "enterpriseCredit": "存在经营异常",
    }])

    result = run_deterministic(record, today=TODAY)

    assert result["data"]["gateResult"]["passed"] is False
    assert result["data"]["recommendations"] == []


def test_deterministic产出补数建议():
    result = run_deterministic(_record(), today=TODAY)
    suggestions = result["data"]["supplementSuggestions"]

    assert suggestions
    assert all(item["field"] and item["unlocksRules"] for item in suggestions)


def test_补数建议排除永久跳过的字段():
    """R17 依赖的字段接口里就没有，补不了，不该出现在建议里。"""
    result = run_deterministic(_record(), today=TODAY)
    fields = {item["field"] for item in result["data"]["supplementSuggestions"]}

    assert "outboundInvestmentCount" not in fields


def test_deterministic产出unmatchedSignals():
    result = run_deterministic(_record(), today=TODAY)

    assert result["data"]["unmatchedSignals"]


def test_meta含dataCompleteness():
    """让调用方看出结果建立在多少信息量上。"""
    result = run_deterministic(_record(), today=TODAY)
    completeness = result["meta"]["dataCompleteness"]

    assert completeness["availableFields"] > 0
    assert completeness["totalFields"] > 0


# --------------------------------------------------------------------------
# agent 正常多轮
# --------------------------------------------------------------------------


def test_正常多轮流程():
    log = Recorder()
    with patch.object(ag, "chat_with_retry") as mocked:
        mocked.side_effect = [
            _tool_message("build_enterprise_profile"),
            _tool_message("run_channel_rules"),
            _tool_message("search_policies", {"cTags": ["C1"], "limit": 5}),
            _final_message(),
        ]
        result = run_agent(_record(), top_n=5, today=TODAY, event_log=log)

    assert result["meta"]["turns"] == 4
    assert result["meta"]["degraded"] is False
    assert result["meta"]["truncated"] is False
    assert result["data"]["recommendations"]


def test_模型只调部分工具时系统补齐():
    """模型没评分就收尾，系统仍补齐分数（实测模型常这样）。"""
    with patch.object(ag, "chat_with_retry") as mocked:
        mocked.side_effect = [
            _tool_message("build_enterprise_profile"),
            _final_message(),
        ]
        result = run_agent(_record(), top_n=5, today=TODAY)

    assert result["data"]["recommendations"], "系统应补齐评分"
    assert result["data"]["scoredCount"] > 0


def test_模型文案被采用():
    with patch.object(ag, "chat_with_retry") as mocked:
        mocked.side_effect = [
            _tool_message("build_enterprise_profile"),
            _final_message(summary="这是模型写的总结"),
        ]
        result = run_agent(_record(), top_n=5, today=TODAY)

    assert result["data"]["summary"] == "这是模型写的总结"


def test_模型给的reason被写入对应政策():
    deterministic = run_deterministic(_record(), top_n=5, today=TODAY)
    top_id = deterministic["data"]["recommendations"][0]["fusionPolicyId"]

    with patch.object(ag, "chat_with_retry") as mocked:
        mocked.side_effect = [
            _tool_message("build_enterprise_profile"),
            _final_message(recommendations=[
                {"fusionPolicyId": top_id, "rank": 1, "reason": "模型写的理由"},
            ]),
        ]
        result = run_agent(_record(), top_n=5, today=TODAY)

    match = next(
        x for x in result["data"]["recommendations"] if x["fusionPolicyId"] == top_id
    )
    assert match["reason"] == "模型写的理由"


# --------------------------------------------------------------------------
# 防越权：分数只从 state 取
# --------------------------------------------------------------------------


def test_模型伪造分数不被采用():
    """模型在最终输出里塞分数，系统一律不采用。"""
    deterministic = run_deterministic(_record(), top_n=5, today=TODAY)
    top = deterministic["data"]["recommendations"][0]

    forged = {"content": json.dumps({
        "recommendations": [{
            "fusionPolicyId": top["fusionPolicyId"], "rank": 1,
            "reason": "理由", "conditionMatchScore": 99.0,
        }],
        "summary": "总结",
    }, ensure_ascii=False)}

    with patch.object(ag, "chat_with_retry") as mocked:
        mocked.side_effect = [_tool_message("build_enterprise_profile"), forged]
        result = run_agent(_record(), top_n=5, today=TODAY)

    match = next(
        x for x in result["data"]["recommendations"]
        if x["fusionPolicyId"] == top["fusionPolicyId"]
    )
    assert match["conditionMatchScore"] == top["conditionMatchScore"], "分数必须来自确定性函数"
    assert match["conditionMatchScore"] != 99.0


def test_模型推荐不存在的政策被忽略():
    with patch.object(ag, "chat_with_retry") as mocked:
        mocked.side_effect = [
            _tool_message("build_enterprise_profile"),
            _final_message(recommendations=[
                {"fusionPolicyId": "FAKE_999", "rank": 1, "reason": "编造的政策"},
            ]),
        ]
        result = run_agent(_record(), top_n=5, today=TODAY)

    ids = {x["fusionPolicyId"] for x in result["data"]["recommendations"]}
    assert "FAKE_999" not in ids


def test_模型排序未覆盖高分政策时按分数排():
    """防高分政策被模型漏掉而掉出推荐位。"""
    deterministic = run_deterministic(_record(), top_n=5, today=TODAY)
    low_ranked = deterministic["data"]["recommendations"][-1]

    with patch.object(ag, "chat_with_retry") as mocked:
        mocked.side_effect = [
            _tool_message("build_enterprise_profile"),
            _final_message(recommendations=[
                {"fusionPolicyId": low_ranked["fusionPolicyId"], "rank": 1, "reason": "x"},
            ]),
        ]
        result = run_agent(_record(), top_n=5, today=TODAY)

    scores = [x["conditionMatchScore"] for x in result["data"]["recommendations"]]
    assert scores == sorted(scores, reverse=True)


def test_模型覆盖全部top_n时仍保持确定性业务排序():
    deterministic = run_deterministic(_record(), top_n=3, today=TODAY)
    top_ids = [x["fusionPolicyId"] for x in deterministic["data"]["recommendations"]]
    reversed_ids = list(reversed(top_ids))

    with patch.object(ag, "chat_with_retry") as mocked:
        mocked.side_effect = [
            _tool_message("build_enterprise_profile"),
            _final_message(recommendations=[
                {"fusionPolicyId": policy_id, "rank": index + 1, "reason": "x"}
                for index, policy_id in enumerate(reversed_ids)
            ]),
        ]
        result = run_agent(_record(), top_n=3, today=TODAY)

    actual = [x["fusionPolicyId"] for x in result["data"]["recommendations"]]
    assert actual == top_ids


def test_未建画像就评分时handler返回纠正提示():
    """模型跳过 build_enterprise_profile 直接评分。"""
    log = Recorder()
    with patch.object(ag, "chat_with_retry") as mocked:
        mocked.side_effect = [
            _tool_message("score_policy", {"fusionPolicyIds": ["6548"]}),
            _tool_message("build_enterprise_profile"),
            _final_message(),
        ]
        result = run_agent(_record(), top_n=5, today=TODAY, event_log=log)

    tool_events = [d for k, d in log.records if k == "tool_call"]
    assert tool_events[0]["ok"] is False
    assert result["data"]["recommendations"], "纠正后仍应产出结果"


# --------------------------------------------------------------------------
# 降级：四条路径
# --------------------------------------------------------------------------


def test_模型连续不调工具时降级():
    log = Recorder()
    idle = {"content": None, "tool_calls": None}

    with patch.object(ag, "chat_with_retry") as mocked:
        mocked.side_effect = [idle, idle, idle]
        result = run_agent(_record(), top_n=5, today=TODAY, event_log=log)

    assert result["meta"]["degraded"] is True
    assert result["meta"]["degradedReason"] == DEGRADE_MODEL_IDLE
    assert result["data"]["recommendations"], "降级后仍返回完整分数"


def test_输出连续不可解析时降级():
    garbage = {"content": "抱歉我无法完成这个任务"}

    with patch.object(ag, "chat_with_retry") as mocked:
        mocked.side_effect = [garbage, garbage, garbage]
        result = run_agent(_record(), top_n=5, today=TODAY)

    assert result["meta"]["degradedReason"] == DEGRADE_UNPARSEABLE
    assert result["data"]["recommendations"]


def test_首次不可解析第二次成功不降级():
    with patch.object(ag, "chat_with_retry") as mocked:
        mocked.side_effect = [{"content": "不是 JSON"}, _final_message()]
        result = run_agent(_record(), top_n=5, today=TODAY)

    assert result["meta"]["degraded"] is False


def test_LLM不可用时降级且接口不失败():
    with patch.object(ag, "chat_with_retry") as mocked:
        mocked.side_effect = LLMError("请求超时（240 秒）")
        result = run_agent(_record(), top_n=5, today=TODAY)

    assert result["meta"]["degraded"] is True
    assert result["meta"]["degradedReason"] == DEGRADE_LLM_UNAVAILABLE
    assert result["data"]["recommendations"], "LLM 挂了也要出分数"


def test_超轮次时标记truncated并补齐():
    """模型一直调工具从不收尾。"""
    with patch.object(ag, "chat_with_retry") as mocked:
        mocked.side_effect = [
            _tool_message("search_policies", {"cTags": ["C9"], "limit": index + 1})
            for index in range(10)
        ]
        result = run_agent(_record(), top_n=5, today=TODAY, max_turns=5)

    assert result["meta"]["truncated"] is True
    assert result["meta"]["degradedReason"] == DEGRADE_TRUNCATED
    assert result["data"]["recommendations"]


def test_降级后仍尝试让模型写解释():
    call_count = {"value": 0}

    def side_effect(messages, **kwargs):
        call_count["value"] += 1
        if kwargs.get("tools"):
            return {"content": None, "tool_calls": None}
        return _final_message(summary="降级后的解释")

    with patch.object(ag, "chat_with_retry", side_effect=side_effect):
        result = run_agent(_record(), top_n=5, today=TODAY)

    assert result["data"]["summary"] == "降级后的解释"


def test_解释也失败时用空文案不报错():
    def side_effect(messages, **kwargs):
        if kwargs.get("tools"):
            return {"content": None, "tool_calls": None}
        raise LLMError("解释也挂了")

    with patch.object(ag, "chat_with_retry", side_effect=side_effect):
        result = run_agent(_record(), top_n=5, today=TODAY)

    assert result["data"]["recommendations"]
    assert result["data"]["summary"] == ""


# --------------------------------------------------------------------------
# 上下文压缩
# --------------------------------------------------------------------------


def test_多轮后触发上下文压缩():
    """实测不压缩时 10 轮 prompt 涨到 19812 token 导致超时。"""
    log = Recorder()
    with patch.object(ag, "chat_with_retry") as mocked:
        mocked.side_effect = [
            _tool_message("search_policies", {"cTags": ["C9"], "limit": index + 1},
                          call_id=f"c{index}")
            for index in range(6)
        ] + [_final_message()]
        run_agent(_record(), top_n=5, today=TODAY, event_log=log, max_turns=8)

    assert "context_compacted" in log.kinds()


def test_压缩保留系统提示与首条用户消息():
    messages = [
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "首条用户消息"},
    ]
    for index in range(8):
        messages.append({"role": "assistant", "content": "", "tool_calls": []})
        messages.append({"role": "tool", "tool_call_id": f"c{index}",
                         "content": json.dumps({"ok": True, "policies": [
                             {"fusionPolicyId": str(i)} for i in range(20)
                         ]}, ensure_ascii=False)})

    compacted = ag._compact_messages(messages, NullEventLog())

    assert compacted[0]["content"] == "系统提示"
    assert compacted[1]["content"] == "首条用户消息"


def test_压缩后总长度显著下降():
    messages = [
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "首条"},
    ]
    big = json.dumps({"ok": True, "policies": [
        {"fusionPolicyId": str(i), "summary": "详细摘要" * 50} for i in range(20)
    ]}, ensure_ascii=False)
    for index in range(8):
        messages.append({"role": "assistant", "content": "", "tool_calls": []})
        messages.append({"role": "tool", "tool_call_id": f"c{index}", "content": big})

    before = sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)
    compacted = ag._compact_messages(messages, NullEventLog())
    after = sum(len(json.dumps(m, ensure_ascii=False)) for m in compacted)

    assert after < before * 0.5


def test_压缩保留最近几轮完整内容():
    messages = [
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "首条"},
    ]
    big = json.dumps({"ok": True, "policies": [{"fusionPolicyId": str(i)} for i in range(30)],
                      "padding": "x" * 2000}, ensure_ascii=False)
    for index in range(8):
        messages.append({"role": "assistant", "content": "", "tool_calls": []})
        messages.append({"role": "tool", "tool_call_id": f"c{index}", "content": big})

    compacted = ag._compact_messages(messages, NullEventLog())

    assert compacted[-1]["content"] == big, "最后一轮应保留完整内容"


def test_消息很少时不压缩():
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "", "tool_calls": []},
        {"role": "tool", "tool_call_id": "c1", "content": "{}"},
    ]

    assert ag._compact_messages(messages, NullEventLog()) == messages


def test_摘要保留政策ID便于模型引用():
    content = json.dumps({
        "ok": True,
        "policies": [{"fusionPolicyId": "6548"}, {"fusionPolicyId": "6534"}],
    }, ensure_ascii=False)

    summary = ag._summarize_tool_result(content)

    assert "6548" in summary
    assert "6534" in summary


def test_摘要保留评分结果():
    content = json.dumps({
        "ok": True,
        "scored": [{"fusionPolicyId": "6548", "conditionMatchScore": 88.5}],
    }, ensure_ascii=False)

    summary = ag._summarize_tool_result(content)

    assert "6548" in summary
    assert "88.5" in summary


def test_摘要保留错误信息():
    content = json.dumps({"ok": False, "error": "请先调用 build_enterprise_profile"},
                         ensure_ascii=False)

    summary = ag._summarize_tool_result(content)

    assert "build_enterprise_profile" in summary


# --------------------------------------------------------------------------
# 候选补齐与评分补齐
# --------------------------------------------------------------------------


def test_多组检索覆盖单组会漏的政策():
    """回归：原来只用一组标签检索且 limit=30，对无行业值企业会漏掉高分政策
    （检索返回近百条同权重政策，排序退化为按 ID 稳定排序，30 条截断切掉 ID 偏后的）。
    实测导致双模式重合度掉到 70%。"""
    record = _record(industryLevel1=None, industryLevel2=None)

    result = run_deterministic(record, keyword="研发补助", top_n=10, today=TODAY)

    assert result["data"]["candidateCount"] >= 50, (
        "无行业值企业的候选池应覆盖大部分政策库，否则会漏高分政策"
    )


def test_检索分四组互补口径():
    log = Recorder()
    run_deterministic(_record(), top_n=5, today=TODAY, event_log=log)

    search_events = [d for k, d in log.records if k == "search"]
    assert search_events
    per_group = search_events[0]["perGroup"]
    assert len(per_group) >= 3, f"应有多组互补检索，实际 {per_group}"


def test_合并后候选去重():
    log = Recorder()
    result = run_deterministic(_record(), top_n=5, today=TODAY, event_log=log)

    search_events = [d for k, d in log.records if k == "search"]
    ids = search_events[0]["policyIds"]
    assert len(ids) == len(set(ids))
    assert result["data"]["candidateCount"] == len(ids)


def test_两模式候选口径一致():
    """双模式重合度达标的前提。"""
    det_log = Recorder()
    run_deterministic(_record(), keyword="研发", top_n=10, today=TODAY, event_log=det_log)
    det_ids = set([d for k, d in det_log.records if k == "search"][0]["policyIds"])

    agent_log = Recorder()
    with patch.object(ag, "chat_with_retry") as mocked:
        mocked.side_effect = [_tool_message("build_enterprise_profile"), _final_message()]
        run_agent(_record(), keyword="研发", top_n=10, today=TODAY, event_log=agent_log)
    agent_ids = set([d for k, d in agent_log.records if k == "search"][0]["policyIds"])

    assert det_ids == agent_ids


def test_模型检索过窄时系统补齐候选():
    """模型自己只检索少量政策，系统按 deterministic 口径补齐。"""
    log = Recorder()
    with patch.object(ag, "chat_with_retry") as mocked:
        mocked.side_effect = [
            _tool_message("build_enterprise_profile"),
            _tool_message("search_policies", {"cTags": ["C1"], "limit": 2}),
            _final_message(),
        ]
        result = run_agent(_record(), top_n=10, today=TODAY, event_log=log)

    assert "fill_search" in log.kinds()
    assert result["data"]["candidateCount"] > 2


def test_模型只评部分候选时系统补齐评分():
    log = Recorder()
    with patch.object(ag, "chat_with_retry") as mocked:
        mocked.side_effect = [
            _tool_message("build_enterprise_profile"),
            _tool_message("search_policies", {"cTags": ["C9"], "limit": 10}),
            _final_message(),
        ]
        result = run_agent(_record(), top_n=10, today=TODAY, event_log=log)

    assert "fill_scored" in log.kinds()
    assert result["data"]["scoredCount"] == result["data"]["candidateCount"]


# --------------------------------------------------------------------------
# run_match 分发
# --------------------------------------------------------------------------


def test_run_match分发deterministic():
    result = run_match(_record(), mode=MODE_DETERMINISTIC, top_n=3, today=TODAY)

    assert result["meta"]["mode"] == MODE_DETERMINISTIC


def test_run_match分发agent():
    with patch.object(ag, "chat_with_retry") as mocked:
        mocked.side_effect = [_tool_message("build_enterprise_profile"), _final_message()]
        result = run_match(_record(), mode=MODE_AGENT, top_n=3, today=TODAY)

    assert result["meta"]["mode"] == MODE_AGENT


def test_run_match非法mode抛错():
    with pytest.raises(ValueError) as exc:
        run_match(_record(), mode="magic", today=TODAY)

    assert "deterministic" in str(exc.value)


# --------------------------------------------------------------------------
# 双模式一致性（核心验收项）
# --------------------------------------------------------------------------


def test_双模式分数完全一致():
    """分数由确定性函数算，两模式必须完全相同。"""
    deterministic = run_deterministic(_record(), keyword="研发", top_n=10, today=TODAY)

    with patch.object(ag, "chat_with_retry") as mocked:
        mocked.side_effect = [
            _tool_message("build_enterprise_profile"),
            _tool_message("run_channel_rules"),
            _final_message(),
        ]
        agent = run_agent(_record(), keyword="研发", top_n=10, today=TODAY)

    det_scores = {
        x["fusionPolicyId"]: x["conditionMatchScore"] for x in deterministic["data"]["recommendations"]
    }
    agent_scores = {
        x["fusionPolicyId"]: x["conditionMatchScore"] for x in agent["data"]["recommendations"]
    }
    shared = set(det_scores) & set(agent_scores)

    assert shared
    for policy_id in shared:
        assert det_scores[policy_id] == agent_scores[policy_id], policy_id


def test_双模式推荐集合一致():
    deterministic = run_deterministic(_record(), keyword="研发", top_n=10, today=TODAY)

    with patch.object(ag, "chat_with_retry") as mocked:
        mocked.side_effect = [
            _tool_message("build_enterprise_profile"),
            _tool_message("run_channel_rules"),
            _final_message(),
        ]
        agent = run_agent(_record(), keyword="研发", top_n=10, today=TODAY)

    det_ids = {x["fusionPolicyId"] for x in deterministic["data"]["recommendations"]}
    agent_ids = {x["fusionPolicyId"] for x in agent["data"]["recommendations"]}
    overlap = len(det_ids & agent_ids) / max(len(det_ids), 1)

    assert overlap >= 0.9, f"重合度 {overlap:.0%} 低于 90%"


def test_双模式企业画像一致():
    deterministic = run_deterministic(_record(), top_n=3, today=TODAY)

    with patch.object(ag, "chat_with_retry") as mocked:
        mocked.side_effect = [_tool_message("build_enterprise_profile"), _final_message()]
        agent = run_agent(_record(), top_n=3, today=TODAY)

    assert deterministic["data"]["enterprise"]["tags"] == agent["data"]["enterprise"]["tags"]


# --------------------------------------------------------------------------
# 审计日志
# --------------------------------------------------------------------------


def test_审计日志记录关键步骤():
    log = Recorder()
    run_deterministic(_record(), top_n=3, today=TODAY, event_log=log)

    kinds = log.kinds()
    for expected in ("mode", "profile", "rules", "search", "scored"):
        assert expected in kinds, expected


def test_agent审计日志记录每次工具调用():
    log = Recorder()
    with patch.object(ag, "chat_with_retry") as mocked:
        mocked.side_effect = [
            _tool_message("build_enterprise_profile"),
            _tool_message("run_channel_rules"),
            _final_message(),
        ]
        run_agent(_record(), top_n=3, today=TODAY, event_log=log)

    tool_calls = [d for k, d in log.records if k == "tool_call"]
    assert len(tool_calls) == 2
    assert tool_calls[0]["name"] == "build_enterprise_profile"


def test_meta含requestId与耗时():
    log = Recorder()
    result = run_deterministic(_record(), top_n=3, today=TODAY, event_log=log)

    assert result["meta"]["requestId"] == log.request_id
    assert result["meta"]["elapsedMs"] is not None


# --------------------------------------------------------------------------
# prompt 契约
# --------------------------------------------------------------------------


def test_agent_prompt存在且含禁止条款():
    prompt = ag.load_prompt()

    assert len(prompt) > 1000
    for keyword in ("自行计算分数", "自行判断规则", "未返回的政策", "推测填补"):
        assert keyword in prompt, keyword


def test_agent_prompt说明字段缺失是常态():
    prompt = ag.load_prompt()

    assert "字段缺失是常态" in prompt
    assert "unknownFields" in prompt


def test_agent_prompt输出示例可解析():
    import re

    prompt = ag.load_prompt()
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", prompt, re.S)

    assert blocks
    for block in blocks:
        json.loads(block)


def test_agent_prompt说明只出三个文案字段():
    prompt = ag.load_prompt()

    assert "由系统填充" in prompt
