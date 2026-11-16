# -*- coding: utf-8 -*-
"""政策打标测试。LLM 全部 mock，不发真实请求。

对应 docs/二期_政策打标开发流程.md 第 4.4 节测试要求。
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from service import tagging
from service.llm_client import LLMError
from service.tagging import (
    TaggingError,
    build_tagging_context,
    tag_policy,
    validate_tagging_result,
)


def _policy(**overrides):
    policy = {
        "fusionPolicyId": "6548",
        "title": "厦门市科技型中小企业评价",
        "policyInfo": {
            "policyTitle": "厦门市科技型中小企业评价",
            "summary": "对符合条件的企业开展自主填报、信息确认及入库登记编号的专项认定工作。",
            "industryField": "通用行业",
            "industryFieldPath": "通用行业",
            "applicableScopePath": "市级-厦门市",
            "sceneUsage": "科技型中小企业认定",
        },
        "supportMethods": [{
            "supportType": "科技型中小企业认定",
            "supportTypePath": "资质荣誉-科技型中小企业认定",
            "amount": "", "maxAmount": "", "contentDesc": "",
        }],
        "structuredConditions": [{
            "applicableTarget": "小型企业；中型企业；微型企业",
            "applicationRequirement": "职工总数不超过500人、年销售收入不超过2亿元",
            "applicationInstruction": "2026年06月2日-2026年8月31日",
            "applicationStartTime": "2026.06.02",
            "applicationEndTime": "2026.08.31",
        }],
        "keywords": [{"text": "科技型中小企业认定", "pathText": "资质认定-科技型中小企业认定"}],
        "materials": [{"materials": "申报书", "method": "线上填报"}],
        "documents": [{"publishDate": "2026-05-01", "policyAttribute": "申报通知"}],
        "relatedPolicies": {"parent": [], "directParents": [], "ancestors": [],
                            "children": [], "descendants": []},
    }
    policy.update(overrides)
    return policy


def _valid_result(policy_id="6548", **overrides):
    result = {
        "fusionPolicyId": policy_id,
        "tags": {
            "A": {"primary": "A1", "secondary": [], "confidence": "high", "basis": "supportTypePath=资质荣誉"},
            "B": {"values": ["B1", "B2"], "confidence": "medium", "basis": "职工≤500人门槛"},
            "C": {"values": ["C9"], "subValues": [], "confidence": "high", "basis": "industryField=通用行业"},
            "D": {"value": "D1", "confidence": "high", "basis": "入库登记无评审"},
            "E": {"value": None, "confidence": "low", "basis": "无金额字段"},
            "F": {"value": "F3", "confidence": "high", "basis": "年度评价"},
        },
        "applicability": {"applicable": True, "reason": "企业自主申报入库获得资质", "audience": "企业"},
        "growthChain": {"prerequisite": None, "nextStage": "市高企培育库", "basis": "链路起点"},
        "keywordIndex": {"title": ["科技型中小企业"], "aKeywords": ["资质认定"], "cKeywords": []},
        "notes": "",
    }
    result.update(overrides)
    return result


def _message(payload):
    content = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return {"content": content}


# --------------------------------------------------------------------------
# build_tagging_context
# --------------------------------------------------------------------------


def test_上下文含标题与摘要():
    context = build_tagging_context(_policy())

    assert "厦门市科技型中小企业评价" in context
    assert "入库登记编号" in context


def test_上下文含支持方式与路径():
    context = build_tagging_context(_policy())

    assert "supportTypePath=资质荣誉-科技型中小企业认定" in context


def test_上下文含平台关键词及其分类路径():
    """keywords 88/110 有值，是 A、C 的强信号。"""
    context = build_tagging_context(_policy())

    assert "科技型中小企业认定" in context
    assert "分类路径" in context


def test_上下文含industryField但标注为判断依据():
    context = build_tagging_context(_policy())

    assert "industryField" in context
    assert "通用行业" in context


def test_上下文含申报条件():
    context = build_tagging_context(_policy())

    assert "职工总数不超过500人" in context


def test_上下文含系统已提取的申报期():
    """申报期确定性提取后作为判断 F 兑现周期的参考。"""
    context = build_tagging_context(_policy())

    assert "系统已提取的申报期" in context
    assert "2026-06-02" in context


def test_上下文标注长期受理():
    policy = _policy(structuredConditions=[{"applicationInstruction": "长期受理"}])
    context = build_tagging_context(policy)

    assert "长期受理" in context


def test_上下文在申报期提取失败时明确标注():
    policy = _policy(structuredConditions=[{"applicableTarget": "企业"}])
    context = build_tagging_context(policy)

    assert "未能提取" in context


def test_上下文含关联政策摘要():
    policy = _policy(relatedPolicies={
        "parent": [{
            "fusionPolicyId": "P1", "title": "上级办法",
            "summary": "总体扶持框架",
            "conditions": [{"applicationRequirement": "须为高企"}],
        }],
        "directParents": [], "ancestors": [], "children": [], "descendants": [],
    })
    context = build_tagging_context(policy)

    assert "上级办法" in context
    assert "总体扶持框架" in context
    assert "条件摘要" in context


def test_上下文在关联政策为空时不报错():
    context = build_tagging_context(_policy(relatedPolicies={}))

    assert "厦门市科技型中小企业评价" in context


def test_上下文在支持方式为空时明确标注():
    context = build_tagging_context(_policy(supportMethods=[]))

    assert "无 supportMethods 数据" in context


def test_上下文在申报条件为空时明确标注():
    context = build_tagging_context(_policy(structuredConditions=[]))

    assert "无 structuredConditions 数据" in context


def test_上下文对超长字段截断():
    policy = _policy(policyInfo={**_policy()["policyInfo"], "summary": "很长" * 2000})
    context = build_tagging_context(policy)

    assert len(context) < 8000


def test_上下文清理不换行空格():
    """接口里大量 \\xa0 与多余空白，不清理会浪费 token。"""
    policy = _policy(supportMethods=[{
        "supportType": "资金补助", "supportTypePath": "资金补助-一次性奖励\xa0\xa0\xa0\xa0\xa0",
    }])
    context = build_tagging_context(policy)

    assert "\xa0" not in context


def test_上下文对非字典子表元素不报错():
    policy = _policy(supportMethods=["坏数据"], structuredConditions=["坏数据"], keywords=["坏"])
    context = build_tagging_context(policy)

    assert "厦门市科技型中小企业评价" in context


# --------------------------------------------------------------------------
# validate_tagging_result
# --------------------------------------------------------------------------


def test_合法产出无致命错误():
    fatal, warnings = validate_tagging_result(_valid_result(), "6548")

    assert fatal == []


def test_C输出政策库词表值被拒绝():
    """这条是防匹配轴污染的关键测试（开发计划决策 10）。"""
    result = _valid_result()
    result["tags"]["C"]["values"] = ["通用行业"]

    fatal, _ = validate_tagging_result(result, "6548")

    assert any("C.values" in e for e in fatal)


def test_C输出半导体和集成电路原始词被拒绝():
    result = _valid_result()
    result["tags"]["C"]["values"] = ["半导体和集成电路"]

    fatal, _ = validate_tagging_result(result, "6548")

    assert fatal


def test_basis为空被拒绝():
    result = _valid_result()
    result["tags"]["A"]["basis"] = "   "

    fatal, _ = validate_tagging_result(result, "6548")

    assert any("A.basis" in e for e in fatal)


def test_置信度非法被拒绝():
    result = _valid_result()
    result["tags"]["D"]["confidence"] = "very_high"

    fatal, _ = validate_tagging_result(result, "6548")

    assert any("D.confidence" in e for e in fatal)


def test_policyId不一致被拒绝():
    fatal, _ = validate_tagging_result(_valid_result("9999"), "6548")

    assert any("fusionPolicyId 不一致" in e for e in fatal)


def test_缺tags被拒绝():
    fatal, _ = validate_tagging_result({"fusionPolicyId": "6548"}, "6548")

    assert any("tags" in e for e in fatal)


def test_产出非字典被拒绝():
    fatal, _ = validate_tagging_result("not a dict", "6548")

    assert fatal


def test_子类大类不一致被拒绝():
    """C1.2 的大类是 C1，若 values 里没有 C1 则矛盾。"""
    result = _valid_result()
    result["tags"]["C"] = {
        "values": ["C2"], "subValues": ["C1.2"], "confidence": "high", "basis": "x",
    }

    fatal, _ = validate_tagging_result(result, "6548")

    assert any("大类" in e for e in fatal)


def test_通用标签带子类被拒绝():
    """C9 通用意味着无产业限定，给子类自相矛盾。"""
    result = _valid_result()
    result["tags"]["C"] = {
        "values": ["C9"], "subValues": ["C1.2"], "confidence": "high", "basis": "x",
    }

    fatal, _ = validate_tagging_result(result, "6548")

    assert any("通用" in e for e in fatal)


def test_通用与具体领域并存给警告():
    result = _valid_result()
    result["tags"]["C"] = {
        "values": ["C9", "C1"], "subValues": [], "confidence": "high", "basis": "x",
    }

    fatal, warnings = validate_tagging_result(result, "6548")

    assert fatal == []
    assert any("语义可能矛盾" in w for w in warnings)


def test_四维全low给警告不拒绝():
    result = _valid_result()
    for dimension in ("A", "B", "C", "D"):
        result["tags"][dimension]["confidence"] = "low"

    fatal, warnings = validate_tagging_result(result, "6548")

    assert fatal == []
    assert any("low 置信度" in w for w in warnings)


def test_keywordIndex_title为空给警告():
    result = _valid_result()
    result["keywordIndex"]["title"] = []

    fatal, warnings = validate_tagging_result(result, "6548")

    assert fatal == []
    assert any("title 为空" in w for w in warnings)


def test_keywordIndex类型错误被拒绝():
    result = _valid_result()
    result["keywordIndex"]["title"] = "不是数组"

    fatal, _ = validate_tagging_result(result, "6548")

    assert any("必须是数组" in e for e in fatal)


def test_growthChain类型错误被拒绝():
    result = _valid_result(growthChain="不是对象")

    fatal, _ = validate_tagging_result(result, "6548")

    assert any("growthChain" in e for e in fatal)


# --------------------------------------------------------------------------
# applicability：不是所有入库文件都是可申报政策
# --------------------------------------------------------------------------


def test_缺applicability被拒绝():
    """专家征集、报告报送这类文件若不标出，会被推给企业。"""
    result = _valid_result()
    del result["applicability"]

    fatal, _ = validate_tagging_result(result, "6548")

    assert any("applicability" in e for e in fatal)


def test_applicability非对象被拒绝():
    result = _valid_result(applicability="是")

    fatal, _ = validate_tagging_result(result, "6548")

    assert any("applicability" in e for e in fatal)


def test_applicable非布尔被拒绝():
    result = _valid_result(applicability={"applicable": "是", "reason": "x"})

    fatal, _ = validate_tagging_result(result, "6548")

    assert any("必须是布尔值" in e for e in fatal)


def test_applicability缺reason被拒绝():
    result = _valid_result(applicability={"applicable": True, "reason": "  "})

    fatal, _ = validate_tagging_result(result, "6548")

    assert any("reason" in e for e in fatal)


def test_判定不可申报时给警告不拒绝():
    result = _valid_result(applicability={
        "applicable": False, "reason": "面向个人专家非企业申报", "audience": "个人",
    })

    fatal, warnings = validate_tagging_result(result, "6548")

    assert fatal == []
    assert any("不可申报" in w for w in warnings)


def test_产出携带applicability字段():
    with patch.object(tagging, "chat_with_retry") as mocked:
        mocked.return_value = _message(_valid_result())
        result = tag_policy(_policy())

    assert result["applicability"]["applicable"] is True
    assert result["applicability"]["audience"] == "企业"


# --------------------------------------------------------------------------
# tag_policy：重试与错误回传
# --------------------------------------------------------------------------


def test_首次合法则不重试():
    with patch.object(tagging, "chat_with_retry") as mocked:
        mocked.return_value = _message(_valid_result())
        result = tag_policy(_policy())

    assert mocked.call_count == 1
    assert result["_attempts"] == 1
    assert result["tags"]["C"]["values"] == ["C9"]


def test_前两次非法第三次合法():
    bad = _valid_result()
    bad["tags"]["C"]["values"] = ["通用行业"]

    with patch.object(tagging, "chat_with_retry") as mocked:
        mocked.side_effect = [
            _message(bad), _message(bad), _message(_valid_result()),
        ]
        result = tag_policy(_policy(), max_retries=2)

    assert mocked.call_count == 3
    assert result["_attempts"] == 3


def test_重试时把校验错误回传给模型():
    bad = _valid_result()
    bad["tags"]["C"]["values"] = ["通用行业"]

    with patch.object(tagging, "chat_with_retry") as mocked:
        mocked.side_effect = [_message(bad), _message(_valid_result())]
        tag_policy(_policy(), max_retries=1)

    second_messages = mocked.call_args_list[1][0][0]
    assert second_messages[-1]["role"] == "user"
    assert "C.values" in second_messages[-1]["content"]


def test_连续失败抛TaggingError且含policyId():
    bad = _valid_result()
    bad["tags"]["C"]["values"] = ["通用行业"]

    with patch.object(tagging, "chat_with_retry") as mocked:
        mocked.return_value = _message(bad)
        with pytest.raises(TaggingError) as exc:
            tag_policy(_policy(), max_retries=1)

    assert "6548" in str(exc.value)


def test_响应非法JSON时重试():
    with patch.object(tagging, "chat_with_retry") as mocked:
        mocked.side_effect = [_message("这不是 JSON"), _message(_valid_result())]
        result = tag_policy(_policy(), max_retries=1)

    assert mocked.call_count == 2
    assert result["tags"]


def test_LLM调用异常时抛TaggingError():
    with patch.object(tagging, "chat_with_retry") as mocked:
        mocked.side_effect = LLMError("网络不可达")
        with pytest.raises(TaggingError) as exc:
            tag_policy(_policy())

    assert "6548" in str(exc.value)


def test_缺policyId时抛错():
    with pytest.raises(TaggingError):
        tag_policy(_policy(fusionPolicyId=""))


def test_产出携带警告不影响成功():
    result_payload = _valid_result()
    result_payload["keywordIndex"]["title"] = []

    with patch.object(tagging, "chat_with_retry") as mocked:
        mocked.return_value = _message(result_payload)
        result = tag_policy(_policy())

    assert result["warnings"]


# --------------------------------------------------------------------------
# 申报期合并：确定性优先
# --------------------------------------------------------------------------


def test_申报期优先用确定性提取结果():
    """模型即使输出了申报期，也应被结构化字段覆盖。"""
    payload = _valid_result()
    payload["applicationWindow"] = {"startDate": "1999-01-01", "endDate": "1999-12-31"}

    with patch.object(tagging, "chat_with_retry") as mocked:
        mocked.return_value = _message(payload)
        result = tag_policy(_policy())

    window = result["applicationWindow"]
    assert window["startDate"] == "2026-06-02"
    assert window["source"] == "structured_field"


def test_确定性失败时采用模型申报期():
    policy = _policy(structuredConditions=[{"applicableTarget": "企业"}])
    payload = _valid_result()
    payload["applicationWindow"] = {
        "startDate": "2026-03-01", "endDate": "2026-05-31", "basis": "指南第三条",
    }

    with patch.object(tagging, "chat_with_retry") as mocked:
        mocked.return_value = _message(payload)
        result = tag_policy(policy)

    window = result["applicationWindow"]
    assert window["startDate"] == "2026-03-01"
    assert window["source"] == "llm"


def test_两者都失败时申报期为null():
    policy = _policy(structuredConditions=[{"applicableTarget": "企业"}])

    with patch.object(tagging, "chat_with_retry") as mocked:
        mocked.return_value = _message(_valid_result())
        result = tag_policy(policy)

    assert result["applicationWindow"]["startDate"] is None


def test_产出不含内部标记字段():
    with patch.object(tagging, "chat_with_retry") as mocked:
        mocked.return_value = _message(_valid_result())
        result = tag_policy(_policy())

    assert "needsLlm" not in result["applicationWindow"]
    assert "hints" not in result["applicationWindow"]


# --------------------------------------------------------------------------
# 产出结构
# --------------------------------------------------------------------------


def test_产出含契约要求的全部字段():
    with patch.object(tagging, "chat_with_retry") as mocked:
        mocked.return_value = _message(_valid_result())
        result = tag_policy(_policy())

    for field in ("fusionPolicyId", "title", "tags", "applicationWindow",
                  "growthChain", "keywordIndex", "notes"):
        assert field in result, field


def test_产出的六个维度齐全():
    with patch.object(tagging, "chat_with_retry") as mocked:
        mocked.return_value = _message(_valid_result())
        result = tag_policy(_policy())

    for dimension in ("A", "B", "C", "D", "E", "F"):
        assert dimension in result["tags"], dimension


def test_产出可过一期的枚举校验():
    """打标产出要能直接喂给 scoring.score_policy。"""
    from service import taxonomy as tx

    with patch.object(tagging, "chat_with_retry") as mocked:
        mocked.return_value = _message(_valid_result())
        result = tag_policy(_policy())

    assert tx.validate_tag_payload(result["tags"]) == []


# --------------------------------------------------------------------------
# prompt 加载
# --------------------------------------------------------------------------


def test_prompt能加载且被缓存():
    tagging.load_prompt.cache_clear()
    first = tagging.load_prompt()
    second = tagging.load_prompt()

    assert first is second
    assert len(first) > 1000
